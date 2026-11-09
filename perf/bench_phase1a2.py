
import json, pathlib, statistics, subprocess, time, psutil
ROOT=pathlib.Path(__file__).resolve().parents[1]
PY=ROOT/"perf"/"tmp"/"venv312"/"Scripts"/"python.exe"
GPU=ROOT/"DMR"/"Render"/"gpu_render.py"
VIDEO=ROOT/"perf"/"samples"/"normal_dense"/"normal_dense_peak_7月30日20点23分"/"7月30日20点23分_clip.flv"
OUTDIR=ROOT/"perf"/"outputs"/"phase1a"/"normal_dense"/"normal_dense_peak_7月30日20点23分"
TMP=ROOT/"perf"/"tmp"/"phase1a2"

def tree(pid):
    try: root=psutil.Process(pid)
    except: return []
    found={root.pid:root}; q=[root.pid]; snap=list(psutil.process_iter(["pid","ppid"])); ch={}
    for p in snap: ch.setdefault(p.info.get("ppid"),[]).append(p)
    while q:
        p=q.pop()
        for x in ch.get(p,[]):
            if x.pid not in found: found[x.pid]=x; q.append(x.pid)
    return list(found.values())

def gpu():
    try:
        a=[float(x.strip()) for x in subprocess.check_output(["nvidia-smi","--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder","--format=csv,noheader,nounits"],text=True,stderr=subprocess.DEVNULL).strip().split(",")]
        return a
    except: return [0,0,0]

def bg():
    psutil.cpu_percent(None); vals=[]; end=time.monotonic()+10
    while time.monotonic()<end: time.sleep(.5); vals.append(psutil.cpu_percent(None))
    return statistics.mean(vals) if vals else 0

def run(sprites):
    out=OUTDIR/f"G_gpu_proto_{sprites}.mp4"; log=TMP/f"full_{sprites}.log"; log.parent.mkdir(parents=True,exist_ok=True)
    b=bg(); cmd=[str(PY),str(GPU),"--video",str(VIDEO),"--sprites",str(sprites),"--max-frames","7500","--full","--output",str(out)]
    st=time.perf_counter(); p=subprocess.Popen(cmd,stdout=log.open("w",encoding="utf-8"),stderr=subprocess.STDOUT)
    state={};
    for x in tree(p.pid):
        try: x.cpu_percent(None); state[x.pid]=x
        except: pass
    cpus=[]; gpus=[]
    while p.poll() is None:
        time.sleep(.5); total=0
        for x in tree(p.pid):
            try:
                old=state.get(x.pid)
                if old is None: x.cpu_percent(None)
                else: total+=old.cpu_percent(None)
                state[x.pid]=x
            except: pass
        cpus.append(total); gpus.append(gpu())
    p.wait(); wall=time.perf_counter()-st; frames=0
    try:
        d=json.loads(subprocess.check_output(["ffprobe","-v","error","-count_frames","-select_streams","v:0","-show_entries","stream=nb_read_frames","-of","json",str(out)],text=True)); frames=int((d.get("streams") or [{}])[0].get("nb_read_frames") or 0)
    except: pass
    return {"sprites":sprites,"output":str(out),"frames":frames,"wall_s":wall,"fps":frames/wall if wall else 0,"bg_cpu":b,"cpu_avg":statistics.mean(cpus) if cpus else 0,"cpu_peak":max(cpus) if cpus else 0,"avg_cores":statistics.mean(cpus)/100 if cpus else 0,"peak_cores":max(cpus)/100 if cpus else 0,"gpu_avg":statistics.mean(x[0] for x in gpus) if gpus else 0,"gpu_peak":max(x[0] for x in gpus) if gpus else 0,"encoder_avg":statistics.mean(x[1] for x in gpus) if gpus else 0,"decoder_avg":statistics.mean(x[2] for x in gpus) if gpus else 0,"returncode":p.returncode}

runs=[run(91),run(200)]
(ROOT/"perf"/"tmp"/"phase1a2_raw.json").write_text(json.dumps(runs,ensure_ascii=False,indent=2),encoding="utf-8")
rp=ROOT/"perf"/"results"/"phase1a.md"; old=rp.read_text(encoding="utf-8")
lines=["","## 1a-2 完整链路：GPU NVENC + MP4 封装","",
       "已按 PLAN 顺序尝试：直接 decoded、cuda() 包装类。包装类使用正确的 (H,W,1)/(H/2,W/2,2) 和实际 pitch，NVENC 成功接收 GPU 输入；HEVC 码流随后由 ffmpeg 与源音频封装为 MP4。","",
       "| 精灵 | 帧数 | 墙钟 s | fps | 平均/峰值核数 | GPU 平均/峰值% | NVENC/解码器平均% | 返回码 | 输出 |",
       "|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
for r in runs:
    lines.append(f"| {r['sprites']} | {r['frames']} | {r['wall_s']:.2f} | {r['fps']:.1f} | {r['avg_cores']:.2f}/{r['peak_cores']:.2f} | {r['gpu_avg']:.1f}/{r['gpu_peak']:.1f} | {r['encoder_avg']:.1f}/{r['decoder_avg']:.1f} | {r['returncode']} | {r['output']} |")
lines += ["","输出视频：","- perf/outputs/phase1a/normal_dense/normal_dense_peak_7月30日20点23分/G_gpu_proto_91.mp4","- perf/outputs/phase1a/normal_dense/normal_dense_peak_7月30日20点23分/G_gpu_proto_200.mp4","","1a-2 已完成，按计划停止。"]
rp.write_text(old+"\\n".join(lines)+"\\n",encoding="utf-8")
print(json.dumps(runs,ensure_ascii=False,indent=2))

