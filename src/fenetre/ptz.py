import time
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, List


class PTZError(RuntimeError):
    pass


class PTZBackendUnavailable(PTZError):
    pass


class PTZLocked(PTZError):
    pass


@dataclass
class PTZSession:
    owner: str
    expires_at: float


_sessions: Dict[str, PTZSession] = {}
_locks: Dict[str, Dict[str, Any]] = {}
_endpoint_locks: Dict[str, RLock] = {}
_endpoint_locks_guard = RLock()
_profile_token_cache: Dict[str, str] = {}
_endpoint_failure_backoffs: Dict[str, float] = {}


def _now() -> float:
    return time.time()


def normalize_presets(ptz_config: Dict[str, Any]) -> List[Dict[str, str]]:
    presets = []
    raw_presets = ptz_config.get("presets") or []
    if isinstance(raw_presets, dict):
        raw_presets = [
            {"id": preset_id, **preset}
            for preset_id, preset in raw_presets.items()
            if isinstance(preset, dict)
        ]
    for index, preset in enumerate(raw_presets):
        if not isinstance(preset, dict):
            continue
        preset_id = str(preset.get("id") or preset.get("name") or index).strip()
        name = str(preset.get("name") or preset_id).strip()
        token = str(preset.get("token") or preset_id).strip()
        if not preset_id or not name or not token:
            continue
        presets.append({"id": preset_id, "name": name, "token": token})
    return presets


def public_ptz_metadata(ptz_config: Dict[str, Any]) -> Dict[str, Any]:
    presets = [
        {"id": preset["id"], "name": preset["name"]}
        for preset in normalize_presets(ptz_config)
    ]
    return {
        "enabled": bool(ptz_config.get("enabled", False)),
        "public": bool(ptz_config.get("public", False)),
        "allow_presets": bool(ptz_config.get("allow_presets", True)),
        "allow_manual_control": bool(ptz_config.get("allow_manual_control", False)),
        "access_level": ptz_config.get("access_level", "presets"),
        "presets": presets,
    }


def ptz_configured(ptz_config: Dict[str, Any]) -> bool:
    return bool(
        ptz_config.get("enabled")
        and (ptz_config.get("host") or ptz_config.get("ip"))
        and ptz_config.get("username")
        and ptz_config.get("password")
    )


def current_session(camera_name: str) -> Dict[str, Any] | None:
    session = _sessions.get(camera_name)
    if not session:
        return None
    if session.expires_at <= _now():
        _sessions.pop(camera_name, None)
        return None
    return {
        "owner": session.owner,
        "expires_at": int(session.expires_at),
        "seconds_remaining": max(0, int(session.expires_at - _now())),
    }


def set_lock(camera_name: str, locked: bool, reason: str = "") -> Dict[str, Any]:
    if locked:
        _locks[camera_name] = {"locked": True, "reason": reason or ""}
    else:
        _locks.pop(camera_name, None)
    return lock_status(camera_name)


def lock_status(camera_name: str) -> Dict[str, Any]:
    return _locks.get(camera_name, {"locked": False, "reason": ""})


def acquire_session(
    camera_name: str, owner: str, duration_s: int = 60
) -> Dict[str, Any]:
    status = lock_status(camera_name)
    if status.get("locked"):
        raise PTZLocked(status.get("reason") or "PTZ controls are locked.")
    session = current_session(camera_name)
    if session and session["owner"] != owner:
        raise PTZLocked(
            f"PTZ control is reserved for {session['seconds_remaining']} more seconds."
        )
    expires_at = _now() + max(1, int(duration_s or 60))
    _sessions[camera_name] = PTZSession(owner=owner, expires_at=expires_at)
    return current_session(camera_name)


def ptz_status(camera_name: str) -> Dict[str, Any]:
    return {
        "camera": camera_name,
        "lock": lock_status(camera_name),
        "session": current_session(camera_name),
    }


