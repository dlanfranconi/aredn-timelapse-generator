import unittest
from unittest.mock import MagicMock, patch

from fenetre.ptz import (
    PTZBackendUnavailable,
    PTZError,
    PTZLocked,
    acquire_session,
    discover_presets,
    goto_preset,
    nudge_move,
    normalize_presets,
    public_ptz_metadata,
    set_lock,
    _sessions,
)


class PTZTestCase(unittest.TestCase):
    def tearDown(self):
        set_lock("cam1", False)
        _sessions.clear()

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

    def test_discover_presets_reads_onvif_presets(self):
        media = MagicMock()
        media.GetProfiles.return_value = [MagicMock(token="profile-1")]
        ptz = MagicMock()
        ptz.GetPresets.return_value = [
            MagicMock(token="1", Name="Home"),
            MagicMock(token="2", Name="Launch Pad"),
        ]
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
            }
        }

        with patch.dict("sys.modules", {"onvif": onvif_module}):
            result = discover_presets("cam1", camera_config, "operator")

        self.assertEqual(
            result["presets"],
            [
                {"id": "1", "name": "Home", "token": "1"},
                {"id": "2", "name": "Launch Pad", "token": "2"},
            ],
        )
        ptz.GetPresets.assert_called_once_with({"ProfileToken": "profile-1"})

    def test_nudge_move_stops_after_bounded_duration(self):
        camera_config = {
            "ptz": {
                "enabled": True,
                "host": "192.0.2.10",
                "port": 8899,
                "username": "operator",
                "password": "secret",
            }
        }

        with patch("fenetre.ptz.continuous_move") as mock_move, patch(
            "fenetre.ptz.stop_move"
        ) as mock_stop, patch("fenetre.ptz.time.sleep") as mock_sleep:
            mock_move.return_value = {"ok": True, "camera": "cam1"}
            result = nudge_move(
                "cam1",
                camera_config,
                pan=1,
                move_duration_s=5,
                owner="operator",
            )

        mock_move.assert_called_once()
        mock_sleep.assert_called_once_with(2.0)
        mock_stop.assert_called_once_with("cam1", camera_config)
        self.assertEqual(result["move_duration_s"], 2.0)
