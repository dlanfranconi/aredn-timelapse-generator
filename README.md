# Fenetre AREDN Camera Server

Fenetre is a self-hosted camera server for AREDN and other IP-camera networks. It captures still images from HTTP snapshot endpoints or RTSP streams, builds rolling and daily timelapses, serves a public or private camera dashboard, and can optionally run ONVIF PTZ and rocket-launch capture workflows.

The current deployment path is Docker or Portainer with persistent host-mounted config, data, and logs. The legacy GoPro and Raspberry Pi capture backends are still available as optional extras, but they are no longer the primary documented setup.

## What It Does

- Captures still images from HTTP/HTTPS snapshot URLs, local commands, or RTSP streams through ffmpeg one-frame commands.
- Builds frequent HLS timelapses and daily archive timelapses.
- Serves the camera dashboard on `:8888` and the admin/config dashboard on `:8889`.
- Supports public or private site access, per-camera visibility, and local users.
- Supports roles: `superadmin`, `admin`, `operator`, and `viewer`.
- Supports ONVIF PTZ presets, manual nudges, zoom-only cameras, per-user PTZ camera access, tour pause/resume, and editable preset names.
- Starts bundled go2rtc for RTSP live views when enabled.
- Keeps go2rtc playback video-only and muted by default.
- Supports optional image-profile hooks for vendor camera tuning.
- Supports optional rocket-launch automation with schedule polling, camera preset moves, tour pause/resume, recording hooks, download hooks, and launch dashboards.

## Ports

| Port | Purpose |
| --- | --- |
| `8888` | Public/private camera dashboard and API |
| `8889` | Admin/config dashboard |
| `1984` | go2rtc web UI/API |
| `8554` | go2rtc RTSP restreaming |
| `8555/tcp` and `8555/udp` | go2rtc WebRTC |

Expose `8888` as the viewer-facing site. Do not expose a separate static nginx server directly over `/srv/fenetre/data`; that bypasses private-site login and per-camera visibility controls.

## Quick Start With Docker

Build locally:

```bash
docker build --network=host -t fenetre:local .
```

Seed persistent storage once:

```bash
sudo mkdir -p /srv/fenetre/data /srv/fenetre/logs
sudo install -m 0664 config.aredn-example.yaml /srv/fenetre/config.yaml
```

Run:

```bash
docker run --rm \
  --name fenetre \
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
  fenetre:local
```

Open:

- Camera dashboard: `http://HOST:8888/`
- Admin dashboard: `http://HOST:8889/`
- go2rtc dashboard: `http://HOST:1984/`

On first startup, Fenetre creates `admin` / `admin` as a `superadmin` only if the config has no `users:` block. Change that password immediately.

## Portainer Stack

Use the prebuilt GHCR image:

```text
ghcr.io/dlanfranconi/aredn-timelapse-generator:latest-wip
```

Recommended stack:

```yaml
services:
  fenetre:
    image: ghcr.io/dlanfranconi/aredn-timelapse-generator:latest-wip
    container_name: fenetre
    restart: unless-stopped

    ports:
      - "8888:8888"
      - "8889:8889"
      - "1984:1984"
      - "8554:8554"
      - "8555:8555"
      - "8555:8555/udp"

    environment:
      TZ: America/Los_Angeles
      FENETRE_PID_FILE: /tmp/fenetre.pid
      FENETRE_GO2RTC: auto

    volumes:
      - /srv/fenetre/config.yaml:/srv/fenetre/config.yaml
      - /srv/fenetre/data:/srv/fenetre/data
      - /srv/fenetre/logs:/srv/fenetre/logs

    # Optional AMD64 Intel VAAPI/QuickSync acceleration.
    # devices:
    #   - /dev/dri:/dev/dri
```

The config mount must be read-write. The admin UI writes camera settings, users, site settings, launch settings, and timestamped backups beside the YAML file.

## Persistence And Upgrades

Keep these host paths persistent:

```text
/srv/fenetre/config.yaml  -> /srv/fenetre/config.yaml
/srv/fenetre/data         -> /srv/fenetre/data
/srv/fenetre/logs         -> /srv/fenetre/logs
```

The `users:` block lives in `config.yaml`. Container rebuilds and upgrades do not reset users, password hashes, roles, PTZ access levels, or assigned PTZ camera lists as long as the same mounted config file is kept. Generic config saves preserve existing users even if an older browser tab submits stale or empty user data.

