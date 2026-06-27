import re
from typing import Any, Dict, Optional
from urllib.parse import quote

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

    stream_name = go2rtc_stream_name(camera_name, global_config)
    template = str(
        config.get("player_url_template") or "{base_url}/stream.html?src={stream}"
    )
    encoded_stream = quote(stream_name, safe="")
    try:
        player_url = template.format(
            base_url=base_url,
            stream=encoded_stream,
            stream_name=stream_name,
        )
    except (IndexError, KeyError, ValueError):
        player_url = f"{base_url}/stream.html?src={encoded_stream}"
    return {
        "enabled": True,
        "stream": stream_name,
        "player_url": player_url,
    }
