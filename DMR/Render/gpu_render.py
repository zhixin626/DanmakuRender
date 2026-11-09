
from __future__ import annotations
import argparse, json, os, pathlib, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "perf" / "tmp" / "venv312" / "Lib" / "site-packages"
NVC_PKG = PKG / "PyNvVideoCodec"
for dll in (NVC_PKG, PKG / "nvidia" / "cuda_nvrtc" / "bin", PKG / "nvidia" / "cuda_runtime" / "bin"):
    if dll.exists():
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(dll))
        os.environ["PATH"] = str(dll) + ";" + os.environ.get("PATH", "")

import cupy as cp
import PyNvVideoCodec as nvc

KERNEL = r"""
extern "C" __global__
void blend_pressure(unsigned char* y, int width, int height, int sprites) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int yy = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= width || yy >= height) return;
    int v = y[yy * width + x];
    for (int s = 0; s < sprites; ++s) {
        int x0 = (s * 37) % max(1, width - 300);
        int y0 = (s * 17) % max(1, height - 60);
        if (x >= x0 && x < x0 + 300 && yy >= y0 && yy < y0 + 60) {
            v = min(255, v + 1);
        }
    }
    y[yy * width + x] = (unsigned char)v;
}
"""

class CaiView:
    def __init__(self, source, shape):
        value = dict(source.__cuda_array_interface__)
        value["shape"] = tuple(shape)
        value["strides"] = tuple(value["strides"])
        self.__cuda_array_interface__ = value

def run(video: pathlib.Path, sprites: int, max_frames: int, probe_encoder: bool) -> dict:
    cp.cuda.Device(0).use()
    decoder = nvc.SimpleDecoder(
        str(video), gpu_id=0, use_device_memory=True,
        need_scanned_stream_metadata=False,
    )
    first = decoder.get_batch_frames(1)
    if not first:
        raise RuntimeError("NVDEC returned no frame")
    frame = first[0]
    planes = frame.cuda()
    width = int(planes[0].shape[0])
    height = int(planes[0].shape[1])
    cp.cuda.Device(0).use()
    kernel = cp.RawKernel(KERNEL, "blend_pressure")
    # Reuse the first frame and then sequential batches.
    pending = first
    frames = 0
    started = time.perf_counter()
    kernel_started = time.perf_counter()
    while frames < max_frames:
        batch = pending if pending else decoder.get_batch_frames(16)
        pending = []
        if not batch:
            break
        for decoded in batch:
            cp.cuda.Device(0).use()
            y = cp.asarray(CaiView(decoded.cuda()[0], (height, width, 1)), copy=False)
            kernel(
                ((width + 31) // 32, (height + 7) // 8),
                (32, 8),
                (y, width, height, int(sprites)),
            )
            frames += 1
            if frames >= max_frames:
                break
    cp.cuda.Stream.null.synchronize()
    kernel_wall = time.perf_counter() - kernel_started
    wall = time.perf_counter() - started
    result = {
        "video": str(video),
        "sprites": sprites,
        "width": width,
        "height": height,
        "frames": frames,
        "wall_s": wall,
        "fps": frames / wall if wall else 0.0,
        "kernel_wall_s": kernel_wall,
        "kernel_fps": frames / kernel_wall if kernel_wall else 0.0,
        "encoder_probe": None,
    }
    if probe_encoder:
        try:
            encoder = nvc.CreateEncoder(
                width, height, "NV12", False,
                codec="hevc", bitrate=4000000, framerate=25, preset="P4",
            )
            cp.cuda.Device(0).use()
            # The wheel advertises CUDA Array Interface input; retain the
            # failure text if the installed Windows binary rejects it.
            yuv = cp.zeros((height * 3 // 2, width), dtype=cp.uint8)
            result["encoder_probe"] = {
                "ok": bool(encoder.Encode(yuv)),
                "error": None,
            }
        except Exception as exc:
            result["encoder_probe"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return result

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=pathlib.Path, required=True)
    ap.add_argument("--sprites", type=int, required=True)
    ap.add_argument("--max-frames", type=int, default=7500)
    ap.add_argument("--probe-encoder", action="store_true")
    ap.add_argument("--output", type=pathlib.Path, required=True)
    args = ap.parse_args()
    result = run(args.video, args.sprites, args.max_frames, args.probe_encoder)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