Deleted cameras are automatically removed from every user's PTZ camera list during config saves.

If you lock yourself out, reset the default admin user from the container:

```bash
docker exec -it fenetre fenetre-user --config /srv/fenetre/config.yaml reset-admin
```

## Users And Roles

- `superadmin`: full admin access, can edit users, set other users' passwords, assign PTZ cameras, and control every PTZ camera.
- `admin`: admin dashboard access and camera control only for cameras assigned by a superadmin.
- `operator`: public dashboard access plus assigned PTZ control; no admin dashboard.
- `viewer`: view-only public dashboard access; no admin dashboard.

Public-dashboard users can change their own password from the callsign/account menu in the top-right corner. Admin-dashboard users can also change their own password from the admin menu. Only a `superadmin` can set another user's password.

Browsers are told not to save login and camera-secret fields by default. Some browsers may still offer password management, but the form hints are set to avoid automatic saving.

## Website Access And Camera Visibility

Set website access under `global.ui.public_site` or from the admin Site panel:

```yaml
global:
  ui:
    public_site: true
```

- `true`: anonymous visitors can view public cameras on `:8888`.
- `false`: `:8888` shows a login landing page with the deployment name before camera data is served.

Each camera can be independently visible:

```yaml
cameras:
  Ridge-PTZ:
    visibility: public
  Maintenance-Camera:
    visibility: hidden
```

- `public`: shown to everyone when the site is public.
- `authenticated`: hidden from anonymous visitors, shown after login.
- `hidden`: kept in config/admin but removed from the main camera page.

Older configs with `public: false` are treated as `visibility: authenticated`.

## Camera Capture

Use the admin dashboard at `http://HOST:8889/` for normal camera add/edit work. The guided camera form has separate fields for snapshot URL, snapshot credentials, RTSP URLs, PTZ settings, presets, tours, and launch-camera settings. Saving a camera writes `config.yaml`, syncs public UI assets, rebuilds camera metadata, syncs go2rtc streams, and asks the running app to reload.

HTTP snapshot camera:

```yaml
cameras:
  Snapshot-Camera:
    url: http://camera.local/images/snapshot.jpg
    http_auth:
      username: admin
      password: CHANGE_ME
      type: basic
    snap_interval_s: 60
    activity_interval_s: 10
    timeout_s: 15
```

Use `type: basic` or `type: digest`. Some cameras reject unknown cache-busting query parameters; if a browser URL works but the admin test fails with `_fenetre_test=...`, set `cache_bust: false`.

Sunba snapshot cameras commonly use:

```yaml
url: http://CAMERA_IP:HTTP_PORT/images/snapshot.jpg
http_auth:
  type: basic
  username: admin
  password: CHANGE_ME
```

RTSP-only camera:

```yaml
cameras:
  RTSP-Only:
    rtsp_url: rtsp://admin:CHANGE_ME@camera.local:554/stream1
    local_command: >-
      ffmpeg -hide_banner -loglevel error -rtsp_transport tcp
      -allowed_media_types video
      -i rtsp://admin:CHANGE_ME@camera.local:554/stream1
      -an -map 0:v:0 -frames:v 1 -f image2pipe -vcodec mjpeg -
    snap_interval_s: 60
    capture_failure_interval_s: 180
```

`capture_source: rtsp` is used by the admin form when saving a camera. In YAML, the persistent RTSP capture fields are `rtsp_url` and the generated `local_command`.

Fenetre-generated RTSP snapshot commands are video-only. That avoids negotiating audio tracks on cameras where audio probing is unstable.

If capture fails after at least one good frame, Fenetre marks the camera offline, waits `capture_failure_interval_s`, and retries. For fragile legacy cameras that reboot after failed RTSP sessions, raise this interval or disable the camera while troubleshooting.

## go2rtc Live Views

go2rtc starts automatically when:

- `global.go2rtc.enabled: true`
- at least one camera has `rtsp_url` or `ptz_rtsp_url`
- `FENETRE_GO2RTC` is `auto`, `on`, `true`, or unset

Recommended defaults:

