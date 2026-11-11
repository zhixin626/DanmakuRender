import itertools
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import numpy as np
import pytest

from DMR.Render import gpu_render as gpu
from DMR.Render.sprites import build_atlas, detect_platform


def fake_encoding(monkeypatch):
    monkeypatch.setattr(gpu, "frame_geometry", lambda _: (2, 2))
    encoder = SimpleNamespace(
        Encode=Mock(return_value=[]),
        EndEncode=Mock(return_value=[]),
        GetNumBFrames=lambda: 3,
    )
    monkeypatch.setattr(gpu, "create_encoder", lambda *_: encoder)
    monkeypatch.setattr(
        gpu, "NV12Buffer", lambda *_: SimpleNamespace(load_into_canvas=lambda _: None)
    )
    monkeypatch.setattr(gpu, "upload_atlas", lambda *_: {"events": []})
    monkeypatch.setattr(
        gpu, "SpriteCompositor", lambda *_: SimpleNamespace(apply=lambda *_: 0)
    )
    clock = itertools.count()
    monkeypatch.setattr(gpu.time, "perf_counter", lambda: next(clock))
    sync = Mock()
    stream = MagicMock()
    stream.synchronize = sync
    cp = SimpleNamespace(
        cuda=SimpleNamespace(Stream=Mock(return_value=stream), Event=Mock)
    )
    config = SimpleNamespace(
        video_encoder="hevc_nvenc", encoder_options={}, atlas={}, max_frames=None
    )
    return encoder, cp, config, sync


def test_sprite_compositor_uses_slot_metadata_without_frame_sync():
    captured = []

    class Device:
        def __init__(self, shape):
            self.data = np.zeros(shape, dtype=np.int32)

        def __getitem__(self, item):
            parent = self

            class View:
                def set(self, value):
                    parent.data[item] = value

            return View()

    class Cupy:
        int32 = np.int32

        class cuda:
            @staticmethod
            def alloc_pinned_memory(size):
                return bytearray(size)

        @staticmethod
        def empty(shape, dtype):
            return Device(shape)

        @staticmethod
        def RawKernel(*_args):
            def kernel(_grid, _block, args):
                captured.append(args[4].data.copy())

            return kernel

    event = SimpleNamespace(offset=4, width=2, height=2)
    compositor = gpu.SpriteCompositor(Cupy, 4, 4, {"events": [], "y": [], "u": [], "v": [], "a": []})
    compositor.event_scanner.visible = lambda _timestamp: [(event, 1, 2, 255)]
    frame = SimpleNamespace(
        y=np.zeros((4, 4, 1), dtype=np.uint8),
        uv=np.zeros((2, 2, 2), dtype=np.uint8),
    )
    compositor.apply(frame, 0.0, 0)
    event.offset = 8
    compositor.apply(frame, 0.1, 0)
    assert captured[0][0].tolist() == [1, 2, 4, 2, 2, 255]
    assert captured[1][0].tolist() == [1, 2, 8, 2, 2, 255]


def test_progress_and_one_sync_per_frame(monkeypatch, tmp_path):
    encoder, cp, config, sync = fake_encoding(monkeypatch)
    progress = []
    result = gpu.encode_to_file(
        iter([(1, 0.04), (2, 0.08)]),
        (0, 0),
        cp,
        SimpleNamespace(NV_ENC_PIC_PARAMS=SimpleNamespace),
        tmp_path / "out.hevc",
        (2, 2),
        25,
        config,
        source_frame_rate=25,
        progress_cb=progress.append,
        expected_frames=3,
    )
    assert result["frames"] == 3
    assert progress == sorted(progress) and 0 < progress[0] < progress[-1] <= 99
    assert encoder.Encode.call_count == 3
    assert sync.call_count == 0
    encoder.EndEncode.assert_called_once()


def test_stop_interrupts_encoding(monkeypatch, tmp_path):
    encoder, cp, config, sync = fake_encoding(monkeypatch)
    event = threading.Event()
    with pytest.raises(gpu.RenderStopped):
        gpu.encode_to_file(
            iter([(1, 0.04), (2, 0.08)]),
            (0, 0),
            cp,
            SimpleNamespace(NV_ENC_PIC_PARAMS=SimpleNamespace),
            tmp_path / "out.hevc",
            (2, 2),
            25,
            config,
            source_frame_rate=25,
            progress_cb=lambda _: event.set(),
            expected_frames=3,
            stop_event=event,
        )
    assert encoder.Encode.call_count == 1
    encoder.EndEncode.assert_not_called()


def test_cancel_cleans_partial_and_releases_decoder(monkeypatch, tmp_path):
    closed = []

    def frames(*_):
        try:
            yield SimpleNamespace(), 0
        finally:
            closed.append(True)

    free = Mock()
    cp = SimpleNamespace(
        cuda=SimpleNamespace(Device=lambda _: SimpleNamespace(use=lambda: None)),
        get_default_memory_pool=lambda: SimpleNamespace(free_all_blocks=free),
    )
    monkeypatch.setattr(gpu, "load_cuda_modules", lambda: (cp, None))
    monkeypatch.setattr(gpu, "decode_frames", frames)
    monkeypatch.setattr(gpu, "source_frame_rate", lambda _: 25)
    monkeypatch.setattr(
        gpu.FFprobe,
        "run_ffprobe",
        lambda _: {"format": {"duration": 1, "start_time": 0}, "streams": [{}]},
    )

    def interrupt(*args, **kwargs):
        args[4].write_bytes(b"unfinished")
        kwargs["stop_event"].set()
        gpu.check_stop(kwargs["stop_event"])

    monkeypatch.setattr(gpu, "encode_to_file", interrupt)
    engine = gpu.GpuRender()
    destination = tmp_path / "out.mp4"
    ok, error = engine.render_one(
        "source.flv",
        str(destination),
        atlas={"atlas_bytes": 0},
        canvas_resolution=(2, 2),
    )
    assert not ok and isinstance(error, gpu.RenderStopped)
    assert closed and free.call_count == 1
    assert not destination.exists() and not list(tmp_path.iterdir())


