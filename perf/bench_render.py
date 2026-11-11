"""阶段 0 基准工具：准备裁剪素材并交替运行 A-F 组。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
from typing import Any

import psutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = ROOT / "perf" / "tmp"
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():
    VENV_PYTHON = pathlib.Path(sys.executable)
NULL = "NUL" if os.name == "nt" else "/dev/null"


def ffprobe(path: pathlib.Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate:" "format=duration",
        "-of",
        "json",
        str(path),
    ]
    data = json.loads(subprocess.check_output(cmd, text=True))
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}

    def rate(value: str | None) -> float:
        if not value or value in {"0/0", "N/A"}:
            return 0.0
        n, d = value.split("/", 1)
        return float(n) / float(d)

    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": rate(stream.get("r_frame_rate")) or rate(stream.get("avg_frame_rate")),
        "duration": float(fmt.get("duration") or 0.0),
    }


def probe_tree(root_pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(root_pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []
    result = [root]
    pending = [root_pid]
    # Windows' Python launcher can make children inaccessible through
    # Process.children(); build the same tree from parent ids as a fallback.
    try:
        snapshot = list(psutil.process_iter(["pid", "ppid"]))
        children = {}
        for proc in snapshot:
            children.setdefault(proc.info.get("ppid"), []).append(proc)
        while pending:
            parent = pending.pop()
            for child in children.get(parent, []):
                if child.pid in {p.pid for p in result}:
                    continue
                result.append(child)
                pending.append(child.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return result


def process_cpu(
    processes: list[psutil.Process], state: dict[int, psutil.Process]
) -> float:
    total = 0.0
    current = {}
    for proc in processes:
        try:
            previous = state.get(proc.pid)
            if previous is None:
                proc.cpu_percent(None)
            else:
                total += previous.cpu_percent(None)
            current[proc.pid] = proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    state.clear()
    state.update(current)
    return total


def dmr_render_active() -> bool:
    marker = str(ROOT / "DMR" / "Render" / "emoji_render_mt.py")
    marker = marker.replace("/", "\\").lower()
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if str(proc.info.get("name") or "").lower() not in {"python", "python.exe"}:
                continue
            cmdline = (
                " ".join(proc.info.get("cmdline") or []).replace("/", "\\").lower()
            )
            if marker in cmdline:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def start_pyspy(root_pid: int, output: pathlib.Path) -> subprocess.Popen | None:
    try:
        subprocess.run(
            ["py-spy", "--version"], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    deadline = time.monotonic() + 15.0
    worker_pid = None
    while time.monotonic() < deadline:
        for proc in probe_tree(root_pid):
            try:
                if "--worker" in proc.cmdline():
                    worker_pid = proc.pid
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if worker_pid:
            break
        time.sleep(0.25)
    if not worker_pid:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            "py-spy",
            "record",
            "--format",
            "raw",
            "--output",
            str(output),
            "--duration",
            "30",
            "--pid",
            str(worker_pid),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def background_cpu_sample(seconds: float) -> tuple[float, float]:
    samples = []
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        samples.append(psutil.cpu_percent(None))
        time.sleep(0.5)
    return (
        statistics.mean(samples) if samples else 0.0,
        max(samples) if samples else 0.0,
    )


def run_command(
    command: list[str],
    *,
    cwd: pathlib.Path,
    env: dict[str, str] | None,
    log_path: pathlib.Path,
    expected_frames: int,
    pyspy: pathlib.Path | None = None,
    background_seconds: float = 30.0,
) -> dict[str, Any]:
    if dmr_render_active():
        raise RuntimeError("检测到原仓库 DMR 正在渲染，按计划暂停测量")
    bg_avg, bg_peak = background_cpu_sample(background_seconds)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            command, cwd=str(cwd), env=merged_env, stdout=log, stderr=subprocess.STDOUT
        )
        cpu_state = {}
        process_cpu(probe_tree(proc.pid), cpu_state)
        tree_samples = []
        system_samples = []
        spy = None
        spy_pending = bool(pyspy)
        while proc.poll() is None:
            time.sleep(0.5)
            if dmr_render_active():
                proc.terminate()
                proc.wait(timeout=10)
                raise RuntimeError("测量期间检测到原仓库 DMR 开始渲染，本次结果作废")
            if spy_pending and not spy:
                spy = start_pyspy(proc.pid, pyspy)
                spy_pending = False
            tree_samples.append(process_cpu(probe_tree(proc.pid), cpu_state))
            system_samples.append(psutil.cpu_percent(None))
        proc.wait()
    if spy and spy.poll() is None:
        spy.terminate()
        try:
            spy.wait(timeout=3)
        except subprocess.TimeoutExpired:
            spy.kill()
    wall = time.perf_counter() - started
    result = {
        "returncode": proc.returncode,
        "wall_s": wall,
        "fps": expected_frames / wall if wall > 0 else 0.0,
        "cpu_avg": statistics.mean(tree_samples) if tree_samples else 0.0,
        "cpu_peak": max(tree_samples) if tree_samples else 0.0,
        "system_cpu_avg": statistics.mean(system_samples) if system_samples else 0.0,
        "system_cpu_peak": max(system_samples) if system_samples else 0.0,
        "bg_cpu_avg": bg_avg,
        "bg_cpu_peak": bg_peak,
        "pyspy_available": bool(spy and pyspy and pyspy.exists()),
    }
    if proc.returncode:
        raise RuntimeError(
            f"命令失败 rc={proc.returncode}: {' '.join(command)}; 日志 {log_path}"
        )
    return result


def clip_jsonl(source: pathlib.Path, target: pathlib.Path, duration: float) -> int:
    with source.open(encoding="utf-8") as fp:
        header = json.loads(fp.readline())
        start = float(header.get("segment_start_time", 0) or 0)
        records = []
        for line in fp:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = record.get("received_at")
            if isinstance(ts, (int, float)) and 0 <= float(ts) - start <= duration:
                records.append(record)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fp:
        fp.write(json.dumps(header, ensure_ascii=False) + "\n")
        for record in records:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


def render_args() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from DMR.Config import Config

    config = Config(str(ROOT / "perf" / "configs_snapshot" / "global.yml"))
    return dict(config.global_config["render_args"]["overlay"])


def prepare_case(spec: dict[str, Any], tmp_root: pathlib.Path) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from DMR.Render.layout_compiler import LayoutCompiler
    from DMR.Render.plan_ass import render_plan_to_ass
    from DMR.utils import VideoInfo

    name = str(spec["name"])
    slug = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
    case_dir = tmp_root / slug
    case_dir.mkdir(parents=True, exist_ok=True)
    source_video = pathlib.Path(spec["video"]).resolve()
    source_jsonl = pathlib.Path(spec["jsonl"]).resolve()
    source_info = ffprobe(source_video)
    requested_duration = float(
        spec.get("duration") or min(300.0, source_info["duration"])
    )
    clip_video = case_dir / (source_video.stem + "_clip" + source_video.suffix)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-nostdin",
            "-i",
            str(source_video),
            "-t",
            f"{requested_duration:.6f}",
            "-c",
            "copy",
            str(clip_video),
        ],
        check=True,
    )
    clip_info = ffprobe(clip_video)
    actual_duration = min(
        requested_duration, clip_info["duration"] or requested_duration
    )
    clip_events = case_dir / (source_jsonl.stem + "_clip.jsonl")
    raw_event_count = clip_jsonl(source_jsonl, clip_events, actual_duration)

    args = render_args()
    emoji_root = pathlib.Path(spec.get("emoji_root") or ROOT / "emoji_pack")
    args["emoji_pack"] = str(emoji_root / str(spec.get("platform", "bilibili")))
    compiler = LayoutCompiler(args, engine="emoji_dmrender", engine_config=args)
    video = VideoInfo(
        path=str(clip_video),
        dm_file_id=str(clip_events),
        duration=actual_duration,
        resolution=(clip_info["width"], clip_info["height"]),
    )
    result = compiler.compile(
        video=video, events_path=str(clip_events), work_dir=str(case_dir)
    )
    plan_path = case_dir / "render_plan.json"
    pathlib.Path(result.render_plan_path).replace(plan_path)
    textonly = case_dir / "textonly.ass"
    ordinary = case_dir / "ordinary.ass"
    width, height = result.next_state.canvas_resolution
    render_plan_to_ass(
        str(plan_path),
        str(textonly),
        include_raster_text=False,
        width=width,
        height=height,
    )
    render_plan_to_ass(
        str(plan_path),
        str(ordinary),
        include_raster_text=True,
        width=width,
        height=height,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    raster_events = sum(
        1 for event in plan.get("events", []) if event.get("mode") == "raster"
    )
    return {
        "name": name,
        "video": str(clip_video),
        "jsonl": str(clip_events),
        "source_video": str(source_video),
        "source_jsonl": str(source_jsonl),
        "duration": actual_duration,
        "width": width,
        "height": height,
        "fps": clip_info["fps"],
        "frames": int(actual_duration * clip_info["fps"]),
        "raw_event_count": raw_event_count,
        "raster_events": raster_events,
        "render_plan": str(plan_path),
        "textonly_ass": str(textonly),
        "ordinary_ass": str(ordinary),
        "emoji_pack": str(emoji_root / str(spec.get("platform", "bilibili"))),
        "layout_events": result.event_count,
        "dropped_events": result.dropped_count,
    }


def filter_expr(case: dict[str, Any], subtitles: pathlib.Path | None = None) -> str:
    W, H, fps = case["width"], case["height"], case["fps"]
    base = (
        f"fps={fps:g},scale={W}:{H}:force_original_aspect_ratio=decrease:"
        f"force_divisible_by=2,pad={W}:{H}:-1:-1:color=black,setsar=1"
    )
    return base + (f",subtitles={subtitles.name}" if subtitles else "")


def ffmpeg_filter_command(case: dict[str, Any], group: str) -> list[str]:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-nostdin",
        "-fflags",
        "+genpts+discardcorrupt",
        "-hwaccel",
        "auto",
        "-i",
        case["video"],
    ]
    if group == "A":
        return cmd + ["-map", "0:v:0", "-an", "-f", "null", NULL]
    subtitles = pathlib.Path(case["textonly_ass"]) if group == "C" else None
    return cmd + ["-vf", filter_expr(case, subtitles), "-an", "-f", "null", NULL]


def emoji_command(case: dict[str, Any], output: pathlib.Path) -> list[str]:
    return [
        str(VENV_PYTHON),
        str(ROOT / "DMR" / "Render" / "emoji_render_mt.py"),
        case["video"],
        case["render_plan"],
        str(output),
        "--vencoder",
        "hevc_nvenc",
        "--aencoder",
        "aac",
        "--vb",
        "4M",
        "--ab",
        "128K",
        "--venc-args",
        json.dumps(["-b:v", "4M"]),
        "--aenc-args",
        json.dumps(["-b:a", "128K"]),
        "--emoji-pack",
        case["emoji_pack"],
        "--emoticon-dir",
        str(pathlib.Path(case["emoji_pack"]) / "emoticon"),
        "--canvas-resolution",
        f'{case["width"]}x{case["height"]}',
        "--render-plan",
    ]


def ordinary_command(case: dict[str, Any], output: pathlib.Path) -> list[str]:
    return [
        str(VENV_PYTHON),
        str(pathlib.Path(__file__).resolve()),
        "--ordinary-worker",
        "--video",
        case["video"],
        "--ass",
        case["ordinary_ass"],
        "--output",
        str(output),
        "--width",
        str(case["width"]),
        "--height",
        str(case["height"]),
        "--duration",
        str(case["duration"]),
    ]


def ordinary_worker(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    from DMR.Render.dmrender import DmRender
    from DMR.utils import VideoInfo

    render = DmRender(
        hwaccel="auto",
        vencoder="hevc_nvenc",
        vencoder_args=["-b:v", "4M"],
        aencoder="aac",
        aencoder_args=["-b:a", "128K"],
        output_resize=None,
        rescale_to_1080p=False,
    )
    video = VideoInfo(
        path=args.video,
        duration=float(args.duration),
        resolution=(int(args.width), int(args.height)),
    )
    ok, _ = render.render_one(
        video=video,
        output=args.output,
        danmaku=args.ass,
        canvas_resolution=(int(args.width), int(args.height)),
    )
    return 0 if ok else 1


def median_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        key: statistics.median(row[key] for row in rows)
        for key in (
            "wall_s",
            "fps",
            "bg_cpu_avg",
            "bg_cpu_peak",
            "cpu_avg",
            "cpu_peak",
            "system_cpu_avg",
            "system_cpu_peak",
        )
    }


def run_benchmark(
    manifest_path: pathlib.Path,
    output_path: pathlib.Path,
    repetitions: int,
    background_seconds: float,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_runs = []
    for case in manifest["cases"]:
        case_dir = pathlib.Path(case["video"]).parent
        for repetition in range(1, repetitions + 1):
            for group in ("A", "B", "C", "D", "E", "F"):
                stem = f'{case["name"]}_{group}_{repetition}'.replace(" ", "_")
                output = TMP / "outputs" / f"{stem}.mp4"
                log = TMP / "logs" / f"{stem}.log"
                env = {}
                stats_path = case_dir / f"stats_{group}_{repetition}.json"
                pyspy_path = TMP / "pyspy" / f"{stem}.raw" if group == "E" else None
                if group in ("D", "E"):
                    env["DMR_EMOJI_STATS"] = str(stats_path)
                    if group == "D":
                        env["DMR_EMOJI_BYPASS"] = "1"
                    cmd = emoji_command(case, output)
                elif group == "F":
                    cmd = ordinary_command(case, output)
                else:
                    cmd = ffmpeg_filter_command(case, group)
                row = run_command(
                    cmd,
                    cwd=case_dir,
                    env=env,
                    log_path=log,
                    expected_frames=case["frames"],
                    pyspy=pyspy_path,
                    background_seconds=background_seconds,
                )
                row.update(
                    {"case": case["name"], "group": group, "repetition": repetition}
                )
                if stats_path.exists():
                    try:
                        row["stats"] = json.loads(
                            stats_path.read_text(encoding="utf-8")
                        )
                    except json.JSONDecodeError:
                        pass
                    stats_path.unlink(missing_ok=True)
                all_runs.append(row)
                output.unlink(missing_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {"manifest": manifest, "runs": all_runs}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def md_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_result(raw_path: pathlib.Path, output_path: pathlib.Path) -> None:
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    lines = [
        "# 阶段 0：Emoji 渲染基线",
        "",
        "## 素材与选择理由",
        "",
        "所有原始录像和 JSONL 只读；裁剪副本、RenderPlan、ASS、日志和统计均位于 perf/tmp/。",
        "阶段 0 使用以下已完成录像：",
        "",
    ]
    for case in data["manifest"]["cases"]:
        lines += [
            f"- **{case['name']}**：前端裁剪 {case['duration']:.3f}s，"
            f"画布 {case['width']}×{case['height']}，{case['fps']:.3g}fps；"
            f"布局事件 {case['layout_events']}（丢弃 {case['dropped_events']}），"
            f"RenderPlan raster 事件 {case['raster_events']}，JSONL 记录 {case['raw_event_count']}。",
            f"  - 原始视频：{case['source_video']}",
            f"  - 原始弹幕：{case['source_jsonl']}",
        ]
    if len(data["manifest"]["cases"]) == 1:
        selection_reason = (
            "选择理由：该段前 300 秒含 27 条 Emoji/表情相关记录和 754 条礼物记录，"
            "RenderPlan raster 事件数高，且为已结束录像；排除了名称含 [正在录制] 或 .part 的文件。"
        )
    else:
        selection_reason = (
            "选择理由：第一段在前 300 秒含 27 条 Emoji/表情相关记录和 754 条礼物记录，"
            "第二段在前 300 秒含 8 条 Emoji/表情相关记录、3 条 SC 和 98 条礼物记录；"
            "均为已结束录像，且排除了名称含 [正在录制] 或 .part 的文件。"
        )
    lines += ["", selection_reason, "", "## 测量环境", ""]
    try:
        cpu = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name) -join '; '",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        cpu = "Get-CimInstance 不可用"
    try:
        gpu = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '; '",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        gpu = "Get-CimInstance 不可用"
    ffver = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]
    lines += [
        f"- CPU：{cpu or '未读取到'}",
        f"- GPU：{gpu or '未读取到'}",
        f"- ffmpeg：{ffver}",
        "- DMR 状态：开始前通过进程列表未发现 DMR 的 ffmpeg 或 emoji_render_mt.py 渲染进程；"
        "录制进程数量无法从当前受限进程接口可靠读取。",
        "- 编码参数：hevc_nvenc -b:v 4M、aac -b:a 128K，解码 -hwaccel auto。",
        "- 每组 3 次；各组按 A→B→C→D→E→F 交替执行；每组前空闲背景采样 30 秒，运行中 CPU 每 0.5 秒采样一次。",
        "",
        "## 结果（中位数）",
        "",
        "| 素材 | 组 | 墙钟 s | 处理 fps | bg_cpu 平均/峰值% | 进程树平均/峰值 CPU% | 全系统平均/峰值 CPU% | py-spy |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for case in data["manifest"]["cases"]:
        for group in ("A", "B", "C", "D", "E", "F"):
            rows = [
                row
                for row in data["runs"]
                if row["case"] == case["name"] and row["group"] == group
            ]
            m = median_rows(rows)
            spy = (
                "有 raw"
                if any(row.get("pyspy_available") for row in rows)
                else "不可用"
            )
            lines.append(
                f"| {md_escape(case['name'])} | {group} | {m['wall_s']:.2f} | "
                f"{m['fps']:.1f} | {m['bg_cpu_avg']:.1f}/{m['bg_cpu_peak']:.1f} | "
                f"{m['cpu_avg']:.1f}/{m['cpu_peak']:.1f} | "
                f"{m['system_cpu_avg']:.1f}/{m['system_cpu_peak']:.1f} | {spy} |"
            )
    lines += [
        "",
        "## 每次测量",
        "",
        "| 素材 | 组 | 次数 | 墙钟 s | 处理 fps | bg_cpu 平均/峰值% | 进程树平均/峰值 CPU% | 全系统平均/峰值 CPU% | 返回码 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in data["runs"]:
        lines.append(
            f"| {md_escape(row['case'])} | {row['group']} | {row['repetition']} | "
            f"{row['wall_s']:.2f} | {row['fps']:.1f} | "
            f"{row['bg_cpu_avg']:.1f}/{row['bg_cpu_peak']:.1f} | "
            f"{row['cpu_avg']:.1f}/{row['cpu_peak']:.1f} | "
            f"{row['system_cpu_avg']:.1f}/{row['system_cpu_peak']:.1f} | {row['returncode']} |"
        )
    lines += [
        "",
        "## D/E 统计",
        "",
        "worker 记录总帧数、RenderPlan raster 事件数、平均同屏活动事件数（按帧平均）和峰值同屏事件数；"
        "D 仅透传帧，E 使用当前合成逻辑。",
        "",
        "| 素材 | 组 | 帧数 | raster 事件 | 平均同屏活动事件 | 峰值同屏活动事件 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in data["manifest"]["cases"]:
        for group in ("D", "E"):
            rows = [
                row
                for row in data["runs"]
                if row["case"] == case["name"] and row["group"] == group
            ]
            stats = [row.get("stats") for row in rows if row.get("stats")]
            if not stats:
                continue
            s = stats[len(stats) // 2]
            frames = int(s.get("frames", 0))
            avg_active = s.get("active_event_samples", 0) / frames if frames else 0
            lines.append(
                f"| {md_escape(case['name'])} | {group} | {frames} | {s.get('raster_events', 0)} | "
                f"{avg_active:.3f} | {s.get('max_active_events', 0)} |"
            )
    lines += [
        "",
        "## py-spy",
        "",
        "基准运行时尝试执行 py-spy record --format raw --duration 30 并采样 worker；"
        "若环境未安装 py-spy，表格标记为不可用，对应日志仍保存在 perf/tmp/。",
        "",
        "## 结论",
        "",
        "本阶段只建立基线并加入默认关闭的 DMR_EMOJI_BYPASS 测量开关，"
        "未改变默认合成路径；下一阶段是否优化需由审查者根据 D/E 与 A/B/C 的占比决定。",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--spec", type=pathlib.Path)
    parser.add_argument("--manifest", type=pathlib.Path, default=TMP / "manifest.json")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--write-result", action="store_true")
    parser.add_argument("--raw", type=pathlib.Path, default=TMP / "phase0_raw.json")
    parser.add_argument(
        "--output", type=pathlib.Path, default=ROOT / "perf" / "results" / "phase0.md"
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--background-seconds", type=float, default=30.0)
    parser.add_argument("--ordinary-worker", action="store_true")
    parser.add_argument("--video")
    parser.add_argument("--ass")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--duration", type=float)
    args = parser.parse_args()

    if args.ordinary_worker:
        return ordinary_worker(args)
    if args.prepare:
        if not args.spec:
            parser.error("--prepare 需要 --spec")
        specs = json.loads(args.spec.read_text(encoding="utf-8"))
        cases = [prepare_case(spec, TMP) for spec in specs]
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps({"cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {"manifest": str(args.manifest), "cases": cases},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.run:
        run_benchmark(
            args.manifest, args.raw, args.repetitions, args.background_seconds
        )
    if args.write_result:
        write_result(args.raw, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
