from types import SimpleNamespace
from unittest.mock import Mock

from DMR.Render import gpu_render as gpu


def test_four_slots_wait_for_their_own_copy_before_reuse(monkeypatch):
    buffers = [object() for _ in range(4)]
    created = iter(buffers)
    monkeypatch.setattr(gpu,'NV12Buffer',lambda *_:next(created))
    events = [Mock() for _ in buffers]
    event_iter = iter(events)
    cp = SimpleNamespace(cuda=SimpleNamespace(Event=lambda **_:next(event_iter)))
    stream = object()
    ring = gpu.FrameRing(cp,2,2,stream)
    for index in range(4):
        assert ring.acquire(index) is buffers[index]
        ring.submitted(index)
        events[index].record.assert_called_once_with(stream)
        events[index].synchronize.assert_not_called()
    assert ring.acquire(4) is buffers[0]
    events[0].synchronize.assert_called_once()
    assert all(event.synchronize.call_count==0 for event in events[1:])
