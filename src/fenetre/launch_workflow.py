import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from fenetre.image_profiles import apply_image_profile
from fenetre.ptz import goto_preset, set_tour_state


class LaunchWorkflowError(RuntimeError):
    pass


SENSITIVE_QUERY_KEYS = {
    "auth",
    "authorization",
    "pass",
    "passwd",
    "password",
    "pwd",
    "secret",
    "token",
}

LOCAL_RTSP_RECORDING_VENDORS = {"local_rtsp", "local-rtsp", "sunba", "sunba_local"}
_local_rtsp_recorders: Dict[str, Dict[str, Any]] = {}


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


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_url_for_logs(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_QUERY_KEYS:
                redacted[key] = "***"
            else:
                redacted[key] = _redact(item)
        return redacted
    return value


def _sanitize_command_for_logs(command: list[str]) -> str:
    return " ".join(
        shlex.quote(_sanitize_url_for_logs(part) if "://" in part else str(part))
        for part in command
    )


def launch_workflow_config(config: Dict[str, Any]) -> Dict[str, Any]:
    global_config = config.get("global") or {}
    workflow = global_config.get("launch_workflow") or global_config.get(
        "rocket_launches"
    )
    return workflow if isinstance(workflow, dict) else {}


def _bool_config(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return slug or "launch"


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _nested(raw: Dict[str, Any], *keys: str) -> Any:
    value: Any = raw
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def normalize_launch_event(
    raw: Dict[str, Any], index: int = 0
) -> Dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    launch_time = _parse_datetime(
        raw.get("launch_time_utc")
        or raw.get("launch_time")
        or raw.get("net")
        or raw.get("window_start")
        or raw.get("date_utc")
    )
    if not launch_time:
        return None
    provider = (
        raw.get("provider")
        or _nested(raw, "launch_service_provider", "name")
        or _nested(raw, "rocket", "configuration", "manufacturer", "name")
        or _nested(raw, "rocket", "configuration", "family")
        or ""
    )
    pad = (
        raw.get("pad")
        if isinstance(raw.get("pad"), str)
        else _nested(raw, "pad", "name")
    )
    location = (
        raw.get("location")
        or _nested(raw, "pad", "location", "name")
        or _nested(raw, "launchpad", "locality")
        or ""
    )
    status = raw.get("status")
    if isinstance(status, dict):
        status = status.get("name") or status.get("abbrev")
    name = raw.get("name") or _nested(raw, "mission", "name") or f"Launch {index + 1}"
    # event_id is templated into hook download_path/command values (see
    # _action_context) and used directly as a filesystem directory name
    # (list_past_launch_recordings, _default_launch_download_path). It comes
    # from an external, potentially spoofable schedule_url/schedule_file
    # source, so it's slugified unconditionally -- not just on the "name"
    # fallback -- to rule out path traversal via a crafted "id"/"slug" value
    # like "../../../etc/cron.d/x".
    event_id = _slug(str(raw.get("id") or raw.get("slug") or name).strip())
    return {
        "id": event_id,
        "name": str(name),
        "provider": str(provider or ""),
        "location": str(location or ""),
        "pad": str(pad or ""),
        "status": str(status or ""),
        "launch_time_utc": launch_time.isoformat(),
        "launch_timestamp": launch_time.timestamp(),
        "raw": raw,
    }


def _source_events(workflow: Dict[str, Any]) -> list[Dict[str, Any]]:
    if isinstance(workflow.get("schedule_events"), list):
        raw_data = workflow.get("schedule_events")
    elif workflow.get("schedule_file"):
        with open(str(workflow.get("schedule_file")), "r") as schedule_file:
            raw_data = json.load(schedule_file)
    elif workflow.get("schedule_url"):
        response = requests.get(
            str(workflow.get("schedule_url")),
            timeout=float(workflow.get("schedule_timeout_s") or 10),
        )
        response.raise_for_status()
        raw_data = response.json()
    else:
        raw_data = []

    if isinstance(raw_data, dict):
        if isinstance(raw_data.get("results"), list):
            raw_data = raw_data["results"]
        elif isinstance(raw_data.get("launches"), list):
            raw_data = raw_data["launches"]
        else:
            raw_data = [raw_data]
    if not isinstance(raw_data, list):
        raise LaunchWorkflowError("launch schedule source did not return a list.")

    events = []
    for index, raw in enumerate(raw_data):
        event = normalize_launch_event(raw, index)
        if event:
            events.append(event)
    return events


def _contains_any(value: str, needles: list[Any]) -> bool:
    if not needles:
        return True
    haystack = str(value or "").casefold()
    return any(str(needle).casefold() in haystack for needle in needles)


def _event_matches(event: Dict[str, Any], match: Dict[str, Any]) -> bool:
    if not isinstance(match, dict):
        return True
    checks = (
        ("providers", event.get("provider", "")),
        ("locations", event.get("location", "")),
        ("pads", event.get("pad", "")),
        ("names", event.get("name", "")),
        ("statuses", event.get("status", "")),
    )
    for key, value in checks:
        configured = match.get(key) or []
        if isinstance(configured, str):
            configured = [configured]
        if configured and not _contains_any(str(value), list(configured)):
            return False
    keywords = match.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    combined = " ".join(
        str(event.get(key, "")) for key in ("name", "provider", "location", "pad")
    )
    return _contains_any(combined, list(keywords))


def _plans(workflow: Dict[str, Any]) -> list[tuple[str, Dict[str, Any]]]:
    plans = workflow.get("plans") or {}
    if isinstance(plans, list):
        return [
            (str(plan.get("id") or plan.get("name") or f"plan-{idx + 1}"), plan)
            for idx, plan in enumerate(plans)
            if isinstance(plan, dict)
        ]
    if isinstance(plans, dict):
        return [
            (str(plan_id), plan)
            for plan_id, plan in plans.items()
            if isinstance(plan, dict)
        ]
    return []


def _plan_by_id(workflow: Dict[str, Any], plan_id: str) -> Dict[str, Any]:
    for candidate_id, plan in _plans(workflow):
        if candidate_id == plan_id:
            return plan
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _urls_from_text(value: Any) -> list[str]:
    if not value:
        return []
    text = str(value)
    matches = re.findall(r"(?:https?|rtsp)://[^\s'\"<>]+", text)
    return [match.rstrip("),]") for match in matches]


def _candidate_camera_urls(camera_config: Dict[str, Any]) -> list[str]:
    candidates = []
    for key in ("url", "rtsp_url", "ptz_rtsp_url"):
        candidates.extend(_urls_from_text(camera_config.get(key)))
    candidates.extend(_urls_from_text(camera_config.get("local_command")))
    return candidates


def _query_value(parsed, *keys: str) -> str:
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in keys:
        if query.get(key):
            return query[key]
    return ""


def _camera_network_context(camera_config: Dict[str, Any]) -> Dict[str, Any]:
    ptz_config = camera_config.get("ptz") or {}
    if not isinstance(ptz_config, dict):
        ptz_config = {}
    image_profiles = camera_config.get("image_profiles") or {}
    if not isinstance(image_profiles, dict):
        image_profiles = {}
    http_auth = camera_config.get("http_auth") or {}
    if not isinstance(http_auth, dict):
        http_auth = {}

    parsed_urls = []
    for url in _candidate_camera_urls(camera_config):
        try:
            parsed_urls.append(urlsplit(url))
        except Exception:
            continue
    http_url = next(
        (url for url in parsed_urls if url.scheme in {"http", "https"}), None
    )
    any_url = http_url or (parsed_urls[0] if parsed_urls else None)

    host = _first_text(
        image_profiles.get("host"),
        ptz_config.get("http_host"),
        ptz_config.get("host"),
        ptz_config.get("ip"),
        http_url.hostname if http_url else "",
        any_url.hostname if any_url else "",
    )
    scheme = _first_text(
        image_profiles.get("scheme"),
        camera_config.get("http_scheme"),
        http_url.scheme if http_url else "",
        "http",
    )
    http_port = _parse_int(
        _first_text(
            image_profiles.get("http_port"),
            camera_config.get("http_port"),
            http_url.port if http_url else "",
        ),
        443 if scheme == "https" else 80,
    )
    channel = _parse_int(
        _first_text(
            image_profiles.get("channel"),
            camera_config.get("channel"),
            ptz_config.get("channel"),
            _query_value(http_url, "channel", "chn") if http_url else "",
        ),
        0,
    )
    username = _first_text(
        image_profiles.get("username"),
        ptz_config.get("username"),
        http_auth.get("username"),
        _query_value(http_url, "user", "username") if http_url else "",
        http_url.username if http_url else "",
        any_url.username if any_url else "",
    )
    password = _first_text(
        image_profiles.get("password"),
        ptz_config.get("password"),
        http_auth.get("password"),
        _query_value(http_url, "password", "passwd", "pwd") if http_url else "",
        http_url.password if http_url else "",
        any_url.password if any_url else "",
    )
    return {
        "host": host,
        "ip": host,
        "scheme": scheme,
        "http_port": http_port,
        "port": http_port,
        "channel": channel,
        "username": username,
        "password": password,
    }


def _phase_for_event(
    event: Dict[str, Any],
    now: datetime,
    pre_seconds: int,
    post_seconds: int,
) -> str:
    launch_ts = float(event["launch_timestamp"])
    now_ts = now.timestamp()
    if now_ts < launch_ts - pre_seconds:
        return "pending"
    if now_ts < launch_ts:
        return "prelaunch"
    if now_ts <= launch_ts + post_seconds:
        return "recording"
    return "complete"


def preview_launch_workflow(
    config: Dict[str, Any],
    now: datetime | None = None,
) -> Dict[str, Any]:
    workflow = launch_workflow_config(config)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    enabled = _bool_config(workflow.get("enabled"), False)
    if not enabled:
        return {
            "ok": True,
            "enabled": False,
            "dry_run": _bool_config(workflow.get("dry_run"), True),
            "now": now.isoformat(),
            "events": [],
        }
    default_pre = int(workflow.get("default_pre_seconds") or 60)
    default_post = int(workflow.get("default_post_seconds") or 600)
    lookahead_seconds = int(float(workflow.get("lookahead_hours") or 168) * 3600)
    events = []
    for event in _source_events(workflow):
        if event["launch_timestamp"] < now.timestamp() - default_post:
            continue
        if event["launch_timestamp"] > now.timestamp() + lookahead_seconds:
            continue
        event_plans = []
        for plan_id, plan in _plans(workflow):
            if not _bool_config(plan.get("enabled"), True):
                continue
            if not _event_matches(event, plan.get("match") or {}):
                continue
            pre_seconds = int(plan.get("pre_seconds") or default_pre)
            post_seconds = int(plan.get("post_seconds") or default_post)
            camera_plans = (
                plan.get("cameras") if isinstance(plan.get("cameras"), dict) else {}
            )
            camera_details = []
            for camera_name, camera_plan in sorted(camera_plans.items()):
                if not isinstance(camera_plan, dict):
                    continue
                record = camera_plan.get("record") or {}
                if not isinstance(record, dict):
                    record = {}
                camera_details.append(
                    {
                        "name": str(camera_name),
                        "preset": str(camera_plan.get("preset") or ""),
                        "image_profile": str(camera_plan.get("image_profile") or ""),
                        "pause_tour": _bool_config(
                            camera_plan.get("pause_tour"), False
                        ),
                        "record": bool(
                            _record_vendor(record)
                            or record.get("start_url")
                            or record.get("start_command")
                            or record.get("stop_url")
                            or record.get("stop_command")
                            or record.get("download_url")
                            or record.get("download_command")
                        ),
                        "download_path": str(record.get("download_path") or ""),
                        "skip_when_full_viewers": _bool_config(
                            record.get("skip_when_full_viewers"), False
                        ),
                    }
                )
            event_plans.append(
                {
                    "id": plan_id,
                    "phase": _phase_for_event(event, now, pre_seconds, post_seconds),
                    "pre_seconds": pre_seconds,
                    "post_seconds": post_seconds,
                    "cameras": [camera["name"] for camera in camera_details],
                    "camera_details": camera_details,
                }
            )
        events.append(
            {key: value for key, value in event.items() if key != "raw"}
            | {"plans": event_plans}
        )
    return {
        "ok": True,
        "enabled": enabled,
        "dry_run": _bool_config(workflow.get("dry_run"), True),
        "now": now.isoformat(),
        "events": events,
    }


def _action_context(
    event: Dict[str, Any],
    plan_id: str,
    camera_name: str,
    camera_config: Dict[str, Any],
    active_full_viewers: int = 0,
) -> Dict[str, Any]:
    network = _camera_network_context(camera_config)
    context = {
        "camera": camera_name,
        "plan": plan_id,
        "launch_id": event.get("id", ""),
        "launch_name": event.get("name", ""),
        "launch_time_utc": event.get("launch_time_utc", ""),
        "provider": event.get("provider", ""),
        "location": event.get("location", ""),
        "pad": event.get("pad", ""),
        "active_full_viewers": active_full_viewers,
    }
    context.update(network)
    return context


def _timezone_for_config(config: Dict[str, Any]):
    timezone_name = str((config.get("global") or {}).get("timezone") or "UTC")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _record_window(
    config: Dict[str, Any],
    workflow: Dict[str, Any],
    plan: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, datetime]:
    launch_time = _parse_datetime(event.get("launch_time_utc"))
    if not launch_time:
        raise LaunchWorkflowError("launch event has no usable launch_time_utc.")
    pre_seconds = int(
        plan.get("pre_seconds") or workflow.get("default_pre_seconds") or 60
    )
    post_seconds = int(
        plan.get("post_seconds") or workflow.get("default_post_seconds") or 600
    )
    start_utc = launch_time - timedelta(seconds=pre_seconds)
    stop_utc = launch_time + timedelta(seconds=post_seconds)
    local_tz = _timezone_for_config(config)
    return {
        "start_utc": start_utc,
        "stop_utc": stop_utc,
        "start_local": start_utc.astimezone(local_tz),
        "stop_local": stop_utc.astimezone(local_tz),
    }


def _add_record_window_context(
    context: Dict[str, Any],
    config: Dict[str, Any],
    workflow: Dict[str, Any],
    plan: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, Any]:
    window = _record_window(config, workflow, plan, event)
    context.update(
        {
            "record_start_utc": window["start_utc"].isoformat(),
            "record_stop_utc": window["stop_utc"].isoformat(),
            "record_start_local": window["start_local"].isoformat(),
            "record_stop_local": window["stop_local"].isoformat(),
        }
    )
    return context


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


def _camera_plan_actions(
    event: Dict[str, Any],
    plan_id: str,
    camera_name: str,
    camera_plan: Dict[str, Any],
    pre_seconds: int,
    post_seconds: int,
) -> list[Dict[str, Any]]:
    launch_ts = float(event["launch_timestamp"])
    actions = []

    def add(kind: str, due_ts: float, config_key: str | None = None):
        action = {
            "key": f"{event['id']}:{plan_id}:{camera_name}:{kind}",
            "kind": kind,
            "event": {key: value for key, value in event.items() if key != "raw"},
            "plan": plan_id,
            "camera": camera_name,
            "due_at": datetime.fromtimestamp(due_ts, timezone.utc).isoformat(),
        }
        if config_key:
            action["config_key"] = config_key
        actions.append(action)

    if camera_plan.get("pause_tour"):
        add("pause_tour", launch_ts - pre_seconds)
    if camera_plan.get("image_profile"):
        add("image_profile", launch_ts - pre_seconds)
    if camera_plan.get("preset"):
        add("goto_preset", launch_ts - pre_seconds)
    record = camera_plan.get("record") or {}
    if isinstance(record, dict):
        vendor = _record_vendor(record)
        reolink_manual_record = vendor == "reolink" and _reolink_manual_record_enabled(
            record
        )
        if (
            reolink_manual_record
            or _is_local_rtsp_record_vendor(vendor)
            or record.get("start_url")
            or record.get("start_command")
        ):
            add("record_start", launch_ts - pre_seconds, "start")
        if (
            reolink_manual_record
            or _is_local_rtsp_record_vendor(vendor)
            or record.get("stop_url")
            or record.get("stop_command")
        ):
            add("record_stop", launch_ts + post_seconds, "stop")
        if (
            vendor == "reolink"
            or record.get("download_url")
            or record.get("download_command")
        ):
            add(
                "download_recording",
                launch_ts
                + post_seconds
                + int(record.get("download_delay_seconds") or 0),
                "download",
            )
    if camera_plan.get("resume_tour", camera_plan.get("pause_tour", False)):
        add("resume_tour", launch_ts + post_seconds)
    return actions


def due_launch_actions(
    config: Dict[str, Any],
    now: datetime | None = None,
    state: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    workflow = launch_workflow_config(config)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    executed = ((state or {}).get("actions") or {}).keys()
    default_pre = int(workflow.get("default_pre_seconds") or 60)
    default_post = int(workflow.get("default_post_seconds") or 600)
    cameras = config.get("cameras") or {}
    actions = []
    for event in _source_events(workflow):
        for plan_id, plan in _plans(workflow):
            if not _bool_config(plan.get("enabled"), True):
                continue
            if not _event_matches(event, plan.get("match") or {}):
                continue
            camera_plans = plan.get("cameras") or {}
            if not isinstance(camera_plans, dict):
                continue
            pre_seconds = int(plan.get("pre_seconds") or default_pre)
            post_seconds = int(plan.get("post_seconds") or default_post)
            for camera_name, camera_plan in camera_plans.items():
                if camera_name not in cameras or not isinstance(camera_plan, dict):
                    continue
                for action in _camera_plan_actions(
                    event, plan_id, camera_name, camera_plan, pre_seconds, post_seconds
                ):
                    due_at = _parse_datetime(action["due_at"])
                    if due_at and due_at <= now and action["key"] not in executed:
                        actions.append(action)
    return sorted(actions, key=lambda action: action["due_at"])


def _state_path(config: Dict[str, Any]) -> str | None:
    workflow = launch_workflow_config(config)
    if workflow.get("state_file"):
        return str(workflow.get("state_file"))
    work_dir = (config.get("global") or {}).get("work_dir")
    if not work_dir:
        return None
    return os.path.join(str(work_dir), "launch_workflow_state.json")


def load_launch_state(config: Dict[str, Any]) -> Dict[str, Any]:
    path = _state_path(config)
    if not path or not os.path.exists(path):
        return {"actions": {}}
    with open(path, "r") as state_file:
        state = json.load(state_file)
    if not isinstance(state, dict):
        return {"actions": {}}
    state.setdefault("actions", {})
    return state


def save_launch_state(config: Dict[str, Any], state: Dict[str, Any]) -> None:
    path = _state_path(config)
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as state_file:
        json.dump(state, state_file, indent=2, sort_keys=True)


def _execute_hook(
    action: Dict[str, Any],
    hook_config: Dict[str, Any],
    context: Dict[str, Any],
    dry_run: bool,
) -> Dict[str, Any]:
    rendered = _render_template(hook_config, context)
    if (
        rendered.get("skip_when_full_viewers")
        and context.get("active_full_viewers", 0) > 0
    ):
        return {
            "ok": True,
            "skipped": True,
            "reason": "active full-stream viewers are present",
            "active_full_viewers": context.get("active_full_viewers", 0),
        }
    if rendered.get("command"):
        command = str(rendered.get("command"))
        result = {
            "ok": True,
            "kind": action["kind"],
            "command": command,
            "dry_run": dry_run,
        }
        if dry_run:
            return result
        completed = subprocess.run(
            shlex.split(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=float(rendered.get("timeout_s") or 30),
        )
        result.update(
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            }
        )
        if completed.returncode != 0:
            raise LaunchWorkflowError(
                f"{action['kind']} command failed with exit code {completed.returncode}"
            )
        return result
    if rendered.get("url"):
        method = str(rendered.get("method") or "GET").upper()
        result = {
            "ok": True,
            "kind": action["kind"],
            "method": method,
            "url": _sanitize_url_for_logs(str(rendered["url"])),
            "dry_run": dry_run,
        }
        if dry_run:
            return result
        request_kwargs: Dict[str, Any] = {
            "timeout": float(rendered.get("timeout_s") or 10),
            "headers": rendered.get("headers") or {},
        }
        if "json" in rendered:
            request_kwargs["json"] = rendered.get("json")
        if "data" in rendered:
            request_kwargs["data"] = rendered.get("data")
        response = requests.request(method, str(rendered["url"]), **request_kwargs)
        response.raise_for_status()
        result["status_code"] = response.status_code
        if rendered.get("download_path"):
            download_path = str(rendered.get("download_path"))
            directory = os.path.dirname(download_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(download_path, "wb") as output_file:
                output_file.write(response.content)
            result["download_path"] = download_path
            result["bytes"] = len(response.content)
        return result
    raise LaunchWorkflowError(f"{action['kind']} hook has no command or url.")


def _record_vendor(record: Dict[str, Any]) -> str:
    return (
        str(record.get("vendor") or record.get("recording_vendor") or "")
        .strip()
        .lower()
    )


def _reolink_manual_record_enabled(record: Dict[str, Any]) -> bool:
    return _bool_config(
        record.get("manual_record", record.get("trigger_manual_record")),
        True,
    )


def _is_local_rtsp_record_vendor(vendor: str) -> bool:
    return str(vendor or "").strip().lower() in LOCAL_RTSP_RECORDING_VENDORS


def _reolink_api_url(context: Dict[str, Any], record: Dict[str, Any]) -> str:
    host = str(record.get("host") or context.get("host") or "").strip()
    if not host:
        raise LaunchWorkflowError("Reolink recording requires a camera host.")
    port = _parse_int(record.get("http_port") or context.get("http_port"), 80)
    scheme = (
        str(
            record.get("scheme")
            or record.get("protocol")
            or record.get("http_scheme")
            or ""
        )
        .strip()
        .lower()
    )
    if not scheme:
        if port == 443:
            scheme = "https"
        elif port == 80:
            scheme = "http"
        else:
            scheme = str(context.get("scheme") or "http").strip().lower()
    if scheme not in {"http", "https"}:
        raise LaunchWorkflowError(
            "Reolink recording API scheme must be either http or https."
        )
    default_port = 443 if scheme == "https" else 80
    port_part = "" if port == default_port else f":{port}"
    return f"{scheme}://{host}{port_part}/cgi-bin/api.cgi"


def _reolink_request_params(
    context: Dict[str, Any], record: Dict[str, Any], command: str
) -> Dict[str, Any]:
    username = str(record.get("username") or context.get("username") or "").strip()
    password = str(record.get("password") or context.get("password") or "")
    params: Dict[str, Any] = {"cmd": command}
    if username:
        params["user"] = username
        params["password"] = password
    elif record.get("token"):
        params["token"] = record.get("token")
    return params


def _reolink_result_url(
    context: Dict[str, Any], record: Dict[str, Any], command: str
) -> str:
    url = _reolink_api_url(context, record)
    params = _reolink_request_params(context, record, command)
    return _sanitize_url_for_logs(f"{url}?{urlencode(params)}")


def _reolink_check_json(command: str, payload: Any) -> Any:
    if not isinstance(payload, list) or not payload:
        raise LaunchWorkflowError(f"Reolink {command} returned unexpected data.")
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("code", 0) != 0:
            error = item.get("error") if isinstance(item.get("error"), dict) else {}
            detail = str(error.get("detail") or "").strip()
            rsp_code = error.get("rspCode")
            if command == "SetManualRec" and (
                detail == "not support"
                or rsp_code in {-9, -17}
                or item.get("cmd") == "Unknown"
            ):
                raise LaunchWorkflowError(
                    "This Reolink camera does not support SetManualRec. In Launch "
                    "Automation, either disable 'Trigger manual recording' to only "
                    "search/download existing SD/NVR recordings, or switch the "
                    "camera to 'Local HD RTSP recording' for server-side launch "
                    f"capture. Original response: {item}"
                )
            raise LaunchWorkflowError(
                f"Reolink {command} returned API error code {item.get('code')}: {item}"
            )
        value = item.get("value")
        if isinstance(value, dict):
            rsp_code = value.get("rspCode")
            if rsp_code not in (None, 200):
                raise LaunchWorkflowError(
                    f"Reolink {command} returned response code {rsp_code}: {item}"
                )
    return payload


def _reolink_post_json(
    context: Dict[str, Any],
    record: Dict[str, Any],
    command: str,
    body: list[Dict[str, Any]],
    dry_run: bool,
) -> Dict[str, Any]:
    result = {
        "ok": True,
        "vendor": "reolink",
        "command": command,
        "method": "POST",
        "url": _reolink_result_url(context, record, command),
        "json": _redact(body),
        "dry_run": dry_run,
    }
    if dry_run:
        return result
    response = requests.request(
        "POST",
        _reolink_api_url(context, record),
        params=_reolink_request_params(context, record, command),
        json=body,
        timeout=float(record.get("timeout_s") or 10),
    )
    response.raise_for_status()
    result["response"] = _redact(_reolink_check_json(command, response.json()))
    result["status_code"] = response.status_code
    return result


def _reolink_time(value: datetime) -> Dict[str, int]:
    return {
        "year": value.year,
        "mon": value.month,
        "day": value.day,
        "hour": value.hour,
        "min": value.minute,
        "sec": value.second,
    }


def _reolink_search_files(
    context: Dict[str, Any],
    record: Dict[str, Any],
    window: Dict[str, datetime],
    dry_run: bool,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    channel = _parse_int(record.get("channel") or context.get("channel"), 0)
    stream_type = str(record.get("stream_type") or record.get("stream") or "main")
    body = [
        {
            "cmd": "Search",
            "action": 0,
            "param": {
                "Search": {
                    "channel": channel,
                    "onlyStatus": 0,
                    "streamType": stream_type,
                    "StartTime": _reolink_time(window["start_local"]),
                    "EndTime": _reolink_time(window["stop_local"]),
                }
            },
        }
    ]
    result = {
        "ok": True,
        "vendor": "reolink",
        "command": "Search",
        "method": "POST",
        "url": _reolink_result_url(context, record, "Search"),
        "json": body,
        "dry_run": dry_run,
    }
    if dry_run:
        return result, []
    response = requests.request(
        "POST",
        _reolink_api_url(context, record),
        params=_reolink_request_params(context, record, "Search"),
        json=body,
        timeout=float(record.get("timeout_s") or 20),
    )
    response.raise_for_status()
    payload = _reolink_check_json("Search", response.json())
    result["status_code"] = response.status_code
    files = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        files.extend(
            ((item.get("value") or {}).get("SearchResult") or {}).get("File") or []
        )
    result["files"] = [
        {
            "name": str(file.get("name") or ""),
            "size": file.get("size"),
            "type": file.get("type"),
        }
        for file in files
        if isinstance(file, dict)
    ]
    return result, [
        file for file in files if isinstance(file, dict) and file.get("name")
    ]


def _default_launch_download_path(
    config: Dict[str, Any], event: Dict[str, Any], camera_name: str
) -> str:
    work_dir = str((config.get("global") or {}).get("work_dir") or "/tmp")
    launch_id = _slug(str(event.get("id") or event.get("name") or "launch"))
    camera_id = _slug(camera_name)
    return os.path.join(work_dir, "launches", launch_id, f"{launch_id}-{camera_id}.mp4")


def _launch_file_url(work_dir: str, path: str) -> str:
    rel_path = os.path.relpath(path, work_dir)
    return "/" + "/".join(quote(part) for part in rel_path.split(os.sep))


def _camera_name_for_recording(
    launch_id: str, filename: str, camera_slug_map: Dict[str, str]
) -> str:
    stem, _extension = os.path.splitext(filename)
    launch_slug = _slug(launch_id)
    camera_slug = stem
    prefix = f"{launch_slug}-"
    if camera_slug.startswith(prefix):
        camera_slug = camera_slug[len(prefix) :]
    camera_slug = re.sub(r"-\d{2}$", "", camera_slug)
    return camera_slug_map.get(camera_slug.casefold(), camera_slug)


def camera_name_for_recording_path(
    config: Dict[str, Any], launch_id: str, filename: str
) -> str:
    """Map a launch recording's filename back to its configured camera name.

    Used by the public HTTP handler to enforce the same per-camera
    visibility rules on raw launch recording files as on the launch history
    API, since the on-disk filename is the only place that association is
    recorded once a recording exists.
    """
    camera_slug_map = {
        _slug(str(camera_name)).casefold(): str(camera_name)
        for camera_name in (config.get("cameras") or {}).keys()
    }
    return _camera_name_for_recording(launch_id, filename, camera_slug_map)


def list_past_launch_recordings(
    config: Dict[str, Any], limit: int = 100
) -> Dict[str, Any]:
    workflow = launch_workflow_config(config)
    enabled = _bool_config(workflow.get("enabled"), False)
    work_dir = str((config.get("global") or {}).get("work_dir") or "")
    storage_config = (config.get("global") or {}).get("storage_management") or {}
    result = {
        "ok": True,
        "enabled": enabled,
        "launches": [],
        "retention": {
            "policy": "work_dir",
            "storage_management_enabled": _bool_config(
                storage_config.get("enabled"), False
            ),
            "work_dir_max_size_GB": storage_config.get("work_dir_max_size_GB"),
        },
    }
    if not enabled or not work_dir:
        return result

    launches_dir = os.path.join(work_dir, "launches")
    if not os.path.isdir(launches_dir):
        return result

    camera_slug_map = {
        _slug(str(camera_name)).casefold(): str(camera_name)
        for camera_name in (config.get("cameras") or {}).keys()
    }
    launch_items = []
    for entry in os.scandir(launches_dir):
        if not entry.is_dir():
            continue
        recordings = []
        for root, _dirs, files in os.walk(entry.path):
            for filename in sorted(files):
                extension = os.path.splitext(filename)[1].lower()
                if extension not in {".mp4", ".mov", ".mkv", ".webm", ".ts"}:
                    continue
                path = os.path.join(root, filename)
                if not os.path.isfile(path):
                    continue
                stat = os.stat(path)
                if stat.st_size <= 0:
                    continue
                recordings.append(
                    {
                        "camera": _camera_name_for_recording(
                            entry.name, filename, camera_slug_map
                        ),
                        "filename": filename,
                        "url": _launch_file_url(work_dir, path),
                        "bytes": stat.st_size,
                        "mtime": int(stat.st_mtime),
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc
                        ).isoformat(),
                    }
                )
        if recordings:
            recordings.sort(key=lambda item: item["mtime"], reverse=True)
            launch_mtime = max(item["mtime"] for item in recordings)
            launch_items.append(
                {
                    "id": entry.name,
                    "recordings": recordings,
                    "mtime": launch_mtime,
                    "modified_at": datetime.fromtimestamp(
                        launch_mtime, timezone.utc
                    ).isoformat(),
                    "recording_count": len(recordings),
                    "bytes": sum(int(item["bytes"]) for item in recordings),
                }
            )
    launch_items.sort(key=lambda item: item["mtime"], reverse=True)
    result["launches"] = launch_items[: max(1, int(limit or 100))]
    return result


def _download_path_for_index(download_path: str, index: int, total: int) -> str:
    if total <= 1:
        return download_path
    root, ext = os.path.splitext(download_path)
    return f"{root}-{index:02d}{ext or '.mp4'}"


def _reolink_download_files(
    context: Dict[str, Any],
    record: Dict[str, Any],
    files: list[Dict[str, Any]],
    download_path: str,
) -> list[Dict[str, Any]]:
    command = str(record.get("download_method") or "Download").strip() or "Download"
    if command not in {"Download", "Playback"}:
        raise LaunchWorkflowError(
            "Reolink download_method must be either Download or Playback."
        )
    downloaded = []
    for index, file in enumerate(files, start=1):
        source = str(file.get("name") or "")
        if not source:
            continue
        output_path = _download_path_for_index(download_path, index, len(files))
        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        params = _reolink_request_params(context, record, command)
        params.update({"source": source, "output": os.path.basename(output_path)})
        response = requests.request(
            "GET",
            _reolink_api_url(context, record),
            params=params,
            timeout=float(
                record.get("download_timeout_s") or record.get("timeout_s") or 120
            ),
        )
        response.raise_for_status()
        with open(output_path, "wb") as output_file:
            output_file.write(response.content)
        downloaded.append(
            {
                "source": source,
                "download_path": output_path,
                "bytes": len(response.content),
                "url": _sanitize_url_for_logs(
                    f"{_reolink_api_url(context, record)}?{urlencode(params)}"
                ),
            }
        )
    return downloaded


def _execute_reolink_record_action(
    config: Dict[str, Any],
    action: Dict[str, Any],
    record: Dict[str, Any],
    context: Dict[str, Any],
    window: Dict[str, datetime],
    dry_run: bool,
) -> Dict[str, Any]:
    channel = _parse_int(record.get("channel") or context.get("channel"), 0)
    kind = action["kind"]
    if kind == "record_start":
        if not _reolink_manual_record_enabled(record):
            return {
                "ok": True,
                "vendor": "reolink",
                "kind": kind,
                "skipped": True,
                "reason": "manual Reolink recording is disabled",
                "dry_run": dry_run,
            }
        duration = _parse_int(
            record.get("manual_record_duration_s"),
            max(
                600,
                int((window["stop_utc"] - window["start_utc"]).total_seconds()) + 300,
            ),
        )
        return _reolink_post_json(
            context,
            record,
            "SetManualRec",
            [
                {
                    "cmd": "SetManualRec",
                    "action": 0,
                    "param": {
                        "Rec": {"channel": channel, "enable": 1, "duration": duration}
                    },
                }
            ],
            dry_run,
        )
    if kind == "record_stop":
        if not _reolink_manual_record_enabled(record):
            return {
                "ok": True,
                "vendor": "reolink",
                "kind": kind,
                "skipped": True,
                "reason": "manual Reolink recording is disabled",
                "dry_run": dry_run,
            }
        return _reolink_post_json(
            context,
            record,
            "SetManualRec",
            [
                {
                    "cmd": "SetManualRec",
                    "action": 0,
                    "param": {"Rec": {"channel": channel, "enable": 0}},
                }
            ],
            dry_run,
        )
    if kind == "download_recording":
        if (
            record.get("skip_when_full_viewers")
            and context.get("active_full_viewers", 0) > 0
        ):
            return {
                "ok": True,
                "skipped": True,
                "reason": "active full-stream viewers are present",
                "active_full_viewers": context.get("active_full_viewers", 0),
            }
        download_path = _render_template(
            record.get("download_path")
            or _default_launch_download_path(config, action["event"], action["camera"]),
            context,
        )
        search_result, files = _reolink_search_files(context, record, window, dry_run)
        result = {
            "ok": True,
            "vendor": "reolink",
            "dry_run": dry_run,
            "download_path": download_path,
            "search": search_result,
        }
        if dry_run:
            return result
        if not files:
            raise LaunchWorkflowError("Reolink Search returned no recordings.")
        result["downloads"] = _reolink_download_files(
            context, record, files, str(download_path)
        )
        return result
    raise LaunchWorkflowError(f"Unsupported Reolink launch action '{kind}'.")


def _local_rtsp_recording_key(action: Dict[str, Any]) -> str:
    event_id = str((action.get("event") or {}).get("id") or "launch")
    return f"{event_id}:{action.get('plan')}:{action.get('camera')}"


def _local_rtsp_recording_source(
    camera_config: Dict[str, Any], record: Dict[str, Any], context: Dict[str, Any]
) -> str:
    source = _render_template(
        record.get("rtsp_url")
        or record.get("source_url")
        or camera_config.get("rtsp_url")
        or "",
        context,
    )
    source = str(source or "").strip()
    if source:
        return source
    raise LaunchWorkflowError(
        "Local RTSP launch recording requires the camera's high-definition rtsp_url "
        "or record.rtsp_url. The low-resolution PTZ aiming stream is not used unless "
        "it is explicitly configured as record.rtsp_url."
    )


def _local_rtsp_recording_path(
    config: Dict[str, Any],
    action: Dict[str, Any],
    record: Dict[str, Any],
    context: Dict[str, Any],
) -> str:
    path = _render_template(
        record.get("download_path")
        or record.get("output_path")
        or _default_launch_download_path(config, action["event"], action["camera"]),
        context,
    )
    return str(path)


def _local_rtsp_ffmpeg_command(
    source_url: str,
    output_path: str,
    record: Dict[str, Any],
    duration_s: int,
) -> list[str]:
    transport = str(record.get("rtsp_transport") or record.get("transport") or "tcp")
    video_codec = str(record.get("video_codec") or record.get("codec") or "copy")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        str(record.get("loglevel") or "error"),
        "-rtsp_transport",
        transport,
        "-i",
        source_url,
        "-map",
        "0:v:0",
        "-an",
        "-t",
        str(max(1, int(duration_s or 1))),
    ]
    if video_codec == "copy":
        command.extend(["-c:v", "copy"])
    else:
        command.extend(["-c:v", video_codec])
    command.extend(["-movflags", "+faststart", "-y", output_path])
    return command


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _execute_local_rtsp_record_action(
    config: Dict[str, Any],
    action: Dict[str, Any],
    camera_config: Dict[str, Any],
    record: Dict[str, Any],
    context: Dict[str, Any],
    window: Dict[str, datetime],
    dry_run: bool,
) -> Dict[str, Any]:
    kind = action["kind"]
    recording_key = _local_rtsp_recording_key(action)
    output_path = _local_rtsp_recording_path(config, action, record, context)
    duration_s = _parse_int(
        record.get("manual_record_duration_s") or record.get("duration_s"),
        max(1, int((window["stop_utc"] - window["start_utc"]).total_seconds()) + 300),
    )
    result = {
        "ok": True,
        "vendor": "local_rtsp",
        "kind": kind,
        "camera": action["camera"],
        "recording_key": recording_key,
        "download_path": output_path,
        "dry_run": dry_run,
    }

    if kind == "record_start":
        existing = _local_rtsp_recorders.get(recording_key)
        existing_process = (
            existing.get("process") if isinstance(existing, dict) else None
        )
        if existing_process and existing_process.poll() is None:
            result.update(
                {
                    "already_running": True,
                    "pid": existing_process.pid,
                    "command": existing.get("command"),
                }
            )
            return result

        source_url = _local_rtsp_recording_source(camera_config, record, context)
        command = _local_rtsp_ffmpeg_command(
            source_url, output_path, record, duration_s
        )
        result.update(
            {
                "source": _sanitize_url_for_logs(source_url),
                "command": _sanitize_command_for_logs(command),
                "duration_s": duration_s,
            }
        )
        if dry_run:
            return result

        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _local_rtsp_recorders[recording_key] = {
            "process": process,
            "path": output_path,
            "command": result["command"],
            "started_at": time.time(),
        }
        result["pid"] = process.pid
        return result

    if kind == "record_stop":
        recorder = _local_rtsp_recorders.pop(recording_key, None)
        process = recorder.get("process") if isinstance(recorder, dict) else None
        if process and process.poll() is None:
            timeout_s = float(record.get("stop_timeout_s") or 10)
            process.terminate()
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                result["killed"] = True
        result["returncode"] = process.poll() if process else None
        result["bytes"] = _file_size(output_path)
        result["url"] = (
            _launch_file_url(
                str((config.get("global") or {}).get("work_dir")), output_path
            )
            if (config.get("global") or {}).get("work_dir")
            else ""
        )
        if not process:
            result["warning"] = (
                "No in-memory local RTSP recorder was running for this launch. "
                "The process may have already ended or Fenetre may have restarted."
            )
        return result

    if kind == "download_recording":
        result["bytes"] = _file_size(output_path)
        result["url"] = (
            _launch_file_url(
                str((config.get("global") or {}).get("work_dir")), output_path
            )
            if (config.get("global") or {}).get("work_dir")
            else ""
        )
        return result

    raise LaunchWorkflowError(f"Unsupported local RTSP launch action '{kind}'.")


def test_reolink_recording_action(
    config: Dict[str, Any],
    camera_name: str,
    record: Dict[str, Any],
    kind: str,
    dry_run: bool = True,
    now: datetime | None = None,
    window_seconds: int = 900,
) -> Dict[str, Any]:
    cameras = config.get("cameras") or {}
    camera_config = cameras.get(camera_name)
    if not isinstance(camera_config, dict):
        raise LaunchWorkflowError(f"Camera '{camera_name}' was not found.")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_id = f"reolink-test-{int(now.timestamp())}"
    event = {
        "id": event_id,
        "name": "Reolink Recording Test",
        "launch_time_utc": now.isoformat(),
    }
    action = {
        "key": f"{event_id}:admin-test:{camera_name}:{kind}",
        "kind": kind,
        "event": event,
        "plan": "admin-test",
        "camera": camera_name,
        "config_key": {
            "record_start": "start",
            "record_stop": "stop",
            "download_recording": "download",
        }.get(kind, ""),
    }
    context = _action_context(event, "admin-test", camera_name, camera_config)
    context.update(
        {
            "record_start_utc": (now - timedelta(seconds=window_seconds)).isoformat(),
            "record_stop_utc": now.isoformat(),
            "record_start_local": (now - timedelta(seconds=window_seconds))
            .astimezone(_timezone_for_config(config))
            .isoformat(),
            "record_stop_local": now.astimezone(
                _timezone_for_config(config)
            ).isoformat(),
        }
    )
    window = {
        "start_utc": now - timedelta(seconds=window_seconds),
        "stop_utc": now,
        "start_local": (now - timedelta(seconds=window_seconds)).astimezone(
            _timezone_for_config(config)
        ),
        "stop_local": now.astimezone(_timezone_for_config(config)),
    }
    record = {**record, "vendor": "reolink"}
    return _execute_reolink_record_action(
        config, action, record, context, window, dry_run=bool(dry_run)
    )


def execute_launch_action(
    config: Dict[str, Any],
    action: Dict[str, Any],
    dry_run: bool = True,
    active_view_counter: Callable[[str, str], int] | None = None,
) -> Dict[str, Any]:
    cameras = config.get("cameras") or {}
    camera_name = action["camera"]
    camera_config = cameras.get(camera_name)
    if not isinstance(camera_config, dict):
        raise LaunchWorkflowError(f"Camera '{camera_name}' was not found.")

    workflow = launch_workflow_config(config)
    plan = _plan_by_id(workflow, str(action["plan"]))
    camera_plan = (plan.get("cameras") or {}).get(camera_name) or {}
    if not isinstance(camera_plan, dict):
        raise LaunchWorkflowError(f"No launch camera plan for '{camera_name}'.")
    active_full_viewers = (
        int(active_view_counter(camera_name, "full")) if active_view_counter else 0
    )
    context = _action_context(
        action["event"],
        action["plan"],
        camera_name,
        camera_config,
        active_full_viewers=active_full_viewers,
    )
    context = _add_record_window_context(
        context, config, workflow, plan, action["event"]
    )

    kind = action["kind"]
    if kind == "pause_tour":
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "key": action["key"],
                "kind": kind,
                "camera": camera_name,
                "tour": "pause",
                "active_full_viewers": active_full_viewers,
            }
        return set_tour_state(
            camera_name, camera_config, "pause", owner="launch-workflow"
        )
    if kind == "resume_tour":
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "key": action["key"],
                "kind": kind,
                "camera": camera_name,
                "tour": "resume",
                "active_full_viewers": active_full_viewers,
            }
        return set_tour_state(
            camera_name, camera_config, "resume", owner="launch-workflow"
        )
    if kind == "goto_preset":
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "key": action["key"],
                "kind": kind,
                "camera": camera_name,
                "preset": str(camera_plan.get("preset")),
                "active_full_viewers": active_full_viewers,
            }
        return goto_preset(
            camera_name,
            camera_config,
            str(camera_plan.get("preset")),
            owner="launch-workflow",
        )
    if kind == "image_profile":
        if dry_run:
            return apply_image_profile(
                camera_name,
                camera_config,
                profile_name=str(camera_plan.get("image_profile")),
                dry_run=True,
            )
        return apply_image_profile(
            camera_name,
            camera_config,
            profile_name=str(camera_plan.get("image_profile")),
            dry_run=False,
        )
    if kind in {"record_start", "record_stop", "download_recording"}:
        record = camera_plan.get("record") or {}
        if not isinstance(record, dict):
            record = {}
        config_key = action.get("config_key") or ""
        vendor = _record_vendor(record)
        if vendor == "reolink" and not (
            record.get(f"{config_key}_command") or record.get(f"{config_key}_url")
        ):
            return _execute_reolink_record_action(
                config,
                action,
                record,
                context,
                _record_window(config, workflow, plan, action["event"]),
                dry_run=bool(dry_run),
            )
        if _is_local_rtsp_record_vendor(vendor) and not (
            record.get(f"{config_key}_command") or record.get(f"{config_key}_url")
        ):
            return _execute_local_rtsp_record_action(
                config,
                action,
                camera_config,
                record,
                context,
                _record_window(config, workflow, plan, action["event"]),
                dry_run=bool(dry_run),
            )
        hook_config = {}
        if record.get(f"{config_key}_command"):
            hook_config["command"] = record.get(f"{config_key}_command")
        if record.get(f"{config_key}_url"):
            hook_config["url"] = record.get(f"{config_key}_url")
        for key in (
            f"{config_key}_method",
            f"{config_key}_headers",
            f"{config_key}_json",
            f"{config_key}_data",
            f"{config_key}_timeout_s",
            f"{config_key}_download_path",
        ):
            if key in record:
                hook_config[key.replace(f"{config_key}_", "")] = record[key]
        if config_key == "download" and record.get("download_path"):
            hook_config["download_path"] = record.get("download_path")
        if record.get("skip_when_full_viewers"):
            hook_config["skip_when_full_viewers"] = True
        return _execute_hook(action, hook_config, context, dry_run=bool(dry_run))
    raise LaunchWorkflowError(f"Unsupported launch action kind '{kind}'.")


def run_due_launch_actions(
    config: Dict[str, Any],
    now: datetime | None = None,
    dry_run: bool | None = None,
    active_view_counter: Callable[[str, str], int] | None = None,
) -> Dict[str, Any]:
    workflow = launch_workflow_config(config)
    if not _bool_config(workflow.get("enabled"), False):
        return {"ok": True, "enabled": False, "actions": []}
    if dry_run is None:
        dry_run = _bool_config(workflow.get("dry_run"), True)

    state = load_launch_state(config)
    actions = due_launch_actions(config, now=now, state=state)
    results = []
    for action in actions:
        try:
            result = execute_launch_action(
                config,
                action,
                dry_run=bool(dry_run),
                active_view_counter=active_view_counter,
            )
            results.append({"action": action, "result": result})
            if not dry_run:
                state.setdefault("actions", {})[action["key"]] = {
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                }
        except Exception as exc:
            results.append({"action": action, "error": str(exc)})
    if not dry_run:
        save_launch_state(config, state)
    return {
        "ok": True,
        "enabled": True,
        "dry_run": bool(dry_run),
        "actions": results,
    }
