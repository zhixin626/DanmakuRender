"""Exercise the BAT/main.py task-dispatch entry without starting DMR."""
from types import SimpleNamespace
from unittest.mock import Mock

from DMR.Task.liveevents import LiveEvents


def test_live_recording_dispatch_preserves_gpu_backend():
    handler = LiveEvents.__new__(LiveEvents)
    handler.name = '月亮3'
    handler.logger = Mock()
    options = {'engine':'gpu_render','format':'mp4'}
    handler.config = {'common_event_args':{'auto_render':True},
                      'render_args':{'overlay':options}}
    slot = {'wait':[],'status':'ready'}
    handler.state_dict = {'recording':[{'dm_video':slot}]}
    handler._dispatched = {}
    video = SimpleNamespace(path='recording.flv',group_id='recording')
    message, = handler._check_for_render(video)
    assert message.target == 'render'
    assert message.data['mode'] == 'gpu_render'
    assert message.data['args'] is options
    assert slot['status'] == 'rendering' and slot['wait'] == [message.request_id]
    handler.logger.warning.assert_not_called()
