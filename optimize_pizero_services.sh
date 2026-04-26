#!/usr/bin/env bash
set -euo pipefail

if [[ "${TRACE:-0}" == "1" ]]; then
  set -x
fi

PROMETHEUS_NODE_EXPORTER_DEFAULTS="${PROMETHEUS_NODE_EXPORTER_DEFAULTS:-/etc/default/prometheus-node-exporter}"
PROMETHEUS_TEXTFILE_DIR="${PROMETHEUS_TEXTFILE_DIR:-/var/lib/prometheus/node-exporter}"
SERIAL_GETTY_UNIT="${SERIAL_GETTY_UNIT:-serial-getty@ttyS0.service}"
BOOT_CONFIG="${BOOT_CONFIG:-/boot/firmware/config.txt}"
BOOT_CMDLINE="${BOOT_CMDLINE:-/boot/firmware/cmdline.txt}"
GPU_MEM="${GPU_MEM:-64}"
CMA_SIZE_BYTES="${CMA_SIZE_BYTES:-201326592}"
AUDIO_BLACKLIST="${AUDIO_BLACKLIST:-/etc/modprobe.d/blacklist-fenetre-audio.conf}"

NODE_EXPORTER_ARGS=(
  --collector.disable-defaults
  --collector.cpu
  --collector.cpufreq
  --collector.diskstats
  --collector.filesystem
  --collector.filefd
  --collector.hwmon
  --collector.loadavg
  --collector.meminfo
  --collector.netclass
  --collector.netdev
  --collector.os
  --collector.powersupplyclass
  --collector.stat
  --collector.textfile
  --collector.thermal_zone
  --collector.time
  --collector.uname
  --collector.vmstat
  "--collector.textfile.directory=${PROMETHEUS_TEXTFILE_DIR}"
)

DISABLE_UNITS=(
  bluetooth.service
  ModemManager.service
  smartmontools.service
  nvmf-autoconnect.service
  nvmefc-boot-connections.service
  openipmi.service
  NetworkManager-wait-online.service
  avahi-daemon.service
  avahi-daemon.socket
)

RESET_FAILED_UNITS=(
  smartmontools.service
  nvmf-autoconnect.service
  openipmi.service
)

PROMETHEUS_HELPER_UNITS=(
  prometheus-node-exporter-apt.timer
  prometheus-node-exporter-apt.service
  prometheus-node-exporter-ipmitool-sensor.timer
  prometheus-node-exporter-mellanox-hca-temp.timer
  prometheus-node-exporter-nvme.timer
  prometheus-node-exporter-smartmon.timer
)

log() {
  printf '\n==> %s\n' "$*"
}

backup_file() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    sudo cp -a "${path}" "${path}.bak.$(date +%Y%m%d%H%M%S)"
  fi
}

have_unit() {
  systemctl list-unit-files "$1" --no-legend --no-pager 2>/dev/null | grep -q .
}

disable_unit_if_present() {
  local unit="$1"
  if have_unit "${unit}" || systemctl status "${unit}" >/dev/null 2>&1; then
    log "Disabling ${unit}"
    sudo systemctl disable --now "${unit}" >/dev/null 2>&1 || true
  else
    log "Skipping missing unit ${unit}"
  fi
}

reset_failed_if_present() {
  local unit="$1"
  if systemctl status "${unit}" >/dev/null 2>&1; then
    sudo systemctl reset-failed "${unit}" >/dev/null 2>&1 || true
  fi
}

join_node_exporter_args() {
  local joined=""
  local arg

  for arg in "${NODE_EXPORTER_ARGS[@]}"; do
    if [[ -n "${joined}" ]]; then
      joined+=" "
    fi
    joined+="${arg}"
  done

  printf '%s' "${joined}"
}

write_node_exporter_defaults() {
  local args
  local backup_path
  args="$(join_node_exporter_args)"

  if [[ ! -f "${PROMETHEUS_NODE_EXPORTER_DEFAULTS}" ]]; then
    log "Skipping node exporter tuning; ${PROMETHEUS_NODE_EXPORTER_DEFAULTS} does not exist"
    return
  fi

  backup_path="${PROMETHEUS_NODE_EXPORTER_DEFAULTS}.bak.$(date +%Y%m%d%H%M%S)"
  log "Backing up node exporter defaults to ${backup_path}"
  sudo cp -a "${PROMETHEUS_NODE_EXPORTER_DEFAULTS}" "${backup_path}"

  log "Restricting node exporter collectors"
  sudo sed -i "s|^ARGS=.*|ARGS=\"${args}\"|" "${PROMETHEUS_NODE_EXPORTER_DEFAULTS}"
}

restart_node_exporter_if_present() {
  if have_unit prometheus-node-exporter.service; then
    log "Restarting prometheus-node-exporter.service"
    sudo systemctl restart prometheus-node-exporter.service
  fi
}

