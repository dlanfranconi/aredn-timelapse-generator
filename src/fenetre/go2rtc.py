import re
import sys
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import yaml

_STREAM_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")
_DEFAULT_PLAYER_URL_TEMPLATE = "{base_url}/stream.html?src={stream}&media=video&muted=1"
_OLD_PLAYER_URL_TEMPLATE = "{base_url}/stream.html?src={stream}"
_LEGACY_PLAYER_URL_TEMPLATE = "{base_url}/webrtc.html?src={stream}"
_DEFAULT_PREVIEW_URL_TEMPLATE = (
    "{base_url}/stream.html?src={stream}&media=video&muted=1"
)


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


def go2rtc_full_stream_name(camera_name: str, global_config: Dict[str, Any]) -> str:
    return f"{go2rtc_stream_name(camera_name, global_config)}_full"


def _listen_port(listen: Any, default: int = 1984) -> int:
    listen_text = str(listen or "").strip()
    if not listen_text:
        return default
    if listen_text.isdigit():
        return int(listen_text)
    if ":" in listen_text:
        candidate = listen_text.rsplit(":", 1)[-1]
        if candidate.isdigit():
            return int(candidate)
    return default


def go2rtc_browser_port(global_config: Dict[str, Any]) -> int:
    config = _go2rtc_config(global_config)
    return _listen_port(config.get("api_listen"), 1984)


def _player_url(config: Dict[str, Any], base_url: str, stream_name: str) -> str:
    template = str(config.get("player_url_template") or _DEFAULT_PLAYER_URL_TEMPLATE)
    if template in {_LEGACY_PLAYER_URL_TEMPLATE, _OLD_PLAYER_URL_TEMPLATE}:
        template = _DEFAULT_PLAYER_URL_TEMPLATE
    return _stream_url_from_template(config, base_url, stream_name, template)


def _preview_url(config: Dict[str, Any], base_url: str, stream_name: str) -> str:
    template = str(config.get("preview_url_template") or _DEFAULT_PREVIEW_URL_TEMPLATE)
    if template in {_LEGACY_PLAYER_URL_TEMPLATE, _OLD_PLAYER_URL_TEMPLATE}:
        template = _DEFAULT_PREVIEW_URL_TEMPLATE
    return _stream_url_from_template(config, base_url, stream_name, template)


def _stream_url_from_template(
    config: Dict[str, Any], base_url: str, stream_name: str, template: str
) -> str:
    encoded_stream = quote(stream_name, safe="")
    try:
        rendered = template.format(
            base_url=base_url,
            stream=encoded_stream,
            stream_name=stream_name,
        )
    except (IndexError, KeyError, ValueError):
        rendered = f"{base_url}/stream.html?src={encoded_stream}&media=video&muted=1"
    return _force_muted_player_url(rendered)


def _force_muted_player_url(url: str) -> str:
    parsed = urlsplit(url)
    player_path = parsed.path.rsplit("/", 1)[-1]
    if player_path not in {"stream.html", "webrtc.html"}:
        return url

    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"media", "muted"}
    ]
    query.extend([("media", "video"), ("muted", "1")])
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def _video_only_source(source: Any) -> str:
    source_text = str(source or "").strip()
    if not source_text.lower().startswith(("rtsp://", "rtsps://")):
        return source_text

    parts = source_text.split("#")
    params = parts[1:]
    if any(param.split("=", 1)[0].strip().lower() == "media" for param in params):
        return source_text
    return f"{source_text}#media=video"


def build_go2rtc_metadata(
    camera_name: str,
    camera_config: Dict[str, Any],
    global_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    config = _go2rtc_config(global_config)
    base_url = str(config.get("base_url") or "").rstrip("/")
    if not config.get("enabled"):
        return None
    if camera_config.get("go2rtc_enabled") is False:
        return None

    alignment_source = camera_config.get("ptz_rtsp_url") or camera_config.get(
        "rtsp_url"
    )
    full_source = camera_config.get("rtsp_url") or alignment_source
    if not alignment_source:
        return None

    idle_timeout_s = config.get("live_view_idle_timeout_s")
    if idle_timeout_s is None:
        idle_timeout_s = 60
    stream_name = go2rtc_stream_name(camera_name, global_config)
    full_stream_name = stream_name
    if full_source and full_source != alignment_source:
        full_stream_name = go2rtc_full_stream_name(camera_name, global_config)
    metadata = {
        "enabled": True,
        "stream": stream_name,
        "full_stream": full_stream_name,
        "full_view_url": f"live.html?camera={quote(camera_name, safe='')}&stream=full",
        "base_url_configured": bool(base_url),
        "idle_timeout_s": int(idle_timeout_s),
    }
    if base_url:
        metadata.update(
            {
                "player_url": _player_url(config, base_url, stream_name),
                "full_player_url": _player_url(config, base_url, full_stream_name),
                "preview_url": _preview_url(config, base_url, stream_name),
            }
        )
    else:
        metadata["same_host_port"] = go2rtc_browser_port(global_config)
    return metadata


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
            if camera_config.get("go2rtc_enabled") is False:
                continue
            alignment_source = camera_config.get("ptz_rtsp_url") or camera_config.get(
                "rtsp_url"
            )
            if not alignment_source:
                continue
            stream_name = go2rtc_stream_name(str(camera_name), global_config)
            streams[stream_name] = _video_only_source(alignment_source)
            full_source = camera_config.get("rtsp_url")
            if full_source and full_source != alignment_source:
                streams[go2rtc_full_stream_name(str(camera_name), global_config)] = (
                    _video_only_source(full_source)
                )

    if not streams:
        return None

    webrtc_config: Dict[str, Any] = {
        "listen": str(config.get("webrtc_listen") or ":8555")
    }
    candidates = config.get("webrtc_candidates") or []
    if candidates:
        webrtc_config["candidates"] = candidates

    runtime_config: Dict[str, Any] = {
        "api": {"listen": str(config.get("api_listen") or ":1984")},
        "rtsp": {"listen": str(config.get("rtsp_listen") or ":8554")},
        "webrtc": webrtc_config,
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