```yaml
global:
  go2rtc:
    enabled: true
    # Leave base_url blank when local/offline users should use the current host on port 1984.
    base_url: ""
    # Optional exact browser-host overrides. Use this for Cloudflare/Tunnel hostnames.
    base_urls:
      aredncameras.aredn805.net: https://stream.aredn805.net
    player_url_template: "{base_url}/stream.html?src={stream}&media=video&muted=1"
    preview_url_template: "{base_url}/stream.html?src={stream}&media=video&muted=1"
    # Leave blank to let go2rtc stream.html choose its default mode.
    player_mode: ""
    preview_mode: ""
    stream_name_prefix: fenetre_
    source_mode: ffmpeg
    video_mode: copy
    rtsp_timeout_s: 30
    rtsp_transport: tcp
    api_listen: ":1984"
    rtsp_listen: ":8554"
    webrtc_listen: ":8555"
    webrtc_candidates: []
    live_view_idle_timeout_s: 60
```

The default full player is `stream.html`, not `webrtc.html`, because it works better across routed mesh networks. Fenetre also forces generated player URLs to `media=video&muted=1` so RTSP streams open muted. Leave `player_mode` and `preview_mode` blank to let go2rtc choose its default `stream.html` playback mode. If you need to test a specific mode order, set values such as `mse`, `mp4,mse`, or `hls,mp4,mse`; keep that explicit because some browsers and network paths fail when WebRTC is forced. The PTZ aiming preview uses the low-resolution go2rtc stream, not the `_full` stream.

`source_mode: ffmpeg` makes go2rtc start FFmpeg as the camera-side RTSP client and copy the video stream without transcoding; this is more stable for cameras that play directly but freeze in go2rtc. Set `source_mode: rtsp` globally, or `go2rtc_source_mode: rtsp` on one camera, to use go2rtc's direct RTSP client instead. On lossy point-to-point paths, keep `source_mode: ffmpeg` and set `rtsp_transport: udp` globally or `go2rtc_rtsp_transport: udp` for one camera. That generates `#input=rtsp/udp`, which can keep live video moving through packet loss at the cost of occasional visual corruption. If a substream opens as a black box, try `go2rtc_video_mode: h264` on that camera; it transcodes and uses more CPU, so use it only where needed.

If `base_url` is blank, the public UI builds stream links from the current browser host and the configured go2rtc API port, for example `http://CURRENT_HOST:1984/stream.html?...`. Use `base_urls` for host-specific exceptions such as Cloudflare Tunnel hostnames. The key is the browser host for Fenetre, and the value is the go2rtc browser base URL. Set `base_url` only as a default for every browser host that does not match `base_urls`.

For Cloudflare, use first-level stream hostnames such as `stream.aredn805.net`; multi-level names such as `streams.aredncameras.aredn805.net` are not covered by Cloudflare Universal SSL unless you add Total TLS, Advanced Certificate Manager, or a custom edge certificate.

`rtsp_url` and `ptz_rtsp_url` have different jobs:

- `rtsp_url`: primary RTSP stream, used for full live view and RTSP still capture.
- `ptz_rtsp_url`: optional low-resolution or low-latency PTZ alignment stream.

If both are set and differ, Fenetre creates an alignment stream and a separate `_full` stream. If `ptz_rtsp_url` is empty, PTZ alignment falls back to `rtsp_url`. While an authenticated browser is actively watching a go2rtc stream, Fenetre defers RTSP still captures for that camera so the snapshot loop does not compete with the live stream.

Disable `go2rtc_enabled` per camera if a camera should capture stills only:

```yaml
cameras:
  Fragile-Legacy-Camera:
    go2rtc_enabled: false
```

Override the go2rtc source mode for one camera if needed:

```yaml
cameras:
  Lossy-Link-Camera:
    go2rtc_source_mode: ffmpeg
    go2rtc_rtsp_transport: udp
    go2rtc_rtsp_timeout_s: 45
    # Optional only when the substream is black or browser-incompatible.
    go2rtc_video_mode: h264
```

Inspect the generated config:

```bash
docker exec -it fenetre cat /tmp/fenetre-go2rtc.yaml
```

If `http://HOST:1984/stream.html?src=fenetre_CAMERA` works but the Fenetre page does not, open `/api/go2rtc/status` as an admin and confirm `runtime_enabled`, `api_reachable`, and either `base_url_configured` or `same_host_fallback_enabled` are true.

## PTZ

RTSP live view and ONVIF PTZ are separate services. RTSP usually uses port `554`. ONVIF commonly uses `80`, `8000`, `8080`, or `8899`, depending on the camera. Do not put the ONVIF port into the RTSP URL unless the camera documentation explicitly says RTSP is served there.

