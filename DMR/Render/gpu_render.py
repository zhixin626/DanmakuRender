"""GPU render engine: frame scheduling, canvas composition and encoding."""

from __future__ import annotations

import json
import logging
import math
import os
import sysconfig
import pathlib
import subprocess
import tempfile
import time
import threading
import warnings
from datetime import datetime
from DMR.utils.ffprobe import FFprobe
from dataclasses import dataclass
from typing import Any, Iterator
from fractions import Fraction

from .layout_models import RenderEvent
from .resolution import resolve_output_fps, resolve_render_canvas, fit_canvas
from ..utils.ffprobe import source_frame_rate
from .baserender import BaseRender, RenderStopped
from .sprites import build_atlas

logger = logging.getLogger(__name__)
_dll_handles = []


PLAN_KERNEL = r"""
extern "C" __global__
void blend_plan(unsigned char* y, unsigned char* uv, int width, int height,
                const int* px, const int* py, const int* offsets,
const int* widths, const int* heights, const int* event_alpha, int count,
                const unsigned char* sy, const unsigned char* su,
                const unsigned char* sv, const unsigned char* sa) {
    int ux = blockIdx.x * blockDim.x + threadIdx.x;
    int uy = blockIdx.y * blockDim.y + threadIdx.y;
    if (ux >= width / 2 || uy >= height / 2) return;
    int x0 = ux * 2;
    int y0 = uy * 2;
    for (int dy = 0; dy < 2; ++dy) {
        for (int dx = 0; dx < 2; ++dx) {
            int x = x0 + dx;
            int yy = y0 + dy;
            int value = y[yy * width + x];
            for (int event = 0; event < count; ++event) {
                int sx = x - px[event];
                int sy_local = yy - py[event];
                if (sx < 0 || sy_local < 0 || sx >= widths[event] || sy_local >= heights[event]) continue;
                int index = offsets[event] + sy_local * widths[event] + sx;
                int alpha = (sa[index] * event_alpha[event] + 127) / 255;
                value = (value * (255 - alpha) + sy[index] * alpha + 127) / 255;
            }
            y[yy * width + x] = (unsigned char)value;
        }
    }
    int uvalue = uv[uy * width + ux * 2];
    int vvalue = uv[uy * width + ux * 2 + 1];
    for (int event = 0; event < count; ++event) {
        int alpha_sum = 0;
        int u_sum = 0;
        int v_sum = 0;
        for (int dy = 0; dy < 2; ++dy) {
            for (int dx = 0; dx < 2; ++dx) {
                int sx = x0 + dx - px[event];
                int sy_local = y0 + dy - py[event];
                if (sx < 0 || sy_local < 0 || sx >= widths[event] || sy_local >= heights[event]) continue;
                int index = offsets[event] + sy_local * widths[event] + sx;
                int alpha = (sa[index] * event_alpha[event] + 127) / 255;
                alpha_sum += alpha;
                u_sum += su[index] * alpha;
                v_sum += sv[index] * alpha;
            }
        }
        if (alpha_sum > 0) {
            int alpha = (alpha_sum + 2) / 4;
            int u = u_sum / alpha_sum;
            int v = v_sum / alpha_sum;
            uvalue = (uvalue * (255 - alpha) + u * alpha + 127) / 255;
            vvalue = (vvalue * (255 - alpha) + v * alpha + 127) / 255;
        }
    }
    uv[uy * width + ux * 2] = (unsigned char)uvalue;
    uv[uy * width + ux * 2 + 1] = (unsigned char)vvalue;
}
"""


SCALE_KERNEL = r"""
extern "C" __global__
void scale_nv12(const unsigned char* sy, const unsigned char* suv, unsigned char* dst,
    int cw, int ch, int fw, int fh, int ox, int oy, int sw, int sh,
    int sy_row, int sy_col, int uv_row, int uv_col, int uv_channel,
    const int* yx, const float* ywx, const int* yy, const float* ywy,
    const int* ux, const float* uwx, const int* uy, const float* uwy) {
    int index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= cw * ch * 3 / 2) return;
    bool luma = index < cw * ch;
    int local = luma ? index : index - cw * ch;
    int out_x = local % cw;
    int x = luma ? out_x - ox : out_x / 2 - ox / 2;
    int y = local / cw - (luma ? oy : oy / 2);
    int width = luma ? fw : fw / 2;
    int height = luma ? fh : fh / 2;
    if (x < 0 || y < 0 || x >= width || y >= height) {
        dst[index] = luma ? 16 : 128;
        return;
    }
    int x0 = luma ? yx[x] : ux[x];
    int y0 = luma ? yy[y] : uy[y];
    int x1 = min(x0 + 1, (luma ? sw : sw / 2) - 1);
    int y1 = min(y0 + 1, (luma ? sh : sh / 2) - 1);
    float wx = luma ? ywx[x] : uwx[x];
    float wy = luma ? ywy[y] : uwy[y];
    const unsigned char* src = luma ? sy : suv;
    int row = luma ? sy_row : uv_row;
    int col = luma ? sy_col : uv_col;
    int channel = luma ? 0 : (out_x % 2) * uv_channel;
    float top = src[y0 * row + x0 * col + channel] * (1 - wx)
              + src[y0 * row + x1 * col + channel] * wx;
    float bottom = src[y1 * row + x0 * col + channel] * (1 - wx)
                 + src[y1 * row + x1 * col + channel] * wx;
    dst[index] = (unsigned char)(top * (1 - wy) + bottom * wy + 0.5f);
}
"""

