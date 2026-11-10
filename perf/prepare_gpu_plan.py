"""Compatibility wrapper; production atlas implementation lives in DMR/Render/gpu_atlas.py."""
from __future__ import annotations
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    runpy.run_path(str(ROOT / "DMR" / "Render" / "gpu_atlas.py"), run_name="__main__")
