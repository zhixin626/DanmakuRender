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
