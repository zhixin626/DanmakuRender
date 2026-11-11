"""Exercise minority-orientation rerender through isolated Render and Merger plugins."""

from pathlib import Path
import copy
import json
import queue
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from DMR.Config import Config
from DMR.Render import Render, get_engine
from DMR.Render.layout_compiler import LayoutCompiler
from DMR.Merger import Merger
from DMR.Task.liveevents import LiveEvents
from DMR.utils import FFprobe, PipeMessage, VideoInfo
from perf.dmr_gate import decide


def clip_and_events(work, source, start, duration, name, segment_start):
    video = work / f"{name}.flv"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-i", str(source),
         "-t", str(duration), "-c", "copy", "-avoid_negative_ts", "make_zero", str(video)],
        check=True,
    )
    events = work / f"{name}.jsonl"
    events.write_text(
        json.dumps({"type": "meta", "schema": 1, "platform": "bilibili", "segment_start_time": segment_start}) + "\n",
        encoding="utf8",
    )
    return video, events


def render_segment(options, video, events, output, orientation):
    info = VideoInfo(
        path=str(video), dm_file_id=str(events), duration=FFprobe.get_timeline_duration(str(video)),
        resolution=FFprobe.get_resolution(str(video)), group_id="synthetic", segment_id=orientation,
    )
    args = copy.deepcopy(options)
    args["rescale_to_1080p"] = True
    args["rescale_orientation"] = orientation
    compiled = LayoutCompiler(args, engine="gpu_render", engine_config=args).compile(
        video=info, events_path=str(events), work_dir=str(output.parent)
    )
    try:
        renderer = get_engine("gpu_render").load()(**args)
        ok, result = renderer.render_one(
            info, str(output), render_plan=compiled.render_plan_path,
            canvas_resolution=compiled.next_state.canvas_resolution,
            fps=50,
        )
        if not ok:
            raise RuntimeError(result)
        return info, result
    finally:
        compiled.cleanup()


def main():
    gate = decide(20)
    if not gate["go"]:
        print(json.dumps(gate, ensure_ascii=False))
        return 10
    work = ROOT / "perf/tmp/night/resolution_chain"
    work.mkdir(parents=True, exist_ok=True)
    delivery = ROOT / "演示视频/2026-11-11_12_少数方向重渲染"
    delivery.mkdir(parents=True, exist_ok=True)
    material = ROOT / "演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段"
    problem = ROOT / "perf/samples/problem_resolution/problem_resolution_7月30日22点50分/7月30日22点50分_clip.flv"
    horizontal, h_events = clip_and_events(work, problem, 0, 10, "horizontal", 1000)
    vertical, v_events = clip_and_events(work, material / "7月30日20点23分_中段.flv", 90, 3, "vertical", 1010)
    config = Config(str(material / "配置/global.yml"))
    config.config_dir = str(material / "配置")
    options = config.get_render_args("峰哥")
    options.update(
        resolution_ignore_head_seconds=0,
        resolution_min_span_seconds=0,
        resolution_minority_ratio=0.15,
        resolution_minority_minutes=0.5,
    )
    h_src, h_out = render_segment(options, horizontal, h_events, work / "horizontal_G.mp4", "landscape")
    v_src, v_out = render_segment(options, vertical, v_events, work / "vertical_G.mp4", "portrait")
    handler_config = {
        "render_args": {"overlay": options},
        "common_event_args": {"auto_render": True, "auto_merge": True, "auto_upload": False, "auto_clean": False},
        "merge_args": {"merge_type": ["dm_video"], "remove_source": False, "retry": 0, "file_name_template": "resolution_chain"},
        "download_args": {"output_dir": str(work)},
    }
    handler = LiveEvents("synthetic", handler_config)
    handler.ended_dict["synthetic"] = True
    handler.state_dict["synthetic"] = [
        {"src_video_pre": {"status": "ready", "file": h_src, "wait": []}, "dm_video": {"status": "ready", "file": h_out, "wait": []}},
        {"src_video_pre": {"status": "ready", "file": v_src, "wait": []}, "dm_video": {"status": "ready", "file": v_out, "wait": []}},
    ]
    handler.session_data["synthetic"] = {}
    decision = handler._log_resolution_policy("synthetic")
    send = queue.Queue()
    render_plugin = Render((send, queue.Queue()), failed_tasks_file=str(work / "failed.json"))
    render_plugin.stoped = False
    try:
        rerenders = handler._dispatch_resolution_rerender("synthetic", decision)
        if len(rerenders) != 1:
            raise RuntimeError(f"expected one minority rerender, got {len(rerenders)}")
        render_plugin.add_task(rerenders[0])
        end = send.get(timeout=180)
        merge_messages = handler.onRenderEnd(end)
        if not merge_messages:
            merge_messages = handler._check_for_merge("synthetic")
        merge_messages = [
            message for message in merge_messages if message.target == "merger"
        ]
        if len(merge_messages) > 1:
            merge_messages = merge_messages[:1]
        if len(merge_messages) != 1:
            raise RuntimeError(f"expected one merge message, got {len(merge_messages)}")
        print("merge_message", merge_messages[0].data)
        merger_send = queue.Queue()
        merger = Merger((merger_send, queue.Queue()))
        merger.add_task(merge_messages[0])
        merged = merger_send.get(timeout=180)
        handler.onMergeEnd(merged)
        outputs = merged.data["outputs"]
        output = Path(outputs[0].path)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "9", "-i", str(output), "-frames:v", "1", str(delivery / "切换前_1920x1080.png")], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "10.5", "-i", str(output), "-frames:v", "1", str(delivery / "切换后_1920x1080.png")], check=True)
        (delivery / "说明.txt").write_text(
            "主方向横屏：problem_resolution 10秒；少数方向竖屏：B站中段90秒处3秒。\n"
            "通过LiveEvents策略→现有Render队列→onRenderEnd替换→现有Merger合并。\n"
            "最终输出：一个1920x1080视频；竖屏段按横屏主画布居中补黑边。\n"
            f"最终文件：{output}\n", encoding="utf8"
        )
        print(json.dumps({"decision": decision, "output": str(output), "resolution": outputs[0].resolution, "duration": outputs[0].duration}, ensure_ascii=False))
        return 0
    finally:
        render_plugin.render_executors.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
