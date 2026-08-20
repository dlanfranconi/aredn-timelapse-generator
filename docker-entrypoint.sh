#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 0 && "$1" != --* ]]; then
    exec "$@"
fi

config_path="/srv/fenetre/config.yaml"
previous_arg=""
for arg in "$@"; do
    if [[ "$previous_arg" == "--config" ]]; then
        config_path="$arg"
        break
    fi
    if [[ "$arg" == --config=* ]]; then
        config_path="${arg#--config=}"
        break
    fi
    previous_arg="$arg"
done

# The container starts as root (no USER in the Dockerfile) so it can take
# ownership of host-mounted paths -- config.yaml, work_dir, log_dir -- that
# may predate this non-root image or were created via `sudo mkdir`/`sudo
# install` per the README, before dropping privileges to the fixed non-root
# "fenetre" account for everything that actually talks to the network or
# runs admin-configured commands. Re-execs this same script as that user,
# which skips this block on the second pass since id -u is no longer 0.
if [[ "$(id -u)" == "0" ]]; then
    target_uid="$(id -u fenetre)"
    target_gid="$(id -g fenetre)"
    target_home="$(getent passwd fenetre | cut -d: -f6)"

    take_ownership_once() {
        local path="$1"
        [[ -d "$path" ]] || mkdir -p "$path"
        local marker="${path}/.fenetre-owned-by-${target_uid}-${target_gid}"
        if [[ -f "$marker" ]]; then
            return 0
        fi
        echo "fenetre-docker-entrypoint: taking ownership of ${path} for the non-root 'fenetre' user (one-time; this can take a while for a large existing directory)..."
        chown -R "${target_uid}:${target_gid}" "$path"
        touch "$marker"
        echo "fenetre-docker-entrypoint: done with ${path}."
    }

    take_ownership_once "/srv/fenetre/data"
    take_ownership_once "/srv/fenetre/logs"

    # If global.media_dir is configured (see README: Media Storage Location),
    # work_dir/photos and work_dir/launches are symlinks into it -- a
    # separate host mount the entrypoint otherwise has no way to know about.
    # Peek at the raw config (best-effort; any parse error just skips this,
    # the app's own config loader will surface real problems) so that mount
    # gets the same one-time ownership treatment.
    media_dir="$(python - "$config_path" <<'PYEOF'
import sys
import yaml

try:
    with open(sys.argv[1]) as f:
        raw = yaml.safe_load(f) or {}
    cfg = raw.get("config") if isinstance(raw.get("config"), dict) else raw
    global_cfg = (cfg or {}).get("global") or {}
    print(global_cfg.get("media_dir") or "")
except Exception:
    print("")
PYEOF
    )"
    if [[ -n "$media_dir" ]]; then
        take_ownership_once "$media_dir"
    fi

    # config.yaml is normally a single-file bind mount; its containing
    # directory is baked into the image as fenetre-owned already (see
    # Dockerfile), so writing timestamped backups next to it just works once
    # the file itself is chowned too. Cheap enough to just always do, no
    # marker needed.
    if [[ -f "$config_path" ]]; then
        chown "${target_uid}:${target_gid}" "$config_path" || true
    fi
    if [[ -n "${FENETRE_PID_FILE:-}" ]]; then
        pid_dir="$(dirname "$FENETRE_PID_FILE")"
        [[ -d "$pid_dir" ]] && chown "${target_uid}:${target_gid}" "$pid_dir" 2>/dev/null || true
    fi

    # setpriv only changes uid/gid/groups -- it does not touch the
    # environment, so HOME/USER/LOGNAME would otherwise stay whatever root's
    # shell had (typically HOME=/root). Anything that caches under $HOME
    # (e.g. onvif-zeep/zeep's parsed-WSDL cache for ONVIF PTZ) would then try
    # to write to /root, which the fenetre user can't do, and fail with a
    # confusing "Permission denied: '/root/.cache'" from deep inside a
    # library rather than anything obviously about the user switch.
    export HOME="${target_home:-/srv/fenetre}"
    export USER=fenetre
    export LOGNAME=fenetre

    exec setpriv --reuid=fenetre --regid=fenetre --init-groups "$0" "$@"
fi

go2rtc_mode="${FENETRE_GO2RTC:-auto}"
go2rtc_config="${FENETRE_GO2RTC_CONFIG:-/tmp/fenetre-go2rtc.yaml}"
go2rtc_pid=""
fenetre_pid=""

shutdown() {
    if [[ -n "$fenetre_pid" ]] && kill -0 "$fenetre_pid" 2>/dev/null; then
        kill -TERM "$fenetre_pid" 2>/dev/null || true
    fi
    if [[ -n "$go2rtc_pid" ]] && kill -0 "$go2rtc_pid" 2>/dev/null; then
        kill -TERM "$go2rtc_pid" 2>/dev/null || true
    fi
    wait || true
}
trap shutdown TERM INT

if [[ "$go2rtc_mode" != "off" && "$go2rtc_mode" != "false" && "$go2rtc_mode" != "0" ]]; then
    if python -m fenetre.go2rtc "$config_path" "$go2rtc_config"; then
        echo "Starting go2rtc with generated config ${go2rtc_config}"
        go2rtc -config "$go2rtc_config" &
        go2rtc_pid="$!"
    else
        status="$?"
        if [[ "$go2rtc_mode" == "on" || "$go2rtc_mode" == "true" || "$go2rtc_mode" == "1" ]]; then
            echo "go2rtc was required but no runtime config could be generated" >&2
            exit "$status"
        fi
        echo "go2rtc not started; set global.go2rtc.enabled and at least one camera rtsp_url or ptz_rtsp_url to enable it"
    fi
fi

fenetre "$@" &
fenetre_pid="$!"

set +e
if [[ -n "$go2rtc_pid" ]]; then
    wait -n "$fenetre_pid" "$go2rtc_pid"
else
    wait "$fenetre_pid"
fi
exit_status="$?"
set -e
shutdown
exit "$exit_status"
