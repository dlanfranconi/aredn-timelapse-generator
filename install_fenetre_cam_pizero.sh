#!/usr/bin/env bash
set -euo pipefail

if [[ "${TRACE:-0}" == "1" ]]; then
  set -x
fi

APP_USER="${APP_USER:-fenetre}"
APP_GROUP="${APP_GROUP:-fenetre}"
INSTALL_USER="${SUDO_USER:-${USER}}"
APP_DIR="${APP_DIR:-/srv/fenetre.cam}"
REPO_URL="${REPO_URL:-https://github.com/matfra/fenetre.cam.git}"
BRANCH="${BRANCH:-feature/pizero-compat}"
DATA_DIR="${DATA_DIR:-/srv/fenetre/data}"
VENV_DIR="${VENV_DIR:-${APP_DIR}/venv}"

PYTHON_PACKAGES=(
  python3-pytz
  python3-yaml
  python3-absl
  python3-numpy
  python3-cairosvg
  python3-astral
  python3-waitress
  python3-prometheus-client
  python3-piexif
  python3-skimage
  python3-paho-mqtt
  python3-flask
  python3-venv
  python3-requests
  python3-pil
)

SYSTEM_PACKAGES=(
  acl
  ffmpeg
  vim
  nginx
  rpicam-apps
  git
)

PIP_PACKAGES=(
  mozjpeg-lossless-optimization
)

log() {
  printf '\n==> %s\n' "$*"
}

require_non_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    echo "Run this script as the install user, not directly as root." >&2
    echo "The script uses sudo only for the steps that need it." >&2
    exit 1
  fi
}

create_user_and_groups() {
  log "Ensuring ${APP_USER} user and groups exist"

  if ! getent group "${APP_GROUP}" >/dev/null; then
    sudo groupadd --system "${APP_GROUP}"
  fi

  if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    sudo useradd \
      --system \
      --gid "${APP_GROUP}" \
      --no-create-home \
      --shell /usr/sbin/nologin \
      "${APP_USER}"
  fi

  sudo usermod -a -G "${APP_GROUP}" "${INSTALL_USER}"
  sudo usermod -a -G video "${APP_USER}"
}

install_apt_packages() {
  log "Installing apt dependencies"
  sudo apt update
  sudo apt install -y "${PYTHON_PACKAGES[@]}" "${SYSTEM_PACKAGES[@]}"
}

checkout_branch() {
  if git -C "${APP_DIR}" show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    if git -C "${APP_DIR}" checkout "${BRANCH}"; then
      return
    fi
  else
    if git -C "${APP_DIR}" checkout -b "${BRANCH}" --track "origin/${BRANCH}"; then
      return
    fi
  fi

  if git -C "${APP_DIR}" diff --quiet "origin/${BRANCH}" -- .; then
    git -C "${APP_DIR}" checkout -f -B "${BRANCH}" "origin/${BRANCH}"
    return
  fi

  echo "Cannot switch ${APP_DIR} to ${BRANCH}; local changes differ from origin/${BRANCH}." >&2
  echo "Commit, stash, or move those changes before rerunning this installer." >&2
  exit 1
}

sync_source() {
  log "Ensuring ${BRANCH} checkout exists at ${APP_DIR}"

  if [[ -d "${APP_DIR}/.git" ]]; then
    git -C "${APP_DIR}" fetch origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"
    checkout_branch
  elif [[ -e "${APP_DIR}" ]]; then
    echo "${APP_DIR} exists but is not a git checkout; refusing to overwrite it." >&2
    exit 1
  else
    sudo install -d -o "${INSTALL_USER}" -g "${APP_GROUP}" -m 2775 "${APP_DIR}"
    git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
  fi

  sudo chgrp -R "${APP_GROUP}" "${APP_DIR}"
  sudo chmod -R u+rwX,g+rwX,o-rwx "${APP_DIR}"
  sudo find "${APP_DIR}" -type d -exec chmod g+s {} +
  sudo find "${APP_DIR}" -type d -exec setfacl \
    -m g::rwx,m::rwx \
    -m d:u::rwx,d:g::rwx,d:o::---,d:m::rwx \
    {} +
}

create_runtime_dirs() {
  log "Ensuring runtime directories exist"
  sudo mkdir -p "${DATA_DIR}" "${DATA_DIR}/logs"
  sudo chown -R "${APP_USER}:${APP_GROUP}" "${DATA_DIR}"
  sudo chmod -R u+rwX,g+rwX,o-rwx "${DATA_DIR}"
  sudo find "${DATA_DIR}" -type d -exec chmod g+s {} +

  # Keep new files group-writable for the fenetre group even when writers use
  # the usual 022 umask. The setgid bit above keeps group ownership stable.
  sudo find "${DATA_DIR}" -type d -exec setfacl \
    -m g::rwx,m::rwx \
    -m d:u::rwx,d:g::rwx,d:o::---,d:m::rwx \
    {} +
}

install_package() {
  log "Installing fenetre.cam into ${VENV_DIR}"

  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    sudo -u "${APP_USER}" python3 -m venv --system-site-packages "${VENV_DIR}"
  fi

  sudo -u "${APP_USER}" "${VENV_DIR}/bin/pip" install "${PIP_PACKAGES[@]}"
  sudo -u "${APP_USER}" "${VENV_DIR}/bin/pip" install --no-deps -e "${APP_DIR}"
}

main() {
  require_non_root
  install_apt_packages
  create_user_and_groups
  sync_source
  create_runtime_dirs
  install_package

  log "Bootstrap complete"
  echo "App dir:  ${APP_DIR}"
  echo "Branch:   ${BRANCH}"
  echo "Data dir: ${DATA_DIR}"
  echo "Run:      sudo -u ${APP_USER} ${VENV_DIR}/bin/fenetre --config=${APP_DIR}/config.yaml"
}

main "$@"
