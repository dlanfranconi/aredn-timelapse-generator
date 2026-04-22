import logging
import time
from io import BytesIO
from typing import Dict, Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_EXPOSURE_CONTROL = {
    "enabled": False,
    "default_mode": "day",
    "target_luma": 0.45,
    "deadband": 0.08,
    "min_adjustment_factor": 0.8,
    "max_adjustment_factor": 1.25,
    "metering_area": None,
    "day": {
        "enabled": True,
        "min_exposure_time": 100,
        "max_exposure_time": 20000,
        "min_analogue_gain": 1.0,
        "max_analogue_gain": 4.0,
        "start_exposure_time": 1000,
        "start_analogue_gain": 1.0,
    },
    "night": {
        "enabled": False,
        "min_exposure_time": 1000,
        "max_exposure_time": 15000000,
        "min_analogue_gain": 2.0,
        "max_analogue_gain": 2.0,
        "start_exposure_time": 1000000,
        "start_analogue_gain": 2.0,
    },
    "astro": {
        "enabled": False,
        "min_exposure_time": 1000000,
        "max_exposure_time": 15000000,
        "min_analogue_gain": 2.0,
        "max_analogue_gain": 2.0,
        "start_exposure_time": 5000000,
        "start_analogue_gain": 2.0,
    },
}


