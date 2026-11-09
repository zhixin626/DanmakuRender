"""GPU renderer building blocks used by the future gpu_render engine."""

from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Iterator


PRESSURE_KERNEL = r"""
extern "C" __global__
void blend_pressure(unsigned char* y, int width, int height, int sprites) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int yy = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= width || yy >= height) return;
    int value = y[yy * width + x];
    for (int sprite = 0; sprite < sprites; ++sprite) {
        int x0 = (sprite * 37) % max(1, width - 300);
        int y0 = (sprite * 17) % max(1, height - 60);
        if (x >= x0 && x < x0 + 300 && yy >= y0 && yy < y0 + 60) {
            value = min(255, value + 1);
        }
    }
    y[yy * width + x] = (unsigned char)value;
}
"""

PLAN_KERNEL = r"""
extern "C" __global__
void blend_plan(unsigned char* y, unsigned char* uv, int width, int height,
                const int* px, const int* py, const int* offsets,
                const int* widths, const int* heights, int count,
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
                int alpha = sa[index];
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
                int alpha = sa[index];
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


@dataclass(frozen=True)
class RenderConfig:
    """Runtime values supplied by the render engine or the test launcher."""

    video_encoder: str
    bitrate: int
    preset: str
    fps: float | None
    minimum_fps: float | None
    max_frames: int | None
    pressure_sprites: int
    gpu_id: int
    render_plan: pathlib.Path | None
    sprite_data: pathlib.Path | None


class CaiView:
    """Normalize PyNvVideoCodec CAI metadata for CuPy."""

    def __init__(self, source: Any, shape: tuple[int, ...], strides: tuple[int, ...]):
        value = dict(source.__cuda_array_interface__)
        value["shape"] = tuple(shape)
        value["strides"] = tuple(strides)
        self.__cuda_array_interface__ = value


def load_cuda_modules() -> tuple[Any, Any]:
    """Load CUDA modules lazily so importing DMR does not require perf paths."""
    import cupy
    import PyNvVideoCodec

    return cupy, PyNvVideoCodec


def resolve_output_fps(source_fps: float, minimum_fps: float | None) -> float:
    """Reuse the existing renderer's output-FPS policy."""
    try:
        from .media import resolve_output_fps as resolver
    except ImportError:
        from media import resolve_output_fps as resolver
    return float(resolver(source_fps, minimum_fps))


def probe_source_fps(video: pathlib.Path) -> float:
    """Read the same PyAV frame rate used by the old renderer."""
    try:
        from .media import source_frame_rate
    except ImportError:
        from media import source_frame_rate
    return source_frame_rate(str(video))


def codec_name(video_encoder: str) -> str:
    """Convert an FFmpeg NVENC name into PyNvVideoCodec's codec name."""
    return video_encoder.removesuffix("_nvenc")


def bitrate_from_args(video_encoder_args: list[str] | None, bitrate: int | None) -> int:
    """Read the configured video bitrate without embedding a project default."""
    if bitrate is not None:
        return int(bitrate)
    args = video_encoder_args or []
    for index, value in enumerate(args[:-1]):
        if value == "-b:v":
            return parse_bitrate(args[index + 1])
    raise ValueError("gpu_render requires a configured video bitrate")


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
    return int(float(text) * multiplier)


def decode_frames(video: pathlib.Path, nvc: Any, gpu_id: int) -> Iterator[tuple[Any, float]]:
    """Decode packets and convert PTS to seconds using the stream time base."""
    demuxer = nvc.CreateDemuxer(filename=str(video))
    decoder = nvc.CreateDecoder(gpuid=gpu_id, codec=demuxer.GetNvCodecId(), usedevicememory=True)
    time_base = demuxer.GetTimebaseNum() / demuxer.GetTimebaseDen()
    for packet in demuxer:
        for decoded in decoder.Decode(packet):
            yield decoded, float(decoded.getPTS()) * time_base


def frame_geometry(decoded: Any) -> tuple[int, int]:
    """Return decoded width and height from the NV12 Y-plane metadata."""
    cai = decoded.cuda()[0].__cuda_array_interface__
    return int(cai["shape"][1]), int(cai["shape"][0])


def load_plan_assets(cupy: Any, directory: pathlib.Path) -> dict[str, Any]:
    """Load the CPU-built sprite atlas and event paths once per render."""
    arrays = cupy.load(str(directory / "sprites.npz"))
    events = json.loads((directory / "events.json").read_text(encoding="utf-8"))["events"]
    return {
        "events": events,
        "y": cupy.asarray(arrays["y"]),
        "u": cupy.asarray(arrays["u"]),
        "v": cupy.asarray(arrays["v"]),
        "a": cupy.asarray(arrays["a"]),
    }


