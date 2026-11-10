"""Invoke the production GPU renderer with the active environment."""
import argparse
import json
import logging
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from DMR.Render.gpu_render import load_cuda_modules, render_one

def configure_cuda_dlls():
    load_cuda_modules()


def main() -> None:
    """Run the standalone prototype launcher used by perf tests."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--vencoder", default="hevc_nvenc")
    parser.add_argument("--bitrate", type=int)
    parser.add_argument("--venc-args", default="[]")
    parser.add_argument("--canvas-resolution")
    parser.add_argument("--rescale-to-1080p", action="store_true")
    parser.add_argument("--rc", default=None)
    parser.add_argument("--maxbitrate", default=None)
    parser.add_argument("--vbvbufsize", default=None)
    parser.add_argument("--preset")
    parser.add_argument("--fps", type=float)
    parser.add_argument("--min-fps", type=float)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument("--render-plan", type=pathlib.Path)
    args = parser.parse_args()
    result = render_one(
        args.video,
        str(args.output),
        vencoder=args.vencoder,
        vencoder_args=json.loads(args.venc_args) + [value for flag, item in (
            ("-b:v", args.bitrate), ("-maxrate", args.maxbitrate), ("-bufsize", args.vbvbufsize),
            ("-rc", args.rc), ("-preset", args.preset)) if item is not None for value in (flag, str(item))],
        canvas_resolution=tuple(map(int, args.canvas_resolution.split("x"))) if args.canvas_resolution else None,
        rescale_to_1080p=args.rescale_to_1080p,
        fps=args.fps,
        min_fps=args.min_fps,
        max_frames=args.max_frames,
        gpu_id=args.gpu_id,
        render_plan=args.render_plan,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
