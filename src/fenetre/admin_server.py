import base64
import hmac
import json
import os
import re
import shlex
import signal
import subprocess
from datetime import datetime, timezone
from io import BytesIO

import requests
import yaml
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageOps
from prometheus_client import REGISTRY, Counter, Gauge, generate_latest
from werkzeug.exceptions import BadRequest

from fenetre.auth import (
    authenticate_config_user,
    ensure_default_admin_user,
    hash_password,
    user_has_password,
)
from fenetre.cameras_metadata import write_cameras_metadata
from fenetre.config import config_load
from fenetre.gopro import GoPro
from fenetre.http_auth import auth_from_camera_config
from fenetre.ptz import set_lock
from fenetre.ui_utils import copy_public_html_files

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


@app.before_request
def require_admin_auth():
    if not _admin_auth_enabled():
        return None

    auth = request.authorization
    if not auth:
        return _auth_failed_response()

    config_file_path = app.config.get("FENETRE_CONFIG_FILE")
    if config_file_path and authenticate_config_user(
        config_file_path, auth.username or "", auth.password or ""
    ):
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
    return backup_path


def _slugify_camera_name(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    if not value:
        raise ValueError("Camera name cannot be empty.")
    return value


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
    "description",
    "disabled",
    "public",
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


def _rtsp_snapshot_command(rtsp_url: str) -> str:
    return (
        "ffmpeg -hide_banner -loglevel error -rtsp_transport tcp "
        f"-i {shlex.quote(rtsp_url)} -frames:v 1 -f image2pipe -vcodec mjpeg -"
    )


def _fetch_local_command_bytes(command: str, timeout_s: int = 15):
    result = subprocess.run(
        shlex.split(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"local_command failed with exit code {result.returncode}. Check the camera log for command details."
        )
    image_bytes = result.stdout
    image = Image.open(BytesIO(image_bytes))
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
        "cache_bust": bool(payload.get("cache_bust", True)),
        "gather_metrics": bool(payload.get("gather_metrics", True)),
        "mozjpeg_optimize": bool(payload.get("mozjpeg_optimize", False)),
    }
    if url:
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
        camera["local_command"] = local_command or _rtsp_snapshot_command(rtsp_url)
    description = (payload.get("description") or "").strip()
    if description:
        camera["description"] = description

    if payload.get("disabled"):
        camera["disabled"] = True
    camera["public"] = bool(payload.get("public", True))
    if payload.get("ptz_enabled"):
        presets = payload.get("ptz_presets") or []
        if isinstance(presets, str):
            presets = json.loads(presets) if presets.strip() else []
        camera["ptz"] = {
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
            or (existing_camera.get("ptz") or {}).get("password", ""),
            "profile_token": (payload.get("ptz_profile_token") or "").strip(),
            "presets": presets,
        }
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
    user = {
        "disabled": bool(payload.get("disabled", existing.get("disabled", False))),
        "role": payload.get("role") or existing.get("role") or "viewer",
        "ptz_cameras": payload.get("ptz_cameras", existing.get("ptz_cameras", []))
        or [],
        "ptz_access": payload.get("ptz_access", existing.get("ptz_access", "presets")),
    }
    if payload.get("password"):
        user["password_hash"] = hash_password(str(payload["password"]))
    elif existing.get("password_hash"):
        user["password_hash"] = existing["password_hash"]
    elif existing.get("password"):
        user["password"] = existing["password"]
    return username, user


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
        public_users = []
        for username, user in users.items():
            public_users.append(
                {
                    "username": username,
                    "disabled": bool(user.get("disabled", False)),
                    "role": user.get("role", "viewer"),
                    "ptz_cameras": user.get("ptz_cameras", []),
                    "ptz_access": user.get("ptz_access", "presets"),
                    "has_password": user_has_password(user),
                }
            )
        return jsonify(
            {
                "users": public_users,
                "cameras": sorted((config.get("cameras") or {}).keys()),
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
        users = config.setdefault("users", {})
        username, user = _normalize_user(payload, users.get(payload.get("username")))
        users[username] = user
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        return jsonify(
            {
                "message": f"User '{username}' saved.",
                "backup": os.path.basename(backup_path) if backup_path else None,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to save user: {str(e)}"}), 500


@app.route("/api/users/<path:username>", methods=["DELETE"])
def delete_user(username):
    try:
        config_file_path = _config_file_path()
        raw_config, config = _load_effective_config_with_raw()
        users = config.setdefault("users", {})
        if username not in users:
            return jsonify({"error": f"User '{username}' was not found."}), 404
        users.pop(username)
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        return jsonify(
            {
                "message": f"User '{username}' removed.",
                "backup": os.path.basename(backup_path) if backup_path else None,
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
        config_to_write = _merge_effective_config(raw_config, new_config_json)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        message = "Configuration updated successfully (saved as YAML). Reload is required to apply changes."
        if backup_path:
            message += f" Backup: {os.path.basename(backup_path)}"
        return jsonify({"message": message}), 200
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
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        message = "GUI name updated. Reload and sync UI to publish the change."
        if backup_path:
            message += f" Backup: {os.path.basename(backup_path)}"
        return (
            jsonify(
                {
                    "message": message,
                    "backup": os.path.basename(backup_path) if backup_path else None,
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


@app.route("/")
def serve_ui_page():
    return send_from_directory("static/admin", "index.html")


@app.route("/api/camera/test_snapshot", methods=["POST"])
def test_snapshot_url():
    try:
        payload = request.get_json(force=True) or {}
        url = (payload.get("url") or "").strip()
        rtsp_url = (payload.get("rtsp_url") or "").strip()
        local_command = (payload.get("local_command") or "").strip()
        timeout_s = int(payload.get("timeout_s") or 15)
        camera_config = {}
        if payload.get("snapshot_username") or payload.get("snapshot_password"):
            camera_config["http_auth"] = {
                "type": payload.get("snapshot_auth_type") or "basic",
                "username": payload.get("snapshot_username") or "",
                "password": payload.get("snapshot_password") or "",
            }
        if local_command or rtsp_url:
            image_bytes, content_type, size = _fetch_local_command_bytes(
                local_command or _rtsp_snapshot_command(rtsp_url),
                timeout_s=timeout_s,
            )
        elif url:
            image_bytes, content_type, size = _fetch_snapshot_bytes(
                url, timeout_s=timeout_s, camera_config=camera_config
            )
        else:
            return jsonify({"error": "Snapshot URL is required."}), 400
        return jsonify(
            {
                "ok": True,
                "content_type": content_type,
                "width": size[0],
                "height": size[1],
                "bytes": len(image_bytes),
                "preview_data_url": "data:image/jpeg;base64,"
                + base64.b64encode(image_bytes).decode("ascii"),
            }
        )
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
        config["cameras"][name] = camera
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        return (
            jsonify(
                {
                    "message": f"Camera '{name}' added. Reload the app to make it live.",
                    "camera_name": name,
                    "backup": os.path.basename(backup_path) if backup_path else None,
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
        if name != camera_name:
            cameras.pop(camera_name)
        cameras[name] = updated_camera
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        return jsonify(
            {
                "message": f"Camera '{name}' updated. Reload the app to make it live.",
                "camera_name": name,
                "backup": os.path.basename(backup_path) if backup_path else None,
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
        cameras[new_name] = cameras.pop(old_name)
        if payload.get("description"):
            cameras[new_name]["description"] = payload.get("description")
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        return (
            jsonify(
                {
                    "message": f"Camera renamed from '{old_name}' to '{new_name}'. Existing media folders were not moved.",
                    "backup": os.path.basename(backup_path) if backup_path else None,
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
        work_dir = config.get("global", {}).get("work_dir")
        if not work_dir:
            return jsonify({"error": "work_dir not set in global config."}), 500
        copy_public_html_files(work_dir, config.get("global", {}))
        return jsonify({"message": "UI files synchronized successfully."}), 200
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
        local_command = camera_config.get("local_command")
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
    fenetre_pid_file_path = app.config.get("FENETRE_PID_FILE_PATH")
    if not fenetre_pid_file_path:
        return jsonify({"error": "FENETRE_PID_FILE_PATH not set in app config."}), 500
    try:
        if not os.path.exists(fenetre_pid_file_path):
            return (
                jsonify(
                    {
                        "error": f"PID file not found: {fenetre_pid_file_path}. Cannot signal reload."
                    }
                ),
                404,
            )
        with open(fenetre_pid_file_path, "r") as f:
            pid_str = f.read().strip()
        if not pid_str:
            return jsonify({"error": "PID file is empty."}), 500
        pid = int(pid_str)
        os.kill(pid, signal.SIGHUP)
        return jsonify({"message": f"Reload signal sent to process {pid}."}), 200
    except ProcessLookupError:
        return (
            jsonify(
                {
                    "error": f"Process with PID read from {fenetre_pid_file_path} not found."
                }
            ),
            500,
        )
    except ValueError:
        return jsonify({"error": f"Invalid PID found in {fenetre_pid_file_path}."}), 500
    except Exception as e:
        return jsonify({"error": f"Error signaling reload: {str(e)}"}), 500


@app.route("/api/cameras_json/rebuild", methods=["POST"])
def rebuild_cameras_json():
    try:
        config_file_path = _config_file_path()
        _, cameras_config, global_config, _, timelapse_config = config_load(
            config_file_path
        )
        work_dir = global_config.get("work_dir")
        if not work_dir:
            return jsonify({"error": "work_dir not set in global configuration."}), 500
        cameras_json_path = os.path.join(work_dir, "cameras.json")
        backup_path = None
        if os.path.exists(cameras_json_path):
            backup_path = (
                f"{cameras_json_path}.bak.{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
            )
            os.replace(cameras_json_path, backup_path)
        write_cameras_metadata(
            cameras_config, global_config, timelapse_config, cameras_json_path
        )
        message = "cameras.json rebuilt successfully."
        if backup_path:
            message += f" Previous file saved as {os.path.basename(backup_path)}."
        return jsonify({"message": message}), 200
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": f"Failed to rebuild cameras.json: {str(exc)}"}), 500


# fenetre.py manages the lifecycle of this Flask app.
