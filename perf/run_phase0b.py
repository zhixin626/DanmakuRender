
from __future__ import annotations
import argparse
import json, pathlib, shutil, subprocess, sys, time, statistics, os
import psutil
ROOT=pathlib.Path(__file__).resolve().parents[1]
TMP=ROOT/"perf"/"tmp"/"phase0b"
OUT=ROOT/"perf"/"outputs"/"phase0b"
SAMPLES=ROOT/"perf"/"samples"
sys.path.insert(0,str(ROOT))
import bench_render as b
PYSPY=ROOT/"perf"/"tmp"/"venv"/"Scripts"/"py-spy.exe"

def worker_fps(path):
    try:
        import av
        with av.open(str(path)) as c:
            s=c.streams.video[0]
            r=s.guessed_rate or s.average_rate
            if r: return float(r)
    except Exception: pass
    return float(b.ffprobe(path).get("fps") or 0)

def classify(p):
    try:
        cmd=" ".join(p.cmdline()).lower().replace("/","\\")
        name=(p.name() or "").lower()
    except (psutil.NoSuchProcess,psutil.AccessDenied):
        return "other"
    if "emoji_render_mt.py" in cmd or ("python" in name and "--worker" in cmd): return "worker"
    if "ffmpeg" in name:
        return "encoder" if " -i -" in (" "+cmd) else "decoder"
    return "other"

def sample_tree(pid,state):
    total=0.0; parts={"decoder":0.0,"worker":0.0,"encoder":0.0,"other":0.0}; cur={}
    for p in b.probe_tree(pid):
        try:
            old=state.get(p.pid)
            val=0.0 if old is None else old.cpu_percent(None)
            if old is None: p.cpu_percent(None)
            cur[p.pid]=p; total+=val; parts[classify(p)]+=val
        except (psutil.NoSuchProcess,psutil.AccessDenied): pass
    state.clear(); state.update(cur)
    return total,parts

