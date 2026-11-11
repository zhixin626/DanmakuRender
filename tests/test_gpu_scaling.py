"""Sampling geometry and decoder lookahead ownership without a CUDA device."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from DMR.Render import gpu_render as gpu


def test_half_pixel_and_left_chroma_sampling():
    lower, weight = gpu.sample_axis(np, 4, 8)
    assert lower.tolist() == [0, 0, 0, 1, 1, 2, 2, 3]
    assert weight.tolist() == [0, 0.25, 0.75, 0.25, 0.75, 0.25, 0.75, 0]
    lower, weight = gpu.sample_axis(np, 2, 4, 0.25, 0.25)
    assert np.allclose(lower + weight, [0, 0.375, 0.875, 1])
    lower, weight = gpu.sample_axis(np, 2, 4, 0.5, 0.25)
    assert np.allclose(lower + weight, [0, 0.125, 0.625, 1])


@pytest.mark.parametrize(
    "times",
    [
        [0, 40, 80, 120, 160, 200, 240, 280, 320],
        [0, 5, 10, 100, 105, 110, 240, 245, 400],
    ],
)
@pytest.mark.parametrize("resize", [False, True])
def test_three_owned_slots_protect_cfr_and_vfr_lookahead(monkeypatch, times, resize):
    allocations, clock = [], {"recorded": 0, "completed": 0, "decode_calls": 0}

    class Event:
        def __init__(self, **_):
            self.sequence = 0

        def record(self):
            clock["recorded"] += 1
            self.sequence = clock["recorded"]

        def synchronize(self):
            clock["completed"] = max(clock["completed"], self.sequence)

    class Owned:
        def __init__(self, cp, width, height):
            self.width, self.height = width, height
            allocations.append(self)

        def load(self, decoded):
            self.value = decoded.value

    class Demux:
        def __iter__(self):
            return iter(range(0, len(times) - 2, 2))

        def GetNvCodecId(self):
            return "h264"

        def GetTimebaseNum(self):
            return 1

        def GetTimebaseDen(self):
            return 1000

    def decode(packet):
        # Advancing the SDK is unsafe until all prior source copies completed.
        assert clock["completed"] == clock["recorded"]
        clock["decode_calls"] += 1
        indices = (
            range(packet, min(packet + 2, len(times)))
            if packet is not None
            else range(len(times) - 1, len(times))
        )
        return [SimpleNamespace(value=i, getPTS=lambda i=i: times[i]) for i in indices]

    nvc = SimpleNamespace(
        CreateDemuxer=lambda **_: Demux(),
        CreateDecoder=lambda **_: SimpleNamespace(Decode=decode),
        PacketData=lambda: None,
    )
    cp = SimpleNamespace(cuda=SimpleNamespace(Event=Event))
    monkeypatch.setattr(gpu, "load_cuda_modules", lambda: (cp, nvc))
    monkeypatch.setattr(
        gpu,
        "frame_geometry",
        lambda decoded: (4, 4) if resize and decoded.value >= 4 else (2, 2),
    )
    monkeypatch.setattr(gpu, "NV12Buffer", Owned)
    frames = gpu.decode_frames(Path("sample.flv"), nvc, 0, cp)
    first = next(frames)
    actual = [
        (owned.value, seconds)
        for owned, seconds in gpu.schedule_frames(frames, first, 50, source_fps=25)
    ]
    expected = list(
        gpu.schedule_frames(
            iter(list(enumerate(t / 1000 for t in times))[1:]),
            (0, 0),
            50,
            source_fps=25,
        )
    )
    assert actual == expected
    assert len(allocations) == (6 if resize else 3)
    assert clock["completed"] == clock["recorded"]


def test_production_gpu_module_has_no_test_cli():
    assert not hasattr(gpu, "main")