def event_position(event: dict[str, Any], timestamp: float) -> tuple[int, int] | None:
    """Interpolate one RenderPlan path at a presentation timestamp."""
    path = event.get("path") or []
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

    def __init__(self, events: list[dict[str, Any]]):
        self.events = events
        self.start_order = sorted(range(len(events)), key=lambda index: (events[index]["start"], index))
        self.next_start = 0
        self.active: set[int] = set()
        self.last_timestamp = float("-inf")

    def visible(self, timestamp: float) -> list[tuple[dict[str, Any], int, int]]:
        """Advance the scanline and return active events in source order."""
        if timestamp < self.last_timestamp:
            raise ValueError("RenderPlan timestamps must be monotonic")
        self.last_timestamp = timestamp
        while self.next_start < len(self.start_order):
            index = self.start_order[self.next_start]
            if float(self.events[index]["start"]) > timestamp:
                break
            self.active.add(index)
            self.next_start += 1
        self.active = {
            index for index in self.active if float(self.events[index]["end"]) >= timestamp
        }
        visible = []
        for index in sorted(self.active):
            event = self.events[index]
            position = event_position(event, timestamp)
            if position is not None:
                visible.append((event, position[0], position[1]))
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

    def load(self, decoded: Any) -> None:
        """Copy decoder planes into the stable NV12 allocation."""
        for source, target in zip(decoded.cuda(), (self.y, self.uv)):
            cai = source.__cuda_array_interface__
            shape = tuple(int(value) for value in cai["shape"])
            strides = tuple(int(value) for value in cai["strides"])
            view = self.cupy.asarray(CaiView(source, shape, strides), copy=False)
            target[...] = view.reshape(target.shape)

    def cuda(self) -> list[Any]:
        """Expose the two NV12 planes through the encoder protocol."""
        return [self.y, self.uv]


class SpriteCompositor:
    """Own the GPU kernel and apply pressure or RenderPlan sprites."""

    def __init__(
        self,
        cupy: Any,
        width: int,
        height: int,
        pressure_sprites: int,
        plan_assets: dict[str, Any] | None = None,
    ):
        self.cupy = cupy
        self.width = width
        self.height = height
        self.pressure_sprites = pressure_sprites
        self.plan_assets = plan_assets
        self.event_scanner = EventScanner(plan_assets["events"]) if plan_assets else None
        source = PLAN_KERNEL if plan_assets else PRESSURE_KERNEL
        name = "blend_plan" if plan_assets else "blend_pressure"
        self.kernel = cupy.RawKernel(source, name)

    def apply(self, frame: NV12Buffer, timestamp: float) -> int:
        """Apply visible sprites and return the active event count."""
        if self.plan_assets:
            return self._apply_plan(frame, timestamp)
        self._apply_pressure(frame)
        return self.pressure_sprites

    def _apply_pressure(self, frame: NV12Buffer) -> None:
        """Apply the old prototype workload used by earlier measurements."""
        self.kernel(
            ((self.width + 31) // 32, (self.height + 7) // 8),
            (32, 8),
            (
                frame.buffer[: self.height].reshape(-1),
                self.cupy.int32(self.width),
                self.cupy.int32(self.height),
                self.cupy.int32(self.pressure_sprites),
            ),
        )

    def _apply_plan(self, frame: NV12Buffer, timestamp: float) -> int:
        """Upload active event metadata and blend the shared YUV atlas."""
        active = self.event_scanner.visible(timestamp)
        if not active:
            return 0
        px = self.cupy.asarray([item[1] for item in active], dtype=self.cupy.int32)
        py = self.cupy.asarray([item[2] for item in active], dtype=self.cupy.int32)
        offsets = self.cupy.asarray([item[0]["offset"] for item in active], dtype=self.cupy.int32)
        widths = self.cupy.asarray([item[0]["width"] for item in active], dtype=self.cupy.int32)
        heights = self.cupy.asarray([item[0]["height"] for item in active], dtype=self.cupy.int32)
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
                self.cupy.int32(len(active)),
                self.plan_assets["y"],
                self.plan_assets["u"],
                self.plan_assets["v"],
                self.plan_assets["a"],
            ),
        )
        return len(active)


