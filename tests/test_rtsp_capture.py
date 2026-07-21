import shlex

from fenetre.rtsp_capture import (
    camera_local_command,
    is_fenetre_generated_rtsp_snapshot_command,
    rtsp_snapshot_command,
)


def test_rtsp_snapshot_command_is_video_only():
    command = rtsp_snapshot_command("rtsp://admin:secret@camera.local:554/11")
    args = shlex.split(command)

    assert args[:6] == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
    ]
    assert args[6:8] == ["-allowed_media_types", "video"]
    assert args[8:10] == ["-i", "rtsp://admin:secret@camera.local:554/11"]
    assert "-an" in args
    assert args[args.index("-map") + 1] == "0:v:0"
    assert args[-7:] == ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]


def test_generated_rtsp_snapshot_command_detection_covers_old_and_new_commands():
    old_command = (
        "ffmpeg -hide_banner -loglevel error -rtsp_transport tcp "
        "-i rtsp://camera.local/11 -frames:v 1 -f image2pipe -vcodec mjpeg -"
    )
    new_command = rtsp_snapshot_command("rtsp://camera.local/11")

    assert is_fenetre_generated_rtsp_snapshot_command(old_command)
    assert is_fenetre_generated_rtsp_snapshot_command(new_command)
    assert not is_fenetre_generated_rtsp_snapshot_command(
        "ffmpeg -i rtsp://camera.local/11 -frames:v 1 -f image2pipe -"
    )


def test_camera_local_command_replaces_old_generated_rtsp_command():
    old_command = (
        "ffmpeg -hide_banner -loglevel error -rtsp_transport tcp "
        "-i rtsp://old.example/11 -frames:v 1 -f image2pipe -vcodec mjpeg -"
    )

    command = camera_local_command(
        {
            "local_command": old_command,
            "rtsp_url": "rtsp://admin:secret@camera.local:554/11",
        }
    )

    assert command == rtsp_snapshot_command("rtsp://admin:secret@camera.local:554/11")


def test_camera_local_command_leaves_custom_commands_alone():
    custom_command = "ffmpeg -i rtsp://camera.local/11 -vf fps=1 -frames:v 1 -"

    assert (
        camera_local_command(
            {
                "local_command": custom_command,
                "rtsp_url": "rtsp://camera.local/11",
            }
        )
        == custom_command
    )
