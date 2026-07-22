import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fenetre.launch_workflow import (
    due_launch_actions,
    execute_launch_action,
    preview_launch_workflow,
    run_due_launch_actions,
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
