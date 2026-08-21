#!/usr/bin/env python3
import glob
import base64
import http.server
import json
import logging
import mimetypes
import os
import posixpath
import re
import shlex
import shutil
import signal
import secrets
import subprocess
import threading
import time
import sys
from http.cookies import CookieError, SimpleCookie
from collections import deque
from datetime import date, datetime, timedelta
from functools import partial
from logging.handlers import RotatingFileHandler
from threading import Thread
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import pytz
import requests
from absl import app, flags
from astral import LocationInfo
from astral.sun import sun
import piexif
import yaml
from PIL import Image, UnidentifiedImageError
from skimage.metrics import structural_similarity

from .logging_utils import apply_module_levels, setup_logging, get_camera_logger
from .http_auth import auth_from_camera_config, redact_sensitive_headers
from .http_capture import validate_http_snapshot_url

from fenetre.admin_server import (
    metric_camera_directory_size_bytes,
    metric_camera_mode,
    metric_camera_ssim_target,
    metric_camera_ssim_value,
    metric_camera_online,
    metric_capture_failures_total,
    metric_last_successful_picture_timestamp,
    metric_picture_aperture,
    metric_picture_exposure_time_seconds,
    metric_picture_focal_length_mm,
    metric_picture_height_pixels,
    metric_picture_iso,
    metric_picture_size_bytes,
    metric_picture_white_balance,
    metric_picture_width_pixels,
    metric_pictures_taken_total,
    metric_processing_time_seconds,
    metric_sleep_time_seconds,
    metric_timelapses_created_total,
    metric_work_directory_size_bytes,
    _go2rtc_runtime_status,
    _sync_go2rtc_runtime,
)
from fenetre.archive import (
    archive_daydir,
    list_unarchived_dirs,
    scan_and_publish_metrics,
)
from fenetre.auth import (
    authenticate_config_user_record,
    change_config_user_password,
    effective_user_role,
    ensure_default_admin_user,
)
from fenetre.camera_utils import (
    get_day_night_from_exif,
    format_shutter_speed,
    sanitize_url_for_logs,
)
from fenetre.config import config_load
from fenetre.daylight import observe_daylight_frame, run_end_of_day
from fenetre.launch_workflow import (
    camera_name_for_recording_path,
    list_past_launch_recordings,
    preview_launch_workflow,
    run_due_launch_actions,
)
from fenetre.postprocess import postprocess, publish_metrics_from_exif_dict
from fenetre.rtsp_capture import camera_local_command
from fenetre.ptz import (
    PTZBackendUnavailable,
    PTZError,
    PTZLocked,
    discover_presets,
    focus_move,
    goto_preset,
    mark_tour_state,
    normalize_presets,
    nudge_move,
    ptz_status,
    set_tour_state,
    stop_move,
)
from fenetre.timelapse import (
    add_to_timelapse_queue,
    create_incremental_hls_timelapse,
    create_timelapse,
    get_next_from_timelapse_queue,
    get_queue_size_and_set_metric,
    remove_from_timelapse_queue,
)
from fenetre.ui_utils import copy_public_html_files
from fenetre.cameras_metadata import (
    build_cameras_metadata,
    camera_visibility,
    write_cameras_metadata,
)
from fenetre.mqtt import MQTTManager
from fenetre.media_storage import ensure_media_storage_layout, RELOCATE_LOCK
from fenetre import profiler

mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("video/mp2t", ".ts")

TIMELAPSE_VIDEO_EXTENSIONS = {"mp4", "webm", "m3u8"}
DATE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_sunrise_sunset_window_cache = {}

_GOPRO_BLE_AVAILABLE = True
try:
    from fenetre.gopro_utility import GoProUtilityThread, format_gopro_sd_card
except ModuleNotFoundError as e:
    if e.name and (e.name.startswith("bleak") or e.name == "netifaces"):
        GoProUtilityThread = None  # type: ignore[assignment]
        format_gopro_sd_card = None  # type: ignore[assignment]
        _GOPRO_BLE_AVAILABLE = False
    else:
        raise

from io import BytesIO
import json
import numpy as np
from waitress import serve as waitress_serve

logger = logging.getLogger(__name__)

try:
    import mozjpeg_lossless_optimization
except ModuleNotFoundError:
    mozjpeg_lossless_optimization = None

# Define flags at module level
if "config" not in flags.FLAGS:
    flags.DEFINE_string("config", "config.yaml", "path to YAML config file")
if "debug" not in flags.FLAGS:
    flags.DEFINE_boolean("debug", False, "Enable debug logging.")

FLAGS = flags.FLAGS


DEFAULT_SKY_AREA = "100,0,400,50"
FENETRE_PID_FILE = os.environ.get("FENETRE_PID_FILE", "/tmp/fenetre.pid")

# Global dictionary to keep track of active camera threads and related utility threads
active_camera_threads = {}
http_server_thread_global = None
http_server_instance = None
admin_server_thread_global = None
admin_server_instance_global = None
exit_event = threading.Event()
timelapse_queue_file = None
timelapse_queue_lock = threading.Lock()
background_job_lock = threading.Lock()
mqtt_manager: Optional[MQTTManager] = None
public_auth_sessions = {}
public_config_cache = {}
public_config_cache_lock = threading.Lock()
ptz_tour_resume_timers = {}
ptz_tour_resume_lock = threading.Lock()
live_view_sessions = {}
live_view_sessions_lock = threading.Lock()
camera_capture_request_events = {}
camera_capture_request_events_lock = threading.Lock()
daylight_q = deque()
archive_q = deque()
frequent_timelapse_q = deque()
frequent_timelapse_scheduler_offset = 0


def _prune_live_view_sessions(now: float | None = None):
    now = time.time() if now is None else now
    expired = [
        session_id
        for session_id, session in live_view_sessions.items()
        if session.get("expires_at", 0) <= now
    ]
    for session_id in expired:
        live_view_sessions.pop(session_id, None)


def active_live_view_count(
    camera_name: str | None = None, stream_name: str | None = None, prune: bool = True
) -> int:
    if prune:
        with live_view_sessions_lock:
            _prune_live_view_sessions()
            return active_live_view_count(camera_name, stream_name, prune=False)
    camera_name = str(camera_name or "").strip()
    stream_name = str(stream_name or "").strip()
    return sum(
        1
        for session in live_view_sessions.values()
        if (not camera_name or session.get("camera") == camera_name)
        and (not stream_name or session.get("stream") == stream_name)
    )


def record_live_view_heartbeat(
    camera_name: str,
    stream_name: str,
    session_id: str,
    owner: str,
    ttl_s: int = 45,
    active: bool = True,
) -> Dict[str, int | str | bool]:
    camera_name = str(camera_name or "").strip()
    stream_name = str(stream_name or "full").strip() or "full"
    session_id = str(session_id or "").strip()
    owner = str(owner or "authenticated").strip() or "authenticated"
    ttl_s = max(5, min(300, int(ttl_s or 45)))
    if not camera_name or not session_id:
        raise ValueError("camera and session_id are required")

    with live_view_sessions_lock:
        _prune_live_view_sessions()
        if not active:
            live_view_sessions.pop(session_id, None)
        else:
            now = time.time()
            live_view_sessions[session_id] = {
                "camera": camera_name,
                "stream": stream_name,
                "owner": owner,
                "last_seen": now,
                "expires_at": now + ttl_s,
            }
        count = active_live_view_count(camera_name, stream_name, prune=False)
    return {
        "ok": True,
        "camera": camera_name,
        "stream": stream_name,
        "active": bool(active),
        "active_count": count,
    }


def camera_uses_rtsp_capture_or_live_view(camera_config: Dict[str, Any]) -> bool:
    if camera_config.get("rtsp_url") or camera_config.get("ptz_rtsp_url"):
        return True
    local_command = camera_local_command(camera_config)
    return bool(local_command and "rtsp://" in str(local_command).lower())


def should_defer_capture_for_live_view(
    camera_name: str, camera_config: Dict[str, Any]
) -> bool:
    if not camera_uses_rtsp_capture_or_live_view(camera_config):
        return False
    return active_live_view_count(camera_name) > 0


def configure_mqtt_manager(global_cfg: Dict) -> None:
    global mqtt_manager
    if mqtt_manager:
        mqtt_manager.stop()
        mqtt_manager = None

    mqtt_cfg = global_cfg.get("mqtt") or {}
    if mqtt_cfg.get("enabled"):
        deployment_name = global_cfg.get("deployment_name", "fenetre.cam")
        mqtt_manager = MQTTManager(deployment_name, mqtt_cfg)


def configure_profiler(global_cfg: Dict) -> None:
    profiler.configure(global_cfg.get("profiler", {}))
    profiler.start()


def derive_global_config(global_cfg: Dict) -> Dict:
    """Add derived runtime paths that are not stored in the YAML config."""
    global_cfg = dict(global_cfg)
    global_cfg["pic_dir"] = os.path.join(global_cfg.get("work_dir", "."), "photos")
    return global_cfg


def load_public_config_snapshot() -> Tuple[Dict, Dict, Dict]:
    fallback = (
        globals().get("cameras_config", {}),
        globals().get("global_config", {}),
        globals().get("timelapse_config", {}),
    )
    try:
        config_path = FLAGS.config
        mtime = os.path.getmtime(config_path)
    except Exception:
        return fallback

    with public_config_cache_lock:
        if (
            public_config_cache.get("path") == config_path
            and public_config_cache.get("mtime") == mtime
            and public_config_cache.get("snapshot") is not None
        ):
            return public_config_cache["snapshot"]

        try:
            with open(config_path, "r") as config_file:
                raw_config = yaml.safe_load(config_file) or {}
            if (
                isinstance(raw_config, dict)
                and "config" in raw_config
                and len(raw_config.keys()) == 1
            ):
                raw_config = raw_config.get("config") or {}
            if not isinstance(raw_config, dict):
                raise ValueError("config root is not a mapping")

            file_cameras = raw_config.get("cameras")
            if not isinstance(file_cameras, dict):
                file_cameras = fallback[0]

            file_global = dict(fallback[1] or {})
            raw_global = raw_config.get("global")
            if isinstance(raw_global, dict):
                file_global.update(raw_global)
            file_global = derive_global_config(file_global)

            file_timelapse = raw_config.get("timelapse")
            if not isinstance(file_timelapse, dict):
                file_timelapse = fallback[2]

            snapshot = (file_cameras, file_global, file_timelapse)
            public_config_cache.update(
                {"path": config_path, "mtime": mtime, "snapshot": snapshot}
            )
            return snapshot
        except Exception as exc:
            logger.warning("Could not read current public config: %s", exc)
            return fallback


def run_serialized_background_job(job_name: str, func: Callable, *args, **kwargs):
    """Run a heavy background task without overlapping other heavy background tasks."""
    serialize_background_jobs = global_config.get("serialize_background_jobs", "auto")
    if serialize_background_jobs == "auto":
        serialize_background_jobs = (os.cpu_count() or 1) <= 1
    if not serialize_background_jobs:
        return func(*args, **kwargs)
    logger.info(f"Waiting for background job slot: {job_name}")
    with background_job_lock:
        logger.info(f"Starting serialized background job: {job_name}")
        return func(*args, **kwargs)


def interruptible_sleep(
    duration: float, event: threading.Event, check_interval: float = 1.0
):
    """Sleeps for a given duration, but checks an event periodically."""
    if duration <= 0:
        return

    end_time = time.time() + duration
    while time.time() < end_time:
        if event.is_set():
            break

        remaining_time = end_time - time.time()
        sleep_duration = min(check_interval, remaining_time)

        if sleep_duration > 0:
            time.sleep(sleep_duration)


def camera_capture_request_event(camera_name: str) -> threading.Event:
    with camera_capture_request_events_lock:
        return camera_capture_request_events.setdefault(camera_name, threading.Event())


def request_camera_capture(
    camera_name: str, reason: str = "", delay_s: float = 0
) -> Dict[str, Any]:
    event = camera_capture_request_event(camera_name)
    delay_s = max(0.0, min(60.0, float(delay_s or 0)))
    if delay_s > 0:
        timer = threading.Timer(delay_s, event.set)
        timer.daemon = True
        timer.start()
    else:
        event.set()
    logger.info(
        "%s: Capture requested%s%s.",
        camera_name,
        f" ({reason})" if reason else "",
        f" after {delay_s:.1f}s" if delay_s else "",
    )
    return {"requested": True, "reason": reason, "delay_s": delay_s}


def wait_for_next_capture_interval(camera_name: str, duration: float) -> bool:
    """Wait until the normal interval ends or an immediate capture is requested."""
    if duration <= 0:
        return False
    capture_event = camera_capture_request_event(camera_name)
    end_time = time.time() + duration
    while time.time() < end_time:
        if exit_event.is_set():
            return False
        if capture_event.is_set():
            capture_event.clear()
            return True
        remaining_time = end_time - time.time()
        if remaining_time <= 0:
            break
        if capture_event.wait(min(1.0, remaining_time)):
            capture_event.clear()
            return True
    return False


def update_camera_mode_metric(camera_name: str, mode: str) -> None:
    if mode not in {"unknown", "day", "night", "astro"}:
        logger.debug(
            "%s: received unexpected mode '%s' when updating metrics", camera_name, mode
        )
        mode = "unknown"
    for tracked_mode in ("unknown", "day", "night", "astro"):
        metric_camera_mode.labels(camera_name=camera_name, mode=tracked_mode).set(
            1.0 if tracked_mode == mode else 0.0
        )


def log_camera_error(camera_name: str, error_message: str, global_config: Dict):
    """Logs an error message to a camera-specific log file."""
    log_dir = global_config.get("log_dir")
    if not log_dir:
        return

    camera_logger = get_camera_logger(
        camera_name,
        log_dir,
        global_config.get("log_max_bytes", 10000000),
        global_config.get("log_backup_count", 5),
    )
    camera_logger.error(error_message)


def run_camera_unavailable_command(
    camera_name: str, camera_config: Dict, reason: str
) -> None:
    command = camera_config.get("unavailable_command")
    if not command:
        return

    timeout_s = camera_config.get("unavailable_command_timeout_s", 30)
    env = os.environ.copy()
    env["FENETRE_CAMERA_NAME"] = camera_name
    env["FENETRE_UNAVAILABLE_REASON"] = reason

    try:
        logger.warning(
            "%s: Running unavailable command after camera became unavailable: %s",
            camera_name,
            command,
        )
        result = subprocess.run(
            command,
            shell=True,
            timeout=timeout_s,
            env=env,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "%s: Unavailable command timed out after %ss: %s",
            camera_name,
            timeout_s,
            command,
        )
        return
    except Exception:
        logger.error(
            "%s: Failed to run unavailable command: %s",
            camera_name,
            command,
            exc_info=True,
        )
        return

    if result.returncode != 0:
        logger.error(
            "%s: Unavailable command failed with exit code %s. stdout=%r stderr=%r",
            camera_name,
            result.returncode,
            result.stdout,
            result.stderr,
        )
    else:
        logger.info(
            "%s: Unavailable command completed successfully. stdout=%r stderr=%r",
            camera_name,
            result.stdout,
            result.stderr,
        )


def get_pic_from_url(
    url: str,
    timeout: int,
    ua: str = "",
    camera_name: str = "",
    camera_config: Dict = None,
    global_config: Dict = None,
) -> Image.Image:
    if camera_config is None:
        camera_config = {}
    if global_config is None:
        global_config = {}

    request_url = url
    if camera_config.get("cache_bust", False):
        timestamp = int(time.time())
        if "?" in request_url:
            request_url = f"{request_url}&_={timestamp}"
        else:
            request_url = f"{request_url}?_={timestamp}"
    validate_http_snapshot_url(request_url)

    headers = {"Accept": "image/*,*"}
    if ua:
        requests_version = requests.__version__
        headers = {"User-Agent": f"{ua} v{requests_version}"}
    request_kwargs = {"timeout": timeout, "headers": headers}
    request_auth = auth_from_camera_config(camera_config)
    if request_auth is not None:
        request_kwargs["auth"] = request_auth

    r = requests.get(request_url, **request_kwargs)
    safe_url = sanitize_url_for_logs(url)
    safe_request_url = sanitize_url_for_logs(r.request.url)
    safe_request_headers = redact_sensitive_headers(r.request.headers)

    log_message = (
        f"URL fetch for {safe_url}:"
        f"\n\tRequest URL: {safe_request_url}"
        f"\n\tRequest Headers: {safe_request_headers}"
        f"\n\tResponse Status: {r.status_code}"
        f"\n\tResponse Headers: {r.headers}"
    )

    logger.debug(log_message)

    log_dir = global_config.get("log_dir")
    if log_dir:
        camera_logger = get_camera_logger(
            camera_name,
            log_dir,
            global_config.get("log_max_bytes", 10000000),
            global_config.get("log_backup_count", 5),
        )
        camera_logger.info(log_message)

    if r.status_code != 200:
        raise RuntimeError(
            f"HTTP Request Failed!\n"
            f"URL: {sanitize_url_for_logs(request_url)}\n"
            f"Status Code: {r.status_code}\n"
            f"Request Headers: {safe_request_headers}\n"
            f"Response Headers: {r.headers}\n"
            f"Response Content (first 500 bytes): {r.content[:500]}"
        )

    return Image.open(BytesIO(r.content))


