# fenetre

Takes pictures periodically, build timelapses, archive the footage and share it on a self-hosted website. Check it out at https://fenetre.cam or try it with your own cameras!


## Features
- Support taking pictures from:
  - Raspberry Pi camera, GoPro Hero 9+, local command or any URL)
  - GoPro Hero 9+ via Bluetooth + WiFi with https://gopro.github.io/OpenGoPro/
  - Raspberry Pi camera (tested with v2 and HQ)
  - any local command yielding an image format supported by PIL https://pillow.readthedocs.io/en/latest/reference/features.html#features-module
- Fixed interval or dynamic intervals (sunrise, sunset or fast changing landscape)
- Continuous timelapses (every 20 minutes) + daily high quality ones.
- Daylight browser to browser years of footage easily.
- Produces a fully static website, easy to self-host and put behind Cloudflare.
- Janky admin interface to help adjust picture settings
- Premetheus exporter to collect metrics for monitoring 

## Installation

This is mostly written in Python and it's been tested on Linux but it could run on MacOS and Windows too.


1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/fenetre.cam.git
    cd fenetre.cam
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install the package and its dependencies:**
    The project uses `pyproject.toml` to manage dependencies. Installing in editable mode (`-e`) is recommended for development. This command installs the `fenetre` package and the base runtime dependencies from PyPI.
    ```bash
    pip install -e .
    ```

    Optional dependencies are exposed as package extras. Install only the extras you need for the machine you are setting up:

    - `dev`: local development tools, including `pytest` and `black`.
    - `gopro`: Bluetooth and network helpers for GoPro cameras.
    - `picamera2`: Raspberry Pi camera support through `picamera2`.
    - `pyexiv2`: optional EXIF support through `pyexiv2`.
    - `ptz`: ONVIF PTZ camera control support through `onvif-zeep`.

    For a development machine, install the dev extra:
    ```bash
    pip install -e '.[dev]'
    ```

    If you plan to control GoPro cameras over Bluetooth, install the GoPro extra:
    ```bash
    pip install -e '.[gopro]'
    ```

    If you plan to control ONVIF PTZ cameras, install the PTZ extra:
    ```bash
    pip install -e '.[ptz]'
    ```

    If you plan to capture from a Raspberry Pi camera with `capture_method: picamera2`, install the Picamera2 extra:
    ```bash
    pip install -e '.[picamera2]'
    ```

    On Raspberry Pi OS, `picamera2` and `libcamera` are often best installed from Debian packages instead of PyPI. In that case, install the OS packages, create the virtual environment with `--system-site-packages`, and keep the Python install as the base package:
    ```bash
    sudo apt-get install python3-picamera2
    python3 -m venv --system-site-packages venv
    pip install -e .
    ```

    Extras can be combined in one install command:
    ```bash
    pip install -e '.[dev,gopro,picamera2,pyexiv2,ptz]'
    ```

    Production deployments should usually install only the base package unless a specific camera or workflow requires an extra. The base install does not install Raspberry Pi camera libraries.

## Usage

The application is run using the `fenetre` command, which is made available in your virtual environment after installation.

You must provide the path to a configuration file using the `--config` flag. A sample configuration is provided in `config.example.yaml`.

1.  **Copy the example configuration:**
    ```bash
    cp config.example.yaml config.yaml
    ```

2.  **Edit `config.yaml`** to match your setup (camera URLs, paths, etc.).

3.  **Run the application:**
    ```bash
    fenetre --config=config.yaml
    ```

The application will start, and based on your configuration, it will begin capturing images.

## Running with systemd

For a long-running deployment, create one systemd service per config file. The service should run the `fenetre` executable from the virtual environment, set the repository as the working directory, and pass the deployment-specific config with `--config`.

Example service for `config.fenetre-main.yaml`:

