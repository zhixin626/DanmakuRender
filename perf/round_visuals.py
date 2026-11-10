"""Extract native frames and reproducible, labelled three-times comparisons."""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from perf.round_regression import ROOT, VIDEO
from DMR.Render.layout_compiler import _position_at
from DMR.utils import FFprobe

SHOTS = ROOT/'perf/results/round_shots'


def frame(path,seconds):
    data = subprocess.check_output(['ffmpeg','-v','error','-ss',str(seconds),'-i',str(path),
        '-frames:v','1','-f','image2pipe','-vcodec','png','pipe:1'])
    return Image.open(io.BytesIO(data)).convert('RGB')


def compare(images,path,labels=('旧版 E ×3','GPU G ×3')):
    images = [image.resize((image.width*3,image.height*3),Image.Resampling.NEAREST) for image in images]
    canvas = Image.new('RGB',(sum(image.width for image in images),max(image.height for image in images)+44),'#202020')
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',22)
    x = 0
    for image,label in zip(images,labels):
        canvas.paste(image,(x,44))
        draw.text((x+8,8),label,font=font,fill='white')
        x += image.width
    path.parent.mkdir(parents=True,exist_ok=True)
    canvas.save(path)


def excerpt(left,right,output,windows,fps,bitrate):
    """Keep E-side audio while comparing matching time windows side by side."""
    filters = []
    segments = []
    for index,(start,end) in enumerate(windows):
        for side in (0,1):
            filters.append(f'[{side}:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{side}_{index}]')
        filters.append(f'[v0_{index}][v1_{index}]hstack=inputs=2[v{index}]')
        filters.append(f'[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]')
        segments.append(f'[v{index}][a{index}]')
    filters.append(''.join(segments)+f'concat=n={len(windows)}:v=1:a=1[v][a]')
    font = r"C\:/Windows/Fonts/msyh.ttc"
    filters.append(f"[v]drawtext=fontfile='{font}':text='旧版 E':x=24:y=30:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.6,"
                   f"drawtext=fontfile='{font}':text='GPU G':x=w/2+24:y=30:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.6[out]")
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(left),'-i',str(right),'-filter_complex',';'.join(filters),
        '-map','[out]','-map','[a]','-c:v','hevc_nvenc','-b:v',str(bitrate),'-r',str(fps),
        '-c:a','aac','-movflags','+faststart',str(output)],check=True)


def media_shots(source,outputs,tag):
    SHOTS.mkdir(parents=True,exist_ok=True)
    result = {}
    for name,path in outputs.items():
        duration = FFprobe.get_timeline_duration(str(path))
        images = []
        for ratio in (.1,.5,.9):
            image = frame(path,duration*ratio)
            image.save(SHOTS/f'{tag}_{name}_{int(ratio*100)}.png')
            images.append(np.asarray(image,dtype=np.float32))
        motion = [float(np.abs(a-b).mean()) for a,b in zip(images,images[1:])]
        matches = []
        for seconds in (1,2):
            original = frame(source,seconds)
            output = frame(path,seconds)
            # The panel-free bottom centre stays within source content in both
            # portrait coverage and the fitted landscape resolution test.
            box = (output.width*2//5,output.height*4//5,output.width*3//5,output.height*9//10)
            if original.size!=output.size:
                from DMR.Render.resolution import fit_canvas
                w,h,x,y = fit_canvas(original.size,output.size)
                fitted = Image.new('RGB',output.size)
                fitted.paste(original.resize((w,h),Image.Resampling.BILINEAR),(x,y))
                original = fitted
            matches.append(float(np.abs(np.asarray(original.crop(box),dtype=float)-np.asarray(output.crop(box),dtype=float)).mean()))
        result[name] = {'duration':duration,'dimensions':list(images[0].shape[1::-1]),
                        'motion_mad':motion,'source_1_2_s_mad':matches}
    return result


def coverage(delivery):
    plans = [json.loads((delivery/f'{name}_plan.json').read_text(encoding='utf8'))
             for name in ('02_增强素材_旧版E','03_增强素材_GPU_G')]
    outputs = {'E':delivery/'02_增强素材_旧版E.mp4','G':delivery/'03_增强素材_GPU_G.mp4'}
    report = media_shots(VIDEO,outputs,'07')
    long_sc = [event for event in plans[1]['events'] if event['visual']['type']=='panel'
               and 'Pillow' in event['visual']['nodes'][-1]['text']]
    report['long_sc'] = [{'id':event['id'],'lines':len(event['visual']['nodes'][-1]['text'].splitlines()),
                          'bounds':event['bounds']} for event in long_sc]
    for label,event,seconds in [('长SC',long_sc[0],20),('SC表情',long_sc[2],100)]:
        crops = []
        for plan,path in zip(plans,outputs.values()):
            e = next(e for e in plan['events'] if e['id']==event['id'])
            x,y = _position_at(e['path'],seconds)
            crop = (round(x),round(y),round(x)+e['bounds']['width'],round(y)+e['bounds']['height'])
            crops.append(frame(path,seconds).crop(crop))
        compare(crops,SHOTS/f'07_{label}_EG_3x.png')
    best = None
    for ticks in range(810,901,2):
        seconds = ticks/10
        live = [e for e in plans[1]['events'] if e['path'][0][0]+.5<seconds<e['path'][-1][0]-.5]
        for text in (e for e in live if e['track']=='r2l'):
            tx,ty = _position_at(text['path'],seconds)
            for panel in (e for e in live if e['track']=='slide_right'):
                px,py = _position_at(panel['path'],seconds)
                left,top = max(0,tx,px),max(0,ty,py)
                right,bottom = min(1080,tx+text['bounds']['width'],px+panel['bounds']['width']),min(1920,ty+text['bounds']['height'],py+panel['bounds']['height'])
                area = max(0,right-left)*max(0,bottom-top)
                if best is None or area>best[0]:
                    best = (area,seconds,(int(left)-10,int(top)-10,int(right)+10,int(bottom)+10),text['id'],panel['id'])
    assert best[0]>0, 'supplement did not overlap'
    compare([frame(path,best[1]).crop(best[2]) for path in outputs.values()],SHOTS/'07_层级重叠_EG_3x.png')
    report['overlap'] = {'area':best[0],'seconds':best[1],'crop':best[2],'text':best[3],'panel':best[4]}
    (delivery/'截图验收.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return report
