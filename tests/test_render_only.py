from pathlib import Path
from types import SimpleNamespace

import pytest
import render_only as cli
from DMR.utils import VideoInfo


def answers(monkeypatch, values):
    sequence = iter(values)
    monkeypatch.setattr('builtins.input',lambda _:next(sequence))


def config_fixture(tmp_path):
    task=tmp_path/'DMR-demo.yml'
    task.write_text(f'enable: false\ndownload_args:\n  output_dir: {tmp_path.as_posix()}\n',encoding='utf8')
    options={'engine':'gpu_render','format':'mp4'}
    return SimpleNamespace(task_config_files=lambda:[str(task)],get_render_args=lambda *_:options,
                           global_config={'render_args':{'overlay':options,'transcode':options},'download_args':{}})


def args():
    return SimpleNamespace(input_dir=None,output_dir=None,emoji=False,no_emoji=False)


def test_single_file_shows_all_candidates_matches_task_and_confirms(monkeypatch,tmp_path,capsys):
    video=tmp_path/'clip.flv'; video.touch()
    (tmp_path/'clip.jsonl').touch()
    augmented=tmp_path/'clip_augmented.jsonl'; augmented.touch()
    answers(monkeypatch,[str(video),'','','','',''])
    tasks,options,source=cli.interactive_tasks(config_fixture(tmp_path),args(),'danmaku_render')
    assert tasks==[{'video':str(video),'danmaku':str(augmented),
                   'output':str(tmp_path/'clip（弹幕版）.mp4'),'overwrite':False}]
    out=capsys.readouterr().out
    assert out.index('clip_augmented.jsonl')<out.index('clip.jsonl')
    assert '任务「demo」' in source and 'GPU' in out and '确认' in out


def test_source_listing_excludes_rendered_and_mp4_sibling(tmp_path):
    for name in ('a.flv','a.mp4','b.ts','c.mp4','a（弹幕版）.mp4','b（转码后）.mp4','d.mkv'):
        (tmp_path/name).touch()
    assert [Path(p).name for p in cli.source_videos(str(tmp_path))]==['a.flv','b.ts','c.mp4']


def test_folder_selects_before_task_and_allows_missing_sidecar(monkeypatch,tmp_path,capsys):
    for name in ('a.flv','b.flv','a_augmented.jsonl','manual.jsonl'):(tmp_path/name).touch()
    answers(monkeypatch,[str(tmp_path),'1 2','',str(tmp_path/'manual.jsonl'),'','',''])
    tasks,_,_=cli.interactive_tasks(config_fixture(tmp_path),args(),'danmaku_render')
    assert len(tasks)==2 and tasks[1]['danmaku']==str(tmp_path/'manual.jsonl')
    out=capsys.readouterr().out
    assert out.index('该文件夹中的源视频')<out.index('找到以下弹幕文件')<out.index('任务配置')<out.index('确认')


def test_folder_default_only_selects_matching_videos(monkeypatch,tmp_path):
    for name in ('a.flv','b.flv','a.jsonl'):(tmp_path/name).touch()
    answers(monkeypatch,['99',''])
    assert cli.select_videos(str(tmp_path))==[str(tmp_path/'a.flv')]


def test_danmaku_multiple_candidates_and_invalid_input(monkeypatch,tmp_path):
    for name in ('a.jsonl','a_source.jsonl','a_augmented.jsonl','a.ass'):(tmp_path/name).touch()
    assert [Path(p).name for p in cli.danmaku_candidates(str(tmp_path/'a.flv'))]==['a_augmented.jsonl','a.jsonl','a_source.jsonl','a.ass']
    answers(monkeypatch,['99','2'])
    assert cli.ask_danmaku(str(tmp_path/'a.flv'))==str(tmp_path/'a.jsonl')


def test_missing_sidecar_manual_or_skip(monkeypatch,tmp_path):
    dm=tmp_path/'manual.jsonl'; dm.touch()
    answers(monkeypatch,['wrong',str(dm),''])
    assert cli.ask_danmaku(str(tmp_path/'video.flv'),True)==str(dm)
    assert cli.ask_danmaku(str(tmp_path/'video.flv'),True) is None


@pytest.mark.parametrize('choice,result',[('1','overwrite'),('3','skip')])
def test_output_existing_choices(monkeypatch,tmp_path,choice,result):
    (tmp_path/'out.mp4').write_bytes(b'existing')
    answers(monkeypatch,[choice])
    output,overwrite=cli.ask_output('source.flv',str(tmp_path),'out.mp4',False,set())
    assert (output,overwrite)==((str(tmp_path/'out.mp4'),True) if result=='overwrite' else (None,False))
    assert (tmp_path/'out.mp4').read_bytes()==b'existing'


