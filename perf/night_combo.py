"""Render a short GPU gift-combo demonstration from the isolated Bili material."""

from pathlib import Path
import json
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
    output_dir = ROOT / "演示视频/2026-11-11_11_礼物连击"
    output_dir.mkdir(parents=True, exist_ok=True)
    work = ROOT / "perf/tmp/night/combo"
    work.mkdir(parents=True, exist_ok=True)
    config = Config(str(material / "配置/global.yml"))
    config.config_dir = str(material / "配置")
    options = config.get_render_args("峰哥")
    options["slide_right"]["gift"]["combo_mode"] = "live"
    options["slide_right"]["gift"]["combo_window_seconds"] = 10
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
            max_frames=1500, fps=50,
        )
        if not ok:
            raise RuntimeError(str(info))
    finally:
        compiled.cleanup()
    (output_dir / "说明.txt").write_text(
        "来源：B站峰哥中段 augmented；100秒处补充同用户同礼物 combo_id 连击、交替用户和超过10秒边界事件。\n"
        "01视频为GPU live模式，观察右侧礼物面板数量从合并后的累计值显示；测试细节见 tests/test_gift_combo.py。\n",
        encoding="utf8",
    )
    print(json.dumps({"output": str(output), "duration": info.duration}, ensure_ascii=False))
