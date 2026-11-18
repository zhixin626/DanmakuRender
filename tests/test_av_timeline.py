import json
import subprocess
import threading
from fractions import Fraction

import av
import numpy as np
import pytest

from DMR.Render import gpu_render
from DMR.Render.av_timeline import SharedTimeline, mux_streams, render_audio

RATE = 48000
ORIGIN = 100.0  # mpegts output_ts_offset; the muxer adds 1.4 s of delay
START = 1.4


def piece(folder, name, content_start, content_end, pts_start):
    """AAC in MPEG-TS whose level encodes the content second (sec % 10)."""
    count = int(round((content_end - content_start) * RATE))
    seconds = content_start + np.arange(count) / RATE
    level = ((seconds.astype(int) % 10) + 1) / 20
    pcm = np.repeat(level[None, :], 2, axis=0).astype(np.float32)
    plain = folder / name
    with av.open(str(plain), "w", format="mpegts") as out:
        stream = out.add_stream("aac", rate=RATE)
        stream.codec_context.layout = "stereo"
        stream.codec_context.bit_rate = 320000
        for index in range(0, count, 1024):
            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(pcm[:, index : index + 1024]),
                format="fltp",
                layout="stereo",
            )
            frame.sample_rate = RATE
            frame.pts = index
            frame.time_base = Fraction(1, RATE)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    shifted = folder / f"shifted_{name}"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(plain), "-c", "copy",
         "-output_ts_offset", str(pts_start + ORIGIN), "-f", "mpegts", str(shifted)],
        check=True,
    )
    return shifted.read_bytes()


def glitchy_source(folder):
    """0-10 s; re-sent 8-10 s (step back 2 s) then 10-12 s; a 3 s gap before
    12-15 s; then a reset (+885 s) carrying 15-20 s."""
    source = folder / "source.ts"
    source.write_bytes(
        piece(folder, "a.ts", 0, 10, 0)
        + piece(folder, "b.ts", 8, 12, 8)
        + piece(folder, "c.ts", 12, 15, 15)
        + piece(folder, "d.ts", 15, 20, 900)
    )
    return source


def heard(path, seconds):
    """Content second heard in the middle of each output second (-1 = silence)."""
    with av.open(str(path)) as container:
        pcm = np.concatenate(
            [frame.to_ndarray() for frame in container.decode(audio=0)], axis=1
        )[0]
    result = []
    for second in seconds:
        middle = pcm[int((second + 0.4) * RATE) : int((second + 0.6) * RATE)]
        result.append(round(float(np.median(np.abs(middle))) * 20 - 1))
    return result


def render(folder, timeline):
    output = folder / "audio.m4a"
    render_audio(
        glitchy_source(folder), output, ORIGIN, "aac", 320000, {}, timeline,
        tolerance=0.5 / 30,
    )
    return output


def test_audio_follows_video_timeline_rules(tmp_path):
    timeline = SharedTimeline()
    timeline.close(24.0)  # the video had no reset: audio uses its own shift
    output = render(tmp_path, timeline)
    # content c plays at output START + c, plus 3 s after the gap; the re-sent
    # 8-10 s is dropped, the gap is silent, the reset continues seamlessly.
    expected = {START + c: c % 10 for c in (1, 5, 9, 10, 11)}
    expected.update({START + 12 + g: -1 for g in (0.5, 1.5)})
    expected.update({START + 3 + c: c % 10 for c in (12, 14, 15, 18)})
    assert heard(output, list(expected)) == list(expected.values())
    with av.open(str(output)) as container:
        assert container.streams.audio[0].duration * container.streams.audio[0].time_base >= 24


def test_audio_reuses_the_video_shift_at_a_reset(tmp_path):
    # Own shift would continue right after 15 s of content (output 19.4 s);
    # the video continued 0.5 s later, so the audio must too.
    own = START + 18 - (START + 900)
    timeline = SharedTimeline()
    timeline.add_shift(own + 0.5)
    timeline.close(25.0)
    output = render(tmp_path, timeline)
    # middles at START+18.25 (inside the 0.5 s silence) and START+19 (content 15)
    assert heard(output, [START + 17.75, START + 18.5]) == [-1, 5]


def test_audio_waits_for_the_video_decision(tmp_path):
    timeline = SharedTimeline()
    done = threading.Event()

    def run():
        render(tmp_path, timeline)
        done.set()

    worker = threading.Thread(target=run)
    worker.start()
    assert not done.wait(1.0)  # blocked at the reset until the video decides
    timeline.add_shift(START + 18 - (START + 900))
    timeline.close(24.0)
    worker.join(30)
    assert done.is_set()


