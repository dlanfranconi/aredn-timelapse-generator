import re
import sys
from typing import Any, Dict, Optional
from urllib.parse import quote

import yaml

_STREAM_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


def _go2rtc_config(global_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(global_config, dict):
        return {}
    config = global_config.get("go2rtc") or {}
    return config if isinstance(config, dict) else {}


def sanitize_stream_name(value: str) -> str:
    stream = _STREAM_NAME_PATTERN.sub("_", value.strip())
    stream = stream.strip("_")
    return stream or "camera"


def go2rtc_stream_name(camera_name: str, global_config: Dict[str, Any]) -> str:
    config = _go2rtc_config(global_config)
    prefix = str(config.get("stream_name_prefix") or "fenetre_")
    return f"{prefix}{sanitize_stream_name(camera_name)}"


def build_go2rtc_metadata(
    camera_name: str,
    camera_config: Dict[str, Any],
    global_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    config = _go2rtc_config(global_config)
    base_url = str(config.get("base_url") or "").rstrip("/")
    if not config.get("enabled") or not base_url:
        return None

    rtsp_source = camera_config.get("ptz_rtsp_url") or camera_config.get("rtsp_url")
    if not rtsp_source:
        return None

    idle_timeout_s = config.get("live_view_idle_timeout_s")
    if idle_timeout_s is None:
        idle_timeout_s = 60
    stream_name = go2rtc_stream_name(camera_name, global_config)
    template = str(
        config.get("player_url_template") or "{base_url}/webrtc.html?src={stream}"
    )
    encoded_stream = quote(stream_name, safe="")
    try:
        player_url = template.format(
            base_url=base_url,
            stream=encoded_stream,
            stream_name=stream_name,
        )
    except (IndexError, KeyError, ValueError):
        player_url = f"{base_url}/webrtc.html?src={encoded_stream}"
    return {
        "enabled": True,
        "stream": stream_name,
        "player_url": player_url,
        "idle_timeout_s": int(idle_timeout_s),
    }


def build_go2rtc_runtime_config(
    raw_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    global_config = raw_config.get("global") or {}
    config = _go2rtc_config(global_config)
    if not config.get("enabled"):
        return None

    cameras = raw_config.get("cameras") or {}
    streams = {}
    if isinstance(cameras, dict):
        for camera_name, camera_config in cameras.items():
            if not isinstance(camera_config, dict):
                continue
            rtsp_source = camera_config.get("ptz_rtsp_url") or camera_config.get(
                "rtsp_url"
            )
            if not rtsp_source:
                continue
            streams[go2rtc_stream_name(str(camera_name), global_config)] = rtsp_source

    if not streams:
        return None

    runtime_config: Dict[str, Any] = {
        "api": {"listen": str(config.get("api_listen") or ":1984")},
        "rtsp": {"listen": str(config.get("rtsp_listen") or ":8554")},
        "webrtc": {"listen": str(config.get("webrtc_listen") or ":8555")},
        "streams": streams,
    }
    return runtime_config


def write_go2rtc_runtime_config(config_path: str, output_path: str) -> bool:
    with open(config_path, "r") as config_file:
        raw_config = yaml.safe_load(config_file) or {}
    runtime_config = build_go2rtc_runtime_config(raw_config)
    if not runtime_config:
        return False
    with open(output_path, "w") as output_file:
        yaml.safe_dump(runtime_config, output_file, sort_keys=False)
    return True


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: python -m fenetre.go2rtc <fenetre-config.yaml> <go2rtc-output.yaml>",
            file=sys.stderr,
        )
        return 1
    try:
        wrote_config = write_go2rtc_runtime_config(sys.argv[1], sys.argv[2])
    except Exception as exc:
        print(f"failed to generate go2rtc config: {exc}", file=sys.stderr)
        return 1
    return 0 if wrote_config else 2


if __name__ == "__main__":
    raise SystemExit(main())
