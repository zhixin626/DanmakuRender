from __future__ import annotations

import argparse
import ctypes
import json
import os
import pathlib
import subprocess
import time

import numpy as np

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


VIDEO = ROOT / "perf" / "samples" / "normal_dense" / "normal_dense_peak_7月30日20点23分" / "7月30日20点23分_clip.flv"
MAX_FRAMES = 1000


class CaiView:
    def __init__(self, source, shape, strides):
        value = dict(source.__cuda_array_interface__)
        value["shape"] = tuple(shape)
        value["strides"] = tuple(strides) if strides else None
        self.__cuda_array_interface__ = value


def frame_shape(frame):
    cai = frame.cuda()[0].__cuda_array_interface__
    shape = tuple(int(x) for x in cai["shape"])
    strides = tuple(int(x) for x in cai["strides"])
    return shape[0], shape[1], strides[0]


def checksum(frame, use_device_memory):
    height, width, pitch = frame_shape(frame)
    if use_device_memory:
        plane = frame.cuda()[0]
        view = CaiView(plane, (height, width), (pitch, 1))
        arr = cp.asarray(view, copy=False)
        value = int(cp.sum(arr, dtype=cp.uint64).get())
    else:
        ptr = int(frame.GetPtrToPlane(0))
        raw = ctypes.string_at(ptr, height * pitch)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, pitch)[:, :width]
        value = int(arr.astype(np.uint64, copy=False).sum())
    return value


def frame_info(frame):
    result = {}
    for name in ("timestamp", "getPTS"):
        try:
            value = getattr(frame, name)
            result[name] = int(value() if callable(value) else value)
        except Exception as exc:
            result[name] = f"{type(exc).__name__}: {exc}"
    return result


def summarize(name, checksums, pts, elapsed, error=None):
    changes = [i for i in range(1, len(checksums)) if checksums[i] != checksums[i - 1]]
    unique = len(set(checksums))
    return {
        "variant": name,
        "frames": len(checksums),
        "unique_checksums": unique,
        "last_change_frame": changes[-1] if changes else None,
        "changes": len(changes),
        "first_checksums": checksums[:8],
        "last_checksums": checksums[-8:],
        "first_pts": pts[:8],
        "last_pts": pts[-8:],
        "elapsed_s": elapsed,
        "error": error,
    }


def run_simple(name, *, batch_size, synchronize, need_scanned_stream_metadata, use_device_memory):
    checksums, pts = [], []
    started = time.perf_counter()
    try:
        decoder = nvc.SimpleDecoder(
            str(VIDEO),
            gpu_id=0,
            use_device_memory=use_device_memory,
            need_scanned_stream_metadata=need_scanned_stream_metadata,
        )
        while len(checksums) < MAX_FRAMES:
            batch = decoder.get_batch_frames(batch_size)
            if not batch:
                break
            if synchronize:
                cp.cuda.runtime.deviceSynchronize()
            for frame in batch:
                checksums.append(checksum(frame, use_device_memory))
                pts.append(frame_info(frame))
                if len(checksums) >= MAX_FRAMES:
                    break
        return summarize(name, checksums, pts, time.perf_counter() - started)
    except Exception as exc:
        return summarize(name, checksums, pts, time.perf_counter() - started, f"{type(exc).__name__}: {exc}")


def run_low_level():
    checksums, pts = [], []
    started = time.perf_counter()
    try:
        demuxer = nvc.CreateDemuxer(filename=str(VIDEO))
        decoder = nvc.CreateDecoder(
            gpuid=0,
            codec=demuxer.GetNvCodecId(),
            usedevicememory=True,
        )
        for packet in demuxer:
            for frame in decoder.Decode(packet):
                checksums.append(checksum(frame, True))
                pts.append(frame_info(frame))
                if len(checksums) >= MAX_FRAMES:
                    break
            if len(checksums) >= MAX_FRAMES:
                break
        return summarize("low_level_demux_decoder", checksums, pts, time.perf_counter() - started)
    except Exception as exc:
        return summarize("low_level_demux_decoder", checksums, pts, time.perf_counter() - started, f"{type(exc).__name__}: {exc}")


def ffmpeg_checksums():
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
        "-of", "json", str(VIDEO),
    ], text=True))
    stream = probe["streams"][0]
    width, height = int(stream["width"]), int(stream["height"])
    frame_size = width * height
    proc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-i", str(VIDEO), "-frames:v", str(MAX_FRAMES),
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ], stdout=subprocess.PIPE)
    raw, _ = proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")
    checksums = [int(np.frombuffer(raw[i:i + frame_size], dtype=np.uint8).astype(np.uint64).sum())
                 for i in range(0, len(raw) - frame_size + 1, frame_size)]
    return summarize("ffmpeg_reference", checksums, [], 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=pathlib.Path, default=ROOT / "perf" / "tmp" / "diag_decoder.json")
    args = ap.parse_args()
    results = [
        run_simple("simple_batch16", batch_size=16, synchronize=False, need_scanned_stream_metadata=False, use_device_memory=True),
        run_simple("simple_batch1", batch_size=1, synchronize=False, need_scanned_stream_metadata=False, use_device_memory=True),
        run_simple("simple_batch16_sync", batch_size=16, synchronize=True, need_scanned_stream_metadata=False, use_device_memory=True),
        run_simple("simple_scanned_metadata", batch_size=16, synchronize=False, need_scanned_stream_metadata=True, use_device_memory=True),
        run_simple("simple_host_memory", batch_size=16, synchronize=False, need_scanned_stream_metadata=False, use_device_memory=False),
        run_low_level(),
        ffmpeg_checksums(),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"video": str(VIDEO), "max_frames": MAX_FRAMES, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