class Picamera2Capture:
    """Reusable native Raspberry Pi camera capture wrapper."""

    def __init__(
        self, camera_config: Dict, initial_exposure_state: Optional[Dict] = None
    ):
        self.picam2 = None
        self.closed = True
        self.started = False

        from libcamera import controls
        from picamera2 import Picamera2

        try:
            self.camera_config = camera_config
            self.controls = controls
            self.exposure_control = self._exposure_control_config()
            self.dynamic_controls_by_mode = {}
            self._restore_exposure_control_state(initial_exposure_state)
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

    def _exposure_control_config(self) -> Dict:
        configured = self.camera_config.get("exposure_control", {}) or {}
        out = dict(DEFAULT_EXPOSURE_CONTROL)
        mode_names = ("day", "night", "astro")
        out.update({k: v for k, v in configured.items() if k not in mode_names})
        for mode_name in mode_names:
            out[mode_name] = dict(DEFAULT_EXPOSURE_CONTROL[mode_name])
            out[mode_name].update(configured.get(mode_name, {}) or {})
        return out

    def _exposure_mode(self, mode: str) -> str:
        if mode in ("day", "night", "astro"):
            return mode
        return self.exposure_control.get("default_mode", "day")

    def _restore_exposure_control_state(self, exposure_state: Optional[Dict]) -> None:
        if not exposure_state:
            return
        modes = exposure_state.get("modes", {})
        if not isinstance(modes, dict):
            return

        for mode, controls in modes.items():
            if mode not in ("day", "night", "astro"):
                continue
            if not isinstance(controls, dict):
                continue
            exposure_time = controls.get("exposure_time")
            analogue_gain = controls.get("analogue_gain")
            if exposure_time is None or analogue_gain is None:
                continue
            try:
                self.dynamic_controls_by_mode[mode] = {
                    "AeEnable": False,
                    "ExposureTime": int(exposure_time),
                    "AnalogueGain": float(analogue_gain),
                }
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid restored picamera exposure state for mode %s: %s",
                    mode,
                    controls,
                )

        if self.dynamic_controls_by_mode:
            logger.info(
                "Restored picamera exposure control state for modes: %s",
                ", ".join(sorted(self.dynamic_controls_by_mode)),
            )

    def get_exposure_control_state(self) -> Dict:
        modes = {}
        for mode, controls in self.dynamic_controls_by_mode.items():
            modes[mode] = {
                "ae_enable": bool(controls.get("AeEnable", False)),
                "exposure_time": int(controls["ExposureTime"]),
                "analogue_gain": float(controls["AnalogueGain"]),
            }
        return {
            "enabled": bool(self.exposure_control.get("enabled", False)),
            "modes": modes,
        }

    def _capture_metadata(self) -> Dict:
        try:
            metadata = self.picam2.capture_metadata()
        except AttributeError:
            return {}
        except Exception:
            logger.debug("Failed to read picamera2 capture metadata.", exc_info=True)
            return {}
        return metadata or {}

    def _crop_box(
        self, image: Image.Image, area: Optional[str]
    ) -> Optional[Tuple[int, int, int, int]]:
        if not area:
            return None
        try:
            values = [float(i) for i in area.split(",")]
        except (AttributeError, ValueError):
            logger.warning("Invalid picamera exposure metering_area '%s'.", area)
            return None
        if len(values) != 4:
            logger.warning("Invalid picamera exposure metering_area '%s'.", area)
            return None

        width, height = image.size
        if all(v <= 1.0 for v in values):
            x1, y1, x2, y2 = (
                int(width * values[0]),
                int(height * values[1]),
                int(width * values[2]),
                int(height * values[3]),
            )
        else:
            x1, y1, x2, y2 = (int(v) for v in values)

        crop_box = (
            max(0, min(width, x1)),
            max(0, min(height, y1)),
            max(0, min(width, x2)),
            max(0, min(height, y2)),
        )
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            logger.warning(
                "Invalid picamera exposure metering_area '%s' for image size %s.",
                area,
                image.size,
            )
            return None
        return crop_box

    def _measure_luma(self, pic: Image.Image) -> float:
        crop_box = self._crop_box(pic, self.exposure_control.get("metering_area"))
        target = pic.crop(crop_box) if crop_box else pic
        thumbnail = target.convert("L").resize((64, 64))
        midpoint = thumbnail.width * thumbnail.height // 2
        seen = 0
        for luma, count in enumerate(thumbnail.histogram()):
            seen += count
            if seen > midpoint:
                return luma / 255.0
        return 0.0

    def _bounded_factor(self, measured_luma: float) -> float:
        target_luma = float(self.exposure_control.get("target_luma", 0.45))
        deadband = float(self.exposure_control.get("deadband", 0.08))
        if measured_luma <= 0:
            raw_factor = float(self.exposure_control.get("max_adjustment_factor", 1.25))
        else:
            raw_factor = target_luma / measured_luma
        if abs(raw_factor - 1.0) < deadband:
            return 1.0
        return max(
            float(self.exposure_control.get("min_adjustment_factor", 0.8)),
            min(
                float(self.exposure_control.get("max_adjustment_factor", 1.25)),
                raw_factor,
            ),
        )

    def _update_exposure_control(
        self, mode: str, pic: Image.Image, metadata: Dict
    ) -> None:
        if not self.exposure_control.get("enabled", False):
            return

        exposure_mode = self._exposure_mode(mode)
        mode_config = self.exposure_control.get(exposure_mode, {}) or {}
        if not mode_config.get("enabled", False):
            return

        measured_luma = self._measure_luma(pic)
        factor = self._bounded_factor(measured_luma)
        previous_controls = self.dynamic_controls_by_mode.get(exposure_mode, {})
        current_exposure_time = int(
            previous_controls.get(
                "ExposureTime",
                mode_config.get(
                    "start_exposure_time", metadata.get("ExposureTime", 1000)
                ),
            )
        )
        current_gain = float(
            previous_controls.get(
                "AnalogueGain",
                mode_config.get(
                    "start_analogue_gain", metadata.get("AnalogueGain", 1.0)
                ),
            )
        )
        min_exposure = int(mode_config.get("min_exposure_time", 100))
        max_exposure = int(mode_config.get("max_exposure_time", 20000))
        min_gain = float(mode_config.get("min_analogue_gain", 1.0))
        max_gain = float(mode_config.get("max_analogue_gain", 4.0))

        desired_total = current_exposure_time * current_gain * factor
        next_exposure_time = int(
            max(min_exposure, min(max_exposure, desired_total / current_gain))
        )
        next_gain = max(min_gain, min(max_gain, desired_total / next_exposure_time))

        self.dynamic_controls_by_mode[exposure_mode] = {
            "AeEnable": False,
            "ExposureTime": next_exposure_time,
            "AnalogueGain": next_gain,
        }
        logger.info(
            "Picamera exposure control mode=%s luma=%.3f factor=%.3f "
            "exposure_time=%dus analogue_gain=%.2f",
            exposure_mode,
            measured_luma,
            factor,
            next_exposure_time,
            next_gain,
        )

    def capture(self, mode: str = "unknown") -> Image.Image:
        controls_to_apply = self._controls_from_config(self.camera_config)
        mode_settings = self.camera_config.get(f"{mode}_settings", {})
        controls_to_apply.update(self._controls_from_config(mode_settings))
        exposure_mode = self._exposure_mode(mode)
        controls_to_apply.update(self.dynamic_controls_by_mode.get(exposure_mode, {}))

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
        metadata = self._capture_metadata()
        self._update_exposure_control(mode, pic, metadata)
        return pic


def get_pic_from_picamera2(camera_config: Dict, mode: str = "unknown") -> Image.Image:
    """Captures a picture from a Raspberry Pi camera using the picamera2 library."""
    capture = Picamera2Capture(camera_config)
    try:
        return capture.capture(mode)
    finally:
        capture.close()
