import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable, Dict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    event_id = str(raw.get("id") or raw.get("slug") or _slug(name)).strip()
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
            event_plans.append(
                {
                    "id": plan_id,
                    "phase": _phase_for_event(event, now, pre_seconds, post_seconds),
                    "pre_seconds": pre_seconds,
                    "post_seconds": post_seconds,
                    "cameras": (
                        sorted((plan.get("cameras") or {}).keys())
                        if isinstance(plan.get("cameras"), dict)
                        else []
                    ),
                }
            )
        events.append(
            {key: value for key, value in event.items() if key != "raw"}
            | {"plans": event_plans}
        )
    return {
        "ok": True,
        "enabled": _bool_config(workflow.get("enabled"), False),
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
    ptz_config = camera_config.get("ptz") or {}
    image_profiles = camera_config.get("image_profiles") or {}
    host = (
        image_profiles.get("host")
        or ptz_config.get("host")
        or ptz_config.get("ip")
        or ""
    )
    return {
        "camera": camera_name,
        "plan": plan_id,
        "launch_id": event.get("id", ""),
        "launch_name": event.get("name", ""),
        "launch_time_utc": event.get("launch_time_utc", ""),
        "provider": event.get("provider", ""),
        "location": event.get("location", ""),
        "pad": event.get("pad", ""),
        "host": host,
        "ip": host,
        "username": ptz_config.get("username") or image_profiles.get("username") or "",
        "password": ptz_config.get("password") or image_profiles.get("password") or "",
        "active_full_viewers": active_full_viewers,
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
        if record.get("start_url") or record.get("start_command"):
            add("record_start", launch_ts - pre_seconds, "start")
        if record.get("stop_url") or record.get("stop_command"):
            add("record_stop", launch_ts + post_seconds, "stop")
        if record.get("download_url") or record.get("download_command"):
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
        config_key = action.get("config_key") or ""
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
