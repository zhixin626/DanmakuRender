
from __future__ import annotations
import argparse, json, os, pathlib, subprocess, time
ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "perf" / "tmp" / "venv312" / "Lib" / "site-packages"
NVC_PKG = PKG / "PyNvVideoCodec"
for dll in (NVC_PKG, PKG / "nvidia" / "cuda_nvrtc" / "bin", PKG / "nvidia" / "cuda_runtime" / "bin"):
    if dll.exists():
        if hasattr(os, "add_dll_directory"): os.add_dll_directory(str(dll))
        os.environ["PATH"] = str(dll) + ";" + os.environ.get("PATH", "")
import cupy as cp
import PyNvVideoCodec as nvc

KERNEL = r"""
extern "C" __global__
void blend_pressure(unsigned char* y, int width, int height, int pitch, int sprites) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int yy = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= width || yy >= height) return;
    int v = y[yy * pitch + x];
    for (int s = 0; s < sprites; ++s) {
        int x0 = (s * 37) % max(1, width - 300);
        int y0 = (s * 17) % max(1, height - 60);
        if (x >= x0 && x < x0 + 300 && yy >= y0 && yy < y0 + 60) v = min(255, v + 1);
    }
    y[yy * pitch + x] = (unsigned char)v;
}
"""

class CaiView:
    def __init__(self, source, shape, strides):
        value = dict(source.__cuda_array_interface__)
        value["shape"] = tuple(shape)
        value["strides"] = tuple(strides)
        self.__cuda_array_interface__ = value

def plane_geometry(decoded):
    """Return (width, height, pitch) of the NV12 Y plane.

    The decoder's CAI shape is (rows, cols, ...), i.e. (height, width), and the
    row stride is the NVDEC surface pitch, which is usually larger than width.
    Never assume pitch == width: doing so shears the picture diagonally.
    """
    cai = decoded.cuda()[0].__cuda_array_interface__
    height, width = int(cai["shape"][0]), int(cai["shape"][1])
    strides = cai.get("strides")
    pitch = int(strides[0]) if strides else width
    return width, height, pitch

def kernel_for(width, height):
    cp.cuda.Device(0).use()
    return cp.RawKernel(KERNEL, "blend_pressure")

def apply_kernel(decoded, width, height, pitch, sprites, kernel):
    cp.cuda.Device(0).use()
    plane = decoded.cuda()[0]
    # View the Y plane as a flat byte buffer of pitch*height; the kernel
    # indexes rows by pitch, so padding bytes are never touched.
    y = cp.asarray(CaiView(plane, (height * pitch,), (1,)), copy=False)
    kernel(((width + 31) // 32, (height + 7) // 8), (32, 8),
           (y, cp.int32(width), cp.int32(height), cp.int32(pitch), cp.int32(sprites)))
    # The decoded frame is modified in place and passed to NVENC as-is,
    # so its native shape/pitch metadata stays consistent.
    return decoded

def run_probe(video, sprites, max_frames, probe_encoder):
    cp.cuda.Device(0).use()
    decoder = nvc.SimpleDecoder(str(video), gpu_id=0, use_device_memory=True, need_scanned_stream_metadata=False)
    first = decoder.get_batch_frames(1)
    if not first: raise RuntimeError("NVDEC returned no frame")
    width, height, pitch = plane_geometry(first[0])
    kernel = kernel_for(width, height)
    pending = first; frames = 0; started = time.perf_counter()
    while frames < max_frames:
        batch = pending if pending else decoder.get_batch_frames(16); pending = []
        if not batch: break
        for decoded in batch:
            apply_kernel(decoded, width, height, pitch, sprites, kernel)
            frames += 1
            if frames >= max_frames: break
    cp.cuda.Stream.null.synchronize()
    wall = time.perf_counter() - started
    result = {"video": str(video), "sprites": sprites, "width": width, "height": height,
              "frames": frames, "wall_s": wall, "fps": frames / wall if wall else 0.0,
              "encoder_probe": None}
    if probe_encoder:
        try:
            encoder = nvc.CreateEncoder(width, height, "NV12", False, codec="hevc", bitrate=4000000, framerate=25, preset="P4")
            result["encoder_probe"] = {"ok": bool(encoder.Encode(first[0])), "error": None}
        except Exception as exc:
            result["encoder_probe"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return result

def run_full(video, sprites, max_frames, output):
    cp.cuda.Device(0).use()
    decoder = nvc.SimpleDecoder(str(video), gpu_id=0, use_device_memory=True, need_scanned_stream_metadata=False)
    first = decoder.get_batch_frames(1)
    if not first: raise RuntimeError("NVDEC returned no frame")
    width, height, pitch = plane_geometry(first[0])
    kernel = kernel_for(width, height)
    encoder = nvc.CreateEncoder(width, height, "NV12", False, codec="hevc", bitrate=4000000, framerate=25, preset="P4")
    bitstream = output.with_suffix(".hevc")
    bitstream.parent.mkdir(parents=True, exist_ok=True)
    pending = first; frames = 0; started = time.perf_counter()
    with bitstream.open("wb") as fp:
        while frames < max_frames:
            batch = pending if pending else decoder.get_batch_frames(16); pending = []
            if not batch: break
            for decoded in batch:
                frame = apply_kernel(decoded, width, height, pitch, sprites, kernel)
                for packet in encoder.Encode(frame):
                    fp.write(packet["data"])
                frames += 1
                if frames >= max_frames: break
        for packet in encoder.EndEncode():
            fp.write(packet["data"])
    wall = time.perf_counter() - started
    mux = ["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "hevc", "-r", "25", "-i", str(bitstream),
           "-i", str(video), "-map", "0:v:0", "-map", "1:a", "-c:v", "copy", "-c:a", "copy", "-shortest", str(output)]
    mux_run = subprocess.run(mux, capture_output=True, text=True)
    if mux_run.returncode:
        raise RuntimeError(mux_run.stderr[-1000:])
    return {"video": str(video), "output": str(output), "sprites": sprites, "width": width, "height": height,
            "frames": frames, "wall_s": wall, "fps": frames / wall if wall else 0.0, "bitstream": str(bitstream)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=pathlib.Path, required=True)
    ap.add_argument("--sprites", type=int, required=True)
    ap.add_argument("--max-frames", type=int, default=7500)
    ap.add_argument("--probe-encoder", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--output", type=pathlib.Path, required=True)
    a = ap.parse_args()
    result = run_full(a.video, a.sprites, a.max_frames, a.output) if a.full else run_probe(a.video, a.sprites, a.max_frames, a.probe_encoder)
    if not a.full:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
if __name__ == "__main__":
    main()

