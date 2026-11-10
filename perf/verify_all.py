"""Fast pre-commit checks for the refactored renderer."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import json
import argparse

import numpy as np


ROOT = pathlib.Path(__file__).resolve().parents[1]


def source_files() -> list[pathlib.Path]:
    return [*ROOT.glob("DMR/**/*.py"), *ROOT.glob("perf/*.py")]


def probe(path: pathlib.Path) -> dict[str, str]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=r_frame_rate,avg_frame_rate,nb_frames,duration,bit_rate", "-of", "json", str(path)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return {key: str(value) for key, value in stream.items()}


def check_video(spec):
    """Check A/V timing and source/output motion over the final five seconds."""
    from DMR.Render.gpu_render import validate_av_duration
    path, source = ROOT / spec['output'], ROOT / spec['source']
    durations = validate_av_duration(path)
    metadata = probe(path)
    numerator, denominator = metadata['avg_frame_rate'].split('/')
    fps = float(numerator) / float(denominator)
    if abs(fps - spec['fps']) > 0.01:
        raise ValueError(f'wrong fps: {path}')
    if 'bitrate' in spec and float(metadata['bit_rate']) > spec['bitrate'] * 1.1:
        raise ValueError(f'bitrate exceeds configuration: {path}')
    def shot(video, seconds):
        data = subprocess.check_output(['ffmpeg','-v','error','-ss',str(seconds),'-i',str(video),
            '-vf','scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2',
            '-frames:v','1','-f','rawvideo','-pix_fmt','gray','pipe:1'])
        if len(data) != 160 * 90:
            raise ValueError(f'empty tail frame: {video}')
        return np.frombuffer(data,np.uint8).astype(float).reshape(90,160)[45:]
    duration=durations['video']
    times=[max(0,duration-offset) for offset in (5,3,1)]
    out=[shot(path,t) for t in times]
    src=[shot(source,t) for t in times]
    output_motion=[float(np.mean(np.abs(a-b))) for a,b in zip(out,out[1:])]
    source_motion=[float(np.mean(np.abs(a-b))) for a,b in zip(src,src[1:])]
    if max(output_motion) < 0.1 and max(source_motion) > 0.5:
        raise ValueError(f'frozen tail: {path}; output={output_motion}, source={source_motion}')
    result = {'output':spec['output'],'durations':durations,'tail_output_mad':output_motion,'tail_source_mad':source_motion}
    if 'plan' in spec:
        report = path.with_name(path.stem+'_flicker.json')
        command = [sys.executable,str(ROOT/'perf/check_flicker.py'),'--source',str(source),'--gpu',str(path),
                   '--plan',str(ROOT/spec['plan']),'--start','0','--fps',str(spec['fps']),
                   '--frames',str(round(durations['video']*spec['fps'])),'--report',str(report)]
        completed = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf8')
        if completed.returncode:
            raise ValueError(f'flicker check failed: {path}: {completed.stdout[-2000:]} {completed.stderr[-500:]}')
        raw = json.loads(report.read_text(encoding='utf8'))
        result['flicker'] = {key:value for key,value in raw.items() if key!='measurements'}
    return result


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=pathlib.Path,default=ROOT/'perf/delivery_checks.json')
    parser.add_argument('--report',type=pathlib.Path,default=ROOT/'perf/tmp/verification.json')
    args=parser.parse_args()
    failures: list[str] = []
    test = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT)
    if test.returncode:
        failures.append("pytest")
    if args.manifest:
        sys.path.insert(0,str(ROOT))
        checks=[]
        for spec in json.loads(args.manifest.read_text(encoding='utf-8')):
            try:
                checks.append(check_video(spec))
            except (ValueError,KeyError,subprocess.CalledProcessError) as error:
                failures.append(str(error))
        report = args.report
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    if failures:
        print("verify_all failed:", *failures, sep="\n- ")
        return 1
    print("verify_all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