def _endpoint_lock_key(ptz_config: Dict[str, Any]) -> str:
    host = str(ptz_config.get("host") or ptz_config.get("ip") or "").strip().lower()
    port = int(ptz_config.get("port") or 80)
    return f"{host}:{port}"


def _profile_token_cache_key(ptz_config: Dict[str, Any]) -> str:
    username = str(ptz_config.get("username") or "").strip().lower()
    return f"{_endpoint_lock_key(ptz_config)}:{username}"


def _operation_cooldown_s(ptz_config: Dict[str, Any]) -> float:
    return max(0, float(ptz_config.get("failure_cooldown_s") or 60))


def _raise_if_endpoint_in_backoff(ptz_config: Dict[str, Any]):
    key = _endpoint_lock_key(ptz_config)
    expires_at = _endpoint_failure_backoffs.get(key)
    if not expires_at:
        return
    remaining = int(expires_at - _now())
    if remaining <= 0:
        _endpoint_failure_backoffs.pop(key, None)
        return
    raise PTZError(
        f"ONVIF PTZ for {key} is cooling down for {remaining}s after a failed "
        "PTZ operation. This prevents repeated attempts from rebooting fragile "
        "camera firmware."
    )


def _mark_endpoint_failure(ptz_config: Dict[str, Any]):
    cooldown = _operation_cooldown_s(ptz_config)
    if cooldown <= 0:
        return
    _endpoint_failure_backoffs[_endpoint_lock_key(ptz_config)] = _now() + cooldown


def _clear_endpoint_failure(ptz_config: Dict[str, Any]):
    _endpoint_failure_backoffs.pop(_endpoint_lock_key(ptz_config), None)


@contextmanager
def _endpoint_lock(ptz_config: Dict[str, Any]):
    key = _endpoint_lock_key(ptz_config)
    with _endpoint_locks_guard:
        lock = _endpoint_locks.setdefault(key, RLock())
    with lock:
        yield


def _retryable_onvif_setup_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        phrase in text
        for phrase in (
            "connection refused",
            "failed to establish a new connection",
            "timed out",
            "connection aborted",
            "connection reset",
            "remote end closed",
        )
    )