```ini
[Unit]
Description=fenetre.cam main capture service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mathieu
Group=mathieu
WorkingDirectory=/home/mathieu/fenetre-playground/fenetre.cam
ExecStart=/home/mathieu/fenetre-playground/venv/bin/fenetre --config=/home/mathieu/fenetre-playground/fenetre.cam/config.fenetre-main.yaml
Restart=always
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=45
Environment=TZ=America/Los_Angeles
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Install it as a system service:

```bash
sudo install -m 0644 fenetre-main.service /etc/systemd/system/fenetre-main.service
sudo systemctl daemon-reload
sudo systemctl enable --now fenetre-main.service
```

Check that it started correctly:

```bash
systemctl status fenetre-main.service --no-pager
journalctl -u fenetre-main.service -n 80 --no-pager
```

To run multiple deployments on the same machine, repeat the same pattern with a unique service name and config path for each deployment. For example:

```text
fenetre-sfbay.service     -> config.sfbay.yaml
fenetre-camaredn.service  -> config.camaredn.yaml
fenetre-main.service      -> config.fenetre-main.yaml
```

Make sure each config uses distinct ports and work directories before enabling multiple services.

## Running with Docker on Intel

The included `Dockerfile` builds an Ubuntu 26.04 image with Python and ffmpeg. It runs in CPU-only environments by default. The AMD64 image also includes Intel VA-API/QuickSync runtime packages for hosts that expose `/dev/dri`; the ARM64 image stays CPU-only.

Build the image:

```bash
docker build --network=host -t fenetre:intel-vaapi .
```

Run it with the host render devices, config file, and data directories mounted:

```bash
sudo mkdir -p /srv/fenetre/data /srv/fenetre/logs
sudo install -m 0664 config.aredn-example.yaml /srv/fenetre/config.yaml

docker run --rm \
  --name fenetre \
  --device /dev/dri:/dev/dri \
  -p 8888:8888 \
  -p 8889:8889 \
  -p 1984:1984 \
  -p 8554:8554 \
  -p 8555:8555 \
  -p 8555:8555/udp \
  -e TZ=America/Los_Angeles \
  -e FENETRE_PID_FILE=/tmp/fenetre.pid \
  -e FENETRE_GO2RTC=auto \
  -v /srv/fenetre/config.yaml:/srv/fenetre/config.yaml \
  -v /srv/fenetre/data:/srv/fenetre/data \
  -v /srv/fenetre/logs:/srv/fenetre/logs \
  fenetre:intel-vaapi
```

Or use compose:

```bash
docker compose up --build
```

## Portainer Stack Deployment

This is the recommended deployment path for Portainer. It uses the prebuilt
GHCR image, keeps all runtime state on the host, and lets you update the
container without losing config, snapshots, timelapses, or logs.

The GitHub Actions workflow publishes multi-arch AMD64/ARM64 images to GHCR:

```text
ghcr.io/dlanfranconi/aredn-timelapse-generator:latest-wip
```

Use persistent host paths so container upgrades do not delete config, photos,
timelapses, or logs:

```text
/srv/fenetre/config.yaml  -> /srv/fenetre/config.yaml
/srv/fenetre/data         -> /srv/fenetre/data
/srv/fenetre/logs         -> /srv/fenetre/logs
```

On the Docker host, create the directories and seed the config once:

```bash
sudo mkdir -p /srv/fenetre/data /srv/fenetre/logs
sudo install -m 0664 config.aredn-example.yaml /srv/fenetre/config.yaml
```

In Portainer:

1. Go to **Stacks**.
2. Select **Add stack**.
3. Name the stack, for example `fenetre`.
4. Paste the stack YAML below.
5. Deploy the stack.
6. Open the public UI at `http://HOST:8888/`.
7. Open the admin UI at `http://HOST:8889/` and log in.
8. On first setup, log in as `admin` / `admin`, then change the password in
   **Manage Users**.

Example Portainer stack:

```yaml
services:
  fenetre:
    image: ghcr.io/dlanfranconi/aredn-timelapse-generator:latest-wip
    container_name: fenetre
    restart: unless-stopped

    ports:
      - "8888:8888" # public, view-only site
      - "8889:8889" # admin UI, Basic Auth protected
      - "1984:1984" # go2rtc web/API for PTZ live alignment
      - "8554:8554" # go2rtc RTSP restreaming
      - "8555:8555" # go2rtc WebRTC TCP
      - "8555:8555/udp" # go2rtc WebRTC UDP

    environment:
      TZ: America/Los_Angeles
      FENETRE_PID_FILE: /tmp/fenetre.pid
      FENETRE_GO2RTC: auto

    volumes:
      - /srv/fenetre/config.yaml:/srv/fenetre/config.yaml
      - /srv/fenetre/data:/srv/fenetre/data
      - /srv/fenetre/logs:/srv/fenetre/logs

    # Optional AMD64 Intel VAAPI/QuickSync acceleration.
    # Uncomment only on hosts with /dev/dri available.
    # devices:
    #   - /dev/dri:/dev/dri
```

