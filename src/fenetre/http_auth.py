from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from requests.auth import HTTPBasicAuth, HTTPDigestAuth

SUPPORTED_HTTP_AUTH_TYPES = {"basic", "digest"}


def auth_from_camera_config(camera_config: Mapping[str, Any] | None):
    if not camera_config:
        return None

    auth_config = camera_config.get("http_auth")
    if not isinstance(auth_config, Mapping):
        return None

    username = auth_config.get("username")
    password = auth_config.get("password")
    if username is None or password is None:
        return None

    auth_type = str(auth_config.get("type") or "basic").strip().lower()
    username = str(username)
    password = str(password)

    if auth_type == "digest":
        return HTTPDigestAuth(username, password)
    return HTTPBasicAuth(username, password)


def redact_sensitive_headers(headers) -> dict:
    redacted = {}
    for key, value in dict(headers or {}).items():
        if str(key).lower() == "authorization":
            redacted[key] = "REDACTED"
        else:
            redacted[key] = value
    return redacted
