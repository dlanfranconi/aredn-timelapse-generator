# Changelog

All notable changes to Fenetre are documented in this file.

## [1.1.0] - 2026-08-22

This is the first release since 1.0.0 and folds in everything developed on
the WIP branch: admin dashboard authentication and roles, PTZ/ONVIF control,
go2rtc live view, rocket launch recording automation, and a round of
security and reliability fixes.

### Added

- Admin dashboard with HTTP basic auth, persistent config-backed users, and
  four roles (`superadmin`, `admin`, `operator`, `viewer`) with per-user PTZ
  camera assignment.
- Guided camera add/edit UI, vendor URL templates (Reolink, Sunba, generic
  RTSP), and a raw configuration editor for advanced changes.
- PTZ camera control over ONVIF: presets, nudges, tours, live aiming preview,
  and per-user PTZ locking.
- go2rtc integration for low-latency live view (WebRTC/HLS), with per-camera
  transport and playback controls and Cloudflare/multi-host stream URL
  support.
- Rocket launch workflow: scheduled launch tracking, per-camera launch
  plans, a public launch dashboard, Reolink/local RTSP launch recording, and
  a manual test-recording tool for cameras whose vendor API doesn't support
  triggering a real test recording.
- Configurable media storage location with admin-triggered migration, and
  optional automatic storage pruning with per-camera and global size caps.
- Self-service password changes, and public/private site visibility
  controls with per-camera visibility.
- Optional TLS support for the MQTT integration.
- Config change protection: saves are rejected with a clear error if the
  config file changed on the server since the page was loaded, instead of
  silently overwriting that other change.

### Changed

- The container now runs as a non-root user.
- Config backups (written on every admin save) now live under
  `work_dir/config_backups/` instead of beside `config.yaml`, so they
  actually survive a container redeploy under the standard single-file
  bind-mount setup -- previously they were silently lost on every restart.
- Browsing a camera's photos for a specific day (`/photos/<camera>/<date>/`)
  works again; a blanket directory-listing block introduced for camera-name
  privacy had also caught this one legitimate case.
- Launch recording dashboards no longer show camera recording paths, and
  in-progress recordings are excluded from launch history until complete.
- `global.storage_management.camera_max_size_GB` no longer defaults to a
  value when unset -- a deployment that already had storage management
  enabled would otherwise start pruning cameras against a limit it never
  configured.
- HTTP snapshot fetches now verify TLS certificates by default; cameras
  behind a self-signed certificate can opt out per-camera with
  `verify_ssl: false`.

### Fixed

- Admin-role accounts are now scoped to the cameras assigned to them (via
  their PTZ camera list) across every camera-control endpoint -- PTZ preset
  loading, camera add/edit/rename, Reolink and local-RTSP launch-recording
  test actions, and the manual test-recording tool -- closing several gaps
  where an admin-role account could act on cameras that were never assigned
  to them.
- `GET /config` no longer returns every camera's credentials and the full
  user list (including password hashes) to non-superadmin accounts; the
  whole-config editor (raw config tab, launch workflow tab) is now
  superadmin-only.
- A camera that goes permanently unavailable now triggers its configured
  `unavailable_command` again -- an earlier reliability change (retrying
  captures indefinitely instead of restarting the capture thread) had
  silently broken that alerting path.
- Fixed a race where a PTZ preset move could be reported "settled" before
  the camera had actually started moving, by waiting briefly before the
  first status poll.
- Fixed an RTSP URL corruption bug in the go2rtc source builder when a
  camera's password contains a literal `#`.
- Added missing credential parameter names (`api_key`, `apikey`, `secret`,
  `access_token`) to the log/URL sanitizer's redaction list.
- Fixed brute-force lockout, CSRF protection, and a `javascript:` URL XSS in
  the go2rtc live-view iframe on the admin dashboard.
- Numerous PTZ, go2rtc, camera-editor, and timelapse scheduling fixes; see
  commit history for detail.

### Security

- Restricted camera command-execution fields (`local_command`,
  `unavailable_command`, which run unsandboxed on the server) to
  superadmin accounts.
- Added camera-visibility and PTZ-lock authorization checks that were
  missing on several endpoints.
- Verified the go2rtc binary's checksum at image build time and pinned
  minimum-safe dependency versions.
