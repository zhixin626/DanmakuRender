"""Prepare reproducible plans and run this round's local rendering checks."""
from __future__ import annotations

import json
import sys
import time
import threading
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from DMR.Config import Config
from DMR.Render.layout_compiler import LayoutCompiler
from DMR.utils import FFprobe, VideoInfo
from DMR.Render import get_engine
from perf.dmr_gate import dmr_activity, decide

MATERIAL = ROOT / '演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段'
VIDEO = MATERIAL / '7月30日20点23分_中段.flv'
EVENTS = MATERIAL / '7月30日20点23分_中段_augmented.jsonl'
WORK = ROOT / 'perf/tmp/round'
DELIVERY = ROOT / '演示视频/2026-11-11_06_闪烁与素材覆盖'


def prepare(task='月亮3'):
    WORK.mkdir(parents=True, exist_ok=True)
    config = Config('configs/global.yml')
    options = config.get_render_args(task)
    video = VideoInfo(path=str(VIDEO), dm_file_id=str(EVENTS),
                      duration=FFprobe.get_timeline_duration(str(VIDEO)),
                      resolution=FFprobe.get_resolution(str(VIDEO)))
    result = LayoutCompiler(options, engine='gpu_render', engine_config=options).compile(
        video=video, events_path=str(EVENTS), work_dir=str(WORK))
    plan = WORK / 'render_plan.json'
    plan.write_bytes(Path(result.render_plan_path).read_bytes())
    (WORK / 'options.json').write_text(json.dumps(options, ensure_ascii=False), encoding='utf-8')
    result.cleanup()
    return video, options, plan


def check_layer_pixels():
    """Run the real CUDA compositor with opaque, fully overlapping sprites."""
    from DMR.Render.gpu_render import load_cuda_modules, NV12Buffer, SpriteCompositor
    from DMR.Render.layout_models import RenderEvent
    cupy,_ = load_cuda_modules()
    colors = [210,80,120]
    events = [RenderEvent(name,index*16,4,4,0,1,((0,2,2),(1,2,2)),{},track)
              for index,(name,track) in enumerate((('text','r2l'),('gift','slide_right'),('sc','slide_left')))]
    assets = {'events':events,'y':cupy.asarray([color for color in colors for _ in range(16)],dtype=cupy.uint8)}
    assets.update({name:cupy.full(48,value,dtype=cupy.uint8) for name,value in (('u',128),('v',128),('a',255))})
    canvas = NV12Buffer(cupy,8,8)
    canvas.y.fill(16)
    canvas.uv.fill(128)
    SpriteCompositor(cupy,8,8,assets).apply(canvas,.5)
    pixels = cupy.asnumpy(canvas.y[2:6,2:6,0])
    assert (pixels==210).all(), pixels
    return {'pixels':pixels.tolist(),'expected_text_y':210,'passed':True}


def render_coverage(only=None, delivery=DELIVERY, video_path=VIDEO, events_path=EVENTS,
                    rescale=False, names=None, config_name='覆盖配置'):
    """Render the same isolated source-task settings through both public engines."""
    gate = decide(15)
    if not gate['go']:
        print(json.dumps(gate,ensure_ascii=False))
        return 10
    config = Config(str(MATERIAL/'配置/global.yml'))
    config.config_dir = str(MATERIAL/'配置')
    options = config.get_render_args('峰哥')
    if rescale:
        options.update(rescale_to_1080p=True,rescale_orientation='auto')
    # Validation-only overrides; production/personal YAML is never written.
    options['slide_right']['gift']['min_price_cny'] = 0
    options['slide_left']['superchat']['use_event_background_colors'] = False
    for side in ('slide_left','slide_right'):
        options[side]['corner_radius'] = '20px'
    delivery.mkdir(exist_ok=True)
    WORK.mkdir(parents=True,exist_ok=True)
    (delivery/f'{config_name}.json').write_text(json.dumps(options,ensure_ascii=False,indent=2),encoding='utf8')
    summary_file = delivery/'覆盖渲染.json'
    result_summary = json.loads(summary_file.read_text(encoding='utf8')) if summary_file.exists() else {}
    for name,engine_name in names or [('02_增强素材_旧版E','emoji_dmrender'),('03_增强素材_GPU_G','gpu_render')]:
        if only and engine_name!=only:
            continue
        if dmr_activity()['rendering']:
            raise RuntimeError('DMR started rendering; discard this test')
        video = VideoInfo(path=str(video_path),dm_file_id=str(events_path),duration=FFprobe.get_timeline_duration(str(video_path)),
                          resolution=FFprobe.get_resolution(str(video_path)),group_id='coverage',segment_id=1,file_id='source')
        compiled = LayoutCompiler(options,engine=engine_name,engine_config=options).compile(
            video=video,events_path=str(events_path),work_dir=str(WORK))
        plan = delivery/f'{name}_plan.json'
        plan.write_bytes(Path(compiled.render_plan_path).read_bytes())
        renderer = get_engine(engine_name).load()(**options)
        finished = threading.Event()
        interfered = threading.Event()
        def monitor():
            while not finished.wait(3):
                if dmr_activity()['rendering']:
                    interfered.set()
                    renderer.stop()
                    return
        watcher = threading.Thread(target=monitor,daemon=True)
        watcher.start()
        started = time.perf_counter()
        try:
            ok,info = renderer.render_one(video,str(delivery/f'{name}.mp4'),
                render_plan=str(plan),canvas_resolution=compiled.next_state.canvas_resolution,
                progress_cb=lambda value:print(f'{name}: {value:.1f}%',flush=True))
            if not ok or interfered.is_set():
                raise RuntimeError(f'{name}: {info}; interference={interfered.is_set()}')
            result_summary[name] = {'wall_s':time.perf_counter()-started,'output':dict(info)}
            print(name,result_summary[name],flush=True)
        finally:
            finished.set()
            watcher.join()
            compiled.cleanup()
    summary_file.write_text(json.dumps(result_summary,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    return 0


if __name__ == '__main__':
    if '--render-coverage' in sys.argv:
        logging.basicConfig(level=logging.INFO)
        selected = sys.argv[sys.argv.index('--engine')+1] if '--engine' in sys.argv else None
        delivery = Path(sys.argv[sys.argv.index('--delivery')+1]) if '--delivery' in sys.argv else DELIVERY
        raise SystemExit(render_coverage(selected,delivery))
    prepare()
