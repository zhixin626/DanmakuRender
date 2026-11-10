"""Cancellation must survive memory reclamation and permit real redispatch."""
import json
import queue
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from DMR.Task.liveevents import LiveEvents
from DMR.Render import Render
from DMR.Render import gpu_render as gpu
from DMR.Render.baserender import BaseRender, RenderStopped
from DMR.Merger import Merger
from DMR.utils import VideoInfo, FFprobe, PipeMessage
from DMR.Render.sprites import resolve_font


def configuration(tmp_path):
    options = yaml.safe_load(Path('DMR/Config/default.yml').read_text(encoding='utf8'))['render_args']['overlay']
    options.update(engine='gpu_render',output_dir=str(tmp_path/'rendered'))
    return {'download_args':{'output_dir':str(tmp_path),'dltype':'live'},
        'render_args':{'overlay':options},
        'common_event_args':{'auto_render':True,'auto_upload':False,'auto_merge':True,'auto_clean':False},
        'merge_args':{'merge_type':['dm_video'],'remove_source':False}}


@pytest.mark.parametrize('restart',[False,True])
def test_stop_idle_resume_complete_merge(monkeypatch,tmp_path,restart):
    config = configuration(tmp_path)
    handler = LiveEvents('test',config)
    source = tmp_path/'source.flv'
    source.write_bytes(b'video fixture')
    events = tmp_path/'source.jsonl'
    events.write_text('{"type":"meta","schema":1,"platform":"bilibili","segment_start_time":0}\n',encoding='utf8')
    video = VideoInfo(path=str(source),dm_file_id=str(events),duration=1,resolution=(1920,1080),
                      group_id='session',segment_id=1,file_id='original',ctime=datetime.now())
    monkeypatch.setattr(FFprobe,'get_timeline_duration',lambda _:1)
    monkeypatch.setattr(handler,'_collect_session_data',lambda _:None)
    attempts = []
    def worker(video,output,stop_event,**kwargs):
        attempts.append(output)
        if len(attempts)==1:
            stop_event.set()
            raise RenderStopped('test stop')
        Path(output).write_bytes(b'rendered fixture')
        return {'width':1920,'height':1080,'frames':50,'frame_rate':50}
    monkeypatch.setattr(gpu,'render_one',worker)
    send = queue.Queue()
    plugin = Render((send,queue.Queue()),subprocess=1,render_thread=1)
    merger = Merger((send,queue.Queue()))
    plugin.stoped = False
    merger.stoped = False
    try:
        handler.session_data['session'] = {'is_live_end':False}
        earlier = VideoInfo(**dict(video,path=str(tmp_path/'earlier.flv'),file_id='earlier',segment_id=0))
        Path(earlier.path).write_bytes(b'earlier source')
        messages = handler.onLiveSegment(PipeMessage('downloader','test','livesegment',data=earlier))
        earlier_dispatch, = [m for m in messages if m.target=='render' and m.event=='newtask']
        rendered = tmp_path/'earlier.mp4'
        rendered.write_bytes(b'earlier rendered')
        earlier_output = BaseRender.output_info(earlier,str(rendered),(1920,1080),None)
        handler.onRenderEnd(PipeMessage('render','test','end',request_id=earlier_dispatch.request_id,
            data={'config':{'mode':'gpu_render'},'output':earlier_output}))
        messages = handler.onLiveSegment(PipeMessage('downloader','test','livesegment',data=video))
        dispatch, = [m for m in messages if m.target=='render' and m.event=='newtask']
        handler.onLiveEnd(PipeMessage('downloader','test','liveend',data='session'))
        plugin.add_task(dispatch)
        completion = send.get(timeout=5)
        assert completion.event=='stopped'
        handler.onRenderStop(completion)
        assert 'session' not in handler.state_dict  # durable archive, not leaked live state
        assert handler.is_idle_for_restart()
        hold_id, = handler.held_tasks
        assert handler._resume_path.exists() and source.exists()
        if restart:
            handler.onExit()
            handler = LiveEvents('test',config)
            ready = handler.onReady()
            retry, = [m for m in ready if m.target=='render']
            assert not isinstance(retry.data['video'].ctime,str)
            second_ready = handler.onReady()
            assert isinstance(second_ready,PipeMessage) and second_ready.target=='downloader'
        else:
            retry, = handler.onRetry(PipeMessage('webservice','test','retry',data={'hold_id':hold_id}))
        assert retry.request_id!=dispatch.request_id
        assert not handler.is_idle_for_restart()
        plugin.add_task(retry)
        completion = send.get(timeout=5)
        assert completion.event=='end'
        followups = handler.onRenderEnd(completion)
        merge, = [m for m in followups if m.target=='merger']
        def merge_worker(paths,args,meta):
            assert len(paths)==2 and str(rendered) in paths
            out = tmp_path/'merged.mp4'
            out.write_bytes(b'merged fixture')
            return [VideoInfo(path=str(out),group_id=meta['group_id'],dtype='dm_video',resolution=(1920,1080))]
        monkeypatch.setattr(merger,'_merge_pipeline',merge_worker)
        merger.add_task(merge)
        completion = send.get(timeout=5)
        assert completion.event=='end' and completion.source=='merger'
        handler.onMergeEnd(completion)
        assert handler.is_idle_for_restart() and not handler.state_dict
        assert json.loads(handler._resume_path.read_text(encoding='utf8'))['groups']=={}
    finally:
        plugin.render_executors.shutdown()
        merger.merge_executors.shutdown()


def test_exit_recovers_inflight_and_preserves_input(monkeypatch,tmp_path):
    config = configuration(tmp_path)
    config['common_event_args']['auto_clean'] = True
    config['clean_args'] = {'src_video_pre':[{'method':'delete','trigger':'onLiveEnd'}]}
    handler = LiveEvents('test',config)
    monkeypatch.setattr(handler,'_collect_session_data',lambda _:None)
    video = VideoInfo(path=str(tmp_path/'source.flv'),group_id='session',file_id='src')
    Path(video.path).write_bytes(b'source')
    handler.session_data['session'] = {'is_live_end':False}
    handler.onLiveSegment(PipeMessage('downloader','test','livesegment',data=video))
    followups = handler.onLiveEnd(PipeMessage('downloader','test','liveend',data='session'))
    assert not any(m.target=='cleaner' for m in followups)
    handler.onExit()
    restored = LiveEvents('test',config)
    messages = restored.onReady()
    assert len([m for m in messages if m.target=='render'])==1
    assert Path(video.path).exists()


def test_failed_checkpoint_does_not_free_stopped_work(monkeypatch,tmp_path):
    config = configuration(tmp_path)
    handler = LiveEvents('test',config)
    video = VideoInfo(path=str(tmp_path/'source.flv'),group_id='session')
    messages = handler.onLiveSegment(PipeMessage('downloader','test','livesegment',data=video))
    dispatch, = [m for m in messages if m.target=='render' and m.event=='newtask']
    handler.ended_dict['session'] = 1
    def fail(*_):
        raise OSError('disk full')
    monkeypatch.setattr('DMR.Task.liveevents.atomic_json_dump',fail)
    handler.onRenderStop(PipeMessage('render','test','stopped',request_id=dispatch.request_id))
    assert 'session' in handler.state_dict
    assert not handler.is_idle_for_restart()


@pytest.mark.parametrize('bold',[False,True])
def test_missing_font_warns_and_falls_back_to_chinese(caplog,bold):
    path = resolve_font('This Font Does Not Exist 734823',bold)
    assert Path(path).is_file()
    assert ('msyhbd' if bold else 'msyh') in Path(path).name.lower()
    assert '回退' in caplog.text