def frame_signature(cupy: Any, frame: NV12Buffer) -> int:
    """Return a cheap luma checksum used only for diagnostic output."""
    sample = frame.y[frame.height // 4 : frame.height // 4 + 64, :64]
    return int(cupy.sum(sample).get())


def encoder_config(video_encoder: str) -> str:
    """Map the configured FFmpeg encoder to the native codec identifier."""
    return codec_name(video_encoder)


def encode_to_file(
    frames: Iterator[tuple[Any, int]],
    first: tuple[Any, int],
    cupy: Any,
    nvc: Any,
    bitstream: pathlib.Path,
    geometry: tuple[int, int],
    frame_rate: float,
    config: RenderConfig,
) -> dict[str, Any]:
    """Composite and encode decoded frames into an elementary stream."""
    width, height = geometry
    encoder = nvc.CreateEncoder(
        width,
        height,
        "NV12",
        False,
        codec=encoder_config(config.video_encoder),
        bitrate=config.bitrate,
        framerate=frame_rate,
        preset=config.preset,
    )
    buffer = NV12Buffer(cupy, width, height)
    plan_assets = load_plan_assets(cupy, config.sprite_data) if config.sprite_data else None
    compositor = SpriteCompositor(cupy, width, height, config.pressure_sprites, plan_assets)
    signatures: list[int] = []
    active_counts: list[int] = []
    first_pts = first[1]
    frame_count = 0
    started = time.perf_counter()
    with bitstream.open("wb") as stream:
        for decoded, pts in itertools.chain((first,), frames):
            buffer.load(decoded)
            if frame_count < 200 and frame_count % 10 == 0:
                signatures.append(frame_signature(cupy, buffer))
            relative_pts = max(0.0, pts - first_pts)
            frame_time = frame_count / frame_rate
            active_counts.append(compositor.apply(buffer, frame_time))
            for packet in encoder.Encode(buffer):
                stream.write(packet["data"])
            frame_count += 1
            if config.max_frames is not None and frame_count >= config.max_frames:
                break
        for packet in encoder.EndEncode():
            stream.write(packet["data"])
    elapsed = time.perf_counter() - started
    return {
        "frames": frame_count,
        "wall_s": elapsed,
        "fps": frame_count / elapsed if elapsed else 0.0,
        "decoded_signatures": signatures,
        "decoded_frames_change": len(set(signatures)) > 1,
        "plan_events": len(plan_assets["events"]) if plan_assets else 0,
        "active_events_avg": sum(active_counts) / len(active_counts) if active_counts else 0.0,
        "active_events_peak": max(active_counts) if active_counts else 0,
        "first_pts_seconds": first_pts,
        "last_relative_pts_seconds": relative_pts if frame_count else 0.0,
    }


def mux_with_audio(bitstream: pathlib.Path, source: pathlib.Path, output: pathlib.Path, frame_rate: float) -> None:
    """Mux encoded video with the source audio without truncating either stream."""
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-nostdin",
        "-f",
        "hevc",
        "-r",
        str(frame_rate),
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
        "copy",
        str(output),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


def render_one(video: Any, output: str, progress_cb: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Render one video using the same argument shape as EmojiDmRender.render_one."""
    del progress_cb
    source = pathlib.Path(getattr(video, "path", video))
    destination = pathlib.Path(output)
    config = RenderConfig(
        video_encoder=str(kwargs.get("vencoder", "hevc_nvenc")),
        bitrate=bitrate_from_args(kwargs.get("vencoder_args"), kwargs.get("bitrate")),
        preset=str(kwargs.get("preset", "P4")),
        fps=kwargs.get("fps"),
        minimum_fps=kwargs.get("min_fps"),
        max_frames=kwargs.get("max_frames"),
        pressure_sprites=int(kwargs.get("sprites", 91)),
        gpu_id=int(kwargs.get("gpu_id", 0)),
        render_plan=pathlib.Path(kwargs["render_plan"]) if kwargs.get("render_plan") else None,
        sprite_data=pathlib.Path(kwargs["sprite_data"]) if kwargs.get("sprite_data") else None,
    )
    if bool(config.render_plan) != bool(config.sprite_data):
        raise ValueError("render_plan and sprite_data must be supplied together")
    cupy, nvc = load_cuda_modules()
    cupy.cuda.Device(config.gpu_id).use()
    frame_rate = config.fps or resolve_output_fps(probe_source_fps(source), config.minimum_fps)
    decoded = decode_frames(source, nvc, config.gpu_id)
    first = next(decoded)
    width, height = frame_geometry(first[0])
    bitstream = destination.with_suffix(".hevc")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = encode_to_file(
        decoded,
        first,
        cupy,
        nvc,
        bitstream,
        (width, height),
        frame_rate,
        config,
    )
    mux_with_audio(bitstream, source, destination, frame_rate)
    result.update({"video": str(source), "output": str(destination), "width": width, "height": height, "frame_rate": frame_rate})
    return result


def main() -> None:
    """Run the standalone prototype launcher used by perf tests."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--vencoder", default="hevc_nvenc")
    parser.add_argument("--bitrate", type=int, required=True)
    parser.add_argument("--preset", default="P4")
    parser.add_argument("--fps", type=float)
    parser.add_argument("--min-fps", type=float)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--sprites", type=int, default=91)
    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument("--render-plan", type=pathlib.Path)
    parser.add_argument("--sprite-data", type=pathlib.Path)
    args = parser.parse_args()
    result = render_one(
        args.video,
        str(args.output),
        vencoder=args.vencoder,
        bitrate=args.bitrate,
        preset=args.preset,
        fps=args.fps,
        min_fps=args.min_fps,
        max_frames=args.max_frames,
        sprites=args.sprites,
        gpu_id=args.gpu_id,
        render_plan=args.render_plan,
        sprite_data=args.sprite_data,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
