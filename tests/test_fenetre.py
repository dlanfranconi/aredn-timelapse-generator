import unittest
from unittest.mock import patch, MagicMock
from PIL import Image
from io import BytesIO
import sys
from types import SimpleNamespace

from fenetre.fenetre import Picamera2Capture, get_pic_from_url


class TestFenetre(unittest.TestCase):
    @patch('fenetre.fenetre.requests.get')
    @patch('fenetre.fenetre.time.time', return_value=1234567890)
    def test_get_pic_from_url_cache_bust(self, mock_time, mock_requests_get):
        # Mock the response from requests.get
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Create a dummy image for the content
        dummy_image = Image.new('RGB', (100, 100), color = 'red')
        byte_arr = BytesIO()
        dummy_image.save(byte_arr, format='JPEG')
        mock_response.content = byte_arr.getvalue()
        mock_requests_get.return_value = mock_response

        # Test case 1: cache_bust enabled, no existing query params
        camera_config_1 = {'cache_bust': True}
        url_1 = "http://example.com/image.jpg"
        get_pic_from_url(url_1, 10, camera_config=camera_config_1, global_config={})
        mock_requests_get.assert_called_with(
            "http://example.com/image.jpg?_=1234567890",
            timeout=10,
            headers={'Accept': 'image/*,*'}
        )

        # Test case 2: cache_bust enabled, with existing query params
        camera_config_2 = {'cache_bust': True}
        url_2 = "http://example.com/image.jpg?param=value"
        get_pic_from_url(url_2, 10, camera_config=camera_config_2, global_config={})
        mock_requests_get.assert_called_with(
            "http://example.com/image.jpg?param=value&_=1234567890",
            timeout=10,
            headers={'Accept': 'image/*,*'}
        )

        # Test case 3: cache_bust disabled
        camera_config_3 = {'cache_bust': False}
        url_3 = "http://example.com/image.jpg"
        get_pic_from_url(url_3, 10, camera_config=camera_config_3, global_config={})
        mock_requests_get.assert_called_with(
            "http://example.com/image.jpg",
            timeout=10,
            headers={'Accept': 'image/*,*'}
        )

        # Test case 4: cache_bust option not present
        camera_config_4 = {}
        url_4 = "http://example.com/image.jpg"
        get_pic_from_url(url_4, 10, camera_config=camera_config_4, global_config={})
        mock_requests_get.assert_called_with(
            "http://example.com/image.jpg",
            timeout=10,
            headers={'Accept': 'image/*,*'}
        )

    def test_picamera2_capture_applies_base_and_mode_controls(self):
        instances = []

        class FakePicamera2:
            @staticmethod
            def load_tuning_file(path):
                return {"tuning_file": path}

            def __init__(self, tuning=None):
                self.tuning = tuning
                self.configured = None
                self.controls = []
                self.started = False
                self.stopped = False
                instances.append(self)

            def create_still_configuration(self, **kwargs):
                return {"still": kwargs}

            def configure(self, config, tuning=None):
                self.configured = (config, tuning)

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

            def set_controls(self, controls):
                self.controls.append(controls)

            def capture_file(self, output, format=None):
                img = Image.new("RGB", (8, 6), color="red")
                img.save(output, format="JPEG")

        fake_controls = SimpleNamespace(
            draft=SimpleNamespace(
                NoiseReductionModeEnum=SimpleNamespace(HighQuality=42)
            )
        )

        old_picamera2 = sys.modules.get("picamera2")
        old_libcamera = sys.modules.get("libcamera")
        sys.modules["picamera2"] = SimpleNamespace(Picamera2=FakePicamera2)
        sys.modules["libcamera"] = SimpleNamespace(controls=fake_controls)
        try:
            capture = Picamera2Capture(
                {
                    "tuning_file": "/tmp/tuning.json",
                    "startup_warmup_s": 0,
                    "control_warmup_s": 0,
                    "main_size": [4056, 3040],
                    "exposure_value": 1.5,
                    "denoise_mode": "HighQuality",
                    "night_settings": {
                        "ae_enable": False,
                        "exposure_time": 1000000,
                        "analogue_gain": 2.0,
                    },
                }
            )
            pic = capture.capture("night")
            capture.close()
        finally:
            if old_picamera2 is None:
                sys.modules.pop("picamera2", None)
            else:
                sys.modules["picamera2"] = old_picamera2
            if old_libcamera is None:
                sys.modules.pop("libcamera", None)
            else:
                sys.modules["libcamera"] = old_libcamera

        self.assertEqual(pic.size, (8, 6))
        fake = instances[0]
        self.assertTrue(fake.started)
        self.assertTrue(fake.stopped)
        self.assertEqual(
            fake.configured[0],
            {"still": {"main": {"size": (4056, 3040)}}},
        )
        self.assertEqual(
            fake.controls[-1],
            {
                "ExposureValue": 1.5,
                "NoiseReductionMode": 42,
                "AeEnable": False,
                "ExposureTime": 1000000,
                "AnalogueGain": 2.0,
            },
        )

if __name__ == '__main__':
    unittest.main()
