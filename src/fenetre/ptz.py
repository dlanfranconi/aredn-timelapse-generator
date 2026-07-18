import time
from dataclasses import dataclass
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
        return ONVIFCamera(
            host, port, ptz_config.get("username"), ptz_config.get("password")
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
    if not profile_token:
        try:
            profiles = media_service.GetProfiles()
        except Exception as exc:
            raise PTZError(
                f"Could not read ONVIF media profiles from {host}:{port}. "
                "Check the ONVIF port, credentials, and camera ONVIF settings. "
                f"Original error: {exc}"
            ) from exc
        if not profiles:
            raise PTZError("No ONVIF media profiles were returned.")
        profile_token = profiles[0].token
    return ptz_service, profile_token


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
        raise PTZError(f"Preset '{preset_id}' is not configured for this camera.")

    session = acquire_session(camera_name, owner, duration_s)
    ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)

    request = ptz_service.create_type("GotoPreset")
    request.ProfileToken = profile_token
    request.PresetToken = preset["token"]
    ptz_service.GotoPreset(request)
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
    ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)
    try:
        raw_presets = ptz_service.GetPresets({"ProfileToken": profile_token})
    except Exception as exc:
        raise PTZError(f"Could not read ONVIF presets. Original error: {exc}") from exc

    presets = []
    for index, preset in enumerate(raw_presets or []):
        token = str(getattr(preset, "token", "") or index).strip()
        name = str(getattr(preset, "Name", "") or token).strip()
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
    ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)

    request = ptz_service.create_type("ContinuousMove")
    request.ProfileToken = profile_token
    request.Velocity = {
        "PanTilt": {
            "x": max(-1, min(1, float(pan))),
            "y": max(-1, min(1, float(tilt))),
        },
        "Zoom": {"x": max(-1, min(1, float(zoom)))},
    }
    ptz_service.ContinuousMove(request)
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
    result = continuous_move(
        camera_name,
        camera_config,
        pan=pan,
        tilt=tilt,
        zoom=zoom,
        owner=owner,
        duration_s=duration_s,
    )
    try:
        time.sleep(move_duration_s)
    finally:
        stop_move(camera_name, camera_config)
    result["move_duration_s"] = move_duration_s
    return result


def stop_move(camera_name: str, camera_config: Dict[str, Any]) -> Dict[str, Any]:
    ptz_config = camera_config.get("ptz") or {}
    if not ptz_configured(ptz_config):
        raise PTZError("PTZ is not fully configured for this camera.")
    ptz_service, profile_token = _onvif_services_and_profile_token(ptz_config)

    request = ptz_service.create_type("Stop")
    request.ProfileToken = profile_token
    request.PanTilt = True
    request.Zoom = True
    ptz_service.Stop(request)
    return {"ok": True, "camera": camera_name}
