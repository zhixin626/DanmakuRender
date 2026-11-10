from types import SimpleNamespace
from unittest.mock import Mock

from perf import dmr_gate as gate


def test_worktree_main_is_detected_once_even_with_venv_launcher(monkeypatch,tmp_path):
    (tmp_path/'DMR').mkdir()
    def proc(pid,ppid):
        return SimpleNamespace(pid=pid,info={'name':'python.exe','cmdline':['python','main.py']},
                               cwd=lambda:str(tmp_path),ppid=lambda:ppid)
    monkeypatch.setattr(gate.psutil,'process_iter',lambda _:iter([proc(10,1),proc(11,10)]))
    assert [p.pid for p in gate.find_dmr_main()] == [10]


def test_in_process_gpu_render_blocks_gate_without_children(monkeypatch):
    main = SimpleNamespace(pid=10,children=lambda **_:[])
    monkeypatch.setattr(gate,'find_dmr_main',lambda:[main])
    monkeypatch.setattr(gate,'web_activity',lambda _:dict(render_queue={'active':[{'mode':'gpu_render'}]},
        merge_queue={'active':[]},recording_tasks=[]))
    monkeypatch.setattr(gate,'remember_wait_start',Mock())
    monkeypatch.setattr(gate,'waited_hours',lambda:0)
    result = gate.decide(1)
    assert not result['go'] and result['rendering'] == 1


def test_unreadable_instance_is_not_treated_as_idle(monkeypatch):
    main = SimpleNamespace(pid=10,children=lambda **_:[])
    monkeypatch.setattr(gate,'find_dmr_main',lambda:[main])
    def unavailable(_):
        raise OSError('offline API')
    monkeypatch.setattr(gate,'web_activity',unavailable)
    monkeypatch.setattr(gate,'remember_wait_start',Mock())
    monkeypatch.setattr(gate,'waited_hours',lambda:0)
    result = gate.decide(1)
    assert not result['go'] and result['unknown_instances']==[10]
