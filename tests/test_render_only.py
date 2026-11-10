from types import SimpleNamespace

import render_only as cli


def answers(monkeypatch, values):
    sequence = iter(values)
    monkeypatch.setattr('builtins.input',lambda _:next(sequence))


def test_stepwise_single_file_selects_disabled_task_and_defaults(monkeypatch,tmp_path):
    video = tmp_path/'clip.flv'
    video.touch()
    danmaku = tmp_path/'clip_augmented.jsonl'
    danmaku.touch()
    options = {'engine':'gpu_render'}
    config = SimpleNamespace(task_config_files=lambda:[str(tmp_path/'DMR-demo.yml')],
                             get_render_args=lambda task,mode:options)
    answers(monkeypatch,[f'"{video}"','','1','',''])
    tasks, actual, source = cli.interactive_tasks(config,SimpleNamespace(input_dir=None,output_dir=None),'danmaku_render')
    assert tasks == [{'video':str(video),'danmaku':str(danmaku),'output':str(tmp_path/'clip（弹幕版）.mp4')}]
    assert actual is options and 'demo' in source


def test_global_requires_explicit_choice_and_bad_number_reprompts(monkeypatch,capsys):
    options = {'engine':'gpu_render'}
    config = SimpleNamespace(task_config_files=lambda:[],global_config={'render_args':{'overlay':options}})
    answers(monkeypatch,['9',''])
    actual, source = cli.choose_render_args(config,'overlay')
    assert actual is options and '用户选择' in source
    assert '无效' in capsys.readouterr().out


def test_batch_missing_sidecar_can_be_manually_assigned(tmp_path):
    video = tmp_path/'clip.flv'
    video.touch()
    dm = tmp_path/'different.jsonl'
    dm.write_text('{"segment_start_time":0}\n',encoding='utf8')
    tasks, ignores = cli.build_tasks([str(video)],'danmaku_render',{},None,missing_danmaku=lambda _:str(dm))
    assert not ignores and tasks[0]['danmaku'] == str(dm)


def test_missing_sidecar_prompt_accepts_manual_path(monkeypatch,tmp_path):
    dm = tmp_path/'manual.jsonl'
    dm.touch()
    answers(monkeypatch,['wrong-path',str(dm)])
    assert cli.ask_danmaku(str(tmp_path/'clip.flv'),True) == str(dm)


def test_progress_clears_whole_line_and_formats_percent(capsys):
    cli.show_progress('video',99.05490886)
    cli.show_progress('video',100)
    output = capsys.readouterr().out
    assert output.count('\r\x1b[2K') == 2
    assert output.endswith('100.0%') and '05490886' not in output
