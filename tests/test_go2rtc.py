import unittest

from fenetre.go2rtc import (
    _split_go2rtc_source_params,
    _video_only_source,
    go2rtc_preload_query_params,
)


class Go2rtcPreloadQueryParamsTests(unittest.TestCase):
    """go2rtc's live PUT /api/preload takes the same raw query string the
    static preload: YAML key already uses (e.g. "video"). These tests pin
    down how that string turns into request params so a live-triggered
    preload (see warm_ptz_stream in admin_server.py) behaves the same as
    the startup-time one."""

    def test_bare_flag(self):
        self.assertEqual(go2rtc_preload_query_params("video"), {"video": ""})

    def test_compound_query(self):
        self.assertEqual(
            go2rtc_preload_query_params("video&audio=false"),
            {"video": "", "audio": "false"},
        )

    def test_empty_string(self):
        self.assertEqual(go2rtc_preload_query_params(""), {})

    def test_none(self):
        self.assertEqual(go2rtc_preload_query_params(None), {})

    def test_strips_whitespace(self):
        self.assertEqual(go2rtc_preload_query_params("  video  "), {"video": ""})


class SplitGo2rtcSourceParamsTests(unittest.TestCase):
    def test_no_hash_returns_whole_string_as_base(self):
        base, params = _split_go2rtc_source_params("rtsp://user:pass@host/stream")
        self.assertEqual(base, "rtsp://user:pass@host/stream")
        self.assertEqual(params, [])

    def test_splits_trailing_params(self):
        base, params = _split_go2rtc_source_params(
            "rtsp://user:pass@host/stream#video=copy#timeout=30"
        )
        self.assertEqual(base, "rtsp://user:pass@host/stream")
        self.assertEqual(params, ["video=copy", "timeout=30"])

    def test_hash_in_password_is_not_treated_as_param_delimiter(self):
        base, params = _split_go2rtc_source_params(
            "rtsp://user:pa#ss@host/stream#video=copy"
        )
        self.assertEqual(base, "rtsp://user:pa#ss@host/stream")
        self.assertEqual(params, ["video=copy"])

    def test_hash_in_password_with_no_trailing_params(self):
        base, params = _split_go2rtc_source_params("rtsp://user:pa#ss@host/stream")
        self.assertEqual(base, "rtsp://user:pa#ss@host/stream")
        self.assertEqual(params, [])


class VideoOnlySourceTests(unittest.TestCase):
    def test_preserves_hash_in_password_for_ffmpeg_mode(self):
        result = _video_only_source(
            "rtsp://user:pa#ss@host/stream", source_mode="ffmpeg"
        )
        self.assertTrue(result.startswith("ffmpeg:rtsp://user:pa#ss@host/stream#"))

    def test_preserves_hash_in_password_for_rtsp_mode(self):
        result = _video_only_source("rtsp://user:pa#ss@host/stream", source_mode="rtsp")
        self.assertTrue(result.startswith("rtsp://user:pa#ss@host/stream#"))


if __name__ == "__main__":
    unittest.main()
