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

import yaml

from fenetre.auth import (
    authenticate_config_user,
    ensure_default_admin_user,
    hash_password,
    reset_admin_user,
)
from fenetre.admin_server import app as flask_app
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
        flask_app.config.pop("FENETRE_ADMIN_USERNAME", None)
        flask_app.config.pop("FENETRE_ADMIN_PASSWORD", None)

    def tearDown(self):
        flask_app.config["FENETRE_ADMIN_AUTH_ENABLED"] = False
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

    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_add_camera_with_guided_options(self, mock_fetch):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        payload = {
            "name": "ridge-cam",
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
            "ptz_presets": [
                {"id": "launch", "name": "Launch Pad", "token": "preset-1"}
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
        self.assertEqual(camera["ptz"]["presets"][0]["id"], "launch")
        self.assertEqual(camera["snap_interval_s"], 60)
        self.assertEqual(camera["activity_interval_s"], 10)
        self.assertEqual(camera["work_dir_max_size_GB"], 5)
        self.assertEqual(camera["ssim_area"], "0,0,1,1")
        self.assertEqual(camera["sky_area"], "0,0,1,0.35")
        self.assertTrue(camera["sunrise_sunset"]["enabled"])
        self.assertTrue(camera["timelapse_enabled"])

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
        self.assertTrue(updated_data_yaml["global"]["go2rtc"]["enabled"])
        self.assertTrue(response.json["go2rtc"]["api_synced"])
        mock_go2rtc_put.assert_called_once()
        self.assertEqual(
            mock_go2rtc_put.call_args.kwargs["params"]["name"], "fenetre_rtsp-cam"
        )
        self.assertEqual(
            mock_go2rtc_put.call_args.kwargs["params"]["src"],
            "rtsp://admin:secret@camera:554/11",
        )
        mock_fetch.assert_called_once_with(
            camera["local_command"], timeout_s=camera["timeout_s"]
        )

    @patch("fenetre.admin_server._fetch_local_command_bytes")
    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_snapshot_test_checks_ptz_rtsp_url(self, mock_snapshot, mock_stream):
        mock_snapshot.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        mock_stream.return_value = (b"streamjpeg", "image/jpeg", (640, 360))

        response = self.app.post(
            "/api/camera/test_snapshot",
            data=json.dumps(
                {
                    "url": "http://camera/images/snapshot.jpg",
                    "ptz_rtsp_url": "rtsp://admin:secret@camera:554/12",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["stream_tests"],
            [{"name": "PTZ live RTSP", "width": 640, "height": 360, "bytes": 10}],
        )
        self.assertIn(
            "rtsp://admin:secret@camera:554/12", mock_stream.call_args.args[0]
        )

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
        self.assertNotIn("description", camera)
        self.assertEqual(camera["custom_key"], "keep-me")
        self.assertEqual(camera["ptz"]["password"], "existing-secret")
        self.assertEqual(camera["ptz"]["port"], 8899)

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
        create = self.app.post(
            "/api/users",
            data=json.dumps(
                {
                    "username": "operator",
                    "password": "secret",
                    "role": "operator",
                    "disabled": False,
                    "ptz_access": "manual",
                    "ptz_cameras": ["cam1"],
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

        delete = self.app.delete("/api/users/operator")
        self.assertEqual(delete.status_code, 200)
        listed_again = self.app.get("/api/users")
        self.assertEqual(listed_again.json["users"], [])

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

    def test_update_config_preserves_user_password_hash_from_stale_form(self):
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
                            "ptz_access": "manual",
                            "ptz_cameras": ["cam1"],
                        }
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        with open(self.temp_config_file.name, "r") as f:
            updated_data_yaml = yaml.safe_load(f)
        self.assertEqual(updated_data_yaml["users"]["operator"]["role"], "admin")
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