def _retry_onvif_setup(call):
    last_exc = None
    for attempt in range(3):
        try:
            return call()
        except Exception as exc:
            last_exc = exc
            if attempt >= 2 or not _retryable_onvif_setup_error(exc):
                raise
            time.sleep(0.25 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def _call_ptz_operation(ptz_config: Dict[str, Any], description: str, call):
    _raise_if_endpoint_in_backoff(ptz_config)
    try:
        result = call()
    except Exception as exc:
        _mark_endpoint_failure(ptz_config)
        raise PTZError(
            f"ONVIF PTZ {description} failed for {_endpoint_lock_key(ptz_config)}. "
            "The command was not retried because this camera may reboot on "
            f"malformed or unsupported PTZ requests. Original error: {exc}"
        ) from exc
    _clear_endpoint_failure(ptz_config)
    return result


def _onvif_camera(ptz_config: Dict[str, Any]):
    try:
        from onvif import ONVIFCamera
    except ImportError as exc:
        raise PTZBackendUnavailable(
            "ONVIF PTZ backend is not installed. Install the ptz optional dependency."
        ) from exc

    host = ptz_config.get("host") or ptz_config.get("ip")
    port = int(ptz_config.get("port") or 80)
    try:
        return _retry_onvif_setup(
            lambda: ONVIFCamera(
                host, port, ptz_config.get("username"), ptz_config.get("password")
            )
        )
    except Exception as exc:
        raise PTZError(
            f"Could not connect to ONVIF service at {host}:{port}. "
            "Check that ONVIF is enabled on the camera, that the ONVIF host/port "
            "is reachable from the Fenetre container, and that the ONVIF username "
            f"and password are correct. Original error: {exc}"
        ) from exc


def _onvif_services_and_profile_token(ptz_config: Dict[str, Any]):
    host = ptz_config.get("host") or ptz_config.get("ip")
    port = int(ptz_config.get("port") or 80)
    camera = _onvif_camera(ptz_config)
    try:
        media_service = camera.create_media_service()
        ptz_service = camera.create_ptz_service()
    except Exception as exc:
        raise PTZError(
            f"Could not create ONVIF media/PTZ services at {host}:{port}. "
            "The configured port may not be the ONVIF service port, or this "
            f"camera/account may not expose ONVIF PTZ. Original error: {exc}"
        ) from exc

    profile_token = ptz_config.get("profile_token")
    cache_key = _profile_token_cache_key(ptz_config)
    if not profile_token:
        profile_token = _profile_token_cache.get(cache_key)
    if not profile_token:
        try:
            profiles = _retry_onvif_setup(media_service.GetProfiles)
        except Exception as exc:
            raise PTZError(
                f"Could not read ONVIF media profiles from {host}:{port}. "
                "Check the ONVIF port, credentials, and camera ONVIF settings. "
                "If this camera works in another NVR, configure the ONVIF "
                "profile token explicitly to skip media-profile discovery. "
                f"Original error: {exc}"
            ) from exc
        if not profiles:
            raise PTZError("No ONVIF media profiles were returned.")
        profile_token = profiles[0].token
        if profile_token:
            _profile_token_cache[cache_key] = str(profile_token)
    return ptz_service, profile_token


def _send_continuous_move(
    ptz_config: Dict[str, Any], ptz_service, profile_token: str, pan=0, tilt=0, zoom=0
):
    request = ptz_service.create_type("ContinuousMove")
    request.ProfileToken = profile_token
    request.Velocity = {
        "PanTilt": {
            "x": max(-1, min(1, float(pan))),
            "y": max(-1, min(1, float(tilt))),
        },
        "Zoom": {"x": max(-1, min(1, float(zoom)))},
    }
    _call_ptz_operation(
        ptz_config, "continuous move", lambda: ptz_service.ContinuousMove(request)
    )


def _send_relative_move(
    ptz_config: Dict[str, Any],
    ptz_service,
    profile_token: str,
    pan=0,
    tilt=0,
    zoom=0,
):
    scale = max(0.001, min(0.25, float(ptz_config.get("relative_move_scale") or 0.1)))
    request = ptz_service.create_type("RelativeMove")
    request.ProfileToken = profile_token
    request.Translation = {
        "PanTilt": {
            "x": max(-1, min(1, float(pan))) * scale,
            "y": max(-1, min(1, float(tilt))) * scale,
        },
        "Zoom": {"x": max(-1, min(1, float(zoom))) * scale},
    }
    _call_ptz_operation(
        ptz_config, "relative move", lambda: ptz_service.RelativeMove(request)
    )


def _send_stop(ptz_config: Dict[str, Any], ptz_service, profile_token: str):
    request = ptz_service.create_type("Stop")
    request.ProfileToken = profile_token
    request.PanTilt = True
    request.Zoom = True
    _call_ptz_operation(ptz_config, "stop", lambda: ptz_service.Stop(request))


def goto_preset(
    camera_name: str,
    camera_config: Dict[str, Any],
    preset_id: str,
    owner: str = "public",
    duration_s: int = 60,
) -> Dict[str, Any]:
    ptz_config = camera_config.get("ptz") or {}
    if not ptz_configured(ptz_config):
        raise PTZError("PTZ is not fully configured for this camera.")
    preset = next(
        (item for item in normalize_presets(ptz_config) if item["id"] == preset_id),
        None,
    )
    if not preset:
        if normalize_presets(ptz_config):
            raise PTZError(f"Preset '{preset_id}' is not configured for this camera.")
        preset = {"id": preset_id, "name": preset_id, "token": preset_id}

    session = acquire_session(camera_name, owner, duration_s)
    with _endpoint_lock(ptz_config):
        ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)

        request = ptz_service.create_type("GotoPreset")
        request.ProfileToken = profile_token
        request.PresetToken = preset["token"]
        _call_ptz_operation(
            ptz_config, "preset", lambda: ptz_service.GotoPreset(request)
        )
    return {
        "ok": True,
        "camera": camera_name,
        "preset": preset["id"],
        "session": session,
    }


