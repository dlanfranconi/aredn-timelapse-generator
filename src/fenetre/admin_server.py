import base64
import json
import os
import re
import signal
from datetime import datetime
from io import BytesIO

import requests
import yaml
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageOps
from prometheus_client import REGISTRY, Counter, Gauge, generate_latest
from werkzeug.exceptions import BadRequest

from fenetre.cameras_metadata import write_cameras_metadata
from fenetre.config import config_load
from fenetre.gopro import GoPro
from fenetre.ui_utils import copy_public_html_files

metric_pictures_taken_total = Counter("pictures_taken_total", "Total number of pictures taken", ["camera_name"])
metric_last_successful_picture_timestamp = Gauge("capture_last_success_timestamp", "Timestamp of the last successfully taken picture", ["camera_name"])
metric_capture_failures_total = Counter("capture_failures_total", "Total number of capture failures", ["camera_name"])
metric_timelapses_created_total = Counter("timelapses_created_total", "Total number of timelapses created", ["camera_name", "type"])
metric_timelapse_queue_size = Gauge("timelapse_queue_size", "Number of timelapses in the queue")
metric_camera_directory_size_bytes = Gauge("camera_directory_size_bytes", "Size of the camera directory in bytes", ["camera_name"])
metric_work_directory_size_bytes = Gauge("work_dir_size_bytes", "Size of the work directory in bytes")
metric_directories_total = Gauge("dir_total_count", "Total number of directories", ["camera_name"])
metric_directories_archived_total = Gauge("dir_archived_count", "Number of archived directories", ["camera_name"])
metric_directories_timelapse_total = Gauge("dir_timelapse_count", "Number of directories with a timelapse file", ["camera_name"])
metric_directories_daylight_total = Gauge("dir_daylight_count", "Number of directories with a daylight.png file", ["camera_name"])
metric_picture_width_pixels = Gauge("picture_width_pixels", "Width of the captured picture in pixels", ["camera_name"])
metric_picture_height_pixels = Gauge("picture_height_pixels", "Height of the captured picture in pixels", ["camera_name"])
metric_picture_size_bytes = Gauge("picture_size_bytes", "Size of the captured picture in bytes", ["camera_name"])
metric_picture_iso = Gauge("picture_iso", "ISO value of the captured picture", ["camera_name"])
metric_picture_focal_length_mm = Gauge("picture_focal_length_mm", "Focal length of the captured picture in mm", ["camera_name"])
metric_picture_aperture = Gauge("picture_aperture", "Aperture value of the captured picture", ["camera_name"])
metric_picture_exposure_time_seconds = Gauge("picture_exposure_time_seconds", "Exposure time of the captured picture in seconds", ["camera_name"])
metric_picture_white_balance = Gauge("picture_white_balance", "White balance value of the captured picture", ["camera_name"])
metric_processing_time_seconds = Gauge("capture_processing_time_seconds", "Time it took to fetch and process a new picture", ["camera_name"])
metric_sleep_time_seconds = Gauge("capture_loop_sleep_time_seconds", "Time the camera sleeps between pictures", ["camera_name"])
metric_camera_mode = Gauge("camera_mode", "Current camera mode with mode label", ["camera_name", "mode"])
metric_camera_ssim_value = Gauge("camera_ssim_value", "Latest SSIM measurement", ["camera_name"])
metric_camera_ssim_target = Gauge("camera_ssim_target", "Configured SSIM target", ["camera_name"])
metric_camera_online = Gauge("camera_online", "Camera online status reported by the snap loop", ["camera_name"])
gopro_state_gauge = Gauge("gopro_state", "GoPro State", ["camera_name", "state_name"])
gopro_setting_gauge = Gauge("gopro_setting", "GoPro Setting", ["camera_name", "setting_name"])

app = Flask(__name__)


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
    backup_path = f"{config_file_path}.bak.{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    with open(config_file_path, "rb") as src, open(backup_path, "wb") as dst:
        dst.write(src.read())
    return backup_path


def _write_yaml_for_bind_mount(config_file_path: str, config_data: dict) -> str | None:
    """Write config safely when config.yaml is a Docker bind-mounted file.

    os.replace(tmp, config.yaml) can fail with EBUSY on single-file bind mounts, so
    we keep a timestamped backup and then truncate/write/fsync the mounted file.
    """
    backup_path = _backup_config(config_file_path)
    rendered = yaml.safe_dump(config_data, sort_keys=False, default_flow_style=False, indent=2)
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


