import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from DMR.utils.render_with_manimgl import rendercover_with_manimgl

# ── 在这里修改参数 ────────────────────────────────────────────
NAME       = "少年不太冷"
TIME       = "5月4日~月末"
COLOR      = "#58C4DD"
YEAR       = "2026"
FILENAME   = "cover.png"   # 输出文件名
# ─────────────────────────────────────────────────────────────

output_dir = Path(__file__).parent / "covers"
output_dir.mkdir(exist_ok=True)

rendercover_with_manimgl(
    name            = NAME,
    time            = TIME,
    color           = COLOR,
    year            = YEAR,
    output_dir      = str(output_dir),
    output_filename = FILENAME,
)

print(f"封面已保存到 {output_dir / FILENAME}")
