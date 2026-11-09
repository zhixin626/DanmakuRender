"""DMR adapter for the optional GPU renderer with explicit fallback signaling."""

from __future__ import annotations

import logging
from typing import Any

from .baserender import BaseRender


class GpuRender(BaseRender):
    """Call the GPU renderer when a prepared atlas is supplied by the caller."""

    def __init__(self, **kwargs: Any) -> None:
        del kwargs
        self.logger = logging.getLogger(__name__)

    def render_one(self, video, output: str, progress_cb=None, **kwargs: Any):
        """Render with GPU or return a descriptive failure for the caller to fall back."""
        try:
            from .gpu_render import render_one

            sprite_data = kwargs.get("sprite_data")
            if not sprite_data:
                raise RuntimeError("gpu_render requires prepared sprite_data")
            return True, render_one(
                video,
                output,
                progress_cb=progress_cb,
                **kwargs,
            )
        except Exception as exc:
            self.logger.warning("GPU 渲染不可用，将回退到 emoji_dmrender: %s", exc)
            return False, exc

    def stop(self) -> None:
        """GPU renders are subprocess-free at this adapter boundary."""