def start_pyspy(pid,out):
    if not PYSPY.exists(): return None
    worker=None; deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        for p in b.probe_tree(pid):
            try:
                if "--worker" in p.cmdline(): worker=p.pid; break
            except (psutil.NoSuchProcess,psutil.AccessDenied): pass
        if worker: break
        time.sleep(.25)
    if not worker: return None
    out.parent.mkdir(parents=True,exist_ok=True)
    return subprocess.Popen([str(PYSPY),"record","--format","raw","--output",str(out),
                             "--duration","30","--pid",str(worker)],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def count_frames(path):
    if not path.exists(): return 0
    try:
        d=json.loads(subprocess.check_output(["ffprobe","-v","error","-count_frames",
          "-select_streams","v:0","-show_entries","stream=nb_read_frames","-of","json",str(path)],text=True))
        return int((d.get("streams") or [{}])[0].get("nb_read_frames") or 0)
    except Exception: return 0

def progress_frames(path):
    if not path.exists(): return 0
    n=0
    for line in path.read_text(encoding="utf-8",errors="replace").splitlines():
        if line.startswith("frame="):
            try: n=int(line.split("=",1)[1])
            except ValueError: pass
    return n

CASE_MAP={}

def run_command(command, *, cwd, env, log_path, expected_frames, pyspy=None, background_seconds=30.0):
    if b.dmr_render_active(): raise RuntimeError("检测到 DMR 渲染进程")
    bg_avg,bg_peak=b.background_cpu_sample(background_seconds)
    merged=os.environ.copy(); merged.update(env)
    stem=log_path.stem
    progress=TMP/"progress"/(stem+".txt")
    progress.unlink(missing_ok=True)
    progress.parent.mkdir(parents=True,exist_ok=True)
    is_null=("-f" in command and "null" in command)
    cmd=list(command)
    if is_null:
        cmd=cmd[:5]+["-progress",str(progress),"-nostats"]+cmd[5:]
    started=time.perf_counter()
    with log_path.open("w",encoding="utf-8",errors="replace") as log:
        proc=subprocess.Popen(cmd,cwd=str(cwd),env=merged,stdout=log,stderr=subprocess.STDOUT)
        state={}; sample_tree(proc.pid,state)
        tree=[]; system=[]; comps=[]; spy=None; pending=bool(pyspy)
        while proc.poll() is None:
            time.sleep(.5)
            if b.dmr_render_active():
                proc.terminate(); proc.wait(timeout=10)
                raise RuntimeError("测量期间 DMR 开始渲染")
            if pending:
                spy=start_pyspy(proc.pid,pyspy) if pyspy else None
                pending=False
            total,part=sample_tree(proc.pid,state)
            tree.append(total); comps.append(part); system.append(psutil.cpu_percent(None))
        proc.wait()
    if spy:
        try: spy.wait(timeout=5)
        except subprocess.TimeoutExpired: spy.terminate()
    wall=time.perf_counter()-started
    outs=[pathlib.Path(x) for x in cmd if str(x).lower().endswith(".mp4")]
    outpath=outs[0] if outs else None
    frames=progress_frames(progress) if is_null else count_frames(outpath) if outpath else expected_frames
    if proc.returncode:
        raise RuntimeError("命令失败 rc=%s 日志 %s"%(proc.returncode,log_path))
    avg=statistics.mean(tree) if tree else 0
    peak=max(tree) if tree else 0
    comp={k:(statistics.mean([x[k] for x in comps]) if comps else 0) for k in ("decoder","worker","encoder","other")}
    logical=psutil.cpu_count() or 1
    adj=[max(0,(x-bg_avg)*logical) for x in system]
    row={"returncode":proc.returncode,"wall_s":wall,"frames":frames,"fps":frames/wall if wall else 0,
         "cpu_avg":avg,"cpu_peak":peak,"avg_cores":avg/100,"peak_cores":peak/100,
         "core_seconds":avg/100*wall,"system_cpu_avg":statistics.mean(system) if system else 0,
         "system_cpu_peak":max(system) if system else 0,"adjusted_cores_avg":statistics.mean(adj)/100 if adj else 0,
         "adjusted_cores_peak":max(adj)/100 if adj else 0,"bg_cpu_avg":bg_avg,"bg_cpu_peak":bg_peak,
         "component_cpu_avg":comp,"pyspy_available":bool(spy and pyspy and pyspy.exists())}
    for slug,(category,case_name) in CASE_MAP.items():
        if stem.startswith(slug+"_E_1") or stem.startswith(slug+"_F_1"):
            final=OUT/category/slug/("E_emoji_dmrender.mp4" if "_E_" in stem else "F_dmrender.mp4")
            final.parent.mkdir(parents=True,exist_ok=True)
            if outpath and outpath.exists(): shutil.copy2(outpath,final)
    return row

def interval_stats(plan,duration):
    ints=[]
    for e in plan.get("events") or []:
        if e.get("mode")=="raster" and len(e.get("path") or [])>=2:
            ints.append((float(e["path"][0][0]),float(e["path"][-1][0])))
    pts=sorted([(a,1) for a,b in ints]+[(b,-1) for a,b in ints]); cur=peak=0; last=area=0
    for t,d in pts:
        area+=cur*max(0,t-last); cur+=d; peak=max(peak,cur); last=t
    return len(ints),area/max(duration,1),peak

def report(data,out):
    cases=data["manifest"]["cases"]; runs=data["runs"]; cat_by_case={c["name"]:c.get("category","") for c in cases}
    lines=["# 阶段 0b：修正测量与分类补测","","## 测量环境",
           "- CPU：Intel(R) Core(TM) i7-14700KF","- GPU：NVIDIA GeForce RTX 4070 Ti SUPER",
           "- ffmpeg："+subprocess.check_output(["ffmpeg","-version"],text=True).splitlines()[0],
           "- 逻辑核数："+str(psutil.cpu_count() or 1),
           "- 每组 3 次；组顺序 A→B→C→D→E→F；每组前背景 CPU 采样 30 秒；运行中每 0.5 秒采样。",
           "- A/B/C 使用 worker guessed_rate/average_rate；帧数来自 ffmpeg progress；D/E 来自输出计数与 worker stats；F 来自 ffprobe。","",
           "## 素材"]
    for c in cases:
        lines += ["",f"- {c['category']}/{c['name']}：源 {c.get('source_width')}x{c.get('source_height')}，画布 {c.get('width')}x{c.get('height')}，worker {c['fps']:.6g}fps，raster {c.get('raster_events')}，平均/峰值同屏 {c.get('avg_active_events',0):.3f}/{c.get('peak_active_events',0)}。",
                   f"  原始视频：{c['source_video']}",f"  原始弹幕：{c['source_jsonl']}",
                   f"  截取视频：{c['video']}",f"  E 输出：perf/outputs/phase0b/{c['category']}/{c['slug']}/E_emoji_dmrender.mp4",
                   f"  F 输出：perf/outputs/phase0b/{c['category']}/{c['slug']}/F_dmrender.mp4"]
    lines += ["","用户限定目录下最高密度的是峰哥竖屏素材；它作为 normal_dense 代表，输出保持源画布。计划目标 raster 500/平均同屏 10 未达到，横屏不可一世杀手素材更低，具体数值见 samples.md。","","## 中位数结果",
      "| 分类/素材 | 组 | 墙钟s | 帧数 | fps | 进程树CPU均/峰 | 核数均/峰 | 核秒 | 系统减背景核数均/峰 | D/E 解码/worker/编码CPU | py-spy |",
      "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for c in cases:
        for g in "ABCDEF":
            rs=[r for r in runs if r["case"]==c["name"] and r["group"]==g]
            m={k:statistics.median(float(x.get(k,0)) for x in rs) for k in ("wall_s","frames","fps","cpu_avg","cpu_peak","avg_cores","peak_cores","core_seconds","adjusted_cores_avg","adjusted_cores_peak")}
            comp={k:statistics.median(float(x.get("component_cpu_avg",{}).get(k,0)) for x in rs) for k in ("decoder","worker","encoder")}
            spy="有 raw" if any(x.get("pyspy_available") for x in rs) else "不可用"
            lines.append(f"| {c['category']}/{c['name']} | {g} | {m['wall_s']:.2f} | {int(m['frames'])} | {m['fps']:.1f} | {m['cpu_avg']:.1f}/{m['cpu_peak']:.1f} | {m['avg_cores']:.2f}/{m['peak_cores']:.2f} | {m['core_seconds']:.1f} | {m['adjusted_cores_avg']:.2f}/{m['adjusted_cores_peak']:.2f} | {comp['decoder']:.1f}/{comp['worker']:.1f}/{comp['encoder']:.1f} | {spy} |")
    lines += ["","## 每次运行","",
      "| 分类/素材 | 组 | 次数 | 墙钟s | 帧数 | fps | 进程树平均CPU | 平均核数 | 核秒 | 返回码 |",
      "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in runs:
        lines.append(f"| {cat_by_case.get(r['case'],'')}/{r['case']} | {r['group']} | {r['repetition']} | {r['wall_s']:.2f} | {r['frames']} | {r['fps']:.1f} | {r['cpu_avg']:.1f} | {r['avg_cores']:.2f} | {r['core_seconds']:.1f} | {r['returncode']} |")
    lines += ["","## py-spy","",f"版本：{subprocess.check_output([str(PYSPY),'--version'],text=True).strip() if PYSPY.exists() else '不可用'}"]
    for p in sorted((TMP/"pyspy").glob("*.raw")) if (TMP/"pyspy").exists() else []:
        lines += ["",p.name,"---"]+p.read_text(encoding="utf-8",errors="replace").splitlines()[:15]
    lines += ["","## 结论","A/B/C 已按 worker 帧率和实际帧数测量；同时报告进程树 CPU 与系统减背景核数。D/E 提供解码、worker、编码 CPU 拆分。阶段 0b 完成后停止。"]
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text("\n".join(lines)+"\n",encoding="utf-8")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--spec",type=pathlib.Path,required=True)
    ap.add_argument("--manifest",type=pathlib.Path,default=TMP/"manifest.json")
    ap.add_argument("--raw",type=pathlib.Path,default=TMP/"phase0b_raw.json")
    ap.add_argument("--output",type=pathlib.Path,default=ROOT/"perf"/"results"/"phase0b.md")
    ap.add_argument("--prepare",action="store_true"); ap.add_argument("--run",action="store_true")
    ap.add_argument("--repetitions",type=int,default=3); ap.add_argument("--background-seconds",type=float,default=30)
    a=ap.parse_args(); specs=json.loads(a.spec.read_text(encoding="utf-8-sig"))
    if a.prepare:
        cases=[]
        for s in specs:
            c=b.prepare_case(s,b.TMP); probe=b.ffprobe(pathlib.Path(c["video"])); c["source_width"]=probe["width"]; c["source_height"]=probe["height"]; c["fps"]=worker_fps(pathlib.Path(c["video"])); c["frames"]=int(round(c["duration"]*c["fps"]))
            c["category"]=s["category"]; c["slug"]="".join(ch if ch.isalnum() else "_" for ch in s["name"]).strip("_")
            sample=SAMPLES/s["category"]/c["slug"]; sample.mkdir(parents=True,exist_ok=True)
            sv=sample/pathlib.Path(c["video"]).name; sj=sample/pathlib.Path(c["jsonl"]).name
            if not sv.exists(): shutil.copy2(c["video"],sv)
            shutil.copy2(c["jsonl"],sj)
            for key in ("render_plan","textonly_ass","ordinary_ass"):
                src=pathlib.Path(c[key]); dst=sample/src.name; shutil.copy2(src,dst); c[key]=str(dst)
            src_em=pathlib.Path(c["video"]).parent/"emoticon"; dst_em=sample/"emoticon"
            if src_em.exists() and not dst_em.exists(): shutil.copytree(src_em,dst_em)
            c["video"]=str(sv); c["jsonl"]=str(sj)
            p=json.loads(pathlib.Path(c["render_plan"]).read_text(encoding="utf-8")); n,avg,peak=interval_stats(p,c["duration"])
            c["raster_events"]=n; c["avg_active_events"]=avg; c["peak_active_events"]=peak; cases.append(c)
        a.manifest.parent.mkdir(parents=True,exist_ok=True); a.manifest.write_text(json.dumps({"cases":cases},ensure_ascii=False,indent=2),encoding="utf-8")
        lines=["# 阶段 0b 素材清单",""]
        for c in cases:
            lines += [f"## {c['category']}/{c['name']}",f"- 原始视频：{c['source_video']}",f"- 原始弹幕：{c['source_jsonl']}",f"- 截取视频：{c['video']}",f"- 截取区间：前 {c['duration']:.1f}s",f"- 源分辨率：{c['source_width']}x{c['source_height']}；画布：{c['width']}x{c['height']}；worker：{c['fps']:.6g}fps",f"- JSONL：{c['raw_event_count']}；布局：{c['layout_events']}；raster：{c['raster_events']}；平均/峰值同屏：{c['avg_active_events']:.3f}/{c['peak_active_events']}",""]
        (ROOT/"perf"/"samples.md").write_text("\n".join(lines),encoding="utf-8")
        print(json.dumps({"cases":cases},ensure_ascii=False,indent=2)); return
    data=json.loads(a.manifest.read_text(encoding="utf-8")); CASE_MAP={c["slug"]:(c["category"],c["name"]) for c in data["cases"]}
    b.run_command=run_command
    def starter(pid,out): return pyspy(pid,out)
    b.start_pyspy=starter
    if a.run:
        b.run_benchmark(a.manifest,a.raw,a.repetitions,a.background_seconds)
    report(json.loads(a.raw.read_text(encoding="utf-8")),a.output)

if __name__=="__main__": main()
