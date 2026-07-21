import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fenetre.launch_workflow import (
    due_launch_actions,
    preview_launch_workflow,
    run_due_launch_actions,
)


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
