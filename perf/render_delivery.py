"""Render the integration acceptance fixtures through the DMR engine API."""
from __future__ import annotations

import json
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from run_gpu_render import configure_cuda_dlls
configure_cuda_dlls()
from DMR.Render.gpu_render import GpuRender


def main():
    out=ROOT/'演示视频/2026-11-10_04_结构整合与修复'
    out.mkdir(parents=True,exist_ok=True)
    cases=[
        ('02_G_完整渲染_检查结尾', ROOT/'perf/samples/full_coverage/原始/峰哥_8月18日14点19分_1860-2160.flv',
         ROOT/'perf/tmp/full_coverage_sync/full_coverage_sync/render_plan.json',(1080,1920),None),
        ('05_G_面板药丸_抗锯齿', ROOT/'perf/samples/full_coverage/原始/峰哥_8月18日14点19分_1860-2160.flv',
         ROOT/'perf/tmp/full_coverage_sync/full_coverage_sync/render_plan.json',(1080,1920),1500),
        ('01_G_problem_resolution',ROOT/'perf/samples/problem_resolution/problem_resolution_7月30日22点50分/7月30日22点50分_clip.flv',
         ROOT/'perf/samples/problem_resolution/problem_resolution_7月30日22点50分/render_plan.json',(1920,1080),None),
    ]
    engine=GpuRender(vencoder='hevc_nvenc',vencoder_args=['-b:v','4M','-maxrate','6M','-bufsize','8M','-preset','P4'],
                     aencoder='aac',aencoder_args=['-b:a','128K'],rescale_to_1080p=True,emoji_pack=str(ROOT/'emoji_pack/bilibili'))
    results=[]
    for name,source,plan,canvas,limit in cases:
        # Old local fixture plans contain paths from before worktree isolation.
        data=plan.read_text(encoding='utf-8').replace('D:\\\\DanmakuRender\\\\emoji_pack', str(ROOT/'emoji_pack').replace('\\','\\\\'))
        isolated=ROOT/'perf/tmp/integration'/f'{name}.json'
        isolated.parent.mkdir(parents=True,exist_ok=True)
        plan_data=json.loads(data)
        # These delivery fixtures adopt the new default explicitly; frozen
        # plans from earlier rounds otherwise keep their already compiled 40px.
        for event in plan_data['events']:
            for node in event['visual'].get('nodes',[]):
                if node['type']=='rect':
                    node['radius']=20
        isolated.write_text(json.dumps(plan_data,ensure_ascii=False),encoding='utf-8')
        video=SimpleNamespace(path=str(source),resolution=canvas,dm_file_id=str(isolated))
        ok,result=engine.render_one(video,str(out/f'{name}.mp4'),render_plan=str(isolated),canvas_resolution=canvas,max_frames=limit,fps=25)
        if not ok:
            raise RuntimeError(f'{name}: {result}')
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        (ROOT/'perf/tmp/integration/render_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
