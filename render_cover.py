import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from DMR.utils.render_with_manimgl import rendercover_with_manimgl

# ── 在这里修改参数 ────────────────────────────────────────────
NAME       = "不可一世杀手"
TIME       = "5月31日"
COLOR      = "#ffad33"
YEAR       = "2026"
image_path = "im.png"
FILENAME   = "cover.png"   # 输出文件名
# ─────────────────────────────────────────────────────────────

output_dir = Path(__file__).parent

rendercover_with_manimgl(
    name            = NAME,
    time            = TIME,
    color           = COLOR,
    year            = YEAR,
    output_dir      = str(output_dir),
    output_filename = FILENAME,
    image_path      = image_path if image_path else None
)

print(f"封面已保存到 {output_dir / FILENAME}")