def get_pic_dir_and_filename(camera_name: str) -> Tuple[str, str]:
    tz = pytz.timezone(global_config["timezone"])
    dt = datetime.now(tz)
    return (
        os.path.join(
            global_config["work_dir"], "photos", camera_name, dt.strftime("%Y-%m-%d")
        ),
        dt.strftime("%Y-%m-%dT%H-%M-%S%Z.jpg"),
    )


def write_pic_to_disk(
    pic: Image.Image, pic_path: str, optimize: bool = False, exif_data: bytes = b""
):
    with profiler.timed("picture.write_to_disk"):
        os.makedirs(os.path.dirname(pic_path), exist_ok=True)
        os.chmod(os.path.dirname(pic_path), 33277)  # rwxrwxr-x
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Saving picture {pic_path}")
        if optimize is True:
            if mozjpeg_lossless_optimization is None:
                raise RuntimeError(
                    "mozjpeg optimization was requested, but "
                    "mozjpeg-lossless-optimization is not installed."
                )
            jpeg_io = BytesIO()
            pic.convert("RGB").save(jpeg_io, format="JPEG", quality=90, exif=exif_data)
            jpeg_io.seek(0)
            jpeg_bytes = jpeg_io.read()
            optimized_jpeg_bytes = mozjpeg_lossless_optimization.optimize(jpeg_bytes)
            with open(pic_path, "wb") as output_file:
                output_file.write(optimized_jpeg_bytes)
        else:
            pic.convert("RGB").save(pic_path, exif=exif_data)


def update_latest_link(pic_path: str):
    with profiler.timed("picture.update_latest_link"):
        cam_dir = os.path.join(os.path.dirname(pic_path), os.pardir)
        tmp_link = os.path.join(cam_dir, "new.jpg")
        latest_link = os.path.join(cam_dir, "latest.jpg")
        relative_path = os.path.relpath(pic_path, cam_dir)
        os.symlink(relative_path, tmp_link)
        os.rename(tmp_link, latest_link)


