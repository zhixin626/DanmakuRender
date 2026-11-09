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
        from .emoji_render_mt import resolve_output_fps as resolver
    except ImportError:
        from emoji_render_mt import resolve_output_fps as resolver
    return float(resolver(source_fps, minimum_fps))


def probe_source_fps(video: pathlib.Path) -> float:
    """Read the source rate used by both the old and GPU renderers."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate,r_frame_rate", "-of", "json", str(video)],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    for key in ("avg_frame_rate", "r_frame_rate"):
        numerator, denominator = stream.get(key, "0/0").split("/")
        if float(denominator) > 0:
            return float(numerator) / float(denominator)
    raise ValueError(f"unable to determine source fps: {video}")


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
            return int(str(args[index + 1]).lower().removesuffix("m")) * 1_000_000
    raise ValueError("gpu_render requires a configured video bitrate")


def decode_frames(video: pathlib.Path, nvc: Any, gpu_id: int) -> Iterator[tuple[Any, int]]:
    """Decode packets with the native API and yield each frame with its PTS."""
    demuxer = nvc.CreateDemuxer(filename=str(video))
    decoder = nvc.CreateDecoder(gpuid=gpu_id, codec=demuxer.GetNvCodecId(), usedevicememory=True)
    for packet in demuxer:
        for decoded in decoder.Decode(packet):
            yield decoded, int(decoded.getPTS())


def frame_geometry(decoded: Any) -> tuple[int, int]:
    """Return decoded width and height from the NV12 Y-plane metadata."""
    cai = decoded.cuda()[0].__cuda_array_interface__
    return int(cai["shape"][1]), int(cai["shape"][0])


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
    """Own the GPU kernel and apply the current prototype sprite workload."""

    def __init__(self, cupy: Any, width: int, height: int, pressure_sprites: int):
        self.cupy = cupy
        self.width = width
        self.height = height
        self.pressure_sprites = pressure_sprites
        self.kernel = cupy.RawKernel(PRESSURE_KERNEL, "blend_pressure")

    def apply(self, frame: NV12Buffer) -> None:
        """Apply the prototype pressure sprites to the frame's luma plane."""
        self.kernel(
            ((self.width + 31) // 32, (self.height + 7) // 8),
            (32, 8),
            (frame.buffer[: self.height].reshape(-1), self.cupy.int32(self.width), self.cupy.int32(self.height), self.cupy.int32(self.pressure_sprites)),
        )


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
    width: int,
    height: int,
    frame_rate: float,
    bitrate: int,
    video_encoder: str,
    preset: str,
    pressure_sprites: int,
    max_frames: int | None,
) -> dict[str, Any]:
    """Composite and encode decoded frames into an elementary stream."""
    encoder = nvc.CreateEncoder(width, height, "NV12", False, codec=encoder_config(video_encoder), bitrate=bitrate, framerate=frame_rate, preset=preset)
    buffer = NV12Buffer(cupy, width, height)
    compositor = SpriteCompositor(cupy, width, height, pressure_sprites)
    signatures: list[int] = []
    frame_count = 0
    started = time.perf_counter()
    with bitstream.open("wb") as stream:
        for decoded, pts in itertools.chain((first,), frames):
            del pts
            buffer.load(decoded)
            if frame_count < 200 and frame_count % 10 == 0:
                signatures.append(frame_signature(cupy, buffer))
            compositor.apply(buffer)
            for packet in encoder.Encode(buffer):
                stream.write(packet["data"])
            frame_count += 1
            if max_frames is not None and frame_count >= max_frames:
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
    )
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
        width,
        height,
        frame_rate,
        config.bitrate,
        config.video_encoder,
        config.preset,
        config.pressure_sprites,
        config.max_frames,
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
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
