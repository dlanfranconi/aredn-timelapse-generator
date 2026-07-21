# Fenetre Backlog

This file tracks current follow-up work for the AREDN/IP camera server fork. Old original-fork notes for static nginx hosting, GoPro-first deployments, Raspberry Pi Zero installers, and Android prototype config were removed because they no longer describe the current deployment model.

## Camera Server

- Add more vendor-specific guided templates as real cameras are validated.
- Keep hardening fragile-camera behavior around failed RTSP sessions and failed ONVIF sessions without adding vendor-specific hacks to the default path.
- Improve user-facing camera health diagnostics on the admin dashboard.

## PTZ

- Add vendor HTTP tour backends where ONVIF preset tours are not supported.
- Add richer PTZ capability discovery where cameras report pan/tilt/zoom support reliably.
- Continue improving the preset editor around imported ONVIF preset names and tokens.

## Rocket Launch Workflow

- Add a richer recorded-stream browser for downloaded launch clips.
- Add per-camera launch recording health/status once more camera vendor APIs are wired in.
- Add launch workflow integration tests for schedule-file parsing and hook template rendering edge cases.

## Image Profiles

- Build tested Reolink image-profile templates for day, sunrise, sunset, night, and launch modes.
- Add vendor-specific validation for profile settings when a vendor API is selected.

## Operations

- Add an admin maintenance mode indicator for hidden cameras.
- Add documented reverse-proxy examples that preserve Fenetre auth and do not expose `/srv/fenetre/data` directly.
