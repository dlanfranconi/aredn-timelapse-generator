import logging
import time
from io import BytesIO
from typing import Dict

from PIL import Image

logger = logging.getLogger(__name__)


class Picamera2Capture:
    """Reusable native Raspberry Pi camera capture wrapper."""

    def __init__(self, camera_config: Dict):
        self.picam2 = None
        self.closed = True
        self.started = False

        from libcamera import controls
        from picamera2 import Picamera2

        try:
            self.camera_config = camera_config
            self.controls = controls
            tuning = None
            if camera_config.get("tuning_file"):
                tuning = Picamera2.load_tuning_file(camera_config["tuning_file"])

            if tuning is not None:
                try:
                    self.picam2 = Picamera2(tuning=tuning)
                except TypeError:
                    self.picam2 = Picamera2()
            else:
                self.picam2 = Picamera2()
            self.closed = False

            still_config_kwargs = {}
            if camera_config.get("main_size"):
                still_config_kwargs["main"] = {"size": tuple(camera_config["main_size"])}
            if camera_config.get("buffer_count"):
                still_config_kwargs["buffer_count"] = camera_config["buffer_count"]
            config = self.picam2.create_still_configuration(**still_config_kwargs)

            if tuning is not None:
                try:
                    self.picam2.configure(config, tuning=tuning)
                except TypeError:
                    self.picam2.configure(config)
            else:
                self.picam2.configure(config)

            self.picam2.start()
            self.started = True
            time.sleep(float(camera_config.get("startup_warmup_s", 1.0)))
            self.last_mode = None
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.picam2 is None:
            return
        if self.started:
            try:
                self.picam2.stop()
            except Exception:
                logger.debug("Failed to stop picamera2 cleanly.", exc_info=True)
            finally:
                self.started = False
        try:
            self.picam2.close()
        except AttributeError:
            pass
        except Exception:
            logger.debug("Failed to close picamera2 cleanly.", exc_info=True)

    def __del__(self):
        self.close()

    def _denoise_control_value(self, denoise_mode):
        enum = self.controls.draft.NoiseReductionModeEnum
        if not isinstance(denoise_mode, str):
            return denoise_mode
        try:
            return getattr(enum, denoise_mode)
        except AttributeError:
            logger.warning(
                "Unknown picamera2 denoise_mode '%s'; passing value through.",
                denoise_mode,
            )
            return denoise_mode

    def _controls_from_config(self, settings: Dict) -> Dict:
        control_values = dict(settings.get("controls", {}) or {})

        if settings.get("exposure_time") is not None:
            control_values["ExposureTime"] = int(settings["exposure_time"])
        if settings.get("analogue_gain") is not None:
            control_values["AnalogueGain"] = float(settings["analogue_gain"])
        if settings.get("exposure_value") is not None:
            control_values["ExposureValue"] = float(settings["exposure_value"])
        if settings.get("denoise_mode") is not None:
            control_values["NoiseReductionMode"] = self._denoise_control_value(
                settings["denoise_mode"]
            )
        if settings.get("ae_enable") is not None:
            control_values["AeEnable"] = bool(settings["ae_enable"])

        return control_values

    def capture(self, mode: str = "unknown") -> Image.Image:
        controls_to_apply = self._controls_from_config(self.camera_config)
        mode_settings = self.camera_config.get(f"{mode}_settings", {})
        controls_to_apply.update(self._controls_from_config(mode_settings))

        if controls_to_apply:
            self.picam2.set_controls(controls_to_apply)
            if mode != self.last_mode:
                time.sleep(float(self.camera_config.get("control_warmup_s", 0.5)))
        self.last_mode = mode

        jpeg_io = BytesIO()
        self.picam2.capture_file(jpeg_io, format="jpeg")
        jpeg_bytes = jpeg_io.getvalue()
        pic = Image.open(BytesIO(jpeg_bytes))
        pic.info["exif"] = pic.info.get("exif", b"")
        return pic


def get_pic_from_picamera2(camera_config: Dict, mode: str = "unknown") -> Image.Image:
    """Captures a picture from a Raspberry Pi camera using the picamera2 library."""
    capture = Picamera2Capture(camera_config)
    try:
        return capture.capture(mode)
    finally:
        capture.close()
