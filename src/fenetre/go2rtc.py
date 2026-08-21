import re
import sys
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import yaml

_STREAM_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")
_DEFAULT_PLAYER_MODE = ""
_DEFAULT_PREVIEW_MODE = _DEFAULT_PLAYER_MODE
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


def _host_base_urls(config: Dict[str, Any]) -> Dict[str, str]:
    base_urls = config.get("base_urls") or {}
    if not isinstance(base_urls, dict):
        return {}
    normalized = {}
    for host, base_url in base_urls.items():
        host_key = str(host or "").strip().lower()
        base_url_text = str(base_url or "").strip().rstrip("/")
        if host_key and base_url_text:
            normalized[host_key] = base_url_text
    return normalized


def sanitize_stream_name(value: str) -> str:
    stream = _STREAM_NAME_PATTERN.sub("_", value.strip())
    stream = stream.strip("_")
    return stream or "camera"


def go2rtc_stream_name(camera_name: str, global_config: Dict[str, Any]) -> str:
    config = _go2rtc_config(global_config)
    prefix = str(config.get("stream_name_prefix") or "fenetre_")
    return f"{prefix}{sanitize_stream_name(camera_name)}"


def go2rtc_preload_query_params(preload_query: str) -> Dict[str, str]:
    """Turn a preload_query value (e.g. "video", or "video=1&audio=0") into
    query params for go2rtc's live PUT /api/preload endpoint. Mirrors how
    go2rtc itself treats the same string as a raw query when read from the
    static preload: YAML key, so a live-triggered preload behaves like the
    startup-time one.
    """
    text = str(preload_query or "").strip()
    pairs = parse_qsl(text, keep_blank_values=True)
    if pairs:
        return {key: value for key, value in pairs}
    return {text: ""} if text else {}


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
    return _stream_url_from_template(
        config, base_url, stream_name, template, _go2rtc_player_mode(config)
    )


def _preview_url(config: Dict[str, Any], base_url: str, stream_name: str) -> str:
    template = str(config.get("preview_url_template") or _DEFAULT_PREVIEW_URL_TEMPLATE)
    if template in {
        _LEGACY_PLAYER_URL_TEMPLATE,
        _OLD_PLAYER_URL_TEMPLATE,
        _DEFAULT_PLAYER_URL_TEMPLATE,
    }:
        template = _DEFAULT_PREVIEW_URL_TEMPLATE
    return _stream_url_from_template(
        config, base_url, stream_name, template, _go2rtc_preview_mode(config)
    )


def _go2rtc_player_mode(config: Dict[str, Any]) -> str:
    return (
        str(config.get("player_mode") or _DEFAULT_PLAYER_MODE).strip()
        or _DEFAULT_PLAYER_MODE
    )


def _go2rtc_preview_mode(config: Dict[str, Any]) -> str:
    return (
        str(
            config.get("preview_mode")
            or config.get("player_mode")
            or _DEFAULT_PREVIEW_MODE
        ).strip()
        or _DEFAULT_PREVIEW_MODE
    )


def _stream_url_from_template(
    config: Dict[str, Any],
    base_url: str,
    stream_name: str,
    template: str,
    mode: str,
) -> str:
    encoded_stream = quote(stream_name, safe="")
    try:
        rendered = template.format(
            base_url=base_url,
            stream=encoded_stream,
            stream_name=stream_name,
            mode=quote(mode, safe="/,"),
            player_mode=quote(_go2rtc_player_mode(config), safe="/,"),
            preview_mode=quote(_go2rtc_preview_mode(config), safe="/,"),
        )
    except (IndexError, KeyError, ValueError):
        rendered = f"{base_url}/stream.html?src={encoded_stream}&media=video&muted=1"
    return _force_muted_player_url(rendered, mode)


def _force_muted_player_url(url: str, mode: str = "") -> str:
    parsed = urlsplit(url)
    player_path = parsed.path.rsplit("/", 1)[-1]
    if player_path not in {"stream.html", "webrtc.html"}:
        return url

    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"media", "muted"}
    ]
    if player_path == "stream.html" and mode and not _has_url_query_key(query, "mode"):
        query.append(("mode", mode))
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


