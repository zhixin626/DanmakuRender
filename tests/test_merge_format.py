import json
from pathlib import Path

import merge_only
from DMR.Merger import video_merge


def meta(name, extradata="SHA256:aa", wh=(1920, 1080)):
    return {
        "path": Path(name),
        "wh": wh,
        "dur": 60.0,
        "ts": 90000,
        "fmt": ("hevc", "Main", "yuv420p", extradata, "aac", "48000", 2),
    }


def test_segments_split_where_encoder_headers_differ():
    metas = [meta("a"), meta("b"), meta("fallback", "SHA256:bb"), meta("c")]
    groups = video_merge.group_by_format(metas)
    assert [[m["path"].name for m in g] for g in groups] == [
        ["a", "b"], ["fallback"], ["c"]
    ]


def test_unprobed_segment_never_joins_a_group():
    broken = {**meta("broken"), "fmt": None}
    assert len(video_merge.group_by_format([meta("a"), broken, meta("b")])) == 3


def test_probe_reads_video_and_audio_format(monkeypatch):
    probe = {
        "streams": [
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
             "channels": 2},
            {"codec_type": "video", "codec_name": "hevc", "profile": "Main",
             "pix_fmt": "yuv420p", "extradata_hash": "SHA256:aa",
             "width": 1920, "height": 1080, "time_base": "1/90000"},
        ],
        "format": {"duration": "60"},
    }
    monkeypatch.setattr(
        video_merge.subprocess, "check_output",
        lambda *a, **k: json.dumps(probe).encode(),
    )
    assert video_merge.probe_seg("x.mp4") == meta("x.mp4")


def test_merge_only_refuses_mixed_formats_before_asking(monkeypatch, capsys):
    monkeypatch.setattr(merge_only, "ask_videos", lambda: ["a.mp4", "b.mp4"])
    monkeypatch.setattr(
        merge_only, "probe_seg",
        lambda path: meta(path, "SHA256:aa" if path == "a.mp4" else "SHA256:bb"),
    )

    def unexpected(*_):
        raise AssertionError("must stop before asking for options")

    monkeypatch.setattr("builtins.input", unexpected)
    merge_only.main()
    assert "编码格式不一致" in capsys.readouterr().out


def test_gain_is_one_value_for_the_whole_merge(tmp_path):
    import subprocess

    for index, level in ((1, -12), (2, -18)):
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
             "testsrc=size=160x90:rate=25", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=48000", "-t", "4",
             "-af", f"volume={level}dB", "-c:v", "libx264", "-bf", "0",
             "-c:a", "aac", "-shortest", str(tmp_path / f"s{index}.mp4")],
            check=True,
        )
    sources = [tmp_path / "s1.mp4", tmp_path / "s2.mp4"]
    peaks = [video_merge.detect(str(p)) for p in sources]
    metas = [video_merge.probe_seg(p) for p in sources]
    output = video_merge.merge_group(metas, tmp_path / "out.mp4", tmp_path, (-1, 0))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.mp4", "s1.mp4", "s2.mp4"]

    def peak(start):
        log = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", str(start), "-t", "2", "-i", str(output),
             "-vn", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        ).stderr
        return float(log.split("max_volume:")[1].split("dB")[0])

    # the loudest segment reaches the target; the other keeps its distance
    assert abs(peak(1) - (-1)) < 1
    assert abs((peak(1) - peak(5)) - (peaks[0] - peaks[1])) < 1