def _fetch_snapshot_bytes(url: str, timeout_s: int = 15, cache_bust: bool = True):
    request_url = url
    if cache_bust:
        separator = "&" if "?" in request_url else "?"
        request_url = f"{request_url}{separator}_fenetre_test={int(datetime.utcnow().timestamp())}"
    headers = {"Accept": "image/*,*/*;q=0.8", "User-Agent": "Fenetre Admin Snapshot Tester"}
    response = requests.get(request_url, timeout=timeout_s, headers=headers)
    response.raise_for_status()
    image_bytes = response.content
    image = Image.open(BytesIO(image_bytes))
    image.verify()
    reopened = Image.open(BytesIO(image_bytes))
    return image_bytes, response.headers.get("content-type", "image/jpeg"), reopened.size


def _build_camera_config(payload: dict) -> tuple[str, dict]:
    name = _slugify_camera_name(payload.get("name"))
    url = (payload.get("url") or "").strip()
    if not url:
        raise ValueError("Snapshot URL is required.")

    camera = {
        "url": url,
        "description": payload.get("description") or name,
        "timeout_s": int(payload.get("timeout_s") or 15),
        "cache_bust": bool(payload.get("cache_bust", True)),
        "gather_metrics": bool(payload.get("gather_metrics", True)),
        "mozjpeg_optimize": bool(payload.get("mozjpeg_optimize", False)),
    }

    if payload.get("disabled"):
        camera["disabled"] = True
    if payload.get("snap_interval_enabled"):
        camera["snap_interval_s"] = int(payload.get("snap_interval_s") or 60)
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
            "sunrise_offset_start_minutes": int(payload.get("sunrise_offset_start_minutes") or 45),
            "sunrise_offset_end_minutes": int(payload.get("sunrise_offset_end_minutes") or 45),
            "sunset_offset_start_minutes": int(payload.get("sunset_offset_start_minutes") or 45),
            "sunset_offset_end_minutes": int(payload.get("sunset_offset_end_minutes") or 45),
        }

    postprocessing = []
    for step in payload.get("postprocessing", []) or []:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        if step_type == "timestamp":
            postprocessing.append({
                "type": "timestamp",
                "enabled": bool(step.get("enabled", True)),
                "position": step.get("position") or "bottom_right",
                "size": int(step.get("size") or 24),
                "color": step.get("color") or "white",
                "format": step.get("format") or "%Y-%m-%d %H:%M:%S %Z",
            })
        elif step_type == "crop" and step.get("area"):
            postprocessing.append({"type": "crop", "area": step.get("area")})
        elif step_type == "resize":
            postprocessing.append({"type": "resize", "width": int(step.get("width") or 1280), "height": int(step.get("height") or 720)})
        elif step_type == "awb":
            postprocessing.append({"type": "awb"})
    if postprocessing:
        camera["postprocessing"] = postprocessing
    return name, camera


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
            return jsonify({"error": "Root element of the configuration must be a dictionary."}), 400
        raw_config = _load_raw_config()
        config_to_write = _merge_effective_config(raw_config, new_config_json)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        message = "Configuration updated successfully. Reload is required to apply changes."
        if backup_path:
            message += f" Backup: {os.path.basename(backup_path)}"
        return jsonify({"message": message}), 200
    except BadRequest:
        return jsonify({"error": "Invalid JSON format in request body or empty body."}), 400
    except Exception as e:
        return jsonify({"error": f"Error processing configuration: {str(e)}"}), 500


@app.route("/")
def serve_ui_page():
    return send_from_directory("static/admin", "index.html")


@app.route("/api/camera/test_snapshot", methods=["POST"])
def test_snapshot_url():
    try:
        payload = request.get_json(force=True) or {}
        url = (payload.get("url") or "").strip()
        timeout_s = int(payload.get("timeout_s") or 15)
        if not url:
            return jsonify({"error": "Snapshot URL is required."}), 400
        image_bytes, content_type, size = _fetch_snapshot_bytes(url, timeout_s=timeout_s)
        return jsonify({
            "ok": True,
            "content_type": content_type,
            "width": size[0],
            "height": size[1],
            "bytes": len(image_bytes),
            "preview_data_url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii"),
        })
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
            _fetch_snapshot_bytes(camera["url"], timeout_s=camera.get("timeout_s", 15), cache_bust=camera.get("cache_bust", True))
        config["cameras"][name] = camera
        config_to_write = _merge_effective_config(raw_config, config)
        backup_path = _write_yaml_for_bind_mount(config_file_path, config_to_write)
        return jsonify({"message": f"Camera '{name}' added. Reload the app to make it live.", "camera_name": name, "backup": os.path.basename(backup_path) if backup_path else None}), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Failed to add camera: {str(exc)}"}), 500


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
        return jsonify({"message": f"Camera renamed from '{old_name}' to '{new_name}'. Existing media folders were not moved.", "backup": os.path.basename(backup_path) if backup_path else None}), 200
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
            return jsonify({"error": f"Camera '{camera_name}' not found in configuration."}), 404
        camera_config = config["cameras"][camera_name]
        url = camera_config.get("url")
        gopro_ip = camera_config.get("gopro_ip")
        if not url and not gopro_ip:
            return jsonify({"error": f"Camera '{camera_name}' does not have a URL or gopro_ip configured."}), 400
        if url:
            image_bytes, content_type, _ = _fetch_snapshot_bytes(url, camera_config.get("timeout_s", 20), camera_config.get("cache_bust", False))
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
        return jsonify({"error": f"Error fetching image for camera '{camera_name}': {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error capturing image for '{camera_name}': {str(e)}"}), 500


