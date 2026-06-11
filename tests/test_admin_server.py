import json
import os
import signal
# Add project root to allow importing admin_server
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from fenetre.admin_server import app as flask_app




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
        self.assertEqual(
            updated_data_yaml["cameras"], self.test_config_data["cameras"]
        )

    @patch("fenetre.admin_server._fetch_snapshot_bytes")
    def test_add_camera_with_guided_options(self, mock_fetch):
        mock_fetch.return_value = (b"jpeg", "image/jpeg", (1920, 1080))
        payload = {
            "name": "ridge-cam",
            "description": "Ridge Cam",
            "url": "http://camera/snapshot.jpg",
            "timeout_s": 12,
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
        self.assertEqual(camera["snap_interval_s"], 60)
        self.assertEqual(camera["activity_interval_s"], 10)
        self.assertEqual(camera["work_dir_max_size_GB"], 5)
        self.assertEqual(camera["ssim_area"], "0,0,1,1")
        self.assertEqual(camera["sky_area"], "0,0,1,0.35")
        self.assertTrue(camera["sunrise_sunset"]["enabled"])
        self.assertTrue(camera["timelapse_enabled"])

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
