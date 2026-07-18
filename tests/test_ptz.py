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
    stop_move,
    _endpoint_failure_backoffs,
    _profile_token_cache,
    _sessions,
)


class PTZTestCase(unittest.TestCase):
    def tearDown(self):
        set_lock("cam1", False)
        _sessions.clear()
        _endpoint_failure_backoffs.clear()
        _profile_token_cache.clear()

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

    def test_goto_preset_allows_discovered_onvif_token_when_no_configured_presets(self):
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
            }
        }

        with patch.dict("sys.modules", {"onvif": onvif_module}):
            result = goto_preset("cam1", camera_config, "1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["preset"], "1")
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
            MagicMock(token="3", Name=""),
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
        media = MagicMock()
        media.GetProfiles.return_value = [MagicMock(token="profile-1")]
        ptz = MagicMock()
        move_request = MagicMock()
        stop_request = MagicMock()
        ptz.create_type.side_effect = [move_request, stop_request]
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

        with patch.dict("sys.modules", {"onvif": onvif_module}), patch(
            "fenetre.ptz.time.sleep"
        ) as mock_sleep:
            result = nudge_move(
                "cam1",
                camera_config,
                pan=1,
                tilt=0.5,
                zoom=-1,
                move_duration_s=5,
                owner="operator",
            )

        onvif_module.ONVIFCamera.assert_called_once_with(
            "192.0.2.10", 8899, "operator", "secret"
        )
        media.GetProfiles.assert_called_once_with()
        ptz.create_type.assert_any_call("ContinuousMove")
        ptz.create_type.assert_any_call("Stop")
        self.assertEqual(move_request.ProfileToken, "profile-1")
        self.assertEqual(
            move_request.Velocity,
            {"PanTilt": {"x": 1, "y": 0.5}, "Zoom": {"x": -1}},
        )
        mock_sleep.assert_called_once_with(2.0)
        self.assertEqual(stop_request.ProfileToken, "profile-1")
        self.assertTrue(stop_request.PanTilt)
        self.assertTrue(stop_request.Zoom)
        ptz.ContinuousMove.assert_called_once_with(move_request)
        ptz.Stop.assert_called_once_with(stop_request)
        self.assertEqual(result["move_duration_s"], 2.0)

    def test_stop_move_skips_media_profile_lookup_when_profile_token_configured(self):
        media = MagicMock()
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
                "profile_token": "profile-1",
            }
        }

        with patch.dict("sys.modules", {"onvif": onvif_module}):
            result = stop_move("cam1", camera_config)

        self.assertTrue(result["ok"])
        media.GetProfiles.assert_not_called()
        self.assertEqual(request.ProfileToken, "profile-1")
        ptz.Stop.assert_called_once_with(request)

    def test_profile_token_is_cached_after_first_discovery(self):
        media = MagicMock()
        media.GetProfiles.return_value = [MagicMock(token="profile-1")]
        ptz = MagicMock()
        first_request = MagicMock()
        second_request = MagicMock()
        ptz.create_type.side_effect = [first_request, second_request]
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
            stop_move("cam1", camera_config)
            stop_move("cam1", camera_config)

        media.GetProfiles.assert_called_once_with()
        self.assertEqual(first_request.ProfileToken, "profile-1")
        self.assertEqual(second_request.ProfileToken, "profile-1")

    def test_nudge_move_can_use_relative_move_without_stop(self):
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
                "move_mode": "relative",
                "relative_move_scale": 0.2,
            }
        }

        with patch.dict("sys.modules", {"onvif": onvif_module}), patch(
            "fenetre.ptz.time.sleep"
        ) as mock_sleep:
            result = nudge_move(
                "cam1",
                camera_config,
                pan=0.5,
                tilt=-1,
                zoom=0.25,
                move_duration_s=0.5,
            )

        self.assertTrue(result["ok"])
        ptz.create_type.assert_called_once_with("RelativeMove")
        self.assertEqual(request.ProfileToken, "profile-1")
        self.assertEqual(
            request.Translation,
            {"PanTilt": {"x": 0.1, "y": -0.2}, "Zoom": {"x": 0.05}},
        )
        ptz.RelativeMove.assert_called_once_with(request)
        ptz.Stop.assert_not_called()
        mock_sleep.assert_not_called()

    def test_failed_ptz_operation_sets_endpoint_cooldown(self):
        media = MagicMock()
        media.GetProfiles.return_value = [MagicMock(token="profile-1")]
        ptz = MagicMock()
        request = MagicMock()
        ptz.create_type.return_value = request
        ptz.Stop.side_effect = RuntimeError("camera rebooted")
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
                "failure_cooldown_s": 30,
            }
        }

        with patch.dict("sys.modules", {"onvif": onvif_module}):
            with self.assertRaisesRegex(PTZError, "was not retried"):
                stop_move("cam1", camera_config)
            with self.assertRaisesRegex(PTZError, "cooling down"):
                stop_move("cam1", camera_config)

        self.assertIn("192.0.2.10:8899", _endpoint_failure_backoffs)
        self.assertEqual(ptz.Stop.call_count, 1)
