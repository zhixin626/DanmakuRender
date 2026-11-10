"""Build test atlases through the production sprite implementation."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from DMR.Render.sprites import build_atlas

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", default="bilibili")
    parser.add_argument("--emoticon-dir", help="defaults to emoji_pack/<platform>/emoticon")
    args = parser.parse_args()
    print(build_atlas(args.plan, args.output, args.platform, args.emoticon_dir))
