import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import ANY, patch, MagicMock
from PIL import Image
from io import BytesIO
import sys
from types import SimpleNamespace
from requests.auth import HTTPBasicAuth
import yaml

from fenetre.fenetre import (
    FenetreHTTPRequestHandler,
    active_live_view_count,
    capture_failure_retry_interval,
    cleanup_frequent_timelapse_artifacts,
    cleanup_stale_timelapse_artifacts,
    discover_camera_timelapses,
    enforce_camera_storage_limit,
    get_pic_from_url,
    get_ssim_for_area,
    is_sunrise_or_sunset,
    is_camera_timelapse_enabled,
    _prune_launch_recordings_for_global_limit,
    queue_missing_daily_timelapses,
    record_live_view_heartbeat,
    run_camera_unavailable_command,
    should_defer_capture_for_live_view,
)
import fenetre.fenetre as fenetre_module
from fenetre.auth import authenticate_config_user_record, hash_password
from fenetre.camera_utils import sanitize_text_for_logs, sanitize_url_for_logs
from fenetre.picamera import Picamera2Capture


class TestFenetre(unittest.TestCase):
    def _cache_control_for_path(self, path):
        handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
        handler.path = path
        return handler._cache_control_header()

    def test_http_cache_headers_for_frequently_changing_files(self):
        for path in (
            "/cameras.json",
            "/photos/cam1/metadata.json",
            "/photos/cam1/latest.jpg",
            "/photos/cam1/2026-05-02/2026-05-02.m3u8",
            "/list.html",
            "/",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    self._cache_control_for_path(path), "no-cache, must-revalidate"
                )

    def test_http_cache_headers_for_versioned_assets(self):
        for path in (
            "/list.js?v=20260516-description",
            "/list.css?v=20260516-description",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    self._cache_control_for_path(path),
                    "public, max-age=31536000, immutable",
                )

    def test_public_camera_visibility_respects_private_site_and_camera_flags(self):
        handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
        old_global_config = getattr(fenetre_module, "global_config", {})
        old_cameras_config = getattr(fenetre_module, "cameras_config", {})
        try:
            fenetre_module.global_config = {
                "deployment_name": "Private Site",
                "ui": {"public_site": False},
            }
            fenetre_module.cameras_config = {
                "public-cam": {"url": "http://public", "visibility": "public"},
                "auth-cam": {"url": "http://auth", "public": False},
                "hidden-cam": {"url": "http://hidden", "visibility": "hidden"},
            }

            self.assertFalse(handler._camera_visible_to_public_user("public-cam", None))
            self.assertTrue(
                handler._camera_visible_to_public_user(
                    "auth-cam", {"username": "viewer"}
                )
            )
            self.assertFalse(
                handler._camera_visible_to_public_user(
                    "hidden-cam", {"username": "viewer"}
                )
            )
        finally:
            fenetre_module.global_config = old_global_config
            fenetre_module.cameras_config = old_cameras_config

    def test_public_user_can_change_own_password_without_admin_dashboard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            with open(config_path, "w") as f:
                yaml.safe_dump(
                    {
                        "users": {
                            "viewer": {
                                "role": "viewer",
                                "password_hash": hash_password("old-pass"),
                                "ptz_access": "none",
                                "ptz_cameras": [],
                            }
                        }
                    },
                    f,
                )

            handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
            responses = []
            handler._public_session_user = lambda: {
                "username": "viewer",
                "role": "viewer",
            }
            handler._read_json_body = lambda: {
                "current_password": "old-pass",
                "new_password": "new-pass",
            }
            handler._send_json = lambda status, payload: responses.append(
                (status, payload)
            )

            old_flags = fenetre_module.FLAGS
            try:
                fenetre_module.FLAGS = SimpleNamespace(config=config_path)
                fenetre_module.public_auth_sessions.clear()
                fenetre_module.public_auth_sessions.update(
                    {
                        "viewer-token": {
                            "user": {"username": "viewer"},
                            "expires_at": 9999999999,
                        },
                        "other-token": {
                            "user": {"username": "other"},
                            "expires_at": 9999999999,
                        },
                    }
                )
                fenetre_module.public_config_cache.update({"path": config_path})

                handler._handle_public_change_password_api()

                self.assertEqual(responses[0][0], 200)
                self.assertTrue(responses[0][1]["ok"])
                self.assertIsNone(
                    authenticate_config_user_record(config_path, "viewer", "old-pass")
                )
                self.assertIsNotNone(
                    authenticate_config_user_record(config_path, "viewer", "new-pass")
                )
                self.assertNotIn("viewer-token", fenetre_module.public_auth_sessions)
                self.assertIn("other-token", fenetre_module.public_auth_sessions)
                self.assertEqual(fenetre_module.public_config_cache, {})
            finally:
                fenetre_module.FLAGS = old_flags
                fenetre_module.public_auth_sessions.clear()
                fenetre_module.public_config_cache.clear()

    def test_public_launch_preview_filters_hidden_and_private_cameras(self):
        handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
        snapshot = (
            {
                "public-cam": {"url": "http://public", "visibility": "public"},
                "auth-cam": {"url": "http://auth", "public": False},
                "hidden-cam": {"url": "http://hidden", "visibility": "hidden"},
            },
            {"deployment_name": "Launch Site", "ui": {"public_site": True}},
            {},
        )
        preview = {
            "ok": True,
            "enabled": True,
            "events": [
                {
                    "name": "Falcon 9",
                    "plans": [
                        {
                            "id": "vandenberg",
                            "cameras": ["public-cam", "auth-cam", "hidden-cam"],
                            "camera_details": [
                                {"name": "public-cam"},
                                {"name": "auth-cam"},
                                {"name": "hidden-cam"},
                            ],
                        }
                    ],
                }
            ],
        }

        with patch.object(
            fenetre_module, "load_public_config_snapshot", return_value=snapshot
        ):
            public_preview = handler._filter_public_launch_preview(preview, None)
            authed_preview = handler._filter_public_launch_preview(
                preview, {"username": "operator"}
            )

        self.assertEqual(
            public_preview["events"][0]["plans"][0]["cameras"],
            ["public-cam"],
        )
        self.assertEqual(
            authed_preview["events"][0]["plans"][0]["cameras"],
            ["public-cam", "auth-cam"],
        )

    def test_launch_workflow_enabled_flag_reads_current_public_config(self):
        handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
        with patch.object(
            fenetre_module,
            "load_public_config_snapshot",
            return_value=(
                {},
                {"launch_workflow": {"enabled": "yes"}},
                {},
            ),
        ):
            self.assertTrue(handler._launch_workflow_enabled())

    def test_go2rtc_status_api_allows_admin_on_public_server(self):
        handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
        responses = []
        handler._public_session_user = lambda: {
            "username": "admin",
            "role": "admin",
        }
        handler._send_json = lambda status, payload: responses.append((status, payload))
        snapshot = (
            {"cam1": {"rtsp_url": "rtsp://camera/stream"}},
            {"go2rtc": {"enabled": True}},
            {},
        )

        with patch.object(
            fenetre_module, "load_public_config_snapshot", return_value=snapshot
        ), patch.object(
            fenetre_module,
            "_go2rtc_runtime_status",
            return_value={"api_reachable": True},
        ) as mock_status:
            handler._handle_go2rtc_status_api()

        self.assertEqual(responses, [(200, {"api_reachable": True})])
        mock_status.assert_called_once_with(
            {
                "global": {"go2rtc": {"enabled": True}},
                "cameras": {"cam1": {"rtsp_url": "rtsp://camera/stream"}},
            }
        )

    def test_go2rtc_status_api_requires_admin_on_public_server(self):
        handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
        responses = []
        handler._public_session_user = lambda: {
            "username": "viewer",
            "role": "viewer",
        }
        handler._send_json = lambda status, payload: responses.append((status, payload))

        with patch.object(fenetre_module, "_go2rtc_runtime_status") as mock_status:
            handler._handle_go2rtc_status_api()

        self.assertEqual(responses, [(403, {"error": "Admin access required"})])
        mock_status.assert_not_called()

    def test_go2rtc_status_api_requires_login_on_public_server(self):
        handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
        responses = []
        handler._public_session_user = lambda: None
        handler._send_public_auth_required = lambda: responses.append((401, {}))

        handler._handle_go2rtc_status_api()

        self.assertEqual(responses, [(401, {})])

    def test_request_camera_capture_wakes_next_capture_interval(self):
        fenetre_module.camera_capture_request_events.clear()
        fenetre_module.exit_event.clear()

        result = fenetre_module.request_camera_capture("cam1", "test")

        self.assertTrue(result["requested"])
        self.assertTrue(fenetre_module.wait_for_next_capture_interval("cam1", 5))
        self.assertFalse(fenetre_module.camera_capture_request_event("cam1").is_set())

    def test_ptz_preset_api_requests_post_move_capture(self):
        handler = FenetreHTTPRequestHandler.__new__(FenetreHTTPRequestHandler)
        responses = []
        handler._read_json_body = lambda: {"camera": "cam1", "preset": "home"}
        handler._send_json = lambda status, payload: responses.append((status, payload))
        handler._user_can_control_ptz = lambda camera, ptz, level: True
        handler._ptz_owner = lambda: "operator"
        old_cameras_config = getattr(fenetre_module, "cameras_config", {})
        try:
            fenetre_module.cameras_config = {
                "cam1": {"ptz": {"session_duration_s": 30}}
            }
            with patch.object(
                fenetre_module,
                "goto_preset",
                return_value={"ok": True, "camera": "cam1", "preset": "home"},
            ) as mock_goto, patch.object(
                fenetre_module,
                "request_camera_capture",
                return_value={
                    "requested": True,
                    "reason": "ptz preset home",
                    "delay_s": 5.0,
                },
            ) as mock_capture:
                handler._handle_ptz_preset_api()

                self.assertEqual(responses[0][0], 200)
                self.assertEqual(responses[0][1]["capture"]["requested"], True)
                mock_goto.assert_called_once_with(
                    "cam1",
                    {"ptz": {"session_duration_s": 30}},
                    "home",
                    owner="operator",
                    duration_s=30,
                    on_move_settled=ANY,
                    move_status_timeout_s=10.0,
                )
                # The actual capture is no longer requested synchronously --
                # it's deferred until goto_preset confirms (via ONVIF
                # GetStatus) that the physical move has settled, or gives up
                # waiting. Simulate that by invoking the callback goto_preset
                # was given, the same way the real background settle-thread
                # would (must happen inside this `with` block, while
                # request_camera_capture is still patched).
                mock_capture.assert_not_called()
                on_move_settled = mock_goto.call_args.kwargs["on_move_settled"]
                on_move_settled(True)
                mock_capture.assert_called_once_with(
                    "cam1", "ptz preset home", delay_s=5.0
                )
        finally:
            fenetre_module.cameras_config = old_cameras_config

    def test_live_view_heartbeat_tracks_and_expires_sessions(self):
        fenetre_module.live_view_sessions.clear()

        result = record_live_view_heartbeat(
            "cam1", "full", "session-1", "operator", ttl_s=5
        )

        self.assertEqual(result["active_count"], 1)
        self.assertEqual(active_live_view_count("cam1", "full"), 1)

        fenetre_module.live_view_sessions["session-1"]["expires_at"] = 0

        self.assertEqual(active_live_view_count("cam1", "full"), 0)
        self.assertEqual(fenetre_module.live_view_sessions, {})

    def test_live_view_heartbeat_can_remove_session(self):
        fenetre_module.live_view_sessions.clear()

        record_live_view_heartbeat("cam1", "full", "session-1", "operator")
        result = record_live_view_heartbeat(
            "cam1", "full", "session-1", "operator", active=False
        )

        self.assertFalse(result["active"])
        self.assertEqual(result["active_count"], 0)
        self.assertEqual(active_live_view_count("cam1", "full"), 0)

    def test_rtsp_capture_defers_while_live_view_is_active(self):
        fenetre_module.live_view_sessions.clear()

        try:
            record_live_view_heartbeat("cam1", "preview", "session-1", "operator")

            self.assertTrue(
                should_defer_capture_for_live_view(
                    "cam1", {"rtsp_url": "rtsp://example.test/stream"}
                )
            )
            self.assertFalse(
                should_defer_capture_for_live_view("cam2", {"url": "http://snapshot"})
            )
        finally:
            fenetre_module.live_view_sessions.clear()

    def test_discover_camera_timelapses_reports_existing_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            day_dir = os.path.join(tmpdir, "photos", "cam1", "2026-05-02")
            os.makedirs(day_dir)
            for filename in ("2026-05-02.m3u8", "2026-05-02.webm", "ignored.mp4"):
                with open(os.path.join(day_dir, filename), "wb") as f:
                    f.write(b"timelapse")

            timelapses = discover_camera_timelapses(
                "cam1",
                tmpdir,
                {"file_extension": "webm"},
                {"output_format": "hls", "file_extension": "mp4"},
            )

            self.assertEqual(
                [(item["date"], item["type"], item["format"]) for item in timelapses],
                [
                    ("2026-05-02", "daily", "webm"),
                    ("2026-05-02", "frequent", "m3u8"),
                ],
            )
            self.assertEqual(
                [item["url"] for item in timelapses],
                [
                    "/photos/cam1/2026-05-02/2026-05-02.webm",
                    "/photos/cam1/2026-05-02/2026-05-02.m3u8",
                ],
            )

    def test_discover_camera_timelapses_defaults_mp4_to_daily(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            day_dir = os.path.join(tmpdir, "photos", "cam1", "2026-05-02")
            os.makedirs(day_dir)
            for filename in ("2026-05-02.mp4", "2026-05-02.webm"):
                with open(os.path.join(day_dir, filename), "wb") as f:
                    f.write(b"timelapse")

            timelapses = discover_camera_timelapses("cam1", tmpdir, {}, {})

            self.assertEqual(
                [(item["date"], item["type"], item["format"]) for item in timelapses],
                [
                    ("2026-05-02", "daily", "mp4"),
                ],
            )

    def test_cleanup_stale_timelapse_artifacts_removes_duplicates_and_temp_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            day_dir = os.path.join(tmpdir, "2026-05-02")
            os.makedirs(day_dir)
            keep_path = os.path.join(day_dir, "2026-05-02.mp4")
            duplicate_path = os.path.join(day_dir, "2026-05-02.webm")
            zero_path = os.path.join(day_dir, "2026-05-02.m3u8")
            tmp_path = os.path.join(day_dir, ".2026-05-02.tmp.mp4")
            for path, content in (
                (keep_path, b"daily"),
                (duplicate_path, b"old-daily"),
                (zero_path, b""),
                (tmp_path, b"partial"),
            ):
                with open(path, "wb") as f:
                    f.write(content)

            missing = object()
            original_timelapse = getattr(fenetre_module, "timelapse_config", missing)
            try:
                fenetre_module.timelapse_config = {
                    "daily_timelapse": {"file_extension": "mp4"}
                }
                removed = cleanup_stale_timelapse_artifacts(day_dir)
            finally:
                if original_timelapse is missing:
                    delattr(fenetre_module, "timelapse_config")
                else:
                    fenetre_module.timelapse_config = original_timelapse

            self.assertEqual(removed, 3)
            self.assertTrue(os.path.exists(keep_path))
            self.assertFalse(os.path.exists(duplicate_path))
            self.assertFalse(os.path.exists(zero_path))
            self.assertFalse(os.path.exists(tmp_path))

    def test_discover_camera_timelapses_ignores_unsafe_camera_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(
                discover_camera_timelapses("../cam1", tmpdir, {}, {}),
                [],
            )

    def test_sunrise_sunset_unavailable_is_cached_per_day(self):
        fenetre_module._sunrise_sunset_window_cache.clear()
        camera_config = {
            "lat": 64.50186,
            "lon": -165.4128,
            "sunrise_sunset": {
                "enabled": True,
                "sunrise_offset_start_minutes": 60,
                "sunrise_offset_end_minutes": 30,
                "sunset_offset_start_minutes": 30,
                "sunset_offset_end_minutes": 60,
            },
        }
        global_conf = {"timezone": "America/Los_Angeles"}

        with patch(
            "fenetre.fenetre.sun", side_effect=ValueError("polar day")
        ) as mock_sun:
            with patch("fenetre.fenetre.logger") as mock_logger:
                self.assertFalse(
                    is_sunrise_or_sunset(camera_config, global_conf, "nome")
                )
                self.assertFalse(
                    is_sunrise_or_sunset(camera_config, global_conf, "nome")
                )

        self.assertEqual(mock_sun.call_count, 1)
        mock_logger.info.assert_called_once()

    def test_storage_prunes_snapshots_before_daily_timelapse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            camera_dir = os.path.join(tmpdir, "photos", "cam1")
            day1 = os.path.join(camera_dir, "2026-05-01")
            day2 = os.path.join(camera_dir, "2026-05-02")
            os.makedirs(day1)
            os.makedirs(day2)
            with open(os.path.join(day1, "2026-05-01.mp4"), "wb") as f:
                f.write(b"d" * 500)
            with open(os.path.join(day1, "2026-05-01T12-00-00UTC.jpg"), "wb") as f:
                f.write(b"j" * 1000)
            with open(os.path.join(day2, "2026-05-02.mp4"), "wb") as f:
                f.write(b"d" * 500)

            had_global = hasattr(fenetre_module, "global_config")
            had_timelapse = hasattr(fenetre_module, "timelapse_config")
            old_global = getattr(fenetre_module, "global_config", None)
            old_timelapse = getattr(fenetre_module, "timelapse_config", None)
            try:
                fenetre_module.global_config = {
                    "work_dir": tmpdir,
                    "pic_dir": os.path.join(tmpdir, "photos"),
                }
                fenetre_module.timelapse_config = {
                    "daily_timelapse": {"file_extension": "mp4"}
                }

                enforce_camera_storage_limit(
                    "cam1",
                    {},
                    {
                        "camera_max_size_GB": 1200 / (1024**3),
                        "prune_snapshots_first": True,
                    },
                    dry_run=False,
                )
            finally:
                if had_global:
                    fenetre_module.global_config = old_global
                else:
                    delattr(fenetre_module, "global_config")
                if had_timelapse:
                    fenetre_module.timelapse_config = old_timelapse
                else:
                    delattr(fenetre_module, "timelapse_config")

            self.assertFalse(
                os.path.exists(os.path.join(day1, "2026-05-01T12-00-00UTC.jpg"))
            )
            self.assertTrue(os.path.exists(os.path.join(day1, "2026-05-01.mp4")))
            self.assertTrue(os.path.exists(os.path.join(day2, "2026-05-02.mp4")))

    def test_storage_does_not_prune_current_day_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            camera_dir = os.path.join(tmpdir, "photos", "cam1")
            old_day = os.path.join(camera_dir, "2000-01-01")
            current_day = os.path.join(camera_dir, today)
            os.makedirs(old_day)
            os.makedirs(current_day)
            with open(os.path.join(old_day, "2000-01-01.mp4"), "wb") as f:
                f.write(b"d" * 500)
            with open(os.path.join(old_day, "2000-01-01T12-00-00UTC.jpg"), "wb") as f:
                f.write(b"j" * 1000)
            with open(os.path.join(current_day, f"{today}.mp4"), "wb") as f:
                f.write(b"d" * 500)
            current_snapshot = os.path.join(current_day, f"{today}T12-00-00UTC.jpg")
            with open(current_snapshot, "wb") as f:
                f.write(b"j" * 1000)

            had_global = hasattr(fenetre_module, "global_config")
            had_timelapse = hasattr(fenetre_module, "timelapse_config")
            old_global = getattr(fenetre_module, "global_config", None)
            old_timelapse = getattr(fenetre_module, "timelapse_config", None)
            try:
                fenetre_module.global_config = {
                    "work_dir": tmpdir,
                    "pic_dir": os.path.join(tmpdir, "photos"),
                    "timezone": "UTC",
                }
                fenetre_module.timelapse_config = {
                    "daily_timelapse": {"file_extension": "mp4"}
                }

                enforce_camera_storage_limit(
                    "cam1",
                    {},
                    {
                        "camera_max_size_GB": 1900 / (1024**3),
                        "prune_snapshots_first": True,
                    },
                    dry_run=False,
                )
            finally:
                if had_global:
                    fenetre_module.global_config = old_global
                else:
                    delattr(fenetre_module, "global_config")
                if had_timelapse:
                    fenetre_module.timelapse_config = old_timelapse
                else:
                    delattr(fenetre_module, "timelapse_config")

            self.assertTrue(os.path.exists(current_snapshot))
            self.assertFalse(
                os.path.exists(os.path.join(old_day, "2000-01-01T12-00-00UTC.jpg"))
            )

    def test_storage_enforces_slugged_camera_dir_and_global_cap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            camera_dir = os.path.join(tmpdir, "photos", "new-cam")
            day_dir = os.path.join(camera_dir, "2000-01-01")
            os.makedirs(day_dir)
            snapshot = os.path.join(day_dir, "2000-01-01T12-00-00UTC.jpg")
            daily = os.path.join(day_dir, "2000-01-01.mp4")
            with open(snapshot, "wb") as f:
                f.write(b"j" * 1000)
            with open(daily, "wb") as f:
                f.write(b"d" * 500)

            had_global = hasattr(fenetre_module, "global_config")
            had_timelapse = hasattr(fenetre_module, "timelapse_config")
            old_global = getattr(fenetre_module, "global_config", None)
            old_timelapse = getattr(fenetre_module, "timelapse_config", None)
            try:
                fenetre_module.global_config = {
                    "work_dir": tmpdir,
                    "pic_dir": os.path.join(tmpdir, "photos"),
                    "timezone": "UTC",
                }
                fenetre_module.timelapse_config = {
                    "daily_timelapse": {"file_extension": "mp4"}
                }

                current_size = enforce_camera_storage_limit(
                    "New-Cam",
                    {"work_dir_max_size_GB": 5000 / (1024**3)},
                    {
                        "work_dir_max_size_GB": 1200 / (1024**3),
                        "camera_max_size_GB": 5000 / (1024**3),
                        "prune_snapshots_first": True,
                    },
                    dry_run=False,
                )
            finally:
                if had_global:
                    fenetre_module.global_config = old_global
                else:
                    delattr(fenetre_module, "global_config")
                if had_timelapse:
                    fenetre_module.timelapse_config = old_timelapse
                else:
                    delattr(fenetre_module, "timelapse_config")

            self.assertEqual(current_size, 500)
            self.assertFalse(os.path.exists(snapshot))
            self.assertTrue(os.path.exists(daily))

    def test_global_storage_limit_prunes_old_launch_recordings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            launches_dir = os.path.join(tmpdir, "launches")
            old_launch_dir = os.path.join(launches_dir, "old-launch")
            new_launch_dir = os.path.join(launches_dir, "new-launch")
            os.makedirs(old_launch_dir)
            os.makedirs(new_launch_dir)
            old_recording = os.path.join(old_launch_dir, "old-launch-cam1.mp4")
            new_recording = os.path.join(new_launch_dir, "new-launch-cam1.mp4")
            with open(old_recording, "wb") as f:
                f.write(b"o" * 700)
            with open(new_recording, "wb") as f:
                f.write(b"n" * 500)
            os.utime(old_recording, (946684800, 946684800))
            os.utime(old_launch_dir, (946684800, 946684800))
            os.utime(new_recording, (978307200, 978307200))
            os.utime(new_launch_dir, (978307200, 978307200))

            had_global = hasattr(fenetre_module, "global_config")
            old_global = getattr(fenetre_module, "global_config", None)
            try:
                fenetre_module.global_config = {"timezone": "UTC"}
                current_size = _prune_launch_recordings_for_global_limit(
                    tmpdir, 1200, 600, dry_run=False
                )
            finally:
                if had_global:
                    fenetre_module.global_config = old_global
                else:
                    delattr(fenetre_module, "global_config")

            self.assertLessEqual(current_size, 600)
            self.assertFalse(os.path.exists(old_launch_dir))
            self.assertTrue(os.path.exists(new_launch_dir))

    def test_is_camera_timelapse_enabled_respects_disable_flags(self):
        missing = object()
        original_cameras_config = getattr(fenetre_module, "cameras_config", missing)
        try:
            fenetre_module.cameras_config = {
                "enabled": {"url": "http://cam"},
                "disabled_camera": {"url": "http://cam", "disabled": True},
                "legacy_disabled": {
                    "url": "http://cam",
                    "generate_timelapse": False,
                },
                "timelapse_enabled_disabled": {
                    "url": "http://cam",
                    "timelapse_enabled": False,
                },
                "nested_disabled": {
                    "url": "http://cam",
                    "timelapse": {"enabled": False},
                },
            }

            self.assertTrue(is_camera_timelapse_enabled("enabled"))
            self.assertFalse(is_camera_timelapse_enabled("disabled_camera"))
            self.assertFalse(is_camera_timelapse_enabled("legacy_disabled"))
            self.assertFalse(is_camera_timelapse_enabled("timelapse_enabled_disabled"))
            self.assertFalse(is_camera_timelapse_enabled("nested_disabled"))
        finally:
            if original_cameras_config is missing:
                delattr(fenetre_module, "cameras_config")
            else:
                fenetre_module.cameras_config = original_cameras_config

    def test_queue_missing_daily_timelapses_backfills_past_snapshot_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            photos_dir = os.path.join(tmpdir, "photos")
            queue_file = os.path.join(tmpdir, "timelapse_queue.txt")
            open(queue_file, "w").close()

            eligible_day = os.path.join(photos_dir, "enabled", "2000-01-01")
            existing_daily_day = os.path.join(photos_dir, "enabled", "2000-01-02")
            empty_day = os.path.join(photos_dir, "enabled", "2000-01-03")
            disabled_day = os.path.join(photos_dir, "disabled", "2000-01-01")
            for day_dir in (eligible_day, existing_daily_day, empty_day, disabled_day):
                os.makedirs(day_dir)
            with open(
                os.path.join(eligible_day, "2000-01-01T12-00-00UTC.jpg"), "wb"
            ) as f:
                f.write(b"jpg")
            with open(
                os.path.join(existing_daily_day, "2000-01-02T12-00-00UTC.jpg"), "wb"
            ) as f:
                f.write(b"jpg")
            with open(os.path.join(existing_daily_day, "2000-01-02.webm"), "wb") as f:
                f.write(b"webm")
            with open(
                os.path.join(disabled_day, "2000-01-01T12-00-00UTC.jpg"), "wb"
            ) as f:
                f.write(b"jpg")

            missing = object()
            originals = {
                "global_config": getattr(fenetre_module, "global_config", missing),
                "timelapse_config": getattr(
                    fenetre_module, "timelapse_config", missing
                ),
                "cameras_config": getattr(fenetre_module, "cameras_config", missing),
                "timelapse_queue_file": getattr(
                    fenetre_module, "timelapse_queue_file", missing
                ),
            }
            try:
                fenetre_module.global_config = {
                    "work_dir": tmpdir,
                    "pic_dir": photos_dir,
                    "timezone": "UTC",
                }
                fenetre_module.timelapse_config = {
                    "daily_timelapse": {"enabled": True, "file_extension": "webm"}
                }
                fenetre_module.cameras_config = {
                    "enabled": {},
                    "disabled": {"generate_timelapse": False},
                }
                fenetre_module.timelapse_queue_file = queue_file

                queued = queue_missing_daily_timelapses()
            finally:
                for name, value in originals.items():
                    if value is missing:
                        delattr(fenetre_module, name)
                    else:
                        setattr(fenetre_module, name, value)

            with open(queue_file) as f:
                queued_paths = {line.strip() for line in f if line.strip()}

            self.assertEqual(queued, 1)
            self.assertEqual(queued_paths, {eligible_day})

    @patch("fenetre.fenetre.subprocess.run")
    def test_run_camera_unavailable_command(self, mock_subprocess_run):
        mock_subprocess_run.return_value.returncode = 0
        mock_subprocess_run.return_value.stdout = ""
        mock_subprocess_run.return_value.stderr = ""

        run_camera_unavailable_command(
            "cam1",
            {
                "unavailable_command": "printf hello >> /tmp/cam1-unavailable",
                "unavailable_command_timeout_s": 7,
            },
            "thread stopped",
        )

        args, kwargs = mock_subprocess_run.call_args
        self.assertEqual(args[0], "printf hello >> /tmp/cam1-unavailable")
        self.assertTrue(kwargs["shell"])
        self.assertEqual(kwargs["timeout"], 7)
        self.assertEqual(kwargs["env"]["FENETRE_CAMERA_NAME"], "cam1")
        self.assertEqual(kwargs["env"]["FENETRE_UNAVAILABLE_REASON"], "thread stopped")
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])

    @patch("fenetre.fenetre.subprocess.run")
    def test_run_camera_unavailable_command_noop_without_config(
        self, mock_subprocess_run
    ):
        run_camera_unavailable_command("cam1", {}, "thread stopped")

        mock_subprocess_run.assert_not_called()

    @patch("fenetre.fenetre.requests.get")
    @patch("fenetre.fenetre.time.time", return_value=1234567890)
    def test_get_pic_from_url_cache_bust(self, mock_time, mock_requests_get):
        # Mock the response from requests.get
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_response.request = SimpleNamespace(
            url="http://example.com/image.jpg", headers={"Accept": "image/*,*"}
        )
        # Create a dummy image for the content
        dummy_image = Image.new("RGB", (100, 100), color="red")
        byte_arr = BytesIO()
        dummy_image.save(byte_arr, format="JPEG")
        mock_response.content = byte_arr.getvalue()
        mock_requests_get.return_value = mock_response

        # Test case 1: cache_bust enabled, no existing query params
        camera_config_1 = {"cache_bust": True}
        url_1 = "http://example.com/image.jpg"
        get_pic_from_url(url_1, 10, camera_config=camera_config_1, global_config={})
        mock_requests_get.assert_called_with(
            "http://example.com/image.jpg?_=1234567890",
            timeout=10,
            headers={"Accept": "image/*,*"},
        )

        # Test case 2: cache_bust enabled, with existing query params
        camera_config_2 = {"cache_bust": True}
        url_2 = "http://example.com/image.jpg?param=value"
        get_pic_from_url(url_2, 10, camera_config=camera_config_2, global_config={})
        mock_requests_get.assert_called_with(
            "http://example.com/image.jpg?param=value&_=1234567890",
            timeout=10,
            headers={"Accept": "image/*,*"},
        )

        # Test case 3: cache_bust disabled
        camera_config_3 = {"cache_bust": False}
        url_3 = "http://example.com/image.jpg"
        get_pic_from_url(url_3, 10, camera_config=camera_config_3, global_config={})
        mock_requests_get.assert_called_with(
            "http://example.com/image.jpg", timeout=10, headers={"Accept": "image/*,*"}
        )

        # Test case 4: cache_bust option not present
        camera_config_4 = {}
        url_4 = "http://example.com/image.jpg"
        get_pic_from_url(url_4, 10, camera_config=camera_config_4, global_config={})
        mock_requests_get.assert_called_with(
            "http://example.com/image.jpg", timeout=10, headers={"Accept": "image/*,*"}
        )

    @patch("fenetre.fenetre.requests.get")
    def test_get_pic_from_url_supports_http_basic_auth(self, mock_requests_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_response.request = SimpleNamespace(
            url="http://example.com/image.jpg",
            headers={"Authorization": "Basic secret", "Accept": "image/*,*"},
        )
        dummy_image = Image.new("RGB", (100, 100), color="red")
        byte_arr = BytesIO()
        dummy_image.save(byte_arr, format="JPEG")
        mock_response.content = byte_arr.getvalue()
        mock_requests_get.return_value = mock_response

        get_pic_from_url(
            "http://example.com/image.jpg",
            10,
            camera_config={
                "http_auth": {
                    "type": "basic",
                    "username": "admin",
                    "password": "secret",
                }
            },
            global_config={},
        )

        _, kwargs = mock_requests_get.call_args
        self.assertIsInstance(kwargs["auth"], HTTPBasicAuth)
        self.assertEqual(kwargs["auth"].username, "admin")
        self.assertEqual(kwargs["auth"].password, "secret")

    @patch("fenetre.fenetre.requests.get")
    def test_get_pic_from_url_rejects_http_on_rtsp_port(self, mock_requests_get):
        with self.assertRaisesRegex(RuntimeError, "HTTP on port 554"):
            get_pic_from_url(
                "http://10.1.64.69:554/live",
                10,
                camera_config={},
                global_config={},
            )

        mock_requests_get.assert_not_called()

    @patch("fenetre.fenetre.subprocess.run")
    def test_get_pic_from_local_command_uses_shell_style_splitting(
        self, mock_subprocess_run
    ):
        dummy_image = Image.new("RGB", (100, 100), color="red")
        byte_arr = BytesIO()
        dummy_image.save(byte_arr, format="JPEG")
        mock_subprocess_run.return_value = SimpleNamespace(
            returncode=0,
            stdout=byte_arr.getvalue(),
            stderr=b"",
        )

        fenetre_module.global_config = {}
        fenetre_module.get_pic_from_local_command(
            'ffmpeg -i "rtsp://example.local/live stream" -frames:v 1 -f image2pipe -',
            10,
            "cam1",
            {},
        )

        args, kwargs = mock_subprocess_run.call_args
        self.assertEqual(args[0][2], "rtsp://example.local/live stream")
        self.assertEqual(kwargs["timeout"], 10)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)

    @patch("fenetre.fenetre.subprocess.run")
    def test_get_pic_from_local_command_reports_non_image_stdout(
        self, mock_subprocess_run
    ):
        mock_subprocess_run.return_value = SimpleNamespace(
            returncode=0,
            stdout=b"not an image",
            stderr=b"ffmpeg warning text",
        )

        fenetre_module.global_config = {}
        with self.assertRaisesRegex(
            RuntimeError,
            "did not return a valid image.*stdout_first_200=b'not an image'",
        ):
            fenetre_module.get_pic_from_local_command(
                "ffmpeg -i rtsp://example.local/live -frames:v 1 -f image2pipe -",
                10,
                "cam1",
                {},
            )

    @patch("fenetre.fenetre.subprocess.run")
    def test_get_pic_from_local_command_reports_nonzero_exit(self, mock_subprocess_run):
        mock_subprocess_run.return_value = SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"401 Unauthorized",
        )

        fenetre_module.global_config = {}
        with self.assertRaisesRegex(
            RuntimeError,
            "exit code 1.*401 Unauthorized",
        ):
            fenetre_module.get_pic_from_local_command(
                "ffmpeg -i rtsp://example.local/live -frames:v 1 -f image2pipe -",
                10,
                "cam1",
                {},
            )

    def test_capture_failure_retry_interval_defaults_to_normal_interval(self):
        self.assertEqual(
            capture_failure_retry_interval({"snap_interval_s": 180}, 30),
            60.0,
        )
        self.assertEqual(
            capture_failure_retry_interval({"snap_interval_s": 180}),
            180.0,
        )

    def test_capture_failure_retry_interval_honors_camera_override(self):
        self.assertEqual(
            capture_failure_retry_interval(
                {"snap_interval_s": 60, "capture_failure_interval_s": 300}, 60
            ),
            300.0,
        )

    def test_sanitize_url_for_logs_redacts_credentials(self):
        url = (
            "http://admin:secret@example.local/cgi-bin/api.cgi?"
            "cmd=Snap&user=admin&password=p%40ss&token=abc123&channel=0"
        )

        redacted = sanitize_url_for_logs(url)

        self.assertIn("REDACTED@example.local", redacted)
        self.assertIn("cmd=Snap", redacted)
        self.assertIn("channel=0", redacted)
        self.assertIn("user=REDACTED", redacted)
        self.assertIn("password=REDACTED", redacted)
        self.assertIn("token=REDACTED", redacted)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("p%40ss", redacted)
        self.assertNotIn("abc123", redacted)

    def test_sanitize_text_for_logs_redacts_embedded_urls_and_pairs(self):
        text = (
            "ffmpeg -i 'rtsp://admin:secret@example.local/stream' "
            "failed after http://camera.local/snap?user=admin&password=pw "
            "token=abc123"
        )

        redacted = sanitize_text_for_logs(text)

        self.assertIn("rtsp://REDACTED@example.local/stream", redacted)
        self.assertIn("user=REDACTED", redacted)
        self.assertIn("password=REDACTED", redacted)
        self.assertIn("token=REDACTED", redacted)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("admin@example", redacted)
        self.assertNotIn("pw", redacted)
        self.assertNotIn("abc123", redacted)

    def test_picamera2_capture_applies_base_and_mode_controls(self):
        instances = []

        class FakePicamera2:
            @staticmethod
            def load_tuning_file(path):
                return {"tuning_file": path}

            def __init__(self, tuning=None):
                self.tuning = tuning
                self.configured = None
                self.controls = []
                self.started = False
                self.stopped = False
                instances.append(self)

            def create_still_configuration(self, **kwargs):
                return {"still": kwargs}

            def configure(self, config, tuning=None):
                self.configured = (config, tuning)

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

            def set_controls(self, controls):
                self.controls.append(controls)

            def capture_file(self, output, format=None):
                img = Image.new("RGB", (8, 6), color="red")
                img.save(output, format="JPEG")

        fake_controls = SimpleNamespace(
            draft=SimpleNamespace(
                NoiseReductionModeEnum=SimpleNamespace(HighQuality=42)
            )
        )

        old_picamera2 = sys.modules.get("picamera2")
        old_libcamera = sys.modules.get("libcamera")
        sys.modules["picamera2"] = SimpleNamespace(Picamera2=FakePicamera2)
        sys.modules["libcamera"] = SimpleNamespace(controls=fake_controls)
        try:
            capture = Picamera2Capture(
                {
                    "tuning_file": "/tmp/tuning.json",
                    "startup_warmup_s": 0,
                    "control_warmup_s": 0,
                    "main_size": [4056, 3040],
                    "exposure_value": 1.5,
                    "denoise_mode": "HighQuality",
                    "night_settings": {
                        "ae_enable": False,
                        "exposure_time": 1000000,
                        "analogue_gain": 2.0,
                    },
                }
            )
            pic = capture.capture("night")
            capture.close()
        finally:
            if old_picamera2 is None:
                sys.modules.pop("picamera2", None)
            else:
                sys.modules["picamera2"] = old_picamera2
            if old_libcamera is None:
                sys.modules.pop("libcamera", None)
            else:
                sys.modules["libcamera"] = old_libcamera

        self.assertEqual(pic.size, (8, 6))
        fake = instances[0]
        self.assertTrue(fake.started)
        self.assertTrue(fake.stopped)
        self.assertEqual(
            fake.configured[0],
            {"still": {"main": {"size": (4056, 3040)}}},
        )
        self.assertEqual(
            fake.controls[-1],
            {
                "ExposureValue": 1.5,
                "NoiseReductionMode": 42,
                "AeEnable": False,
                "ExposureTime": 1000000,
                "AnalogueGain": 2.0,
            },
        )

    def test_picamera2_exposure_control_updates_next_day_capture(self):
        instances = []

        class FakePicamera2:
            def __init__(self):
                self.controls = []
                instances.append(self)

            def create_still_configuration(self, **kwargs):
                return {}

            def configure(self, config, tuning=None):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

            def set_controls(self, controls):
                self.controls.append(controls)

            def capture_file(self, output, format=None):
                img = Image.new("RGB", (8, 6), color="white")
                img.save(output, format="JPEG")

            def capture_metadata(self):
                return {"ExposureTime": 100000, "AnalogueGain": 8.0}

        fake_controls = SimpleNamespace(
            draft=SimpleNamespace(
                NoiseReductionModeEnum=SimpleNamespace(HighQuality=42)
            )
        )

        old_picamera2 = sys.modules.get("picamera2")
        old_libcamera = sys.modules.get("libcamera")
        sys.modules["picamera2"] = SimpleNamespace(Picamera2=FakePicamera2)
        sys.modules["libcamera"] = SimpleNamespace(controls=fake_controls)
        try:
            capture = Picamera2Capture(
                {
                    "startup_warmup_s": 0,
                    "control_warmup_s": 0,
                    "exposure_control": {
                        "enabled": True,
                        "target_luma": 0.45,
                        "min_adjustment_factor": 0.8,
                        "max_adjustment_factor": 1.25,
                        "day": {
                            "enabled": True,
                            "min_exposure_time": 100,
                            "max_exposure_time": 20000,
                            "min_analogue_gain": 1.0,
                            "max_analogue_gain": 4.0,
                            "start_exposure_time": 1000,
                            "start_analogue_gain": 1.0,
                        },
                    },
                }
            )
            capture.capture("day")
            capture.capture("day")
            capture.close()
        finally:
            if old_picamera2 is None:
                sys.modules.pop("picamera2", None)
            else:
                sys.modules["picamera2"] = old_picamera2
            if old_libcamera is None:
                sys.modules.pop("libcamera", None)
            else:
                sys.modules["libcamera"] = old_libcamera

        self.assertEqual(
            instances[0].controls[-1],
            {"AeEnable": False, "ExposureTime": 800, "AnalogueGain": 1.0},
        )
        self.assertEqual(
            capture.get_exposure_control_state()["modes"]["day"],
            {"ae_enable": False, "exposure_time": 640, "analogue_gain": 1.0},
        )

    def test_picamera2_exposure_control_restores_initial_state(self):
        instances = []

        class FakePicamera2:
            def __init__(self):
                self.controls = []
                instances.append(self)

            def create_still_configuration(self, **kwargs):
                return {}

            def configure(self, config, tuning=None):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

            def set_controls(self, controls):
                self.controls.append(controls)

            def capture_file(self, output, format=None):
                img = Image.new("RGB", (8, 6), color="white")
                img.save(output, format="JPEG")

            def capture_metadata(self):
                return {"ExposureTime": 100000, "AnalogueGain": 8.0}

        fake_controls = SimpleNamespace(
            draft=SimpleNamespace(
                NoiseReductionModeEnum=SimpleNamespace(HighQuality=42)
            )
        )

        old_picamera2 = sys.modules.get("picamera2")
        old_libcamera = sys.modules.get("libcamera")
        sys.modules["picamera2"] = SimpleNamespace(Picamera2=FakePicamera2)
        sys.modules["libcamera"] = SimpleNamespace(controls=fake_controls)
        try:
            capture = Picamera2Capture(
                {
                    "startup_warmup_s": 0,
                    "control_warmup_s": 0,
                    "exposure_control": {
                        "enabled": True,
                        "target_luma": 0.45,
                        "min_adjustment_factor": 0.8,
                        "max_adjustment_factor": 1.25,
                        "day": {"enabled": True},
                    },
                },
                initial_exposure_state={
                    "enabled": True,
                    "modes": {
                        "day": {
                            "ae_enable": False,
                            "exposure_time": 500,
                            "analogue_gain": 2.0,
                        }
                    },
                },
            )
            capture.capture("day")
            capture.close()
        finally:
            if old_picamera2 is None:
                sys.modules.pop("picamera2", None)
            else:
                sys.modules["picamera2"] = old_picamera2
            if old_libcamera is None:
                sys.modules.pop("libcamera", None)
            else:
                sys.modules["libcamera"] = old_libcamera

        self.assertEqual(
            instances[0].controls[0],
            {"AeEnable": False, "ExposureTime": 500, "AnalogueGain": 2.0},
        )

    def test_picamera2_night_exposure_control_locks_analogue_gain(self):
        class FakePicamera2:
            def create_still_configuration(self, **kwargs):
                return {}

            def configure(self, config, tuning=None):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

            def set_controls(self, controls):
                pass

            def capture_file(self, output, format=None):
                img = Image.new("RGB", (8, 6), color="black")
                img.save(output, format="JPEG")

            def capture_metadata(self):
                return {"ExposureTime": 100000, "AnalogueGain": 8.0}

        fake_controls = SimpleNamespace(
            draft=SimpleNamespace(
                NoiseReductionModeEnum=SimpleNamespace(HighQuality=42)
            )
        )

        old_picamera2 = sys.modules.get("picamera2")
        old_libcamera = sys.modules.get("libcamera")
        sys.modules["picamera2"] = SimpleNamespace(Picamera2=FakePicamera2)
        sys.modules["libcamera"] = SimpleNamespace(controls=fake_controls)
        try:
            capture = Picamera2Capture(
                {
                    "startup_warmup_s": 0,
                    "control_warmup_s": 0,
                    "exposure_control": {
                        "enabled": True,
                        "max_adjustment_factor": 3.0,
                        "night": {
                            "enabled": True,
                            "min_exposure_time": 1000,
                            "max_exposure_time": 15000000,
                            "min_analogue_gain": 2.0,
                            "max_analogue_gain": 2.0,
                            "start_exposure_time": 1000000,
                            "start_analogue_gain": 2.0,
                        },
                    },
                }
            )
            capture.capture("night")
            capture.close()
        finally:
            if old_picamera2 is None:
                sys.modules.pop("picamera2", None)
            else:
                sys.modules["picamera2"] = old_picamera2
            if old_libcamera is None:
                sys.modules.pop("libcamera", None)
            else:
                sys.modules["libcamera"] = old_libcamera

        self.assertEqual(
            capture.get_exposure_control_state()["modes"]["night"],
            {"ae_enable": False, "exposure_time": 3000000, "analogue_gain": 2.0},
        )

    def test_picamera2_exposure_metering_percentile_tracks_bright_pixels(self):
        image = Image.new("L", (64, 64), color=0)
        for x in range(58, 64):
            for y in range(64):
                image.putpixel((x, y), 255)

        capture = Picamera2Capture.__new__(Picamera2Capture)
        capture.closed = True
        capture.exposure_control = {"metering_area": None, "metering_percentile": 50}
        self.assertEqual(capture._measure_luma(image), 0.0)

        capture.exposure_control["metering_percentile"] = 95
        self.assertEqual(capture._measure_luma(image), 1.0)

    def test_get_ssim_for_area_clamps_crop_to_image_size(self):
        image1 = Image.new("RGB", (1921, 1440), color="black")
        image2 = Image.new("RGB", (1921, 1440), color="black")

        ssim = get_ssim_for_area(image1, image2, "0,0,2560,500")

        self.assertEqual(ssim, 1.0)

    def test_cleanup_frequent_timelapse_artifacts_removes_hls_outputs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            day_dir = os.path.join(tmp_dir, "2026-05-02")
            os.makedirs(day_dir)
            legacy_segment_dir = os.path.join(day_dir, "2026-05-02.segments")
            os.makedirs(legacy_segment_dir)

            hls_paths = [
                os.path.join(day_dir, "2026-05-02.m3u8"),
                os.path.join(day_dir, ".2026-05-02.hls-manifest.json"),
                os.path.join(day_dir, "init.mp4"),
                os.path.join(day_dir, "segment-000000.ts"),
                os.path.join(day_dir, "segment-000002.ts"),
                os.path.join(day_dir, "segment-000003.m4s"),
                os.path.join(legacy_segment_dir, "segment-legacy.ts"),
            ]
            preserved_paths = [
                os.path.join(day_dir, "2026-05-02.webm"),
                os.path.join(day_dir, "2026-05-02T10-00-00PDT.jpg"),
            ]
            for path in hls_paths + preserved_paths:
                with open(path, "w") as f:
                    f.write("test")

            deleted_paths = cleanup_frequent_timelapse_artifacts(
                day_dir,
                {"output_format": "hls", "file_extension": "mp4"},
                {"file_extension": "webm"},
            )

            self.assertFalse(os.path.exists(os.path.join(day_dir, "2026-05-02.m3u8")))
            self.assertFalse(
                os.path.exists(os.path.join(day_dir, ".2026-05-02.hls-manifest.json"))
            )
            self.assertFalse(os.path.exists(os.path.join(day_dir, "segment-000000.ts")))
            self.assertFalse(os.path.exists(os.path.join(day_dir, "segment-000002.ts")))
            self.assertFalse(
                os.path.exists(os.path.join(day_dir, "segment-000003.m4s"))
            )
            self.assertFalse(os.path.exists(os.path.join(day_dir, "init.mp4")))
            self.assertFalse(os.path.exists(legacy_segment_dir))
            for path in preserved_paths:
                self.assertTrue(os.path.exists(path))
            self.assertEqual(len(deleted_paths), 7)

    def test_cleanup_frequent_timelapse_artifacts_removes_file_output(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            day_dir = os.path.join(tmp_dir, "2026-05-02")
            os.makedirs(day_dir)
            frequent_path = os.path.join(day_dir, "2026-05-02.mp4")
            daily_path = os.path.join(day_dir, "2026-05-02.webm")
            for path in [frequent_path, daily_path]:
                with open(path, "w") as f:
                    f.write("test")

            deleted_paths = cleanup_frequent_timelapse_artifacts(
                day_dir,
                {"output_format": "file", "file_extension": "mp4"},
                {"file_extension": "webm"},
            )

            self.assertFalse(os.path.exists(frequent_path))
            self.assertTrue(os.path.exists(daily_path))
            self.assertEqual(deleted_paths, [frequent_path])

    def test_cleanup_frequent_timelapse_artifacts_preserves_matching_file_extension(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp_dir:
            day_dir = os.path.join(tmp_dir, "2026-05-02")
            os.makedirs(day_dir)
            shared_path = os.path.join(day_dir, "2026-05-02.mp4")
            with open(shared_path, "w") as f:
                f.write("test")

            deleted_paths = cleanup_frequent_timelapse_artifacts(
                day_dir,
                {"output_format": "file", "file_extension": "mp4"},
                {"file_extension": "mp4"},
            )

            self.assertTrue(os.path.exists(shared_path))
            self.assertEqual(deleted_paths, [])


if __name__ == "__main__":
    unittest.main()