def _has_url_query_key(query: list[tuple[str, str]], name: str) -> bool:
    name = name.strip().lower()
    return any(key.strip().lower() == name for key, _ in query)


def _split_go2rtc_source_params(source_text: str) -> tuple[str, list[str]]:
    parts = source_text.split("#")
    return parts[0], parts[1:]


def _has_source_param(params: list[str], name: str) -> bool:
    name = name.strip().lower()
    return any(param.split("=", 1)[0].strip().lower() == name for param in params)


def _join_go2rtc_source_params(base: str, params: list[str]) -> str:
    clean_params = [param for param in params if str(param or "").strip()]
    if not clean_params:
        return base
    return f"{base}#{'#'.join(clean_params)}"


def _go2rtc_source_mode(config: Dict[str, Any], camera_config: Dict[str, Any]) -> str:
    mode = (
        str(
            camera_config.get("go2rtc_source_mode")
            or config.get("source_mode")
            or "ffmpeg"
        )
        .strip()
        .lower()
    )
    return mode if mode in {"ffmpeg", "rtsp"} else "ffmpeg"


def _go2rtc_rtsp_timeout_s(
    config: Dict[str, Any], camera_config: Dict[str, Any]
) -> int:
    value = camera_config.get("go2rtc_rtsp_timeout_s")
    if value is None:
        value = config.get("rtsp_timeout_s")
    if value is None:
        value = 30
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 30


def _go2rtc_rtsp_transport(
    config: Dict[str, Any], camera_config: Dict[str, Any]
) -> str:
    transport = (
        str(
            camera_config.get("go2rtc_rtsp_transport")
            or config.get("rtsp_transport")
            or "tcp"
        )
        .strip()
        .lower()
    )
    return transport if transport in {"tcp", "udp"} else "tcp"


def _go2rtc_video_mode(config: Dict[str, Any], camera_config: Dict[str, Any]) -> str:
    mode = (
        str(
            camera_config.get("go2rtc_video_mode") or config.get("video_mode") or "copy"
        )
        .strip()
        .lower()
    )
    return mode if mode in {"copy", "h264", "h265", "mjpeg"} else "copy"


def _camera_ptz_enabled(camera_config: Dict[str, Any]) -> bool:
    ptz_config = camera_config.get("ptz") or {}
    return isinstance(ptz_config, dict) and ptz_config.get("enabled") is True


def _go2rtc_preload_enabled(
    config: Dict[str, Any], camera_config: Dict[str, Any]
) -> bool:
    if camera_config.get("go2rtc_preload") is not None:
        return bool(camera_config.get("go2rtc_preload"))
    preload_ptz_streams = config.get("preload_ptz_streams")
    if preload_ptz_streams is None:
        preload_ptz_streams = True
    return bool(preload_ptz_streams) and _camera_ptz_enabled(camera_config)


def _video_only_source(
    source: Any,
    source_mode: str = "ffmpeg",
    timeout_s: int = 30,
    rtsp_transport: str = "tcp",
    video_mode: str = "copy",
) -> str:
    source_text = str(source or "").strip()
    if not source_text.lower().startswith(("rtsp://", "rtsps://")):
        return source_text

    base, params = _split_go2rtc_source_params(source_text)
    source_mode = str(source_mode or "ffmpeg").strip().lower()
    rtsp_transport = str(rtsp_transport or "tcp").strip().lower()
    video_mode = str(video_mode or "copy").strip().lower()
    if source_mode == "ffmpeg":
        ffmpeg_params = [
            param
            for param in params
            if param.split("=", 1)[0].strip().lower()
            in {"input", "raw", "timeout", "video", "hardware"}
        ]
        if not _has_source_param(ffmpeg_params, "video"):
            ffmpeg_params.append(f"video={video_mode}")
        if rtsp_transport == "udp" and not _has_source_param(ffmpeg_params, "input"):
            ffmpeg_params.append("input=rtsp/udp")
        if not _has_source_param(ffmpeg_params, "timeout"):
            ffmpeg_params.append(f"timeout={timeout_s}")
        return _join_go2rtc_source_params(f"ffmpeg:{base}", ffmpeg_params)

    if not _has_source_param(params, "media"):
        params.append("media=video")
    if not _has_source_param(params, "backchannel"):
        params.append("backchannel=0")
    if rtsp_transport == "udp" and not _has_source_param(params, "transport"):
        params.append("transport=udp")
    if not _has_source_param(params, "timeout"):
        params.append(f"timeout={timeout_s}")
    return _join_go2rtc_source_params(base, params)