def discover_presets(
    camera_name: str,
    camera_config: Dict[str, Any],
    owner: str = "public",
    duration_s: int = 60,
) -> Dict[str, Any]:
    ptz_config = camera_config.get("ptz") or {}
    if not ptz_configured(ptz_config):
        raise PTZError("PTZ is not fully configured for this camera.")

    session = acquire_session(camera_name, owner, duration_s)
    with _endpoint_lock(ptz_config):
        ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)
        try:
            raw_presets = ptz_service.GetPresets({"ProfileToken": profile_token})
        except Exception as exc:
            raise PTZError(
                f"Could not read ONVIF presets. Original error: {exc}"
            ) from exc

    presets = []
    for index, preset in enumerate(raw_presets or []):
        token = str(getattr(preset, "token", "") or index).strip()
        name = str(getattr(preset, "Name", "") or "").strip()
        if not token or not name:
            continue
        presets.append({"id": token, "name": name, "token": token})
    return {"ok": True, "camera": camera_name, "presets": presets, "session": session}


def continuous_move(
    camera_name: str,
    camera_config: Dict[str, Any],
    pan: float = 0,
    tilt: float = 0,
    zoom: float = 0,
    owner: str = "public",
    duration_s: int = 60,
) -> Dict[str, Any]:
    ptz_config = camera_config.get("ptz") or {}
    if not ptz_configured(ptz_config):
        raise PTZError("PTZ is not fully configured for this camera.")
    session = acquire_session(camera_name, owner, duration_s)

    with _endpoint_lock(ptz_config):
        ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)
        _send_continuous_move(
            ptz_config, ptz_service, profile_token, pan=pan, tilt=tilt, zoom=zoom
        )
    return {"ok": True, "camera": camera_name, "session": session}


def nudge_move(
    camera_name: str,
    camera_config: Dict[str, Any],
    pan: float = 0,
    tilt: float = 0,
    zoom: float = 0,
    move_duration_s: float = 0.25,
    owner: str = "public",
    duration_s: int = 60,
) -> Dict[str, Any]:
    move_duration_s = max(0.05, min(2.0, float(move_duration_s or 0.25)))
    ptz_config = camera_config.get("ptz") or {}
    if not ptz_configured(ptz_config):
        raise PTZError("PTZ is not fully configured for this camera.")
    session = acquire_session(camera_name, owner, duration_s)
    with _endpoint_lock(ptz_config):
        ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)
        if str(ptz_config.get("move_mode") or "").strip().lower() == "relative":
            _send_relative_move(
                ptz_config, ptz_service, profile_token, pan=pan, tilt=tilt, zoom=zoom
            )
        else:
            _send_continuous_move(
                ptz_config, ptz_service, profile_token, pan=pan, tilt=tilt, zoom=zoom
            )
            try:
                time.sleep(move_duration_s)
            finally:
                _send_stop(ptz_config, ptz_service, profile_token)
    result = {"ok": True, "camera": camera_name, "session": session}
    result["move_duration_s"] = move_duration_s
    return result


def stop_move(camera_name: str, camera_config: Dict[str, Any]) -> Dict[str, Any]:
    ptz_config = camera_config.get("ptz") or {}
    if not ptz_configured(ptz_config):
        raise PTZError("PTZ is not fully configured for this camera.")

    with _endpoint_lock(ptz_config):
        ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)
        _send_stop(ptz_config, ptz_service, profile_token)
    return {"ok": True, "camera": camera_name}
