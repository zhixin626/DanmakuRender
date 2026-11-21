"""One timeline for the audio and video of a GPU render.

Live recordings jump their timestamps (pusher reconnects, CDN switches, slow
downloads). ``gpu_render.schedule_frames`` repairs the video timeline; the
audio here follows the same rules, so both tracks stay in sync by
construction instead of being repaired by two different algorithms:

- a step back of up to MAX_TIMESTAMP_GAP re-sends content already played:
  it is dropped until the timeline catches up (video drops frames, audio
  trims samples);
- a step forward of up to MAX_TIMESTAMP_GAP is missing media: the video
  holds its last frame, the audio plays silence;
- a larger jump either way starts a new timeline: it continues right after
  the last good frame. The video decides that shift and the audio reuses it,
  so both tracks move by the same amount.

Differences under ``tolerance`` (half an output video frame, the precision of
the video timeline) are timestamp jitter and keep the audio contiguous.
"""

from __future__ import annotations

import heapq
import pathlib
import threading
from fractions import Fraction
from typing import Iterator

import av
import numpy as np

from .baserender import RenderInputError, RenderStopped

# A timestamp jump larger than this is a stream reset, not missing media;
# holding the last frame that long would freeze the video.
MAX_TIMESTAMP_GAP = 10.0
AUDIO_SAMPLE_FORMAT = "fltp"
FALLBACK_FRAME_SIZE = 1024


def _check_stop(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise RenderStopped("GPU render stopped by user")


class SharedTimeline:
    """Timeline decisions the video publishes for the audio: the cumulative
    shift after each reset, then the final video length."""

    def __init__(self):
        self._shifts: list[float] = []
        self._end: float | None = None
        self._closed = False
        self._changed = threading.Condition()

    def add_shift(self, shift: float) -> None:
        with self._changed:
            self._shifts.append(shift)
            self._changed.notify_all()

    def close(self, end: float | None = None) -> None:
        """Video finished (end = its length) or failed (end = None)."""
        with self._changed:
            self._end = end
            self._closed = True
            self._changed.notify_all()

    def _wait(self, ready, stop_event):
        with self._changed:
            while not ready() and not self._closed:
                _check_stop(stop_event)
                self._changed.wait(0.2)

    def shift(self, index: int, stop_event=None) -> float | None:
        """The video's shift for its index-th reset, or None if it had none."""
        self._wait(lambda: len(self._shifts) > index, stop_event)
        return self._shifts[index] if index < len(self._shifts) else None

    def end(self, stop_event=None) -> float:
        self._wait(lambda: False, stop_event)
        if self._end is None:
            raise RenderStopped("video render ended without a result")
        return self._end


class AudioWriter:
    """Encodes planar samples placed at absolute output sample positions."""

    def __init__(self, stream, container, layout: str, tolerance: int):
        self.stream = stream
        self.container = container
        self.layout = layout
        self.rate = stream.codec_context.sample_rate
        self.tolerance = tolerance
        self.written = 0
        self.encoded = 0
        self.pending: list[np.ndarray] = []
        self.pending_samples = 0
        context = stream.codec_context
        context.open()
        self.frame_size = context.frame_size or FALLBACK_FRAME_SIZE
        encoder_format = context.format.name
        self.convert = (
            None
            if encoder_format == AUDIO_SAMPLE_FORMAT
            else av.AudioResampler(
                format=encoder_format, layout=layout, rate=self.rate
            )
        )

    def place(self, samples: np.ndarray, start: int) -> None:
        gap = start - self.written
        if abs(gap) <= self.tolerance:
            gap = 0
        if gap > 0:
            self.silence(gap)
        elif gap < 0:
            samples = samples[:, -gap:]
        self._append(samples)

    def silence(self, count: int) -> None:
        channels = len(self.stream.codec_context.layout.channels)
        while count > 0:
            chunk = min(count, self.rate)
            self._append(np.zeros((channels, chunk), dtype=np.float32))
            count -= chunk

    def pad_to(self, total: int) -> None:
        if total > self.written:
            self.silence(total - self.written)

    def _append(self, samples: np.ndarray) -> None:
        if not samples.shape[1]:
            return
        self.pending.append(samples)
        self.pending_samples += samples.shape[1]
        self.written += samples.shape[1]
        while self.pending_samples >= self.frame_size:
            buffered = np.concatenate(self.pending, axis=1)
            self._encode(buffered[:, : self.frame_size])
            rest = buffered[:, self.frame_size :]
            self.pending = [rest] if rest.shape[1] else []
            self.pending_samples = rest.shape[1]

    def _encode(self, samples: np.ndarray) -> None:
    
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(samples, dtype=np.float32),
            format=AUDIO_SAMPLE_FORMAT,
            layout=self.layout,
        )
        frame.sample_rate = self.rate
        frame.pts = self.encoded
        frame.time_base = Fraction(1, self.rate)
        self.encoded += samples.shape[1]
        for converted in self.convert.resample(frame) if self.convert else [frame]:
            for packet in self.stream.encode(converted):
                self.container.mux(packet)

    def flush(self) -> None:
        if self.pending_samples:
            self._encode(np.concatenate(self.pending, axis=1))
            self.pending, self.pending_samples = [], 0
        for packet in self.stream.encode(None):
            self.container.mux(packet)


