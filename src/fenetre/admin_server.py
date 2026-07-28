import base64
import hmac
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from io import BytesIO

import requests
import yaml
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageOps, UnidentifiedImageError
from prometheus_client import REGISTRY, Counter, Gauge, generate_latest
from werkzeug.exceptions import BadRequest

from fenetre.auth import (
    ADMIN_ROLES,
    authenticate_config_user_record,
    effective_user_role,
    ensure_default_admin_user,
    hash_password,
    user_has_password,
    verify_password,
)
from fenetre.cameras_metadata import write_cameras_metadata
from fenetre.gopro import GoPro
from fenetre.go2rtc import build_go2rtc_runtime_config, go2rtc_browser_port
from fenetre.http_auth import auth_from_camera_config
from fenetre.image_profiles import ImageProfileError, apply_image_profile
from fenetre.log_sanitizer import sanitize_text_for_logs
from fenetre.launch_workflow import preview_launch_workflow, run_due_launch_actions
from fenetre.ptz import discover_presets, set_lock
from fenetre.rtsp_capture import camera_local_command, rtsp_snapshot_command
from fenetre.ui_utils import copy_public_html_files

go2rtc_spawned_process = None

metric_pictures_taken_total = Counter(
    "pictures_taken_total", "Total number of pictures taken", ["camera_name"]
)
metric_last_successful_picture_timestamp = Gauge(
    "capture_last_success_timestamp",
    "Timestamp of the last successfully taken picture",
    ["camera_name"],
)
metric_capture_failures_total = Counter(
    "capture_failures_total", "Total number of capture failures", ["camera_name"]
)
metric_timelapses_created_total = Counter(
    "timelapses_created_total",
    "Total number of timelapses created",
    ["camera_name", "type"],
)
metric_timelapse_queue_size = Gauge(
    "timelapse_queue_size", "Number of timelapses in the queue"
)
metric_camera_directory_size_bytes = Gauge(
    "camera_directory_size_bytes",
    "Size of the camera directory in bytes",
    ["camera_name"],
)
metric_work_directory_size_bytes = Gauge(
    "work_dir_size_bytes", "Size of the work directory in bytes"
)
metric_directories_total = Gauge(
    "dir_total_count", "Total number of directories", ["camera_name"]
)
metric_directories_archived_total = Gauge(
    "dir_archived_count", "Number of archived directories", ["camera_name"]
)
metric_directories_timelapse_total = Gauge(
    "dir_timelapse_count",
    "Number of directories with a timelapse file",
    ["camera_name"],
)
metric_directories_daylight_total = Gauge(
    "dir_daylight_count",
    "Number of directories with a daylight.png file",
    ["camera_name"],
)
metric_picture_width_pixels = Gauge(
    "picture_width_pixels", "Width of the captured picture in pixels", ["camera_name"]
)
metric_picture_height_pixels = Gauge(
    "picture_height_pixels", "Height of the captured picture in pixels", ["camera_name"]
)
metric_picture_size_bytes = Gauge(
    "picture_size_bytes", "Size of the captured picture in bytes", ["camera_name"]
)
metric_picture_iso = Gauge(
    "picture_iso", "ISO value of the captured picture", ["camera_name"]
)
metric_picture_focal_length_mm = Gauge(
    "picture_focal_length_mm",
    "Focal length of the captured picture in mm",
    ["camera_name"],
)
metric_picture_aperture = Gauge(
    "picture_aperture", "Aperture value of the captured picture", ["camera_name"]
)
metric_picture_exposure_time_seconds = Gauge(
    "picture_exposure_time_seconds",
    "Exposure time of the captured picture in seconds",
    ["camera_name"],
)
metric_picture_white_balance = Gauge(
    "picture_white_balance",
    "White balance value of the captured picture",
    ["camera_name"],
)
metric_processing_time_seconds = Gauge(
    "capture_processing_time_seconds",
    "Time it took to fetch and process a new picture",
    ["camera_name"],
)
metric_sleep_time_seconds = Gauge(
    "capture_loop_sleep_time_seconds",
    "Time the camera sleeps between pictures",
    ["camera_name"],
)
metric_camera_mode = Gauge(
    "camera_mode", "Current camera mode with mode label", ["camera_name", "mode"]
)
metric_camera_ssim_value = Gauge(
    "camera_ssim_value", "Latest SSIM measurement", ["camera_name"]
)
metric_camera_ssim_target = Gauge(
    "camera_ssim_target", "Configured SSIM target", ["camera_name"]
)
metric_camera_online = Gauge(
    "camera_online", "Camera online status reported by the snap loop", ["camera_name"]
)
gopro_state_gauge = Gauge("gopro_state", "GoPro State", ["camera_name", "state_name"])
gopro_setting_gauge = Gauge(
    "gopro_setting", "GoPro Setting", ["camera_name", "setting_name"]
)

