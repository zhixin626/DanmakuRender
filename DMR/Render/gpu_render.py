
from __future__ import annotations
import argparse, json, itertools, os, pathlib, subprocess, time
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

class NV12Buffer:
    """A persistent, contiguous NV12 frame owned by us (pitch == width).

    Each decoded frame is copied into this buffer (device-to-device, ~3 MB,
    negligible), composited here, then handed to NVENC through ``cuda()``.
    Reason: passing decoder surfaces straight to Encode() produced a video in
    which every frame was identical (1b-0) -- the encoder kept reading the
    first surface it saw. Using one fixed input buffer is the standard NVENC
    pattern and also decouples us from the decoder's surface pool, which the
    pipelined version (1b-1) needs anyway.
    """
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.buf = cp.empty((height * 3 // 2, width), dtype=cp.uint8)
        self.y = self.buf[:height].reshape(height, width, 1)
        self.uv = self.buf[height:].reshape(height // 2, width // 2, 2)
    def load(self, decoded):
        planes = decoded.cuda()
        # PyNvVideoCodec exposes CAI metadata with list-valued shape/strides;
        # CuPy expects tuples when importing the device view.
        def as_cupy(view):
            cai = view.__cuda_array_interface__
            shape = tuple(cai["shape"])
            strides = tuple(cai["strides"]) if cai.get("strides") else None
            return cp.asarray(CaiView(view, shape, strides), copy=False)
        src_y = as_cupy(planes[0])
        src_uv = as_cupy(planes[1])
        self.y[...] = src_y.reshape(self.y.shape)
        self.uv[...] = src_uv.reshape(self.uv.shape)
    def cuda(self):
        return [self.y, self.uv]

def frame_signature(arr):
    """Cheap per-frame checksum of the luma plane, used to prove frames change."""
    h = arr.shape[0]
    return int(arr[h // 4: h // 4 + 64, :64].sum())

def low_level_frames(video):
    """Decode packets through the native demuxer/decoder API.

    SimpleDecoder on this FLV repeats one surface after about 55 frames and
    reports a stuck PTS. The lower-level packet API keeps advancing through
    the stream and returns changing frames.
    """
    demuxer = nvc.CreateDemuxer(filename=str(video))
    decoder = nvc.CreateDecoder(gpuid=0, codec=demuxer.GetNvCodecId(), usedevicememory=True)
    for packet in demuxer:
        for decoded in decoder.Decode(packet):
            yield decoded

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
    decoded_iter = low_level_frames(video)
    try:
        first = next(decoded_iter)
    except StopIteration:
        raise RuntimeError("NVDEC returned no frame")
    width, height, pitch = plane_geometry(first)
    kernel = kernel_for(width, height)
    encoder = nvc.CreateEncoder(width, height, "NV12", False, codec="hevc", bitrate=4000000, framerate=25, preset="P4")
    bitstream = output.with_suffix(".hevc")
    bitstream.parent.mkdir(parents=True, exist_ok=True)
    frame = NV12Buffer(width, height)
    flat_y = frame.buf[:height].reshape(-1)
    signatures = []
    frames = 0; started = time.perf_counter()
    with bitstream.open("wb") as fp:
        for decoded in itertools.chain((first,), decoded_iter):
            frame.load(decoded)
            if frames < 200 and frames % 10 == 0:
                signatures.append(frame_signature(frame.y))
            kernel(((width + 31) // 32, (height + 7) // 8), (32, 8),
                   (flat_y, cp.int32(width), cp.int32(height), cp.int32(width), cp.int32(sprites)))
            for packet in encoder.Encode(frame):
                fp.write(packet["data"])
            frames += 1
            if frames >= max_frames: break
        for packet in encoder.EndEncode():
            fp.write(packet["data"])
    wall = time.perf_counter() - started
    mux = ["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "hevc", "-r", "25", "-i", str(bitstream),
           "-i", str(video), "-map", "0:v:0", "-map", "1:a", "-c:v", "copy", "-c:a", "copy",
           str(output)]
    mux_run = subprocess.run(mux, capture_output=True, text=True)
    if mux_run.returncode:
        raise RuntimeError(mux_run.stderr[-1000:])
    return {"video": str(video), "output": str(output), "sprites": sprites, "width": width, "height": height,
            "frames": frames, "wall_s": wall, "fps": frames / wall if wall else 0.0, "bitstream": str(bitstream),
            # Decoded-frame luma checksums every 10 frames: must NOT all be equal.
            "decoded_signatures": signatures,
            "decoded_frames_change": len(set(signatures)) > 1}

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

