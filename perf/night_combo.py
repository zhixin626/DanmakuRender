"""Render a short GPU gift-combo demonstration from the isolated Bili material."""

from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from DMR.Config import Config
from DMR.Render import get_engine
from DMR.Render.layout_compiler import LayoutCompiler
from DMR.utils import FFprobe, VideoInfo
from perf.dmr_gate import decide


if __name__ == "__main__":
    gate = decide(10)
    if not gate["go"]:
        print(json.dumps(gate, ensure_ascii=False))
        raise SystemExit(10)
    material = ROOT / "演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段"
    source = material / "7月30日20点23分_中段.flv"
    events = material / "7月30日20点23分_中段_augmented.jsonl"
    output_dir = ROOT / "演示视频/2026-11-11_13_连击数字"
    output_dir.mkdir(parents=True, exist_ok=True)
    work = ROOT / "perf/tmp/night/combo"
    work.mkdir(parents=True, exist_ok=True)
    clip = work / "combo_clip.flv"
    clip_events = work / "combo_clip.jsonl"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", "90", "-i", str(source),
         "-t", "40", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-avoid_negative_ts", "make_zero", str(clip)],
        check=True,
    )
    rows = [json.loads(line) for line in events.read_text(encoding="utf8").splitlines() if line.strip()]
    header = dict(rows[0])
    header["segment_start_time"] = float(header["segment_start_time"]) + 90
    end_time = header["segment_start_time"] + 40
    selected = [header] + [
        row for row in rows[1:]
        if header["segment_start_time"] <= float(row.get("received_at", 0)) < end_time
    ]
    clip_events.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in selected) + "\n",
        encoding="utf8",
    )
    source = clip
    events = clip_events
    config = Config(str(material / "配置/global.yml"))
    config.config_dir = str(material / "配置")
    options = config.get_render_args("峰哥")
    options["slide_right"]["gift"]["combo_window_seconds"] = 5
    video = VideoInfo(
        path=str(source), dm_file_id=str(events),
        duration=FFprobe.get_timeline_duration(str(source)),
        resolution=FFprobe.get_resolution(str(source)),
    )
    compiled = LayoutCompiler(options, engine="gpu_render", engine_config=options).compile(
        video=video, events_path=str(events), work_dir=str(work)
    )
    plan = output_dir / "01_礼物连击_GPU_G_plan.json"
    plan.write_bytes(Path(compiled.render_plan_path).read_bytes())
    renderer = get_engine("gpu_render").load()(**options)
    output = output_dir / "01_礼物连击_GPU_G.mp4"
    try:
        ok, info = renderer.render_one(
            video, str(output), render_plan=str(plan),
            canvas_resolution=compiled.next_state.canvas_resolution,
            max_frames=2000, fps=50,
        )
        if not ok:
            raise RuntimeError(str(info))
    finally:
        compiled.cleanup()
    (output_dir / "说明.txt").write_text(
        "来源：B站峰哥中段 90–130 秒裁剪片段；100秒处补充同用户同礼物5秒滑动窗口连击、交替用户和窗口边界事件。\n"
        "01视频为GPU连续状态事件，观察右侧礼物数字 ×1→×2→×3；测试细节见 tests/test_gift_combo.py。\n",
        encoding="utf8",
    )
    print(json.dumps({"output": str(output), "duration": info.duration}, ensure_ascii=False))