The config mount is intentionally read-write because the admin UI updates the
YAML file and writes timestamped backups beside it.

The admin UI on port `8889` requires HTTP Basic Auth. On first setup, Fenetre
creates a config-backed superadmin user with username `admin` and password `admin`.
This bootstrap only happens when the config has no `users:` block yet, so
upgrading the container will not restore an admin user you removed or overwrite
a password you changed.

Change the password from **Manage Users** in the admin UI after the first login.
If you lock yourself out, reset the admin user from the container CLI:

```bash
docker exec -it fenetre fenetre-user --config /srv/fenetre/config.yaml reset-admin
```

Useful checks inside the built image:

```bash
docker run --rm --device /dev/dri:/dev/dri --entrypoint vainfo fenetre:intel-vaapi
docker run --rm --entrypoint ffmpeg fenetre:intel-vaapi -hide_banner -encoders | grep -E 'vaapi|qsv'
```

When using hardware encoding, set `ffmpeg_options` in the relevant timelapse config to an encoder available on the host, such as `h264_vaapi`, `hevc_vaapi`, or a supported `*_qsv` encoder. The container provides the userspace libraries, but the host kernel driver and `/dev/dri` devices still determine what actually works.

Raspberry Pi camera deployments are better served by the systemd approach above because `picamera2`, `libcamera`, and device permissions are closely tied to Raspberry Pi OS.

## Capture cadence and storage retention

For HTTP/HTTPS snapshot cameras, use `snap_interval_s: 60` for one snapshot per minute and `activity_interval_s: 10` for fast capture when SSIM detects changes inside `ssim_area`. Sunrise/sunset windows use `sunrise_sunset.interval_s`, typically `10`.

## Camera credentials, RTSP capture, PTZ, and go2rtc

Use the admin UI at `http://HOST:8889/` for normal camera setup. The camera form has separate fields for snapshot credentials, RTSP stream URLs, and PTZ options, so credentials do not need to be embedded in the HTTP snapshot URL.

![Admin camera setup fields](docs/images/go2rtc-admin-camera.svg)

The **Template**, **Snapshot template**, and **RTSP template** dropdowns provide starting points for common cameras: Reolink, Sunba, Hikvision, Ubiquiti, Dahua, Amcrest, Axis, and TP-Link. Replace `CAMERA_IP` and `HTTP_PORT` in the generated snapshot URL with the camera address and web port. RTSP templates use `rtsp://USERNAME:PASSWORD@CAMERA_IP:554/...`; replace the placeholders, or use the snapshot username/password fields before selecting the RTSP template so Fenetre can prefill them.

For cameras that need HTTP Basic or Digest authentication for snapshots, keep the URL clean and set credentials in `http_auth`:

```yaml
cameras:
  Authenticated-Snapshot:
    url: http://camera.local/snapshot.jpg
    http_auth:
      username: admin
      password: change-me
      type: auto
```

Some embedded camera CGI endpoints reject unknown query parameters. If a snapshot URL works in a browser but the admin snapshot test fails with a 404 containing `_fenetre_test=...`, disable cache busting for that camera by unchecking `Cache bust` in the admin form or setting `cache_bust: false`.

Sunba cameras commonly use:

```yaml
url: http://CAMERA_IP:HTTP_PORT/images/snapshot.jpg
http_auth:
  type: basic
  username: admin
  password: change-me
```

If a vendor snapshot endpoint only accepts credentials as URL query parameters, use that vendor's legacy CGI template and understand that the camera password will be stored in the snapshot URL.

For RTSP-only cameras that do not support HTTP/HTTPS snapshots, set `capture_source: rtsp`. Fenetre generates a local ffmpeg one-frame snapshot command from `rtsp_url`:

