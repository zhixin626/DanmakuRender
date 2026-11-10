"""Durable renderer checks, independent of performance fixtures and Git history."""
import ast
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_render_module_boundary():
    original={'__init__.py','baserender.py','dmrender.py','emoji_dmrender.py','emoji_render_mt.py',
              'emoji_width.py','ffmpeg.py','layout_compiler.py','layout_models.py','layout_track.py',
              'plan_ass.py','resolution.py','transcode.py'}
    assert {p.name for p in (ROOT/'DMR/Render').glob('*.py')} - original == {'sprites.py','gpu_render.py'}


def test_production_has_no_perf_import_or_main_worktree_path():
    forbidden=re.compile(r'[A-Za-z]:[\\/]+DanmakuRender(?:[\\/]|$)',re.I)
    for path in [*ROOT.glob('DMR/**/*.py'),ROOT/'render_only.py']:
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                assert all(item.name.split('.')[0]!='perf' for item in node.names),path
            elif isinstance(node,ast.ImportFrom):
                assert (node.module or '').split('.')[0]!='perf',path
            elif isinstance(node,ast.Constant) and isinstance(node.value,str):
                # Exclude descriptive docstrings, which can discuss old paths.
                if '\n' not in node.value:
                    assert not forbidden.search(node.value),(path,node.value)


def test_no_stress_kernel_or_obsolete_gpu_settings():
    source=(ROOT/'DMR/Render/gpu_render.py').read_text(encoding='utf-8')
    names={node.id for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Name)}
    assert not names & {'PRESSURE_KERNEL','pressure_sprites','frame_signature'}
    assert 'gpu_rc_args' not in source and 'reference_fps' not in source
    assert 'gpu_rc_args' not in (ROOT/'DMR/Config/default.yml').read_text(encoding='utf-8')


def test_single_encoder_loop():
    tree=ast.parse((ROOT/'DMR/Render/gpu_render.py').read_text(encoding='utf-8'))
    calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
           and node.func.attr=='Encode']
    assert len(calls)==1


def test_emoticon_cache_uses_shared_store():
    for name in ('emoji_dmrender.py','gpu_render.py'):
        source=(ROOT/'DMR/Render'/name).read_text(encoding='utf-8')
        assert 'detect_platform' in source and 'emoticon' in source
    assert 'def _detect_platform' not in (ROOT/'DMR/Render/emoji_dmrender.py').read_text(encoding='utf-8')


def test_engine_names_are_only_defined_in_the_registry_or_engine_module():
    from DMR.Render import ENGINES
    # 'transcode' is also a configuration-section/operation name, independent
    # of backend selection. Overlay backend IDs have no such dual meaning.
    overlay_names = {name for name,spec in ENGINES.items() if spec.overlay}
    for path in [*ROOT.glob('DMR/**/*.py'),ROOT/'render_only.py']:
        if path == ROOT/'DMR/Render/__init__.py' or path.stem in ENGINES:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf8'))):
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                assert node.value not in overlay_names,(path,node.value)