@app.route("/api/camera/preview_crop", methods=["POST"])
def preview_crop():
    if "image" not in request.files:
        return jsonify({"error": "No image file provided in the request."}), 400
    crop_data_str = request.form.get("crop_data")
    if not crop_data_str:
        return jsonify({"error": "No crop_data provided in the request form."}), 400
    try:
        crop_data = json.loads(crop_data_str)
        x = int(crop_data.get("x")); y = int(crop_data.get("y")); width = int(crop_data.get("width")); height = int(crop_data.get("height"))
        if width <= 0 or height <= 0:
            return jsonify({"error": "Crop width and height must be positive."}), 400
        img = Image.open(request.files["image"].stream)
        img = ImageOps.exif_transpose(img)
        img_width, img_height = img.size
        crop_box = (max(0, x), max(0, y), min(img_width, x + width), min(img_height, y + height))
        if crop_box[0] >= crop_box[2] or crop_box[1] >= crop_box[3]:
            return jsonify({"error": "Crop area is outside image bounds."}), 400
        cropped_img = img.crop(crop_box)
        img_io = BytesIO()
        img_format = img.format or "JPEG"
        if img_format.upper() == "JPG":
            img_format = "JPEG"
        cropped_img.save(img_io, format=img_format)
        img_io.seek(0)
        return send_file(img_io, mimetype="image/jpeg" if img_format == "JPEG" else f"image/{img_format.lower()}")
    except Exception as e:
        return jsonify({"error": f"Error during image processing: {str(e)}"}), 500


@app.route("/config/reload", methods=["POST"])
def reload_config():
    fenetre_pid_file_path = app.config.get("FENETRE_PID_FILE_PATH")
    if not fenetre_pid_file_path:
        return jsonify({"error": "FENETRE_PID_FILE_PATH not set in app config."}), 500
    try:
        if not os.path.exists(fenetre_pid_file_path):
            return jsonify({"error": f"PID file not found: {fenetre_pid_file_path}. Cannot signal reload."}), 404
        with open(fenetre_pid_file_path, "r") as f:
            pid_str = f.read().strip()
        if not pid_str:
            return jsonify({"error": "PID file is empty."}), 500
        pid = int(pid_str)
        os.kill(pid, signal.SIGHUP)
        return jsonify({"message": f"Reload signal sent to process {pid}."}), 200
    except ProcessLookupError:
        return jsonify({"error": f"Process with PID read from {fenetre_pid_file_path} not found."}), 500
    except ValueError:
        return jsonify({"error": f"Invalid PID found in {fenetre_pid_file_path}."}), 500
    except Exception as e:
        return jsonify({"error": f"Error signaling reload: {str(e)}"}), 500


@app.route("/api/cameras_json/rebuild", methods=["POST"])
def rebuild_cameras_json():
    try:
        config_file_path = _config_file_path()
        (_, cameras_config, global_config, _, timelapse_config) = config_load(config_file_path)
        work_dir = global_config.get("work_dir")
        if not work_dir:
            return jsonify({"error": "work_dir not set in global configuration."}), 500
        cameras_json_path = os.path.join(work_dir, "cameras.json")
        backup_path = None
        if os.path.exists(cameras_json_path):
            backup_path = f"{cameras_json_path}.bak.{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
            os.replace(cameras_json_path, backup_path)
        write_cameras_metadata(cameras_config, global_config, timelapse_config, cameras_json_path)
        message = "cameras.json rebuilt successfully."
        if backup_path:
            message += f" Previous file saved as {os.path.basename(backup_path)}."
        return jsonify({"message": message}), 200
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": f"Failed to rebuild cameras.json: {str(exc)}"}), 500


# fenetre.py manages the lifecycle of this Flask app.
