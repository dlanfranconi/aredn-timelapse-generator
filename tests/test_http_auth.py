import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image
from io import BytesIO
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from fenetre.compat import patched_get_pic_from_url
from fenetre.http_auth import auth_from_camera_config, redact_sensitive_headers


class HttpAuthTest(unittest.TestCase):
    def test_auth_from_camera_config_supports_basic_and_digest(self):
        basic = auth_from_camera_config(
            {"http_auth": {"type": "basic", "username": "admin", "password": "pw"}}
        )
        digest = auth_from_camera_config(
            {"http_auth": {"type": "digest", "username": "admin", "password": "pw"}}
        )

        self.assertIsInstance(basic, HTTPBasicAuth)
        self.assertIsInstance(digest, HTTPDigestAuth)

    def test_redact_sensitive_headers_hides_authorization(self):
        self.assertEqual(
            redact_sensitive_headers(
                {"Authorization": "Basic secret", "Accept": "*/*"}
            ),
            {"Authorization": "REDACTED", "Accept": "*/*"},
        )

    @patch("fenetre.compat.requests.get")
    def test_patched_get_pic_from_url_passes_http_auth(self, mock_requests_get):
        image = Image.new("RGB", (10, 10), color="red")
        image_bytes = BytesIO()
        image.save(image_bytes, format="JPEG")

        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "image/jpeg"}
        response.content = image_bytes.getvalue()
        response.request = SimpleNamespace(
            url="http://example.local/snapshot.jpg",
            headers={"Authorization": "Basic secret"},
        )
        mock_requests_get.return_value = response

        patched_get_pic_from_url(
            "http://example.local/snapshot.jpg",
            10,
            camera_config={
                "http_auth": {
                    "type": "basic",
                    "username": "admin",
                    "password": "pw",
                }
            },
        )

        _, kwargs = mock_requests_get.call_args
        self.assertIsInstance(kwargs["auth"], HTTPBasicAuth)
        self.assertEqual(kwargs["auth"].username, "admin")
        self.assertEqual(kwargs["auth"].password, "pw")

    @patch("fenetre.compat.requests.get")
    def test_patched_get_pic_from_url_does_not_verify_tls_by_default(
        self, mock_requests_get
    ):
        # AREDN mesh cameras on .local.mesh hostnames are self-signed as a
        # matter of course -- there's no real CA for a private radio mesh --
        # so this must stay opt-in, not opt-out.
        image = Image.new("RGB", (10, 10), color="red")
        image_bytes = BytesIO()
        image.save(image_bytes, format="JPEG")

        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "image/jpeg"}
        response.content = image_bytes.getvalue()
        response.request = SimpleNamespace(
            url="https://camera.local.mesh/snapshot.jpg", headers={}
        )
        mock_requests_get.return_value = response

        patched_get_pic_from_url(
            "https://camera.local.mesh/snapshot.jpg", 10, camera_config={}
        )

        _, kwargs = mock_requests_get.call_args
        self.assertFalse(kwargs["verify"])

    @patch("fenetre.compat.requests.get")
    def test_patched_get_pic_from_url_honors_verify_ssl_true(self, mock_requests_get):
        image = Image.new("RGB", (10, 10), color="red")
        image_bytes = BytesIO()
        image.save(image_bytes, format="JPEG")

        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "image/jpeg"}
        response.content = image_bytes.getvalue()
        response.request = SimpleNamespace(
            url="https://example.local/snapshot.jpg", headers={}
        )
        mock_requests_get.return_value = response

        patched_get_pic_from_url(
            "https://example.local/snapshot.jpg",
            10,
            camera_config={"verify_ssl": True},
        )

        _, kwargs = mock_requests_get.call_args
        self.assertTrue(kwargs["verify"])


if __name__ == "__main__":
    unittest.main()
