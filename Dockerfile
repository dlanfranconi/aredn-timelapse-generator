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
    pip install --no-cache-dir -e '.[gopro]' && \
    pip uninstall -y pip setuptools wheel && \
    find "$VIRTUAL_ENV" -type d -name '__pycache__' -prune -exec rm -rf '{}' + && \
    find "$VIRTUAL_ENV" -type f -name '*.py[co]' -delete

FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/srv/fenetre/venv \
    PATH=/srv/fenetre/venv/bin:$PATH

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        intel-media-va-driver \
        libgl1 \
        libglib2.0-0 \
        libva-drm2 \
        libva2 \
        libvpl2 \
        mesa-va-drivers \
        python3 \
        vainfo && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /srv/fenetre/venv /srv/fenetre/venv
COPY --from=builder /srv/fenetre/app /srv/fenetre/app

WORKDIR /srv/fenetre/app

VOLUME ["/srv/fenetre/data", "/srv/fenetre/logs"]

ENTRYPOINT ["fenetre"]
CMD ["--config", "/srv/fenetre/config.yaml"]
