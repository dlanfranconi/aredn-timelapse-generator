import json
import os
import signal
import base64
import errno

# Add project root to allow importing admin_server
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests
import yaml

from fenetre.auth import (
    authenticate_config_user,
    authenticate_config_user_record,
    ensure_default_admin_user,
    hash_password,
    reset_admin_user,
)
from fenetre.admin_server import _sync_go2rtc_runtime, app as flask_app
from fenetre.ptz import set_lock


class ConfigServerTestCase(unittest.TestCase):

    def setUp(self):
        self.app = flask_app.test_client()
        self.app.testing = True

        # Create a temporary config file
        self.temp_config_file = tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".yaml"
        )
        self.test_config_data = {
            "global": {"setting": "value"},
            "cameras": {"cam1": {"url": "http://localhost"}},
        }
        yaml.dump(self.test_config_data, self.temp_config_file)
        self.temp_config_file.close()

        # Create a temporary PID file
        self.temp_pid_file = tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".pid"
        )
        self.temp_pid_file.write(str(os.getpid()))  # Write a dummy PID
        self.temp_pid_file.close()

        # Patch the module-level variables in admin_server directly
        flask_app.config["FENETRE_CONFIG_FILE"] = self.temp_config_file.name
        flask_app.config["FENETRE_PID_FILE_PATH"] = self.temp_pid_file.name
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = False
        flask_app.config["FENETRE_RELOAD_ON_CONFIG_WRITE"] = False
        flask_app.config.pop("FENETRE_ADMIN_USERNAME", None)
        flask_app.config.pop("FENETRE_ADMIN_PASSWORD", None)

    def tearDown(self):
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = False
        flask_app.config["FENETRE_RELOAD_ON_CONFIG_WRITE"] = False
        set_lock("cam1", False)

    def test_get_config_success(self):
        response = self.app.get("/config")
        self.assertEqual(response.status_code, 200)
        # The config is returned under a 'config' key
        self.assertEqual(response.json["config"], self.test_config_data)

    def test_get_config_not_found(self):
        flask_app.config["FENETRE_CONFIG_FILE"] = "/tmp/non_existent_config.yaml"

    # ... (other parts of the class) ...

    def test_update_config_success(self):
        new_config_data_json = {
            "global": {"setting": "new_value"},
            "cameras": {"cam2": {"url": "http://newhost"}},
        }

        response = self.app.put(
            "/config",
            data=json.dumps(new_config_data_json),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Configuration updated successfully (saved as YAML)",
            response.json["message"],
        )
        self.assertEqual(response.json["config_path"], self.temp_config_file.name)
        self.assertGreater(response.json["size_bytes"], 0)
        self.assertIn("mtime", response.json)

        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertEqual(updated_data_yaml, new_config_data_json)

    def test_update_deployment_name_patches_existing_config(self):
        response = self.app.put(
            "/api/global/deployment_name",
            data=json.dumps({"deployment_name": "Mesh Skywatch"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertEqual(
            updated_data_yaml["global"]["deployment_name"], "Mesh Skywatch"
        )
        self.assertEqual(updated_data_yaml["cameras"], self.test_config_data["cameras"])

    def test_update_site_settings_patches_public_site_flag(self):
        response = self.app.put(
            "/api/global/deployment_name",
            data=json.dumps({"deployment_name": "Mesh Skywatch", "public_site": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertEqual(
            updated_data_yaml["global"]["deployment_name"], "Mesh Skywatch"
        )
        self.assertFalse(updated_data_yaml["global"]["ui"]["public_site"])

    def test_update_camera_order_patches_global_ui(self):
        self.test_config_data["cameras"] = {
            "z-cam": {"url": "http://z"},
            "a-cam": {"url": "http://a"},
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/api/global/camera_order",
            data=json.dumps({"camera_order": ["a-cam", "missing", "z-cam", "a-cam"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertEqual(
            updated_data_yaml["global"]["ui"]["camera_order"], ["a-cam", "z-cam"]
        )

    def test_admin_logout_returns_no_store_redirect_page(self):
        response = self.app.get("/logout")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Clear-Site-Data", response.headers)

    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_add_camera_with_guided_options(self, mock_fetch):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        payload = {
            "name": "ridge-cam",
            "display_name": "Ridge Cam",
            "template_vendor": "sunba",
            "snapshot_template": "sunba-images",
            "rtsp_template": "sunba-12",
            "description": "Ridge view across the valley",
            "url": "http://camera/snapshot.jpg",
            "timeout_s": 12,
            "public": False,
            "ptz_enabled": True,
            "ptz_public": True,
            "ptz_allow_presets": True,
            "ptz_allow_manual_control": False,
            "ptz_access_level": "presets",
            "ptz_host": "192.0.2.10",
            "ptz_port": 8899,
            "ptz_username": "operator",
            "ptz_password": "secret",
            "ptz_profile_token": "profile-1",
            "ptz_capabilities": {
                "pan": True,
                "tilt": False,
                "zoom": True,
                "focus": True,
            },
            "ptz_tour_enabled": True,
            "ptz_tour_auto_resume_s": 1800,
            "ptz_presets": [
                {
                    "id": "launch",
                    "name": "Launch Pad",
                    "token": "preset-1",
                    "enabled": False,
                }
            ],
            "cache_bust": True,
            "mozjpeg_optimize": True,
            "timelapse_enabled": True,
            "work_dir_max_size_GB": 5,
            "snap_interval_enabled": True,
            "snap_interval_s": 60,
            "activity_interval_enabled": True,
            "activity_interval_s": 10,
            "ssim_enabled": True,
            "ssim_setpoint": 0.88,
            "ssim_area": "0,0,1,1",
            "sky_area_enabled": True,
            "sky_area": "0,0,1,0.35",
            "sunrise_sunset_enabled": True,
            "sunrise_sunset_interval_s": 10,
            "lat": 35.2828,
            "lon": -120.6596,
            "postprocessing": [
                {
                    "type": "timestamp",
                    "enabled": True,
                    "position": "bottom_right",
                    "size": 24,
                    "color": "white",
                    "format": "%Y-%m-%d %H:%M:%S %Z",
                }
            ],
        }

        response = self.app.post(
            "/api/camera/add",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        camera = updated_data_yaml["cameras"]["ridge-cam"]
        self.assertEqual(camera["url"], "http://camera/snapshot.jpg")
        self.assertEqual(camera["display_name"], "Ridge Cam")
        self.assertEqual(camera["template_vendor"], "sunba")
        self.assertEqual(camera["snapshot_template"], "sunba-images")
        self.assertEqual(camera["rtsp_template"], "sunba-12")
        self.assertEqual(camera["description"], "Ridge view across the valley")
        self.assertFalse(camera["public"])
        self.assertEqual(camera["visibility"], "authenticated")
        self.assertTrue(camera["ptz"]["enabled"])
        self.assertTrue(camera["ptz"]["public"])
        self.assertTrue(camera["ptz"]["allow_presets"])
        self.assertFalse(camera["ptz"]["allow_manual_control"])
        self.assertEqual(camera["ptz"]["access_level"], "presets")
        self.assertEqual(camera["ptz"]["host"], "192.0.2.10")
        self.assertEqual(camera["ptz"]["port"], 8899)
        self.assertEqual(camera["ptz"]["username"], "operator")
        self.assertEqual(camera["ptz"]["password"], "secret")
        self.assertEqual(camera["ptz"]["profile_token"], "profile-1")
        self.assertEqual(
            camera["ptz"]["capabilities"],
            {"pan": True, "tilt": False, "zoom": True, "focus": True},
        )
        self.assertTrue(camera["ptz"]["tour"]["enabled"])
        self.assertEqual(camera["ptz"]["tour"]["auto_resume_s"], 1800)
        self.assertEqual(camera["ptz"]["presets"][0]["id"], "launch")
        self.assertFalse(camera["ptz"]["presets"][0]["enabled"])
        self.assertEqual(camera["snap_interval_s"], 60)
        self.assertEqual(camera["activity_interval_s"], 10)
        self.assertEqual(camera["work_dir_max_size_GB"], 5)
        self.assertEqual(camera["ssim_area"], "0,0,1,1")
        self.assertEqual(camera["sky_area"], "0,0,1,0.35")
        self.assertTrue(camera["sunrise_sunset"]["enabled"])
        self.assertTrue(camera["timelapse_enabled"])

    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_add_camera_preserves_camera_id_capitalization(self, mock_fetch):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))

        response = self.app.post(
            "/api/camera/add",
            data=json.dumps(
                {
                    "name": "Cuesta-Peak-PTZ",
                    "display_name": "Cuesta Peak PTZ",
                    "url": "http://camera/snapshot.jpg",
                    "require_test": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertIn("Cuesta-Peak-PTZ", updated_data_yaml["cameras"])
        self.assertNotIn("cuesta-peak-ptz", updated_data_yaml["cameras"])

    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_add_camera_with_snapshot_http_auth(self, mock_fetch):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        response = self.app.post(
            "/api/camera/add",
            data=json.dumps(
                {
                    "name": "auth-cam",
                    "url": "http://camera/snapshot.jpg",
                    "snapshot_auth_type": "basic",
                    "snapshot_username": "admin",
                    "snapshot_password": "secret",
                    "require_test": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        camera = updated_data_yaml["cameras"]["auth-cam"]
        self.assertEqual(
            camera["http_auth"],
            {"type": "basic", "username": "admin", "password": "secret"},
        )
        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args.kwargs["camera_config"], camera)

    @patch("fenetre.admin_server.discover_presets")
    def test_load_ptz_presets_from_guided_fields(self, mock_discover):
        mock_discover.return_value = {
            "ok": True,
            "presets": [
                {"id": "1", "name": "Home", "token": "1"},
                {"id": "2", "name": "Launch Pad", "token": "2"},
            ],
        }

        response = self.app.post(
            "/api/camera/ptz_presets",
            data=json.dumps(
                {
                    "camera_name": "cam1",
                    "ptz_host": "192.0.2.10",
                    "ptz_port": 8899,
                    "ptz_username": "operator",
                    "ptz_password": "secret",
                    "ptz_profile_token": "profile-1",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["count"], 2)
        self.assertEqual(response.json["presets"][0]["name"], "Home")
        camera_config = mock_discover.call_args.args[1]
        self.assertEqual(camera_config["ptz"]["password"], "secret")
        self.assertEqual(camera_config["ptz"]["profile_token"], "profile-1")

    @patch("fenetre.admin_server.discover_presets")
    def test_load_ptz_presets_reuses_existing_password_on_edit(self, mock_discover):
        self.test_config_data["cameras"]["cam1"]["ptz"] = {
            "enabled": True,
            "host": "192.0.2.10",
            "port": 8899,
            "username": "operator",
            "password": "existing-secret",
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)
        mock_discover.return_value = {"ok": True, "presets": []}

        response = self.app.post(
            "/api/camera/ptz_presets",
            data=json.dumps(
                {
                    "camera_name": "cam1",
                    "ptz_host": "192.0.2.10",
                    "ptz_port": 8899,
                    "ptz_username": "operator",
                    "ptz_password": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        camera_config = mock_discover.call_args.args[1]
        self.assertEqual(camera_config["ptz"]["password"], "existing-secret")

    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_snapshot_test_respects_cache_bust_false(self, mock_fetch):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))

        response = self.app.post(
            "/api/camera/test_snapshot",
            data=json.dumps(
                {
                    "url": "http://camera/cgi-bin/snapshot.cgi?chn=0&u=admin&p=pw",
                    "timeout_s": 12,
                    "cache_bust": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_fetch.assert_called_once()
        self.assertFalse(mock_fetch.call_args.kwargs["cache_bust"])

    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_snapshot_test_uses_snapshot_auth_fields(self, mock_fetch):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))

        response = self.app.post(
            "/api/camera/test_snapshot",
            data=json.dumps(
                {
                    "url": "http://camera/images/snapshot.jpg",
                    "snapshot_auth_type": "digest",
                    "snapshot_username": "admin",
                    "snapshot_password": "secret",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        camera_config = mock_fetch.call_args.kwargs["camera_config"]
        self.assertEqual(
            camera_config["http_auth"],
            {"type": "digest", "username": "admin", "password": "secret"},
        )

    @patch("fenetre.admin_server.requests.put")
    @patch("fenetre.admin_server._fetch_local_command_bytes")
    def test_add_camera_with_rtsp_capture_source(self, mock_fetch, mock_go2rtc_put):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        mock_go2rtc_put.return_value.status_code = 200
        mock_go2rtc_put.return_value.raise_for_status.return_value = None
        response = self.app.post(
            "/api/camera/add",
            data=json.dumps(
                {
                    "name": "rtsp-cam",
                    "capture_source": "rtsp",
                    "rtsp_url": "rtsp://admin:secret@camera:554/11",
                    "require_test": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        camera = updated_data_yaml["cameras"]["rtsp-cam"]
        self.assertEqual(camera["rtsp_url"], "rtsp://admin:secret@camera:554/11")
        self.assertIn("ffmpeg", camera["local_command"])
        self.assertIn("-allowed_media_types video", camera["local_command"])
        self.assertIn("-an -map 0:v:0", camera["local_command"])
        self.assertTrue(updated_data_yaml["global"]["go2rtc"]["enabled"])
        self.assertTrue(response.json["go2rtc"]["api_synced"])
        mock_go2rtc_put.assert_called_once()
        self.assertEqual(
            mock_go2rtc_put.call_args.kwargs["params"]["name"], "fenetre_rtsp-cam"
        )
        self.assertEqual(
            mock_go2rtc_put.call_args.kwargs["params"]["src"],
            "ffmpeg:rtsp://admin:secret@camera:554/11#video=copy#timeout=30",
        )
        mock_fetch.assert_called_once_with(
            camera["local_command"], timeout_s=camera["timeout_s"]
        )

    @patch("fenetre.admin_server.requests.put")
    def test_add_snapshot_camera_preserves_rtsp_live_view_url(self, mock_go2rtc_put):
        mock_go2rtc_put.return_value.status_code = 200
        mock_go2rtc_put.return_value.raise_for_status.return_value = None
        self.test_config_data["global"] = {"go2rtc": {"enabled": False}}
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.post(
            "/api/camera/add",
            data=json.dumps(
                {
                    "name": "snapshot-live-cam",
                    "capture_source": "snapshot",
                    "url": "http://camera/snapshot.jpg",
                    "rtsp_url": "rtsp://admin:secret@camera:554/11",
                    "go2rtc_enabled": True,
                    "go2rtc_rtsp_transport": "udp",
                    "go2rtc_video_mode": "h264",
                    "require_test": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        camera = updated_data_yaml["cameras"]["snapshot-live-cam"]
        self.assertEqual(camera["url"], "http://camera/snapshot.jpg")
        self.assertEqual(camera["rtsp_url"], "rtsp://admin:secret@camera:554/11")
        self.assertEqual(camera["go2rtc_rtsp_transport"], "udp")
        self.assertEqual(camera["go2rtc_video_mode"], "h264")
        self.assertNotIn("local_command", camera)
        self.assertTrue(updated_data_yaml["global"]["go2rtc"]["enabled"])
        mock_go2rtc_put.assert_called_once()
        self.assertEqual(
            mock_go2rtc_put.call_args.kwargs["params"]["src"],
            "ffmpeg:rtsp://admin:secret@camera:554/11#video=h264#input=rtsp/udp#timeout=30",
        )

    @patch("fenetre.admin_server.requests.put")
    @patch("fenetre.admin_server._fetch_local_command_bytes")
    def test_add_camera_can_disable_go2rtc_live_view(self, mock_fetch, mock_go2rtc_put):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        response = self.app.post(
            "/api/camera/add",
            data=json.dumps(
                {
                    "name": "disabled-live-cam",
                    "capture_source": "rtsp",
                    "rtsp_url": "rtsp://admin:secret@camera:554/11",
                    "go2rtc_enabled": False,
                    "require_test": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        camera = updated_data_yaml["cameras"]["disabled-live-cam"]
        self.assertFalse(camera["go2rtc_enabled"])
        self.assertNotIn("go2rtc", updated_data_yaml.get("global", {}))
        self.assertFalse(response.json["go2rtc"]["enabled"])
        mock_go2rtc_put.assert_not_called()

    @patch("fenetre.admin_server.requests.delete")
    def test_sync_go2rtc_removes_last_disabled_live_view_stream(self, mock_delete):
        mock_delete.return_value.status_code = 200
        output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".yaml")
        output_file.write(b"streams:\n  fenetre_cam1: rtsp://old\n")
        output_file.close()
        old_output = os.environ.get("FENETRE_GO2RTC_CONFIG")
        os.environ["FENETRE_GO2RTC_CONFIG"] = output_file.name
        previous_config = {
            "global": {"go2rtc": {"enabled": True, "api_listen": ":1984"}},
            "cameras": {"cam1": {"rtsp_url": "rtsp://admin:secret@camera/11"}},
        }
        current_config = {
            "global": {"go2rtc": {"enabled": True, "api_listen": ":1984"}},
            "cameras": {
                "cam1": {
                    "rtsp_url": "rtsp://admin:secret@camera/11",
                    "go2rtc_enabled": False,
                }
            },
        }

        try:
            result = _sync_go2rtc_runtime(current_config, previous_config)
            removed_config_file = not os.path.exists(output_file.name)
        finally:
            if old_output is None:
                os.environ.pop("FENETRE_GO2RTC_CONFIG", None)
            else:
                os.environ["FENETRE_GO2RTC_CONFIG"] = old_output
            if os.path.exists(output_file.name):
                os.unlink(output_file.name)

        self.assertFalse(result["enabled"])
        self.assertEqual(result["streams"], [])
        self.assertEqual(result["removed_streams"], ["fenetre_cam1"])
        self.assertTrue(result["api_synced"])
        self.assertTrue(removed_config_file)
        mock_delete.assert_called_once()
        self.assertEqual(
            mock_delete.call_args.args[0], "http://127.0.0.1:1984/api/streams"
        )
        self.assertEqual(
            mock_delete.call_args.kwargs["params"], {"src": "fenetre_cam1"}
        )

    @patch("fenetre.admin_server._start_go2rtc_if_needed")
    @patch("fenetre.admin_server.requests.put")
    def test_sync_go2rtc_warning_redacts_stream_credentials(self, mock_put, mock_start):
        mock_put.side_effect = requests.RequestException(
            "PUT failed for http://127.0.0.1:1984/api/streams?"
            "src=rtsp://admin:secret@camera.local/11&password=hidden"
        )
        mock_start.return_value = "go2rtc binary was not found in PATH."
        config = {
            "global": {"go2rtc": {"enabled": True, "api_listen": ":1984"}},
            "cameras": {"cam1": {"rtsp_url": "rtsp://admin:secret@camera.local/11"}},
        }

        result = _sync_go2rtc_runtime(config)

        self.assertFalse(result["api_synced"])
        self.assertIn("go2rtc API sync failed", result["warning"])
        self.assertIn("src=REDACTED", result["warning"])
        self.assertIn("password=REDACTED", result["warning"])
        self.assertNotIn("secret", result["warning"])
        self.assertNotIn("admin%3Asecret", result["warning"])
        self.assertNotIn("hidden", result["warning"])

    @patch("fenetre.admin_server.requests.get")
    def test_go2rtc_status_reports_internal_api_health(self, mock_get):
        response_mock = mock_get.return_value
        response_mock.status_code = 200
        response_mock.raise_for_status.return_value = None
        self.test_config_data = {
            "global": {
                "go2rtc": {
                    "enabled": True,
                    "base_url": "http://camera-host:1984",
                    "api_listen": ":1984",
                }
            },
            "cameras": {"cam1": {"rtsp_url": "rtsp://admin:secret@camera.local/11"}},
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.get("/api/go2rtc/status")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["configured_enabled"])
        self.assertTrue(response.json["runtime_enabled"])
        self.assertTrue(response.json["api_reachable"])
        self.assertEqual(response.json["api_base"], "http://127.0.0.1:1984")
        self.assertEqual(response.json["streams"], ["fenetre_cam1"])
        mock_get.assert_called_once_with("http://127.0.0.1:1984/api/streams", timeout=3)

    def test_go2rtc_status_reports_same_host_fallback_when_base_url_is_empty(self):
        self.test_config_data = {
            "global": {
                "go2rtc": {
                    "enabled": True,
                    "base_url": "",
                    "api_listen": ":1984",
                }
            },
            "cameras": {"cam1": {"rtsp_url": "rtsp://admin:secret@camera.local/11"}},
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        with patch("fenetre.admin_server.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("connection refused")
            response = self.app.get("/api/go2rtc/status")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["base_url_configured"])
        self.assertTrue(response.json["same_host_fallback_enabled"])
        self.assertEqual(response.json["same_host_port"], 1984)
        self.assertFalse(response.json["api_reachable"])
        self.assertNotIn("base_url is empty", response.json["warning"])
        self.assertEqual(response.json["api_error"], "connection refused")

    def test_apply_camera_image_profile_dry_run(self):
        self.test_config_data["cameras"]["cam1"]["image_profiles"] = {
            "enabled": True,
            "host": "camera.local",
            "profiles": {
                "launch": {
                    "actions": [{"url": "http://{host}/image/launch", "method": "POST"}]
                }
            },
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.post(
            "/api/camera/image_profile",
            data=json.dumps({"camera": "cam1", "profile": "launch", "dry_run": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["dry_run"])
        self.assertEqual(
            response.json["actions"][0]["url"], "http://camera.local/image/launch"
        )

    def test_launch_preview_and_run_due_dry_run(self):
        self.test_config_data["global"] = {
            "work_dir": tempfile.gettempdir(),
            "launch_workflow": {
                "enabled": True,
                "dry_run": True,
                "default_pre_seconds": 60,
                "default_post_seconds": 120,
                "lookahead_hours": 1000000,
                "schedule_events": [
                    {
                        "id": "launch-1",
                        "name": "Falcon 9",
                        "net": "2099-07-21T12:00:00Z",
                        "provider": "SpaceX",
                        "location": "Vandenberg",
                    }
                ],
                "plans": {
                    "vandenberg": {
                        "match": {"providers": ["SpaceX"]},
                        "cameras": {"cam1": {"preset": "launch"}},
                    }
                },
            },
        }
        self.test_config_data["cameras"]["cam1"]["ptz"] = {
            "enabled": True,
            "host": "camera.local",
            "username": "admin",
            "password": "secret",
            "presets": [{"id": "launch", "name": "Launch", "token": "1"}],
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        preview = self.app.get("/api/launches/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json["events"][0]["plans"][0]["id"], "vandenberg")
        self.assertEqual(
            preview.json["events"][0]["plans"][0]["camera_details"][0]["name"],
            "cam1",
        )

        unsaved_config = {
            "global": {
                "launch_workflow": {
                    "enabled": True,
                    "dry_run": True,
                    "lookahead_hours": 1000000,
                    "schedule_events": [
                        {
                            "id": "launch-2",
                            "name": "Unsaved Launch",
                            "net": "2099-07-21T12:00:00Z",
                            "provider": "SpaceX",
                            "location": "Vandenberg",
                        }
                    ],
                    "plans": {
                        "unsaved": {
                            "match": {"providers": ["SpaceX"]},
                            "cameras": {"cam1": {"preset": "launch"}},
                        }
                    },
                }
            },
            "cameras": self.test_config_data["cameras"],
        }
        unsaved_preview = self.app.post(
            "/api/launches/preview",
            data=json.dumps({"config": unsaved_config}),
            content_type="application/json",
        )
        self.assertEqual(unsaved_preview.status_code, 200)
        self.assertEqual(
            unsaved_preview.json["events"][0]["name"],
            "Unsaved Launch",
        )

        with patch("fenetre.admin_server.run_due_launch_actions") as mock_run_due:
            mock_run_due.return_value = {"ok": True, "enabled": True, "actions": []}
            response = self.app.post(
                "/api/launches/run_due",
                data=json.dumps({"dry_run": True}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        mock_run_due.assert_called_once()

    @patch("fenetre.admin_server._fetch_local_command_bytes")
    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_snapshot_test_checks_live_rtsp_urls(self, mock_snapshot, mock_stream):
        mock_snapshot.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        mock_stream.side_effect = [
            (b"fullstreamjpeg", "image/jpeg", (1920, 1080)),
            (b"streamjpeg", "image/jpeg", (640, 360)),
        ]

        response = self.app.post(
            "/api/camera/test_snapshot",
            data=json.dumps(
                {
                    "url": "http://camera/images/snapshot.jpg",
                    "capture_source": "snapshot",
                    "rtsp_url": "rtsp://admin:secret@camera:554/11",
                    "ptz_rtsp_url": "rtsp://admin:secret@camera:554/12",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["stream_tests"],
            [
                {
                    "name": "RTSP live view",
                    "width": 1920,
                    "height": 1080,
                    "bytes": 14,
                },
                {"name": "PTZ live RTSP", "width": 640, "height": 360, "bytes": 10},
            ],
        )
        stream_commands = [call.args[0] for call in mock_stream.call_args_list]
        self.assertIn("rtsp://admin:secret@camera:554/11", stream_commands[0])
        self.assertIn("rtsp://admin:secret@camera:554/12", stream_commands[1])

    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_add_camera_allows_blank_description(self, mock_fetch):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        response = self.app.post(
            "/api/camera/add",
            data=json.dumps(
                {
                    "name": "blank-desc",
                    "description": "",
                    "url": "http://camera/snapshot.jpg",
                    "require_test": False,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertNotIn("description", updated_data_yaml["cameras"]["blank-desc"])

    def test_update_camera_reuses_guided_form_and_preserves_ptz_password(self):
        self.test_config_data["cameras"] = {
            "cam1": {
                "url": "http://old-camera/snapshot.jpg",
                "description": "Old description",
                "custom_key": "keep-me",
                "ptz": {
                    "enabled": True,
                    "host": "192.0.2.10",
                    "port": 80,
                    "username": "operator",
                    "password": "existing-secret",
                },
            }
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/api/camera/cam1",
            data=json.dumps(
                {
                    "name": "cam1",
                    "display_name": "Camera One",
                    "template_vendor": "reolink",
                    "snapshot_template": "reolink-api",
                    "rtsp_template": "reolink-main",
                    "description": "",
                    "url": "http://new-camera/snapshot.jpg",
                    "timeout_s": 20,
                    "public": True,
                    "cache_bust": True,
                    "mozjpeg_optimize": False,
                    "timelapse_enabled": True,
                    "require_test": False,
                    "ptz_enabled": True,
                    "ptz_host": "192.0.2.10",
                    "ptz_port": 8899,
                    "ptz_username": "operator",
                    "ptz_password": "",
                    "ptz_presets": [],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        camera = updated_data_yaml["cameras"]["cam1"]
        self.assertEqual(camera["url"], "http://new-camera/snapshot.jpg")
        self.assertEqual(camera["display_name"], "Camera One")
        self.assertEqual(camera["template_vendor"], "reolink")
        self.assertEqual(camera["snapshot_template"], "reolink-api")
        self.assertEqual(camera["rtsp_template"], "reolink-main")
        self.assertNotIn("description", camera)
        self.assertEqual(camera["custom_key"], "keep-me")
        self.assertEqual(camera["ptz"]["password"], "existing-secret")
        self.assertEqual(camera["ptz"]["port"], 8899)

    def test_update_camera_renames_references_and_moves_photo_directory(self):
        work_dir = tempfile.mkdtemp()
        old_camera_dir = os.path.join(work_dir, "photos", "Old-Cam", "2026-07-27")
        os.makedirs(old_camera_dir)
        with open(os.path.join(old_camera_dir, "frame.jpg"), "wb") as f:
            f.write(b"jpeg")
        with open(os.path.join(work_dir, "cameras.json"), "w") as f:
            json.dump({"cameras": [{"id": "Old-Cam", "title": "Old Cam"}]}, f)
        self.test_config_data = {
            "global": {
                "work_dir": work_dir,
                "timezone": "UTC",
                "ui": {
                    "camera_order": ["Old-Cam"],
                    "fullscreen_camera": "Old-Cam",
                },
                "launch_workflow": {
                    "enabled": True,
                    "plans": {
                        "vandenberg": {
                            "match": {"locations": ["Vandenberg"]},
                            "cameras": {"Old-Cam": {"preset": "launch"}},
                        }
                    },
                },
            },
            "users": {
                "operator": {
                    "role": "operator",
                    "password_hash": "hash",
                    "ptz_access": "manual",
                    "ptz_cameras": ["Old-Cam"],
                }
            },
            "cameras": {
                "Old-Cam": {
                    "url": "http://old-camera/snapshot.jpg",
                    "ptz": {
                        "enabled": True,
                        "host": "192.0.2.10",
                        "username": "operator",
                        "password": "secret",
                    },
                }
            },
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/api/camera/Old-Cam",
            data=json.dumps(
                {
                    "name": "New-Cam",
                    "display_name": "New Cam",
                    "capture_source": "snapshot",
                    "url": "http://old-camera/snapshot.jpg",
                    "timeout_s": 20,
                    "public": True,
                    "require_test": False,
                    "ptz_enabled": True,
                    "ptz_host": "192.0.2.10",
                    "ptz_port": 80,
                    "ptz_username": "operator",
                    "ptz_password": "",
                    "ptz_presets": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertNotIn("Old-Cam", updated_data_yaml["cameras"])
        self.assertIn("New-Cam", updated_data_yaml["cameras"])
        self.assertEqual(updated_data_yaml["global"]["ui"]["camera_order"], ["New-Cam"])
        self.assertEqual(
            updated_data_yaml["global"]["ui"]["fullscreen_camera"], "New-Cam"
        )
        self.assertEqual(
            updated_data_yaml["users"]["operator"]["ptz_cameras"], ["New-Cam"]
        )
        self.assertIn(
            "New-Cam",
            updated_data_yaml["global"]["launch_workflow"]["plans"]["vandenberg"][
                "cameras"
            ],
        )
        self.assertFalse(os.path.exists(os.path.join(work_dir, "photos", "Old-Cam")))
        self.assertTrue(
            os.path.exists(
                os.path.join(work_dir, "photos", "New-Cam", "2026-07-27", "frame.jpg")
            )
        )
        with open(os.path.join(work_dir, "cameras.json"), "r") as f:
            cameras_json = json.load(f)
        camera_ids = [camera["id"] for camera in cameras_json["cameras"]]
        self.assertEqual(camera_ids, ["New-Cam"])

    @patch("os.kill")
    def test_update_camera_signals_runtime_reload_when_enabled(self, mock_kill):
        with open(self.temp_pid_file.name, "w") as f:
            f.write("12345")
        flask_app.config["FENETRE_RELOAD_ON_CONFIG_WRITE"] = True

        response = self.app.put(
            "/api/camera/cam1",
            data=json.dumps(
                {
                    "name": "cam1",
                    "capture_source": "snapshot",
                    "url": "http://localhost",
                    "timeout_s": 20,
                    "public": True,
                    "require_test": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["runtime_reload"]["ok"])
        self.assertEqual(response.json["runtime_reload"]["pid"], 12345)
        mock_kill.assert_called_once_with(12345, signal.SIGHUP)

    @patch("fenetre.admin_server.requests.put")
    def test_update_camera_to_rtsp_capture_removes_stale_snapshot_fields(
        self, mock_go2rtc_put
    ):
        mock_go2rtc_put.return_value.status_code = 200
        mock_go2rtc_put.return_value.raise_for_status.return_value = None
        self.test_config_data["cameras"] = {
            "cam1": {
                "url": "http://old-camera/snapshot.jpg",
                "http_auth": {
                    "type": "basic",
                    "username": "admin",
                    "password": "old-secret",
                },
            }
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/api/camera/cam1",
            data=json.dumps(
                {
                    "name": "cam1",
                    "capture_source": "rtsp",
                    "url": "http://CAMERA_IP:HTTP_PORT/images/snapshot.jpg",
                    "snapshot_username": "admin",
                    "snapshot_password": "wrong-place",
                    "rtsp_url": "rtsp://admin:secret@camera:554/11",
                    "timeout_s": 20,
                    "public": True,
                    "require_test": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        camera = updated_data_yaml["cameras"]["cam1"]
        self.assertNotIn("url", camera)
        self.assertNotIn("http_auth", camera)
        self.assertEqual(camera["rtsp_url"], "rtsp://admin:secret@camera:554/11")
        self.assertIn("-allowed_media_types video", camera["local_command"])
        self.assertIn("-an -map 0:v:0", camera["local_command"])

    @patch("fenetre.admin_server.requests.put")
    def test_update_camera_rebuilds_public_metadata_when_work_dir_is_set(
        self, mock_go2rtc_put
    ):
        mock_go2rtc_put.return_value.status_code = 200
        mock_go2rtc_put.return_value.raise_for_status.return_value = None
        work_dir = tempfile.mkdtemp()
        self.test_config_data["global"] = {
            "work_dir": work_dir,
            "timezone": "UTC",
            "go2rtc": {"enabled": True},
        }
        self.test_config_data["cameras"] = {"cam1": {"url": "http://old"}}
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/api/camera/cam1",
            data=json.dumps(
                {
                    "name": "cam1",
                    "capture_source": "rtsp",
                    "rtsp_url": "rtsp://admin:secret@camera:554/11",
                    "timeout_s": 20,
                    "public": True,
                    "require_test": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["cameras_json"]["ok"])
        self.assertTrue(os.path.exists(os.path.join(work_dir, "cameras.json")))

    def test_storage_summary_reports_total_and_dry_run(self):
        work_dir = tempfile.mkdtemp()
        photos_dir = os.path.join(work_dir, "photos", "cam1")
        os.makedirs(photos_dir)
        with open(os.path.join(photos_dir, "frame.jpg"), "wb") as f:
            f.write(b"x" * 2048)
        self.test_config_data["global"] = {
            "work_dir": work_dir,
            "storage_management": {
                "enabled": True,
                "dry_run": True,
                "work_dir_max_size_GB": 50,
                "camera_max_size_GB": 5,
            },
        }
        self.test_config_data["cameras"] = {"cam1": {"url": "http://localhost"}}
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.get("/api/storage/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["work_dir"], work_dir)
        self.assertTrue(response.json["enabled"])
        self.assertTrue(response.json["dry_run"])
        self.assertEqual(response.json["limit_GB"], 50)
        self.assertEqual(response.json["cameras"][0]["name"], "cam1")
        self.assertEqual(response.json["cameras"][0]["limit_GB"], 5)

    def test_user_management_crud(self):
        self.test_config_data["cameras"]["cam1"]["ptz"] = {"enabled": True}
        self.test_config_data["cameras"]["fixed-cam"] = {"url": "http://fixed"}
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        create = self.app.post(
            "/api/users",
            data=json.dumps(
                {
                    "username": "operator",
                    "password": "secret",
                    "role": "operator",
                    "disabled": False,
                    "ptz_access": "manual",
                    "ptz_cameras": ["cam1", "fixed-cam"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 200)

        listed = self.app.get("/api/users")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json["users"][0]["username"], "operator")
        self.assertTrue(listed.json["users"][0]["has_password"])
        self.assertEqual(listed.json["users"][0]["ptz_access"], "manual")
        self.assertEqual(listed.json["users"][0]["ptz_cameras"], ["cam1"])
        self.assertEqual(listed.json["cameras"], ["cam1"])

        delete = self.app.delete("/api/users/operator")
        self.assertEqual(delete.status_code, 200)
        listed_again = self.app.get("/api/users")
        self.assertEqual(listed_again.json["users"], [])

    def test_list_users_only_returns_ptz_capable_cameras(self):
        self.test_config_data["cameras"] = {
            "fixed-cam": {"url": "http://fixed"},
            "ptz-cam": {"url": "http://ptz", "ptz": {"enabled": True}},
            "disabled-ptz": {"url": "http://off", "ptz": {"enabled": False}},
            "zoom-cam": {
                "url": "http://zoom",
                "ptz": {"enabled": True, "capabilities": {"pan": False, "tilt": False}},
            },
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        listed = self.app.get("/api/users")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json["cameras"], ["ptz-cam", "zoom-cam"])

    def test_list_users_bootstraps_default_admin_user(self):
        listed = self.app.get("/api/users")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json["users"]), 1)
        self.assertEqual(listed.json["users"][0]["username"], "admin")
        self.assertEqual(listed.json["users"][0]["role"], "superadmin")
        self.assertTrue(listed.json["users"][0]["has_password"])

        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertIn("admin", updated_data_yaml["users"])
        self.assertNotIn("password", updated_data_yaml["users"]["admin"])
        self.assertIn("password_hash", updated_data_yaml["users"]["admin"])

    def test_default_admin_bootstrap_falls_back_when_atomic_replace_is_busy(self):
        def busy_replace(src, dst):
            raise OSError(errno.EBUSY, "Device or resource busy")

        with patch("fenetre.auth.os.replace", side_effect=busy_replace):
            created = ensure_default_admin_user(self.temp_config_file.name)

        self.assertTrue(created)
        self.assertFalse(os.path.exists(f"{self.temp_config_file.name}.tmp"))
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertIn("admin", updated_data_yaml["users"])
        self.assertTrue(
            authenticate_config_user(self.temp_config_file.name, "admin", "admin")
        )

    def test_default_admin_bootstrap_does_not_restore_removed_admin(self):
        self.test_config_data["users"] = {}
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        created = ensure_default_admin_user(self.temp_config_file.name)

        self.assertFalse(created)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertEqual(updated_data_yaml["users"], {})

    def test_reset_admin_user_restores_admin_manually(self):
        self.test_config_data["users"] = {}
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        reset_admin_user(self.temp_config_file.name, "manual-reset")

        self.assertTrue(
            authenticate_config_user(
                self.temp_config_file.name, "admin", "manual-reset"
            )
        )
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertIn("admin", updated_data_yaml["users"])
        self.assertEqual(updated_data_yaml["users"]["admin"]["role"], "superadmin")

    def test_superadmin_is_required_to_manage_users_after_bootstrap(self):
        self.test_config_data["users"] = {
            "root": {
                "role": "superadmin",
                "password_hash": hash_password("rootpw"),
            },
            "admin": {
                "role": "admin",
                "password_hash": hash_password("adminpw"),
                "ptz_access": "manual",
                "ptz_cameras": ["cam1"],
            },
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = True

        admin_token = base64.b64encode(b"admin:adminpw").decode("ascii")
        denied = self.app.post(
            "/api/users",
            data=json.dumps({"username": "operator", "role": "operator"}),
            content_type="application/json",
            headers={"Authorization": f"Basic {admin_token}"},
        )
        self.assertEqual(denied.status_code, 403)

        root_token = base64.b64encode(b"root:rootpw").decode("ascii")
        allowed = self.app.post(
            "/api/users",
            data=json.dumps({"username": "operator", "role": "operator"}),
            content_type="application/json",
            headers={"Authorization": f"Basic {root_token}"},
        )
        self.assertEqual(allowed.status_code, 200)

    def test_non_superadmin_cannot_change_another_users_password(self):
        self.test_config_data["users"] = {
            "admin": {
                "role": "admin",
                "password_hash": hash_password("adminpw"),
                "ptz_access": "admin",
                "ptz_cameras": [],
            },
            "operator": {
                "role": "operator",
                "password_hash": hash_password("oldpw"),
                "ptz_access": "presets",
                "ptz_cameras": [],
            },
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = True

        admin_token = base64.b64encode(b"admin:adminpw").decode("ascii")
        denied = self.app.post(
            "/api/users",
            data=json.dumps(
                {
                    "username": "operator",
                    "password": "new-secret",
                    "role": "operator",
                }
            ),
            content_type="application/json",
            headers={"Authorization": f"Basic {admin_token}"},
        )

        self.assertEqual(denied.status_code, 403)
        self.assertTrue(
            authenticate_config_user_record(
                self.temp_config_file.name, "operator", "oldpw"
            )
        )

    def test_current_admin_can_change_own_password(self):
        self.test_config_data["users"] = {
            "admin": {
                "role": "superadmin",
                "password_hash": hash_password("old-secret"),
                "ptz_access": "admin",
                "ptz_cameras": [],
            }
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = True

        token = base64.b64encode(b"admin:old-secret").decode("ascii")
        changed = self.app.post(
            "/api/users/password",
            data=json.dumps(
                {
                    "current_password": "old-secret",
                    "new_password": "new-secret",
                }
            ),
            content_type="application/json",
            headers={"Authorization": f"Basic {token}"},
        )

        self.assertEqual(changed.status_code, 200)
        self.assertFalse(
            authenticate_config_user_record(
                self.temp_config_file.name, "admin", "old-secret"
            )
        )
        self.assertTrue(
            authenticate_config_user_record(
                self.temp_config_file.name, "admin", "new-secret"
            )
        )

    def test_ptz_lock_endpoint_updates_runtime_lock(self):
        response = self.app.post(
            "/api/ptz/lock",
            data=json.dumps(
                {
                    "camera": "cam1",
                    "locked": True,
                    "reason": "Launch framing",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["lock"]["locked"])
        self.assertEqual(response.json["lock"]["reason"], "Launch framing")

    def test_admin_auth_requires_basic_credentials(self):
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = True

        unauthenticated = self.app.get("/config")
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertIn("Basic", unauthenticated.headers["WWW-Authenticate"])

        bad_token = base64.b64encode(b"admin:wrong").decode("ascii")
        bad = self.app.get("/config", headers={"Authorization": f"Basic {bad_token}"})
        self.assertEqual(bad.status_code, 401)

        good_token = base64.b64encode(b"admin:admin").decode("ascii")
        good = self.app.get("/config", headers={"Authorization": f"Basic {good_token}"})
        self.assertEqual(good.status_code, 200)

    def test_admin_auth_uses_changed_config_password(self):
        ensure_default_admin_user(self.temp_config_file.name)
        change = self.app.post(
            "/api/users",
            data=json.dumps(
                {
                    "username": "admin",
                    "password": "changed",
                    "role": "admin",
                    "disabled": False,
                    "ptz_access": "admin",
                    "ptz_cameras": [],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(change.status_code, 200)
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = True

        old_token = base64.b64encode(b"admin:admin").decode("ascii")
        old_response = self.app.get(
            "/config", headers={"Authorization": f"Basic {old_token}"}
        )
        self.assertEqual(old_response.status_code, 401)

        new_token = base64.b64encode(b"admin:changed").decode("ascii")
        new_response = self.app.get(
            "/config", headers={"Authorization": f"Basic {new_token}"}
        )
        self.assertEqual(new_response.status_code, 200)
        self.assertTrue(
            authenticate_config_user(self.temp_config_file.name, "admin", "changed")
        )

    def test_admin_auth_accepts_superuser_role_alias(self):
        self.test_config_data["users"] = {
            "normal": {
                "role": "superuser",
                "password_hash": hash_password("secret"),
                "ptz_access": "admin",
                "ptz_cameras": [],
            }
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = True

        token = base64.b64encode(b"normal:secret").decode("ascii")
        response = self.app.get("/config", headers={"Authorization": f"Basic {token}"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            authenticate_config_user(self.temp_config_file.name, "normal", "secret")
        )
        user = authenticate_config_user_record(
            self.temp_config_file.name, "normal", "secret"
        )
        self.assertEqual(user["role"], "superadmin")

    def test_update_config_invalid_json(self):
        invalid_json_string = '{"global": {"setting": "value"}, "broken": [1,2,'
        response = self.app.put(
            "/config", data=invalid_json_string, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON format in request body", response.json["error"])

    def test_update_config_empty_body(self):
        response = self.app.put("/config", data="", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Invalid JSON format in request body or empty body.", response.json["error"]
        )

    def test_update_config_not_dict_root_json(self):
        invalid_json_list = json.dumps(["item1", "item2"])
        response = self.app.put(
            "/config", data=invalid_json_list, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Root element of the configuration must be a dictionary",
            response.json["error"],
        )

    def test_update_config_wrong_content_type(self):
        new_config_data_json = {"global": {"setting": "new_value"}}
        response = self.app.put(
            "/config", data=json.dumps(new_config_data_json), content_type="text/plain"
        )
        self.assertEqual(response.status_code, 415)
        self.assertIn("Request body must be JSON", response.json["error"])

    def test_update_config_preserves_users_when_omitted(self):
        self.test_config_data["users"] = {
            "operator": {
                "role": "operator",
                "ptz_access": "manual",
                "ptz_cameras": ["cam1"],
                "password_hash": "hash",
            }
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/config",
            data=json.dumps(
                {
                    "global": {"setting": "new_value"},
                    "cameras": {"cam1": {"url": "http://localhost"}},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertIn("users", updated_data_yaml)
        self.assertEqual(
            updated_data_yaml["users"]["operator"]["ptz_cameras"], ["cam1"]
        )

    def test_update_config_preserves_users_when_submitted_users_are_stale(self):
        self.test_config_data["users"] = {
            "operator": {
                "role": "operator",
                "ptz_access": "manual",
                "ptz_cameras": ["cam1"],
                "password_hash": "hash",
            }
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/config",
            data=json.dumps(
                {
                    "global": {"setting": "new_value"},
                    "cameras": {"cam1": {"url": "http://localhost"}},
                    "users": {},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertIn("operator", updated_data_yaml["users"])
        self.assertEqual(
            updated_data_yaml["users"]["operator"]["password_hash"], "hash"
        )
        self.assertEqual(
            updated_data_yaml["users"]["operator"]["ptz_cameras"], ["cam1"]
        )

    def test_update_config_preserves_user_settings_from_stale_form(self):
        self.test_config_data["users"] = {
            "operator": {
                "role": "operator",
                "ptz_access": "manual",
                "ptz_cameras": ["cam1"],
                "password_hash": "hash",
            },
            "viewer": {
                "role": "viewer",
                "ptz_access": "none",
                "ptz_cameras": [],
                "password_hash": "viewer-hash",
            },
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/config",
            data=json.dumps(
                {
                    "global": {"setting": "new_value"},
                    "cameras": {"cam1": {"url": "http://localhost"}},
                    "users": {
                        "operator": {
                            "role": "admin",
                            "ptz_access": "none",
                            "ptz_cameras": [],
                            "disabled": True,
                        }
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertEqual(updated_data_yaml["users"]["operator"]["role"], "operator")
        self.assertEqual(updated_data_yaml["users"]["operator"]["ptz_access"], "manual")
        self.assertEqual(
            updated_data_yaml["users"]["operator"]["ptz_cameras"], ["cam1"]
        )
        self.assertNotIn("disabled", updated_data_yaml["users"]["operator"])
        self.assertEqual(
            updated_data_yaml["users"]["operator"]["password_hash"], "hash"
        )
        self.assertIn("viewer", updated_data_yaml["users"])

    def test_update_config_removes_deleted_camera_from_user_access(self):
        self.test_config_data["users"] = {
            "operator": {
                "role": "operator",
                "ptz_access": "manual",
                "ptz_cameras": ["cam1", "missing-cam"],
                "password_hash": "hash",
            }
        }
        with open(self.temp_config_file.name, "w") as f:
            yaml.safe_dump(self.test_config_data, f)

        response = self.app.put(
            "/config",
            data=json.dumps({"global": {"setting": "value"}, "cameras": {}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["user_camera_access_removed"],
            {"operator": ["cam1", "missing-cam"]},
        )
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertEqual(updated_data_yaml["users"]["operator"]["ptz_cameras"], [])

    @patch("os.kill")
    def test_reload_config_success(self, mock_kill):
        # Ensure PID file exists and has a valid PID
        with open(self.temp_pid_file.name, "w") as f:
            f.write("12345")  # Dummy PID
        flask_app.config["FENETRE_PID_FILE_PATH"] = (
            self.temp_pid_file.name
        )  # Ensure app uses this

        response = self.app.post("/config/reload")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Reload signal sent to process 12345", response.json["message"])
        mock_kill.assert_called_once_with(12345, signal.SIGHUP)

    @patch("os.kill")
    def test_reload_config_pid_file_not_found(self, mock_kill):
        flask_app.config["FENETRE_PID_FILE_PATH"] = "/tmp/non_existent_pid_file.pid"

    @patch("os.kill", side_effect=ProcessLookupError)
    def test_reload_config_process_not_found(self, mock_kill):
        with open(self.temp_pid_file.name, "w") as f:
            f.write("54321")
        flask_app.config["FENETRE_PID_FILE_PATH"] = self.temp_pid_file.name

        response = self.app.post("/config/reload")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Process with PID read from", response.json["error"])
        self.assertIn("not found", response.json["error"])
        mock_kill.assert_called_once_with(54321, signal.SIGHUP)

    @patch("os.kill")
    def test_reload_config_pid_file_empty(self, mock_kill):
        with open(self.temp_pid_file.name, "w") as f:
            f.write("")  # Empty PID file
        flask_app.config["FENETRE_PID_FILE_PATH"] = self.temp_pid_file.name

        response = self.app.post("/config/reload")
        self.assertEqual(response.status_code, 500)
        self.assertIn("PID file is empty", response.json["error"])
        mock_kill.assert_not_called()

    @patch("os.kill")
    def test_reload_config_pid_file_invalid_pid(self, mock_kill):
        with open(self.temp_pid_file.name, "w") as f:
            f.write("not_a_pid")  # Invalid PID
        flask_app.config["FENETRE_PID_FILE_PATH"] = self.temp_pid_file.name

        response = self.app.post("/config/reload")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Invalid PID found", response.json["error"])
        mock_kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