Basic ONVIF PTZ:

```yaml
cameras:
  Ridge-PTZ:
    url: http://ridge-camera.local/images/snapshot.jpg
    rtsp_url: rtsp://admin:CHANGE_ME@ridge-camera.local:554/main
    ptz_rtsp_url: rtsp://admin:CHANGE_ME@ridge-camera.local:554/sub
    ptz:
      enabled: true
      host: ridge-camera.local
      port: 80
      username: admin
      password: CHANGE_ME
      allow_presets: true
      allow_manual_control: true
      access_level: presets
      capabilities:
        pan: true
        tilt: true
        zoom: true
      presets:
        - id: launch-pad
          token: "1"
          name: Launch Pad
```

Preset rows are editable in the admin camera editor. `Load ONVIF Presets` imports named presets from the camera. Unnamed presets and numeric-only presets with no real name are treated as untaught and hidden.

If a camera only supports zoom, disable pan and tilt:

```yaml
ptz:
  capabilities:
    pan: false
    tilt: false
    zoom: true
```

Some cameras handle short relative nudges better than continuous move plus stop:

```yaml
ptz:
  move_mode: relative
  relative_move_scale: 0.05
  disable_stop: true
```

Tour controls are configured per camera:

```yaml
ptz:
  tour:
    enabled: true
    auto_resume_s: 1800
```

When enabled, authorized manual-PTZ users see context-aware Start Tour, Stop Tour, Pause Tour, and Resume Tour controls. Manual movement, focus movement, and preset moves mark the runtime tour state as paused, and the public controls switch to Resume Tour. `auto_resume_s: 1800` resumes the tour 30 minutes after an explicit tour pause or manual control takeover. Set it to `0` to disable auto-resume.

For legacy cameras that crash on ONVIF or RTSP sessions, keep the camera as snapshot-only or RTSP-capture-only and set `ptz.enabled: false` and `go2rtc_enabled: false`. That keeps the deployment clean without adding vendor-specific crash workarounds to the normal path.

## Image Profiles

Image profiles are optional HTTP/API hooks for camera image tuning. They are not RTSP stream profiles. Use them for day, sunset, night, or launch-specific camera settings such as exposure, brightness, contrast, IR/white light, WDR, or other vendor image controls.

```yaml
cameras:
  Reolink-PTZ:
    image_profiles:
      enabled: true
      vendor: reolink
      host: reolink.local
      http_port: 80
      channel: 0
      username: admin
      password: CHANGE_ME
      mode_profiles:
        sunrise: sunrise
        sunset: sunset
        night: night
      profiles:
        launch:
          actions:
            - name: launch-exposure
              method: POST
              url: http://{host}/api/launch-image
              json:
                mode: launch
```

Dry-run an image profile from the admin API:

```bash
curl -u admin:password -X POST http://HOST:8889/api/camera/image_profile \
  -H 'Content-Type: application/json' \
  -d '{"camera":"Reolink-PTZ","profile":"launch","dry_run":true}'
```

## Rocket Launch Timer

Rocket-launch automation is optional and disabled by default. When disabled, Fenetre behaves like a normal camera/timelapse server and the launch dashboard link is hidden on the public page.

Enable it only on deployments where launches matter:

```yaml
global:
  launch_workflow:
    enabled: true
    dry_run: true
```

Keep `dry_run: true` until the preview shows the right launches, cameras, presets, and hooks.

### How Launch Times Are Known

Fenetre reads launch schedules from one of three sources:

- `schedule_events`: inline events in `config.yaml`, useful for testing.
- `schedule_file`: local JSON file.
- `schedule_url`: remote JSON endpoint.

The recommended remote source is Launch Library 2.3.0 upcoming launches:

```yaml
schedule_url: https://ll.thespacedevs.com/2.3.0/launches/upcoming/?format=json&limit=100&ordering=net
refresh_interval_s: 300
lookahead_hours: 168
```

Launch Library responses are normalized from fields such as `net`, launch provider, pad, location, mission name, and status.

### Selecting Launch Locations

Plans filter normalized events. For Vandenberg-only SpaceX launches:

```yaml
plans:
  vandenberg-spacex:
    enabled: true
    match:
      providers: [SpaceX]
      locations: [Vandenberg]
      pads: []
      names: []
      statuses: []
      keywords: []
```