def get_pic_from_local_command(
    cmd: str, timeout_s: int, camera_name: str, camera_config: Dict
) -> Image.Image:
    log_dir = global_config.get("log_dir")
    stderr_output = b""
    if log_dir:
        camera_logger = get_camera_logger(
            camera_name,
            log_dir,
            global_config.get("log_max_bytes", 10000000),
            global_config.get("log_backup_count", 5),
        )
        # Find the handler for the camera's log file
        log_file_handler = None
        for handler in camera_logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                log_file_handler = handler
                break

        s = subprocess.run(
            shlex.split(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
        stderr_output = s.stderr or b""
        if log_file_handler:
            with open(log_file_handler.baseFilename, "ab") as log_file:
                log_file.write(stderr_output)
                if stderr_output and not stderr_output.endswith(b"\n"):
                    log_file.write(b"\n")
        else:
            logger.warning("No camera log file handler found for %s", camera_name)
    else:
        s = subprocess.run(
            shlex.split(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
        stderr_output = s.stderr or b""

    if s.returncode != 0:
        stderr_preview = stderr_output.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(
            f"local_command failed for {camera_name} with exit code {s.returncode}. "
            f"stderr_last_500={stderr_preview!r}"
        )
    try:
        return Image.open(BytesIO(s.stdout))
    except UnidentifiedImageError as exc:
        stderr_preview = stderr_output.decode("utf-8", errors="replace")[-500:]
        stdout_preview = (s.stdout or b"")[:200]
        raise RuntimeError(
            f"local_command for {camera_name} did not return a valid image. "
            f"stdout_bytes={len(s.stdout or b'')}, "
            f"stdout_first_200={stdout_preview!r}, "
            f"stderr_last_500={stderr_preview!r}"
        ) from exc


def capture_failure_retry_interval(
    camera_config: Dict, current_sleep_interval: Optional[float] = None
) -> float:
    configured = camera_config.get("capture_failure_interval_s")
    if isinstance(configured, (int, float)) and configured > 0:
        return float(configured)
    if isinstance(current_sleep_interval, (int, float)) and current_sleep_interval > 0:
        return max(60.0, float(current_sleep_interval))
    fixed_snap_interval = camera_config.get("snap_interval_s")
    if isinstance(fixed_snap_interval, (int, float)) and fixed_snap_interval > 0:
        return max(60.0, float(fixed_snap_interval))
    return 60.0


def is_sunrise_or_sunset(
    camera_config: Dict, global_config: Dict, camera_name: str = ""
) -> bool:
    """
    Determines if the current time is within the sunrise or sunset window for a given camera.
    """
    sunrise_sunset_config = camera_config.get("sunrise_sunset", {})
    if not sunrise_sunset_config.get("enabled"):
        return False

    lat = camera_config.get("lat")
    lon = camera_config.get("lon")
    if lat is None or lon is None:
        return False

    tz = pytz.timezone(global_config["timezone"])
    now = datetime.now(tz)
    cache_key = (
        camera_name or camera_config.get("name") or "",
        now.date().isoformat(),
        global_config["timezone"],
        lat,
        lon,
        sunrise_sunset_config.get("sunrise_offset_start_minutes"),
        sunrise_sunset_config.get("sunrise_offset_end_minutes"),
        sunrise_sunset_config.get("sunset_offset_start_minutes"),
        sunrise_sunset_config.get("sunset_offset_end_minutes"),
    )
    cached_windows = _sunrise_sunset_window_cache.get(cache_key)
    if cached_windows is False:
        return False

    try:
        if cached_windows is None:
            location = LocationInfo(
                latitude=lat,
                longitude=lon,
                timezone=global_config["timezone"],
            )
            s = sun(location.observer, date=now.date(), tzinfo=location.timezone)
            cached_windows = (
                (
                    s["sunrise"]
                    - timedelta(
                        minutes=sunrise_sunset_config["sunrise_offset_start_minutes"]
                    ),
                    s["sunrise"]
                    + timedelta(
                        minutes=sunrise_sunset_config["sunrise_offset_end_minutes"]
                    ),
                ),
                (
                    s["sunset"]
                    - timedelta(
                        minutes=sunrise_sunset_config["sunset_offset_start_minutes"]
                    ),
                    s["sunset"]
                    + timedelta(
                        minutes=sunrise_sunset_config["sunset_offset_end_minutes"]
                    ),
                ),
            )
            _sunrise_sunset_window_cache[cache_key] = cached_windows

        (sunrise_start, sunrise_end), (sunset_start, sunset_end) = cached_windows
        return (sunrise_start <= now <= sunrise_end) or (
            sunset_start <= now <= sunset_end
        )

    except ValueError as e:
        _sunrise_sunset_window_cache[cache_key] = False
        camera_label = f" for {camera_name}" if camera_name else ""
        logger.info(
            "Sunrise/sunset unavailable%s on %s at %s,%s: %s",
            camera_label,
            now.date().isoformat(),
            lat,
            lon,
            e,
        )
        return False
    except Exception as e:
        logger.error(f"Error calculating sunrise/sunset: {e}")
        return False


def snap(camera_name, camera_config: Dict):
    picamera2_capture = None
    picamera2_initial_exposure_state = None
    camera_capture_request_event(camera_name)

    def current_config_matches_snap_thread() -> bool:
        if cameras_config.get(camera_name) == camera_config:
            return True
        logger.info(
            "%s: Snap thread exiting because camera config was removed or changed.",
            camera_name,
        )
        return False

    def load_picamera2_exposure_state() -> Optional[Dict]:
        metadata_path = os.path.join(
            global_config["work_dir"], "photos", camera_name, "metadata.json"
        )
        try:
            with open(metadata_path, "r") as f:
                metadata = json.load(f)
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning(
                "%s: Failed to read previous metadata for exposure recovery.",
                camera_name,
                exc_info=True,
            )
            return None
        exposure_state = metadata.get("picamera2_exposure_control")
        if isinstance(exposure_state, dict):
            return exposure_state
        return None

    def clear_camera_gauges():
        for mode in ("unknown", "day", "night", "astro"):
            try:
                metric_camera_mode.remove(camera_name, mode)
            except KeyError:
                pass
        for gauge in (
            metric_camera_online,
            metric_last_successful_picture_timestamp,
            metric_processing_time_seconds,
            metric_sleep_time_seconds,
            metric_camera_ssim_value,
            metric_camera_ssim_target,
            metric_picture_width_pixels,
            metric_picture_height_pixels,
            metric_picture_size_bytes,
            metric_picture_iso,
            metric_picture_focal_length_mm,
            metric_picture_aperture,
            metric_picture_exposure_time_seconds,
            metric_picture_white_balance,
        ):
            try:
                gauge.remove(camera_name)
            except KeyError:
                pass

    clear_camera_gauges()
    camera_online_metric = metric_camera_online.labels(camera_name=camera_name)
    camera_online_metric.set(0.0)
    if mqtt_manager:
        mqtt_manager.publish_camera_state(camera_name, False)
    if camera_config.get("capture_method") == "picamera2":
        picamera2_initial_exposure_state = load_picamera2_exposure_state()

    # This is the capture function which is the only place in this snap thread where we have image source type specific info and logic.
    def capture(mode: str) -> Image.Image:
        nonlocal picamera2_capture, picamera2_initial_exposure_state

        logger.info("%s: Fetching new picture.", camera_name)

        timeout = camera_config.get("timeout_s", 60)
        local_command = camera_local_command(camera_config)

        # local_command is very flexible, it could be anything from running raspistill locally, to extracting a picture from a stream with ffmpeg, etc...
        if local_command is not None:
            return get_pic_from_local_command(
                local_command, timeout, camera_name, camera_config
            )

        # Capture picture from a URL. Useful for public cams or CCTV
        url = camera_config.get("url")
        if url is not None:
            ua = global_config.get("user_agent", "")
            return get_pic_from_url(
                url, timeout, ua, camera_name, camera_config, global_config
            )

        # gopro_model will call GoPro specific Classes defiend in gopro.py
        gopro_model = camera_config.get("gopro_model")
        if gopro_model is not None:
            gopro_instance = active_camera_threads.get(camera_name, {}).get(
                "gopro_instance"
            )
            if not gopro_instance:
                raise RuntimeError(f"GoPro instance not found for camera {camera_name}")
            if mode in ("day", "night", "astro"):
                gopro_instance.set_mode(mode)
            jpeg_bytes = gopro_instance.capture_photo()
            try:
                # new_pic is only used to check if the image is valid
                Image.open(BytesIO(jpeg_bytes))
            except Image.UnidentifiedImageError:
                logger.error(
                    f"Failed to open image from GoPro: {gopro_model}. Resetting gopro"
                )
                raise
            return Image.open(BytesIO(jpeg_bytes))

        if camera_config.get("capture_method") == "picamera2":
            from fenetre.picamera import Picamera2Capture

            if picamera2_capture is None:
                picamera2_capture = Picamera2Capture(
                    camera_config,
                    initial_exposure_state=picamera2_initial_exposure_state,
                )
                picamera2_initial_exposure_state = None
            return picamera2_capture.capture(mode)
        return None

    # Here we take the very first picture, we will only save it when we start the main loop. I don't remember why I implemented the loop that way but it made sense at the time.
    previous_pic_dir, previous_pic_filename = get_pic_dir_and_filename(camera_name)
    previous_pic_fullpath = os.path.join(previous_pic_dir, previous_pic_filename)
    previous_mode = "unknown"
    previous_pic = None
    while not exit_event.is_set():
        if not current_config_matches_snap_thread():
            return
        try:
            with profiler.timed(f"camera.{camera_name}.capture"):
                previous_pic = capture(mode=previous_mode)
            break
        except Exception as e:
            error_msg = f"Failed to capture initial image for {camera_name}: {e}"
            logger.error(error_msg, exc_info=True)
            log_camera_error(camera_name, error_msg, global_config)
            metric_capture_failures_total.labels(camera_name=camera_name).inc()
            camera_online_metric.set(0.0)
            if mqtt_manager:
                mqtt_manager.publish_camera_state(camera_name, False)
            retry_interval = capture_failure_retry_interval(camera_config)
            logger.info(
                "%s: Initial capture failed; retrying in %.1fs without restarting the snap thread.",
                camera_name,
                retry_interval,
            )
            interruptible_sleep(retry_interval, exit_event)
    if exit_event.is_set() or previous_pic is None:
        logger.info(
            "%s: Exiting snap loop before initial capture completed.", camera_name
        )
        return
    if not current_config_matches_snap_thread():
        return
    previous_exif_bytes = previous_pic.info.get("exif") or b""
    if len(camera_config.get("postprocessing", [])) > 0:
        with profiler.timed(f"camera.{camera_name}.postprocess"):
            previous_pic = postprocess(
                previous_pic,
                camera_config.get("postprocessing", []),
                global_config,
                camera_config,
            )
    with profiler.timed(f"camera.{camera_name}.daylight_observe"):
        observe_daylight_frame(
            camera_name,
            previous_pic_fullpath,
            previous_pic,
            camera_config.get("sky_area"),
        )
    fixed_snap_interval = camera_config.get("snap_interval_s", None)
    if camera_name not in sleep_intervals:
        sleep_intervals[camera_name] = (
            float(fixed_snap_interval)
            if isinstance(fixed_snap_interval, (int, float))
            else 60.0
        )

    while not exit_event.is_set():
        if not current_config_matches_snap_thread():
            return
        # Immediately save the previous pic to disk. Held under RELOCATE_LOCK so
        # an admin-triggered media_dir relocation cannot swap the photos/launches
        # symlink out from under an in-progress write.
        with RELOCATE_LOCK:
            write_pic_to_disk(
                previous_pic,
                previous_pic_fullpath,
                camera_config.get("mozjpeg_optimize", False),
                previous_exif_bytes,
            )

            # Read EXIF data that will be used for metrics
            from .postprocess import get_exif_dict

            with profiler.timed(f"camera.{camera_name}.exif_read"):
                previous_exif = get_exif_dict(previous_pic_fullpath)

        # Gather and publish metrics after we have succesfully written the picture on disk
        # TODO: We should only do that if the admin server is enabled.
        if camera_config.get("gather_metrics", True):
            try:
                with profiler.timed(f"camera.{camera_name}.publish_exif_metrics"):
                    publish_metrics_from_exif_dict(previous_exif, camera_name)
            except Exception as e:
                logger.error(
                    f"Error gathering metrics for {previous_pic_fullpath}: {e}"
                )
        metric_pictures_taken_total.labels(camera_name=camera_name).inc()
        metric_last_successful_picture_timestamp.labels(
            camera_name=camera_name
        ).set_to_current_time()
        camera_online_metric.set(1.0)
        if mqtt_manager:
            mqtt_manager.publish_camera_state(camera_name, True)

        # Now we update the links for the frontend/UI
        with RELOCATE_LOCK:
            update_latest_link(previous_pic_fullpath)
            metadata = {
                "last_picture_url": os.path.relpath(
                    previous_pic_fullpath,
                    os.path.join(previous_pic_fullpath, os.path.pardir, os.path.pardir),
                ),
                "iso": previous_exif.get("iso"),
                "shutter_speed": format_shutter_speed(
                    previous_exif.get("exposure_time")
                ),
            }
            if picamera2_capture is not None:
                exposure_state = picamera2_capture.get_exposure_control_state()
                if exposure_state.get("modes"):
                    metadata["picamera2_exposure_control"] = exposure_state
            metadata_path = os.path.join(
                previous_pic_dir, os.path.pardir, "metadata.json"
            )
            with profiler.timed(f"camera.{camera_name}.metadata_write"):
                with open(metadata_path, "w") as f:
                    json.dump(metadata, f, indent=4)
                    logger.debug(
                        f"{camera_name}: Updated metadata file {metadata_path}"
                    )

        current_mode = get_day_night_from_exif(
            previous_exif, camera_config, previous_mode, previous_pic_fullpath
        )
        if camera_config.get("gather_metrics", True):
            update_camera_mode_metric(camera_name, previous_mode)

        # This is a good moment to gracefully exit if the user wants to.
        if exit_event.is_set():
            logger.info(f"{camera_name}: Exiting snap loop.")
            return

        # Let's figure out how long we will be waiting before taking the next picture
        sunrise_sunset = is_sunrise_or_sunset(camera_config, global_config, camera_name)
        if sunrise_sunset:
            fast_sunrise_sunset_interval = camera_config.get("sunrise_sunset", {}).get(
                "interval_s", 10
            )
            sleep_intervals[camera_name] = fast_sunrise_sunset_interval
            logger.info(
                f"{camera_name}: Sunrise/sunset detected, using fast interval: {fast_sunrise_sunset_interval}s"
            )
        current_sleep_interval = sleep_intervals[camera_name]
        metric_sleep_time_seconds.labels(camera_name=camera_name).set(
            current_sleep_interval
        )
        logger.info(f"{camera_name}: Sleeping {current_sleep_interval}s")
        capture_requested = wait_for_next_capture_interval(
            camera_name, current_sleep_interval
        )
        if capture_requested:
            logger.info("%s: Waking early for requested capture.", camera_name)

        if exit_event.is_set():
            return
        if not current_config_matches_snap_thread():
            return

        if should_defer_capture_for_live_view(camera_name, camera_config):
            logger.info(
                "%s: Deferring RTSP capture while live go2rtc stream is active.",
                camera_name,
            )
            if capture_requested:
                request_camera_capture(
                    camera_name,
                    "deferred while live stream active",
                    delay_s=5,
                )
            interruptible_sleep(5, exit_event)
            continue

        start_time = time.time()
        new_pic_dir, new_pic_filename = get_pic_dir_and_filename(camera_name)
        new_pic_fullpath = os.path.join(new_pic_dir, new_pic_filename)

        if not previous_pic_dir == new_pic_dir:
            # This is a new day. We can now process the previous day.
            daylight_q.append(
                (
                    camera_name,
                    previous_pic_dir,
                    camera_config.get("sky_area", DEFAULT_SKY_AREA),
                )
            )

        new_pic = None
        while not exit_event.is_set():
            if not current_config_matches_snap_thread():
                return
            try:
                with profiler.timed(f"camera.{camera_name}.capture"):
                    new_pic = capture(current_mode)
                break
            except Exception as e:
                error_msg = f"Could not fetch picture for {camera_name}: {e}"
                logger.warning(error_msg, exc_info=True)
                log_camera_error(camera_name, error_msg, global_config)
                metric_capture_failures_total.labels(camera_name=camera_name).inc()
                camera_online_metric.set(0.0)
                if mqtt_manager:
                    mqtt_manager.publish_camera_state(camera_name, False)
                retry_interval = capture_failure_retry_interval(
                    camera_config, current_sleep_interval
                )
                logger.info(
                    "%s: Capture failed; retrying in %.1fs without restarting the snap thread.",
                    camera_name,
                    retry_interval,
                )
                interruptible_sleep(retry_interval, exit_event)
        if exit_event.is_set():
            return
        if not current_config_matches_snap_thread():
            return
        if new_pic is None:
            logger.error(f"{camera_name}: Could not fetch picture.")
            raise ValueError
        new_exif_bytes = new_pic.info.get("exif") or b""
        if len(camera_config.get("postprocessing", [])) > 0:
            with profiler.timed(f"camera.{camera_name}.postprocess"):
                new_pic = postprocess(
                    new_pic,
                    camera_config.get("postprocessing", []),
                    global_config,
                    camera_config,
                )
        with profiler.timed(f"camera.{camera_name}.daylight_observe"):
            observe_daylight_frame(
                camera_name,
                new_pic_fullpath,
                new_pic,
                camera_config.get("sky_area"),
            )
        # SSIM activity logic. With snap_interval_s set, SSIM can still
        # temporarily switch the next capture to activity_interval_s.
        if not sunrise_sunset:
            with profiler.timed(f"camera.{camera_name}.ssim"):
                ssim = get_ssim_for_area(
                    previous_pic, new_pic, camera_config.get("ssim_area", None)
                )
            ssim_setpoint = camera_config.get("ssim_setpoint", 0.85)
            if fixed_snap_interval:
                sleep_intervals[camera_name] = (
                    camera_config.get("activity_interval_s", 10)
                    if ssim < ssim_setpoint
                    else fixed_snap_interval
                )
            else:
                if ssim < ssim_setpoint:
                    sleep_intervals[camera_name] = sleep_intervals[camera_name] * 0.9
                else:
                    sleep_intervals[camera_name] = min(
                        90,
                        sleep_intervals[camera_name]
                        + 2,  # TODO: Make this configurable
                    )
            if camera_config.get("gather_metrics", True):
                metric_camera_ssim_value.labels(camera_name=camera_name).set(ssim)
                metric_camera_ssim_target.labels(camera_name=camera_name).set(
                    ssim_setpoint
                )
            logger.info(
                f"{camera_name}: ssim {ssim}, setpoint: {ssim_setpoint}, new sleep interval: {sleep_intervals[camera_name]}s, next mode: {current_mode}"
            )
        end_time = time.time()
        metric_processing_time_seconds.labels(camera_name=camera_name).set(
            end_time - start_time
        )
        previous_pic = new_pic
        previous_exif_bytes = new_exif_bytes
        previous_pic_dir = new_pic_dir
        previous_pic_fullpath = new_pic_fullpath
        previous_mode = current_mode


def get_ssim_for_area(
    image1: Image.Image, image2: Image.Image, area: Optional[str]
) -> float:
    if image1.size != image2.size:
        logger.error(
            f"Images {image1.size} and {image2.size} are not the same size, cannot compare SSIM."
        )
        return 1.0

    target_image1 = image1
    target_image2 = image2

    # Compute SSIM on the full image.
    if area:
        crop_points_list = [float(i) for i in area.split(",")]

        # If all values are <= 1.0, treat them as ratios
        if all(v <= 1.0 for v in crop_points_list):
            img_width, img_height = image1.size
            x1 = int(img_width * crop_points_list[0])
            y1 = int(img_height * crop_points_list[1])
            x2 = int(img_width * crop_points_list[2])
            y2 = int(img_height * crop_points_list[3])
            crop_points = (x1, y1, x2, y2)
        else:  # Otherwise, treat as absolute pixel values (legacy)
            crop_points = (
                int(crop_points_list[0]),
                int(crop_points_list[1]),
                int(crop_points_list[2]),
                int(crop_points_list[3]),
            )

        img_width, img_height = image1.size
        x1, y1, x2, y2 = crop_points
        crop_points = (
            max(0, min(img_width, x1)),
            max(0, min(img_height, y1)),
            max(0, min(img_width, x2)),
            max(0, min(img_height, y2)),
        )
        if crop_points[2] <= crop_points[0] or crop_points[3] <= crop_points[1]:
            logger.warning(
                "Invalid SSIM crop area %s for image size %s; skipping SSIM crop.",
                area,
                image1.size,
            )
            return 1.0

        logger.debug(f"SSIM crop points: {crop_points}")
        target_image1 = image1.resize((50, 50), box=crop_points)
        target_image2 = image2.resize((50, 50), box=crop_points)

    image1_np = np.array(target_image1.convert("L"))
    image2_np = np.array(target_image2.convert("L"))

    return structural_similarity(image1_np, image2_np, data_range=255)


def _cors_allow_origin_for_request(request_origin, cors_config):
    allow_origins = cors_config.get("cors_allow_origins") or []
    if "*" in allow_origins:
        return "*"
    if request_origin and request_origin in allow_origins:
        return request_origin
    if not request_origin:
        return cors_config.get("cors_allow_origin") or (
            allow_origins[0] if allow_origins else "*"
        )
    return None


def _classify_timelapse_file(
    filename: str, daily_config: Dict, frequent_config: Dict
) -> Optional[str]:
    _, extension = os.path.splitext(filename)
    extension = extension.lstrip(".").lower()
    if extension not in TIMELAPSE_VIDEO_EXTENSIONS:
        return None
    if extension == "m3u8" and frequent_config.get("output_format") == "hls":
        return "frequent"

    daily_extension = (daily_config.get("file_extension") or "mp4").lower()
    frequent_extension = (frequent_config.get("file_extension") or "mp4").lower()
    if extension == daily_extension:
        return "daily"
    if (
        frequent_config.get("output_format") == "file"
        and extension == frequent_extension
        and frequent_extension != daily_extension
    ):
        return "frequent"

    # Date-named video files are daily archives in normal deployments. Treat
    # older formats as daily too so previously generated archives remain visible
    # after changing file_extension.
    return "daily"


def discover_camera_timelapses(
    camera_name: str,
    work_dir: str,
    daily_config: Dict,
    frequent_config: Dict,
) -> List[Dict]:
    if not camera_name or os.path.basename(camera_name) != camera_name:
        return []

    camera_dir = os.path.join(work_dir, "photos", camera_name)
    if not os.path.isdir(camera_dir):
        return []

    results = []
    daily_extension = (daily_config.get("file_extension") or "mp4").lower()
    for date_name in sorted(os.listdir(camera_dir), reverse=True):
        if not DATE_DIR_PATTERN.match(date_name):
            continue
        day_dir = os.path.join(camera_dir, date_name)
        if not os.path.isdir(day_dir):
            continue
        daily_candidates = []
        frequent_candidates = []
        for filename in sorted(os.listdir(day_dir)):
            base, extension = os.path.splitext(filename)
            if base != date_name:
                continue
            extension = extension.lstrip(".").lower()
            timelapse_type = _classify_timelapse_file(
                filename, daily_config, frequent_config
            )
            if not timelapse_type:
                continue
            filepath = os.path.join(day_dir, filename)
            if not os.path.isfile(filepath) or os.path.getsize(filepath) <= 0:
                continue
            item = {
                "date": date_name,
                "type": timelapse_type,
                "format": extension,
                "url": f"/photos/{camera_name}/{date_name}/{filename}",
                "bytes": os.path.getsize(filepath),
                "mtime": int(os.path.getmtime(filepath)),
            }
            if timelapse_type == "daily":
                daily_candidates.append(item)
            else:
                frequent_candidates.append(item)
        if daily_candidates:
            daily_candidates.sort(
                key=lambda item: (
                    item["format"] != daily_extension,
                    -item["mtime"],
                )
            )
            results.append(daily_candidates[0])
        results.extend(frequent_candidates)
    return results


def is_camera_timelapse_enabled(camera_name: str) -> bool:
    camera_cfg = cameras_config.get(camera_name, {})
    if camera_cfg.get("disabled", False):
        return False
    if camera_cfg.get("generate_timelapse") is False:
        return False
    if camera_cfg.get("timelapse_enabled") is False:
        return False
    camera_timelapse = camera_cfg.get("timelapse")
    if isinstance(camera_timelapse, dict) and camera_timelapse.get("enabled") is False:
        return False
    return True


def camera_name_from_day_dir(day_dir: str) -> str:
    return os.path.basename(os.path.dirname(os.path.normpath(day_dir)))


def canonical_request_path(path: str) -> str:
    """Decode percent-encoding and normalize a request path the same way
    SimpleHTTPRequestHandler.translate_path resolves it on disk (unquote the
    whole path first, *then* split/normalize) so every visibility/permission
    check runs against the exact path that will actually be served. Checking
    against the raw, still-encoded path let a request like
    /photos%2fHiddenCam/... slip past camera-name detection (the literal "/"
    never appeared before unquoting) while translate_path still resolved and
    served the real file underneath work_dir/photos/HiddenCam/...
    """
    decoded = unquote(path)
    normalized = posixpath.normpath(decoded)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


class FenetreHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def _cache_control_header(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        basename = os.path.basename(path)
        query = parse_qs(parsed.query)

        if "v" in query:
            return "public, max-age=31536000, immutable"
        if basename in {"cameras.json", "metadata.json", "latest.jpg"}:
            return "no-cache, must-revalidate"
        if path.endswith(".m3u8"):
            return "no-cache, must-revalidate"
        if path.endswith((".html", ".htm")) or basename == "":
            return "no-cache, must-revalidate"
        return None

    def _send_json(self, status_code, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _deployment_name(self) -> str:
        _, current_global_config, _ = load_public_config_snapshot()
        return current_global_config.get("deployment_name") or "fenetre.cam"

    def _ui_config(self) -> Dict:
        _, current_global_config, _ = load_public_config_snapshot()
        ui_config = current_global_config.get("ui") or {}
        return ui_config if isinstance(ui_config, dict) else {}

    def _site_is_public(self) -> bool:
        return self._ui_config().get("public_site", True) is not False

    def _launch_workflow_enabled(self) -> bool:
        _, current_global_config, _ = load_public_config_snapshot()
        workflow = current_global_config.get(
            "launch_workflow"
        ) or current_global_config.get("rocket_launches")
        if not isinstance(workflow, dict):
            return False
        value = workflow.get("enabled")
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _send_public_auth_required(self):
        self._send_json(
            401,
            {
                "error": "Authentication required",
                "public_site": False,
                "deployment_name": self._deployment_name(),
            },
        )

    def _camera_visible_to_public_user(
        self, camera_name: str, user: Optional[Dict] = None
    ) -> bool:
        current_cameras_config, _, _ = load_public_config_snapshot()
        camera_config = current_cameras_config.get(camera_name)
        if not camera_config:
            return False
        visibility = camera_visibility(camera_config)
        if visibility == "hidden":
            return False
        if not self._site_is_public() and not user:
            return False
        if visibility == "authenticated" and not user:
            return False
        return True

    def _path_camera_name(self, canonical_path: str) -> Optional[str]:
        # canonical_path must already be decoded+normalized via
        # canonical_request_path(); do not pass a raw, still-encoded path in.
        parts = [part for part in canonical_path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "photos":
            return parts[1]
        return None

    def _launches_recording_camera_name(self, canonical_path: str) -> Optional[str]:
        # canonical_path must already be decoded+normalized via
        # canonical_request_path().
        parts = [part for part in canonical_path.split("/") if part]
        if len(parts) < 3 or parts[0] != "launches":
            return None
        launch_id, filename = parts[1], parts[-1]
        current_cameras_config, _, _ = load_public_config_snapshot()
        camera_name = camera_name_for_recording_path(
            {"cameras": current_cameras_config}, launch_id, filename
        )
        return camera_name or None

    def _camera_timelapse_enabled(self, camera_config: Dict) -> bool:
        if camera_config.get("disabled", False):
            return False
        if camera_config.get("generate_timelapse") is False:
            return False
        if camera_config.get("timelapse_enabled") is False:
            return False
        camera_timelapse = camera_config.get("timelapse")
        if (
            isinstance(camera_timelapse, dict)
            and camera_timelapse.get("enabled") is False
        ):
            return False
        return True

    def _handle_cameras_api(self):
        user = self._public_session_user()
        if not self._site_is_public() and not user:
            self._send_public_auth_required()
            return
        current_cameras_config, current_global_config, current_timelapse_config = (
            load_public_config_snapshot()
        )
        json_filepath = os.path.join(
            current_global_config.get("work_dir", "."), "cameras.json"
        )
        metadata = build_cameras_metadata(
            current_cameras_config,
            current_global_config or {},
            current_timelapse_config or {},
            json_filepath,
            include_private=bool(user),
            include_hidden=False,
            include_removed=False,
            include_go2rtc=bool(user),
        )
        self._send_json(200, metadata)

    def _filter_public_launch_preview(
        self, preview: Dict, user: Optional[Dict]
    ) -> Dict:
        filtered = dict(preview or {})
        events = []
        for event in filtered.get("events") or []:
            event_copy = dict(event)
            plans = []
            for plan in event_copy.get("plans") or []:
                plan_copy = dict(plan)
                camera_details = [
                    dict(camera)
                    for camera in plan_copy.get("camera_details") or []
                    if isinstance(camera, dict)
                    and self._camera_visible_to_public_user(
                        camera.get("name", ""), user
                    )
                ]
                plan_copy["camera_details"] = camera_details
                plan_copy["cameras"] = [camera["name"] for camera in camera_details]
                plans.append(plan_copy)
            event_copy["plans"] = plans
            events.append(event_copy)
        filtered["events"] = events
        return filtered

    def _filter_public_launch_history(
        self, history: Dict, user: Optional[Dict]
    ) -> Dict:
        filtered = dict(history or {})
        launches = []
        for launch in filtered.get("launches") or []:
            launch_copy = dict(launch)
            recordings = []
            for recording in launch_copy.get("recordings") or []:
                if not isinstance(recording, dict):
                    continue
                camera_name = str(recording.get("camera") or "")
                if camera_name and self._camera_visible_to_public_user(
                    camera_name, user
                ):
                    recordings.append(dict(recording))
            if recordings:
                launch_copy["recordings"] = recordings
                launch_copy["recording_count"] = len(recordings)
                launch_copy["bytes"] = sum(
                    int(recording.get("bytes") or 0) for recording in recordings
                )
                launches.append(launch_copy)
        filtered["launches"] = launches
        return filtered

    def _handle_launches_preview_api(self):
        user = self._public_session_user()
        if not self._site_is_public() and not user:
            self._send_public_auth_required()
            return
        current_cameras_config, current_global_config, _ = load_public_config_snapshot()
        try:
            preview = preview_launch_workflow(
                {
                    "global": current_global_config or {},
                    "cameras": current_cameras_config or {},
                }
            )
            self._send_json(200, self._filter_public_launch_preview(preview, user))
        except Exception as exc:
            logger.error("Unexpected launch preview error.", exc_info=True)
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _handle_launches_history_api(self):
        user = self._public_session_user()
        if not self._site_is_public() and not user:
            self._send_public_auth_required()
            return
        current_cameras_config, current_global_config, _ = load_public_config_snapshot()
        try:
            history = list_past_launch_recordings(
                {
                    "global": current_global_config or {},
                    "cameras": current_cameras_config or {},
                }
            )
            self._send_json(200, self._filter_public_launch_history(history, user))
        except Exception as exc:
            logger.error("Unexpected launch history error.", exc_info=True)
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _handle_timelapses_api(self, parsed_url):
        query = parse_qs(parsed_url.query)
        camera_name = (query.get("camera") or [""])[0]
        if not camera_name:
            self._send_json(400, {"error": "camera query parameter is required"})
            return
        current_cameras_config, current_global_config, current_timelapse_config = (
            load_public_config_snapshot()
        )
        camera_config = current_cameras_config.get(camera_name)
        if not camera_config:
            self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
            return
        if not self._camera_visible_to_public_user(
            camera_name, self._public_session_user()
        ):
            self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
            return
        timelapse_enabled = self._camera_timelapse_enabled(camera_config)

        if not timelapse_enabled:
            timelapses = []
        else:
            timelapses = discover_camera_timelapses(
                camera_name,
                current_global_config.get("work_dir", "."),
                current_timelapse_config.get("daily_timelapse", {}) or {},
                current_timelapse_config.get("frequent_timelapse", {}) or {},
            )
        self._send_json(
            200,
            {
                "camera": camera_name,
                "timelapse_enabled": timelapse_enabled,
                "timelapses": timelapses,
            },
        )

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length <= 0:
            return {}
        if content_length > 64 * 1024:
            raise ValueError("Request body is too large")
        return json.loads(self.rfile.read(content_length).decode("utf-8"))

    def _public_session_user(self):
        auth_header = self.headers.get("Authorization") or ""
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        if not token:
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie") or "")
            except CookieError:
                cookies = SimpleCookie()
            cookie_token = cookies.get("fenetreAuthToken")
            if cookie_token:
                token = cookie_token.value
        if not token:
            return None
        session = public_auth_sessions.get(token)
        if not session:
            return None
        if session.get("expires_at", 0) <= time.time():
            public_auth_sessions.pop(token, None)
            return None
        return session.get("user")

    def _handle_public_login_api(self):
        token, public_user = self._create_public_session_from_json_body()
        if token and public_user:
            self._send_json(200, {"token": token, "user": public_user})

    def _create_public_session_from_json_body(self):
        try:
            payload = self._read_json_body()
            username = (payload.get("username") or "").strip()
            password = payload.get("password") or ""
            return self._create_public_session(username, password)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return None, None

    def _create_public_session(
        self, username: str, password: str, send_errors: bool = True
    ):
        if not username or not password:
            if send_errors:
                self._send_json(400, {"error": "username and password are required"})
            return None, None
        user = authenticate_config_user_record(FLAGS.config, username, password)
        if not user:
            if send_errors:
                self._send_json(401, {"error": "Invalid username or password"})
            return None, None
        token = secrets.token_urlsafe(32)
        public_user = {
            "username": username,
            "role": user.get("role", "viewer"),
            "ptz_access": user.get("ptz_access", "presets"),
            "ptz_cameras": user.get("ptz_cameras", []),
        }
        public_auth_sessions[token] = {
            "user": public_user,
            "expires_at": time.time() + 12 * 60 * 60,
        }
        return token, public_user

    def _send_basic_auth_challenge(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Fenetre Public"')
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"Authentication required"}')

    def _handle_public_basic_login_api(self):
        auth_header = self.headers.get("Authorization") or ""
        if not auth_header.lower().startswith("basic "):
            self._send_basic_auth_challenge()
            return
        try:
            credentials = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
            username, password = credentials.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            self._send_basic_auth_challenge()
            return
        token, public_user = self._create_public_session(
            username.strip(), password, send_errors=False
        )
        if not token or not public_user:
            self._send_basic_auth_challenge()
            return
        self._send_json(200, {"token": token, "user": public_user})

    def _basic_auth_credentials(self):
        auth_header = self.headers.get("Authorization") or ""
        if not auth_header.lower().startswith("basic "):
            return None, None
        try:
            credentials = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
            username, password = credentials.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return None, None
        return username.strip(), password

    def _handle_public_login_page(self, parsed_url):
        username, password = self._basic_auth_credentials()
        if not username or not password:
            self._send_basic_auth_challenge()
            return
        token, public_user = self._create_public_session(
            username, password, send_errors=False
        )
        if not token or not public_user:
            self._send_basic_auth_challenge()
            return

        next_values = parse_qs(parsed_url.query).get("next") or ["/"]
        next_url = next_values[0] or "/"
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        html = f"""<!DOCTYPE html>
<html>
<head><title>Fenetre Login</title></head>
<body>
<script>
localStorage.setItem('fenetreAuthToken', {json.dumps(token)});
document.cookie = 'fenetreAuthToken=' + encodeURIComponent({json.dumps(token)}) + '; Path=/; SameSite=Lax';
window.location.replace({json.dumps(next_url)});
</script>
</body>
</html>
"""
        encoded = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _handle_public_logout_api(self):
        auth_header = self.headers.get("Authorization") or ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            public_auth_sessions.pop(token, None)
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie") or "")
        except CookieError:
            cookies = SimpleCookie()
        cookie_token = cookies.get("fenetreAuthToken")
        if cookie_token:
            public_auth_sessions.pop(cookie_token.value, None)
        self._send_json(200, {"ok": True})

    def _clear_public_sessions_for_user(self, username: str):
        for token, session in list(public_auth_sessions.items()):
            session_user = session.get("user") or {}
            if session_user.get("username") == username:
                public_auth_sessions.pop(token, None)

    def _handle_public_change_password_api(self):
        user = self._public_session_user()
        if not user:
            self._send_json(401, {"error": "Authentication required"})
            return
        try:
            payload = self._read_json_body()
            current_password = payload.get("current_password") or ""
            new_password = payload.get("new_password") or ""
            change_config_user_password(
                FLAGS.config,
                user.get("username") or "",
                current_password,
                new_password,
            )
            self._clear_public_sessions_for_user(user.get("username") or "")
            with public_config_cache_lock:
                public_config_cache.clear()
            self._send_json(
                200,
                {
                    "ok": True,
                    "message": "Password changed. Sign in again with the new password.",
                },
            )
        except PermissionError as exc:
            self._send_json(401, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            logger.error("Unexpected public password change error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def _handle_public_auth_status_api(self):
        user = self._public_session_user()
        self._send_json(
            200,
            {
                "authenticated": bool(user),
                "user": user,
                "public_site": self._site_is_public(),
                "deployment_name": self._deployment_name(),
                "launch_workflow_enabled": self._launch_workflow_enabled(),
            },
        )

    def _handle_go2rtc_status_api(self):
        try:
            user = self._public_session_user()
            if not user:
                self._send_public_auth_required()
                return
            if effective_user_role(user) not in {"admin", "superadmin"}:
                self._send_json(403, {"error": "Admin access required"})
                return
            current_cameras_config, current_global_config, _ = (
                load_public_config_snapshot()
            )
            self._send_json(
                200,
                _go2rtc_runtime_status(
                    {
                        "global": current_global_config or {},
                        "cameras": current_cameras_config or {},
                    }
                ),
            )
        except Exception as exc:
            logger.error("Unexpected go2rtc status error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def _handle_live_view_heartbeat_api(self):
        try:
            user = self._public_session_user()
            if not user:
                self._send_json(401, {"error": "Authentication required"})
                return
            payload = self._read_json_body()
            camera_name = (payload.get("camera") or "").strip()
            session_id = (payload.get("session_id") or "").strip()
            stream_name = (payload.get("stream") or "full").strip() or "full"
            active = payload.get("active", True) is not False
            ttl_s = int(payload.get("ttl_s") or 45)
            if not camera_name or not session_id:
                self._send_json(400, {"error": "camera and session_id are required"})
                return
            current_cameras_config, _, _ = load_public_config_snapshot()
            if camera_name not in current_cameras_config:
                self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
                return
            if not self._camera_visible_to_public_user(camera_name, user):
                self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
                return
            result = record_live_view_heartbeat(
                camera_name,
                stream_name,
                session_id,
                user.get("username") or "authenticated",
                ttl_s=ttl_s,
                active=active,
            )
            self._send_json(200, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            logger.error("Unexpected live view heartbeat error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def _user_can_control_ptz(self, camera_name: str, ptz_config: Dict, action: str):
        if not ptz_config.get("enabled"):
            return False
        user = self._public_session_user()
        if not user:
            return False
        allowed_cameras = user.get("ptz_cameras") or []
        role = effective_user_role(user)
        if role == "superadmin":
            return True
        if camera_name not in allowed_cameras:
            return False
        access = user.get("ptz_access", "presets")
        if access == "admin":
            return True
        if action == "preset":
            return access in {"presets", "manual"}
        return action == "manual" and access == "manual"

    def _ptz_owner(self):
        user = self._public_session_user()
        if user:
            return user.get("username") or "authenticated"
        return self.client_address[0] if self.client_address else "public"

    def _cancel_ptz_tour_resume(self, camera_name: str):
        with ptz_tour_resume_lock:
            timer = ptz_tour_resume_timers.pop(camera_name, None)
        if timer:
            timer.cancel()

    def _schedule_ptz_tour_resume(
        self, camera_name: str, auto_resume_s: int, owner: str
    ):
        if auto_resume_s <= 0:
            self._cancel_ptz_tour_resume(camera_name)
            return

        def resume_tour():
            with ptz_tour_resume_lock:
                current = ptz_tour_resume_timers.get(camera_name)
                if current is not timer:
                    return
                ptz_tour_resume_timers.pop(camera_name, None)
            camera_config = cameras_config.get(camera_name)
            if not camera_config:
                logger.warning(
                    "PTZ tour auto-resume skipped camera=%s reason=missing-camera",
                    camera_name,
                )
                return
            try:
                set_tour_state(
                    camera_name,
                    camera_config,
                    "resume",
                    owner=f"{owner}-auto-resume",
                    duration_s=1,
                )
                logger.info("PTZ tour auto-resumed camera=%s", camera_name)
            except Exception as exc:
                logger.warning(
                    "PTZ tour auto-resume failed camera=%s error=%s",
                    camera_name,
                    exc,
                )

        timer = threading.Timer(auto_resume_s, resume_tour)
        timer.daemon = True
        with ptz_tour_resume_lock:
            previous = ptz_tour_resume_timers.pop(camera_name, None)
            if previous:
                previous.cancel()
            ptz_tour_resume_timers[camera_name] = timer
        timer.start()

    def _mark_ptz_tour_paused_after_control(
        self,
        camera_name: str,
        ptz_config: Dict[str, Any],
        owner: str,
    ):
        tour_config = ptz_config.get("tour") or {}
        if not (isinstance(tour_config, dict) and tour_config.get("enabled")):
            self._cancel_ptz_tour_resume(camera_name)
            return None
        auto_resume_value = tour_config.get("auto_resume_s")
        if auto_resume_value is None or auto_resume_value == "":
            auto_resume_value = 1800
        auto_resume_s = max(0, int(auto_resume_value))
        tour_status = mark_tour_state(
            camera_name, "paused", owner, auto_resume_s=auto_resume_s
        )
        self._schedule_ptz_tour_resume(camera_name, auto_resume_s, owner)
        return tour_status

    def _handle_ptz_status_api(self, parsed_url):
        query = parse_qs(parsed_url.query)
        camera_name = (query.get("camera") or [""])[0]
        if not camera_name:
            self._send_json(400, {"error": "camera query parameter is required"})
            return
        if camera_name not in cameras_config:
            self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
            return
        # Unlike the PTZ control endpoints below, this is a read-only status
        # query with no _user_can_control_ptz check, so it needs its own
        # visibility gate: without one, an anonymous caller could enumerate
        # hidden/private cameras and read their lock/tour/owner state.
        user = self._public_session_user()
        if not self._camera_visible_to_public_user(camera_name, user):
            self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
            return
        self._send_json(200, ptz_status(camera_name))

    def _handle_ptz_preset_api(self):
        try:
            payload = self._read_json_body()
            camera_name = (payload.get("camera") or "").strip()
            preset_id = (payload.get("preset") or "").strip()
            if not camera_name or not preset_id:
                self._send_json(400, {"error": "camera and preset are required"})
                return
            logger.info(
                "PTZ preset request camera=%s preset=%s", camera_name, preset_id
            )
            camera_config = cameras_config.get(camera_name)
            if not camera_config:
                self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
                return
            ptz_config = camera_config.get("ptz") or {}
            if not self._user_can_control_ptz(camera_name, ptz_config, "preset"):
                logger.warning("PTZ preset denied camera=%s", camera_name)
                self._send_json(403, {"error": "PTZ presets are not allowed"})
                return
            focus_settle_s = float(ptz_config.get("post_preset_capture_delay_s") or 5.0)
            move_status_timeout_s = float(
                ptz_config.get("move_status_timeout_s") or 10.0
            )

            def _on_move_settled(idle: Optional[bool]) -> None:
                # idle is True once GetStatus confirms the pan/tilt/zoom has
                # actually stopped, False if it gave up waiting (camera
                # still reports MOVING after move_status_timeout_s), or None
                # if this camera doesn't expose MoveStatus at all. Either
                # way, focus_settle_s still runs afterwards -- MoveStatus
                # only covers physical motion, not autofocus, which ONVIF
                # doesn't expose a readiness signal for.
                request_camera_capture(
                    camera_name,
                    f"ptz preset {preset_id}",
                    delay_s=focus_settle_s,
                )

            result = goto_preset(
                camera_name,
                camera_config,
                preset_id,
                owner=self._ptz_owner(),
                duration_s=int(ptz_config.get("session_duration_s") or 60),
                on_move_settled=_on_move_settled,
                move_status_timeout_s=move_status_timeout_s,
            )
            tour_status = self._mark_ptz_tour_paused_after_control(
                camera_name, ptz_config, self._ptz_owner()
            )
            if tour_status:
                result["tour_status"] = tour_status
            result["capture"] = {
                "requested": True,
                "reason": f"ptz preset {preset_id}",
                "delay_s": focus_settle_s,
            }
            self._send_json(200, result)
        except PTZBackendUnavailable as exc:
            self._send_json(501, {"error": str(exc)})
        except PTZLocked as exc:
            self._send_json(423, {"error": str(exc)})
        except (PTZError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            logger.error("Unexpected PTZ preset error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def _handle_ptz_presets_api(self, parsed_url):
        try:
            query = parse_qs(parsed_url.query)
            camera_name = (query.get("camera") or [""])[0].strip()
            if not camera_name:
                self._send_json(400, {"error": "camera query parameter is required"})
                return
            camera_config = cameras_config.get(camera_name)
            if not camera_config:
                self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
                return
            ptz_config = camera_config.get("ptz") or {}
            if not self._user_can_control_ptz(camera_name, ptz_config, "preset"):
                logger.warning("PTZ preset discovery denied camera=%s", camera_name)
                self._send_json(403, {"error": "PTZ presets are not allowed"})
                return
            configured_presets = ptz_config.get("presets") or []
            if configured_presets:
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "camera": camera_name,
                        "presets": [
                            {"id": preset["id"], "name": preset["name"]}
                            for preset in normalize_presets(ptz_config)
                        ],
                        "source": "config",
                    },
                )
                return
            result = discover_presets(
                camera_name,
                camera_config,
                owner=self._ptz_owner(),
                duration_s=int(ptz_config.get("session_duration_s") or 60),
            )
            self._send_json(
                200,
                {
                    **result,
                    "presets": [
                        {"id": preset["id"], "name": preset["name"]}
                        for preset in result.get("presets", [])
                    ],
                    "source": "onvif",
                },
            )
        except PTZBackendUnavailable as exc:
            self._send_json(501, {"error": str(exc)})
        except PTZLocked as exc:
            self._send_json(423, {"error": str(exc)})
        except (PTZError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            logger.error("Unexpected PTZ presets error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def _handle_ptz_move_api(self):
        try:
            payload = self._read_json_body()
            camera_name = (payload.get("camera") or "").strip()
            if not camera_name:
                self._send_json(400, {"error": "camera is required"})
                return
            logger.info(
                "PTZ move request camera=%s pan=%s tilt=%s zoom=%s",
                camera_name,
                payload.get("pan"),
                payload.get("tilt"),
                payload.get("zoom"),
            )
            camera_config = cameras_config.get(camera_name)
            if not camera_config:
                self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
                return
            ptz_config = camera_config.get("ptz") or {}
            if not ptz_config.get("allow_manual_control", False):
                logger.warning(
                    "PTZ move denied camera=%s reason=manual-control-disabled",
                    camera_name,
                )
                self._send_json(
                    403,
                    {
                        "error": (
                            "Manual PTZ control is disabled for this camera. "
                            "Enable Allow manual movement in the camera PTZ settings."
                        )
                    },
                )
                return
            if not self._user_can_control_ptz(camera_name, ptz_config, "manual"):
                logger.warning("PTZ move denied camera=%s", camera_name)
                self._send_json(
                    403,
                    {
                        "error": (
                            "Manual PTZ control is not allowed for this user/camera. "
                            "A superadmin must grant this user manual PTZ access to the camera."
                        )
                    },
                )
                return
            speed = max(0.05, min(1.0, float(payload.get("speed") or 0.35)))
            move_duration_ms = max(
                50, min(2000, int(payload.get("move_duration_ms") or 250))
            )
            result = nudge_move(
                camera_name,
                camera_config,
                pan=float(payload.get("pan") or 0) * speed,
                tilt=float(payload.get("tilt") or 0) * speed,
                zoom=float(payload.get("zoom") or 0) * speed,
                move_duration_s=move_duration_ms / 1000,
                owner=self._ptz_owner(),
                duration_s=int(ptz_config.get("session_duration_s") or 60),
            )
            tour_config = ptz_config.get("tour") or {}
            if isinstance(tour_config, dict) and tour_config.get("enabled"):
                result["tour_status"] = self._mark_ptz_tour_paused_after_control(
                    camera_name, ptz_config, self._ptz_owner()
                )
            self._send_json(200, result)
        except PTZBackendUnavailable as exc:
            self._send_json(501, {"error": str(exc)})
        except PTZLocked as exc:
            self._send_json(423, {"error": str(exc)})
        except (PTZError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            logger.error("Unexpected PTZ move error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def _handle_ptz_focus_api(self):
        try:
            payload = self._read_json_body()
            camera_name = (payload.get("camera") or "").strip()
            if not camera_name:
                self._send_json(400, {"error": "camera is required"})
                return
            logger.info(
                "PTZ focus request camera=%s focus=%s",
                camera_name,
                payload.get("focus"),
            )
            camera_config = cameras_config.get(camera_name)
            if not camera_config:
                self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
                return
            ptz_config = camera_config.get("ptz") or {}
            if not (
                ptz_config.get("allow_manual_control", False)
                and self._user_can_control_ptz(camera_name, ptz_config, "manual")
            ):
                logger.warning("PTZ focus denied camera=%s", camera_name)
                self._send_json(403, {"error": "Manual PTZ control is not allowed"})
                return
            speed = max(0.05, min(1.0, float(payload.get("speed") or 0.35)))
            move_duration_ms = max(
                50, min(2000, int(payload.get("move_duration_ms") or 250))
            )
            result = focus_move(
                camera_name,
                camera_config,
                focus=float(payload.get("focus") or 0) * speed,
                move_duration_s=move_duration_ms / 1000,
                owner=self._ptz_owner(),
                duration_s=int(ptz_config.get("session_duration_s") or 60),
            )
            tour_config = ptz_config.get("tour") or {}
            if isinstance(tour_config, dict) and tour_config.get("enabled"):
                result["tour_status"] = self._mark_ptz_tour_paused_after_control(
                    camera_name, ptz_config, self._ptz_owner()
                )
            self._send_json(200, result)
        except PTZBackendUnavailable as exc:
            self._send_json(501, {"error": str(exc)})
        except PTZLocked as exc:
            self._send_json(423, {"error": str(exc)})
        except (PTZError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            logger.error("Unexpected PTZ focus error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def _handle_ptz_stop_api(self):
        try:
            payload = self._read_json_body()
            camera_name = (payload.get("camera") or "").strip()
            if not camera_name:
                self._send_json(400, {"error": "camera is required"})
                return
            logger.info("PTZ stop request camera=%s", camera_name)
            camera_config = cameras_config.get(camera_name)
            if not camera_config:
                self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
                return
            ptz_config = camera_config.get("ptz") or {}
            if not (
                ptz_config.get("allow_manual_control", False)
                and self._user_can_control_ptz(camera_name, ptz_config, "manual")
            ):
                logger.warning("PTZ stop denied camera=%s", camera_name)
                self._send_json(403, {"error": "Manual PTZ control is not allowed"})
                return
            result = stop_move(camera_name, camera_config)
            tour_status = self._mark_ptz_tour_paused_after_control(
                camera_name, ptz_config, self._ptz_owner()
            )
            if tour_status:
                result["tour_status"] = tour_status
            self._send_json(200, result)
        except PTZBackendUnavailable as exc:
            self._send_json(501, {"error": str(exc)})
        except PTZLocked as exc:
            self._send_json(423, {"error": str(exc)})
        except (PTZError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            logger.error("Unexpected PTZ stop error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def _handle_ptz_tour_api(self):
        try:
            payload = self._read_json_body()
            camera_name = (payload.get("camera") or "").strip()
            action = (payload.get("action") or "").strip().lower()
            if not camera_name or not action:
                self._send_json(400, {"error": "camera and action are required"})
                return
            logger.info("PTZ tour request camera=%s action=%s", camera_name, action)
            camera_config = cameras_config.get(camera_name)
            if not camera_config:
                self._send_json(404, {"error": f"Camera '{camera_name}' was not found"})
                return
            ptz_config = camera_config.get("ptz") or {}
            if not (
                ptz_config.get("allow_manual_control", False)
                and self._user_can_control_ptz(camera_name, ptz_config, "manual")
            ):
                logger.warning("PTZ tour denied camera=%s", camera_name)
                self._send_json(403, {"error": "Manual PTZ control is not allowed"})
                return
            owner = self._ptz_owner()
            result = set_tour_state(
                camera_name,
                camera_config,
                action,
                owner=owner,
                duration_s=int(ptz_config.get("session_duration_s") or 60),
            )
            if result.get("tour") == "pause":
                self._schedule_ptz_tour_resume(
                    camera_name, int(result.get("auto_resume_s") or 0), owner
                )
            elif result.get("tour") in {"resume", "start", "stop"}:
                self._cancel_ptz_tour_resume(camera_name)
            self._send_json(200, result)
        except PTZBackendUnavailable as exc:
            self._send_json(501, {"error": str(exc)})
        except PTZLocked as exc:
            self._send_json(423, {"error": str(exc)})
        except (PTZError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            logger.error("Unexpected PTZ tour error.", exc_info=True)
            self._send_json(500, {"error": str(exc)})

    def do_GET(self):
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/api/cameras":
            self._handle_cameras_api()
            return
        if parsed_url.path == "/api/timelapses":
            self._handle_timelapses_api(parsed_url)
            return
        if parsed_url.path == "/api/launches/preview":
            self._handle_launches_preview_api()
            return
        if parsed_url.path == "/api/launches/history":
            self._handle_launches_history_api()
            return
        if parsed_url.path == "/api/ptz/status":
            self._handle_ptz_status_api(parsed_url)
            return
        if parsed_url.path == "/api/ptz/presets":
            self._handle_ptz_presets_api(parsed_url)
            return
        if parsed_url.path == "/api/auth/status":
            self._handle_public_auth_status_api()
            return
        if parsed_url.path == "/api/go2rtc/status":
            self._handle_go2rtc_status_api()
            return
        if parsed_url.path == "/api/auth/basic-login":
            self._handle_public_basic_login_api()
            return
        if parsed_url.path == "/login":
            self._handle_public_login_page(parsed_url)
            return
        user = self._public_session_user()
        if (
            parsed_url.path == "/cameras.json"
            and not self._site_is_public()
            and not user
        ):
            self._send_public_auth_required()
            return

        # Decode+normalize once and run every check below against that same
        # canonical form, matching what translate_path will actually resolve
        # on disk (see canonical_request_path's docstring for why this
        # matters: checking the raw, still-encoded path let %2f-style
        # requests bypass camera-visibility checks entirely).
        canonical_path = canonical_request_path(parsed_url.path)

        if (
            canonical_path.startswith("/launches/")
            and not self._site_is_public()
            and not user
        ):
            self._send_public_auth_required()
            return
        camera_name = self._path_camera_name(canonical_path)
        if camera_name and not self._camera_visible_to_public_user(camera_name, user):
            self.send_error(404, "File not found")
            return
        if canonical_path.startswith("/launches/"):
            recording_camera = self._launches_recording_camera_name(canonical_path)
            if recording_camera and not self._camera_visible_to_public_user(
                recording_camera, user
            ):
                self.send_error(404, "File not found")
                return
        super().do_GET()

    def list_directory(self, path):
        # Directory listings would enumerate camera/launch names (including
        # ones marked hidden/authenticated-only) and internal file layout.
        # Nothing in the app links to or relies on them: the site root always
        # serves a generated index.html, and camera/timelapse listings go
        # through the JSON APIs above, which already apply visibility rules.
        self.send_error(404, "File not found")
        return None

    def do_POST(self):
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/api/auth/login":
            self._handle_public_login_api()
            return
        if parsed_url.path == "/api/auth/logout":
            self._handle_public_logout_api()
            return
        if parsed_url.path == "/api/auth/change-password":
            self._handle_public_change_password_api()
            return
        if parsed_url.path == "/api/live-view/heartbeat":
            self._handle_live_view_heartbeat_api()
            return
        if parsed_url.path == "/api/ptz/preset":
            self._handle_ptz_preset_api()
            return
        if parsed_url.path == "/api/ptz/move":
            self._handle_ptz_move_api()
            return
        if parsed_url.path == "/api/ptz/focus":
            self._handle_ptz_focus_api()
            return
        if parsed_url.path == "/api/ptz/stop":
            self._handle_ptz_stop_api()
            return
        if parsed_url.path == "/api/ptz/tour":
            self._handle_ptz_tour_api()
            return
        self.send_error(405, "Method Not Allowed")

    def end_headers(self):
        cache_control = self._cache_control_header()
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        if server_config.get("allow_cors"):
            allow_origin = _cors_allow_origin_for_request(
                self.headers.get("Origin"), server_config
            )
            if allow_origin:
                self.send_header("Access-Control-Allow-Origin", allow_origin)
                if allow_origin != "*":
                    self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Origin, Range, Content-Type, Accept, Authorization",
            )
            self.send_header(
                "Access-Control-Expose-Headers", "Content-Length, Content-Range"
            )
        super().end_headers()

    def do_OPTIONS(self):
        if server_config.get("allow_cors"):
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(405, "Method Not Allowed")


def server_run():
    server_class = http.server.ThreadingHTTPServer
    handler_class = partial(
        FenetreHTTPRequestHandler, directory=global_config["work_dir"]
    )

    listen_str = server_config.get("listen", "0.0.0.0:8888")
    try:
        host, port_str = listen_str.split(":")
        port = int(port_str)
        server_address = (host, port)
    except ValueError:
        logger.error(
            f"Invalid listen address format in http_server config: '{listen_str}'. It should be 'host:port'. Defaulting to 0.0.0.0:8888."
        )
        server_address = ("0.0.0.0", 8888)

    logger.info(f"Starting HTTP Server on {server_address}")
    httpd = server_class(server_address, handler_class)
    global http_server_instance
    http_server_instance = httpd
    logger.debug(f"HTTP server instance {http_server_instance} started.")
    try:
        httpd.serve_forever()
    except Exception as e:
        if not exit_event.is_set():
            logger.error(f"HTTP server crashed: {e}", exc_info=True)
    finally:
        logger.info(f"HTTP server {server_address} stopped.")


def stop_http_server():
    global http_server_instance, http_server_thread_global
    if http_server_instance:
        http_server_instance.shutdown()
        http_server_instance.server_close()
        http_server_instance = None
        logger.info("HTTP server shut down.")
    if http_server_thread_global and http_server_thread_global.is_alive():
        logger.info("Waiting for HTTP server thread to join...")
        http_server_thread_global.join(timeout=5)
        if http_server_thread_global.is_alive():
            logger.warning("HTTP server thread did not join in time.")
        else:
            logger.info("HTTP server thread joined.")
    http_server_thread_global = None


# --- Config Server Management ---
def run_admin_server_func(
    listeners: str, flask_app, fenetre_config_file: str, fenetre_pid_file: str
):
    """Runs the Flask admin server."""
    flask_app.config["FENETRE_CONFIG_FILE"] = fenetre_config_file
    flask_app.config["FENETRE_PID_FILE_PATH"] = fenetre_pid_file
    flask_app.config["EXIT_EVENT"] = exit_event

    global admin_server_instance_global
    try:
        logger.info(f"Starting admin server on {listeners}")
        waitress_serve(flask_app, listen=listeners, threads=4, _quiet=False)
    except SystemExit:
        logger.info("admin server shutting down (SystemExit caught).")
    except Exception as e:
        if not exit_event.is_set():
            logger.error(f"admin server crashed: {e}", exc_info=True)
    finally:
        logger.info(f"admin server stopped.")
        admin_server_instance_global = None


def stop_admin_server():
    global admin_server_thread_global, admin_server_instance_global
    logger.info("Attempting to shut down Config Server...")

    # Signaling shutdown to a blocking WSGI server in a thread is complex.
    # If waitress or werkzeug were run with a programmatic server object, we could call .shutdown() or similar.
    # Since we call serve() or run_simple() which block, we rely on exit_event and thread joining.
    # The most reliable way is if the server itself checks exit_event or has a shutdown endpoint.
    # For now, we set exit_event, which should be checked by Flask routes if they are long-running (not typical).
    # The server thread itself, if daemon, will be killed on app exit.
    # For SIGHUP reloads where we want to stop/start it, joining the thread is key.
    # If the server doesn't exit cleanly from exit_event, the join might hang or timeout.

    # Flask's development server (werkzeug.serving.run_simple) can be stopped by sending a SIGINT to the process,
    # or by making a request to a special /shutdown route (if implemented).
    # Waitress might need a similar mechanism or a more direct control.
    # For now, the primary mechanism is exit_event and thread join.
    # If admin_server_instance_global held a server object with a shutdown method, we'd call it here.
    # Since it doesn't (waitress_serve and werkzeug_run_simple are blocking calls),
    # we rely on the thread terminating when exit_event is set (if the server respects it) or during join.

    if admin_server_thread_global and admin_server_thread_global.is_alive():
        logger.info("Config server thread is alive. Waiting for it to join...")
        # exit_event is already set by the main shutdown_application or handle_sighup logic if it's a full stop.
        # If this is a specific stop for the config server (e.g. disabled in config), ensure exit_event is relevant.
        # For simplicity, assume global exit_event is the main control.
        admin_server_thread_global.join(timeout=10)  # Wait for thread to finish
        if admin_server_thread_global.is_alive():
            logger.warning(
                "Config server thread did not join in time. It might not support graceful shutdown perfectly."
            )
        else:
            logger.info("Config server thread joined.")
    else:
        logger.info("Config server thread already stopped or not started.")

    admin_server_thread_global = None
    admin_server_instance_global = None  # Ensure cleaned up


def create_and_start_and_watch_thread(
    f: Callable,
    name: str,
    arguments: List[str] = [],
    exp_backoff_limit: int = 0,
    camera_name_for_management: Optional[str] = None,
) -> None:
    failure_count = 0
    last_failure = datetime.now()
    # sleep_intervals is now managed globally by the reload logic if needed for specific cameras
    # global sleep_intervals
    # sleep_intervals = {} # This was problematic as it reset sleep_intervals on thread restart.
    # It should be initialized once globally or per camera by the main logic.

    thread_instance = None  # Keep a reference to the running thread
    managed_camera_config = None
    if camera_name_for_management and len(arguments) >= 2:
        candidate_config = arguments[1]
        if isinstance(candidate_config, dict):
            managed_camera_config = candidate_config

    while not exit_event.is_set():
        # Check if this thread (for a specific camera) should still be running
        current_camera_config = (
            cameras_config.get(camera_name_for_management)
            if camera_name_for_management
            else None
        )
        if camera_name_for_management and (
            current_camera_config is None
            or (
                managed_camera_config is not None
                and current_camera_config != managed_camera_config
            )
        ):
            logger.info(
                f"Camera {camera_name_for_management} removed or changed in config. Watchdog {name} stopping."
            )
            if thread_instance and thread_instance.is_alive():
                # The 'snap' function needs to respect exit_event to terminate gracefully.
                # Forcing a stop is harder; relying on exit_event being set for the thread.
                logger.info(
                    f"Thread {name} for {camera_name_for_management} should stop due to config removal or change."
                )
                request_camera_capture(camera_name_for_management, "config reload")
            return  # Exit the watchdog loop for this camera

        if not thread_instance or not thread_instance.is_alive():
            if thread_instance and camera_name_for_management:
                cam_conf = cameras_config.get(camera_name_for_management, {})
                run_camera_unavailable_command(
                    camera_name_for_management,
                    cam_conf,
                    f"thread {name} stopped unexpectedly",
                )

            # Prune old thread reference from active_camera_threads if it matches this watchdog's managed camera
            if (
                camera_name_for_management
                and active_camera_threads.get(camera_name_for_management, {}).get(
                    "watchdog_thread"
                )
                == thread_instance
            ):
                # This ensures we don't clear another thread's reference if names collide or structure changes
                pass  # The new thread will be added below

            thread_instance = Thread(target=f, daemon=False, name=name, args=arguments)

            # Store the new thread instance for management if it's a camera thread
            if camera_name_for_management:
                if camera_name_for_management not in active_camera_threads:
                    active_camera_threads[camera_name_for_management] = {}
                active_camera_threads[camera_name_for_management][
                    "watchdog_thread"
                ] = thread_instance
                # Also ensure sleep_intervals is initialized for this camera if not already
                if camera_name_for_management not in sleep_intervals:
                    cam_conf = cameras_config.get(camera_name_for_management, {})
                    fixed_snap_interval = cam_conf.get("snap_interval_s", None)
                    sleep_intervals[camera_name_for_management] = (
                        float(fixed_snap_interval)
                        if isinstance(fixed_snap_interval, (int, float))
                        else 60.0
                    )

            exp_backoff_delay = min(exp_backoff_limit, 2**failure_count)
            if datetime.now() - last_failure > timedelta(
                seconds=90
            ):  # Corrected timedelta
                failure_count = 0

            if failure_count > 0:  # Only log and sleep if it's a restart
                logger.info(
                    f"Thread {name} (re)start attempt {failure_count}. Delaying {exp_backoff_delay}s."
                )
                if camera_name_for_management:
                    metric_capture_failures_total.labels(
                        camera_name=camera_name_for_management
                    ).inc()
                interruptible_sleep(exp_backoff_delay, exit_event)

            failure_count += 1
            last_failure = datetime.now()
            try:
                thread_instance.start()
                logger.info(f"Thread {name} started.")
            except Exception as e:
                logger.error(f"Failed to start thread {name}: {e}", exc_info=True)
                thread_instance = None  # Ensure we try to restart it

        interruptible_sleep(5, exit_event)  # Check every 5 seconds


def update_cameras_metadata(cameras_configs: Dict, work_dir: str):
    """Regenerates cameras.json from the current camera config."""
    json_filepath = os.path.join(work_dir, "cameras.json")
    write_cameras_metadata(
        cameras_configs,
        global_config or {},
        timelapse_config or {},
        json_filepath,
    )


def sync_go2rtc_runtime(previous_config: Optional[Dict] = None):
    current_config = {
        "global": global_config or {},
        "cameras": cameras_config or {},
    }
    try:
        result = _sync_go2rtc_runtime(current_config, previous_config)
    except Exception as exc:
        logger.warning("go2rtc runtime sync failed: %s", exc, exc_info=True)
        return

    if result.get("api_synced"):
        started = " and started" if result.get("started") else ""
        logger.info(
            "go2rtc runtime synced%s with %d stream(s): %s",
            started,
            len(result.get("streams") or []),
            ", ".join(result.get("streams") or []),
        )
    elif result.get("enabled"):
        logger.warning("go2rtc runtime not synced: %s", result.get("warning"))
    else:
        logger.info("go2rtc not started; no enabled RTSP live streams configured.")


timelapse_thread_global = None
daylight_thread_global = None
archive_thread_global = None
frequent_timelapse_loop_thread_global = None
frequent_timelapse_scheduler_thread_global = None
disk_management_thread_global = None
launch_workflow_thread_global = None


def main(argv):
    del argv  # Unused.

    setup_logging()

    with open(FENETRE_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logger.info(f"PID {os.getpid()} written to {FENETRE_PID_FILE}")

    global exit_event
    exit_event = threading.Event()

    # Setup signal handling for SIGHUP for config reload and SIGINT/SIGTERM for graceful exit
    signal.signal(signal.SIGHUP, handle_sighup)
    signal.signal(signal.SIGINT, signal_handler_exit)  # Graceful exit on Ctrl+C
    signal.signal(
        signal.SIGTERM, signal_handler_exit
    )  # Graceful exit on kill/systemd stop

    # Initialize global sleep_intervals (important for camera threads)
    global sleep_intervals
    sleep_intervals = {}

    # These queues are global and should persist across reloads if fenetre.py itself isn't restarted.
    # If reload implies restarting these loops, then re-initialization might be needed in reload_configuration_logic
    global daylight_q, archive_q, frequent_timelapse_q, frequent_timelapse_scheduler_offset
    daylight_q = deque()
    archive_q = deque()
    frequent_timelapse_q = deque()
    frequent_timelapse_scheduler_offset = 0

    # All threads are started here. We don't start all at the same time to prevent cluttering the stdout and hiding some potentially useful warnings.
    global timelapse_thread_global, daylight_thread_global, archive_thread_global, frequent_timelapse_loop_thread_global, launch_workflow_thread_global

    # This starts the camera threads.
    load_and_apply_configuration(initial_load=True)  # Uses FLAGS.config by default

    global timelapse_queue_file
    timelapse_queue_file = os.path.join(
        global_config.get("work_dir"), "timelapse_queue.txt"
    )

    if not os.path.exists(timelapse_queue_file):
        open(timelapse_queue_file, "a").close()  # Create the file if it does not exist
    get_queue_size_and_set_metric(timelapse_queue_file, timelapse_queue_lock)
    cleanup_stale_timelapse_artifacts_for_all_cameras()
    queue_missing_daily_timelapses()

    logger.info("Disk management thread will start in 10s...")
    interruptible_sleep(10, exit_event)

    disk_management_thread_global = Thread(
        target=disk_management_loop, daemon=True, name="disk_management_loop"
    )
    disk_management_thread_global.start()
    logger.info(f"Starting thread {disk_management_thread_global.name}")

    logger.info("Archive thread will start in 10s...")
    interruptible_sleep(10, exit_event)
    archive_thread_global = Thread(
        target=archive_loop, daemon=True, name="archive_loop"
    )
    archive_thread_global.start()
    logger.info(f"Starting thread {archive_thread_global.name}")

    daily_cfg = timelapse_config.get("daily_timelapse")
    if daily_cfg and daily_cfg.get("enabled", True):
        logger.info("Timelapse thread will start in 10s...")
        interruptible_sleep(10, exit_event)
        timelapse_thread_global = Thread(
            target=timelapse_loop, daemon=True, name="timelapse_loop"
        )
        timelapse_thread_global.start()
        logger.info(f"Starting thread {timelapse_thread_global.name}")
    else:
        logger.warning("Daily timelapse is disabled.")

    logger.info("Daylight thread will start in 10s...")
    interruptible_sleep(10, exit_event)
    daylight_thread_global = Thread(
        target=daylight_loop, daemon=True, name="daylight_loop"
    )
    daylight_thread_global.start()
    logger.info(f"Starting thread {daylight_thread_global.name}")

    frequent_cfg = timelapse_config.get("frequent_timelapse")
    if frequent_cfg and frequent_cfg.get("enabled", True):
        logger.info("Frequent timelapse thread will start in 10s...")
        interruptible_sleep(10, exit_event)
        frequent_timelapse_loop_thread_global = Thread(
            target=frequent_timelapse_loop, daemon=True, name="frequent_timelapse_loop"
        )
        frequent_timelapse_loop_thread_global.start()
        logger.info(f"Starting thread {frequent_timelapse_loop_thread_global.name}")
        frequent_timelapse_scheduler_thread_global = Thread(
            target=frequent_timelapse_scheduler_loop,
            daemon=True,
            name="frequent_timelapse_scheduler_loop",
        )
        frequent_timelapse_scheduler_thread_global.start()
        logger.info(
            f"Starting thread {frequent_timelapse_scheduler_thread_global.name}"
        )
    else:
        logger.warning("Frequent timelapse scheduler is disabled.")

    logger.info("Launch workflow thread will start in 5s...")
    interruptible_sleep(5, exit_event)
    launch_workflow_thread_global = Thread(
        target=launch_workflow_loop, daemon=True, name="launch_workflow_loop"
    )
    launch_workflow_thread_global.start()
    logger.info(f"Starting thread {launch_workflow_thread_global.name}")

    try:
        while not exit_event.is_set():
            # Main loop can perform periodic checks or just wait for exit_event
            time.sleep(1)  # Keep main thread alive and responsive to signals
    except KeyboardInterrupt:  # Should be caught by SIGINT handler now
        logger.info(
            "KeyboardInterrupt caught in main loop (should have been handled by SIGINT). Exiting."
        )
        # This path should ideally not be taken if SIGINT handler works as expected.
    finally:
        logger.info("Main loop exiting. Cleaning up...")
        shutdown_application()


def load_and_apply_configuration(initial_load=False, config_file_override=None):
    """Loads configuration and applies it.
    If initial_load is True, it loads all configs and starts all services.
    If initial_load is False (on SIGHUP), it only reloads camera configs.
    """
    global server_config, cameras_config, global_config, admin_server_config, timelapse_config, flask_app_instance

    logger.info("Loading and applying configuration...")
    previous_runtime_config = None
    if not initial_load:
        previous_runtime_config = {
            "global": globals().get("global_config") or {},
            "cameras": globals().get("cameras_config") or {},
        }

    config_path_to_load = config_file_override if config_file_override else FLAGS.config
    if not config_path_to_load:
        logger.error("No configuration file path specified. Cannot load configuration.")
        return

    # Load new configuration
    (
        new_server_config,
        new_cameras_config,
        new_global_config,
        new_admin_server_config,
        new_timelapse_config,
    ) = config_load(config_path_to_load)

    if initial_load:
        server_config = new_server_config
        global_config = derive_global_config(new_global_config)
        admin_server_config = new_admin_server_config
        timelapse_config = new_timelapse_config
        setup_logging(
            global_config.get("log_dir"),
            global_config.get("logging_level"),
            log_max_bytes=global_config.get("log_max_bytes", 10000000),
            log_backup_count=global_config.get("log_backup_count", 5),
        )
        apply_module_levels(global_config.get("logging_levels", {}))
        configure_profiler(global_config)
        configure_mqtt_manager(global_config)
        try:
            if ensure_default_admin_user(config_path_to_load):
                logger.info(
                    "Created default admin user 'admin'. Change this password in Manage Users."
                )
        except Exception as e:
            logger.error("Failed to bootstrap default admin user: %s", e, exc_info=True)

        try:
            from .admin_server import app as imported_flask_app

            flask_app_instance = imported_flask_app
        except ImportError as e:
            logger.error(
                f"Failed to import Flask app from admin_server: {e}. Config server UI will not be available."
            )
            flask_app_instance = None

        # Start HTTP Server if enabled
        if server_config.get("enabled", False):
            http_server_thread_global = Thread(
                target=server_run, daemon=True, name="http_server"
            )
            http_server_thread_global.start()

        # Start Config Server if enabled
        if admin_server_config.get("enabled", False) and flask_app_instance:
            main_config_file_path = FLAGS.config
            pid_file_path = FENETRE_PID_FILE
            admin_server_thread_global = Thread(
                target=run_admin_server_func,
                args=(
                    admin_server_config.get("listen", "0.0.0.0:8889"),
                    flask_app_instance,
                    main_config_file_path,
                    pid_file_path,
                ),
                daemon=True,
                name="admin_server_flask",
            )
            admin_server_thread_global.start()
    else:  # This is a reload
        # If server was enabled and is now disabled, stop it
        if server_config.get("enabled", False) and not new_server_config.get(
            "enabled", False
        ):
            stop_http_server()
        server_config = new_server_config
        global_config = derive_global_config(new_global_config)
        admin_server_config = new_admin_server_config
        timelapse_config = new_timelapse_config
        configure_profiler(global_config)
        configure_mqtt_manager(global_config)

    # Update cameras_config and manage camera threads
    cameras_config = new_cameras_config
    if global_config.get("work_dir"):
        ensure_media_storage_layout(global_config)
        update_cameras_metadata(cameras_config, global_config["work_dir"])
        copy_public_html_files(global_config["work_dir"], global_config)
    else:
        logger.error(
            "work_dir not set in global config. Cannot update camera metadata."
        )

    sync_go2rtc_runtime(previous_runtime_config)

    manage_camera_threads()

    manage_camera_threads()


def manage_camera_threads():
    """Starts and stops camera threads based on the current cameras_config."""
    current_camera_names = set(cameras_config.keys())
    threads_to_remove = []

    # Stop threads for removed or disabled cameras
    for cam_name, thread_info in list(active_camera_threads.items()):
        current_camera_config = cameras_config.get(cam_name)
        managed_camera_config = thread_info.get("camera_config")
        config_changed = (
            current_camera_config is not None
            and managed_camera_config is not None
            and managed_camera_config != current_camera_config
        )
        disabled = bool(
            current_camera_config and current_camera_config.get("disabled", False)
        )
        if cam_name not in current_camera_names or disabled or config_changed:
            reason = "changed" if config_changed else "removed or disabled"
            logger.info(f"Camera {cam_name} {reason}. Stopping its threads.")
            request_camera_capture(cam_name, "config reload")
            if (
                "watchdog_thread" in thread_info
                and thread_info["watchdog_thread"].is_alive()
            ):
                thread_info["watchdog_thread"].join(timeout=5)
            if (
                "watchdog_manager_thread" in thread_info
                and thread_info["watchdog_manager_thread"].is_alive()
            ):
                # The watchdog manager will see the camera is gone/changed and exit.
                # We join to ensure it cleans up.
                thread_info["watchdog_manager_thread"].join(timeout=5)
            if (
                "gopro_utility" in thread_info
                and thread_info["gopro_utility"].is_alive()
            ):
                thread_info["gopro_utility"].stop()
                thread_info["gopro_utility"].join(timeout=5)
            if mqtt_manager:
                mqtt_manager.publish_camera_state(cam_name, False)
            threads_to_remove.append(cam_name)

    for cam_name in threads_to_remove:
        if cam_name in active_camera_threads:
            del active_camera_threads[cam_name]
        if cam_name in sleep_intervals:
            del sleep_intervals[cam_name]

    # Start threads for new or enabled cameras
    for cam_name, cam_conf in cameras_config.items():
        if (
            cam_conf.get("disabled", False)
            or cam_conf.get("source") == "external_website"
        ):
            continue

        if (
            cam_name not in active_camera_threads
            or not active_camera_threads[cam_name]
            .get("watchdog_manager_thread", {})
            .is_alive()
        ):
            logger.info(f"Starting/Restarting threads for camera {cam_name}")

            # Initialize sleep interval
            fixed_snap_interval = cam_conf.get("snap_interval_s", None)
            sleep_intervals[cam_name] = (
                float(fixed_snap_interval)
                if isinstance(fixed_snap_interval, (int, float))
                else 60.0
            )

            # Set exponential backoff limit based on camera type
            if (
                cam_conf.get("gopro_model")
                or cam_conf.get("capture_method") == "picamera2"
            ):
                exp_backoff_limit = 32
            else:
                exp_backoff_limit = 128

            # Start watchdog manager for the snap thread
            watchdog_name = f"{cam_name}_watchdog_manager"
            cam_watchdog_thread = Thread(
                target=create_and_start_and_watch_thread,
                daemon=True,
                name=watchdog_name,
                args=[
                    snap,
                    f"{cam_name}_snap",
                    [cam_name, cam_conf],
                    exp_backoff_limit,
                    cam_name,
                ],
            )
            cam_watchdog_thread.start()
            if cam_name not in active_camera_threads:
                active_camera_threads[cam_name] = {}
            active_camera_threads[cam_name]["camera_config"] = cam_conf
            active_camera_threads[cam_name][
                "watchdog_manager_thread"
            ] = cam_watchdog_thread

            # Start GoPro utility thread if needed
            gopro_model = cam_conf.get("gopro_model")
            if gopro_model:
                from .gopro import GoPro

                gopro_ip = cam_conf.get("gopro_ip")
                iface = cam_conf.get("iface")
                if not gopro_ip:
                    logger.error(
                        f"Camera {cam_name} specifies gopro_model '{gopro_model}' but no gopro_ip."
                    )
                else:
                    gopro_instance = GoPro(
                        ip_address=gopro_ip,
                        root_ca=cam_conf.get("gopro_root_ca"),
                        log_dir=global_config.get("log_dir"),
                        camera_config=cam_conf,
                        lat=cam_conf.get("lat"),
                        lon=cam_conf.get("lon"),
                        timezone=global_config.get("timezone"),
                        gopro_model=gopro_model,
                        gopro_usb=cam_conf.get("gopro_usb"),
                        iface=iface,
                    )

                    # The GoProUtilityThread is only for Hero 11 (OpenGoPro) models
                    if gopro_model in {"hero11", "hero9", "open_gopro"}:
                        if not _GOPRO_BLE_AVAILABLE or GoProUtilityThread is None:
                            logger.warning(
                                "Camera %s uses GoPro model '%s' but Bluetooth support "
                                "is disabled. Install the 'gopro' extra to enable it.",
                                cam_name,
                                gopro_model,
                            )
                        else:
                            gopro_utility_thread = GoProUtilityThread(
                                gopro_instance, cam_name, cam_conf, exit_event
                            )
                            gopro_utility_thread.start()
                            if cam_name not in active_camera_threads:
                                active_camera_threads[cam_name] = {}
                            active_camera_threads[cam_name][
                                "gopro_utility"
                            ] = gopro_utility_thread

                    active_camera_threads.setdefault(cam_name, {})[
                        "gopro_instance"
                    ] = gopro_instance
        else:
            # For existing, running cameras, we could update settings like sleep_interval here if they change.
            fixed_snap_interval = cam_conf.get("snap_interval_s", None)
            if fixed_snap_interval is not None:
                new_interval = float(fixed_snap_interval)
                if sleep_intervals.get(cam_name) != new_interval:
                    logger.info(
                        f"Updating snap interval for camera {cam_name} to {new_interval}s"
                    )
                    sleep_intervals[cam_name] = new_interval


def handle_sighup(signum, frame):
    """Signal handler for SIGHUP to reload configuration."""
    logger.info(f"SIGHUP received. Reloading configuration from {FLAGS.config}...")
    # Schedule the reload to happen in the main thread or a dedicated thread
    # to avoid issues with signal handlers and complex operations.
    # For now, directly calling, but be wary of re-entrancy or blocking issues.
    # A queue processed by the main loop would be more robust for production.
    load_and_apply_configuration()  # Uses FLAGS.config by default


def signal_handler_exit(signum, frame):
    """Signal handler for SIGINT and SIGTERM to gracefully shut down."""
    signal_name = signal.Signals(signum).name
    if exit_event.is_set():
        logger.warning(f"Forcing shutdown.")
        sys.exit(0)
    logger.info(f"{signal_name} received. Initiating graceful shutdown...")
    exit_event.set()  # Signal all threads to exit
    # The main loop's finally block will call shutdown_application()


def shutdown_application():
    """Cleans up resources before exiting."""
    logger.info("Starting application shutdown sequence...")
    exit_event.set()  # Ensure it's set for all threads

    # Stop camera threads and their utility threads
    for cam_name, thread_info in list(active_camera_threads.items()):
        logger.info(f"Stopping threads for camera {cam_name}...")
        watchdog_manager = thread_info.get(
            "watchdog_manager_thread"
        )  # The thread that runs create_and_start_and_watch_thread
        gopro_utility = thread_info.get("gopro_utility")

        # The create_and_start_and_watch_thread loop itself respects exit_event.
        # The 'snap' thread started by it also respects exit_event.
        # So, setting exit_event should lead to their termination.
        # GoProUtilityThread should also respect exit_event.

        if gopro_utility and gopro_utility.is_alive():
            gopro_utility.join(timeout=10)  # Wait for GoPro utility thread
            if gopro_utility.is_alive():
                logger.warning(
                    f"GoPro utility thread for {cam_name} did not exit gracefully."
                )

        # The watchdog_manager_thread (which runs create_and_start_and_watch_thread) will exit once exit_event is set.
        # It, in turn, manages the actual snap_thread. The snap_thread also checks exit_event.
        if watchdog_manager and watchdog_manager.is_alive():
            watchdog_manager.join(timeout=10)  # Wait for the manager of the snap thread
            if watchdog_manager.is_alive():
                logger.warning(
                    f"Watchdog manager thread for {cam_name} did not exit gracefully."
                )

        # The actual snap thread (watchdog_thread in older naming) is managed by create_and_start_and_watch_thread
        # and should have been joined by its manager if it was robust.
        # Double check if it's still there and alive (shouldn't be if manager joined)
        snap_thread = thread_info.get("watchdog_thread")
        if snap_thread and snap_thread.is_alive():
            snap_thread.join(timeout=10)
            if snap_thread.is_alive():
                logger.warning(
                    f"Snap thread {snap_thread.name} for {cam_name} did not exit gracefully."
                )

    # Stop HTTP server
    stop_http_server()

    # Stop Config Server
    stop_admin_server()

    global mqtt_manager
    if mqtt_manager:
        mqtt_manager.stop()
        mqtt_manager = None

    profiler.stop()

    # Stop Timelapse and Daylight threads
    global timelapse_thread_global, daylight_thread_global, launch_workflow_thread_global
    if timelapse_thread_global and timelapse_thread_global.is_alive():
        timelapse_thread_global.join(timeout=10)
        if timelapse_thread_global.is_alive():
            logger.warning("Timelapse thread did not exit gracefully.")
    if daylight_thread_global and daylight_thread_global.is_alive():
        daylight_thread_global.join(timeout=10)
        if daylight_thread_global.is_alive():
            logger.warning("Daylight thread did not exit gracefully.")
    if launch_workflow_thread_global and launch_workflow_thread_global.is_alive():
        launch_workflow_thread_global.join(timeout=10)
        if launch_workflow_thread_global.is_alive():
            logger.warning("Launch workflow thread did not exit gracefully.")

    # Clean up PID file
    try:
        if os.path.exists(FENETRE_PID_FILE):
            os.remove(FENETRE_PID_FILE)
            logger.info(f"PID file {FENETRE_PID_FILE} removed.")
    except IOError as e:
        logger.error(f"Error removing PID file: {e}", exc_info=True)

    logger.info("Application shutdown complete.")
    # sys.exit(0) # Explicitly exit. This might be too abrupt if called from signal handler context.
    # Rely on main thread exiting naturally after exit_event is processed.


def launch_workflow_loop():
    while not exit_event.is_set():
        workflow_config = (global_config or {}).get("launch_workflow") or {}
        if not workflow_config.get("enabled", False):
            interruptible_sleep(10, exit_event)
            continue
        try:
            result = run_due_launch_actions(
                {"global": global_config or {}, "cameras": cameras_config or {}},
                active_view_counter=active_live_view_count,
            )
            action_count = len(result.get("actions") or [])
            if action_count:
                logger.info(
                    "Launch workflow processed %s due action(s) dry_run=%s",
                    action_count,
                    result.get("dry_run"),
                )
        except Exception as exc:
            logger.warning("Launch workflow run failed: %s", exc, exc_info=True)
        interval_s = int(workflow_config.get("refresh_interval_s") or 300)
        interruptible_sleep(max(10, interval_s), exit_event)


def frequent_timelapse_scheduler_loop():
    """
    This is a loop that schedules timelapse creation for the current day periodically.
    """
    global frequent_timelapse_scheduler_offset
    while not exit_event.is_set():
        frequent_cfg = timelapse_config.get("frequent_timelapse")
        if not frequent_cfg or not frequent_cfg.get("enabled", True):
            interruptible_sleep(5, exit_event)
            continue
        interval = frequent_cfg.get("interval_s", 1200)
        camera_names = list(cameras_config.keys())
        if camera_names:
            offset = frequent_timelapse_scheduler_offset % len(camera_names)
            camera_names = camera_names[offset:] + camera_names[:offset]
            frequent_timelapse_scheduler_offset += 1

        queued_dirs = {item[0] for item in frequent_timelapse_q}
        for camera_name in camera_names:
            try:
                if not is_camera_timelapse_enabled(camera_name):
                    continue

                logger.info(f"Time to update the frequent timelapse for {camera_name}.")
                pic_dir, _ = get_pic_dir_and_filename(camera_name)
                if pic_dir in queued_dirs:
                    logger.info(
                        "Frequent timelapse already queued for %s; skipping duplicate.",
                        camera_name,
                    )
                    continue
                timelapse_settings_tuple = (
                    pic_dir,
                    frequent_cfg,
                )
                frequent_timelapse_q.append(timelapse_settings_tuple)
                queued_dirs.add(pic_dir)
            except Exception as e:
                logger.warning(
                    f"Error in frequent timelapse scheduler loop for camera {camera_name}: {e}"
                )
                logger.error(
                    f"Error in frequent timelapse scheduler loop for camera {camera_name}",
                    exc_info=True,
                )
        interruptible_sleep(interval, exit_event)


def frequent_timelapse_loop():
    while not exit_event.is_set():
        frequent_cfg = timelapse_config.get("frequent_timelapse")
        if not frequent_cfg or not frequent_cfg.get("enabled", True):
            interruptible_sleep(5, exit_event)
            continue
        if len(frequent_timelapse_q) == 0:
            time.sleep(5)
            continue
        timelapse_settings_tuple = frequent_timelapse_q.popleft()
        pic_dir, timelapse_settings = timelapse_settings_tuple
        camera_name = camera_name_from_day_dir(pic_dir)
        if not is_camera_timelapse_enabled(camera_name):
            logger.info(
                "Skipping frequent timelapse for disabled camera %s.", camera_name
            )
            continue
        if not os.path.isdir(pic_dir):
            logger.info("Skipping frequent timelapse for missing dir: %s", pic_dir)
            continue
        try:
            output_format = timelapse_settings.get("output_format", "file")
            timelapse_args = {
                "dir": pic_dir,
                "log_dir": global_config.get("log_dir"),
                "ffmpeg_options": timelapse_settings.get("ffmpeg_options", ""),
                "max_width": timelapse_settings.get("max_width"),
                "max_height": timelapse_settings.get("max_height"),
                "log_max_bytes": global_config.get("log_max_bytes", 10000000),
                "log_backup_count": global_config.get("log_backup_count", 5),
            }
            if timelapse_settings.get("framerate"):
                timelapse_args["framerate"] = timelapse_settings.get("framerate")
            if timelapse_settings.get("hls_segment_type"):
                timelapse_args["hls_segment_type"] = timelapse_settings.get(
                    "hls_segment_type"
                )
            if timelapse_settings.get("hls_segment_extension"):
                timelapse_args["hls_segment_extension"] = timelapse_settings.get(
                    "hls_segment_extension"
                )

            if output_format == "hls":
                result = run_serialized_background_job(
                    f"frequent_timelapse_hls:{pic_dir}",
                    create_incremental_hls_timelapse,
                    **timelapse_args,
                )
            else:
                timelapse_args["overwrite"] = True
                timelapse_args["two_pass"] = timelapse_settings.get(
                    "ffmpeg_2pass", False
                )
                if timelapse_settings.get("file_extension"):
                    timelapse_args["file_extension"] = timelapse_settings.get(
                        "file_extension"
                    )
                result = run_serialized_background_job(
                    f"frequent_timelapse:{pic_dir}",
                    create_timelapse,
                    **timelapse_args,
                )
            if result:
                metric_timelapses_created_total.labels(
                    camera_name=camera_name, type="frequent"
                ).inc()
            else:
                logger.error(
                    f"There was an error creating the timelapse for dir: {pic_dir}"
                )
        except FileExistsError:
            logger.warning(f"Found an existing timelapse in dir {pic_dir}, Skipping.")
        except Exception as e:
            logger.error(
                f"There was an error creating the timelapse for dir: {pic_dir}: {e}",
                exc_info=True,
            )
        time.sleep(5)


def cleanup_frequent_timelapse_artifacts(
    day_dir: str, frequent_timelapse_config: Dict, daily_timelapse_config: Dict
) -> List[str]:
    if not frequent_timelapse_config:
        return []

    base_name = os.path.basename(os.path.normpath(day_dir))
    deleted_paths = []

    def remove_file(path):
        if os.path.exists(path):
            os.remove(path)
            deleted_paths.append(path)
            logger.info("Deleted frequent timelapse artifact: %s", path)

    if frequent_timelapse_config.get("output_format") == "hls":
        remove_file(os.path.join(day_dir, f"{base_name}.m3u8"))
        remove_file(os.path.join(day_dir, f".{base_name}.hls-manifest.json"))
        remove_file(os.path.join(day_dir, "init.mp4"))
        for segment_path in glob.glob(os.path.join(day_dir, "segment-*.*")):
            remove_file(segment_path)

        legacy_segment_dir = os.path.join(day_dir, f"{base_name}.segments")
        if os.path.isdir(legacy_segment_dir):
            shutil.rmtree(legacy_segment_dir)
            deleted_paths.append(legacy_segment_dir)
            logger.info(
                "Deleted frequent timelapse artifact directory: %s",
                legacy_segment_dir,
            )
        return deleted_paths

    frequent_timelapse_file_extension = frequent_timelapse_config.get(
        "file_extension", "mp4"
    )
    if frequent_timelapse_file_extension != daily_timelapse_config.get(
        "file_extension", "webm"
    ):
        remove_file(
            os.path.join(
                day_dir,
                f"{base_name}.{frequent_timelapse_file_extension}",
            )
        )
    return deleted_paths


def timelapse_loop():
    """
    This is a loop to create the high quality daily timelapse. Typically these run at 60 fps, use v9 CPU encoding with a slow preset and 2 pass.
    This prevent overloading the system by creating new daily timelapses for all the cameras at the same time, this is a blocking thread to create them one at a time. Each timelapse can take several hours.
    """
    while not exit_event.is_set():
        daily_cfg = timelapse_config.get("daily_timelapse")
        if not daily_cfg or not daily_cfg.get("enabled", True):
            interruptible_sleep(5, exit_event)
            continue
        dir_to_process = get_next_from_timelapse_queue(
            timelapse_queue_file, timelapse_queue_lock
        )

        if dir_to_process:
            try:
                camera_name = camera_name_from_day_dir(dir_to_process)
                if not is_camera_timelapse_enabled(camera_name):
                    logger.info(
                        "Removing queued daily timelapse for disabled camera %s: %s",
                        camera_name,
                        dir_to_process,
                    )
                    remove_from_timelapse_queue(
                        dir_to_process, timelapse_queue_file, timelapse_queue_lock
                    )
                    time.sleep(1)
                    continue
                if not os.path.isdir(dir_to_process):
                    logger.warning(
                        "Removing queued daily timelapse for missing directory: %s",
                        dir_to_process,
                    )
                    remove_from_timelapse_queue(
                        dir_to_process, timelapse_queue_file, timelapse_queue_lock
                    )
                    time.sleep(1)
                    continue
                result = run_serialized_background_job(
                    f"daily_timelapse:{dir_to_process}",
                    create_timelapse,
                    dir=dir_to_process,
                    overwrite=True,
                    two_pass=daily_cfg.get("ffmpeg_2pass", True),
                    log_dir=global_config.get("log_dir"),
                    ffmpeg_options=daily_cfg.get(
                        "ffmpeg_options",
                        "-c:v libvpx-vp9 -b:v 0 -crf 30 -deadline best",
                    ),
                    file_extension=daily_cfg.get("file_extension", "webm"),
                    framerate=daily_cfg.get("framerate", 60),
                    max_width=daily_cfg.get("max_width"),
                    max_height=daily_cfg.get("max_height"),
                    log_max_bytes=global_config.get("log_max_bytes", 10000000),
                    log_backup_count=global_config.get("log_backup_count", 5),
                )
                logging.info(f"ffmpeg ran. Result is {result}")
                if result:
                    metric_timelapses_created_total.labels(
                        camera_name=camera_name, type="daily"
                    ).inc()
                    cleanup_frequent_timelapse_artifacts(
                        dir_to_process,
                        timelapse_config.get("frequent_timelapse", {}),
                        timelapse_config.get("daily_timelapse", {}),
                    )
                    cleanup_stale_timelapse_artifacts(dir_to_process)
                    remove_from_timelapse_queue(
                        dir_to_process, timelapse_queue_file, timelapse_queue_lock
                    )
                else:
                    logger.error(
                        f"There was an error creating the timelapse for dir: {dir_to_process}"
                    )
            except FileExistsError:
                logger.warning(
                    f"Found an existing timelapse in dir {dir_to_process}, Skipping."
                )
            except Exception as e:
                logger.error(
                    f"There was an error creating the timelapse for dir: {dir_to_process}: {e}",
                    exc_info=True,
                )
        time.sleep(5)


def daylight_loop():
    """
    This is a loop generating the daylight bands, one at a time.
    """
    while not exit_event.is_set():
        if len(daylight_q) > 0:
            camera_name, daily_pic_dir, sky_area = daylight_q.popleft()
            try:
                logger.info(
                    f"Running daylight in {daily_pic_dir} with sky_area {sky_area}"
                )
                run_serialized_background_job(
                    f"daylight:{daily_pic_dir}",
                    run_end_of_day,
                    camera_name,
                    daily_pic_dir,
                    sky_area,
                )
                daily_cfg = timelapse_config.get("daily_timelapse")
                if (
                    daily_cfg
                    and daily_cfg.get("enabled", True)
                    and is_camera_timelapse_enabled(camera_name)
                ):
                    add_to_timelapse_queue(
                        daily_pic_dir, timelapse_queue_file, timelapse_queue_lock
                    )
                archive_q.append(daily_pic_dir)
            except Exception as e:
                logger.warning(f"Could not process daylight for {daily_pic_dir}: {e}")
                logger.error(
                    f"Error processing daylight for {daily_pic_dir}", exc_info=True
                )
        time.sleep(1)


def get_dir_size(path="."):
    total_size = 0
    # followlinks=True so a relocated media_dir (photos/launches symlinked out of
    # work_dir, see media_storage.py) is still counted towards work_dir's size.
    for dirpath, dirnames, filenames in os.walk(path, followlinks=True):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if it is symbolic link
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)
    return total_size


def _sorted_day_dirs(camera_dir: str) -> List[str]:
    if not os.path.isdir(camera_dir):
        return []
    day_dirs = [
        d.path
        for d in os.scandir(camera_dir)
        if d.is_dir() and DATE_DIR_PATTERN.match(d.name)
    ]
    return sorted(day_dirs, key=lambda path: os.path.basename(path))


def _daily_timelapse_path(day_dir: str) -> Optional[str]:
    daily_cfg = timelapse_config.get("daily_timelapse", {}) or {}
    extension = daily_cfg.get("file_extension") or "mp4"
    candidate = os.path.join(
        day_dir, f"{os.path.basename(os.path.normpath(day_dir))}.{extension}"
    )
    if os.path.isfile(candidate):
        return candidate
    return None


def _daily_timelapse_candidate_paths(day_dir: str) -> List[str]:
    date_name = os.path.basename(os.path.normpath(day_dir))
    paths = []
    for extension in TIMELAPSE_VIDEO_EXTENSIONS:
        if extension == "m3u8":
            continue
        paths.append(os.path.join(day_dir, f"{date_name}.{extension}"))
    return paths


def cleanup_stale_timelapse_artifacts(day_dir: str, dry_run: bool = False) -> int:
    """Remove interrupted or duplicate daily timelapse files for one day dir."""
    if not os.path.isdir(day_dir):
        return 0
    removed = 0
    daily_path = _daily_timelapse_path(day_dir)

    stale_patterns = [
        os.path.join(day_dir, ".*.tmp.*"),
        os.path.join(day_dir, "*.tmp.*"),
    ]
    date_name = os.path.basename(os.path.normpath(day_dir))
    for extension in TIMELAPSE_VIDEO_EXTENSIONS:
        stale_patterns.append(os.path.join(day_dir, f"{date_name}.{extension}"))

    for path in sorted(
        {path for pattern in stale_patterns for path in glob.glob(pattern)}
    ):
        if not os.path.isfile(path):
            continue
        if daily_path and os.path.abspath(path) == os.path.abspath(daily_path):
            continue
        should_remove = ".tmp." in os.path.basename(path) or os.path.getsize(path) <= 0
        if daily_path and path in _daily_timelapse_candidate_paths(day_dir):
            should_remove = True
        if not should_remove:
            continue
        removed += 1
        if dry_run:
            logger.info("[DRY RUN] Would remove stale timelapse artifact %s", path)
        else:
            logger.info("Removing stale timelapse artifact %s", path)
            os.remove(path)
    return removed


def cleanup_stale_timelapse_artifacts_for_all_cameras() -> int:
    pic_dir = global_config.get("pic_dir")
    if not pic_dir or not os.path.isdir(pic_dir):
        return 0
    removed = 0
    for camera_name in sorted(cameras_config):
        camera_dir = os.path.join(pic_dir, camera_name)
        for day_dir in _sorted_day_dirs(camera_dir):
            removed += cleanup_stale_timelapse_artifacts(day_dir)
    if removed:
        logger.info("Removed %s stale timelapse artifacts.", removed)
    return removed


def _date_from_day_dir(day_dir: str) -> Optional[date]:
    date_name = os.path.basename(os.path.normpath(day_dir))
    if not DATE_DIR_PATTERN.match(date_name):
        return None
    try:
        return datetime.strptime(date_name, "%Y-%m-%d").date()
    except ValueError:
        return None


def _today_date_for_config() -> date:
    timezone_name = global_config.get("timezone", "UTC")
    try:
        timezone = pytz.timezone(timezone_name)
    except pytz.UnknownTimeZoneError:
        timezone = pytz.UTC
    return datetime.now(timezone).date()


def _day_dir_has_snapshots(day_dir: str) -> bool:
    for pattern in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG"):
        if glob.glob(os.path.join(day_dir, pattern)):
            return True
    return False


def _should_queue_daily_timelapse(day_dir: str) -> bool:
    daily_cfg = timelapse_config.get("daily_timelapse")
    if not daily_cfg or not daily_cfg.get("enabled", True):
        return False
    if not os.path.isdir(day_dir):
        return False
    day_date = _date_from_day_dir(day_dir)
    if day_date is None or day_date >= _today_date_for_config():
        return False
    camera_name = camera_name_from_day_dir(day_dir)
    if not is_camera_timelapse_enabled(camera_name):
        return False
    if _daily_timelapse_path(day_dir):
        return False
    return _day_dir_has_snapshots(day_dir)


def _is_current_day_dir(day_dir: str) -> bool:
    day_date = _date_from_day_dir(day_dir)
    return day_date is not None and day_date >= _today_date_for_config()


def queue_missing_daily_timelapses() -> int:
    """Queue past date folders that still have snapshots but no daily timelapse."""
    if not timelapse_queue_file:
        return 0
    queued = 0
    pic_dir = global_config.get("pic_dir")
    if not pic_dir or not os.path.isdir(pic_dir):
        return 0

    for camera_name in sorted(cameras_config):
        if not is_camera_timelapse_enabled(camera_name):
            continue
        camera_dir = os.path.join(pic_dir, camera_name)
        if not os.path.isdir(camera_dir):
            continue
        for day_dir in _sorted_day_dirs(camera_dir):
            if not _should_queue_daily_timelapse(day_dir):
                continue
            add_to_timelapse_queue(day_dir, timelapse_queue_file, timelapse_queue_lock)
            queued += 1

    if queued:
        logger.info("Queued %s missing daily timelapse directories.", queued)
    return queued


def _preserve_and_queue_missing_daily_timelapse(day_dir: str) -> bool:
    if not _should_queue_daily_timelapse(day_dir):
        return False
    add_to_timelapse_queue(day_dir, timelapse_queue_file, timelapse_queue_lock)
    logger.info(
        "Preserving %s because it has snapshots but no daily timelapse yet.",
        day_dir,
    )
    return True


def _remove_file_for_storage(path: str, dry_run: bool) -> int:
    if not os.path.isfile(path):
        return 0
    size = os.path.getsize(path)
    if dry_run:
        logger.info("[DRY RUN] Would delete %s to free %.2f MB", path, size / (1024**2))
    else:
        logger.info("Deleting %s to free %.2f MB", path, size / (1024**2))
        os.remove(path)
    return size


def _storage_entry_mtime(path: str) -> float:
    if os.path.isfile(path):
        return os.path.getmtime(path)
    latest_mtime = os.path.getmtime(path)
    for root, _dirs, files in os.walk(path):
        for filename in files:
            try:
                latest_mtime = max(
                    latest_mtime, os.path.getmtime(os.path.join(root, filename))
                )
            except FileNotFoundError:
                continue
    return latest_mtime


def _is_current_storage_entry(path: str) -> bool:
    try:
        timezone_name = (global_config or {}).get("timezone") or "UTC"
        tz = pytz.timezone(timezone_name)
        mtime_date = datetime.fromtimestamp(_storage_entry_mtime(path), tz).date()
        return mtime_date == datetime.now(tz).date()
    except Exception:
        return False


def _remove_path_for_storage(path: str, dry_run: bool) -> int:
    if os.path.isfile(path):
        return _remove_file_for_storage(path, dry_run)
    if not os.path.isdir(path):
        return 0
    size = get_dir_size(path)
    if dry_run:
        logger.info("[DRY RUN] Would delete %s to free %.2f MB", path, size / (1024**2))
    else:
        logger.info("Deleting %s to free %.2f MB", path, size / (1024**2))
        shutil.rmtree(path)
    return size


def _prune_launch_recordings_for_global_limit(
    work_dir: str, current_size_bytes: int, limit_bytes: int, dry_run: bool
) -> int:
    launches_dir = os.path.join(work_dir, "launches")
    if not os.path.isdir(launches_dir):
        return current_size_bytes

    entries = []
    for entry in os.scandir(launches_dir):
        if not entry.is_dir() and not entry.is_file():
            continue
        entries.append((_storage_entry_mtime(entry.path), entry.path))
    entries.sort(key=lambda item: item[0])

    for _mtime, path in entries:
        if current_size_bytes <= limit_bytes:
            break
        if _is_current_storage_entry(path):
            continue
        current_size_bytes -= _remove_path_for_storage(path, dry_run)
    return current_size_bytes


def _prune_snapshots_keep_daily_timelapse(
    camera_dir: str, current_size_bytes: int, limit_bytes: int, dry_run: bool
) -> int:
    for day_dir in _sorted_day_dirs(camera_dir):
        if current_size_bytes <= limit_bytes:
            break
        if _is_current_day_dir(day_dir):
            continue
        if not _daily_timelapse_path(day_dir):
            continue
        for pattern in (
            "*.jpg",
            "segment-*.*",
            "*.m3u8",
            "init.mp4",
            ".*.hls-manifest.json",
        ):
            for path in sorted(glob.glob(os.path.join(day_dir, pattern))):
                if current_size_bytes <= limit_bytes:
                    break
                current_size_bytes -= _remove_file_for_storage(path, dry_run)
        archive_marker = os.path.join(day_dir, "archived")
        if not dry_run and not os.path.exists(archive_marker):
            open(archive_marker, "w").close()
    return current_size_bytes


def _prune_daily_timelapses(
    camera_dir: str, current_size_bytes: int, limit_bytes: int, dry_run: bool
) -> int:
    for day_dir in _sorted_day_dirs(camera_dir):
        if current_size_bytes <= limit_bytes:
            break
        if _is_current_day_dir(day_dir):
            continue
        daily_path = _daily_timelapse_path(day_dir)
        if daily_path:
            current_size_bytes -= _remove_file_for_storage(daily_path, dry_run)
            continue
        if _preserve_and_queue_missing_daily_timelapse(day_dir):
            continue
        dir_size = get_dir_size(day_dir)
        if dry_run:
            logger.info(
                "[DRY RUN] Would delete %s to free %.2f MB",
                day_dir,
                dir_size / (1024**2),
            )
        else:
            logger.info("Deleting %s to free %.2f MB", day_dir, dir_size / (1024**2))
            shutil.rmtree(day_dir)
        current_size_bytes -= dir_size
    return current_size_bytes


def _storage_slugify_camera_name(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def _camera_storage_dirs(pic_dir: str, camera_name: str) -> List[str]:
    if not pic_dir:
        return []
    matches = []
    exact_dir = os.path.join(pic_dir, camera_name)
    if os.path.isdir(exact_dir):
        matches.append(exact_dir)
    if not os.path.isdir(pic_dir):
        return matches

    expected = {
        camera_name.casefold(),
        _storage_slugify_camera_name(camera_name).casefold(),
    }
    for entry in os.scandir(pic_dir):
        if not entry.is_dir():
            continue
        if entry.path in matches:
            continue
        if entry.name.casefold() in expected:
            matches.append(entry.path)
    return matches


def _effective_camera_storage_limit_gb(
    camera_limit_gb: int | float | None, global_limit_gb: int | float | None
) -> int | float | None:
    if camera_limit_gb is None:
        return None
    if global_limit_gb is None:
        return camera_limit_gb
    return min(camera_limit_gb, global_limit_gb)


def enforce_camera_storage_limit(
    camera_name: str,
    camera_config: Dict,
    storage_management_config: Dict,
    dry_run: bool,
) -> int:
    configured_camera_limit_gb = camera_config.get(
        "work_dir_max_size_GB", storage_management_config.get("camera_max_size_GB")
    )
    camera_limit_gb = _effective_camera_storage_limit_gb(
        configured_camera_limit_gb,
        storage_management_config.get("work_dir_max_size_GB"),
    )
    if camera_limit_gb is None:
        return 0

    camera_dirs = _camera_storage_dirs(global_config.get("pic_dir"), camera_name)
    if not camera_dirs:
        metric_camera_directory_size_bytes.labels(camera_name=camera_name).set(0)
        return 0

    current_size_bytes = sum(get_dir_size(camera_dir) for camera_dir in camera_dirs)
    metric_camera_directory_size_bytes.labels(camera_name=camera_name).set(
        current_size_bytes
    )
    limit_bytes = camera_limit_gb * (1024**3)

    if current_size_bytes <= limit_bytes:
        return current_size_bytes

    logger.info(
        "Camera %s is over its %.2f GB limit. Current size: %.2f GB.",
        camera_name,
        camera_limit_gb,
        current_size_bytes / (1024**3),
    )

    if storage_management_config.get("prune_snapshots_first", True):
        for camera_dir in camera_dirs:
            if current_size_bytes <= limit_bytes:
                break
            current_size_bytes = _prune_snapshots_keep_daily_timelapse(
                camera_dir, current_size_bytes, limit_bytes, dry_run
            )

    if current_size_bytes > limit_bytes:
        logger.info(
            "Camera %s is still over limit after snapshot pruning; trimming oldest daily timelapses.",
            camera_name,
        )
        for camera_dir in camera_dirs:
            if current_size_bytes <= limit_bytes:
                break
            current_size_bytes = _prune_daily_timelapses(
                camera_dir, current_size_bytes, limit_bytes, dry_run
            )

    metric_camera_directory_size_bytes.labels(camera_name=camera_name).set(
        current_size_bytes
    )
    return current_size_bytes


def disk_management_loop():
    """
    This is a loop that manages disk space.
    """
    storage_management_config = global_config.get("storage_management", {})
    if not storage_management_config.get("enabled", False):
        logger.info("Disk management is disabled.")
        return

    interval = storage_management_config.get("check_interval_s", 300)
    dry_run = storage_management_config.get("dry_run", True)

    while not exit_event.is_set():
        # Manage per-camera limits
        for camera_name, camera_config in cameras_config.items():
            try:
                enforce_camera_storage_limit(
                    camera_name, camera_config, storage_management_config, dry_run
                )
            except Exception as e:
                logger.warning(
                    f"Error in disk management loop for camera {camera_name}: {e}"
                )
                logger.error(
                    f"Error in disk management loop for camera {camera_name}",
                    exc_info=True,
                )

        # Manage global limit
        try:
            global_limit_gb = storage_management_config.get("work_dir_max_size_GB")
            if global_limit_gb is not None:
                work_dir = global_config.get("work_dir")
                current_work_dir_size = get_dir_size(work_dir)
                metric_work_directory_size_bytes.set(current_work_dir_size)
                global_limit_bytes = global_limit_gb * (1024**3)

                if current_work_dir_size > global_limit_bytes:
                    logger.info(
                        f"Global work_dir is over its limit of {global_limit_gb} GB. Current size: {current_work_dir_size / (1024**3):.2f} GB. Deleting oldest directories across all cameras."
                    )

                    all_day_dirs = []
                    for camera_name in cameras_config:
                        for camera_dir in _camera_storage_dirs(
                            global_config.get("pic_dir"), camera_name
                        ):
                            all_day_dirs.extend(
                                [
                                    d.path
                                    for d in os.scandir(camera_dir)
                                    if d.is_dir() and d.name != "daylight"
                                ]
                            )

                    all_day_dirs.sort(key=lambda x: os.path.basename(x))

                    for day_dir in all_day_dirs:
                        if current_work_dir_size <= global_limit_bytes:
                            break
                        if _is_current_day_dir(day_dir):
                            continue
                        daily_path = _daily_timelapse_path(day_dir)
                        if daily_path:
                            for pattern in (
                                "*.jpg",
                                "segment-*.*",
                                "*.m3u8",
                                "init.mp4",
                                ".*.hls-manifest.json",
                            ):
                                for path in sorted(
                                    glob.glob(os.path.join(day_dir, pattern))
                                ):
                                    if current_work_dir_size <= global_limit_bytes:
                                        break
                                    current_work_dir_size -= _remove_file_for_storage(
                                        path, dry_run
                                    )
                            if current_work_dir_size <= global_limit_bytes:
                                break
                            current_work_dir_size -= _remove_file_for_storage(
                                daily_path, dry_run
                            )
                        elif _preserve_and_queue_missing_daily_timelapse(day_dir):
                            continue
                        else:
                            dir_to_delete_size = get_dir_size(day_dir)
                            if dry_run:
                                logger.info(
                                    f"[DRY RUN] Would delete {day_dir} to free up {dir_to_delete_size / (1024**2):.2f} MB"
                                )
                            else:
                                logger.info(
                                    f"Deleting {day_dir} to free up {dir_to_delete_size / (1024**2):.2f} MB"
                                )
                                shutil.rmtree(day_dir)
                            current_work_dir_size -= dir_to_delete_size
                    if current_work_dir_size > global_limit_bytes:
                        logger.info(
                            "Global work_dir is still over limit after camera pruning; trimming oldest launch recordings."
                        )
                        current_work_dir_size = (
                            _prune_launch_recordings_for_global_limit(
                                work_dir,
                                current_work_dir_size,
                                global_limit_bytes,
                                dry_run,
                            )
                        )
        except Exception as e:
            logger.warning(f"Error in disk management loop for global limit: {e}")
            logger.error(
                "Error in disk management loop for global limit", exc_info=True
            )

        logger.debug(f"Disk management sleeping for {interval} seconds.")
        interruptible_sleep(interval, exit_event)


def archive_loop():
    """
    This is a loop with a blocking Thread to archive pictures one at a time.
    """
    while not exit_event.is_set():
        for camera_name, camera_config in cameras_config.items():
            try:
                camera_dir = os.path.join(global_config["pic_dir"], camera_name)
                scan_and_publish_metrics(camera_name, camera_dir, global_config)
                daydirs = list_unarchived_dirs(camera_dir)
                for daydir in daydirs:
                    archive_daydir(
                        daydir=daydir,
                        global_config=global_config,
                        cam=camera_name,
                        sky_area=camera_config.get("sky_area"),
                        dry_run=False,
                        # TODO: Enable this after making the daylight an external file queue
                        create_daylight_bands=False,
                        daylight_bands_queue_file=None,
                        create_timelapses=is_camera_timelapse_enabled(camera_name),
                        timelapse_queue_file=timelapse_queue_file,
                        timelapse_queue_file_lock=timelapse_queue_lock,
                    )
            except Exception as e:
                logger.warning(f"Error in archive loop for camera {camera_name}: {e}")
                logger.error(
                    f"Error in archive loop for camera {camera_name}", exc_info=True
                )
        interruptible_sleep(600, exit_event)


def run():
    """Entry point for the fenetre console script."""
    app.run(main)


if __name__ == "__main__":
    run()
