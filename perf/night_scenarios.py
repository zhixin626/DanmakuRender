"""Render the twelve gift-combo coverage scenarios through E and G."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from perf.round_regression import render_coverage


if __name__ == "__main__":
    material = ROOT / "演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段"
    delivery = ROOT / "演示视频/2026-11-11_14_连击场景"
    delivery.mkdir(parents=True, exist_ok=True)
    raise SystemExit(
        render_coverage(
            delivery=delivery,
            video_path=material / "7月30日20点23分_中段.flv",
            events_path=material / "7月30日20点23分_中段_augmented.jsonl",
            names=[
                ("01_场景覆盖_旧版E", "emoji_dmrender"),
                ("02_场景覆盖_GPU_G", "gpu_render"),
            ],
            config_name="12类连击场景配置",
        )
    )
