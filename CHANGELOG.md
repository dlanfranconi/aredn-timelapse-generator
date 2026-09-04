# Changelog

All notable changes to aredn-timelapse-server are documented in this file.

## [2.1.2] - 2026-09-03

### Changed

- The "Previous Timelapses" dropdown once again opens the selected date
  with a single tap/selection, on every device -- no separate "Open
  timelapse" link to tap afterward. It now navigates in the same tab
  (`window.location.assign()`) instead of a new one: iOS (Safari, and
  every other iOS browser, since Apple requires them all to use WebKit)
  does not treat a `<select>` change event as a strong enough user
  gesture to authorize `window.open()`, so a new-tab request from there
  is silently blocked no matter what triggers it. Same-tab navigation
  from that event has no such restriction and is the classic
  cross-browser "jump menu" pattern, so a single tap now works
  reliably everywhere, iOS included. (The 2.1.1 changelog entry below
  blamed this on `location.assign()` itself being ignored on iOS; that
  was actually a stale-cache artifact -- see 2.1.1's third fix -- not a
  real restriction, hence this revert to the simpler one-tap flow.)

## [2.1.1] - 2026-09-03

### Fixed

- iOS Safari/Firefox: the timelapse player's native-HLS branch now sets the
  video source to the original `.m3u8` playlist URL instead of an in-memory
  `blob:` snapshot, which iOS's native HLS player silently refused to play
  (showed a crossed-out play icon with no error). The blob-snapshot approach
  is kept for the hls.js (desktop) branch. The player's status text now
  reads "Loading…" until playback actually starts (`canplay`/`loadedmetadata`)
  instead of claiming "Playing…" prematurely. (#3)
- iOS Safari/Firefox: selecting a date from a camera's "Previous Timelapses"
  dropdown now reveals a real "Open timelapse" link (`<a target="_blank">`)
  instead of navigating via script. iOS ignores both `window.open()` and
  `window.location.assign()` when triggered from a `<select>` change event,
  so navigation now happens on the link's own tap, which iOS recognizes as
  user-initiated. (#4)

## [2.1.0] - 2026-08-24

### Added

- Per-camera map privacy control in the guided admin camera editor: a
  "Show approximate location only (privacy circle)" toggle and radius
  field, so this no longer requires hand-editing `config.yaml`.
- README credit to [matfra/fenetre.cam](https://github.com/matfra/fenetre.cam),
  the project this one was originally forked from.

### Changed

- The default map privacy radius is now 1200m (was 1000m).
- The public map's cluster/marker behavior was reworked twice this
  release based on testing: cameras with a privacy circle first lost
  their pin entirely (to stop it from looking like an exact-location
  marker), then got a pin back -- pins are genuinely useful for
  spotting multiple cameras at a glance, especially on mobile -- but
  now placed at a per-camera-stable, pseudo-random point inside the
  circle instead of at its exact (already server-jittered) center.
- The public map no longer does a full rebuild of every marker/circle
  on every ~60s auto-refresh when camera positions haven't changed,
  which was likely responsible for it feeling laggy at times.
- Fenetre's own `cameras.json`/`api/cameras` responses can now be
  fetched cross-origin (CORS) from configured allowed origins, needed
  for a deployment to be linked from another site's camera map (e.g.
  fenetre.cam's `linked_deployments`).

### Fixed

- The GHCR container build workflow never actually triggered on
  `Main` due to a branch-name case mismatch (`main` vs `Main`); the
  `latest` image tag was not being built from it.

## [2.0.0] - 2026-08-23

This is the first release since 1.0.0 and folds in everything developed on
the WIP branch: admin dashboard authentication and roles, PTZ/ONVIF control,
go2rtc live view, rocket launch recording automation, and a round of
security and reliability fixes. Bumped to a new major version rather than
1.1.0 because of two breaking changes below, not just the volume of change.

**Breaking changes:**

- **The container now runs as a non-root user.** An existing deployment
  upgrading from a pre-2.0 (root-only) image will hit permission errors on
  its bind-mounted media/config directories until they're re-owned by the
  new non-root user. See "Upgrading From An Older, Root-Only Image" in
  README.md before upgrading a running deployment.
- **The admin dashboard now requires authentication.** Previously open,
  it's now gated behind HTTP Basic Auth, with an `admin`/`admin` superadmin
  account bootstrapped on first startup if no `users:` block exists in
  config. Change that password immediately after upgrading.

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
- Logging out of the admin dashboard now actually logs you out. HTTP Basic
  Auth credentials are cached by the browser itself, not by the server, so
  the previous `/logout` (which only cleared cookies/storage) left the
  browser silently re-sending the old credentials on the next request.
- The public site's login is now a modal dialog (with a close button,
  click-outside, and Escape to dismiss) instead of an inline field bar, to
  match the admin dashboard's login experience and make it clearer that
  you're being asked to log in.

### Security

- Restricted camera command-execution fields (`local_command`,
  `unavailable_command`, which run unsandboxed on the server) to
  superadmin accounts.
- Added camera-visibility and PTZ-lock authorization checks that were
  missing on several endpoints.
- Verified the go2rtc binary's checksum at image build time and pinned
  minimum-safe dependency versions.
- The public site's `/api/auth/login` now has the same per-username+IP
  brute-force lockout (10 failed attempts / 15 minutes) the admin dashboard
  login already had; previously it had no rate limiting at all.