def test_output_invalid_choice_then_rename_and_protect_source(monkeypatch,tmp_path):
    source=tmp_path/'out.mp4'; source.touch()
    answers(monkeypatch,['bad','2','renamed.mp4'])
    assert cli.ask_output('source.flv',str(tmp_path),'out.mp4',False,set())==(str(tmp_path/'renamed.mp4'),False)
    answers(monkeypatch,['safe.mp4'])
    assert cli.ask_output(str(source),str(tmp_path),'out.mp4',False,{cli.os.path.normcase(str(source.resolve()))})[0]==str(tmp_path/'safe.mp4')


def test_task_default_and_invalid_choice(monkeypatch,tmp_path):
    answers(monkeypatch,['bad',''])
    assert 'demo' in cli.ask_task(config_fixture(tmp_path),str(tmp_path),'overlay')[1]
    assert cli.default_task([('parent',str(tmp_path)),('child',str(tmp_path/'nested'))],str(tmp_path/'nested'/'day'))==2


def test_video_and_confirmation_reprompt(monkeypatch,tmp_path):
    video=tmp_path/'a.ts'; video.touch()
    answers(monkeypatch,['missing',f'"{video}"','bad','n'])
    assert cli.ask_video()==str(video)
    assert not cli.confirm([{'video':str(video),'danmaku':None,'output':'out.mp4'}],'global','test')


@pytest.mark.parametrize('success',[True,False])
def test_overwrite_is_atomic_and_failure_preserves_existing(monkeypatch,tmp_path,success):
    import sys
    video=tmp_path/'a.mp4'; video.write_bytes(b'source')
    dm=tmp_path/'a.ass'; dm.touch()
    output=tmp_path/'rendered.mp4'; output.write_bytes(b'previous')
    cfg=config_fixture(tmp_path)
    monkeypatch.setattr(cli,'Config',lambda _:cfg)
    monkeypatch.setattr(cli,'interactive_tasks',lambda *_:([{'video':str(video),'danmaku':str(dm),'output':str(output),'overwrite':True}],cfg.global_config['render_args']['overlay'],'demo'))
    monkeypatch.setattr(cli.FFprobe,'get_resolution',lambda _:(1920,1080))
    monkeypatch.setattr(cli.FFprobe,'get_timeline_duration',lambda _:1)
    class Renderer:
        def __init__(self,**_):pass
        def render_one(self,video,output,**_):
            Path(output).write_bytes(b'new')
            return (True,VideoInfo(path=output)) if success else (False,RuntimeError('failure'))
    monkeypatch.setattr(cli,'get_engine',lambda _:SimpleNamespace(raster=True,label='test',load=lambda:Renderer))
    monkeypatch.setattr(sys,'argv',['render_only.py'])
    assert cli.main()==(0 if success else 1)
    assert output.read_bytes()==(b'new' if success else b'previous')
    assert not list(tmp_path.glob('.render_only_*'))


def test_progress_clears_whole_line(capsys):
    cli.show_progress('video',99.05490886); cli.show_progress('video',100)
    out=capsys.readouterr().out
    assert out.count('\r\x1b[2K')==2 and out.endswith('100.0%') and '05490886' not in out


def test_argument_mode_never_prompts(monkeypatch,tmp_path):
    import sys
    video=tmp_path/'source.mp4'; video.touch()
    dm=tmp_path/'source.ass'; dm.touch()
    output=tmp_path/'new.mp4'
    monkeypatch.setattr(cli,'Config',lambda _:config_fixture(tmp_path))
    def unexpected(_):
        raise AssertionError('parameter mode must not ask for input')
    monkeypatch.setattr('builtins.input',unexpected)
    monkeypatch.setattr(cli.FFprobe,'get_resolution',lambda _:(1920,1080))
    monkeypatch.setattr(cli.FFprobe,'get_timeline_duration',lambda _:1)
    class Renderer:
        def __init__(self,**_):pass
        def render_one(self,video,output,**_):
            Path(output).write_bytes(b'encoded')
            return True,VideoInfo(path=output)
    monkeypatch.setattr(cli,'get_engine',lambda _:SimpleNamespace(raster=True,label='test',load=lambda:Renderer))
    monkeypatch.setattr(sys,'argv',['render_only.py','--task','demo','--video',str(video),'--danmaku',str(dm),'--output',str(output)])
    assert cli.main()==0 and output.read_bytes()==b'encoded'


def test_rename_enter_uses_nonconflicting_suggestion(monkeypatch,tmp_path,capsys):
    for filename in ('out.mp4','out_2.mp4'):
        (tmp_path/filename).touch()
    answers(monkeypatch,['2',''])
    output,overwrite = cli.ask_output('source.flv',str(tmp_path),'out.mp4',False,set())
    assert output==str(tmp_path/'out_3.mp4') and not overwrite
