import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fenetre.launch_workflow import (
    due_launch_actions,
    execute_launch_action,
    list_past_launch_recordings,
    local_rtsp_recording_active,
    normalize_launch_event,
    preview_launch_workflow,
    run_due_launch_actions,
    _local_rtsp_recorders,
)


class FakeResponse:
    def __init__(self, json_data=None, content=b"", status_code=200):
        self._json_data = json_data
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        return None


def sample_config(state_file=None):
    return {
        "global": {
            "work_dir": tempfile.gettempdir(),
            "launch_workflow": {
                "enabled": True,
                "dry_run": True,
                "default_pre_seconds": 60,
                "default_post_seconds": 120,
                "state_file": state_file or "",
                "schedule_events": [
                    {
                        "id": "launch-1",
                        "name": "Falcon 9 Mission",
                        "net": "2026-07-21T12:00:00Z",
                        "launch_service_provider": {"name": "SpaceX"},
                        "pad": {
                            "name": "SLC-4E",
                            "location": {"name": "Vandenberg SFB"},
                        },
                    }
                ],
                "plans": {
                    "vandenberg": {
                        "match": {
                            "providers": ["SpaceX"],
                            "locations": ["Vandenberg"],
                        },
                        "cameras": {
                            "cam1": {
                                "preset": "launch-pad",
                                "pause_tour": True,
                                "image_profile": "launch",
                                "record": {
                                    "start_url": "http://{host}/record/start?event={launch_id}",
                                    "stop_url": "http://{host}/record/stop?event={launch_id}",
                                },
                            }
                        },
                    }
                },
            },
        },
        "cameras": {
            "cam1": {
                "url": "http://camera.local/snapshot.jpg",
                "ptz": {
                    "enabled": True,
                    "host": "camera.local",
                    "username": "admin",
                    "password": "secret",
                    "tour": {"enabled": True},
                    "presets": [
                        {"id": "launch-pad", "name": "Launch Pad", "token": "1"}
                    ],
                },
                "image_profiles": {
                    "enabled": True,
                    "host": "camera.local",
                    "profiles": {
                        "launch": {
                            "actions": [
                                {
                                    "url": "http://{host}/image/launch",
                                    "method": "POST",
                                }
                            ]
                        }
                    },
                },
            }
        },
    }


