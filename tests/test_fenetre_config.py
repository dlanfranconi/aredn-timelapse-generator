import os

# Add project root to allow importing fenetre
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import yaml

# Import the functions/classes to be tested
from fenetre.cameras_metadata import build_cameras_metadata, write_cameras_metadata
from fenetre.config import ConfigError, config_load
from fenetre.fenetre import _cors_allow_origin_for_request, load_and_apply_configuration
from fenetre.go2rtc import build_go2rtc_runtime_config, write_go2rtc_runtime_config


# Minimal stub for GoProUtilityThread if fenetre.py imports it and it causes issues
class MockGoProUtilityThread:
    def __init__(self, config, event):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


sys.modules["gopro_utility"] = MagicMock(GoProUtilityThread=MockGoProUtilityThread)


class FenetreConfigTestCase(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mock_work_dir = self.temp_dir.name

        # Reset fenetre's global config state before each test if necessary
        # We will re-initialize them in tests that call load_and_apply_configuration
        if "fenetre.fenetre" in sys.modules:
            fenetre_module = sys.modules["fenetre.fenetre"]
            fenetre_module.server_config = {}
            fenetre_module.cameras_config = {}
            fenetre_module.global_config = {}
            fenetre_module.sleep_intervals = {}
            fenetre_module.active_camera_threads = {}
            fenetre_module.http_server_thread_global = None
            fenetre_module.http_server_instance = None
            if (
                hasattr(fenetre_module, "exit_event") and fenetre_module.exit_event
            ):  # If it was set by a previous test
                fenetre_module.exit_event.clear()

    def tearDown(self):
        self.temp_dir.cleanup()
        # Clean up any created PID file by fenetre during tests
        pid_file_path = os.environ.get("FENETRE_PID_FILE", "fenetre.pid")
        if os.path.exists(pid_file_path):
            try:
                os.remove(pid_file_path)
            except OSError:
                pass  # Ignore if it's already gone or permissions issue in test env

    def _create_temp_config_file(self, data):
        fd, path = tempfile.mkstemp(suffix=".yaml", dir=self.temp_dir.name)
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f)
        return path

    def test_config_load_success(self):
        test_data = {
            "global": {
                "setting": "global_val",
                "work_dir": self.mock_work_dir,
                "timezone": "UTC",
            },
            "http_server": {"listen": "0.0.0.0:8080"},
            "cameras": {"cam1": {"url": "http://cam1"}},
        }
        config_path = self._create_temp_config_file(test_data)

        server_conf, cameras_conf, global_conf, admin_server_conf, _ = config_load(
            config_path
        )

        self.assertEqual(global_conf["work_dir"], self.mock_work_dir)
        self.assertEqual(global_conf["timezone"], "UTC")
        self.assertEqual(server_conf["listen"], test_data["http_server"]["listen"])
        self.assertTrue(server_conf["enabled"])
        self.assertIn("cam1", cameras_conf)
        self.assertEqual(cameras_conf["cam1"]["url"], "http://cam1")
        self.assertTrue(admin_server_conf["enabled"])

    def test_config_diff_logs_redact_camera_credentials(self):
        test_data = {
            "global": {
                "work_dir": self.mock_work_dir,
                "timezone": "UTC",
                "mqtt": {
                    "enabled": False,
                    "username": "mqtt-user",
                    "password": "mqtt-secret",
                },
            },
            "cameras": {
                "cam1": {
                    "url": (
                        "http://camera.local/cgi-bin/api.cgi?"
                        "cmd=Snap&user=admin&password=snap-secret&token=url-token"
                    ),
                    "local_command": (
                        "ffmpeg -i 'rtsp://admin:rtsp-secret@camera.local/stream' "
                        "-frames:v 1 -f image2pipe -"
                    ),
                    "http_auth": {
                        "type": "basic",
                        "username": "admin",
                        "password": "auth-secret",
                    },
                    "ptz": {
                        "enabled": True,
                        "host": "camera.local",
                        "username": "admin",
                        "password": "ptz-secret",
                    },
                    "rtsp_url": "rtsp://admin:main-secret@camera.local/main",
                    "ptz_rtsp_url": "rtsp://admin:sub-secret@camera.local/sub",
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        with self.assertLogs("fenetre.config", level="WARNING") as logs:
            config_load(config_path)
        logged = "\n".join(logs.output)

        for secret in (
            "mqtt-secret",
            "snap-secret",
            "url-token",
            "rtsp-secret",
            "auth-secret",
            "ptz-secret",
            "main-secret",
            "sub-secret",
        ):
            self.assertNotIn(secret, logged)
        self.assertIn("password=REDACTED", logged)
        self.assertIn("token=REDACTED", logged)
        self.assertIn("rtsp://REDACTED@camera.local", logged)
        self.assertIn("password: REDACTED", logged)

    def test_config_load_public_site_flag(self):
        test_data = {
            "global": {
                "work_dir": self.mock_work_dir,
                "timezone": "UTC",
                "ui": {"public_site": False},
            },
            "cameras": {"cam1": {"url": "http://cam1"}},
        }
        config_path = self._create_temp_config_file(test_data)

        _, _, global_conf, _, _ = config_load(config_path)

        self.assertFalse(global_conf["ui"]["public_site"])

    def test_config_load_launch_workflow(self):
        test_data = {
            "global": {
                "work_dir": self.mock_work_dir,
                "timezone": "UTC",
                "launch_workflow": {
                    "enabled": True,
                    "dry_run": True,
                    "refresh_interval_s": 120,
                    "schedule_events": [
                        {"name": "Mission", "net": "2026-07-21T12:00:00Z"}
                    ],
                    "plans": {
                        "vandenberg": {
                            "match": {"locations": ["Vandenberg"]},
                            "cameras": {"cam1": {"preset": "launch"}},
                        }
                    },
                },
            },
            "cameras": {"cam1": {"url": "http://cam1"}},
        }
        config_path = self._create_temp_config_file(test_data)

        _, _, global_conf, _, _ = config_load(config_path)

        launch_workflow = global_conf["launch_workflow"]
        self.assertTrue(launch_workflow["enabled"])
        self.assertEqual(launch_workflow["refresh_interval_s"], 120)
        self.assertEqual(
            launch_workflow["plans"]["vandenberg"]["cameras"]["cam1"]["preset"],
            "launch",
        )

    def test_config_load_storage_management_defaults(self):
        test_data = {
            "global": {
                "work_dir": self.mock_work_dir,
                "timezone": "UTC",
                "storage_management": {"enabled": True},
            },
            "cameras": {"cam1": {"url": "http://cam1"}},
        }
        config_path = self._create_temp_config_file(test_data)

        _, _, global_conf, _, _ = config_load(config_path)

        storage_conf = global_conf["storage_management"]
        self.assertEqual(storage_conf["camera_max_size_GB"], 5)
        self.assertTrue(storage_conf["prune_snapshots_first"])

    def test_config_load_camera_unavailable_command(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "unavailable_command": "printf hello >> /tmp/cam1-unavailable",
                    "unavailable_command_timeout_s": 7,
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        self.assertEqual(
            cameras_conf["cam1"]["unavailable_command"],
            "printf hello >> /tmp/cam1-unavailable",
        )
        self.assertEqual(cameras_conf["cam1"]["unavailable_command_timeout_s"], 7)

    def test_config_load_camera_capture_failure_interval(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "capture_failure_interval_s": 300,
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        self.assertEqual(cameras_conf["cam1"]["capture_failure_interval_s"], 300)

    def test_config_load_camera_go2rtc_enabled(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "go2rtc_enabled": False,
                    "go2rtc_source_mode": "rtsp",
                    "go2rtc_rtsp_timeout_s": 45,
                    "go2rtc_rtsp_transport": "udp",
                    "go2rtc_video_mode": "h264",
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        self.assertFalse(cameras_conf["cam1"]["go2rtc_enabled"])
        self.assertEqual(cameras_conf["cam1"]["go2rtc_source_mode"], "rtsp")
        self.assertEqual(cameras_conf["cam1"]["go2rtc_rtsp_timeout_s"], 45)
        self.assertEqual(cameras_conf["cam1"]["go2rtc_rtsp_transport"], "udp")
        self.assertEqual(cameras_conf["cam1"]["go2rtc_video_mode"], "h264")

    def test_config_load_camera_image_profiles(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "image_profiles": {
                        "enabled": True,
                        "vendor": "reolink",
                        "host": "cam1",
                        "mode_profiles": {"night": "low-light"},
                        "profiles": {
                            "low-light": {
                                "actions": [
                                    {
                                        "url": "http://{host}/api/night",
                                        "method": "POST",
                                    }
                                ]
                            }
                        },
                    },
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        image_profiles = cameras_conf["cam1"]["image_profiles"]
        self.assertTrue(image_profiles["enabled"])
        self.assertEqual(image_profiles["vendor"], "reolink")
        self.assertEqual(image_profiles["mode_profiles"], {"night": "low-light"})
        self.assertIn("low-light", image_profiles["profiles"])

    def test_config_load_camera_http_auth(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1/snapshot.jpg",
                    "http_auth": {
                        "type": "basic",
                        "username": "admin",
                        "password": "secret",
                    },
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        self.assertEqual(
            cameras_conf["cam1"]["http_auth"],
            {"type": "basic", "username": "admin", "password": "secret"},
        )

    def test_config_load_camera_http_auth_requires_credentials(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1/snapshot.jpg",
                    "http_auth": {"type": "basic"},
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        with self.assertRaises(ConfigError):
            config_load(config_path)

    def test_config_load_unavailable_command_timeout_requires_command(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "unavailable_command_timeout_s": 7,
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        with self.assertRaises(ConfigError):
            config_load(config_path)

    def test_config_dualstack_load_success(self):
        test_data = {
            "global": {
                "setting": "global_val",
                "work_dir": self.mock_work_dir,
                "timezone": "UTC",
            },
            "http_server": {"listen": "0.0.0.0:8080 [::]:8080"},
            "cameras": {"cam1": {"url": "http://cam1"}},
        }
        config_path = self._create_temp_config_file(test_data)

        server_conf, cameras_conf, global_conf, admin_server_conf, _ = config_load(
            config_path
        )

        self.assertEqual(global_conf["work_dir"], self.mock_work_dir)
        self.assertEqual(global_conf["timezone"], "UTC")
        self.assertEqual(server_conf["listen"], test_data["http_server"]["listen"])
        self.assertTrue(server_conf["enabled"])
        self.assertIn("cam1", cameras_conf)
        self.assertEqual(cameras_conf["cam1"]["url"], "http://cam1")
        self.assertTrue(admin_server_conf["enabled"])

    def test_config_load_http_cors_allow_origins(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "http_server": {
                "listen": "0.0.0.0:8080",
                "cors_allow_origin": "https://fenetre.cam",
                "cors_allow_origins": [
                    "https://fenetre.cam",
                    "https://dev.fenetre.cam",
                ],
            },
            "cameras": {"cam1": {"url": "http://cam1"}},
        }
        config_path = self._create_temp_config_file(test_data)

        server_conf, _, _, _, _ = config_load(config_path)

        self.assertEqual(server_conf["cors_allow_origin"], "https://fenetre.cam")
        self.assertEqual(
            server_conf["cors_allow_origins"],
            ["https://fenetre.cam", "https://dev.fenetre.cam"],
        )

    def test_cors_allow_origin_reflects_allowed_request_origin(self):
        cors_config = {
            "cors_allow_origin": "https://fenetre.cam",
            "cors_allow_origins": [
                "https://fenetre.cam",
                "https://dev.fenetre.cam",
            ],
        }

        self.assertEqual(
            _cors_allow_origin_for_request("https://dev.fenetre.cam", cors_config),
            "https://dev.fenetre.cam",
        )
        self.assertEqual(
            _cors_allow_origin_for_request("https://other.example", cors_config),
            None,
        )
        self.assertEqual(
            _cors_allow_origin_for_request(None, cors_config),
            "https://fenetre.cam",
        )

    def test_config_load_missing_sections(self):
        test_data = {
            "global": {
                "setting": "global_val",
                "work_dir": self.mock_work_dir,
                "timezone": "UTC",
            }
            # http_server and cameras are missing
        }
        config_path = self._create_temp_config_file(test_data)

        server_conf, cameras_conf, global_conf, admin_server_conf, _ = config_load(
            config_path
        )

        self.assertEqual(global_conf["timezone"], "UTC")
        self.assertEqual(global_conf["work_dir"], self.mock_work_dir)
        self.assertTrue(server_conf["enabled"])
        self.assertEqual(server_conf["listen"], "0.0.0.0:8888")
        self.assertEqual(cameras_conf, {})  # Should default to empty dict
        self.assertTrue(admin_server_conf["enabled"])
        self.assertEqual(admin_server_conf["listen"], "0.0.0.0:8889 [::]:8889")

    def test_config_load_missing_timelapse_section(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {"cam1": {"url": "http://cam1"}},
        }
        config_path = self._create_temp_config_file(test_data)

        _, _, _, _, timelapse_conf = config_load(config_path)

        self.assertEqual(timelapse_conf, {})

    def test_config_load_preserves_camera_timelapse_flags(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "legacy": {
                    "url": "http://legacy",
                    "description": "Legacy camera",
                    "disabled": True,
                    "public": False,
                    "ptz": {
                        "enabled": True,
                        "public": False,
                        "allow_presets": True,
                        "allow_manual_control": False,
                        "access_level": "presets",
                    },
                    "generate_timelapse": False,
                    "timelapse_enabled": False,
                },
                "nested": {
                    "url": "http://nested",
                    "description": "Nested camera",
                    "timelapse": {"enabled": False},
                },
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        self.assertEqual(cameras_conf["legacy"]["description"], "Legacy camera")
        self.assertTrue(cameras_conf["legacy"]["disabled"])
        self.assertFalse(cameras_conf["legacy"]["public"])
        self.assertTrue(cameras_conf["legacy"]["ptz"]["enabled"])
        self.assertFalse(cameras_conf["legacy"]["generate_timelapse"])
        self.assertFalse(cameras_conf["legacy"]["timelapse_enabled"])
        self.assertEqual(cameras_conf["nested"]["description"], "Nested camera")
        self.assertFalse(cameras_conf["nested"]["timelapse"]["enabled"])

    def test_cameras_metadata_filters_private_and_includes_description_ptz(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "public-cam": {
                    "url": "http://public",
                    "display_name": "Public Cam",
                    "description": "Ridgeline view",
                    "public": True,
                    "ptz": {
                        "enabled": True,
                        "public": True,
                        "allow_presets": True,
                        "allow_manual_control": False,
                        "host": "192.0.2.10",
                        "port": 8899,
                        "username": "operator",
                        "password": "secret",
                        "presets": [
                            {
                                "id": "launch",
                                "name": "Launch Pad",
                                "token": "preset-token",
                            }
                        ],
                    },
                },
                "private-cam": {"url": "http://private", "public": False},
            },
            {"ui": {}},
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
        )

        self.assertEqual([cam["id"] for cam in metadata["cameras"]], ["public-cam"])
        self.assertEqual([cam["title"] for cam in metadata["cameras"]], ["Public Cam"])
        public_cam = metadata["cameras"][0]
        self.assertEqual(public_cam["description"], "Ridgeline view")
        self.assertTrue(public_cam["public"])
        self.assertEqual(public_cam["visibility"], "public")
        self.assertTrue(public_cam["ptz"]["enabled"])
        self.assertTrue(public_cam["ptz"]["public"])
        self.assertTrue(public_cam["ptz"]["allow_presets"])
        self.assertFalse(public_cam["ptz"]["allow_manual_control"])
        self.assertEqual(
            public_cam["ptz"]["presets"], [{"id": "launch", "name": "Launch Pad"}]
        )
        self.assertNotIn("password", public_cam["ptz"])
        self.assertNotIn("host", public_cam["ptz"])

    def test_cameras_metadata_can_include_authenticated_only_cameras(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        cameras = {
            "public-cam": {"url": "http://public", "visibility": "public"},
            "auth-cam": {"url": "http://auth", "public": False},
            "hidden-cam": {"url": "http://hidden", "visibility": "hidden"},
        }

        public_metadata = build_cameras_metadata(
            cameras,
            {"ui": {}},
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
            include_removed=False,
        )
        private_metadata = build_cameras_metadata(
            cameras,
            {"ui": {}},
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
            include_private=True,
            include_removed=False,
        )

        self.assertEqual(
            [cam["title"] for cam in public_metadata["cameras"]], ["public-cam"]
        )
        self.assertEqual(
            [cam["title"] for cam in private_metadata["cameras"]],
            ["auth-cam", "public-cam"],
        )
        self.assertEqual(private_metadata["cameras"][0]["visibility"], "authenticated")

    def test_cameras_metadata_sorts_by_display_name_by_default(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "z-cam": {"url": "http://z", "display_name": "Zulu"},
                "a-cam": {"url": "http://a", "display_name": "Alpha"},
                "m-cam": {"url": "http://m"},
            },
            {"ui": {}},
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
            include_removed=False,
        )

        self.assertEqual(
            [cam["id"] for cam in metadata["cameras"]],
            ["a-cam", "m-cam", "z-cam"],
        )

    def test_cameras_metadata_honors_camera_order(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "z-cam": {"url": "http://z", "display_name": "Zulu"},
                "a-cam": {"url": "http://a", "display_name": "Alpha"},
                "m-cam": {"url": "http://m"},
            },
            {"ui": {"camera_order": ["z-cam"]}},
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
            include_removed=False,
        )

        self.assertEqual(
            [cam["id"] for cam in metadata["cameras"]],
            ["z-cam", "a-cam", "m-cam"],
        )

    def test_written_public_cameras_metadata_omits_go2rtc_urls(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = write_cameras_metadata(
            {
                "public-cam": {
                    "url": "http://public",
                    "rtsp_url": "rtsp://admin:secret@example.test:554/stream1",
                },
            },
            {
                "ui": {},
                "go2rtc": {
                    "enabled": True,
                    "base_url": "http://go2rtc.local:1984/",
                },
            },
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
        )

        self.assertNotIn("go2rtc", metadata["cameras"][0])

    def test_cameras_metadata_honors_explicit_go2rtc_player_template(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "North Ridge Camera": {
                    "url": "http://snapshot",
                    "rtsp_url": "rtsp://admin:secret@example.test:554/stream1",
                    "ptz_rtsp_url": "rtsp://admin:secret@example.test:554/stream2",
                },
            },
            {
                "ui": {},
                "go2rtc": {
                    "enabled": True,
                    "base_url": "http://go2rtc.local:1984/",
                    "player_url_template": "{base_url}/stream.html?src={stream}&mode=mse",
                    "stream_name_prefix": "site_",
                },
            },
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
        )

        camera = metadata["cameras"][0]
        self.assertEqual(camera["go2rtc"]["stream"], "site_North_Ridge_Camera")
        self.assertEqual(
            camera["go2rtc"]["full_stream"], "site_North_Ridge_Camera_full"
        )
        self.assertEqual(
            camera["go2rtc"]["player_url"],
            "http://go2rtc.local:1984/stream.html?src=site_North_Ridge_Camera&mode=mse&media=video&muted=1",
        )
        self.assertEqual(
            camera["go2rtc"]["full_player_url"],
            "http://go2rtc.local:1984/stream.html?src=site_North_Ridge_Camera_full&mode=mse&media=video&muted=1",
        )
        self.assertEqual(
            camera["go2rtc"]["full_view_url"],
            "live.html?camera=North%20Ridge%20Camera&stream=full",
        )
        encoded = yaml.dump(camera)
        self.assertNotIn("rtsp://", encoded)
        self.assertNotIn("secret", encoded)

    def test_cameras_metadata_defaults_go2rtc_player_to_stream_html(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "North Ridge Camera": {
                    "rtsp_url": "rtsp://admin:secret@example.test:554/stream1",
                },
            },
            {
                "ui": {},
                "go2rtc": {
                    "enabled": True,
                    "base_url": "http://go2rtc.local:1984/",
                    "stream_name_prefix": "site_",
                    "preview_url_template": "{base_url}/api/stream.mjpeg?src={stream}",
                    "live_view_idle_timeout_s": 15,
                },
            },
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
        )

        self.assertEqual(
            metadata["cameras"][0]["go2rtc"]["player_url"],
            "http://go2rtc.local:1984/stream.html?src=site_North_Ridge_Camera&media=video&muted=1",
        )
        self.assertEqual(
            metadata["cameras"][0]["go2rtc"]["preview_url"],
            "http://go2rtc.local:1984/api/stream.mjpeg?src=site_North_Ridge_Camera",
        )
        self.assertEqual(
            metadata["cameras"][0]["go2rtc"]["full_view_url"],
            "live.html?camera=North%20Ridge%20Camera&stream=full",
        )
        self.assertEqual(metadata["cameras"][0]["go2rtc"]["idle_timeout_s"], 15)

    def test_cameras_metadata_adds_go2rtc_mode_only_when_configured(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "North Ridge Camera": {
                    "rtsp_url": "rtsp://admin:secret@example.test:554/stream1",
                },
            },
            {
                "ui": {},
                "go2rtc": {
                    "enabled": True,
                    "base_url": "http://go2rtc.local:1984/",
                    "player_mode": "mse,mp4",
                    "preview_mode": "mse",
                    "stream_name_prefix": "site_",
                },
            },
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
        )

        go2rtc = metadata["cameras"][0]["go2rtc"]
        self.assertEqual(
            go2rtc["player_url"],
            "http://go2rtc.local:1984/stream.html?src=site_North_Ridge_Camera&mode=mse%2Cmp4&media=video&muted=1",
        )
        self.assertEqual(
            go2rtc["preview_url"],
            "http://go2rtc.local:1984/stream.html?src=site_North_Ridge_Camera&mode=mse&media=video&muted=1",
        )

    def test_cameras_metadata_publishes_same_host_go2rtc_fallback(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "North Ridge Camera": {
                    "rtsp_url": "rtsp://admin:secret@example.test:554/stream1",
                },
            },
            {
                "ui": {},
                "go2rtc": {
                    "enabled": True,
                    "base_url": "",
                    "api_listen": ":11984",
                    "stream_name_prefix": "site_",
                },
            },
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
        )

        go2rtc = metadata["cameras"][0]["go2rtc"]
        self.assertTrue(go2rtc["enabled"])
        self.assertFalse(go2rtc["base_url_configured"])
        self.assertEqual(go2rtc["same_host_port"], 11984)
        self.assertEqual(go2rtc["stream"], "site_North_Ridge_Camera")
        self.assertNotIn("player_url", go2rtc)
        encoded = yaml.dump(go2rtc)
        self.assertNotIn("rtsp://", encoded)
        self.assertNotIn("secret", encoded)

    def test_cameras_metadata_publishes_host_specific_go2rtc_urls(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "North Ridge Camera": {
                    "rtsp_url": "rtsp://admin:secret@example.test:554/stream1",
                    "ptz_rtsp_url": "rtsp://admin:secret@example.test:554/stream2",
                },
            },
            {
                "ui": {},
                "go2rtc": {
                    "enabled": True,
                    "base_url": "",
                    "base_urls": {
                        "aredncameras.aredn805.net": "https://stream.aredn805.net/",
                        "10.123.159.233": "http://10.123.159.233:1984",
                    },
                    "api_listen": ":11984",
                    "stream_name_prefix": "site_",
                },
            },
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
        )

        go2rtc = metadata["cameras"][0]["go2rtc"]
        self.assertFalse(go2rtc["base_url_configured"])
        self.assertTrue(go2rtc["base_urls_configured"])
        self.assertEqual(go2rtc["same_host_port"], 11984)
        self.assertEqual(
            go2rtc["preview_urls"]["aredncameras.aredn805.net"],
            "https://stream.aredn805.net/stream.html?src=site_North_Ridge_Camera&media=video&muted=1",
        )
        self.assertEqual(
            go2rtc["full_player_urls"]["10.123.159.233"],
            "http://10.123.159.233:1984/stream.html?src=site_North_Ridge_Camera_full&media=video&muted=1",
        )
        self.assertNotIn("player_url", go2rtc)

    def test_cameras_metadata_omits_disabled_camera_go2rtc(self):
        json_path = os.path.join(self.temp_dir.name, "cameras.json")
        metadata = build_cameras_metadata(
            {
                "North Ridge Camera": {
                    "rtsp_url": "rtsp://admin:secret@example.test:554/stream1",
                    "go2rtc_enabled": False,
                },
            },
            {
                "ui": {},
                "go2rtc": {
                    "enabled": True,
                    "base_url": "http://go2rtc.local:1984/",
                },
            },
            {"daily_timelapse": {"file_extension": "mp4"}},
            json_path,
        )

        self.assertNotIn("go2rtc", metadata["cameras"][0])

    def test_config_load_go2rtc_global_settings(self):
        test_data = {
            "global": {
                "work_dir": self.mock_work_dir,
                "timezone": "UTC",
                "go2rtc": {
                    "enabled": True,
                    "base_url": "http://go2rtc.local:1984",
                    "base_urls": {
                        "AREDnCameras.AREDN805.net": "https://stream.aredn805.net/"
                    },
                    "player_url_template": "{base_url}/webrtc.html?src={stream}",
                    "preview_url_template": "{base_url}/api/stream.mjpeg?src={stream}",
                    "player_mode": "mp4,mse",
                    "preview_mode": "mse",
                    "stream_name_prefix": "mesh_",
                    "source_mode": "rtsp",
                    "video_mode": "h264",
                    "rtsp_timeout_s": 45,
                    "rtsp_transport": "udp",
                    "preload_ptz_streams": False,
                    "preload_query": "video=h264",
                    "api_listen": ":11984",
                    "rtsp_listen": ":18554",
                    "webrtc_listen": ":18555",
                    "webrtc_candidates": ["go2rtc.example.test:8555", "stun:8555"],
                    "live_view_idle_timeout_s": 30,
                },
            },
            "cameras": {"cam1": {"url": "http://cam1"}},
        }
        config_path = self._create_temp_config_file(test_data)

        _, _, global_conf, _, _ = config_load(config_path)

        self.assertTrue(global_conf["go2rtc"]["enabled"])
        self.assertEqual(global_conf["go2rtc"]["base_url"], "http://go2rtc.local:1984")
        self.assertEqual(
            global_conf["go2rtc"]["base_urls"],
            {"aredncameras.aredn805.net": "https://stream.aredn805.net"},
        )
        self.assertEqual(
            global_conf["go2rtc"]["preview_url_template"],
            "{base_url}/stream.html?src={stream}&media=video&muted=1",
        )
        self.assertEqual(
            global_conf["go2rtc"]["player_url_template"],
            "{base_url}/stream.html?src={stream}&media=video&muted=1",
        )
        self.assertEqual(global_conf["go2rtc"]["player_mode"], "mp4,mse")
        self.assertEqual(global_conf["go2rtc"]["preview_mode"], "mse")
        self.assertEqual(global_conf["go2rtc"]["stream_name_prefix"], "mesh_")
        self.assertEqual(global_conf["go2rtc"]["source_mode"], "rtsp")
        self.assertEqual(global_conf["go2rtc"]["video_mode"], "h264")
        self.assertEqual(global_conf["go2rtc"]["rtsp_timeout_s"], 45)
        self.assertEqual(global_conf["go2rtc"]["rtsp_transport"], "udp")
        self.assertFalse(global_conf["go2rtc"]["preload_ptz_streams"])
        self.assertEqual(global_conf["go2rtc"]["preload_query"], "video=h264")
        self.assertEqual(global_conf["go2rtc"]["api_listen"], ":11984")
        self.assertEqual(global_conf["go2rtc"]["rtsp_listen"], ":18554")
        self.assertEqual(global_conf["go2rtc"]["webrtc_listen"], ":18555")
        self.assertEqual(
            global_conf["go2rtc"]["webrtc_candidates"],
            ["go2rtc.example.test:8555", "stun:8555"],
        )
        self.assertEqual(global_conf["go2rtc"]["live_view_idle_timeout_s"], 30)

    def test_go2rtc_runtime_config_uses_rtsp_sources(self):
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {
                    "go2rtc": {
                        "enabled": True,
                        "stream_name_prefix": "mesh_",
                        "api_listen": ":11984",
                        "rtsp_listen": ":18554",
                        "webrtc_listen": ":18555",
                        "webrtc_candidates": ["go2rtc.example.test:18555"],
                    }
                },
                "cameras": {
                    "Ridge Camera": {
                        "rtsp_url": "rtsp://admin:snapshot@example.test/stream1",
                        "ptz_rtsp_url": "rtsp://admin:ptz@example.test/stream2",
                    },
                    "No RTSP": {"url": "http://snapshot"},
                },
            }
        )

        self.assertEqual(runtime_config["api"]["listen"], ":11984")
        self.assertEqual(runtime_config["rtsp"]["listen"], ":18554")
        self.assertEqual(runtime_config["webrtc"]["listen"], ":18555")
        self.assertEqual(
            runtime_config["webrtc"]["candidates"], ["go2rtc.example.test:18555"]
        )
        self.assertEqual(
            runtime_config["streams"],
            {
                "mesh_Ridge_Camera": "ffmpeg:rtsp://admin:ptz@example.test/stream2#video=copy#timeout=30",
                "mesh_Ridge_Camera_full": "ffmpeg:rtsp://admin:snapshot@example.test/stream1#video=copy#timeout=30",
            },
        )
        self.assertNotIn("preload", runtime_config)

    def test_go2rtc_runtime_config_excludes_hidden_cameras(self):
        # "hidden" cameras are documented as fully removed from the site;
        # go2rtc has no tie to Fenetre's own auth/visibility rules, so a
        # hidden camera's RTSP feed must never be published as a go2rtc
        # stream, or anyone who can reach the go2rtc port gets a live view of
        # a camera the rest of the app treats as not existing.
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {"go2rtc": {"enabled": True}},
                "cameras": {
                    "Public Camera": {
                        "rtsp_url": "rtsp://admin:secret@example.test/public",
                        "visibility": "public",
                    },
                    "Hidden Camera": {
                        "rtsp_url": "rtsp://admin:secret@example.test/hidden",
                        "visibility": "hidden",
                    },
                    "Legacy Hidden Camera": {
                        "rtsp_url": "rtsp://admin:secret@example.test/legacy",
                        "hidden": True,
                    },
                },
            }
        )

        self.assertIn("fenetre_Public_Camera", runtime_config["streams"])
        self.assertNotIn("fenetre_Hidden_Camera", runtime_config["streams"])
        self.assertNotIn("fenetre_Legacy_Hidden_Camera", runtime_config["streams"])

    def test_go2rtc_runtime_config_preloads_low_res_ptz_stream_by_default(self):
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {"go2rtc": {"enabled": True}},
                "cameras": {
                    "South": {
                        "rtsp_url": "rtsp://admin:secret@example.test/main",
                        "ptz_rtsp_url": "rtsp://admin:secret@example.test/sub",
                        "ptz": {"enabled": True},
                    }
                },
            }
        )

        self.assertEqual(
            runtime_config["streams"],
            {
                "fenetre_South": "ffmpeg:rtsp://admin:secret@example.test/sub#video=copy#timeout=30",
                "fenetre_South_full": "ffmpeg:rtsp://admin:secret@example.test/main#video=copy#timeout=30",
            },
        )
        self.assertEqual(runtime_config["preload"], {"fenetre_South": "video"})

    def test_go2rtc_runtime_config_honors_preload_overrides(self):
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {
                    "go2rtc": {
                        "enabled": True,
                        "preload_ptz_streams": True,
                        "preload_query": "video=h264",
                    }
                },
                "cameras": {
                    "South": {
                        "rtsp_url": "rtsp://admin:secret@example.test/main",
                        "ptz_rtsp_url": "rtsp://admin:secret@example.test/sub",
                        "ptz": {"enabled": True},
                        "go2rtc_preload": False,
                    },
                    "West": {
                        "rtsp_url": "rtsp://admin:secret@example.test/west",
                        "go2rtc_preload": True,
                    },
                },
            }
        )

        self.assertEqual(runtime_config["preload"], {"fenetre_West": "video=h264"})

    def test_go2rtc_runtime_config_skips_disabled_camera_streams(self):
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {
                    "go2rtc": {
                        "enabled": True,
                        "stream_name_prefix": "mesh_",
                    }
                },
                "cameras": {
                    "Enabled Camera": {
                        "rtsp_url": "rtsp://admin:secret@example.test/enabled",
                    },
                    "Disabled Camera": {
                        "rtsp_url": "rtsp://admin:secret@example.test/disabled",
                        "ptz_rtsp_url": "rtsp://admin:secret@example.test/disabled-sub",
                        "go2rtc_enabled": False,
                    },
                },
            }
        )

        self.assertEqual(
            runtime_config["streams"],
            {
                "mesh_Enabled_Camera": "ffmpeg:rtsp://admin:secret@example.test/enabled#video=copy#timeout=30"
            },
        )

    def test_go2rtc_runtime_config_can_use_direct_rtsp_mode(self):
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {
                    "go2rtc": {
                        "enabled": True,
                        "source_mode": "rtsp",
                        "rtsp_timeout_s": 45,
                    }
                },
                "cameras": {
                    "South": {
                        "rtsp_url": "rtsp://admin:secret@example.test/stream#media=video"
                    }
                },
            }
        )

        self.assertEqual(
            runtime_config["streams"],
            {
                "fenetre_South": "rtsp://admin:secret@example.test/stream#media=video#backchannel=0#timeout=45"
            },
        )

    def test_go2rtc_runtime_config_honors_camera_source_mode_override(self):
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {"go2rtc": {"enabled": True, "source_mode": "ffmpeg"}},
                "cameras": {
                    "South": {
                        "rtsp_url": "rtsp://admin:secret@example.test/stream",
                        "go2rtc_source_mode": "rtsp",
                        "go2rtc_rtsp_timeout_s": 12,
                    }
                },
            }
        )

        self.assertEqual(
            runtime_config["streams"],
            {
                "fenetre_South": "rtsp://admin:secret@example.test/stream#media=video#backchannel=0#timeout=12"
            },
        )

    def test_go2rtc_runtime_config_honors_transport_and_video_mode(self):
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {
                    "go2rtc": {
                        "enabled": True,
                        "rtsp_transport": "udp",
                        "video_mode": "h264",
                    }
                },
                "cameras": {
                    "South": {
                        "rtsp_url": "rtsp://admin:secret@example.test/main",
                        "ptz_rtsp_url": "rtsp://admin:secret@example.test/sub",
                    },
                    "West": {
                        "rtsp_url": "rtsp://admin:secret@example.test/west",
                        "go2rtc_rtsp_transport": "tcp",
                        "go2rtc_video_mode": "copy",
                    },
                },
            }
        )

        self.assertEqual(
            runtime_config["streams"],
            {
                "fenetre_South": "ffmpeg:rtsp://admin:secret@example.test/sub#video=h264#input=rtsp/udp#timeout=30",
                "fenetre_South_full": "ffmpeg:rtsp://admin:secret@example.test/main#video=h264#input=rtsp/udp#timeout=30",
                "fenetre_West": "ffmpeg:rtsp://admin:secret@example.test/west#video=copy#timeout=30",
            },
        )

    def test_go2rtc_runtime_config_adds_udp_transport_to_direct_rtsp_source(self):
        runtime_config = build_go2rtc_runtime_config(
            {
                "global": {
                    "go2rtc": {
                        "enabled": True,
                        "source_mode": "rtsp",
                        "rtsp_transport": "udp",
                    }
                },
                "cameras": {
                    "South": {"rtsp_url": "rtsp://admin:secret@example.test/stream"}
                },
            }
        )

        self.assertEqual(
            runtime_config["streams"],
            {
                "fenetre_South": "rtsp://admin:secret@example.test/stream#media=video#backchannel=0#transport=udp#timeout=30"
            },
        )

    def test_go2rtc_runtime_config_writer(self):
        config_path = self._create_temp_config_file(
            {
                "global": {"go2rtc": {"enabled": True}},
                "cameras": {
                    "South": {"rtsp_url": "rtsp://admin:secret@example.test/stream"}
                },
            }
        )
        output_path = os.path.join(self.temp_dir.name, "go2rtc.yaml")

        self.assertTrue(write_go2rtc_runtime_config(config_path, output_path))
        with open(output_path, "r") as output_file:
            generated = yaml.safe_load(output_file)

        self.assertEqual(generated["api"]["listen"], ":1984")
        self.assertEqual(
            generated["streams"],
            {
                "fenetre_South": "ffmpeg:rtsp://admin:secret@example.test/stream#video=copy#timeout=30"
            },
        )

    def test_config_load_adds_default_sun_path_postprocessing(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "postprocessing": [
                        {
                            "type": "timestamp",
                            "enabled": True,
                        }
                    ],
                },
                "cam2": {"url": "http://cam2"},
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        self.assertEqual(cameras_conf["cam1"]["postprocessing"][0]["type"], "timestamp")
        cam1_sun_path = cameras_conf["cam1"]["postprocessing"][1]
        self.assertEqual(cam1_sun_path["type"], "sun_path")
        self.assertEqual(cam1_sun_path["position"], "top_center")
        self.assertEqual(cam1_sun_path["overlay_width"], 800)
        self.assertEqual(cam1_sun_path["overlay_bar_width"], 4)
        self.assertEqual(cameras_conf["cam2"]["postprocessing"][0]["type"], "sun_path")

    def test_config_load_picamera2_controls(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "picam": {
                    "capture_method": "picamera2",
                    "main_size": [4056, 3040],
                    "buffer_count": 1,
                    "startup_warmup_s": 0,
                    "control_warmup_s": 0,
                    "exposure_value": 1.5,
                    "denoise_mode": "HighQuality",
                    "controls": {"AwbEnable": True},
                    "exposure_control": {
                        "enabled": True,
                        "day": {
                            "min_exposure_time": 100,
                            "max_exposure_time": 20000,
                        },
                    },
                    "night_settings": {
                        "ae_enable": False,
                        "exposure_time": 1000000,
                        "analogue_gain": 2.0,
                    },
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        picam = cameras_conf["picam"]
        self.assertEqual(picam["capture_method"], "picamera2")
        self.assertEqual(picam["main_size"], [4056, 3040])
        self.assertEqual(picam["buffer_count"], 1)
        self.assertEqual(picam["exposure_value"], 1.5)
        self.assertEqual(picam["denoise_mode"], "HighQuality")
        self.assertEqual(picam["controls"], {"AwbEnable": True})
        self.assertEqual(picam["exposure_control"]["day"]["max_exposure_time"], 20000)
        self.assertEqual(picam["night_settings"]["exposure_time"], 1000000)
        self.assertEqual(picam["night_settings"]["analogue_gain"], 2.0)
        self.assertFalse(picam["night_settings"]["ae_enable"])

    def test_config_load_daily_timelapse_only(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {"cam1": {"url": "http://cam1"}},
            "timelapse": {"daily_timelapse": {}},
        }
        config_path = self._create_temp_config_file(test_data)

        _, _, _, _, timelapse_conf = config_load(config_path)

        self.assertIn("daily_timelapse", timelapse_conf)
        self.assertNotIn("frequent_timelapse", timelapse_conf)
        self.assertTrue(timelapse_conf["daily_timelapse"]["enabled"])

    def test_config_load_daily_and_frequent_timelapse(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {"cam1": {"url": "http://cam1"}},
            "timelapse": {"daily_timelapse": {}, "frequent_timelapse": {}},
        }
        config_path = self._create_temp_config_file(test_data)

        _, _, _, _, timelapse_conf = config_load(config_path)

        self.assertIn("daily_timelapse", timelapse_conf)
        self.assertIn("frequent_timelapse", timelapse_conf)
        self.assertTrue(timelapse_conf["daily_timelapse"]["enabled"])
        self.assertTrue(timelapse_conf["frequent_timelapse"]["enabled"])
        self.assertEqual(timelapse_conf["frequent_timelapse"]["output_format"], "file")
        self.assertEqual(timelapse_conf["frequent_timelapse"]["max_width"], 1280)
        self.assertEqual(timelapse_conf["frequent_timelapse"]["max_height"], 720)
        self.assertIn("-crf 26", timelapse_conf["frequent_timelapse"]["ffmpeg_options"])
        self.assertEqual(
            timelapse_conf["frequent_timelapse"]["hls_segment_type"], "mpegts"
        )
        self.assertEqual(timelapse_conf["daily_timelapse"]["max_width"], 1920)
        self.assertEqual(timelapse_conf["daily_timelapse"]["max_height"], 1080)

    def test_config_load_frequent_timelapse_fmp4_segments(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {"cam1": {"url": "http://cam1"}},
            "timelapse": {
                "frequent_timelapse": {
                    "output_format": "hls",
                    "hls_segment_type": "fmp4",
                    "hls_segment_extension": "m4s",
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, _, _, _, timelapse_conf = config_load(config_path)

        frequent_conf = timelapse_conf["frequent_timelapse"]
        self.assertEqual(frequent_conf["hls_segment_type"], "fmp4")
        self.assertEqual(frequent_conf["hls_segment_extension"], "m4s")

    @patch("fenetre.config.logger")
    def test_config_load_file_not_found(self, mock_config_logging):
        with self.assertRaises(FileNotFoundError):
            config_load("non_existent_config.yaml")

    @patch("fenetre.config.logger")
    def test_config_load_invalid_yaml(self, mock_config_logging):
        fd, path = tempfile.mkstemp(suffix=".yaml", dir=self.temp_dir.name)
        with os.fdopen(fd, "w") as f:
            f.write("global: setting: value\n  nested_setting: [1,2")  # Invalid YAML

        with self.assertRaises(yaml.YAMLError):
            config_load(path)

    @patch("fenetre.fenetre.copy_public_html_files")
    @patch("fenetre.fenetre.update_cameras_metadata")
    @patch("fenetre.fenetre.Thread")  # Mock threads so they don't actually start
    @patch("fenetre.fenetre.GoProUtilityThread")  # Mock GoPro threads
    @patch("fenetre.fenetre._GOPRO_BLE_AVAILABLE", True)
    @patch("fenetre.fenetre.server_run")  # Mock server_run
    @patch("fenetre.fenetre.stop_http_server")
    def test_load_and_apply_configuration_initial_load(
        self,
        mock_stop_http,
        mock_server_run,
        MockGoProThread,
        MockThread,
        mock_update_meta,
        mock_copy_files,
    ):
        # This test is more of an integration test for the config application logic,
        # focusing on variable updates and mock calls rather than actual thread behavior.
        fenetre_module = sys.modules["fenetre.fenetre"]

        # Set up FLAGS.config for fenetre.py
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "http_server": {"enabled": True, "listen": "0.0.0.0:8080"},
            "cameras": {
                "cam1": {"url": "http://cam1", "snap_interval_s": 30},
                "cam2": {"gopro_ip": "10.5.5.9", "gopro_model": "hero11"},
            },
        }
        config_path = self._create_temp_config_file(test_data)
        # mock_flags_instance.config = config_path # No longer needed to set on mock

        # Initialize exit_event as it's used by GoProUtilityThread
        fenetre_module.exit_event = MagicMock()
        fenetre_module.exit_event.is_set.return_value = False

        fenetre_module.FLAGS = SimpleNamespace(config=config_path)

        # Call with config_file_override
        load_and_apply_configuration(
            initial_load=True, config_file_override=config_path
        )

        self.assertEqual(fenetre_module.global_config["work_dir"], self.mock_work_dir)
        self.assertEqual(fenetre_module.server_config["listen"], "0.0.0.0:8080")
        self.assertIn("cam1", fenetre_module.cameras_config)
        self.assertIn("cam2", fenetre_module.cameras_config)

        mock_copy_files.assert_called_with(
            self.mock_work_dir, fenetre_module.global_config
        )
        mock_update_meta.assert_called_with(
            fenetre_module.cameras_config, self.mock_work_dir
        )

        # Check that sleep_intervals are initialized
        self.assertEqual(fenetre_module.sleep_intervals["cam1"], 30)
        self.assertEqual(
            fenetre_module.sleep_intervals["cam2"], 60.0
        )  # Default if snap_interval_s not set

        # Check that threads were "started" (mocked)
        # Two camera watchdog manager threads + one HTTP server thread
        # MockThread is used for camera watchdogs and the http_server
        # MockGoProThread is used for GoPro utility

        # Expected calls to MockThread:
        # 1. cam1_watchdog (target=create_and_start_and_watch_thread)
        # 2. cam2_watchdog (target=create_and_start_and_watch_thread)
        # 3. http_server (target=server_run)
        # 4. admin_server (target=run_admin_server_func)
        self.assertEqual(MockThread.call_count, 4)

        # Check calls for GoProUtilityThread
        self.assertEqual(MockGoProThread.call_count, 1)
        MockGoProThread.assert_any_call(
            unittest.mock.ANY,
            "cam2",
            fenetre_module.cameras_config["cam2"],
            fenetre_module.exit_event,
        )

        # Check http server start
        mock_server_run_found = False
        for call_args in MockThread.call_args_list:
            if call_args[1].get("target") == mock_server_run:
                mock_server_run_found = True
                break
        self.assertTrue(
            mock_server_run_found, "server_run should have been a target for a Thread"
        )

    @patch("fenetre.fenetre.copy_public_html_files")
    @patch("fenetre.fenetre.update_cameras_metadata")
    @patch("fenetre.fenetre.Thread")
    @patch("fenetre.fenetre.GoProUtilityThread")
    @patch("fenetre.fenetre.server_run")
    @patch("fenetre.fenetre.stop_http_server")
    def test_load_and_apply_configuration_reload_disable_server_remove_camera(
        self,
        mock_stop_http,
        mock_server_run,
        MockGoProThread,
        MockThread,
        mock_update_meta,
        mock_copy_files,
    ):
        fenetre_module = sys.modules["fenetre.fenetre"]

        # Initial config
        initial_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "http_server": {"enabled": True, "listen": "0.0.0.0:8080"},
            "cameras": {
                "cam1": {"url": "http://cam1", "snap_interval_s": 30},
                "cam_to_remove": {"url": "http://toberemoved"},
            },
        }
        config_path = self._create_temp_config_file(initial_data)
        fenetre_module.FLAGS = SimpleNamespace(config=config_path)
        fenetre_module.exit_event = MagicMock()
        fenetre_module.exit_event.is_set.return_value = False

        # Mock initial state of threads
        mock_cam1_watchdog_manager = MagicMock(spec=sys.modules["threading"].Thread)
        mock_cam1_watchdog_manager.is_alive.return_value = True
        mock_cam_to_remove_watchdog_manager = MagicMock(
            spec=sys.modules["threading"].Thread
        )
        mock_cam_to_remove_watchdog_manager.is_alive.return_value = True

        fenetre_module.active_camera_threads = {
            "cam1": {
                "watchdog_manager_thread": mock_cam1_watchdog_manager,
                "watchdog_thread": MagicMock(is_alive=MagicMock(return_value=True)),
            },
            "cam_to_remove": {
                "watchdog_manager_thread": mock_cam_to_remove_watchdog_manager,
                "watchdog_thread": MagicMock(is_alive=MagicMock(return_value=True)),
            },
        }
        fenetre_module.sleep_intervals = {"cam1": 30, "cam_to_remove": 60}

        mock_http_thread = MagicMock(spec=sys.modules["threading"].Thread)
        mock_http_thread.is_alive.return_value = True
        fenetre_module.http_server_thread_global = mock_http_thread

        load_and_apply_configuration(
            initial_load=True, config_file_override=config_path
        )  # Apply initial

        # Reset mocks for the reload part
        MockThread.reset_mock()
        MockGoProThread.reset_mock()
        mock_stop_http.reset_mock()
        mock_server_run.reset_mock()  # server_run is a target for Thread, not called directly

        # New config: disable server, remove cam_to_remove
        reloaded_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "http_server": {
                "enabled": False,
                "port": 8080,
                "host": "0.0.0.0",
            },  # Disabled
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "snap_interval_s": 20,
                }  # Interval changed
            },
        }
        config_path_reloaded = self._create_temp_config_file(reloaded_data)
        # mock_flags_instance.config = config_path_reloaded # No longer needed

        fenetre_module.FLAGS = SimpleNamespace(config=config_path_reloaded)

        load_and_apply_configuration(
            initial_load=False, config_file_override=config_path_reloaded
        )  # Apply reloaded config

        self.assertNotIn("cam_to_remove", fenetre_module.cameras_config)
        self.assertNotIn("cam_to_remove", fenetre_module.active_camera_threads)
        self.assertNotIn("cam_to_remove", fenetre_module.sleep_intervals)

        self.assertEqual(fenetre_module.server_config["enabled"], False)
        mock_stop_http.assert_called_once()  # HTTP server should be stopped

        # Check if cam1's sleep interval was updated
        self.assertEqual(fenetre_module.sleep_intervals["cam1"], 20)

        # Cam1 thread should ideally be managed by its watchdog.
        # The test for create_and_start_and_watch_thread would cover camera thread lifecycle.
        # Here, we check that no new thread was started for cam1 if its watchdog manager was alive.
        # This part is tricky as load_and_apply_configuration starts *managers* for watchdogs.
        # If the manager for cam1 was already running, it shouldn't start a new one.

        # Assert that no new server thread was started
        server_run_targeted = any(
            call_args[1].get("target") == mock_server_run
            for call_args in MockThread.call_args_list
        )
        self.assertFalse(
            server_run_targeted,
            "server_run should not have been targeted by a new Thread on reload when server is disabled.",
        )

        # Cam_to_remove's original watchdog manager thread should have been joined
        # This requires the mock thread to have a join method.
        mock_cam_to_remove_watchdog_manager.join.assert_called_with(timeout=5)

    def test_manage_camera_threads_restarts_changed_camera_config(self):
        fenetre_module = sys.modules["fenetre.fenetre"]
        old_config = {"url": "http://old-camera", "snap_interval_s": 30}
        new_config = {"url": "http://new-camera", "snap_interval_s": 20}
        old_manager = MagicMock(spec=sys.modules["threading"].Thread)
        old_manager.is_alive.return_value = True
        old_snap = MagicMock(spec=sys.modules["threading"].Thread)
        old_snap.is_alive.return_value = True
        fenetre_module.cameras_config = {"cam1": new_config}
        fenetre_module.active_camera_threads = {
            "cam1": {
                "camera_config": old_config,
                "watchdog_manager_thread": old_manager,
                "watchdog_thread": old_snap,
            }
        }
        fenetre_module.sleep_intervals = {"cam1": 30}
        fenetre_module.exit_event = MagicMock()
        fenetre_module.exit_event.is_set.return_value = False
        fenetre_module.mqtt_manager = None

        new_manager = MagicMock(spec=sys.modules["threading"].Thread)
        with patch("fenetre.fenetre.Thread", return_value=new_manager) as mock_thread:
            with patch(
                "fenetre.fenetre.request_camera_capture"
            ) as mock_request_capture:
                fenetre_module.manage_camera_threads()

        mock_request_capture.assert_called_once_with("cam1", "config reload")
        old_snap.join.assert_called_once_with(timeout=5)
        old_manager.join.assert_called_once_with(timeout=5)
        mock_thread.assert_called_once()
        new_manager.start.assert_called_once()
        self.assertEqual(
            fenetre_module.active_camera_threads["cam1"]["camera_config"], new_config
        )
        self.assertEqual(fenetre_module.sleep_intervals["cam1"], 20)

    def test_config_load_sunrise_sunset_offsets(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "sunrise_sunset": {
                        "enabled": True,
                        "interval_s": 15,
                        "sunrise_offset_start_minutes": -45,
                        "sunrise_offset_end_minutes": 15,
                        "sunset_offset_start_minutes": -25,
                        "sunset_offset_end_minutes": 55,
                    },
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        self.assertIn("cam1", cameras_conf)
        self.assertIn("sunrise_sunset", cameras_conf["cam1"])
        ss_config = cameras_conf["cam1"]["sunrise_sunset"]
        self.assertEqual(ss_config["enabled"], True)
        self.assertEqual(ss_config["interval_s"], 15)
        self.assertEqual(ss_config["sunrise_offset_start_minutes"], -45)
        self.assertEqual(ss_config["sunrise_offset_end_minutes"], 15)
        self.assertEqual(ss_config["sunset_offset_start_minutes"], -25)
        self.assertEqual(ss_config["sunset_offset_end_minutes"], 55)

    def test_config_load_sunrise_sunset_offsets_defaults(self):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "cameras": {
                "cam1": {
                    "url": "http://cam1",
                    "sunrise_sunset": {
                        "enabled": True,
                    },
                }
            },
        }
        config_path = self._create_temp_config_file(test_data)

        _, cameras_conf, _, _, _ = config_load(config_path)

        self.assertIn("cam1", cameras_conf)
        self.assertIn("sunrise_sunset", cameras_conf["cam1"])
        ss_config = cameras_conf["cam1"]["sunrise_sunset"]
        self.assertEqual(ss_config["enabled"], True)
        self.assertEqual(ss_config["interval_s"], 10)  # default
        self.assertEqual(ss_config["sunrise_offset_start_minutes"], 60)  # default
        self.assertEqual(ss_config["sunrise_offset_end_minutes"], 30)  # default
        self.assertEqual(ss_config["sunset_offset_start_minutes"], 30)  # default
        self.assertEqual(ss_config["sunset_offset_end_minutes"], 60)  # default

    @patch("fenetre.config.logger")
    def test_config_load_sanitization_warning(self, mock_logger):
        test_data = {
            "global": {"work_dir": self.mock_work_dir, "timezone": "UTC"},
            "http_server": {
                "enabled": "not-a-boolean",  # Invalid value
                "listen": "0.0.0.0:8080",
                "extra_key": "should be ignored",
            },
            "cameras": {},
        }
        config_path = self._create_temp_config_file(test_data)

        with self.assertRaises(ConfigError):
            config_load(config_path)

        self.assertTrue(mock_logger.error.called)


if __name__ == "__main__":
    # Need to setup absl flags before running tests if fenetre.py uses app.run() or defines its own flags
    # For this setup, we are mocking FLAGS directly.
    unittest.main()