def render_audio(
    source: pathlib.Path,
    output: pathlib.Path,
    origin: float,
    codec: str,
    bit_rate: int | None,
    options: dict[str, str],
    timeline: SharedTimeline,
    tolerance: float,
    stop_event=None,
    limit: float | None = None,
) -> float:
    """Decode the source audio, place it on the shared timeline and encode
    it to ``output``, padded with silence to the video length. Returns where
    the source audio ended on the timeline (seconds), before padding."""

    with av.open(str(source)) as container:
        if not container.streams.audio:
            raise RenderInputError(f"missing audio stream: {source}")
        source_stream = container.streams.audio[0]
        rate = source_stream.codec_context.sample_rate
        layout = source_stream.codec_context.layout.name
        resampler = av.AudioResampler(
            format=AUDIO_SAMPLE_FORMAT, layout=layout, rate=rate
        )
        with av.open(str(output), "w") as target:
            stream = target.add_stream(codec, rate=rate)
            stream.codec_context.layout = layout
            if bit_rate:
                stream.codec_context.bit_rate = bit_rate
            if options:
                stream.codec_context.options = dict(options)
            writer = AudioWriter(stream, target, layout, round(tolerance * rate))
            shift = 0.0
            resets = 0
            for frame in container.decode(source_stream):
                _check_stop(stop_event)
                expected = writer.written / rate
                relative = (
                    expected
                    if frame.pts is None
                    else float(frame.pts * frame.time_base) - origin + shift
                )
                if abs(expected - relative) > MAX_TIMESTAMP_GAP:
                    own = shift + expected - relative
                    video = timeline.shift(resets, stop_event)
                    resets += 1
                    # Reuse the video's shift so both tracks move together;
                    # an implausible one means the resets did not pair up.
                    shift = (
                        video
                        if video is not None and abs(video - own) <= MAX_TIMESTAMP_GAP
                        else own
                    )
                    relative = float(frame.pts * frame.time_base) - origin + shift
                # A preview (max_frames) needs no audio past its end; mux drops
                # it anyway. Full renders keep going: where the source audio
                # ends is what detects video lost at the end of the file.
                if limit is not None and relative >= limit:
                    break
                converted = [f.to_ndarray() for f in resampler.resample(frame)]
                if converted:
                    writer.place(
                        np.concatenate(converted, axis=1), round(relative * rate)
                    )
            for tail in resampler.resample(None):
                writer.place(tail.to_ndarray(), writer.written)
            content_end = writer.written / rate
            writer.pad_to(round(timeline.end(stop_event) * rate))
            writer.flush()
    return content_end


class AudioJob:
    """Runs render_audio beside the video encoder; join() returns its result
    or re-raises its error."""

    def __init__(self, **kwargs):
        self.error: BaseException | None = None
        self.content_end = 0.0
        self.thread = threading.Thread(target=self._run, kwargs=kwargs, daemon=True)
        self.thread.start()

    def _run(self, **kwargs):
        try:
            self.content_end = render_audio(**kwargs)
        except BaseException as error:
            self.error = error

    def wait(self) -> None:
        self.thread.join()

    def join(self) -> float:
        self.wait()
        if self.error is not None:
            raise self.error
        return self.content_end


def mux_streams(
    bitstream: pathlib.Path,
    packet_times: list[int],
    bframes: int,
    frame_rate: float,
    audio: pathlib.Path,
    output: pathlib.Path,
    end: float,
    stop_event=None,
) -> None:
    """Write the encoded video (NVENC presentation indices, as in
    stamp_video) and the timeline audio into ``output`` in one pass. Audio
    packets starting at or after ``end`` (the video length) are dropped."""

    video_base = Fraction(1, 1) / Fraction(str(frame_rate))
    with av.open(str(bitstream)) as video_in, av.open(str(audio)) as audio_in, av.open(
        str(output), "w"
    ) as target:
        video_out = target.add_stream_from_template(
            video_in.streams.video[0], opaque=True
        )
        video_out.time_base = video_base
        audio_source = audio_in.streams.audio[0]
        audio_out = target.add_stream_from_template(audio_source, opaque=True)

        def video_packets() -> Iterator[tuple[float, int, object]]:
            count = 0
            for packet in video_in.demux(video=0):
                if not packet.size:
                    continue
                if count >= len(packet_times):
                    raise ValueError("more video packets than encoder timestamps")
                packet.pts = packet_times[count]
                packet.dts = count - bframes
                packet.duration = 1
                packet.time_base = video_base
                packet.stream = video_out
                yield float(packet.dts * video_base), 0, packet
                count += 1
            if count != len(packet_times):
                raise ValueError("video packet/timestamp count differs")

        def audio_packets() -> Iterator[tuple[float, int, object]]:
            for packet in audio_in.demux(audio_source):
                if not packet.size or packet.pts is None:
                    continue
                start = float(packet.pts * packet.time_base)
                if start >= end:
                    break
                packet.stream = audio_out
                yield float(packet.dts * packet.time_base), 1, packet

        for _, _, packet in heapq.merge(
            video_packets(), audio_packets(), key=lambda item: item[:2]
        ):
            _check_stop(stop_event)
            target.mux(packet)