# Phases in the half-resolution chroma grid, relative to luma pixel centres.
# The encoder emits ordinary MPEG/H.26x left-sited NV12 (x=.25, y=.5).
CHROMA_PHASES = {
    "left": (0.25, 0.5),
    "center": (0.5, 0.5),
    "topleft": (0.25, 0.25),
    "top": (0.5, 0.25),
    "bottomleft": (0.25, 0.75),
    "bottom": (0.5, 0.75),
}


def sample_axis(
    cupy: Any, source: int, target: int, source_phase=0.5, target_phase=0.5
):
    """Return clamped lower indices and bilinear weights for pixel centres."""
    positions = (cupy.arange(target, dtype=cupy.float32) + target_phase) * (
        source / target
    ) - source_phase
    positions = cupy.minimum(cupy.maximum(positions, 0), source - 1)
    lower = positions.astype(cupy.int32)
    return lower, (positions - lower).astype(cupy.float32)


@dataclass(frozen=True)
class RenderConfig:
    """Runtime values supplied by the render engine or the test launcher."""

    video_encoder: str
    encoder_options: dict[str, str]
    fps: float | None
    minimum_fps: float | None
    max_frames: int | None
    gpu_id: int
    atlas: dict[str, Any]


class CaiView:
    """Normalize PyNvVideoCodec CAI metadata for CuPy."""

    def __init__(self, source: Any, shape: tuple[int, ...], strides: tuple[int, ...]):
        value = dict(source.__cuda_array_interface__)
        value["shape"] = tuple(shape)
        value["strides"] = tuple(strides)
        self.__cuda_array_interface__ = value


def load_cuda_modules() -> tuple[Any, Any]:
    """Load optional CUDA wheels from the active interpreter, never perf/."""
    if os.name == "nt" and not _dll_handles:
        packages = pathlib.Path(sysconfig.get_paths()["purelib"])
        cuda = packages / "nvidia" / "cuda_nvrtc"
        if cuda.exists():
            os.environ.setdefault("CUDA_PATH", str(cuda))
        for directory in (
            packages / "PyNvVideoCodec",
            cuda / "bin",
            packages / "nvidia" / "cuda_runtime" / "bin",
        ):
            if directory.exists():
                _dll_handles.append(os.add_dll_directory(str(directory)))
    # CuPy 14's pathfinder deliberately returns no common root for split
    # CUDA 12 wheels, even with CUDA_PATH set. The DLLs above are configured;
    # suppress only that discovery warning, never CUDA load/runtime errors.
    with warnings.catch_warnings():
        if os.environ.get("CUDA_PATH") and _dll_handles:
            warnings.filterwarnings(
                "ignore",
                message=r"CUDA path could not be detected\..*",
                category=UserWarning,
            )
        import cupy
    # Sequential decoding does not use FLV seeking. Keep SDK errors visible,
    # and honor an explicit user LOGGER_LEVEL when diagnosing the codec.
    os.environ.setdefault("LOGGER_LEVEL", "ERROR")
    import PyNvVideoCodec

    return cupy, PyNvVideoCodec


def codec_name(video_encoder: str) -> str:
    """Convert an FFmpeg NVENC name into PyNvVideoCodec's codec name."""
    return video_encoder.removesuffix("_nvenc")


def parse_bitrate(value: Any) -> int:
    """Parse FFmpeg bitrate values such as 4M, 4000K, or raw bits per second."""
    text = str(value).strip().upper()
    multiplier = 1
    if text.endswith("K"):
        multiplier = 1_000
        text = text[:-1]
    elif text.endswith("M"):
        multiplier = 1_000_000
        text = text[:-1]
    result = int(float(text) * multiplier)
    if result <= 0:
        raise ValueError("bitrate must be positive")
    return result


