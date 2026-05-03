import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import time
import requests
import yaml
import socket
from contextlib import closing
from urllib.parse import urljoin

from PIL import Image
from prometheus_client import REGISTRY

# Add project root to allow importing fenetre
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fenetre.timelapse import create_incremental_hls_timelapse
from src.fenetre.fenetre import (
    load_and_apply_configuration,
    shutdown_application,
    exit_event,
)


class ServerIntegrationTest(unittest.TestCase):

    def setUp(self):
        # Reset Prometheus registry
        collectors = list(REGISTRY._collector_to_names.keys())
        for collector in collectors:
            REGISTRY.unregister(collector)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = self.temp_dir.name
        self.config_path = os.path.join(self.work_dir, "config.yaml")
        self.test_content = "<html><body>Hello, test!</body></html>"
        self.test_page_path = os.path.join(self.work_dir, "test.html")

        with open(self.test_page_path, "w") as f:
            f.write(self.test_content)

        # Find a free port
        try:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
                s.bind(("", 0))
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.port = s.getsockname()[1]
        except PermissionError as exc:
            self.skipTest(f"Socket operations not permitted in this environment: {exc}")

        self.config_data = {
            "global": {"timezone": "UTC", "work_dir": self.work_dir},
            "http_server": {
                "enabled": True,
                "listen": f"127.0.0.1:{self.port}",
            },
            "cameras": {},
            "admin_server": {"enabled": False},
            "timelapse": {"enabled": False},
        }

        with open(self.config_path, "w") as f:
            yaml.dump(self.config_data, f)

        # Reset fenetre's global state before each test
        if "fenetre.fenetre" in sys.modules:
            fenetre_module = sys.modules["src.fenetre.fenetre"]
            fenetre_module.server_config = {}
            fenetre_module.cameras_config = {}
            fenetre_module.global_config = {}
            fenetre_module.sleep_intervals = {}
            fenetre_module.active_camera_threads = {}
            fenetre_module.http_server_thread_global = None
            fenetre_module.http_server_instance = None
            # Initialize all global thread variables that shutdown_application touches
            fenetre_module.timelapse_thread_global = None
            fenetre_module.daylight_thread_global = None
            fenetre_module.archive_thread_global = None
            fenetre_module.frequent_timelapse_loop_thread_global = None
            fenetre_module.admin_server_thread_global = None
            if hasattr(fenetre_module, "exit_event") and fenetre_module.exit_event:
                fenetre_module.exit_event.clear()

    def tearDown(self):
        shutdown_application()
        self.temp_dir.cleanup()
        pid_file_path = os.environ.get("FENETRE_PID_FILE", "fenetre.pid")
        if os.path.exists(pid_file_path):
            try:
                os.remove(pid_file_path)
            except OSError:
                pass

    def test_http_server_serves_content(self):
        load_and_apply_configuration(
            initial_load=True, config_file_override=self.config_path
        )

        # Give the server a moment to start up
        time.sleep(1)

        try:
            response = requests.get(
                f"http://127.0.0.1:{self.port}/test.html", timeout=5
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, self.test_content)
        except requests.exceptions.ConnectionError as e:
            self.fail(
                f"HTTP server did not start or is not listening. Connection error: {e}"
            )

    def test_http_server_serves_incremental_hls_timelapse(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg and ffprobe are required for HLS integration tests")

        day_dir = os.path.join(self.work_dir, "photos", "testcam", "2026-05-02")
        os.makedirs(day_dir)
        ffmpeg_options = (
            "-c:v libx264 -preset ultrafast -crf 28 -g 2 -keyint_min 2 -sc_threshold 0"
        )
        self._write_jpeg_frames(day_dir, 0, 12)

        self.assertTrue(
            create_incremental_hls_timelapse(
                day_dir,
                log_dir=self.work_dir,
                tmp_dir=os.path.join(self.work_dir, "tmp"),
                ffmpeg_options=ffmpeg_options,
                framerate=1,
            )
        )

        playlist_path = os.path.join(day_dir, "2026-05-02.m3u8")
        self.assertTrue(os.path.exists(playlist_path))
        first_segment_uris = self._read_playlist_segment_uris(playlist_path)
        self.assertGreaterEqual(len(first_segment_uris), 5)

        self._write_jpeg_frames(day_dir, 12, 4)
        self.assertTrue(
            create_incremental_hls_timelapse(
                day_dir,
                log_dir=self.work_dir,
                tmp_dir=os.path.join(self.work_dir, "tmp"),
                ffmpeg_options=ffmpeg_options,
                framerate=1,
            )
        )

        segment_uris = self._read_playlist_segment_uris(playlist_path)
        self.assertGreater(len(segment_uris), len(first_segment_uris))
        self.assertEqual(len(segment_uris), len(set(segment_uris)))

        load_and_apply_configuration(
            initial_load=True, config_file_override=self.config_path
        )
        self._wait_for_http_server(f"http://127.0.0.1:{self.port}/test.html")

        playlist_url = (
            f"http://127.0.0.1:{self.port}/photos/testcam/2026-05-02/2026-05-02.m3u8"
        )
        snapshot_path = os.path.join(day_dir, "2026-05-02.snapshot.m3u8")
        snapshot_url = "http://127.0.0.1:{}/photos/testcam/2026-05-02/{}".format(
            self.port, os.path.basename(snapshot_path)
        )
        self._write_vod_snapshot_playlist(playlist_path, snapshot_path, playlist_url)

        playlist_response = requests.get(playlist_url, timeout=5)
        self.assertEqual(playlist_response.status_code, 200)
        self.assertIn("#EXTM3U", playlist_response.text)
        self.assertIn(
            "application/vnd.apple.mpegurl",
            playlist_response.headers.get("Content-Type", ""),
        )

        for segment_uri in segment_uris:
            segment_response = requests.get(
                urljoin(playlist_url, segment_uri), timeout=5
            )
            self.assertEqual(segment_response.status_code, 200)
            self.assertGreater(len(segment_response.content), 0)
            self.assertIn(
                "video/mp2t", segment_response.headers.get("Content-Type", "")
            )

        self._assert_ffprobe_can_read_hls(snapshot_url)
        self._assert_ffmpeg_can_decode_hls(snapshot_url)

    def _write_jpeg_frames(self, day_dir, start, count):
        for frame_index in range(start, start + count):
            image = Image.new(
                "RGB",
                (320, 180),
                (
                    (frame_index * 47) % 255,
                    (frame_index * 83) % 255,
                    (frame_index * 131) % 255,
                ),
            )
            image.save(
                os.path.join(day_dir, f"{frame_index:06d}.jpg"),
                quality=90,
                comment=b"x" * (frame_index + 1),
            )

    def _read_playlist_segment_uris(self, playlist_path):
        with open(playlist_path, "r") as f:
            return [
                line.strip() for line in f if line.strip() and not line.startswith("#")
            ]

    def _write_vod_snapshot_playlist(self, playlist_path, snapshot_path, playlist_url):
        with open(playlist_path, "r") as f:
            lines = [line.strip() for line in f if line.strip()]

        snapshot_lines = []
        has_playlist_type = False
        has_endlist = False
        for line in lines:
            if line.startswith("#EXT-X-PLAYLIST-TYPE:"):
                snapshot_lines.append("#EXT-X-PLAYLIST-TYPE:VOD")
                has_playlist_type = True
                continue
            if line.startswith("#EXT-X-ENDLIST"):
                has_endlist = True
                snapshot_lines.append(line)
                continue
            if line.startswith("#"):
                snapshot_lines.append(line)
                continue
            snapshot_lines.append(urljoin(playlist_url, line))

        if not has_playlist_type:
            snapshot_lines.insert(3, "#EXT-X-PLAYLIST-TYPE:VOD")
        if not has_endlist:
            snapshot_lines.append("#EXT-X-ENDLIST")

        with open(snapshot_path, "w") as f:
            f.write("\n".join(snapshot_lines) + "\n")

    def _assert_ffprobe_can_read_hls(self, playlist_url):
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-print_format",
                "json",
                playlist_url,
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
        probe_data = json.loads(result.stdout)
        video_streams = [
            stream
            for stream in probe_data.get("streams", [])
            if stream.get("codec_type") == "video"
        ]
        self.assertEqual(len(video_streams), 1)
        video_stream = video_streams[0]
        self.assertEqual(video_stream.get("codec_name"), "h264")
        self.assertGreater(video_stream.get("width", 0), 0)
        self.assertGreater(video_stream.get("height", 0), 0)
        duration = float(
            probe_data.get("format", {}).get("duration")
            or video_stream.get("duration")
            or 0
        )
        self.assertGreaterEqual(duration, 10.0)

    def _assert_ffmpeg_can_decode_hls(self, playlist_url):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", playlist_url, "-f", "null", "-"],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )

    def _wait_for_http_server(self, url):
        deadline = time.monotonic() + 5
        last_error = None
        while time.monotonic() < deadline:
            try:
                response = requests.get(url, timeout=0.5)
                if response.status_code == 200:
                    return
            except requests.exceptions.RequestException as exc:
                last_error = exc
            time.sleep(0.1)
        self.fail(f"HTTP server did not become ready: {last_error}")


if __name__ == "__main__":
    unittest.main()