Use `locations`, `pads`, `providers`, `names`, `statuses`, and `keywords` to include or exclude launch sources. A deployment in Florida can use a different plan; a deployment with no launch use case should leave `enabled: false`.

### Launch Phases And Actions

For each matched launch, Fenetre computes:

- `pending`: before `launch_time - pre_seconds`
- `prelaunch`: from `launch_time - pre_seconds` until launch time
- `recording`: from launch time through `launch_time + post_seconds`
- `complete`: after the post window

Due actions are scheduled once per launch/camera/action:

- `pause_tour` at `launch_time - pre_seconds`
- `image_profile` at `launch_time - pre_seconds`
- `goto_preset` at `launch_time - pre_seconds`
- `record_start` at `launch_time - pre_seconds`
- `record_stop` at `launch_time + post_seconds`
- `download_recording` after `record_stop + download_delay_seconds`
- `resume_tour` at `launch_time + post_seconds`

When `dry_run: false`, completed action keys are persisted to `state_file` so restarting the container does not repeat already-completed launch actions.

### Launch Camera Example

Launch cameras can be PTZ or non-PTZ. Non-PTZ cameras can still record or apply image profiles, but Fenetre only shows preset and tour options for cameras with `ptz.enabled: true`.

```yaml
global:
  launch_workflow:
    enabled: true
    dry_run: true
    schedule_url: https://ll.thespacedevs.com/2.3.0/launches/upcoming/?format=json&limit=100&ordering=net
    refresh_interval_s: 300
    lookahead_hours: 168
    default_pre_seconds: 60
    default_post_seconds: 900
    state_file: /srv/fenetre/data/launch_workflow_state.json
    plans:
      vandenberg-spacex:
        enabled: true
        match:
          providers: [SpaceX]
          locations: [Vandenberg]
        pre_seconds: 60
        post_seconds: 900
        cameras:
          Ridge-PTZ:
            pause_tour: true
            resume_tour: true
            preset: launch-pad
            image_profile: launch
            record:
              start_url: http://{host}/api/record/start?event={launch_id}
              stop_url: http://{host}/api/record/stop?event={launch_id}
              download_url: http://{host}/api/record/download?event={launch_id}
              download_path: /srv/fenetre/data/launches/{launch_id}-{camera}.mp4
              download_delay_seconds: 60
              skip_when_full_viewers: true
```

Hook URL and command templates can use:

```text
{camera} {plan} {launch_id} {launch_name} {launch_time_utc}
{record_start_utc} {record_stop_utc} {record_start_local} {record_stop_local}
{provider} {location} {pad} {host} {ip} {http_port} {port} {channel}
{username} {password}
{active_full_viewers}
```

`skip_when_full_viewers: true` skips custom recording/download hooks when an authenticated user is actively watching the camera's full HD stream. For the built-in Reolink recorder it skips the download action only, so a live viewer does not block the camera from recording the launch locally.

### Reolink Launch Recording

For Reolink cameras such as the RLC-811A, choose `Recording API: Reolink camera API` in the Launch Automation admin panel, or set `record.vendor: reolink` in YAML. Fenetre infers host, channel, and credentials from the camera URL, RTSP URL, `http_auth`, `ptz`, or `image_profiles` where possible.

```yaml
cameras:
  RLC811A-Launch:
    url: http://192.0.2.60/cgi-bin/api.cgi?cmd=Snap&channel=0&rs=fenetre&user=admin&password=CHANGE_ME
    http_auth:
      username: admin
      password: CHANGE_ME
    rtsp_url: rtsp://admin:CHANGE_ME@192.0.2.60:554/h264Preview_01_main
    go2rtc_enabled: true

global:
  launch_workflow:
    plans:
      vandenberg-spacex:
        cameras:
          RLC811A-Launch:
            image_profile: launch
            record:
              vendor: reolink
              http_port: 80
              channel: 0
              stream_type: main
              manual_record_duration_s: 1200
              download_method: Download
              download_path: /srv/fenetre/data/launches/{launch_id}/{launch_id}-{camera}.mp4
              download_delay_seconds: 60
              skip_when_full_viewers: true
```

Built-in Reolink recording uses direct HTTP API calls:

- Start recording: `SetManualRec` with `{"Rec":{"channel":0,"enable":1,"duration":1200}}`
- Stop recording: `SetManualRec` with `{"Rec":{"channel":0,"enable":0}}`
- Find files: `Search` for the launch record window and selected `stream_type`
- Download files: `Download` or `Playback` using the file names returned by `Search`

Reolink's official HTTP API documentation covers `Search`, `Download`, and `Playback`; `SetManualRec` is implemented by the maintained `reolink_aio` integration and is capability-dependent. If a camera/NVR returns a Reolink API error for `SetManualRec`, leave the recorder in custom-hook mode or use an external RTSP recorder.

Fenetre uses the camera-local time window derived from `global.timezone` for Reolink `Search`. Keep `global.timezone` aligned with the camera's local timezone.

For Sunba P636 V2, no verified public local HTTP API for start/stop/download of on-camera recordings has been found. Keep `record.vendor` as custom hooks and only enter URLs/commands that you have tested against that camera or its management software.

### Launch Dashboards

Admin launch dashboard:

```text
http://HOST:8889/static/admin/launches.html
```

Public launch dashboard:

```text
http://HOST:8888/launches.html
```

The public launch dashboard is linked from the main camera page only when launch automation is enabled. It shows upcoming matched launch times, matching plans, relevant launch cameras, presets, image profiles, recording status, and configured download paths. It also lists past launch recordings saved under `work_dir/launches`, filtered by the same camera visibility rules as the main page.

Past launch recordings follow the normal global storage policy. They count toward `global.storage_management.work_dir_max_size_GB`; if the global work directory is still over limit after normal camera pruning, Fenetre trims the oldest launch recording folders/files while preserving current-day launch recordings.

Use the admin Launch Automation panel to preview schedules before disabling dry-run.

## Config Files In This Repo

- `config.aredn-example.yaml`: current Docker/Portainer template for AREDN/IP camera deployments.
- `config.smaller.local.yaml`: local ffmpeg test-source config for quick development checks.
- `docker-compose.yaml`: local build compose file.
- `docker-compose.ghcr.yml`: compose file that pulls the GHCR image.
- `docker-compose.portainer.yml`: Portainer-friendly compose file.

Old personal deployment configs and static nginx configs from the original fork were removed because they no longer match the authenticated camera server.

## Development

Create a venv and install the package:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e '.[dev,ptz]'
```

Run the server from a config file:

```bash
venv/bin/fenetre --config=config.aredn-example.yaml
```

Run tests:

```bash
venv/bin/pytest
```

Optional extras:

- `ptz`: ONVIF PTZ support.
- `gopro`: legacy GoPro support.
- `picamera2`: legacy Raspberry Pi camera support.
- `pyexiv2`: optional EXIF support.

## Troubleshooting

Check container logs:

```bash
docker logs fenetre
```

Check go2rtc startup:

```bash
docker logs fenetre | grep -i go2rtc
```

Test a one-frame RTSP capture inside the container:

```bash
docker exec -it fenetre sh -c "ffmpeg -hide_banner -loglevel error -rtsp_transport tcp \
  -allowed_media_types video \
  -i 'rtsp://USER:PASSWORD@camera.local:554/stream1' \
  -an -map 0:v:0 -frames:v 1 -f image2pipe -vcodec mjpeg - > /tmp/test.jpg"
```

Inspect go2rtc streams:

```text
http://HOST:1984/
```

If direct camera RTSP playback is stable but go2rtc pauses or buffers after a few seconds, inspect `/tmp/fenetre-go2rtc.yaml`. Current Fenetre defaults should generate stream sources like `ffmpeg:rtsp://...#video=copy#timeout=30`. On lossy mesh paths, test `go2rtc_rtsp_transport: udp` for the affected camera; the generated source should become `ffmpeg:rtsp://...#video=copy#input=rtsp/udp#timeout=30`. If the low-resolution aiming stream is black, test `go2rtc_video_mode: h264` on that camera. If a source still starts with plain `rtsp://`, reload/save the camera settings so the go2rtc runtime is synced, or set `global.go2rtc.source_mode: ffmpeg`.

If ONVIF PTZ fails while RTSP works, test the ONVIF host and port separately from the RTSP URL. A `405 Method Not Allowed` response to a plain browser or curl GET on `/onvif/device_service` can still mean the ONVIF service is present, because ONVIF expects SOAP POST requests.