set_or_append_boot_config() {
  local key="$1"
  local value="$2"

  if grep -q "^${key}=" "${BOOT_CONFIG}"; then
    sudo sed -i "s|^${key}=.*|${key}=${value}|" "${BOOT_CONFIG}"
  else
    printf '%s=%s\n' "${key}" "${value}" | sudo tee -a "${BOOT_CONFIG}" >/dev/null
  fi
}

optimize_boot_config() {
  if [[ ! -f "${BOOT_CONFIG}" || ! -f "${BOOT_CMDLINE}" ]]; then
    log "Skipping boot config tuning; ${BOOT_CONFIG} or ${BOOT_CMDLINE} is missing"
    return
  fi

  log "Backing up boot config files"
  backup_file "${BOOT_CONFIG}"
  backup_file "${BOOT_CMDLINE}"

  log "Applying headless boot config"
  set_or_append_boot_config dtparam "audio=off"
  set_or_append_boot_config camera_auto_detect 1
  set_or_append_boot_config display_auto_detect 0
  set_or_append_boot_config max_framebuffers 0
  set_or_append_boot_config enable_uart 0
  set_or_append_boot_config gpu_mem "${GPU_MEM}"
  set_or_append_boot_config arm_boost 1

  sudo sed -i 's|^dtoverlay=vc4-kms-v3d|#dtoverlay=vc4-kms-v3d|' "${BOOT_CONFIG}"
  sudo sed -i '/^arm_freq=/d' "${BOOT_CONFIG}"
  sudo sed -i '/^dtoverlay=cma,/d' "${BOOT_CONFIG}"
  if ! grep -q '^dtoverlay=disable-bt' "${BOOT_CONFIG}"; then
    printf '\n# Disable Bluetooth hardware on headless camera nodes.\ndtoverlay=disable-bt\n' \
      | sudo tee -a "${BOOT_CONFIG}" >/dev/null
  fi
  if [[ -n "${CMA_SIZE_BYTES}" ]]; then
    printf '\n# Reserve contiguous DMA memory for camera and hardware video encoding.\ndtoverlay=cma,cma-size=%s\n' "${CMA_SIZE_BYTES}" \
      | sudo tee -a "${BOOT_CONFIG}" >/dev/null
  fi

  sudo sed -i -E 's/(^| )console=(serial0|ttyS0),115200//g; s/^ +//; s/  +/ /g' "${BOOT_CMDLINE}"
  sudo sed -i -E 's/(^| )cma=[^ ]+//g; s/^ +//; s/  +/ /g' "${BOOT_CMDLINE}"
  sudo sh -c "tmp=\$(mktemp); tr -d '\n' <'${BOOT_CMDLINE}' >\"\${tmp}\"; printf '\n' >>\"\${tmp}\"; cat \"\${tmp}\" >'${BOOT_CMDLINE}'; rm -f \"\${tmp}\""
}

blacklist_audio_modules() {
  log "Blacklisting unused audio modules"
  sudo tee "${AUDIO_BLACKLIST}" >/dev/null <<'EOF'
# Headless fenetre.cam Pi nodes do not use onboard audio.
blacklist snd_bcm2835
blacklist snd_pcm
blacklist snd_timer
blacklist snd
EOF

  sudo modprobe -r snd_bcm2835 snd_pcm snd_timer snd >/dev/null 2>&1 || true
}

print_summary() {
  log "Running services after optimization"
  systemctl --type=service --state=running --no-pager --no-legend

  log "Memory summary"
  free -h

  if command -v prometheus-node-exporter >/dev/null && systemctl is-active --quiet prometheus-node-exporter.service; then
    log "Node exporter process"
    ps -o pid,pcpu,pmem,rss,args -C prometheus-node-exporter || true
  fi
}

main() {
  optimize_boot_config
  blacklist_audio_modules

  log "Disabling unused services"
  for unit in "${DISABLE_UNITS[@]}"; do
    disable_unit_if_present "${unit}"
  done

  log "Masking unused serial console"
  sudo systemctl disable --now "${SERIAL_GETTY_UNIT}" >/dev/null 2>&1 || true
  sudo systemctl mask "${SERIAL_GETTY_UNIT}" >/dev/null 2>&1 || true

  log "Clearing stale failed states"
  for unit in "${RESET_FAILED_UNITS[@]}"; do
    reset_failed_if_present "${unit}"
  done

  log "Disabling optional prometheus textfile helper collectors"
  for unit in "${PROMETHEUS_HELPER_UNITS[@]}"; do
    disable_unit_if_present "${unit}"
  done

  write_node_exporter_defaults
  restart_node_exporter_if_present
  print_summary

  log "Optimization complete"
  echo "fenetre.service was not started or disabled by this script."
  echo "Boot config changes require a reboot before they fully take effect."
}

main "$@"
