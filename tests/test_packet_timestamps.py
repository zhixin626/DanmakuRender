"""Real B-frame roundtrip: no lost first frames or guessed presentation order."""

from fractions import Fraction

import av
import numpy as np

from DMR.Render.gpu_render import stamp_video


def test_packet_presentation_indices_survive_mp4_mux(tmp_path):
    codec = av.CodecContext.create("libx264", "w")
    codec.width, codec.height = 128, 128
    codec.pix_fmt = "yuv420p"
    codec.time_base = Fraction(1, 25)
    codec.framerate = Fraction(25)
    codec.options = {
        "preset": "ultrafast",
        "bf": "3",
        "x264-params": "b-adapt=0:scenecut=0",
    }
    packets = []
    for index in range(24):
        frame = av.VideoFrame.from_ndarray(
            np.full((128, 128, 3), 30 + index * 5, np.uint8), format="rgb24"
        )
        frame.pts = index
        packets.extend(codec.encode(frame))
    packets.extend(codec.encode(None))
    assert [p.pts for p in packets] != sorted(p.pts for p in packets)
    raw = tmp_path / "video.h264"
    raw.write_bytes(b"".join(bytes(p) for p in packets))
    output = tmp_path / "video.mp4"
    stamp_video(raw, output, 25, [p.pts for p in packets], 3)
    with av.open(str(output)) as reader:
        frames = list(reader.decode(video=0))
    assert len(frames) == 24
    assert [float(f.pts * f.time_base) for f in frames] == [i / 25 for i in range(24)]
    assert abs(float(frames[0].to_ndarray(format="rgb24").mean()) - 30) < 3
