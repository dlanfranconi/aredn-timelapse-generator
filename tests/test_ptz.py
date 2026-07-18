import unittest
from unittest.mock import MagicMock, patch

from fenetre.ptz import (
    PTZBackendUnavailable,
    PTZError,
    PTZLocked,
    acquire_session,
    goto_preset,
    normalize_presets,
    public_ptz_metadata,
    set_lock,
)


class PTZTestCase(unittest.TestCase):
    def tearDown(self):
        set_lock("cam1", False)

    def test_public_metadata_redacts_onvif_connection(self):
        metadata = public_ptz_metadata(
            {
                "enabled": True,
                "public": True,
                "allow_presets": True,
                "allow_manual_control": False,
                "host": "192.0.2.10",
                "username": "admin",
                "password": "secret",
                "presets": [
                    {"id": "launch", "name": "Launch Pad", "token": "preset-1"}
                ],
            }
        )

        self.assertTrue(metadata["enabled"])
        self.assertEqual(metadata["presets"], [{"id": "launch", "name": "Launch Pad"}])
        self.assertNotIn("host", metadata)
        self.assertNotIn("username", metadata)
        self.assertNotIn("password", metadata)

    def test_normalize_presets_accepts_mapping(self):
        self.assertEqual(
            normalize_presets({"presets": {"home": {"name": "Home", "token": "1"}}}),
            [{"id": "home", "name": "Home", "token": "1"}],
        )

    def test_lock_blocks_session(self):
        set_lock("cam1", True, "Maintenance")

        with self.assertRaises(PTZLocked):
            acquire_session("cam1", "user-a")

    def test_goto_preset_reports_missing_optional_backend(self):
        camera_config = {
            "ptz": {
                "enabled": True,
                "host": "192.0.2.10",
                "port": 8899,
                "username": "operator",
                "password": "secret",
                "presets": [{"id": "home", "name": "Home", "token": "1"}],
            }
        }
        with patch.dict("sys.modules", {"onvif": None}):
            with self.assertRaises(PTZBackendUnavailable):
                goto_preset("cam1", camera_config, "home")

    def test_goto_preset_uses_onvif_backend_when_available(self):
        media = MagicMock()
        media.GetProfiles.return_value = [MagicMock(token="profile-1")]
        ptz = MagicMock()
        request = MagicMock()
        ptz.create_type.return_value = request
        camera = MagicMock()
        camera.create_media_service.return_value = media
        camera.create_ptz_service.return_value = ptz
        onvif_module = MagicMock()
        onvif_module.ONVIFCamera.return_value = camera
        camera_config = {
            "ptz": {
                "enabled": True,
                "host": "192.0.2.10",
                "port": 8899,
                "username": "operator",
                "password": "secret",
                "presets": [{"id": "home", "name": "Home", "token": "1"}],
            }
        }

        with patch.dict("sys.modules", {"onvif": onvif_module}):
            result = goto_preset("cam1", camera_config, "home")

        self.assertTrue(result["ok"])
        onvif_module.ONVIFCamera.assert_called_once_with(
            "192.0.2.10", 8899, "operator", "secret"
        )
        self.assertEqual(request.ProfileToken, "profile-1")
        self.assertEqual(request.PresetToken, "1")
        ptz.GotoPreset.assert_called_once_with(request)

    def test_goto_preset_reports_onvif_connection_error_with_host_and_port(self):
        onvif_module = MagicMock()
        onvif_module.ONVIFCamera.side_effect = ConnectionRefusedError(
            "Connection refused"
        )
        camera_config = {
            "ptz": {
                "enabled": True,
                "host": "10.1.64.69",
                "port": 8899,
                "username": "operator",
                "password": "secret",
                "presets": [{"id": "home", "name": "Home", "token": "1"}],
            }
        }

        with patch.dict("sys.modules", {"onvif": onvif_module}):
            with self.assertRaisesRegex(
                PTZError,
                "Could not connect to ONVIF service at 10.1.64.69:8899",
            ):
                goto_preset("cam1", camera_config, "home")
