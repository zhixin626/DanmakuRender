from abc import ABC, abstractmethod
import copy
import os
from DMR.utils import VideoInfo, uuid


class RenderStopped(RuntimeError):
    """User cancellation; callers must neither retry nor fall back."""


class BaseRender(ABC):
    @abstractmethod
    def __init__(self, debug, **kwargs):
        pass

    @abstractmethod
    def render_one(
        self, video: VideoInfo, output: str, **kwargs
    ) -> tuple[bool, VideoInfo | Exception]:
        """Return output VideoInfo on success, or an exception on failure.

        RenderStopped denotes cancellation. Never return diagnostic dictionaries:
        the task engine needs the output's group/segment identity to reconcile.
        """
        raise NotImplementedError

    @staticmethod
    def output_info(
        video, output, canvas_resolution, started, duration=None
    ) -> VideoInfo:
        """Build a render result while preserving the source's task identity."""
        result = (
            copy.deepcopy(video)
            if isinstance(video, VideoInfo)
            else VideoInfo(path=str(video))
        )
        result.dtype = "dm_video"
        result.path = output
        result.resolution = tuple(canvas_resolution)
        result.file_id = uuid()
        result.size = os.path.getsize(output)
        result.ctime = started
        result.dm_file_id = None
        result.src_video_id = getattr(video, "file_id", None)
        if duration is not None:
            result.duration = duration
        return result

    def is_stopped(self) -> bool:
        event = getattr(self, "stop_event", None)
        return bool(
            getattr(self, "_stopped", False) or (event is not None and event.is_set())
        )

    @abstractmethod
    def stop(self):
        pass