class LaunchWorkflowTestCase(unittest.TestCase):
    def test_preview_matches_vandenberg_plan(self):
        preview = preview_launch_workflow(
            sample_config(), now=datetime(2026, 7, 21, 11, 0, tzinfo=timezone.utc)
        )

        self.assertTrue(preview["ok"])
        self.assertTrue(preview["enabled"])
        self.assertEqual(preview["events"][0]["name"], "Falcon 9 Mission")
        self.assertEqual(preview["events"][0]["plans"][0]["id"], "vandenberg")
        self.assertEqual(preview["events"][0]["plans"][0]["phase"], "pending")
        camera_details = preview["events"][0]["plans"][0]["camera_details"][0]
        self.assertEqual(camera_details["name"], "cam1")
        self.assertEqual(camera_details["preset"], "launch-pad")
        self.assertTrue(camera_details["record"])

    @patch("fenetre.launch_workflow.requests.get")
    def test_preview_disabled_does_not_fetch_schedule(self, mock_get):
        config = sample_config()
        config["global"]["launch_workflow"]["enabled"] = False
        config["global"]["launch_workflow"]["schedule_events"] = []
        config["global"]["launch_workflow"][
            "schedule_url"
        ] = "https://example.invalid/launches.json"

        preview = preview_launch_workflow(
            config, now=datetime(2026, 7, 21, 11, 0, tzinfo=timezone.utc)
        )

        self.assertTrue(preview["ok"])
        self.assertFalse(preview["enabled"])
        self.assertEqual(preview["events"], [])
        mock_get.assert_not_called()

    def test_list_past_launch_recordings_groups_downloaded_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            launch_dir = os.path.join(temp_dir, "launches", "falcon-9")
            os.makedirs(launch_dir)
            recording_path = os.path.join(launch_dir, "falcon-9-cam-one.mp4")
            with open(recording_path, "wb") as recording_file:
                recording_file.write(b"video")

            config = sample_config()
            config["global"]["work_dir"] = temp_dir
            config["global"]["storage_management"] = {
                "enabled": True,
                "work_dir_max_size_GB": 40,
            }
            config["cameras"] = {"Cam One": {"url": "http://camera.local"}}

            history = list_past_launch_recordings(config)

        self.assertTrue(history["ok"])
        self.assertTrue(history["enabled"])
        self.assertEqual(history["retention"]["policy"], "work_dir")
        self.assertEqual(history["launches"][0]["id"], "falcon-9")
        self.assertEqual(history["launches"][0]["recording_count"], 1)
        recording = history["launches"][0]["recordings"][0]
        self.assertEqual(recording["camera"], "Cam One")
        self.assertEqual(recording["url"], "/launches/falcon-9/falcon-9-cam-one.mp4")

    def test_due_launch_actions_builds_prelaunch_actions(self):
        actions = due_launch_actions(
            sample_config(), now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc)
        )

        self.assertEqual(
            [action["kind"] for action in actions],
            ["pause_tour", "image_profile", "goto_preset", "record_start"],
        )

    def test_run_due_launch_actions_dry_run_renders_profile_and_record_hook(self):
        result = run_due_launch_actions(
            sample_config(), now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc)
        )

        self.assertTrue(result["dry_run"])
        by_kind = {item["action"]["kind"]: item["result"] for item in result["actions"]}
        self.assertEqual(by_kind["goto_preset"]["preset"], "launch-pad")
        self.assertEqual(
            by_kind["record_start"]["url"],
            "http://camera.local/record/start?event=launch-1",
        )
        self.assertEqual(
            by_kind["image_profile"]["actions"][0]["url"],
            "http://camera.local/image/launch",
        )

    @patch("fenetre.launch_workflow.goto_preset")
    def test_run_due_launch_actions_real_records_state(self, mock_goto_preset):
        mock_goto_preset.return_value = {"ok": True, "camera": "cam1"}
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = os.path.join(temp_dir, "launch-state.json")
            config = sample_config(state_file=state_file)
            config["global"]["launch_workflow"]["plans"]["vandenberg"]["cameras"][
                "cam1"
            ] = {"preset": "launch-pad"}

            first = run_due_launch_actions(
                config,
                now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc),
                dry_run=False,
            )
            second = run_due_launch_actions(
                config,
                now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc),
                dry_run=False,
            )

        self.assertEqual(len(first["actions"]), 1)
        self.assertEqual(second["actions"], [])
        mock_goto_preset.assert_called_once()

    @patch("fenetre.launch_workflow.requests.request")
    def test_record_hook_can_skip_when_full_viewers_are_active(self, mock_request):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = sample_config(state_file=os.path.join(temp_dir, "state.json"))
            camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
                "cameras"
            ]["cam1"]
            camera_plan.clear()
            camera_plan["record"] = {
                "start_url": "http://{host}/record/start",
                "skip_when_full_viewers": True,
            }

            result = run_due_launch_actions(
                config,
                now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc),
                dry_run=False,
                active_view_counter=lambda camera, stream: 2,
            )

        self.assertTrue(result["actions"][0]["result"]["skipped"])
        mock_request.assert_not_called()

    def test_reolink_vendor_record_schedules_start_stop_and_download(self):
        config = sample_config()
        camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
            "cameras"
        ]["cam1"]
        camera_plan.clear()
        camera_plan["record"] = {"vendor": "reolink"}

        actions = due_launch_actions(
            config, now=datetime(2026, 7, 21, 12, 3, tzinfo=timezone.utc)
        )

        self.assertEqual(
            [action["kind"] for action in actions],
            ["record_start", "record_stop", "download_recording"],
        )

    def test_reolink_download_only_skips_manual_record_actions(self):
        config = sample_config()
        camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
            "cameras"
        ]["cam1"]
        camera_plan.clear()
        camera_plan["record"] = {"vendor": "reolink", "manual_record": False}

        actions = due_launch_actions(
            config, now=datetime(2026, 7, 21, 12, 3, tzinfo=timezone.utc)
        )

        self.assertEqual(
            [action["kind"] for action in actions],
            ["download_recording"],
        )

    def test_local_rtsp_vendor_schedules_start_and_stop(self):
        config = sample_config()
        config["cameras"]["cam1"]["rtsp_url"] = "rtsp://admin:secret@camera.local/main"
        camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
            "cameras"
        ]["cam1"]
        camera_plan.clear()
        camera_plan["record"] = {"vendor": "local_rtsp"}

        actions = due_launch_actions(
            config, now=datetime(2026, 7, 21, 12, 3, tzinfo=timezone.utc)
        )

        self.assertEqual(
            [action["kind"] for action in actions],
            ["record_start", "record_stop"],
        )

    def test_local_rtsp_record_start_dry_run_uses_full_rtsp_url(self):
        config = sample_config()
        config["cameras"]["cam1"]["rtsp_url"] = "rtsp://admin:secret@camera.local/main"
        config["cameras"]["cam1"][
            "ptz_rtsp_url"
        ] = "rtsp://admin:secret@camera.local/sub"
        camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
            "cameras"
        ]["cam1"]
        camera_plan.clear()
        camera_plan["record"] = {"vendor": "local_rtsp"}

        result = run_due_launch_actions(
            config, now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc)
        )

        record_start = result["actions"][0]["result"]
        self.assertEqual(record_start["vendor"], "local_rtsp")
        self.assertIn("camera.local/main", record_start["source"])
        self.assertNotIn("secret", record_start["source"])
        self.assertIn("camera.local/main", record_start["command"])
        self.assertNotIn("camera.local/sub", record_start["command"])
        self.assertNotIn("secret", record_start["command"])

    @patch("fenetre.launch_workflow.requests.request")
    def test_reolink_manual_record_start_posts_set_manual_rec(self, mock_request):
        mock_request.return_value = FakeResponse(
            [{"cmd": "SetManualRec", "code": 0, "value": {"rspCode": 200}}]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config = sample_config(state_file=os.path.join(temp_dir, "state.json"))
            camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
                "cameras"
            ]["cam1"]
            camera_plan.clear()
            camera_plan["record"] = {"vendor": "reolink", "http_port": 80}

            result = run_due_launch_actions(
                config,
                now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc),
                dry_run=False,
            )

        self.assertEqual(result["actions"][0]["result"]["command"], "SetManualRec")
        method, url = mock_request.call_args.args[:2]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "http://camera.local/cgi-bin/api.cgi")
        self.assertEqual(
            mock_request.call_args.kwargs["params"],
            {"cmd": "SetManualRec", "user": "admin", "password": "secret"},
        )
        self.assertEqual(
            mock_request.call_args.kwargs["json"][0]["param"]["Rec"]["enable"], 1
        )

    @patch("fenetre.launch_workflow.requests.request")
    def test_reolink_recording_port_80_uses_http_even_with_https_snapshot(
        self, mock_request
    ):
        mock_request.return_value = FakeResponse(
            [{"cmd": "SetManualRec", "code": 0, "value": {"rspCode": 200}}]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config = sample_config(state_file=os.path.join(temp_dir, "state.json"))
            config["cameras"]["cam1"]["url"] = "https://camera.local/snapshot.jpg"
            camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
                "cameras"
            ]["cam1"]
            camera_plan.clear()
            camera_plan["record"] = {"vendor": "reolink", "http_port": 80}

            run_due_launch_actions(
                config,
                now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc),
                dry_run=False,
            )

        self.assertEqual(
            mock_request.call_args.args[1],
            "http://camera.local/cgi-bin/api.cgi",
        )

    @patch("fenetre.launch_workflow.requests.request")
    def test_reolink_manual_record_not_supported_message_points_to_modes(
        self, mock_request
    ):
        mock_request.return_value = FakeResponse(
            [
                {
                    "cmd": "Unknown",
                    "code": 1,
                    "error": {"detail": "not support", "rspCode": -9},
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config = sample_config(state_file=os.path.join(temp_dir, "state.json"))
            camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
                "cameras"
            ]["cam1"]
            camera_plan.clear()
            camera_plan["record"] = {"vendor": "reolink"}

            result = run_due_launch_actions(
                config,
                now=datetime(2026, 7, 21, 11, 59, 30, tzinfo=timezone.utc),
                dry_run=False,
            )

        self.assertIn("does not support SetManualRec", result["actions"][0]["error"])
        self.assertIn("Trigger manual recording", result["actions"][0]["error"])
        self.assertIn("Local HD RTSP recording", result["actions"][0]["error"])

    @patch("fenetre.launch_workflow.requests.request")
    def test_reolink_download_searches_and_saves_matching_recording(self, mock_request):
        search_response = FakeResponse(
            [
                {
                    "cmd": "Search",
                    "code": 0,
                    "value": {
                        "SearchResult": {
                            "File": [
                                {
                                    "name": "Mp4Record/2026-07-21/RecM01_20260721_115900_120200_demo.mp4",
                                    "size": 3,
                                    "type": "main",
                                }
                            ]
                        }
                    },
                }
            ]
        )
        download_response = FakeResponse(content=b"abc")
        mock_request.side_effect = [search_response, download_response]
        with tempfile.TemporaryDirectory() as temp_dir:
            download_path = os.path.join(temp_dir, "launch-1-cam1.mp4")
            config = sample_config()
            camera_plan = config["global"]["launch_workflow"]["plans"]["vandenberg"][
                "cameras"
            ]["cam1"]
            camera_plan.clear()
            camera_plan["record"] = {
                "vendor": "reolink",
                "download_path": download_path,
            }
            action = {
                "key": "launch-1:vandenberg:cam1:download_recording",
                "kind": "download_recording",
                "event": {
                    "id": "launch-1",
                    "name": "Falcon 9 Mission",
                    "launch_time_utc": "2026-07-21T12:00:00+00:00",
                },
                "plan": "vandenberg",
                "camera": "cam1",
                "config_key": "download",
                "due_at": "2026-07-21T12:02:00+00:00",
            }

            result = execute_launch_action(config, action, dry_run=False)

            with open(download_path, "rb") as downloaded_file:
                content = downloaded_file.read()

        self.assertEqual(content, b"abc")
        self.assertEqual(result["downloads"][0]["download_path"], download_path)
        self.assertEqual(mock_request.call_args_list[0].args[0], "POST")
        self.assertEqual(mock_request.call_args_list[1].args[0], "GET")


class NormalizeLaunchEventSecurityTests(unittest.TestCase):
    """Regression tests: event["id"] is templated into hook download_path/
    command values and used directly as a filesystem directory name, and
    comes from an external, potentially spoofable schedule source. It must
    always be slugified so it can't be used for path traversal."""

    def test_malicious_launch_id_is_slugified(self):
        event = normalize_launch_event(
            {
                "id": "../../../../etc/cron.d/pwn",
                "name": "Test Launch",
                "net": "2026-07-21T12:00:00Z",
            }
        )
        self.assertIsNotNone(event)
        self.assertNotIn("..", event["id"])
        self.assertNotIn("/", event["id"])

    def test_malicious_launch_slug_is_slugified(self):
        event = normalize_launch_event(
            {
                "slug": "../../etc/passwd",
                "name": "Test Launch",
                "net": "2026-07-21T12:00:00Z",
            }
        )
        self.assertIsNotNone(event)
        self.assertNotIn("..", event["id"])
        self.assertNotIn("/", event["id"])

    def test_normal_uuid_launch_id_is_preserved(self):
        event = normalize_launch_event(
            {
                "id": "5c4c1ee1-e5e9-4c1a-b7b2-abcdef123456",
                "name": "Test Launch",
                "net": "2026-07-21T12:00:00Z",
            }
        )
        self.assertEqual(event["id"], "5c4c1ee1-e5e9-4c1a-b7b2-abcdef123456")


class LocalRtspRecordingActiveTests(unittest.TestCase):
    """Regression tests for the go2rtc PTZ warm-preload feature's recording
    guard: it must not release a preloaded stream while a Sunba-style
    local_rtsp recording (no camera-side recording API, so it's a direct
    ffmpeg RTSP session) is in progress for that camera."""

    def tearDown(self):
        _local_rtsp_recorders.clear()

    def test_false_when_no_recorders(self):
        self.assertFalse(local_rtsp_recording_active("cam1"))

    def test_true_while_process_is_running(self):
        process = MagicMock()
        process.poll.return_value = None
        _local_rtsp_recorders["event:plan:cam1"] = {
            "process": process,
            "camera": "cam1",
        }
        self.assertTrue(local_rtsp_recording_active("cam1"))
        self.assertFalse(local_rtsp_recording_active("cam2"))

    def test_false_once_process_has_exited(self):
        process = MagicMock()
        process.poll.return_value = 0
        _local_rtsp_recorders["event:plan:cam1"] = {
            "process": process,
            "camera": "cam1",
        }
        self.assertFalse(local_rtsp_recording_active("cam1"))


if __name__ == "__main__":
    unittest.main()