def test_stopping_before_start_never_opens_cuda(monkeypatch, tmp_path):
    load = Mock()
    monkeypatch.setattr(gpu, "load_cuda_modules", load)
    engine = gpu.GpuRender()
    engine.stop()
    ok, error = engine.render_one("source.flv", str(tmp_path / "out.mp4"))
    assert not ok and isinstance(error, gpu.RenderStopped)
    load.assert_not_called()


def test_atlas_is_in_memory_and_preserves_repeated_events(tmp_path):
    style = {
        "font": "Microsoft YaHei",
        "bold": False,
        "size": 30,
        "color": "#FFFFFF",
        "opacity": 1,
        "outline": 1,
        "outline_color": "#000000",
    }
    visual = {"type": "line", "text": "same text", "style": style}
    plan = {
        "duration": 2,
        "events": [
            {
                "id": str(i),
                "bounds": {"width": 150, "height": 40},
                "path": [[i, 10, 10], [i + 1, 0, 10]],
                "visual": visual,
            }
            for i in (0, 1)
        ],
    }
    result = build_atlas(plan, emoticon_dir=tmp_path / "emoticon")
    assert len(result["events"]) == 2
    assert all(isinstance(result[key], np.ndarray) for key in ("y", "u", "v", "a"))
    assert result["atlas_bytes"] == sum(
        result[key].nbytes for key in ("y", "u", "v", "a")
    )
    assert not list(tmp_path.rglob("*.npz")) and not list(tmp_path.rglob("events.json"))
    assert result["events"][0]["offset"] != result["events"][1]["offset"]


def test_shared_platform_detection(tmp_path):
    source = tmp_path / "events.jsonl"
    source.write_text('{"type":"meta","platform":"bilibili"}\n', encoding="utf-8")
    assert detect_platform(source) == "bilibili"


@pytest.mark.parametrize("extension,pretty", [(".jsonl", False), (".json", True)])
def test_structured_platform_wins_over_urls_in_content(tmp_path, extension, pretty):
    import json
    from DMR.Render.layout_models import RenderPlan

    source = tmp_path / ("events" + extension)
    source.write_text(
        json.dumps(
            {
                "type": "meta",
                "platform": "douyin",
                "note": "https://live.bilibili.com/123",
            },
            indent=2 if pretty else None,
        ),
        encoding="utf-8-sig",
    )
    assert detect_platform(source) == "douyin"
    plan = tmp_path / "plan.json"
    RenderPlan(1, [], platform="douyu").save(str(plan))
    assert RenderPlan.load(str(plan)).platform == "douyu"
    assert detect_platform(plan) == "douyu"


def test_invalid_structured_header_keeps_legacy_fallback(tmp_path):
    source = tmp_path / "old.jsonl"
    source.write_text("legacy https://live.bilibili.com/123", encoding="utf8")
    assert detect_platform(source) == "bilibili"


def test_offline_configuration_merges_disabled_task(tmp_path, monkeypatch):
    from DMR.Config import Config
    from DMR.utils import ToolsList

    monkeypatch.setattr(ToolsList, "get", lambda *_, **__: "ffmpeg")
    global_file = tmp_path / "global.yml"
    global_file.write_text(
        f"dmr_engine_args:\n  config_path: {tmp_path.as_posix()}\nrender_args:\n  overlay:\n    vencoder_args: [-b:v, 4M]\n    slide_left:\n      fontsize: 26\n",
        encoding="utf-8",
    )
    (tmp_path / "DMR-demo.yml").write_text(
        "enable: false\nrender_args:\n  overlay:\n    engine: gpu_render\n    slide_left:\n      fontsize: 18\n",
        encoding="utf-8",
    )
    config = Config(str(global_file))
    args = config.get_render_args("demo")
    assert args["engine"] == "gpu_render"
    assert args["vencoder_args"] == ["-b:v", "4M"]
    assert (
        args["slide_left"]["fontsize"] == 18
        and args["slide_left"]["corner_radius"] == "20px"
    )
    assert "demo" not in config.task_configs
    with pytest.raises(ValueError):
        config.get_render_args("unknown")


def test_enabled_pipeline_configuration_uses_isolated_fixture(tmp_path, monkeypatch):
    # Personal configs and launchers are user-editable runtime inputs. Validate
    # the loader contract with a fixture instead of asserting the user's toggle.
    from DMR.Config import Config
    from DMR.utils import ToolsList

    (tmp_path / "DMR-demo.yml").write_text(
        "enable: true\ncommon_event_args:\n  auto_render: true\n  auto_upload: true\n"
        "render_args:\n  overlay:\n    engine: gpu_render\n"
        "upload_args:\n  dm_video:\n    target: bilibili\n    account: demo\n",
        encoding="utf8",
    )
    global_file = tmp_path / "global.yml"
    global_file.write_text(
        f"dmr_engine_args:\n  config_path: {tmp_path.as_posix()}\n", encoding="utf8"
    )
    monkeypatch.setattr(ToolsList, "get", lambda *_, **__: "ffmpeg")
    config = Config(str(global_file)).get_task_config("demo")
    assert config["render_args"]["overlay"]["engine"] == "gpu_render"
    assert config["upload_args"]["dm_video"][0]["account"] == "demo"