app = Flask(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _admin_auth_enabled() -> bool:
    if "FENETRE_ADMIN_AUTH_ENABLED" in app.config:
        return bool(app.config["FENETRE_ADMIN_AUTH_ENABLED"])
    return _env_bool("FENETRE_ADMIN_AUTH_ENABLED", True)


def _auth_failed_response():
    return Response(
        "Authentication required.\n",
        401,
        {"WWW-Authenticate": 'Basic realm="Fenetre Admin", charset="UTF-8"'},
    )


def _current_admin_user():
    user = getattr(request, "fenetre_admin_user", None)
    return user if isinstance(user, dict) else None


def _has_superadmin(users: dict) -> bool:
    return any(
        isinstance(user, dict)
        and not user.get("disabled", False)
        and effective_user_role(user) == "superadmin"
        for user in (users or {}).values()
    )


def _current_user_can_manage_users(config: dict) -> bool:
    user = _current_admin_user()
    if not user:
        return not _admin_auth_enabled()
    role = effective_user_role(user)
    if role == "superadmin":
        return True
    # Backward compatibility: allow the existing admin account to promote a
    # superadmin until one exists, then reserve user permission edits for
    # superadmins.
    return role == "admin" and not _has_superadmin(config.get("users") or {})


def _current_user_can_set_user_password(target_username: str) -> bool:
    if not _admin_auth_enabled():
        return True
    user = _current_admin_user()
    if not user:
        return False
    if effective_user_role(user) == "superadmin":
        return True
    return user.get("username") == target_username


@app.before_request
def require_admin_auth():
    if request.path == "/logout":
        return None
    if not _admin_auth_enabled():
        return None

    auth = request.authorization
    if not auth:
        return _auth_failed_response()

    config_file_path = app.config.get("FENETRE_CONFIG_FILE")
    if config_file_path:
        ensure_default_admin_user(config_file_path)
        user = authenticate_config_user_record(
            config_file_path, auth.username or "", auth.password or ""
        )
        if user and effective_user_role(user) in ADMIN_ROLES:
            request.fenetre_admin_user = user
            return None

    expected_username = app.config.get("FENETRE_ADMIN_USERNAME") or os.environ.get(
        "FENETRE_ADMIN_USERNAME"
    )
    expected_password = app.config.get("FENETRE_ADMIN_PASSWORD") or os.environ.get(
        "FENETRE_ADMIN_PASSWORD"
    )
    if expected_username is None or expected_password is None:
        return _auth_failed_response()
    username_ok = hmac.compare_digest(auth.username or "", str(expected_username))
    password_ok = hmac.compare_digest(auth.password or "", str(expected_password))
    if not (username_ok and password_ok):
        return _auth_failed_response()
    request.fenetre_admin_user = {
        "username": auth.username or "env-admin",
        "role": "superadmin",
        "ptz_access": "admin",
        "ptz_cameras": [],
    }
    return None


def _config_file_path():
    config_file_path = app.config.get("FENETRE_CONFIG_FILE")
    if not config_file_path:
        raise RuntimeError("FENETRE_CONFIG_FILE not set in app config.")
    return config_file_path


def _load_raw_config():
    config_file_path = _config_file_path()
    if not os.path.exists(config_file_path):
        raise FileNotFoundError(f"Configuration file not found: {config_file_path}")
    with open(config_file_path, "r") as f:
        return yaml.safe_load(f) or {}


def _get_effective_config(raw_config: dict) -> dict:
    """Return the actual Fenetre config mapping regardless of wrapper style.

    Some user configs are stored as:

        config:
          global: ...
          cameras: ...

    while Fenetre's runtime config is the mapping containing global/cameras/etc.
    Admin mutations must edit that effective mapping instead of accidentally creating
    top-level siblings such as `cameras:` next to `config:`.
    """
    if isinstance(raw_config, dict) and isinstance(raw_config.get("config"), dict):
        return raw_config["config"]
    return raw_config


def _merge_effective_config(raw_config: dict, effective_config: dict) -> dict:
    """Put an edited effective config back into the original file shape."""
    if isinstance(raw_config, dict) and isinstance(raw_config.get("config"), dict):
        updated = dict(raw_config)
        updated["config"] = effective_config
        return updated
    return effective_config


def _load_effective_config_with_raw() -> tuple[dict, dict]:
    raw_config = _load_raw_config()
    return raw_config, _get_effective_config(raw_config)


def _backup_config(config_file_path: str) -> str | None:
    if not os.path.exists(config_file_path):
        return None
    backup_path = f"{config_file_path}.bak.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    with open(config_file_path, "rb") as src, open(backup_path, "wb") as dst:
        dst.write(src.read())
    return backup_path


def _write_yaml_for_bind_mount(config_file_path: str, config_data: dict) -> str | None:
    """Write config safely when config.yaml is a Docker bind-mounted file.

    os.replace(tmp, config.yaml) can fail with EBUSY on single-file bind mounts, so
    we keep a timestamped backup and then truncate/write/fsync the mounted file.
    """
    backup_path = _backup_config(config_file_path)
    rendered = yaml.safe_dump(
        config_data, sort_keys=False, default_flow_style=False, indent=2
    )
    with open(config_file_path, "w") as f:
        f.write(rendered)
        f.flush()
        os.fsync(f.fileno())
    with open(config_file_path, "r") as f:
        written_config = yaml.safe_load(f) or {}
    if written_config != config_data:
        raise IOError(
            f"Configuration write verification failed for {config_file_path}."
        )
    return backup_path


def _config_write_metadata(config_file_path: str, backup_path: str | None) -> dict:
    stat = os.stat(config_file_path)
    return {
        "config_path": config_file_path,
        "backup": os.path.basename(backup_path) if backup_path else None,
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _fenetre_reload_signal_result() -> tuple[dict, int]:
    fenetre_pid_file_path = app.config.get("FENETRE_PID_FILE_PATH")
    if not fenetre_pid_file_path:
        return {"error": "FENETRE_PID_FILE_PATH not set in app config."}, 500
    if not os.path.exists(fenetre_pid_file_path):
        return (
            {
                "error": f"PID file not found: {fenetre_pid_file_path}. Cannot signal reload."
            },
            404,
        )
    try:
        with open(fenetre_pid_file_path, "r") as f:
            pid_str = f.read().strip()
        if not pid_str:
            return {"error": "PID file is empty."}, 500
        pid = int(pid_str)
        os.kill(pid, signal.SIGHUP)
        return {"message": f"Reload signal sent to process {pid}.", "pid": pid}, 200
    except ProcessLookupError:
        return (
            {
                "error": f"Process with PID read from {fenetre_pid_file_path} not found."
            },
            500,
        )
    except ValueError:
        return {"error": f"Invalid PID found in {fenetre_pid_file_path}."}, 500
    except Exception as e:
        return {"error": f"Error signaling reload: {str(e)}"}, 500


def _reload_after_config_write() -> dict:
    if app.config.get("FENETRE_RELOAD_ON_CONFIG_WRITE", True) is False:
        return {
            "ok": True,
            "skipped": True,
            "message": "Runtime reload after config write is disabled.",
        }
    result, status = _fenetre_reload_signal_result()
    payload = dict(result)
    payload["ok"] = status == 200
    if status != 200 and "warning" not in payload:
        payload["warning"] = payload.get("error", "Runtime reload failed.")
    return payload


def _sync_public_ui_files(config: dict) -> dict:
    work_dir = (config.get("global") or {}).get("work_dir")
    if not work_dir:
        return {"ok": False, "warning": "work_dir not set in global config."}
    copy_public_html_files(work_dir, config.get("global", {}))
    return {"ok": True, "message": "UI files synchronized successfully."}


def _rebuild_cameras_json(config: dict) -> dict:
    global_config = config.get("global") or {}
    work_dir = global_config.get("work_dir")
    if not work_dir:
        return {"ok": False, "warning": "work_dir not set in global configuration."}
    cameras_json_path = os.path.join(work_dir, "cameras.json")
    backup_path = None
    if os.path.exists(cameras_json_path):
        backup_path = (
            f"{cameras_json_path}.bak.{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
        )
        os.replace(cameras_json_path, backup_path)
    write_cameras_metadata(
        config.get("cameras") or {},
        global_config,
        config.get("timelapse") or {},
        cameras_json_path,
    )
    result = {
        "ok": True,
        "message": "cameras.json rebuilt successfully.",
        "path": cameras_json_path,
    }
    if backup_path:
        result["backup"] = os.path.basename(backup_path)
    return result


def _publish_public_artifacts(config: dict) -> dict:
    result = {}
    try:
        result["ui_sync"] = _sync_public_ui_files(config)
    except Exception as exc:
        result["ui_sync"] = {"ok": False, "warning": str(exc)}
    try:
        result["cameras_json"] = _rebuild_cameras_json(config)
    except Exception as exc:
        result["cameras_json"] = {"ok": False, "warning": str(exc)}
    return result


def _local_go2rtc_api_base(runtime_config: dict | None) -> str | None:
    if not runtime_config:
        return None
    listen = str((runtime_config.get("api") or {}).get("listen") or "").strip()
    if not listen:
        return None
    if listen.startswith(":"):
        return f"http://127.0.0.1{listen}"
    if listen.startswith("[") and "]:" in listen:
        port = listen.rsplit(":", 1)[-1]
        return f"http://127.0.0.1:{port}"
    if ":" in listen:
        host, port = listen.rsplit(":", 1)
        if host in {"", "0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        return f"http://{host}:{port}"
    return f"http://127.0.0.1:{listen}"


def _go2rtc_mode_allows_autostart() -> bool:
    mode = str(os.environ.get("FENETRE_GO2RTC", "auto")).strip().lower()
    return mode not in {"off", "false", "0", "no"}


def _start_go2rtc_if_needed(config_path: str) -> str | None:
    global go2rtc_spawned_process
    if not _go2rtc_mode_allows_autostart():
        return "FENETRE_GO2RTC disables bundled go2rtc autostart."
    if go2rtc_spawned_process and go2rtc_spawned_process.poll() is None:
        return None
    go2rtc_binary = shutil.which("go2rtc")
    if not go2rtc_binary:
        return "go2rtc binary was not found in PATH."
    try:
        go2rtc_spawned_process = subprocess.Popen(
            [go2rtc_binary, "-config", config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
    except OSError as exc:
        return f"failed to start go2rtc: {exc}"
    return None


def _sync_go2rtc_api(
    api_base: str,
    streams: dict,
    removed_streams: list[str],
) -> None:
    for stream_name, stream_source in streams.items():
        response = requests.put(
            f"{api_base}/api/streams",
            params={"name": stream_name, "src": stream_source},
            timeout=3,
        )
        response.raise_for_status()
    for stream_name in removed_streams:
        response = requests.delete(
            f"{api_base}/api/streams",
            params={"src": stream_name},
            timeout=3,
        )
        if response.status_code not in {200, 204, 404}:
            response.raise_for_status()


def _sync_go2rtc_runtime(
    config: dict,
    previous_config: dict | None = None,
) -> dict:
    runtime_config = build_go2rtc_runtime_config(config)
    previous_runtime_config = (
        build_go2rtc_runtime_config(previous_config) if previous_config else None
    )
    output_path = os.environ.get("FENETRE_GO2RTC_CONFIG", "/tmp/fenetre-go2rtc.yaml")
    streams = (runtime_config or {}).get("streams") or {}
    previous_streams = (previous_runtime_config or {}).get("streams") or {}
    removed_streams = sorted(set(previous_streams) - set(streams))
    result = {
        "enabled": bool(runtime_config),
        "config_path": output_path,
        "api_base": _local_go2rtc_api_base(runtime_config or previous_runtime_config),
        "streams": sorted(streams.keys()),
        "removed_streams": removed_streams,
        "api_synced": False,
        "warning": None,
    }
    if not runtime_config and not removed_streams:
        return result

    if runtime_config:
        with open(output_path, "w") as output_file:
            yaml.safe_dump(runtime_config, output_file, sort_keys=False)
    elif os.path.exists(output_path):
        os.remove(output_path)

    api_base = _local_go2rtc_api_base(runtime_config or previous_runtime_config)
    if not api_base:
        result["warning"] = "go2rtc API listen address is disabled."
        return result

    try:
        _sync_go2rtc_api(api_base, streams, removed_streams)
        result["api_synced"] = True
    except requests.RequestException as exc:
        if not runtime_config:
            result["warning"] = sanitize_text_for_logs(f"go2rtc API sync failed: {exc}")
            return result
        start_warning = _start_go2rtc_if_needed(output_path)
        if start_warning:
            result["warning"] = sanitize_text_for_logs(
                f"go2rtc API sync failed: {exc}; {start_warning}"
            )
            return result
        try:
            _sync_go2rtc_api(api_base, streams, removed_streams)
            result["api_synced"] = True
            result["started"] = True
        except requests.RequestException as retry_exc:
            result["warning"] = sanitize_text_for_logs(
                f"go2rtc API sync failed: {retry_exc}"
            )
    return result


def _go2rtc_runtime_status(config: dict) -> dict:
    global_config = config.get("global") or {}
    go2rtc_config = (
        global_config.get("go2rtc") if isinstance(global_config, dict) else {}
    ) or {}
    if not isinstance(go2rtc_config, dict):
        go2rtc_config = {}

    runtime_config = build_go2rtc_runtime_config(config)
    api_base = _local_go2rtc_api_base(runtime_config)
    streams = (runtime_config or {}).get("streams") or {}
    spawned_running = bool(
        go2rtc_spawned_process and go2rtc_spawned_process.poll() is None
    )
    status = {
        "configured_enabled": bool(go2rtc_config.get("enabled")),
        "runtime_enabled": bool(runtime_config),
        "autostart_enabled": _go2rtc_mode_allows_autostart(),
        "binary_path": shutil.which("go2rtc"),
        "config_path": os.environ.get(
            "FENETRE_GO2RTC_CONFIG", "/tmp/fenetre-go2rtc.yaml"
        ),
        "api_base": api_base,
        "base_url_configured": bool(str(go2rtc_config.get("base_url") or "").strip()),
        "base_urls_configured": bool(go2rtc_config.get("base_urls") or {}),
        "base_url_hosts": sorted((go2rtc_config.get("base_urls") or {}).keys()),
        "same_host_fallback_enabled": not bool(
            str(go2rtc_config.get("base_url") or "").strip()
        ),
        "same_host_port": go2rtc_browser_port(global_config),
        "streams": sorted(streams.keys()),
        "stream_count": len(streams),
        "spawned_pid": go2rtc_spawned_process.pid if spawned_running else None,
        "spawned_running": spawned_running,
        "api_reachable": False,
        "api_status_code": None,
        "api_error": None,
        "warning": None,
    }
    if not go2rtc_config.get("enabled"):
        status["warning"] = "global.go2rtc.enabled is false."
        return status
    if not runtime_config:
        status["warning"] = (
            "go2rtc is enabled, but no camera has an enabled rtsp_url or "
            "ptz_rtsp_url."
        )
        return status
    if not api_base:
        status["api_error"] = "go2rtc API listen address is disabled."
        return status

    try:
        response = requests.get(f"{api_base}/api/streams", timeout=3)
        status["api_status_code"] = response.status_code
        response.raise_for_status()
        status["api_reachable"] = True
    except requests.RequestException as exc:
        status["api_error"] = sanitize_text_for_logs(str(exc))
        status["warning"] = "go2rtc API is not reachable from Fenetre."
    return status


def _ensure_go2rtc_enabled_for_camera(config: dict, camera: dict) -> None:
    if not (camera.get("rtsp_url") or camera.get("ptz_rtsp_url")):
        return
    if camera.get("go2rtc_enabled") is False:
        return
    global_config = config.setdefault("global", {})
    if not isinstance(global_config, dict):
        return
    go2rtc_config = global_config.setdefault("go2rtc", {})
    if not isinstance(go2rtc_config, dict):
        global_config["go2rtc"] = {"enabled": True}
        return
    go2rtc_config["enabled"] = True


def _merge_persistent_users(new_config: dict, existing_config: dict) -> None:
    existing_users = existing_config.get("users")
    if not isinstance(existing_users, dict):
        return

    submitted_users = new_config.get("users")
    if not isinstance(submitted_users, dict):
        new_config["users"] = yaml.safe_load(yaml.safe_dump(existing_users)) or {}
        return

    merged_users = yaml.safe_load(yaml.safe_dump(existing_users)) or {}
    for username, submitted_user in submitted_users.items():
        if not isinstance(submitted_user, dict):
            continue
        if not isinstance(merged_users.get(username), dict):
            merged_users[username] = submitted_user

    new_config["users"] = merged_users


def _cleanup_user_camera_access(config: dict) -> dict:
    valid_cameras = set((config.get("cameras") or {}).keys())
    removed = {}
    users = config.get("users") or {}
    if not isinstance(users, dict):
        return removed
    for username, user in users.items():
        if not isinstance(user, dict):
            continue
        camera_names = user.get("ptz_cameras") or []
        if not isinstance(camera_names, list):
            camera_names = []
        cleaned = [camera for camera in camera_names if camera in valid_cameras]
        dropped = sorted(set(camera_names) - set(cleaned))
        if dropped:
            removed[str(username)] = dropped
            user["ptz_cameras"] = cleaned
    return removed


def _replace_user_camera_access(config: dict, old_camera: str, new_camera: str) -> dict:
    changed = {}
    users = config.get("users") or {}
    if not isinstance(users, dict):
        return changed
    for username, user in users.items():
        if not isinstance(user, dict):
            continue
        camera_names = user.get("ptz_cameras") or []
        if not isinstance(camera_names, list) or old_camera not in camera_names:
            continue
        replaced = [
            new_camera if camera == old_camera else camera for camera in camera_names
        ]
        user["ptz_cameras"] = list(dict.fromkeys(replaced))
        changed[str(username)] = {"from": old_camera, "to": new_camera}
    return changed


def _replace_camera_order_reference(config: dict, old_camera: str, new_camera: str):
    ui_config = (config.get("global") or {}).get("ui") or {}
    camera_order = ui_config.get("camera_order")
    if not isinstance(camera_order, list) or old_camera not in camera_order:
        return []
    replaced = [new_camera if item == old_camera else item for item in camera_order]
    camera_names = set((config.get("cameras") or {}).keys())
    cleaned = []
    for item in replaced:
        if item in camera_names and item not in cleaned:
            cleaned.append(item)
    ui_config["camera_order"] = cleaned
    return cleaned


def _replace_launch_workflow_camera_reference(
    config: dict, old_camera: str, new_camera: str
) -> list[str]:
    changed_plans = []
    global_config = config.get("global") or {}
    for workflow_key in ("launch_workflow", "rocket_launches"):
        workflow = global_config.get(workflow_key)
        if not isinstance(workflow, dict):
            continue
        plans = workflow.get("plans")
        if isinstance(plans, dict):
            plan_items = plans.items()
        elif isinstance(plans, list):
            plan_items = [
                (str(plan.get("id") or index), plan)
                for index, plan in enumerate(plans)
                if isinstance(plan, dict)
            ]
        else:
            continue
        for plan_id, plan in plan_items:
            cameras = plan.get("cameras")
            if not isinstance(cameras, dict) or old_camera not in cameras:
                continue
            old_plan = cameras.pop(old_camera)
            cameras.setdefault(new_camera, old_plan)
            changed_plans.append(f"{workflow_key}:{plan_id}")
    return changed_plans


def _replace_camera_references(config: dict, old_camera: str, new_camera: str) -> dict:
    changes = {
        "user_ptz_access": _replace_user_camera_access(config, old_camera, new_camera),
        "camera_order": _replace_camera_order_reference(config, old_camera, new_camera),
        "launch_workflow": _replace_launch_workflow_camera_reference(
            config, old_camera, new_camera
        ),
    }
    ui_config = (config.get("global") or {}).get("ui") or {}
    if ui_config.get("fullscreen_camera") == old_camera:
        ui_config["fullscreen_camera"] = new_camera
        changes["fullscreen_camera"] = {"from": old_camera, "to": new_camera}
    return changes


def _slugify_camera_name(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    if not value:
        raise ValueError("Camera name cannot be empty.")
    return value


def _unique_destination_path(path: str) -> str:
    base, ext = os.path.splitext(path)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    candidate = f"{base}.migrated-{stamp}{ext}"
    index = 1
    while os.path.exists(candidate):
        candidate = f"{base}.migrated-{stamp}-{index}{ext}"
        index += 1
    return candidate


def _merge_directory_contents(src_dir: str, dst_dir: str) -> None:
    os.makedirs(dst_dir, exist_ok=True)
    for entry in os.listdir(src_dir):
        src_path = os.path.join(src_dir, entry)
        dst_path = os.path.join(dst_dir, entry)
        if os.path.isdir(src_path) and not os.path.islink(src_path):
            if os.path.exists(dst_path) and not os.path.isdir(dst_path):
                dst_path = _unique_destination_path(dst_path)
            _merge_directory_contents(src_path, dst_path)
            continue
        if os.path.exists(dst_path):
            dst_path = _unique_destination_path(dst_path)
        shutil.move(src_path, dst_path)
    shutil.rmtree(src_dir)


def _move_camera_media_dir(config: dict, old_camera: str, new_camera: str) -> dict:
    if old_camera == new_camera:
        return {"moved": False}
    work_dir = _work_dir_from_config(config)
    if not work_dir:
        return {"moved": False, "warning": "work_dir not set"}
    photos_dir = os.path.join(work_dir, "photos")
    src_dir = os.path.join(photos_dir, old_camera)
    dst_dir = os.path.join(photos_dir, new_camera)
    if not os.path.isdir(src_dir):
        return {"moved": False, "from": src_dir, "to": dst_dir}
    if os.path.abspath(src_dir) == os.path.abspath(dst_dir):
        return {"moved": False, "from": src_dir, "to": dst_dir}
    if os.path.exists(dst_dir):
        _merge_directory_contents(src_dir, dst_dir)
        return {"moved": True, "merged": True, "from": src_dir, "to": dst_dir}
    os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
    shutil.move(src_dir, dst_dir)
    return {"moved": True, "merged": False, "from": src_dir, "to": dst_dir}


GUIDED_CAMERA_KEYS = {
    "url",
    "http_auth",
    "rtsp_url",
    "ptz_rtsp_url",
    "local_command",
    "timeout_s",
    "cache_bust",
    "gather_metrics",
    "mozjpeg_optimize",
    "display_name",
    "template_vendor",
    "snapshot_template",
    "rtsp_template",
    "description",
    "disabled",
    "public",
    "visibility",
    "hidden",
    "ptz",
    "timelapse_enabled",
    "work_dir_max_size_GB",
    "snap_interval_s",
    "activity_interval_s",
    "ssim_setpoint",
    "ssim_area",
    "sky_area",
    "lat",
    "lon",
    "sunrise_sunset",
    "postprocessing",
}


def _fetch_local_command_bytes(command: str, timeout_s: int = 15):
    result = subprocess.run(
        shlex.split(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    if result.returncode != 0:
        stderr_preview = (result.stderr or b"").decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(
            f"local_command failed with exit code {result.returncode}. "
            f"stderr_last_500={stderr_preview!r}"
        )
    image_bytes = result.stdout
    try:
        image = Image.open(BytesIO(image_bytes))
    except UnidentifiedImageError as exc:
        stderr_preview = (result.stderr or b"").decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(
            "local_command did not return a valid image. "
            f"stdout_bytes={len(image_bytes or b'')}, "
            f"stdout_first_200={(image_bytes or b'')[:200]!r}, "
            f"stderr_last_500={stderr_preview!r}"
        ) from exc
    image.verify()
    reopened = Image.open(BytesIO(image_bytes))
    return image_bytes, "image/jpeg", reopened.size


def _fetch_snapshot_bytes(
    url: str,
    timeout_s: int = 15,
    cache_bust: bool = True,
    camera_config: dict | None = None,
):
    request_url = url
    if cache_bust:
        separator = "&" if "?" in request_url else "?"
        request_url = f"{request_url}{separator}_fenetre_test={int(datetime.now(timezone.utc).timestamp())}"
    headers = {
        "Accept": "image/*,*/*;q=0.8",
        "User-Agent": "Fenetre Admin Snapshot Tester",
    }
    request_kwargs = {"timeout": timeout_s, "headers": headers}
    request_auth = auth_from_camera_config(camera_config or {})
    if request_auth is not None:
        request_kwargs["auth"] = request_auth
    response = requests.get(request_url, **request_kwargs)
    response.raise_for_status()
    image_bytes = response.content
    image = Image.open(BytesIO(image_bytes))
    image.verify()
    reopened = Image.open(BytesIO(image_bytes))
    return (
        image_bytes,
        response.headers.get("content-type", "image/jpeg"),
        reopened.size,
    )


def _build_camera_config(
    payload: dict, existing_camera: dict | None = None
) -> tuple[str, dict]:
    existing_camera = existing_camera or {}
    name = _slugify_camera_name(payload.get("name"))
    url = (payload.get("url") or "").strip()
    source_type = payload.get("capture_source") or ("rtsp" if not url else "snapshot")
    rtsp_url = (payload.get("rtsp_url") or "").strip()
    local_command = (payload.get("local_command") or "").strip()
    if source_type == "snapshot" and not url:
        raise ValueError("Snapshot URL is required.")
    if source_type == "rtsp" and not (rtsp_url or local_command):
        raise ValueError("RTSP URL or local_command is required.")

    camera = {
        "timeout_s": int(payload.get("timeout_s") or 15),
        "capture_failure_interval_s": int(
            payload.get("capture_failure_interval_s") or 60
        ),
        "go2rtc_enabled": bool(payload.get("go2rtc_enabled", True)),
        "cache_bust": bool(payload.get("cache_bust", True)),
        "gather_metrics": bool(payload.get("gather_metrics", True)),
        "mozjpeg_optimize": bool(payload.get("mozjpeg_optimize", False)),
    }
    if source_type == "snapshot" and url:
        camera["url"] = url
        auth_username = (payload.get("snapshot_username") or "").strip()
        auth_password = payload.get("snapshot_password")
        existing_auth = existing_camera.get("http_auth") or {}
        if auth_username or auth_password or existing_auth:
            camera["http_auth"] = {
                "type": payload.get("snapshot_auth_type")
                or existing_auth.get("type")
                or "basic",
                "username": auth_username or existing_auth.get("username", ""),
                "password": auth_password or existing_auth.get("password", ""),
            }
    if rtsp_url:
        camera["rtsp_url"] = rtsp_url
    if payload.get("ptz_rtsp_url"):
        camera["ptz_rtsp_url"] = str(payload.get("ptz_rtsp_url")).strip()
    if source_type == "rtsp":
        command = local_command or rtsp_snapshot_command(rtsp_url)
        camera["local_command"] = (
            camera_local_command({"local_command": command, "rtsp_url": rtsp_url})
            or command
        )
    display_name = (payload.get("display_name") or "").strip()
    if display_name:
        camera["display_name"] = display_name
    for payload_key, camera_key in (
        ("template_vendor", "template_vendor"),
        ("snapshot_template", "snapshot_template"),
        ("rtsp_template", "rtsp_template"),
    ):
        value = (payload.get(payload_key) or "").strip()
        if value:
            camera[camera_key] = value
    description = (payload.get("description") or "").strip()
    if description:
        camera["description"] = description

    if payload.get("disabled"):
        camera["disabled"] = True
    visibility = payload.get("visibility")
    if visibility not in {"public", "authenticated", "hidden"}:
        if payload.get("hidden"):
            visibility = "hidden"
        elif payload.get("public", True) is False:
            visibility = "authenticated"
        else:
            visibility = "public"
    camera["visibility"] = visibility
    camera["public"] = visibility == "public"
    if visibility == "hidden":
        camera["hidden"] = True
    if payload.get("ptz_enabled"):
        presets = payload.get("ptz_presets") or []
        if isinstance(presets, str):
            presets = json.loads(presets) if presets.strip() else []
        existing_ptz = existing_camera.get("ptz") or {}
        ptz_config = dict(existing_ptz) if isinstance(existing_ptz, dict) else {}
        for legacy_key in ("compatibility", "ptz_profile", "vendor"):
            ptz_config.pop(legacy_key, None)
        ptz_config.update(
            {
                "enabled": True,
                "public": bool(payload.get("ptz_public", False)),
                "allow_presets": bool(payload.get("ptz_allow_presets", True)),
                "allow_manual_control": bool(
                    payload.get("ptz_allow_manual_control", False)
                ),
                "access_level": payload.get("ptz_access_level") or "presets",
                "host": (payload.get("ptz_host") or "").strip(),
                "port": int(payload.get("ptz_port") or 80),
                "username": (payload.get("ptz_username") or "").strip(),
                "password": payload.get("ptz_password")
                or ptz_config.get("password", ""),
                "profile_token": (payload.get("ptz_profile_token") or "").strip(),
                "presets": presets,
            }
        )
        capabilities = payload.get("ptz_capabilities")
        if isinstance(capabilities, dict):
            ptz_config["capabilities"] = {
                "pan": bool(capabilities.get("pan", True)),
                "tilt": bool(capabilities.get("tilt", True)),
                "zoom": bool(capabilities.get("zoom", True)),
                "focus": bool(capabilities.get("focus", False)),
            }
        elif any(
            key in payload
            for key in (
                "ptz_capability_pan",
                "ptz_capability_tilt",
                "ptz_capability_zoom",
                "ptz_capability_focus",
            )
        ):
            ptz_config["capabilities"] = {
                "pan": bool(payload.get("ptz_capability_pan", True)),
                "tilt": bool(payload.get("ptz_capability_tilt", True)),
                "zoom": bool(payload.get("ptz_capability_zoom", True)),
                "focus": bool(payload.get("ptz_capability_focus", False)),
            }
        existing_tour = ptz_config.get("tour") or {}
        tour_config = dict(existing_tour) if isinstance(existing_tour, dict) else {}
        if "ptz_tour_enabled" in payload:
            tour_config["enabled"] = bool(payload.get("ptz_tour_enabled"))
        if "ptz_tour_auto_resume_s" in payload:
            tour_config["auto_resume_s"] = int(
                payload.get("ptz_tour_auto_resume_s") or 1800
            )
        if payload.get("ptz_tour_backend"):
            tour_config["backend"] = str(payload.get("ptz_tour_backend")).strip()
        if tour_config:
            ptz_config["tour"] = tour_config
        camera["ptz"] = ptz_config
    if payload.get("timelapse_enabled") is not None:
        camera["timelapse_enabled"] = bool(payload.get("timelapse_enabled"))
    if payload.get("work_dir_max_size_GB"):
        camera["work_dir_max_size_GB"] = int(payload.get("work_dir_max_size_GB"))
    if payload.get("snap_interval_enabled"):
        camera["snap_interval_s"] = int(payload.get("snap_interval_s") or 60)
    if payload.get("activity_interval_enabled"):
        camera["activity_interval_s"] = int(payload.get("activity_interval_s") or 10)
    if payload.get("ssim_enabled"):
        camera["ssim_setpoint"] = float(payload.get("ssim_setpoint") or 0.85)
        if payload.get("ssim_area"):
            camera["ssim_area"] = payload.get("ssim_area")
    if payload.get("sky_area_enabled") and payload.get("sky_area"):
        camera["sky_area"] = payload.get("sky_area")

    if bool(payload.get("sunrise_sunset_enabled", True)):
        camera["lat"] = float(payload.get("lat") or 35.2828)
        camera["lon"] = float(payload.get("lon") or -120.6596)
        camera["sunrise_sunset"] = {
            "enabled": True,
            "interval_s": int(payload.get("sunrise_sunset_interval_s") or 15),
            "sunrise_offset_start_minutes": int(
                payload.get("sunrise_offset_start_minutes") or 45
            ),
            "sunrise_offset_end_minutes": int(
                payload.get("sunrise_offset_end_minutes") or 45
            ),
            "sunset_offset_start_minutes": int(
                payload.get("sunset_offset_start_minutes") or 45
            ),
            "sunset_offset_end_minutes": int(
                payload.get("sunset_offset_end_minutes") or 45
            ),
        }

    postprocessing = []
    for step in payload.get("postprocessing", []) or []:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        if step_type == "timestamp":
            postprocessing.append(
                {
                    "type": "timestamp",
                    "enabled": bool(step.get("enabled", True)),
                    "position": step.get("position") or "bottom_right",
                    "size": int(step.get("size") or 24),
                    "color": step.get("color") or "white",
                    "format": step.get("format") or "%Y-%m-%d %H:%M:%S %Z",
                }
            )
        elif step_type == "crop" and step.get("area"):
            postprocessing.append({"type": "crop", "area": step.get("area")})
        elif step_type == "resize":
            postprocessing.append(
                {
                    "type": "resize",
                    "width": int(step.get("width") or 1280),
                    "height": int(step.get("height") or 720),
                }
            )
        elif step_type == "awb":
            postprocessing.append({"type": "awb"})
    if postprocessing:
        camera["postprocessing"] = postprocessing
    return name, camera


def _merge_guided_camera_update(existing_camera: dict, camera: dict) -> dict:
    merged = {
        key: value
        for key, value in (existing_camera or {}).items()
        if key not in GUIDED_CAMERA_KEYS
    }
    merged.update(camera)
    return merged


def _normalize_user(payload: dict, existing: dict | None = None) -> tuple[str, dict]:
    username = (payload.get("username") or "").strip()
    if not username:
        raise ValueError("username is required.")
    if not re.match(r"^[A-Za-z0-9_.@-]+$", username):
        raise ValueError("username can only use letters, numbers, _, ., @, and -.")

    existing = dict(existing or {})
    role = payload.get("role") or existing.get("role") or "viewer"
    role = effective_user_role({"role": role})
    if role not in {"viewer", "operator", "admin", "superadmin"}:
        raise ValueError(
            "role must be viewer, operator, admin, superadmin, or superuser."
        )
    ptz_access = payload.get("ptz_access", existing.get("ptz_access", "presets"))
    if ptz_access not in {"none", "presets", "manual", "admin"}:
        raise ValueError("ptz_access must be none, presets, manual, or admin.")
    user = {
        "disabled": bool(payload.get("disabled", existing.get("disabled", False))),
        "role": role,
        "ptz_cameras": payload.get("ptz_cameras", existing.get("ptz_cameras", []))
        or [],
        "ptz_access": ptz_access,
    }
    if payload.get("password"):
        user["password_hash"] = hash_password(str(payload["password"]))
    elif existing.get("password_hash"):
        user["password_hash"] = existing["password_hash"]
    elif existing.get("password"):
        user["password"] = existing["password"]
    return username, user


def _ptz_capable_camera_names(config: dict) -> list[str]:
    cameras = config.get("cameras") or {}
    names = []
    for camera_name, camera_config in cameras.items():
        if not isinstance(camera_config, dict):
            continue
        ptz_config = camera_config.get("ptz") or {}
        if isinstance(ptz_config, dict) and ptz_config.get("enabled"):
            names.append(str(camera_name))
    return sorted(names)


def _work_dir_from_config(config: dict) -> str | None:
    return (config.get("global") or {}).get("work_dir")


def _dir_size(path: str) -> int:
    total = 0
    if not path or not os.path.exists(path):
        return 0
    for dirpath, _, filenames in os.walk(path):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            if not os.path.islink(full_path):
                total += os.path.getsize(full_path)
    return total


def _format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


@app.route("/metrics")
def metrics():
    return Response(generate_latest(REGISTRY), mimetype="text/plain")


@app.route("/api/go2rtc/status", methods=["GET"])
def go2rtc_status():
    try:
        _, config = _load_effective_config_with_raw()
        return jsonify(_go2rtc_runtime_status(config)), 200
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as e:
        return jsonify({"error": f"Error reading go2rtc status: {str(e)}"}), 500


@app.route("/config", methods=["GET"])
def get_config():
    try:
        raw_config, effective_config = _load_effective_config_with_raw()
        return jsonify({"config": effective_config}), 200
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as e:
        return jsonify({"error": f"Error reading configuration: {str(e)}"}), 500


@app.route("/api/storage/summary", methods=["GET"])
def storage_summary():
    try:
        _, config = _load_effective_config_with_raw()
        work_dir = _work_dir_from_config(config)
        storage_config = (config.get("global") or {}).get(
            "storage_management", {}
        ) or {}
        photos_dir = os.path.join(work_dir, "photos") if work_dir else None
        total_bytes = _dir_size(work_dir) if work_dir else 0

        cameras = []
        for name, camera_cfg in (config.get("cameras") or {}).items():
            camera_dir = os.path.join(photos_dir, name) if photos_dir else None
            size_bytes = _dir_size(camera_dir) if camera_dir else 0
            limit_gb = camera_cfg.get(
                "work_dir_max_size_GB", storage_config.get("camera_max_size_GB")
            )
            cameras.append(
                {
                    "name": name,
                    "bytes": size_bytes,
                    "display": _format_bytes(size_bytes),
                    "limit_GB": limit_gb,
                }
            )
        cameras.sort(key=lambda item: item["bytes"], reverse=True)

        return jsonify(
            {
                "work_dir": work_dir,
                "bytes": total_bytes,
                "display": _format_bytes(total_bytes),
                "limit_GB": storage_config.get("work_dir_max_size_GB"),
                "enabled": bool(storage_config.get("enabled", False)),
                "dry_run": bool(storage_config.get("dry_run", True)),
                "cameras": cameras,
            }
        )
    except Exception as e:
        return jsonify({"error": f"Failed to calculate storage summary: {str(e)}"}), 500


@app.route("/api/users", methods=["GET"])
def list_users():
    try:
        ensure_default_admin_user(_config_file_path())
        _, config = _load_effective_config_with_raw()
        users = config.get("users") or {}
        ptz_cameras = _ptz_capable_camera_names(config)
        current_user = _current_admin_user()
        current_role = effective_user_role(current_user)
        can_set_user_passwords = (not _admin_auth_enabled()) or (
            current_role == "superadmin"
        )
        public_users = []
        for username, user in users.items():
            public_users.append(
                {
                    "username": username,
                    "disabled": bool(user.get("disabled", False)),
                    "role": effective_user_role(user),
                    "ptz_cameras": user.get("ptz_cameras", []),
                    "ptz_access": user.get("ptz_access", "presets"),
                    "has_password": user_has_password(user),
                }
            )
        return jsonify(
            {
                "users": public_users,
                "cameras": ptz_cameras,
                "can_manage_users": _current_user_can_manage_users(config),
                "can_set_user_passwords": can_set_user_passwords,
                "current_user": (
                    {
                        "username": current_user.get("username"),
                        "role": current_role,
                    }
                    if current_user
                    else None
                ),
            }
        )
    except Exception as e:
        return jsonify({"error": f"Failed to list users: {str(e)}"}), 500


@app.route("/api/users", methods=["POST"])
def upsert_user():
    try:
        payload = request.get_json(force=True) or {}
        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        if not _current_user_can_manage_users(config):
            return jsonify({"error": "Only superadmins can manage users."}), 403
        users = config.setdefault("users", {})
        username = (payload.get("username") or "").strip()
        if payload.get("password") and not _current_user_can_set_user_password(
            username
        ):
            return (
                jsonify(
                    {
                        "error": (
                            "Only superadmins can change another user's password. "
                            "Use Change Password to update your own password."
                        )
                    }
                ),
                403,
            )
        ptz_cameras = set(_ptz_capable_camera_names(config))
        if isinstance(payload.get("ptz_cameras"), list):
            payload = dict(payload)
            payload["ptz_cameras"] = [
                camera_name
                for camera_name in payload.get("ptz_cameras", [])
                if camera_name in ptz_cameras
            ]
        username, user = _normalize_user(payload, users.get(payload.get("username")))
        users[username] = user
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        metadata = _config_write_metadata(config_file_path, backup_path)
        return jsonify(
            {
                "message": f"User '{username}' saved.",
                **metadata,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to save user: {str(e)}"}), 500


@app.route("/api/users/password", methods=["POST"])
def change_current_user_password():
    try:
        payload = request.get_json(force=True) or {}
        current_password = str(payload.get("current_password") or "")
        new_password = str(payload.get("new_password") or "")
        if not current_password or not new_password:
            return (
                jsonify({"error": "current_password and new_password are required."}),
                400,
            )
        if len(new_password) < 8:
            return (
                jsonify({"error": "New password must be at least 8 characters."}),
                400,
            )

        current_user = _current_admin_user()
        if not current_user:
            return jsonify({"error": "Authentication is required."}), 401
        username = (current_user.get("username") or "").strip()
        if not username:
            return jsonify({"error": "Current user is unknown."}), 400

        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        users = config.setdefault("users", {})
        user = users.get(username)
        if not isinstance(user, dict):
            return (
                jsonify(
                    {
                        "error": (
                            "The current admin user is not managed in config.yaml. "
                            "Set a config-backed user before changing passwords here."
                        )
                    }
                ),
                400,
            )
        if user.get("disabled", False) or not verify_password(user, current_password):
            return jsonify({"error": "Current password is incorrect."}), 401

        updated_user = dict(user)
        updated_user["password_hash"] = hash_password(new_password)
        updated_user.pop("password", None)
        updated_user["password_changed_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        users[username] = updated_user
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        metadata = _config_write_metadata(config_file_path, backup_path)
        return jsonify(
            {
                "message": "Password changed. Sign in again with the new password.",
                **metadata,
            }
        )
    except BadRequest:
        return (
            jsonify({"error": "Invalid JSON format in request body or empty body."}),
            400,
        )
    except Exception as e:
        return jsonify({"error": f"Failed to change password: {str(e)}"}), 500


@app.route("/api/users/<path:username>", methods=["DELETE"])
def delete_user(username):
    try:
        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        if not _current_user_can_manage_users(config):
            return jsonify({"error": "Only superadmins can manage users."}), 403
        users = config.setdefault("users", {})
        if username not in users:
            return jsonify({"error": f"User '{username}' was not found."}), 404
        users.pop(username)
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        metadata = _config_write_metadata(config_file_path, backup_path)
        return jsonify(
            {
                "message": f"User '{username}' removed.",
                **metadata,
            }
        )
    except Exception as e:
        return jsonify({"error": f"Failed to remove user: {str(e)}"}), 500


@app.route("/api/ptz/lock", methods=["POST"])
def update_ptz_lock():
    try:
        payload = request.get_json(force=True) or {}
        camera_name = (payload.get("camera") or "").strip()
        if not camera_name:
            return jsonify({"error": "camera is required."}), 400
        _, config = _load_effective_config_with_raw()
        if camera_name not in (config.get("cameras") or {}):
            return jsonify({"error": f"Camera '{camera_name}' was not found."}), 404
        status = set_lock(
            camera_name,
            bool(payload.get("locked", False)),
            payload.get("reason") or "",
        )
        return jsonify({"camera": camera_name, "lock": status})
    except BadRequest:
        return (
            jsonify({"error": "Invalid JSON format in request body or empty body."}),
            400,
        )
    except Exception as e:
        return jsonify({"error": f"Failed to update PTZ lock: {str(e)}"}), 500


@app.route("/config", methods=["PUT"])
def update_config():
    try:
        config_file_path = _config_file_path()
        if not request.is_json:
            return jsonify({"error": "Request body must be JSON."}), 415
        new_config_json = request.get_json()
        if not new_config_json:
            return jsonify({"error": "Request body is empty or not valid JSON."}), 400
        if "config" in new_config_json and len(new_config_json.keys()) == 1:
            new_config_json = new_config_json["config"]
        if not isinstance(new_config_json, dict):
            return (
                jsonify(
                    {"error": "Root element of the configuration must be a dictionary."}
                ),
                400,
            )
        raw_config = _load_raw_config()
        existing_config = _get_effective_config(raw_config)
        previous_config = yaml.safe_load(yaml.safe_dump(existing_config)) or {}
        _merge_persistent_users(new_config_json, existing_config)
        user_access_removed = _cleanup_user_camera_access(new_config_json)
        config_to_write = _merge_effective_config(raw_config, new_config_json)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        go2rtc_result = _sync_go2rtc_runtime(new_config_json, previous_config)
        publish_result = _publish_public_artifacts(new_config_json)
        runtime_reload = _reload_after_config_write()
        message = "Configuration updated successfully (saved as YAML). Public UI files and cameras.json were updated."
        if backup_path:
            message += f" Backup: {os.path.basename(backup_path)}"
        return (
            jsonify(
                {
                    "message": message,
                    "go2rtc": go2rtc_result,
                    **publish_result,
                    "runtime_reload": runtime_reload,
                    "user_camera_access_removed": user_access_removed,
                    **_config_write_metadata(config_file_path, backup_path),
                }
            ),
            200,
        )
    except BadRequest:
        return (
            jsonify({"error": "Invalid JSON format in request body or empty body."}),
            400,
        )
    except Exception as e:
        return jsonify({"error": f"Error processing configuration: {str(e)}"}), 500


@app.route("/api/global/deployment_name", methods=["PUT"])
def update_deployment_name():
    try:
        payload = request.get_json(force=True) or {}
        deployment_name = (payload.get("deployment_name") or "").strip()
        if not deployment_name:
            return jsonify({"error": "deployment_name is required."}), 400

        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        config.setdefault("global", {})
        if not isinstance(config["global"], dict):
            return jsonify({"error": "Config key 'global' must be a mapping."}), 400
        config["global"]["deployment_name"] = deployment_name
        if "public_site" in payload:
            ui_config = config["global"].setdefault("ui", {})
            if not isinstance(ui_config, dict):
                return (
                    jsonify({"error": "Config key 'global.ui' must be a mapping."}),
                    400,
                )
            ui_config["public_site"] = bool(payload.get("public_site"))
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        publish_result = _publish_public_artifacts(config)
        runtime_reload = _reload_after_config_write()
        message = (
            "Site settings updated. Public UI files and cameras.json were updated."
        )
        if backup_path:
            message += f" Backup: {os.path.basename(backup_path)}"
        return (
            jsonify(
                {
                    "message": message,
                    **publish_result,
                    "runtime_reload": runtime_reload,
                    **_config_write_metadata(config_file_path, backup_path),
                }
            ),
            200,
        )
    except BadRequest:
        return (
            jsonify({"error": "Invalid JSON format in request body or empty body."}),
            400,
        )
    except Exception as e:
        return jsonify({"error": f"Failed to update GUI name: {str(e)}"}), 500


@app.route("/api/global/camera_order", methods=["PUT"])
def update_camera_order():
    try:
        payload = request.get_json(force=True) or {}
        order = payload.get("camera_order") or []
        if not isinstance(order, list):
            return jsonify({"error": "camera_order must be a list."}), 400

        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        cameras = config.get("cameras") or {}
        if not isinstance(cameras, dict):
            return jsonify({"error": "Config key 'cameras' must be a mapping."}), 400
        camera_names = set(cameras.keys())
        cleaned_order = []
        for item in order:
            camera_name = str(item)
            if camera_name in camera_names and camera_name not in cleaned_order:
                cleaned_order.append(camera_name)

        global_config = config.setdefault("global", {})
        if not isinstance(global_config, dict):
            return jsonify({"error": "Config key 'global' must be a mapping."}), 400
        ui_config = global_config.setdefault("ui", {})
        if not isinstance(ui_config, dict):
            return jsonify({"error": "Config key 'global.ui' must be a mapping."}), 400
        ui_config["camera_order"] = cleaned_order

        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        publish_result = _publish_public_artifacts(config)
        runtime_reload = _reload_after_config_write()
        message = "Camera order updated. Public UI files and cameras.json were updated."
        if backup_path:
            message += f" Backup: {os.path.basename(backup_path)}"
        return (
            jsonify(
                {
                    "message": message,
                    "camera_order": cleaned_order,
                    **publish_result,
                    "runtime_reload": runtime_reload,
                    **_config_write_metadata(config_file_path, backup_path),
                }
            ),
            200,
        )
    except BadRequest:
        return (
            jsonify({"error": "Invalid JSON format in request body or empty body."}),
            400,
        )
    except Exception as e:
        return jsonify({"error": f"Failed to update camera order: {str(e)}"}), 500


@app.route("/")
def serve_ui_page():
    return send_from_directory("static/admin", "index.html")


@app.route("/logout")
def admin_logout():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fenetre Admin Logout</title>
</head>
<body>
    <p>Logged out of Fenetre admin. Redirecting to the login prompt...</p>
    <script>
        window.setTimeout(() => window.location.replace('/'), 500);
    </script>
</body>
</html>
"""
    return Response(
        html,
        200,
        {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "Clear-Site-Data": '"cache", "cookies", "storage"',
        },
    )


@app.route("/api/camera/test_snapshot", methods=["POST"])
def test_snapshot_url():
    try:
        payload = request.get_json(force=True) or {}
        url = (payload.get("url") or "").strip()
        rtsp_url = (payload.get("rtsp_url") or "").strip()
        ptz_rtsp_url = (payload.get("ptz_rtsp_url") or "").strip()
        local_command = (payload.get("local_command") or "").strip()
        capture_source = payload.get("capture_source") or (
            "rtsp" if local_command and not url else "snapshot"
        )
        timeout_s = int(payload.get("timeout_s") or 15)
        cache_bust = bool(payload.get("cache_bust", True))
        camera_config = {}
        if payload.get("snapshot_username") or payload.get("snapshot_password"):
            camera_config["http_auth"] = {
                "type": payload.get("snapshot_auth_type") or "basic",
                "username": payload.get("snapshot_username") or "",
                "password": payload.get("snapshot_password") or "",
            }
        if capture_source == "rtsp" and (local_command or rtsp_url):
            image_bytes, content_type, size = _fetch_local_command_bytes(
                local_command or rtsp_snapshot_command(rtsp_url),
                timeout_s=timeout_s,
            )
        elif url:
            image_bytes, content_type, size = _fetch_snapshot_bytes(
                url,
                timeout_s=timeout_s,
                cache_bust=cache_bust,
                camera_config=camera_config,
            )
        else:
            return jsonify({"error": "Snapshot URL is required."}), 400
        stream_tests = []
        live_stream_tests = []
        if capture_source != "rtsp" and rtsp_url:
            live_stream_tests.append(("RTSP live view", rtsp_url))
        if ptz_rtsp_url and ptz_rtsp_url != rtsp_url:
            live_stream_tests.append(("PTZ live RTSP", ptz_rtsp_url))
        for stream_name, stream_url in live_stream_tests:
            stream_bytes, _, stream_size = _fetch_local_command_bytes(
                rtsp_snapshot_command(stream_url), timeout_s=timeout_s
            )
            stream_tests.append(
                {
                    "name": stream_name,
                    "width": stream_size[0],
                    "height": stream_size[1],
                    "bytes": len(stream_bytes),
                }
            )
        return jsonify(
            {
                "ok": True,
                "content_type": content_type,
                "width": size[0],
                "height": size[1],
                "bytes": len(image_bytes),
                "stream_tests": stream_tests,
                "preview_data_url": "data:image/jpeg;base64,"
                + base64.b64encode(image_bytes).decode("ascii"),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/camera/ptz_presets", methods=["POST"])
def load_ptz_presets():
    try:
        payload = request.get_json(force=True) or {}
        camera_name = str(payload.get("camera_name") or payload.get("name") or "camera")
        ptz_config = {
            "enabled": True,
            "host": (payload.get("ptz_host") or "").strip(),
            "port": int(payload.get("ptz_port") or 80),
            "username": (payload.get("ptz_username") or "").strip(),
            "password": payload.get("ptz_password") or "",
            "profile_token": (payload.get("ptz_profile_token") or "").strip(),
        }
        if not ptz_config["password"] and payload.get("camera_name"):
            _, config = _load_effective_config_with_raw()
            existing_ptz = (
                (config.get("cameras") or {})
                .get(str(payload.get("camera_name")), {})
                .get("ptz", {})
            )
            if isinstance(existing_ptz, dict):
                ptz_config["password"] = existing_ptz.get("password") or ""
        missing = [
            label
            for label in ("host", "username", "password")
            if not ptz_config.get(label)
        ]
        if missing:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Missing ONVIF " + ", ".join(missing),
                    }
                ),
                400,
            )
        result = discover_presets(
            camera_name,
            {"ptz": ptz_config},
            owner="admin",
            duration_s=15,
        )
        return jsonify(
            {
                "ok": True,
                "presets": result.get("presets", []),
                "count": len(result.get("presets", [])),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/camera/image_profile", methods=["POST"])
def apply_camera_image_profile():
    try:
        payload = request.get_json(force=True) or {}
        camera_name = (payload.get("camera") or "").strip()
        if not camera_name:
            return jsonify({"error": "camera is required."}), 400
        _, config = _load_effective_config_with_raw()
        camera_config = (config.get("cameras") or {}).get(camera_name)
        if not isinstance(camera_config, dict):
            return jsonify({"error": f"Camera '{camera_name}' was not found."}), 404
        result = apply_image_profile(
            camera_name,
            camera_config,
            profile_name=(payload.get("profile") or "").strip() or None,
            mode=(payload.get("mode") or "").strip() or None,
            dry_run=payload.get("dry_run", True) is not False,
        )
        return jsonify(result)
    except ImageProfileError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/launches/preview", methods=["GET", "POST"])
def preview_launches():
    try:
        payload = request.get_json(silent=True) or {}
        config = (
            payload.get("config") if isinstance(payload.get("config"), dict) else None
        )
        if config is None:
            _, config = _load_effective_config_with_raw()
        return jsonify(preview_launch_workflow(config))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/launches/run_due", methods=["POST"])
def run_due_launches():
    try:
        payload = request.get_json(silent=True) or {}
        _, config = _load_effective_config_with_raw()
        dry_run = payload.get("dry_run")
        result = run_due_launch_actions(
            config,
            dry_run=None if dry_run is None else dry_run is not False,
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/camera/add", methods=["POST"])
def add_camera():
    try:
        payload = request.get_json(force=True) or {}
        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        config.setdefault("cameras", {})
        if not isinstance(config["cameras"], dict):
            return jsonify({"error": "Config key 'cameras' must be a mapping."}), 400
        name, camera = _build_camera_config(payload)
        if name in config["cameras"]:
            return jsonify({"error": f"Camera '{name}' already exists."}), 409
        previous_config = yaml.safe_load(yaml.safe_dump(config)) or {}
        if payload.get("require_test", True):
            if camera.get("local_command"):
                _fetch_local_command_bytes(
                    camera["local_command"], timeout_s=camera.get("timeout_s", 15)
                )
            else:
                _fetch_snapshot_bytes(
                    camera["url"],
                    timeout_s=camera.get("timeout_s", 15),
                    cache_bust=camera.get("cache_bust", True),
                    camera_config=camera,
                )
        _ensure_go2rtc_enabled_for_camera(config, camera)
        config["cameras"][name] = camera
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        metadata = _config_write_metadata(config_file_path, backup_path)
        go2rtc_result = _sync_go2rtc_runtime(config, previous_config)
        publish_result = _publish_public_artifacts(config)
        runtime_reload = _reload_after_config_write()
        return (
            jsonify(
                {
                    "message": f"Camera '{name}' added. Public UI files and cameras.json were updated.",
                    "camera_name": name,
                    "go2rtc": go2rtc_result,
                    **publish_result,
                    "runtime_reload": runtime_reload,
                    **metadata,
                }
            ),
            200,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Failed to add camera: {str(exc)}"}), 500


@app.route("/api/camera/<path:camera_name>", methods=["PUT"])
def update_camera(camera_name):
    try:
        payload = request.get_json(force=True) or {}
        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        cameras = config.setdefault("cameras", {})
        if not isinstance(cameras, dict):
            return jsonify({"error": "Config key 'cameras' must be a mapping."}), 400
        if camera_name not in cameras:
            return jsonify({"error": f"Camera '{camera_name}' was not found."}), 404

        previous_config = yaml.safe_load(yaml.safe_dump(config)) or {}
        old_camera = dict(cameras.get(camera_name) or {})
        name, camera = _build_camera_config(payload, existing_camera=old_camera)
        if name != camera_name and name in cameras:
            return jsonify({"error": f"Camera '{name}' already exists."}), 409
        if payload.get("require_test", False):
            if camera.get("local_command"):
                _fetch_local_command_bytes(
                    camera["local_command"], timeout_s=camera.get("timeout_s", 15)
                )
            else:
                _fetch_snapshot_bytes(
                    camera["url"],
                    timeout_s=camera.get("timeout_s", 15),
                    cache_bust=camera.get("cache_bust", True),
                    camera_config=camera,
                )

        updated_camera = _merge_guided_camera_update(old_camera, camera)
        rename_changes = {}
        media_move_result = {"moved": False}
        if name != camera_name:
            cameras.pop(camera_name)
        _ensure_go2rtc_enabled_for_camera(config, updated_camera)
        cameras[name] = updated_camera
        if name != camera_name:
            rename_changes = _replace_camera_references(config, camera_name, name)
        user_access_removed = _cleanup_user_camera_access(config)
        if name != camera_name:
            media_move_result = _move_camera_media_dir(config, camera_name, name)
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        metadata = _config_write_metadata(config_file_path, backup_path)
        go2rtc_result = _sync_go2rtc_runtime(config, previous_config)
        publish_result = _publish_public_artifacts(config)
        runtime_reload = _reload_after_config_write()
        return jsonify(
            {
                "message": f"Camera '{name}' updated. Public UI files and cameras.json were updated.",
                "camera_name": name,
                "go2rtc": go2rtc_result,
                **publish_result,
                "runtime_reload": runtime_reload,
                "camera_rename": rename_changes,
                "media_move": media_move_result,
                "user_camera_access_removed": user_access_removed,
                **metadata,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Failed to update camera: {str(exc)}"}), 500


@app.route("/api/camera/rename", methods=["POST"])
def rename_camera():
    try:
        payload = request.get_json(force=True) or {}
        old_name = _slugify_camera_name(payload.get("old_name"))
        new_name = _slugify_camera_name(payload.get("new_name"))
        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        cameras = config.setdefault("cameras", {})
        if old_name not in cameras:
            return jsonify({"error": f"Camera '{old_name}' was not found."}), 404
        if new_name in cameras and new_name != old_name:
            return jsonify({"error": f"Camera '{new_name}' already exists."}), 409
        previous_config = yaml.safe_load(yaml.safe_dump(config)) or {}
        cameras[new_name] = cameras.pop(old_name)
        rename_changes = _replace_camera_references(config, old_name, new_name)
        user_access_removed = _cleanup_user_camera_access(config)
        if payload.get("description"):
            cameras[new_name]["description"] = payload.get("description")
        media_move_result = _move_camera_media_dir(config, old_name, new_name)
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        metadata = _config_write_metadata(config_file_path, backup_path)
        go2rtc_result = _sync_go2rtc_runtime(config, previous_config)
        publish_result = _publish_public_artifacts(config)
        runtime_reload = _reload_after_config_write()
        return (
            jsonify(
                {
                    "message": f"Camera renamed from '{old_name}' to '{new_name}'. Existing media folder was moved when present.",
                    "camera_name": new_name,
                    "camera_rename": rename_changes,
                    "media_move": media_move_result,
                    "go2rtc": go2rtc_result,
                    **publish_result,
                    "runtime_reload": runtime_reload,
                    "user_camera_access_removed": user_access_removed,
                    **metadata,
                }
            ),
            200,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Failed to rename camera: {str(exc)}"}), 500


@app.route("/api/sync_ui", methods=["POST"])
def sync_ui():
    try:
        _, config = _load_effective_config_with_raw()
        result = _sync_public_ui_files(config)
        if not result.get("ok"):
            return jsonify({"error": result.get("warning")}), 500
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": f"Error synchronizing UI files: {str(e)}"}), 500


@app.route("/api/camera/<string:camera_name>/capture_for_ui", methods=["POST"])
def capture_for_ui(camera_name):
    try:
        _, config = _load_effective_config_with_raw()
        if "cameras" not in config or camera_name not in config["cameras"]:
            return (
                jsonify(
                    {"error": f"Camera '{camera_name}' not found in configuration."}
                ),
                404,
            )
        camera_config = config["cameras"][camera_name]
        url = camera_config.get("url")
        local_command = camera_local_command(camera_config)
        gopro_ip = camera_config.get("gopro_ip")
        if not url and not local_command and not gopro_ip:
            return (
                jsonify(
                    {
                        "error": f"Camera '{camera_name}' does not have a URL, local_command, or gopro_ip configured."
                    }
                ),
                400,
            )
        if local_command:
            image_bytes, content_type, _ = _fetch_local_command_bytes(
                local_command,
                camera_config.get("timeout_s", 20),
            )
            return send_file(BytesIO(image_bytes), mimetype=content_type)
        if url:
            image_bytes, content_type, _ = _fetch_snapshot_bytes(
                url,
                camera_config.get("timeout_s", 20),
                camera_config.get("cache_bust", False),
                camera_config=camera_config,
            )
            return send_file(BytesIO(image_bytes), mimetype=content_type)
        gopro_model = camera_config.get("gopro_model") or "hero11"
        if gopro_model == "open_gopro":
            gopro_model = "hero11"
        gopro = GoPro(ip_address=gopro_ip, gopro_model=gopro_model)
        jpeg_bytes = gopro.capture_photo()
        if not jpeg_bytes:
            return jsonify({"error": "Failed to capture photo from GoPro."}), 500
        return send_file(BytesIO(jpeg_bytes), mimetype="image/jpeg")
    except requests.exceptions.RequestException as e:
        return (
            jsonify(
                {"error": f"Error fetching image for camera '{camera_name}': {str(e)}"}
            ),
            500,
        )
    except Exception as e:
        return (
            jsonify(
                {
                    "error": f"Unexpected error capturing image for '{camera_name}': {str(e)}"
                }
            ),
            500,
        )


@app.route("/api/camera/preview_crop", methods=["POST"])
def preview_crop():
    if "image" not in request.files:
        return jsonify({"error": "No image file provided in the request."}), 400
    crop_data_str = request.form.get("crop_data")
    if not crop_data_str:
        return jsonify({"error": "No crop_data provided in the request form."}), 400
    try:
        crop_data = json.loads(crop_data_str)
        x = int(crop_data.get("x"))
        y = int(crop_data.get("y"))
        width = int(crop_data.get("width"))
        height = int(crop_data.get("height"))
        if width <= 0 or height <= 0:
            return jsonify({"error": "Crop width and height must be positive."}), 400
        img = Image.open(request.files["image"].stream)
        img = ImageOps.exif_transpose(img)
        img_width, img_height = img.size
        crop_box = (
            max(0, x),
            max(0, y),
            min(img_width, x + width),
            min(img_height, y + height),
        )
        if crop_box[0] >= crop_box[2] or crop_box[1] >= crop_box[3]:
            return jsonify({"error": "Crop area is outside image bounds."}), 400
        cropped_img = img.crop(crop_box)
        img_io = BytesIO()
        img_format = img.format or "JPEG"
        if img_format.upper() == "JPG":
            img_format = "JPEG"
        cropped_img.save(img_io, format=img_format)
        img_io.seek(0)
        return send_file(
            img_io,
            mimetype=(
                "image/jpeg" if img_format == "JPEG" else f"image/{img_format.lower()}"
            ),
        )
    except Exception as e:
        return jsonify({"error": f"Error during image processing: {str(e)}"}), 500


@app.route("/config/reload", methods=["POST"])
def reload_config():
    result, status = _fenetre_reload_signal_result()
    return jsonify(result), status


@app.route("/api/cameras_json/rebuild", methods=["POST"])
def rebuild_cameras_json():
    try:
        _, config = _load_effective_config_with_raw()
        result = _rebuild_cameras_json(config)
        if not result.get("ok"):
            return jsonify({"error": result.get("warning")}), 500
        message = result["message"]
        if result.get("backup"):
            message += f" Previous file saved as {result['backup']}."
        result["message"] = message
        return jsonify(result), 200
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": f"Failed to rebuild cameras.json: {str(exc)}"}), 500


# fenetre.py manages the lifecycle of this Flask app.
