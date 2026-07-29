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

ARG GO2RTC_VERSION=1.9.14
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
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
        amd64) go2rtc_arch="amd64";; \
        arm64) go2rtc_arch="arm64";; \
        arm) go2rtc_arch="arm";; \
        *) echo "Unsupported go2rtc architecture: ${TARGETARCH:-$(dpkg --print-architecture)}" >&2; exit 1;; \
    esac && \
    curl -fsSL \
        "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${go2rtc_arch}" \
        -o /usr/local/bin/go2rtc && \
    chmod 0755 /usr/local/bin/go2rtc && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /srv/fenetre/venv /srv/fenetre/venv
COPY --from=builder /srv/fenetre/app /srv/fenetre/app
COPY docker-entrypoint.sh /usr/local/bin/fenetre-docker-entrypoint

WORKDIR /srv/fenetre/app

VOLUME ["/srv/fenetre/data", "/srv/fenetre/logs"]
EXPOSE 8888 8889 1984 8554 8555/tcp 8555/udp

ENTRYPOINT ["fenetre-docker-entrypoint"]
CMD ["--config", "/srv/fenetre/config.yaml"]
