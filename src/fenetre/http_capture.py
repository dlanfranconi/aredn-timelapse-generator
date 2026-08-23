from __future__ import annotations

from urllib.parse import urlparse


def validate_http_snapshot_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return

    if parsed.scheme in {"http", "https"} and port == 554:
        raise RuntimeError(
            "Snapshot URL uses HTTP on port 554, which is normally RTSP. "
            "Use an HTTP snapshot URL on the camera's web port, or configure "
            "a local_command that captures one frame from an rtsp:// URL."
        )
