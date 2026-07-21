from __future__ import annotations

import os
import shlex

_BASE_FFMPEG_ARGS = [
    "ffmpeg",
    "-hide_banner",
    "-loglevel",
    "error",
    "-rtsp_transport",
    "tcp",
]
_OLD_OUTPUT_ARGS = ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
_VIDEO_ONLY_INPUT_ARGS = ["-allowed_media_types", "video"]
_VIDEO_ONLY_OUTPUT_ARGS = [
    "-an",
    "-map",
    "0:v:0",
    "-frames:v",
    "1",
    "-f",
    "image2pipe",
    "-vcodec",
    "mjpeg",
    "-",
]


def rtsp_snapshot_command(rtsp_url: str) -> str:
    args = (
        _BASE_FFMPEG_ARGS
        + _VIDEO_ONLY_INPUT_ARGS
        + ["-i", rtsp_url]
        + _VIDEO_ONLY_OUTPUT_ARGS
    )
    return " ".join(shlex.quote(arg) for arg in args)


def is_fenetre_generated_rtsp_snapshot_command(command: str | None) -> bool:
    if not command:
        return False
    try:
        args = shlex.split(command)
    except ValueError:
        return False
    if not args or os.path.basename(args[0]) != "ffmpeg":
        return False
    args = ["ffmpeg", *args[1:]]
    if "-i" not in args:
        return False
    input_index = args.index("-i")
    if input_index + 1 >= len(args):
        return False
    input_url = args[input_index + 1]
    if not input_url.lower().startswith("rtsp://"):
        return False

    before_input = args[:input_index]
    after_input = args[input_index + 2 :]
    return (before_input == _BASE_FFMPEG_ARGS and after_input == _OLD_OUTPUT_ARGS) or (
        before_input == _BASE_FFMPEG_ARGS + _VIDEO_ONLY_INPUT_ARGS
        and after_input == _VIDEO_ONLY_OUTPUT_ARGS
    )


def camera_local_command(camera_config: dict) -> str | None:
    local_command = camera_config.get("local_command")
    rtsp_url = camera_config.get("rtsp_url")
    if rtsp_url and is_fenetre_generated_rtsp_snapshot_command(local_command):
        return rtsp_snapshot_command(str(rtsp_url))
    return local_command