```yaml
cameras:
  RTSP-Only:
    capture_source: rtsp
    rtsp_url: rtsp://admin:change-me@camera.local:554/stream1
    snap_interval_s: 60
```

If an RTSP/local-command camera logs `did not return a valid image`, the command ran but stdout was not a JPEG/PNG that Pillow could decode. Fenetre logs the command exit code, the first bytes of stdout, and the last stderr text so you can tell whether ffmpeg returned an auth error, protocol error, empty output, HTML, or another non-image response. To test the generated command manually, run an equivalent one-frame capture inside the container:

```bash
docker exec -it fenetre sh -c "ffmpeg -hide_banner -loglevel error -rtsp_transport tcp \
  -i 'rtsp://USER:PASSWORD@camera.local:554/stream1' \
  -frames:v 1 -f image2pipe -vcodec mjpeg - > /tmp/test.jpg"
```

Then confirm `/tmp/test.jpg` is a real JPEG. If the command hangs or prints an error, fix the RTSP URL, credentials, stream path, or transport before adding it back to Fenetre.

`rtsp_url` and `ptz_rtsp_url` are used for different workflows:

- `rtsp_url`: the stream Fenetre uses when the camera is RTSP-only and has no HTTP/HTTPS snapshot endpoint. This same stream is also the go2rtc live-view fallback.
- `ptz_rtsp_url`: an optional stream used only for PTZ alignment/live view on snapshot-based cameras, or when the camera has a lower-latency/lower-resolution substream that is better for aiming. If it is empty, go2rtc falls back to `rtsp_url`.

For an RTSP-only camera, set only `rtsp_url`; do not duplicate the same URL into `ptz_rtsp_url`. RTSP video URLs should normally use the camera's RTSP service, usually port `554`, for example `rtsp://user:password@camera.local:554/stream1`. For a snapshot camera with PTZ controls, keep the snapshot URL in `url` and set `ptz_rtsp_url` only if you want a live alignment view.

For PTZ alignment, the Docker image includes go2rtc and starts it automatically when all of these are true:

- `global.go2rtc.enabled: true`
- at least one camera has `rtsp_url` or `ptz_rtsp_url`
- `FENETRE_GO2RTC` is unset, `auto`, `on`, `true`, or `1`

The Docker image also includes the `ptz` Python extra, so ONVIF PTZ control is available without a separate dependency install. If you run Fenetre outside Docker, install `pip install -e '.[ptz]'` for ONVIF controls.

The generated go2rtc config is written inside the container at `/tmp/fenetre-go2rtc.yaml`. The public `cameras.json` exposes only the generated go2rtc stream name and player URL, not the raw RTSP URL.

```yaml
global:
  go2rtc:
    enabled: true
    base_url: http://HOST:1984
    player_url_template: "{base_url}/webrtc.html?src={stream}"
    stream_name_prefix: fenetre_
    api_listen: ":1984"
    rtsp_listen: ":8554"
    webrtc_listen: ":8555"
    # Set this when WebRTC works poorly or fails from browsers outside the container network.
    # Use the host/IP and port that browser clients can reach.
    webrtc_candidates:
      - HOST:8555
    live_view_idle_timeout_s: 60

cameras:
  Ridge-PTZ:
    url: http://ridge-camera.local/snapshot.jpg
    rtsp_url: rtsp://admin:password@ridge-camera.local:554/stream1
    ptz_rtsp_url: rtsp://admin:password@ridge-camera.local:554/stream2
    ptz:
      enabled: true
      allow_manual_control: true
```

RTSP/go2rtc live view and ONVIF PTZ are separate camera services. RTSP should use an `rtsp://...` URL, normally on port `554`; do not put an ONVIF/PTZ port such as `8899` in the RTSP URL unless the camera documentation explicitly says RTSP is served there. A camera can stream correctly over RTSP while PTZ fails if `ptz.host` or `ptz.port` points at the wrong ONVIF endpoint. The ONVIF port is often `80`, `8000`, `8080`, or `8899`, but it is camera/vendor dependent; it is not necessarily the RTSP port and may not be the same as the camera's web UI or CGI PTZ port. If PTZ returns a connection refused error such as `/onvif/Media` on `10.1.64.69:8899`, enable ONVIF in the camera settings and change the configured ONVIF port to the port where the camera exposes ONVIF.

