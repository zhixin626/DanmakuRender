"""Real task dispatch/plugin/end/reconcile, with only media workers substituted."""
import queue
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from DMR.Render import Render, get_engine
from DMR.Render import gpu_render as gpu
from DMR.Render import emoji_dmrender as emoji
from DMR.Render.baserender import BaseRender, RenderStopped
from DMR.Task.liveevents import LiveEvents
from DMR.utils import VideoInfo, FFprobe, PipeMessage


@pytest.mark.parametrize('engine,outcome,next_stage',[
    ('emoji_dmrender','success','uploader'),
    ('gpu_render','success','uploader'),
    ('gpu_render','success','merger'),
    ('gpu_render','fallback','uploader'),
    ('gpu_render','stopped',None),
    ('gpu_render','fallback_stopped',None),
])
def test_segment_render_and_state_progression(monkeypatch,tmp_path,engine,outcome,next_stage):
    options = yaml.safe_load(Path('DMR/Config/default.yml').read_text(encoding='utf8'))['render_args']['overlay']
    options.update(engine=engine,retry=2,output_dir=str(tmp_path/'rendered'))
    config = {'download_args':{'output_dir':str(tmp_path)},'render_args':{'overlay':options},
              'common_event_args':{'auto_render':True,'auto_upload':next_stage=='uploader',
                                   'auto_merge':next_stage=='merger','auto_clean':False},
              'merge_args':{'merge_type':['dm_video']},
              'upload_args':{'dm_video':[{'realtime':True,'engine':'test-upload'}]}}
    handler = LiveEvents('moon',config)
    source_path = tmp_path/'recording.mp4'
    source_path.write_bytes(b'source fixture')
    events = tmp_path/'recording.jsonl'
    events.write_text('{"type":"meta","schema":1,"platform":"bilibili","segment_start_time":0}\n'
                      '{"type":"superchat","received_at":0.2,"uid":"test","uname":"SC",'
                      '"price_cny":30,"content":"主流程面板 😀"}\n',encoding='utf8')
    video = VideoInfo(path=str(source_path),file_id='original',group_id='session',segment_id=3,
                      taskname='moon',resolution=(1920,1080),duration=1,dm_file_id=str(events))
    observed_ready = []
    original_reconcile = handler.reconcile
    def reconcile(*args,**kwargs):
        observed_ready.append(handler.state_dict['session'][-1]['dm_video']['status'])
        return original_reconcile(*args,**kwargs)
    handler.reconcile = reconcile
    monkeypatch.setattr(FFprobe,'get_timeline_duration',lambda _:1)
    gpu_calls = []
    def gpu_worker(video,output,progress_cb,stop_event,**kwargs):
        gpu_calls.append(output)
        plan = json.loads(Path(kwargs['render_plan']).read_text(encoding='utf8'))
        assert any(e['visual']['type']=='panel' and e['mode']=='raster' for e in plan['events'])
        progress_cb(45)
        if outcome in ('fallback','fallback_stopped'):
            raise RuntimeError('simulated NVENC failure')
        if outcome=='stopped':
            stop_event.set()
            raise RenderStopped('user cancellation')
        Path(output).write_bytes(b'gpu encoded fixture')
        progress_cb(100)
        return dict(width=1920,height=1080,frames=60,frame_rate=60)
    monkeypatch.setattr(gpu,'render_one',gpu_worker)
    fallback_calls = []
    def emoji_process(command,**_):
        fallback_calls.append(command)
        plan = json.loads(Path(command[3]).read_text(encoding='utf8'))
        assert any(e['visual']['type']=='panel' and e['mode']=='ass' for e in plan['events'])
        Path(command[4]).write_bytes(b'emoji encoded fixture')
        if outcome=='fallback_stopped':
            # Simulate a user stop during the fallback, not another retry.
            plugin._render_class[next(iter(plugin._render_class))].stop()
        return SimpleNamespace(stdout=iter(['[DMR_PROGRESS] 45\n','[DMR_PROGRESS] 100\n']),
                               wait=lambda **_:0,poll=lambda:0,returncode=0)
    monkeypatch.setattr(emoji.subprocess,'Popen',emoji_process)
    monkeypatch.setattr('DMR.Render.time.sleep',lambda _:None)
    send = queue.Queue()
    plugin = Render((send,queue.Queue()))
    plugin.stoped = False
    progress = []
    gather = plugin._gather
    def observe_gather(task,status,desc=''):
        progress.append(task['progress'])
        gather(task,status,desc)
    plugin._gather = observe_gather
    messages = handler.onLiveSegment(PipeMessage(source='downloader',target='moon',event='livesegment',data=video))
    dispatch, = [message for message in messages if message.target=='render' and message.event=='newtask']
    slot = handler.state_dict['session'][-1]['dm_video']
    if next_stage=='merger':
        handler.ended_dict['session'] = time.time()
    plugin.add_task(dispatch)
    completion = send.get(timeout=5)
    if outcome in ('stopped','fallback_stopped'):
        assert completion.event=='stopped' and '已停止' in completion.msg
        assert handler.event_dict['render/stopped'](completion)==[]
        assert slot['status']=='stopped' and slot['wait']==[]
        assert observed_ready==['rendering','stopped']
        assert len(handler.held_tasks)==1 and next(iter(handler.held_tasks.values()))['cancelled']
        assert not list((tmp_path/'rendered').glob('*.mp4'))
        assert len(gpu_calls)==(3 if outcome=='fallback_stopped' else 1)
        assert len(fallback_calls)==(1 if outcome=='fallback_stopped' else 0)
    else:
        assert completion.event=='end'
        output = completion.data['output']
        assert isinstance(output,VideoInfo)
        assert (output.group_id,output.segment_id,output.src_video_id)==('session',3,'original')
        followups = handler.onRenderEnd(completion)
        assert observed_ready==['rendering','ready']
        assert len([msg for msg in followups if msg.target==next_stage])==1
        assert all(msg.target in (next_stage,'render') for msg in followups)
        assert progress==['100%']
        assert slot['status']==('uploading' if next_stage=='uploader' else 'merging')
        assert len(fallback_calls)==(1 if engine=='emoji_dmrender' or outcome=='fallback' else 0)
    assert not plugin.render_tasks
    plugin.render_executors.shutdown()


