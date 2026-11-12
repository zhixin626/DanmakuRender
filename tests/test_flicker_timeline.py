"""The checker must use the same zero-based CFR grid as the renderer."""

import subprocess

import numpy as np

from tests.render_checks import frame_stream


def test_delayed_video_keeps_first_frame_back_to_audio_origin(tmp_path):
    path = tmp_path / "delayed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-itsoffset",
            "0.2",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=25:duration=0.4",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            "0.6",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-bf",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    frames = list(frame_stream(path, 25, 10, size=(64, 64)))
    assert len(frames) == 10
    assert all(np.array_equal(frames[0], frame) for frame in frames[1:6])
    assert not np.array_equal(frames[0], frames[6])