The default stream name is `fenetre_` plus the camera name with unsafe characters replaced by `_`. For example, `Ridge-PTZ` becomes `fenetre_Ridge-PTZ`.

The default go2rtc player is WebRTC: `{base_url}/webrtc.html?src={stream}`. If WebRTC fails in the go2rtc portal but MSE or HLS works, the RTSP source is healthy and the issue is usually WebRTC candidate/connectivity. Set `global.go2rtc.webrtc_candidates` to the hostname or IP and port that browsers can reach, such as `camera-host.local.mesh:8555` or `10.218.x.x:8555`, and make sure TCP and UDP `8555` are published. If you intentionally prefer MSE for compatibility, set:

```yaml
global:
  go2rtc:
    player_url_template: "{base_url}/stream.html?src={stream}"
```

Expose the go2rtc ports in Docker or Compose:

```yaml
ports:
  - "8888:8888"      # public Fenetre UI
  - "8889:8889"      # admin UI
  - "1984:1984"      # go2rtc web/API
  - "8554:8554"      # go2rtc RTSP restreaming
  - "8555:8555"      # go2rtc WebRTC TCP
  - "8555:8555/udp"  # go2rtc WebRTC UDP

environment:
  TZ: America/Los_Angeles
  FENETRE_PID_FILE: /tmp/fenetre.pid
  FENETRE_GO2RTC: auto
```

`FENETRE_GO2RTC` controls the bundled go2rtc process:

- `auto`: start go2rtc only when the Fenetre config enables it and has RTSP streams.
- `off`: never start go2rtc.
- `on`: require go2rtc; fail container startup if config generation fails.

When the container starts correctly, logs include:

```text
Starting go2rtc with generated config /tmp/fenetre-go2rtc.yaml
```

If `http://HOST:1984/` gives `connection refused`, go2rtc did not start or the port is not published. Check the container logs first:

```bash
docker logs fenetre | grep -i go2rtc
```

In `auto` mode, this message means go2rtc intentionally stayed off because the mounted `/srv/fenetre/config.yaml` did not enable it or did not have any RTSP streams:

```text
go2rtc not started; set global.go2rtc.enabled and at least one camera rtsp_url or ptz_rtsp_url to enable it
```

After editing `global.go2rtc` or camera RTSP fields, restart the container so the bundled go2rtc config is regenerated.

You can inspect the generated go2rtc config:

```bash
docker exec -it fenetre cat /tmp/fenetre-go2rtc.yaml
```

And open the go2rtc UI:

```text
http://HOST:1984/
```

On the public Fenetre page, normal viewers remain view-only. Pressing `Login` opens a browser-native Basic Auth prompt, like the admin page. After a successful login, the page stores a short-lived public session token, reloads automatically, and shows manual PTZ controls only for configured PTZ cameras the user may control. The go2rtc live view is loaded lazily when a manual PTZ control is pressed, so normal page loads do not keep RTSP streams open. The embedded alignment view is unloaded after `global.go2rtc.live_view_idle_timeout_s` seconds of PTZ inactivity, defaulting to 60 seconds, so idle WebRTC clients do not keep RTSP streams open.

PTZ user roles are:

- `superadmin`: can manage users and control every PTZ camera.
- `admin`: can open the admin UI, but PTZ control is limited to the cameras and PTZ access level assigned by a superadmin.
- `operator`: intended for public-page PTZ operation on assigned cameras.
- `viewer`: view-only unless explicitly given PTZ access.

For non-superadmin users, the **Manage Users** camera access table controls exactly which cameras they can move or send to presets. Manual movement also requires the camera's **Allow manual movement** PTZ option; otherwise the public page returns "Manual PTZ control is disabled for this camera" even if the user is logged in.

![Public PTZ live alignment controls](docs/images/go2rtc-public-ptz.svg)

Storage management is configured under `global.storage_management`. Set `work_dir_max_size_GB: 50` for the full deployment and `camera_max_size_GB: 5` for the default per-camera cap. When `prune_snapshots_first: true`, Fenetre removes old snapshots and rolling timelapse artifacts from days that already have a daily timelapse before trimming old daily timelapse files.

