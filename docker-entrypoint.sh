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