def test_failed_video_releases_the_audio(tmp_path):
    timeline = SharedTimeline()
    timeline.close()  # video failed: no length
    with pytest.raises(gpu_render.RenderStopped):
        render(tmp_path, timeline)


def test_one_pass_mux_trims_audio_to_video(tmp_path):
    timeline = SharedTimeline()
    timeline.close(24.0)
    audio = render(tmp_path, timeline)
    raw = tmp_path / "video.h264"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=160x90:rate=25", "-t", "20", "-c:v", "libx264", "-bf", "0",
         "-f", "h264", str(raw)],
        check=True,
    )
    output = tmp_path / "out.mp4"
    mux_streams(raw, list(range(500)), 0, 25, audio, output, 20.0)
    streams = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            capture_output=True, text=True, check=True,
        ).stdout
    )["streams"]
    durations = {s["codec_type"]: float(s["duration"]) for s in streams}
    assert durations["video"] == pytest.approx(20.0)
    assert abs(durations["audio"] - durations["video"]) < 1024 / RATE + 1e-6


def test_video_scheduler_publishes_reset_shifts():
    timeline = SharedTimeline()
    frames = [(i, i * 0.04) for i in range(5)] + [(9, 500.0), (10, 500.04)]
    list(gpu_render.schedule_frames(iter(frames[1:]), frames[0], 25, source_fps=25, timeline=timeline))
    timeline.close(1.0)
    assert timeline.shift(0) == pytest.approx(0.16 + 0.04 - 500.0)
    assert timeline.shift(1) is None


def test_audio_encoder_args():
    assert gpu_render.parse_audio_encoder_args(["-b:a", "320K", "-aac_coder", "fast"]) == (
        320000,
        {"aac_coder": "fast"},
    )
    with pytest.raises(ValueError):
        gpu_render.parse_audio_encoder_args(["-b:a"])


def fake_gpu(monkeypatch, encode):
    from types import SimpleNamespace
    from unittest.mock import Mock

    def frames(*_):
        yield SimpleNamespace(), 0.0

    cp = SimpleNamespace(
        cuda=SimpleNamespace(Device=lambda _: SimpleNamespace(use=lambda: None)),
        get_default_memory_pool=lambda: SimpleNamespace(free_all_blocks=Mock()),
    )
    monkeypatch.setattr(gpu_render, "load_cuda_modules", lambda: (cp, None))
    monkeypatch.setattr(gpu_render, "decode_frames", frames)
    monkeypatch.setattr(gpu_render, "source_frame_rate", lambda _: 25)
    monkeypatch.setattr(
        gpu_render.FFprobe,
        "run_ffprobe",
        lambda _: {"format": {"duration": 4, "start_time": 0}, "streams": [{}]},
    )
    monkeypatch.setattr(gpu_render, "encode_to_file", encode)


def source_with_audio(folder):
    source = folder / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=160x90:rate=25", "-f", "lavfi", "-i", "sine", "-t", "4",
         "-c:v", "libx264", "-bf", "0", "-c:a", "aac", "-shortest", str(source)],
        check=True,
    )
    return source


def test_render_one_encodes_audio_beside_video(monkeypatch, tmp_path):
    source = source_with_audio(tmp_path)

    def encode(*args, **kwargs):
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(source), "-c:v", "copy",
             "-an", "-f", "h264", str(args[4])],
            check=True,
        )
        return {"frames": 100, "packet_times": list(range(100)), "bframes": 0}

    fake_gpu(monkeypatch, encode)
    output = tmp_path / "out.mp4"
    result = gpu_render.render_one(
        str(source), str(output), without_danmaku=True, canvas_resolution=(160, 90),
        aencoder="aac", aencoder_args=["-b:a", "128K"],
    )
    assert result["frames"] == 100
    durations = gpu_render.validate_av_duration(output)
    assert durations["video"] == pytest.approx(4.0)
    assert abs(durations["audio"] - 4.0) < 1024 / 44100 + 1e-6
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.mp4", "source.mp4"]


def test_stopping_video_releases_running_audio(monkeypatch, tmp_path):
    source = source_with_audio(tmp_path)

    def encode(*args, **kwargs):
        kwargs["stop_event"].set()
        gpu_render.check_stop(kwargs["stop_event"])

    fake_gpu(monkeypatch, encode)
    engine = gpu_render.GpuRender(aencoder="aac")
    ok, error = engine.render_one(
        str(source), str(tmp_path / "out.mp4"), without_danmaku=True,
        canvas_resolution=(160, 90),
    )
    assert not ok and isinstance(error, gpu_render.RenderStopped)
    assert [p.name for p in tmp_path.iterdir()] == ["source.mp4"]