## Recommended video encoding options

Timelapse encoding is controlled per deployment in `config.yaml` under `timelapse.daily_timelapse.ffmpeg_options` and `timelapse.frequent_timelapse.ffmpeg_options`.

The frequent timelapse usually benefits most from hardware encoding because it runs repeatedly and is normally viewed as HLS. The daily timelapse can use slower CPU encoding if quality or compression matters more than encode time.

Good default CPU options:

```yaml
timelapse:
  frequent_timelapse:
    ffmpeg_2pass: false
    framerate: 30
    max_width: 1280
    max_height: 720
    ffmpeg_options: -c:v libx264 -preset veryfast -crf 26 -movflags +faststart
    file_extension: mp4
    output_format: hls
  daily_timelapse:
    framerate: 60
    max_width: 1920
    max_height: 1080
```

For low-bandwidth deployments, keep the frequent timelapse at 720p HLS and the daily archive at 1080p MP4 unless you have confirmed the network and CPU can handle more. Per-camera timelapse generation can be disabled with `timelapse_enabled: false`; the legacy `generate_timelapse: false` key is also honored.

Recommended Intel VAAPI options:

```yaml
timelapse:
  frequent_timelapse:
    ffmpeg_2pass: false
    ffmpeg_options: -vaapi_device /dev/dri/renderD128 -vf format=nv12,hwupload -c:v h264_vaapi -qp 24 -movflags +faststart
    file_extension: mp4
    output_format: hls
```

The service user must be able to open the render device. For a systemd deployment, add a drop-in like this:

```ini
[Service]
SupplementaryGroups=render video
```

Then reload and restart the service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart fenetre.service
```

For Docker deployments, pass the render device through to the container:

```bash
docker run --device /dev/dri:/dev/dri ...
```

or in compose:

```yaml
services:
  fenetre:
    devices:
      - /dev/dri:/dev/dri
```

Useful host checks:

```bash
ffmpeg -hide_banner -encoders | grep -E 'h264_vaapi|hevc_vaapi|h264_qsv|h264_v4l2m2m|libx264'
ls -l /dev/dri
```

Validate VAAPI before enabling it in a production config:

```bash
ffmpeg -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=640x360:rate=30 \
  -frames:v 30 \
  -vaapi_device /dev/dri/renderD128 \
  -vf format=nv12,hwupload \
  -c:v h264_vaapi -qp 24 \
  -f mp4 -y /tmp/fenetre-vaapi-test.mp4
```

If that command fails with a render-device permission error, fix the service user or container device access. If it fails with a VAAPI device or driver error, use CPU `libx264` until the host graphics driver stack is fixed.

Quick Sync (`h264_qsv`) can be faster on some Intel systems, but it is more sensitive to driver and ffmpeg build details. Prefer VAAPI on Linux unless QSV has been tested on the exact host:

```yaml
ffmpeg_options: -c:v h264_qsv -global_quality 24 -look_ahead 0 -movflags +faststart
```

Raspberry Pi deployments can use the V4L2 mem2mem encoder when available:

```yaml
ffmpeg_options: -c:v h264_v4l2m2m -b:v 5M
```

For high-quality daily archives, VP9 CPU encoding is still reasonable when encode time is acceptable:

```yaml
timelapse:
  daily_timelapse:
    ffmpeg_2pass: true
    ffmpeg_options: -c:v libvpx-vp9 -b:v 7M
    file_extension: webm
```

### GoPro

On the first run:
- Put the GoPro in Pairing mode (Menu connections wireless Quic)
- Open bluetoothctl and locate the Mac address of the GoPro (use `scan le` if it's not already showing) then type `trust <MAC_ADDR>` and `pair <MAC_ADDR>`. You can then exit bluetoothctl with `quit`. Remeber to `scan off` if you had to turn ont he scan.
- In the app logs, you should see the Wi-Fi SSID and the password to connect to the GoPro. You may want to configure your system (netplan, wpa_supplicant ...) to autoconnect to the GoPro.

**By default, the admin server runs on `http://0.0.0.0:8889`.**
