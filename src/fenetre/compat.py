"""Runtime compatibility patches for the AREDN/Fenetre WIP image.

This module is intentionally small and imported through the console entry point.
It lets us stabilize camera fetching and timelapse kwargs without risky large-file
rewrites of fenetre.py while the admin UI refactor is still in progress.
"""

from __future__ import annotations

from io import BytesIO
import inspect
import logging
import time
from typing import Dict

from PIL import Image
import requests
import urllib3

from fenetre import fenetre as core

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


def _bool_config(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def patched_get_pic_from_url(
    url: str,
    timeout: int,
    ua: str = "",
    camera_name: str = "",
    camera_config: Dict | None = None,
    global_config: Dict | None = None,
) -> Image.Image:
    camera_config = camera_config or {}
    global_config = global_config or {}

    request_url = url
    if camera_config.get("cache_bust", False):
        timestamp = int(time.time())
        request_url = f"{request_url}&_={timestamp}" if "?" in request_url else f"{request_url}?_={timestamp}"

    user_agent = ua or camera_config.get("user_agent") or "Mozilla/5.0 (X11; Linux x86_64) AREDN-Timelapse/1.0"
    headers = {
        "User-Agent": user_agent,
        "Accept": "image/jpeg,image/*,*/*;q=0.8",
        "Connection": "close",
    }

    verify_ssl = _bool_config(camera_config.get("verify_ssl"), default=False)
    allow_redirects = _bool_config(camera_config.get("allow_redirects"), default=True)

    response = requests.get(
        request_url,
        timeout=timeout,
        headers=headers,
        allow_redirects=allow_redirects,
        verify=verify_ssl,
    )

    log_message = (
        f"URL fetch for {url}:"
        f"\n\tRequest URL: {response.request.url}"
        f"\n\tRequest Headers: {response.request.headers}"
        f"\n\tResponse Status: {response.status_code}"
        f"\n\tResponse Headers: {response.headers}"
        f"\n\tResponse Content-Type: {response.headers.get('content-type', '')}"
        f"\n\tResponse Bytes: {len(response.content)}"
    )
    logger.debug(log_message)

    log_dir = global_config.get("log_dir")
    if log_dir and camera_name:
        try:
            camera_logger = core.get_camera_logger(
                camera_name,
                log_dir,
                global_config.get("log_max_bytes", 10000000),
                global_config.get("log_backup_count", 5),
            )
            camera_logger.info(log_message)
        except Exception:
            logger.debug("Could not write camera-specific fetch log", exc_info=True)

    if response.status_code != 200:
        raise RuntimeError(
            "HTTP camera snapshot request failed. "
            f"URL={request_url!r} status={response.status_code} "
            f"headers={dict(response.headers)!r} first_500_bytes={response.content[:500]!r}"
        )

    content_type = response.headers.get("content-type", "").lower()
    if "image" not in content_type:
        raise RuntimeError(
            "Camera snapshot URL did not return an image. "
            f"URL={request_url!r} content_type={content_type!r} "
            f"first_500_bytes={response.content[:500]!r}"
        )

    try:
        return Image.open(BytesIO(response.content))
    except Exception as exc:
        raise RuntimeError(
            "Camera snapshot response could not be decoded as an image. "
            f"URL={request_url!r} content_type={content_type!r} "
            f"bytes={len(response.content)} first_500_bytes={response.content[:500]!r}"
        ) from exc


def _wrap_timelapse_function(func):
    signature = inspect.signature(func)
    allowed_kwargs = set(signature.parameters.keys())

    def wrapper(*args, **kwargs):
        filtered_kwargs = {key: value for key, value in kwargs.items() if key in allowed_kwargs}
        dropped = sorted(set(kwargs.keys()) - set(filtered_kwargs.keys()))
        if dropped:
            logger.info("Dropping unsupported timelapse kwargs for compatibility: %s", dropped)
        return func(*args, **filtered_kwargs)

    return wrapper


def apply_patches() -> None:
    core.get_pic_from_url = patched_get_pic_from_url
    core.create_timelapse = _wrap_timelapse_function(core.create_timelapse)
    logger.info("Applied AREDN WIP compatibility patches: snapshot fetch + timelapse kwargs")


def run():
    apply_patches()
    return core.run()
