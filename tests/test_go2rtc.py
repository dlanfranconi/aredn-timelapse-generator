import unittest

from fenetre.go2rtc import go2rtc_preload_query_params


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


if __name__ == "__main__":
    unittest.main()