def parse_encoder_args(args: list[str]) -> dict[str, str]:
    """Read configured FFmpeg options; absent options stay absent."""
    names = {
        "-b:v": "bitrate",
        "-maxrate": "maxbitrate",
        "-bufsize": "vbvbufsize",
        "-rc": "rc",
        "-preset": "preset",
    }
    result = {}
    for index, flag in enumerate(args):
        if flag not in names:
            continue
        if index + 1 >= len(args) or str(args[index + 1]).startswith("-"):
            raise ValueError(f"missing value for {flag}")
        value = args[index + 1]
        result[names[flag]] = (
            str(parse_bitrate(value))
            if flag in ("-b:v", "-maxrate", "-bufsize")
            else str(value)
        )
    return result


def validate_av_duration(path: pathlib.Path) -> dict[str, float]:
    """Fail full renders whose audio and video timelines disagree."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout)["streams"]
    durations = {
        item["codec_type"]: float(item["duration"])
        for item in streams
        if item["codec_type"] in ("video", "audio")
    }
    if "video" not in durations or "audio" not in durations:
        raise ValueError(f"missing audio/video stream: {path}")
    if abs(durations["video"] - durations["audio"]) > 0.1:
        raise ValueError(f"audio/video duration difference exceeds 0.1s: {durations}")
    return durations


def decode_frames(
    video: pathlib.Path, nvc: Any, gpu_id: int
) -> Iterator[tuple[Any, float]]:
    """Decode packets and convert PTS to seconds using the stream time base."""
    demuxer = nvc.CreateDemuxer(filename=str(video))
    decoder = nvc.CreateDecoder(
        gpuid=gpu_id, codec=demuxer.GetNvCodecId(), usedevicememory=True
    )
    time_base = demuxer.GetTimebaseNum() / demuxer.GetTimebaseDen()
    cupy, _ = load_cuda_modules()

    def retain(decoded):
        nonlocal next_slot, copied
        slot = next_slot % len(buffers)
        geometry = frame_geometry(decoded)
        owned = buffers[slot]
        if owned is None or (owned.width, owned.height) != geometry:
            owned = buffers[slot] = NV12Buffer(cupy, *geometry)
            owned.ready = cupy.cuda.Event(disable_timing=True)
        owned.load(decoded)
        owned.ready.record()
        copied = owned.ready
        next_slot += 1
        return owned, float(decoded.getPTS()) * time_base

    # The CFR scheduler holds current + lookahead, then asks for one more frame
    # before yielding. Three slots keep those references distinct, even in VFR.
    buffers = [None] * 3
    next_slot = 0
    copied = None

    def decode(packet):
        nonlocal copied
        # Decode may reuse its output surfaces. Fence only the latest D2D copy
        # before letting the SDK advance; other CUDA work need not be drained.
        if copied is not None:
            copied.synchronize()
            copied = None
        return decoder.Decode(packet)

    try:
        for packet in demuxer:
            for decoded in decode(packet):
                yield retain(decoded)
        # The installed SDK drains delayed frames on an empty PacketData.
        for decoded in decode(nvc.PacketData()):
            yield retain(decoded)
    finally:
        if copied is not None:
            copied.synchronize()


def schedule_frames(
    decoded: Iterator[tuple[Any, float]],
    first: tuple[Any, float],
    output_fps: float,
    max_frames: int | None = None,
    source_fps: float | None = None,
    start_offset: float = 0.0,
) -> Iterator[tuple[Any, float]]:
    """Yield one decoded frame and its CFR presentation time.

    The decoder is consumed once.  A source frame remains current until the
    next source PTS, which handles direct output, VFR input, integer-rate
    duplication and the final frame without separate encoding branches.
    """
    if not math.isfinite(output_fps) or output_fps <= 0:
        raise ValueError("output_fps must be positive")
    if max_frames is not None and max_frames <= 0:
        raise ValueError("max_frames must be positive")
    first_frame, first_pts = first
    first_pts -= start_offset
    current = first_frame
    current_relative = 0.0
    last_interval = 1.0 / (source_fps or output_fps)
    pending = next(decoded, None)
    frame_index = 0
    while True:
        presentation = frame_index / output_fps
        while pending is not None:
            pending_relative = max(0.0, float(pending[1]) - first_pts)
            if pending_relative < current_relative - 1e-9:
                raise ValueError("source timestamps must be monotonic")
            if pending_relative > presentation + 1e-9:
                break
            if pending_relative > current_relative:
                last_interval = pending_relative - current_relative
            current = pending[0]
            current_relative = pending_relative
            pending = next(decoded, None)
        # A source frame owns [PTS, next PTS). At EOF retain its nominal
        # interval, never pad to unreliable FLV format.duration metadata.
        end = current_relative + (1.0 / source_fps if source_fps else last_interval)
        if pending is None and presentation >= end - 1e-9:
            break
        yield current, presentation
        frame_index += 1
        if max_frames is not None and frame_index >= max_frames:
            return


def frame_geometry(decoded: Any) -> tuple[int, int]:
    """Return decoded width and height from the NV12 Y-plane metadata."""
    cai = decoded.cuda()[0].__cuda_array_interface__
    return int(cai["shape"][1]), int(cai["shape"][0])


def upload_atlas(cupy, atlas):
    """Upload the in-memory atlas once; no intermediate serialization."""
    return {
        "events": [RenderEvent.from_mapping(item) for item in atlas["events"]],
        **{key: cupy.asarray(atlas[key]) for key in ("y", "u", "v", "a")},
    }


def check_stop(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise RenderStopped("GPU render stopped by user")


def event_position(event: RenderEvent, timestamp: float) -> tuple[int, int] | None:
    """Interpolate one RenderPlan path at a presentation timestamp."""
    path = event.path
    if not path:
        return None
    if timestamp <= path[0][0]:
        return int(round(path[0][1])), int(round(path[0][2]))
    for left, right in zip(path, path[1:]):
        if timestamp <= right[0]:
            span = max(1e-9, float(right[0]) - float(left[0]))
            ratio = (timestamp - float(left[0])) / span
            x = left[1] + (right[1] - left[1]) * ratio
            y = left[2] + (right[2] - left[2]) * ratio
            return int(round(x)), int(round(y))
    return int(round(path[-1][1])), int(round(path[-1][2]))


class EventScanner:
    """Maintain active events with a start-time scanline."""

    def __init__(self, events: list[RenderEvent]):
        self.events = events
        self.start_order = sorted(
            range(len(events)), key=lambda index: (events[index].start, index)
        )
        self.next_start = 0
        self.active: set[int] = set()
        self.last_timestamp = float("-inf")

    def visible(self, timestamp: float) -> list[tuple[dict[str, Any], int, int, int]]:
        """Advance the scanline, preserving source order within each layer."""
        if timestamp < self.last_timestamp:
            raise ValueError("RenderPlan timestamps must be monotonic")
        self.last_timestamp = timestamp
        while self.next_start < len(self.start_order):
            index = self.start_order[self.next_start]
            if self.events[index].start > timestamp:
                break
            self.active.add(index)
            self.next_start += 1
        self.active = {
            index for index in self.active if self.events[index].end >= timestamp
        }
        visible = []
        for index in sorted(
            self.active, key=lambda index: (self.events[index].z_order, index)
        ):
            event = self.events[index]
            position = event_position(event, timestamp)
            if position is not None:
                motion = event.motion
                enter = max(0.0, float(motion.get("enter_duration", 0.0)))
                exit_duration = max(0.0, float(motion.get("exit_duration", 0.0)))
                alpha = 1.0
                if enter and timestamp < event.start + enter:
                    alpha = max(0.0, min(1.0, (timestamp - event.start) / enter))
                elif exit_duration and timestamp > event.end - exit_duration:
                    alpha = max(0.0, min(1.0, (event.end - timestamp) / exit_duration))
                visible.append(
                    (event, position[0], position[1], int(round(alpha * 255)))
                )
        return visible


class NV12Buffer:
    """Own one contiguous NV12 frame so the encoder sees stable storage."""

    def __init__(self, cupy: Any, width: int, height: int):
        self.cupy = cupy
        self.width = width
        self.height = height
        self.buffer = cupy.empty((height * 3 // 2, width), dtype=cupy.uint8)
        self.y = self.buffer[:height].reshape(height, width, 1)
        self.uv = self.buffer[height:].reshape(height // 2, width // 2, 2)
        self.ready = None
        self.chroma_location = "left"
        self.scale_tables = {}
        self.scale_kernel = None

    def load(self, decoded: Any) -> None:
        """Copy decoder planes into the stable NV12 allocation."""
        for source, target in zip(decoded.cuda(), (self.y, self.uv)):
            cai = source.__cuda_array_interface__
            shape = tuple(int(value) for value in cai["shape"])
            strides = tuple(int(value) for value in (cai["strides"] or source.strides))
            view = self.cupy.asarray(CaiView(source, shape, strides), copy=False)
            target[...] = view.reshape(target.shape)

    def load_into_canvas(self, decoded: Any) -> None:
        """Copy a decoded NV12 frame, scaling and letterboxing on the GPU."""
        ready = getattr(decoded, "ready", None)
        if ready is not None:
            self.cupy.cuda.get_current_stream().wait_event(ready)
        views = []
        for source in decoded.cuda():
            cai = source.__cuda_array_interface__
            shape = tuple(int(value) for value in cai["shape"])
            strides = tuple(int(value) for value in (cai["strides"] or source.strides))
            views.append(self.cupy.asarray(CaiView(source, shape, strides), copy=False))
        source_height, source_width = views[0].shape[:2]
        source_y = views[0].reshape(source_height, source_width)
        source_uv = views[1].reshape(source_height // 2, source_width // 2, 2)
        if source_width == self.width and source_height == self.height:
            self.y[..., 0] = source_y
            self.uv[...] = source_uv
            return
        self._scale(source_y, source_uv)

    def _scale(self, source_y: Any, source_uv: Any) -> None:
        """Scale both planes in one kernel, using cached centre/siting tables."""
        source_height, source_width = source_y.shape
        scaled_width, scaled_height, offset_x, offset_y = fit_canvas(
            (source_width, source_height), (self.width, self.height)
        )
        key = (
            source_width,
            source_height,
            self.width,
            self.height,
            self.chroma_location,
        )
        if key not in self.scale_tables:
            phase_x, phase_y = CHROMA_PHASES[self.chroma_location]
            axes = (
                (source_width, scaled_width, 0.5, 0.5),
                (source_height, scaled_height, 0.5, 0.5),
                (source_width // 2, scaled_width // 2, phase_x, 0.25),
                (source_height // 2, scaled_height // 2, phase_y, 0.5),
            )
            self.scale_tables[key] = tuple(
                value for axis in axes for value in sample_axis(self.cupy, *axis)
            )
        if self.scale_kernel is None:
            self.scale_kernel = self.cupy.RawKernel(SCALE_KERNEL, "scale_nv12")
        geometry = (
            self.width,
            self.height,
            scaled_width,
            scaled_height,
            offset_x,
            offset_y,
            source_width,
            source_height,
            *source_y.strides,
            *source_uv.strides,
        )
        count = self.width * self.height * 3 // 2
        self.scale_kernel(
            ((count + 255) // 256,),
            (256,),
            (
                source_y,
                source_uv,
                self.buffer,
                *(self.cupy.int32(value) for value in geometry),
                *self.scale_tables[key],
            ),
        )

    def cuda(self) -> list[Any]:
        """Expose the two NV12 planes through the encoder protocol."""
        return [self.y, self.uv]


class SpriteCompositor:
    """Own the GPU kernel and composite RenderPlan sprites."""

    def __init__(
        self,
        cupy: Any,
        width: int,
        height: int,
        plan_assets: dict[str, Any],
    ):
        self.cupy = cupy
        self.width = width
        self.height = height
        self.plan_assets = plan_assets
        self.event_scanner = EventScanner(plan_assets["events"])
        self.kernel = cupy.RawKernel(PLAN_KERNEL, "blend_plan")

    def apply(self, frame, timestamp):
        return self._apply_plan(frame, timestamp)

    def _apply_plan(self, frame: NV12Buffer, timestamp: float) -> int:
        """Upload active event metadata and blend the shared YUV atlas."""
        active = self.event_scanner.visible(timestamp)
        if not active:
            return 0
        px = self.cupy.asarray([item[1] for item in active], dtype=self.cupy.int32)
        py = self.cupy.asarray([item[2] for item in active], dtype=self.cupy.int32)
        offsets = self.cupy.asarray(
            [item[0].offset for item in active], dtype=self.cupy.int32
        )
        widths = self.cupy.asarray(
            [item[0].width for item in active], dtype=self.cupy.int32
        )
        heights = self.cupy.asarray(
            [item[0].height for item in active], dtype=self.cupy.int32
        )
        event_alpha = self.cupy.asarray(
            [item[3] for item in active], dtype=self.cupy.int32
        )
        self.kernel(
            ((self.width // 2 + 31) // 32, (self.height // 2 + 7) // 8),
            (32, 8),
            (
                frame.y.reshape(-1),
                frame.uv.reshape(-1),
                self.cupy.int32(self.width),
                self.cupy.int32(self.height),
                px,
                py,
                offsets,
                widths,
                heights,
                event_alpha,
                self.cupy.int32(len(active)),
                self.plan_assets["y"],
                self.plan_assets["u"],
                self.plan_assets["v"],
                self.plan_assets["a"],
            ),
        )
        return len(active)


def create_encoder(
    nvc, width, height, frame_rate, video_encoder, configured_options, stream=None
):
    """Use the native fps name and pass only explicitly configured options."""
    options = {
        "codec": codec_name(video_encoder),
        "fps": str(frame_rate),
        **configured_options,
    }
    logger.info(
        "CreateEncoder args: %s",
        json.dumps({"width": width, "height": height, **options}, sort_keys=True),
    )
    if stream is not None:
        options["cudastream"] = stream.ptr
    return nvc.CreateEncoder(width, height, "NV12", False, **options)


class FrameRing:
    """Keep four application inputs alive until NVENC's CUDA input copy ends.

    The encoder copies application planes to its own delayed/B-frame surfaces
    on the explicitly supplied CUDA stream. A slot's event is recorded after
    Encode enqueues that copy. Waiting only on slot reuse avoids overwriting
    an input without adding a device-wide synchronization after every Encode.
    Output packets cannot fence these four slots: the default B-frame pipeline
    returns its first packet only after seven submissions.
    """

    def __init__(self, cupy: Any, width: int, height: int, stream: Any):
        self.stream = stream
        self.buffers = [NV12Buffer(cupy, width, height) for _ in range(4)]
        self.copied = [cupy.cuda.Event(disable_timing=True) for _ in self.buffers]
        self.used = [False] * len(self.buffers)

    def acquire(self, index: int) -> NV12Buffer:
        slot = index % len(self.buffers)
        if self.used[slot]:
            self.copied[slot].synchronize()
        return self.buffers[slot]

    def submitted(self, index: int) -> None:
        slot = index % len(self.buffers)
        self.copied[slot].record(self.stream)
        self.used[slot] = True


def encode_to_file(
    frames: Iterator[tuple[Any, float]],
    first: tuple[Any, float],
    cupy: Any,
    nvc: Any,
    bitstream: pathlib.Path,
    geometry: tuple[int, int],
    frame_rate: float,
    config: RenderConfig,
    source_frame_rate: float | None = None,
    start_offset: float = 0.0,
    progress_cb=None,
    expected_frames: int | None = None,
    stop_event=None,
    chroma_location="left",
) -> dict[str, Any]:
    """Composite and encode decoded frames into an elementary stream."""
    width, height = geometry
    encode_stream = cupy.cuda.Stream(non_blocking=True)
    encoder = create_encoder(
        nvc,
        width,
        height,
        frame_rate,
        config.video_encoder,
        config.encoder_options,
        encode_stream,
    )
    ring = FrameRing(cupy, width, height, encode_stream)
    for buffer in ring.buffers:
        buffer.chroma_location = chroma_location
    plan_assets = upload_atlas(cupy, config.atlas)
    event_count = len(plan_assets["events"])
    compositor = SpriteCompositor(cupy, width, height, plan_assets)
    active_counts: list[int] = []
    first_pts = first[1]
    resolution_spans: list[list[float | int]] = []
    source_frames = 1
    last_source_pts = first_pts

    def record_geometry(decoded, timestamp):
        try:
            width, height = frame_geometry(decoded)
        except (AttributeError, IndexError, TypeError, KeyError):
            # Unit-test fakes and non-NV12 adapters may not expose CUDA shape
            # metadata; the production decoder always does.
            return
        relative = max(0.0, float(timestamp) - float(first_pts))
        if resolution_spans and resolution_spans[-1][2:] == [width, height]:
            resolution_spans[-1][1] = relative
        else:
            if resolution_spans:
                resolution_spans[-1][1] = relative
            resolution_spans.append([relative, relative, width, height])

    record_geometry(first[0], first_pts)

    def tracked_frames():
        nonlocal source_frames, last_source_pts
        for item in frames:
            source_frames += 1
            last_source_pts = item[1]
            record_geometry(item[0], item[1])
            yield item

    frame_count = 0
    packet_times = []
    started = time.perf_counter()
    last_progress = started
    try:
        with bitstream.open("wb") as stream:
            for decoded, frame_time in schedule_frames(
                tracked_frames(),
                first,
                frame_rate,
                max_frames=config.max_frames,
                source_fps=source_frame_rate,
                start_offset=start_offset,
            ):
                check_stop(stop_event)
                buffer = ring.acquire(frame_count)
                with encode_stream:
                    buffer.load_into_canvas(decoded)
                    active_counts.append(compositor.apply(buffer, frame_time))
                encode_stream.synchronize()
                picture = nvc.NV_ENC_PIC_PARAMS()
                picture.frameIdx = frame_count
                picture.inputTimeStamp = frame_count
                packets = encoder.Encode(buffer, picture)
                ring.submitted(frame_count)
                for packet in packets:
                    stream.write(packet["data"])
                    packet_times.append(int(packet["timestamp"]))
                frame_count += 1
                now = time.perf_counter()
                if progress_cb and now - last_progress >= 0.5:
                    progress_cb(
                        min(
                            99.0,
                            frame_count
                            * 100.0
                            / max(1, expected_frames or frame_count),
                        )
                    )
                    last_progress = now
            for packet in encoder.EndEncode():
                stream.write(packet["data"])
                packet_times.append(int(packet["timestamp"]))
            bframes = encoder.GetNumBFrames()
    finally:
        # Destroy the encoder before releasing any application input storage.
        del encoder, compositor, plan_assets, ring
        # Exceptions retain this frame until the caller clears the traceback.
        # Drop input references now, before render_one releases the CUDA pool.
        buffer = decoded = first = frames = None
    elapsed = time.perf_counter() - started
    return {
        "frames": frame_count,
        "packet_times": packet_times,
        "bframes": bframes,
        "wall_s": elapsed,
        "fps": frame_count / elapsed if elapsed else 0.0,
        "plan_events": event_count,
        "active_events_avg": (
            sum(active_counts) / len(active_counts) if active_counts else 0.0
        ),
        "active_events_peak": max(active_counts) if active_counts else 0,
        "first_pts_seconds": first_pts,
        "source_frames": source_frames,
        "last_source_time": last_source_pts - first_pts + start_offset,
        "last_relative_pts_seconds": (
            (frame_count - 1) / frame_rate if frame_count else 0.0
        ),
        "resolution_spans": [tuple(span) for span in resolution_spans],
    }


def stamp_video(
    bitstream: pathlib.Path,
    output: pathlib.Path,
    frame_rate: float,
    packet_times: list[int],
    bframes: int,
    stop_event=None,
) -> None:
    """Preserve NVENC's presentation indices across B-frame reordering.

    Raw HEVC has no container PTS. Guessing PTS from packet order can discard
    initial frames and reorder displayed frames. Negative DTS is valid; every
    presentation timestamp starts at or after zero.
    """
    import av

    time_base = Fraction(1, 1) / Fraction(str(frame_rate))
    with av.open(str(bitstream)) as source, av.open(str(output), "w") as target:
        stream = target.add_stream_from_template(source.streams.video[0], opaque=True)
        stream.time_base = time_base
        count = 0
        for packet in source.demux(video=0):
            check_stop(stop_event)
            if not packet.size:
                continue
            if count >= len(packet_times):
                raise ValueError("more video packets than encoder timestamps")
            packet.pts = packet_times[count]
            packet.dts = count - bframes
            packet.duration = 1
            packet.time_base = time_base
            packet.stream = stream
            target.mux(packet)
            count += 1
        if count != len(packet_times):
            raise ValueError("video packet/timestamp count differs")


def mux_audio(
    bitstream: pathlib.Path,
    source: pathlib.Path,
    output: pathlib.Path,
    frame_rate: float,
    trim_duration: float | None = None,
    audio_encoder: str = "copy",
    audio_args: list[str] | None = None,
    video_duration: float | None = None,
    stop_event=None,
    packet_times=None,
    bframes=0,
) -> None:
    """Mux encoded video with the source audio without truncating either stream."""
    if packet_times is not None:
        timestamped = bitstream.with_suffix(".mp4")
        stamp_video(
            bitstream, timestamped, frame_rate, packet_times, bframes, stop_event
        )
        bitstream = timestamped
    input_options = (
        []
        if bitstream.suffix == ".mp4"
        else ["-f", bitstream.suffix.lstrip("."), "-r", str(frame_rate)]
    )
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-nostdin",
        *input_options,
        "-i",
        str(bitstream),
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        audio_encoder,
        *(audio_args or []),
        str(output),
    ]
    if trim_duration is not None:
        command[-1:-1] = ["-t", str(trim_duration)]
    if audio_encoder != "copy":
        # Same audio timeline repair used by emoji_render_mt; pad missing tail
        # samples with silence without discarding any source audio.
        filters = "aresample=async=1000:first_pts=0"
        if trim_duration is None and video_duration is not None:
            filters += f",apad=whole_dur={video_duration}"
        command[-1:-1] = ["-af", filters]
    if stop_event is None:
        subprocess.run(command, check=True, capture_output=True, text=True)
    else:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=errors
            )
            try:
                while process.poll() is None:
                    check_stop(stop_event)
                    time.sleep(0.1)
                if process.returncode:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode("utf-8", errors="replace"))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait()


def render_one(video: Any, output: str, progress_cb=None, stop_event=None, **kwargs):
    """Render atomically; cancellation leaves no incomplete output."""
    check_stop(stop_event)
    source = pathlib.Path(getattr(video, "path", video))
    destination = pathlib.Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if progress_cb:
        progress_cb(0.0)
    from DMR.Render.sprites import detect_platform

    render_plan = kwargs.get("render_plan")
    atlas = kwargs.get("atlas")
    if atlas is None:
        if not render_plan:
            raise ValueError("gpu_render requires a RenderPlan")
        platform = kwargs.get("platform") or detect_platform(
            getattr(video, "dm_file_id", None) or render_plan
        )
        pack = (
            pathlib.Path(kwargs["emoji_pack"])
            if kwargs.get("emoji_pack")
            else pathlib.Path("emoji_pack") / platform
        )
        atlas = build_atlas(
            render_plan,
            platform=platform,
            emoticon_dir=pack / "emoticon",
            stop_check=lambda: check_stop(stop_event),
            pack_dir=str(pack),
            emoji_font=kwargs.get("emoji_font"),
        )
    check_stop(stop_event)
    config = RenderConfig(
        video_encoder=str(kwargs.get("vencoder", "hevc_nvenc")),
        encoder_options=parse_encoder_args(kwargs.get("vencoder_args") or []),
        fps=kwargs.get("fps"),
        minimum_fps=kwargs.get("min_fps"),
        max_frames=kwargs.get("max_frames"),
        gpu_id=int(kwargs.get("gpu_id", 0)),
        atlas=atlas,
    )
    cupy, nvc = load_cuda_modules()
    cupy.cuda.Device(config.gpu_id).use()
    source_fps = source_frame_rate(str(source))
    frame_rate = config.fps or resolve_output_fps(source_fps, config.minimum_fps)
    metadata = FFprobe.run_ffprobe(str(source))
    timing = metadata["format"]
    # Missing/unspecified metadata follows the H.26x left-sited convention.
    chroma_location = metadata["streams"][0].get("chroma_location", "left")
    if chroma_location in ("unknown", "unspecified"):
        chroma_location = "left"
    if chroma_location not in CHROMA_PHASES:
        raise ValueError(f"unsupported source chroma location: {chroma_location}")
    source_duration, source_start = float(timing["duration"]), float(
        timing.get("start_time", 0)
    )
    expected = int(round(source_duration * frame_rate))
    if config.max_frames is not None:
        expected = min(expected, config.max_frames)
    decoded = decode_frames(source, nvc, config.gpu_id)
    first = None
    try:
        first = next(decoded)
        width, height = kwargs.get("canvas_resolution") or resolve_render_canvas(
            getattr(video, "resolution", None) or frame_geometry(first[0]),
            rescale_to_1080p=bool(kwargs.get("rescale_to_1080p", False)),
            rescale_orientation=kwargs.get("rescale_orientation", "auto"),
        )
        codec = codec_name(config.video_encoder)
        if codec not in ("hevc", "h264"):
            raise ValueError(f"unsupported GPU video codec: {codec}")
        with tempfile.TemporaryDirectory(
            prefix="dmr_render_", dir=destination.parent
        ) as work:
            bitstream = pathlib.Path(work) / ("video." + codec)
            partial = pathlib.Path(work) / destination.name
            result = encode_to_file(
                decoded,
                first,
                cupy,
                nvc,
                bitstream,
                (width, height),
                frame_rate,
                config,
                source_frame_rate=source_fps,
                start_offset=max(0.0, first[1] - source_start),
                progress_cb=progress_cb,
                expected_frames=expected,
                stop_event=stop_event,
                chroma_location=chroma_location,
            )
            check_stop(stop_event)
            video_duration = result["frames"] / frame_rate
            truncated = (
                config.max_frames is not None
                and video_duration < source_duration - 1 / frame_rate
            )
            mux_audio(
                bitstream,
                source,
                partial,
                frame_rate,
                video_duration if truncated else None,
                kwargs.get("aencoder", "copy"),
                kwargs.get("aencoder_args"),
                video_duration,
                stop_event,
                result.pop("packet_times"),
                result["bframes"],
            )
            if not truncated:
                validate_av_duration(partial)
            check_stop(stop_event)
            partial.replace(destination)
        result.update(
            video=str(source),
            output=str(destination),
            width=width,
            height=height,
            frame_rate=frame_rate,
            intentional_trim=truncated,
            atlas_bytes=atlas["atlas_bytes"],
        )
        if progress_cb:
            progress_cb(100.0)
        return result
    finally:
        decoded.close()
        first = None
        config = None
        atlas = None
        # Only free unused pool blocks; allocations of other GPU tasks survive.
        cupy.get_default_memory_pool().free_all_blocks()


class GpuRender(BaseRender):
    """DMR GPU engine with cooperative progress and cancellation."""

    def __init__(self, **kwargs):
        self.options = dict(kwargs)
        self.stop_event = threading.Event()
        self.logger = logging.getLogger(__name__)

    def render_one(self, video, output, progress_cb=None, **kwargs):
        try:
            started = datetime.now()
            result = render_one(
                video,
                output,
                progress_cb=progress_cb,
                stop_event=self.stop_event,
                **{**self.options, **kwargs},
            )
            self.logger.info(
                "GPU render statistics: %s", json.dumps(result, ensure_ascii=False)
            )
            output_info = self.output_info(
                video,
                output,
                (result["width"], result["height"]),
                started,
                duration=result["frames"] / result["frame_rate"],
            )
            output_info.resolution_spans = result.get("resolution_spans", [])
            output_info["resolution_spans"] = output_info.resolution_spans
            return True, output_info
        except RenderStopped as error:
            error.__traceback__ = None
            return False, error
        except Exception as error:
            self.logger.warning("GPU render failed: %s", error)
            error.__traceback__ = None
            return False, error

    def stop(self):
        self.stop_event.set()
