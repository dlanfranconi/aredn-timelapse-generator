import copy
import json
from typing import Any, Dict
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import requests

from fenetre.http_auth import auth_from_camera_config


class ImageProfileError(RuntimeError):
    pass


SENSITIVE_KEYS = {"password", "passwd", "secret", "token", "authorization"}
SENSITIVE_QUERY_KEYS = SENSITIVE_KEYS | {"pass", "pwd", "auth"}


def _sanitize_url_for_logs(url: str) -> str:
    try:
        parsed = urlsplit(str(url))
    except Exception:
        return str(url)

    netloc = parsed.netloc
    if "@" in netloc:
        credentials, host = netloc.rsplit("@", 1)
        if ":" in credentials:
            username, _password = credentials.split(":", 1)
            netloc = f"{username}:***@{host}"
        else:
            netloc = f"***@{host}"

    redacted_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS:
            redacted_query.append((key, "***"))
        else:
            redacted_query.append((key, value))
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            urlencode(redacted_query, safe="*"),
            parsed.fragment,
        )
    )


def _bool_config(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _camera_host(camera_config: Dict[str, Any], profile_config: Dict[str, Any]) -> str:
    if profile_config.get("host"):
        return str(profile_config.get("host")).strip()
    ptz_config = camera_config.get("ptz") or {}
    if isinstance(ptz_config, dict) and (
        ptz_config.get("host") or ptz_config.get("ip")
    ):
        return str(ptz_config.get("host") or ptz_config.get("ip")).strip()
    parsed = urlparse(str(camera_config.get("url") or ""))
    return parsed.hostname or ""


def _profile_context(
    camera_name: str,
    camera_config: Dict[str, Any],
    profile_config: Dict[str, Any],
    profile_name: str,
) -> Dict[str, Any]:
    ptz_config = camera_config.get("ptz") or {}
    if not isinstance(ptz_config, dict):
        ptz_config = {}
    host = _camera_host(camera_config, profile_config)
    username = (
        profile_config.get("username")
        or ptz_config.get("username")
        or (camera_config.get("http_auth") or {}).get("username")
        or ""
    )
    password = (
        profile_config.get("password")
        or ptz_config.get("password")
        or (camera_config.get("http_auth") or {}).get("password")
        or ""
    )
    return {
        "camera": camera_name,
        "profile": profile_name,
        "host": host,
        "ip": host,
        "http_port": profile_config.get("http_port")
        or profile_config.get("port")
        or 80,
        "port": profile_config.get("port") or profile_config.get("http_port") or 80,
        "channel": profile_config.get("channel", 0),
        "username": username,
        "password": password,
    }


def _render_template(value: Any, context: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        rendered = value
        for key, replacement in context.items():
            rendered = rendered.replace("{" + key + "}", str(replacement))
        return rendered
    if isinstance(value, list):
        return [_render_template(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_template(item, context) for key, item in value.items()}
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_url_for_logs(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(item)
        return redacted
    return value


def _profile_config(camera_config: Dict[str, Any]) -> Dict[str, Any]:
    config = camera_config.get("image_profiles") or camera_config.get(
        "image_settings_profiles"
    )
    return config if isinstance(config, dict) else {}


def _profile_name_for_mode(
    profile_config: Dict[str, Any], mode: str | None
) -> str | None:
    if not mode:
        return None
    mapping = (
        profile_config.get("mode_profiles") or profile_config.get("mode_map") or {}
    )
    if isinstance(mapping, dict) and mapping.get(mode):
        return str(mapping.get(mode))
    return str(mode)


def _reolink_action_for_settings(
    profile_config: Dict[str, Any],
    profile: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any] | None:
    settings = profile.get("settings")
    if not isinstance(settings, dict) or not settings:
        return None
    if str(profile_config.get("vendor") or "").strip().lower() != "reolink":
        return None

    channel = int(context.get("channel") or 0)
    return {
        "name": "reolink-set-image",
        "method": "POST",
        "url": (
            "http://{host}:{http_port}/api.cgi"
            "?cmd=SetImage&channel={channel}&user={username}&password={password}"
        ),
        "json": [
            {
                "cmd": "SetImage",
                "action": 0,
                "param": {"Image": {"channel": channel, **settings}},
            }
        ],
    }


def _profile_actions(
    profile_config: Dict[str, Any],
    profile: Dict[str, Any],
    context: Dict[str, Any],
) -> list[Dict[str, Any]]:
    actions = profile.get("actions")
    if isinstance(actions, list):
        return [action for action in actions if isinstance(action, dict)]
    if profile.get("url"):
        return [profile]
    reolink_action = _reolink_action_for_settings(profile_config, profile, context)
    return [reolink_action] if reolink_action else []


def apply_image_profile(
    camera_name: str,
    camera_config: Dict[str, Any],
    profile_name: str | None = None,
    mode: str | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    profile_config = _profile_config(camera_config)
    if not _bool_config(profile_config.get("enabled"), False):
        raise ImageProfileError("Image profiles are not enabled for this camera.")

    profile_name = profile_name or _profile_name_for_mode(profile_config, mode)
    if not profile_name:
        raise ImageProfileError("profile or mode is required.")

    profiles = profile_config.get("profiles") or {}
    if not isinstance(profiles, dict) or profile_name not in profiles:
        raise ImageProfileError(
            f"Image profile '{profile_name}' is not configured for this camera."
        )
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise ImageProfileError(f"Image profile '{profile_name}' must be a mapping.")

    context = _profile_context(camera_name, camera_config, profile_config, profile_name)
    rendered_actions = []
    for action in _profile_actions(profile_config, profile, context):
        rendered_actions.append(_render_template(copy.deepcopy(action), context))
    if not rendered_actions:
        raise ImageProfileError(
            f"Image profile '{profile_name}' does not define any actions."
        )

    results = []
    for index, action in enumerate(rendered_actions):
        method = str(action.get("method") or "GET").upper()
        url = action.get("url")
        if not url:
            raise ImageProfileError(f"Image profile action {index} is missing url.")
        timeout = float(action.get("timeout_s") or profile_config.get("timeout_s") or 5)
        request_kwargs: Dict[str, Any] = {
            "timeout": timeout,
            "headers": action.get("headers") or {},
        }
        if "json" in action:
            request_kwargs["json"] = action.get("json")
        if "data" in action:
            request_kwargs["data"] = action.get("data")
        if action.get("auth") == "camera":
            request_auth = auth_from_camera_config(camera_config)
            if request_auth is not None:
                request_kwargs["auth"] = request_auth
        elif action.get("auth") == "basic":
            request_kwargs["auth"] = (
                context.get("username") or "",
                context.get("password") or "",
            )

        result = {
            "name": action.get("name") or f"action-{index + 1}",
            "method": method,
            "url": _sanitize_url_for_logs(str(url)),
            "dry_run": bool(dry_run),
        }
        if dry_run:
            result["request"] = _redact(
                {"url": url, "method": method, **request_kwargs}
            )
        else:
            response = requests.request(method, str(url), **request_kwargs)
            response.raise_for_status()
            result["status_code"] = response.status_code
        results.append(result)

    return {
        "ok": True,
        "camera": camera_name,
        "profile": profile_name,
        "mode": mode,
        "dry_run": bool(dry_run),
        "actions": results,
    }


def image_profile_summary(camera_config: Dict[str, Any]) -> Dict[str, Any]:
    profile_config = _profile_config(camera_config)
    profiles = profile_config.get("profiles") or {}
    if not isinstance(profiles, dict):
        profiles = {}
    return {
        "enabled": _bool_config(profile_config.get("enabled"), False),
        "vendor": str(profile_config.get("vendor") or "generic"),
        "profiles": sorted(profiles.keys()),
        "mode_profiles": profile_config.get("mode_profiles")
        or profile_config.get("mode_map")
        or {},
    }


def dumps_redacted(value: Any) -> str:
    return json.dumps(_redact(value), sort_keys=True)