def test_output_info_uses_canvas_and_preserves_identity(tmp_path):
    source = VideoInfo(path='source.mp4',group_id='session',segment_id=9,file_id='original',resolution=(640,480))
    output = tmp_path/'out.mp4'
    output.write_bytes(b'frame')
    result = BaseRender.output_info(source,str(output),(1920,1080),None)
    assert result.resolution==(1920,1080) and result.group_id=='session' and result.segment_id==9
    assert source.resolution==(640,480) and result.file_id!=source.file_id


def test_unknown_engine_fails_in_dispatch_and_layout(tmp_path):
    from DMR.Render.layout_compiler import LayoutCompiler
    with pytest.raises(ValueError,match='未知渲染引擎'):
        get_engine('typo-engine')
    with pytest.raises(ValueError,match='未知渲染引擎'):
        LayoutCompiler(engine='typo-engine')
    handler = LiveEvents('demo',{'download_args':{'output_dir':str(tmp_path)},
        'common_event_args':{'auto_render':True},'render_args':{'overlay':{'engine':'typo-engine'}}})
    with pytest.raises(ValueError,match='未知渲染引擎'):
        handler._check_for_render(VideoInfo(path='clip.mp4'))


def test_carried_gpu_panel_is_rebuilt_for_the_fallback_backend(tmp_path):
    from DMR.Render.layout_track import LayoutTrackManager
    options = yaml.safe_load(Path('DMR/Config/default.yml').read_text(encoding='utf8'))['render_args']['overlay']
    manager = LayoutTrackManager()
    manager.begin('live')
    outputs = []
    try:
        for sequence,engine in enumerate(('gpu_render','emoji_dmrender')):
            events = tmp_path/f'{sequence}.jsonl'
            text = json.dumps({'type':'meta','schema':1,'platform':'bilibili','segment_start_time':sequence})+'\n'
            if sequence==0:
                text += json.dumps({'type':'superchat','received_at':.2,'uid':'test','uname':'SC',
                                    'price_cny':30,'content':'跨段保留'})+'\n'
            events.write_text(text,encoding='utf8')
            video = VideoInfo(path=f'{sequence}.mp4',file_id=str(sequence),duration=1,resolution=(1920,1080))
            compiled = manager.compile_segment(track_key='live',sequence=sequence,video=video,events_path=str(events),
                        layout_config=options,engine=engine,engine_config=options,work_dir=str(tmp_path))
            outputs.append(compiled)
        plan = json.loads(Path(outputs[1].render_plan_path).read_text(encoding='utf8'))
        assert any(e['visual']['type']=='panel' and e['mode']=='ass' for e in plan['events'])
        assert outputs[0].next_state.events[0]['mode']=='raster'
    finally:
        for output in outputs:
            output.cleanup()
