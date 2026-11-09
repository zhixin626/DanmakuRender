
from __future__ import annotations
import json, pathlib, statistics, subprocess, time
import psutil

ROOT=pathlib.Path(__file__).resolve().parents[1]
PYTHON=ROOT/"perf"/"tmp"/"venv312"/"Scripts"/"python.exe"
GPU_SCRIPT=ROOT/"DMR"/"Render"/"gpu_render.py"
VIDEO=ROOT/"perf"/"samples"/"normal_dense"/"normal_dense_peak_7月30日20点23分"/"7月30日20点23分_clip.flv"
TMP=ROOT/"perf"/"tmp"/"phase1a"

def tree(root):
    try: p=psutil.Process(root)
    except: return []
    found={p.pid:p}; q=[p.pid]; snap=list(psutil.process_iter(["pid","ppid"])); children={}
    for x in snap: children.setdefault(x.info.get("ppid"),[]).append(x)
    while q:
        pid=q.pop()
        for x in children.get(pid,[]):
            if x.pid not in found: found[x.pid]=x; q.append(x.pid)
    return list(found.values())

def sample_gpu():
    try:
        out=subprocess.check_output(["nvidia-smi","--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder","--format=csv,noheader,nounits"],text=True,stderr=subprocess.DEVNULL).strip()
        a=[float(x.strip()) for x in out.split(",")]; return {"gpu":a[0],"encoder":a[1],"decoder":a[2]}
    except Exception: return {"gpu":0.0,"encoder":0.0,"decoder":0.0}

def background(sec=10):
    vals=[]; end=time.monotonic()+sec; psutil.cpu_percent(None)
    while time.monotonic()<end:
        time.sleep(.5); vals.append(psutil.cpu_percent(None))
    return statistics.mean(vals) if vals else 0.0

def run(sprites, probe=False):
    bg=background(10); out=TMP/f"run_{sprites}.json"; log=TMP/f"run_{sprites}.log"; out.parent.mkdir(parents=True,exist_ok=True)
    cmd=[str(PYTHON),str(GPU_SCRIPT),"--video",str(VIDEO),"--sprites",str(sprites),"--max-frames","7500","--output",str(out)]
    if probe: cmd.append("--probe-encoder")
    start=time.perf_counter(); p=subprocess.Popen(cmd,stdout=log.open("w",encoding="utf-8"),stderr=subprocess.STDOUT)
    state={};
    for x in tree(p.pid):
        try: x.cpu_percent(None); state[x.pid]=x
        except: pass
    cpu=[]; gpu=[]
    while p.poll() is None:
        time.sleep(.5); total=0
        for x in tree(p.pid):
            try:
                old=state.get(x.pid)
                if old is None: x.cpu_percent(None)
                else: total+=old.cpu_percent(None)
                state[x.pid]=x
            except: pass
        cpu.append(total); gpu.append(sample_gpu())
    p.wait(); wall=time.perf_counter()-start
    data=json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    data.update({"sprites":sprites,"wall_sampled_s":wall,"bg_cpu":bg,"process_cpu_avg":statistics.mean(cpu) if cpu else 0,"process_cpu_peak":max(cpu) if cpu else 0,"avg_cores":statistics.mean(cpu)/100 if cpu else 0,"peak_cores":max(cpu)/100 if cpu else 0,"gpu_avg":statistics.mean(x["gpu"] for x in gpu) if gpu else 0,"gpu_peak":max(x["gpu"] for x in gpu) if gpu else 0,"encoder_avg":statistics.mean(x["encoder"] for x in gpu) if gpu else 0,"decoder_avg":statistics.mean(x["decoder"] for x in gpu) if gpu else 0,"returncode":p.returncode})
    return data

def med_phase0b():
    raw=json.loads((ROOT/"perf"/"tmp"/"phase0b_raw.json").read_text(encoding="utf-8")); c=next(c for c in raw["manifest"]["cases"] if c["category"]=="normal_dense"); name=c["name"]; out={}
    for g in ("E","F"):
        rows=[r for r in raw["runs"] if r["case"]==name and r["group"]==g]
        out[g]={"wall_s":statistics.median(r["wall_s"] for r in rows),"avg_cores":statistics.median(r["avg_cores"] for r in rows),"core_seconds":statistics.median(r["core_seconds"] for r in rows)}
    return out

def main():
    runs=[run(91,True),run(50),run(200)]; raw={"runs":runs,"phase0b_baseline":med_phase0b()}
    (ROOT/"perf"/"tmp"/"phase1a_raw.json").write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 阶段 1a：Python GPU 原型","","## 环境","- CPU：Intel(R) Core(TM) i7-14700KF","- GPU：NVIDIA GeForce RTX 4070 Ti SUPER；驱动 591.86；CUDA 13.1","- PyNvVideoCodec：2.2.4（Python 3.12 Windows wheel，NVIDIA 官方索引）","- CuPy：14.2.0；CUDA 12.x runtime/NVRTC wheels","- 输入：perf/samples/normal_dense/normal_dense_peak_7月30日20点23分/，NVDEC 直接读取 FLV。","- 每项 1 次；每项前背景 CPU 采样 10 秒；进程树 CPU 和 nvidia-smi GPU/encoder/decoder 每 0.5 秒采样。","","## 原型链路","PyNvVideoCodec SimpleDecoder 输出 NV12 GPU 平面；CuPy CUDA Array Interface 零拷贝包装 Y 平面；RawKernel 在显存中对每帧执行精灵压力合成。阶段 1a 的 NVENC 闭环探测失败，未生成视频输出。","","## 结果","| 压力 | 帧数 | 墙钟 s | fps | 平均/峰值核数 | GPU 平均/峰值% | NVENC/解码器平均% | 返回码 |","|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in runs: lines.append(f"| {r['sprites']} | {r.get('frames',0)} | {r['wall_sampled_s']:.2f} | {r.get('fps',0):.1f} | {r['avg_cores']:.2f}/{r['peak_cores']:.2f} | {r['gpu_avg']:.1f}/{r['gpu_peak']:.1f} | {r['encoder_avg']:.1f}/{r['decoder_avg']:.1f} | {r['returncode']} |")
    b=raw["phase0b_baseline"]; lines += ["","## 阶段 0b 对照（normal_dense）","",f"- 当前 E：{b['E']['wall_s']:.2f}s，平均 {b['E']['avg_cores']:.2f} 核，{b['E']['core_seconds']:.1f} 核·秒。",f"- 普通 F：{b['F']['wall_s']:.2f}s，平均 {b['F']['avg_cores']:.2f} 核，{b['F']['core_seconds']:.1f} 核·秒。","","## 阻塞与结论","PyNvVideoCodec 与 CuPy 的 NVDEC/GPU kernel 路径已跑通。PyNvVideoCodec 2.2.4 Windows 编码器对 CUDA Array Interface 输入返回 Error code 8: incorrect usage of CPU input buffer；CPU 输入模式可创建编码器，但不满足阶段 1a 的全 GPU 链路。因此本阶段未生成可播放输出视频，GPU 编码接口阻塞点已保留在 raw 结果和 gpu_render.py 中。","当前原型只证明了显存解码和 CuPy 精灵压力 kernel；50/200 精灵的 CPU/GPU趋势可用于后续修复 NVENC API 后继续。按 PLAN.md 时间控制，阶段 1a 在此停止，等待下一步判断。"]
    (ROOT/"perf"/"results"/"phase1a.md").write_text("\n".join(lines)+"\n",encoding="utf-8"); print(json.dumps(runs,ensure_ascii=False,indent=2))
if __name__=="__main__": main()

