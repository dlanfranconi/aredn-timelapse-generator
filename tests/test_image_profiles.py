import unittest
from unittest.mock import patch

from fenetre.image_profiles import apply_image_profile, image_profile_summary


class ImageProfilesTestCase(unittest.TestCase):
    def test_reolink_profile_dry_run_renders_set_image_request(self):
        camera_config = {
            "image_profiles": {
                "enabled": True,
                "vendor": "reolink",
                "host": "192.0.2.20",
                "http_port": 80,
                "channel": 0,
                "username": "admin",
                "password": "secret",
                "profiles": {
                    "day": {
                        "settings": {
                            "bright": 128,
                            "contrast": 64,
                        }
                    }
                },
            }
        }

        result = apply_image_profile(
            "cam1", camera_config, profile_name="day", dry_run=True
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "day")
        action = result["actions"][0]
        self.assertEqual(action["name"], "reolink-set-image")
        self.assertIn("password=***", action["url"])
        request_json = action["request"]["json"]
        self.assertEqual(request_json[0]["param"]["Image"]["bright"], 128)

    @patch("fenetre.image_profiles.requests.request")
    def test_generic_profile_action_executes_http_request(self, mock_request):
        mock_request.return_value.status_code = 200
        mock_request.return_value.raise_for_status.return_value = None
        camera_config = {
            "image_profiles": {
                "enabled": True,
                "profiles": {
                    "night": {
                        "actions": [
                            {
                                "method": "POST",
                                "url": "http://camera.local/api/night",
                                "json": {"mode": "night"},
                            }
                        ]
                    }
                },
            }
        }

        result = apply_image_profile(
            "cam1", camera_config, profile_name="night", dry_run=False
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["actions"][0]["status_code"], 200)
        mock_request.assert_called_once_with(
            "POST",
            "http://camera.local/api/night",
            timeout=5.0,
            headers={},
            json={"mode": "night"},
        )

    def test_image_profile_summary_lists_profiles_and_mode_map(self):
        summary = image_profile_summary(
            {
                "image_profiles": {
                    "enabled": True,
                    "vendor": "reolink",
                    "mode_profiles": {"night": "low-light"},
                    "profiles": {"day": {}, "low-light": {}},
                }
            }
        )

        self.assertTrue(summary["enabled"])
        self.assertEqual(summary["vendor"], "reolink")
        self.assertEqual(summary["profiles"], ["day", "low-light"])
        self.assertEqual(summary["mode_profiles"], {"night": "low-light"})