def build_go2rtc_metadata(
    camera_name: str,
    camera_config: Dict[str, Any],
    global_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    config = _go2rtc_config(global_config)
    base_url = str(config.get("base_url") or "").rstrip("/")
    base_urls = _host_base_urls(config)
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
        "base_urls_configured": bool(base_urls),
        "same_host_port": go2rtc_browser_port(global_config),
        "idle_timeout_s": int(idle_timeout_s),
        "player_mode": _go2rtc_player_mode(config),
        "preview_mode": _go2rtc_preview_mode(config),
    }
    if base_url:
        metadata.update(
            {
                "player_url": _player_url(config, base_url, stream_name),
                "full_player_url": _player_url(config, base_url, full_stream_name),
                "preview_url": _preview_url(config, base_url, stream_name),
            }
        )
    if base_urls:
        metadata.update(
            {
                "player_urls": {
                    host: _player_url(config, host_base_url, stream_name)
                    for host, host_base_url in base_urls.items()
                },
                "full_player_urls": {
                    host: _player_url(config, host_base_url, full_stream_name)
                    for host, host_base_url in base_urls.items()
                },
                "preview_urls": {
                    host: _preview_url(config, host_base_url, stream_name)
                    for host, host_base_url in base_urls.items()
                },
            }
        )
    return metadata


def build_go2rtc_runtime_config(
    raw_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    global_config = raw_config.get("global") or {}
    config = _go2rtc_config(global_config)
    if not config.get("enabled"):
        return None

    # Local import: cameras_metadata imports build_go2rtc_metadata from this
    # module at module load time, so a top-level import here would be
    # circular. camera_visibility is resolved lazily instead, by which point
    # both modules are fully initialized.
    from fenetre.cameras_metadata import camera_visibility

    cameras = raw_config.get("cameras") or {}
    streams = {}
    preload = {}
    preload_query = str(config.get("preload_query") or "video").strip() or "video"
    if isinstance(cameras, dict):
        for camera_name, camera_config in cameras.items():
            if not isinstance(camera_config, dict):
                continue
            if camera_config.get("go2rtc_enabled") is False:
                continue
            if camera_visibility(camera_config) == "hidden":
                # "hidden" cameras are documented as fully removed from the
                # site (config/admin only); go2rtc has no tie to Fenetre's
                # own auth/visibility rules, so anyone who can reach the
                # go2rtc port would otherwise get a live feed of a camera
                # the rest of the app treats as not existing.
                continue
            alignment_source = camera_config.get("ptz_rtsp_url") or camera_config.get(
                "rtsp_url"
            )
            if not alignment_source:
                continue
            stream_name = go2rtc_stream_name(str(camera_name), global_config)
            source_mode = _go2rtc_source_mode(config, camera_config)
            rtsp_timeout_s = _go2rtc_rtsp_timeout_s(config, camera_config)
            rtsp_transport = _go2rtc_rtsp_transport(config, camera_config)
            video_mode = _go2rtc_video_mode(config, camera_config)
            streams[stream_name] = _video_only_source(
                alignment_source,
                source_mode,
                rtsp_timeout_s,
                rtsp_transport,
                video_mode,
            )
            if _go2rtc_preload_enabled(config, camera_config):
                preload[stream_name] = preload_query
            full_source = camera_config.get("rtsp_url")
            if full_source and full_source != alignment_source:
                streams[go2rtc_full_stream_name(str(camera_name), global_config)] = (
                    _video_only_source(
                        full_source,
                        source_mode,
                        rtsp_timeout_s,
                        rtsp_transport,
                        video_mode,
                    )
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
    if preload:
        runtime_config["preload"] = preload
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
