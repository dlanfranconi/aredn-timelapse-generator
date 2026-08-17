FROM ubuntu:26.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/srv/fenetre/venv \
    PATH=/srv/fenetre/venv/bin:$PATH

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        python3 \
        python3-dev \
        python3-venv && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /srv/fenetre/app

RUN python3 -m venv "$VIRTUAL_ENV"

COPY pyproject.toml README.md ./
COPY src/fenetre ./src/fenetre

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -e '.[gopro,ptz]' && \
    pip uninstall -y pip setuptools wheel && \
    find "$VIRTUAL_ENV" -type d -name '__pycache__' -prune -exec rm -rf '{}' + && \
    find "$VIRTUAL_ENV" -type f -name '*.py[co]' -delete

FROM ubuntu:26.04

# go2rtc doesn't publish a checksums file, so the sha256 for each
# architecture's release binary is pinned below and verified at build time.
# Bumping GO2RTC_VERSION requires updating those hashes too (fetch the new
# release asset and `sha256sum` it) -- the build fails closed if they don't
# match rather than skipping verification.
ARG GO2RTC_VERSION=1.9.14
ARG TARGETARCH
# Fixed, non-root uid:gid the app and go2rtc run as (see
# docker-entrypoint.sh for how it takes ownership of host-mounted paths on
# first start). Used for both uid and gid.
ARG FENETRE_UID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/srv/fenetre/venv \
    PATH=/srv/fenetre/venv/bin:$PATH

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        python3 && \
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        apt-get install -y --no-install-recommends \
            intel-media-va-driver \
            libva-drm2 \
            libva2 \
            libvpl2 \
            mesa-va-drivers \
            vainfo; \
    fi && \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) go2rtc_arch="amd64"; go2rtc_sha256="32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6";; \
        arm64) go2rtc_arch="arm64"; go2rtc_sha256="359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50";; \
        arm) go2rtc_arch="arm"; go2rtc_sha256="4d7e1639af5a2722a28e864468fd8099b3c1682565446c798bf9e3b38fde12e4";; \
        *) echo "Unsupported go2rtc architecture: ${TARGETARCH:-$(dpkg --print-architecture)}" >&2; exit 1;; \
    esac && \
    curl -fsSL \
        "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${go2rtc_arch}" \
        -o /usr/local/bin/go2rtc && \
    echo "${go2rtc_sha256}  /usr/local/bin/go2rtc" | sha256sum -c - && \
    chmod 0755 /usr/local/bin/go2rtc && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g "${FENETRE_UID}" fenetre && \
    useradd -u "${FENETRE_UID}" -g "${FENETRE_UID}" -M -d /srv/fenetre -s /usr/sbin/nologin fenetre

COPY --from=builder /srv/fenetre/venv /srv/fenetre/venv
COPY --from=builder /srv/fenetre/app /srv/fenetre/app
COPY docker-entrypoint.sh /usr/local/bin/fenetre-docker-entrypoint

# Bake in ownership of the app/venv (not a live bind mount, so this is fast
# and has no bearing on host data). The container still starts as root --
# ENTRYPOINT has no USER switch -- because it needs root once at startup to
# take ownership of the host-mounted config/data/logs paths (which may
# predate this non-root image) before dropping privileges to fenetre; see
# docker-entrypoint.sh.
RUN chown -R fenetre:fenetre /srv/fenetre

WORKDIR /srv/fenetre/app

VOLUME ["/srv/fenetre/data", "/srv/fenetre/logs"]
EXPOSE 8888 8889 1984 8554 8555/tcp 8555/udp

ENTRYPOINT ["fenetre-docker-entrypoint"]
CMD ["--config", "/srv/fenetre/config.yaml"]
