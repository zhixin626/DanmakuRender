"""DMR adapter for the optional GPU renderer with explicit fallback signaling."""

from __future__ import annotations

import logging
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .baserender import BaseRender


class GpuRender(BaseRender):
    """Call the GPU renderer when a prepared atlas is supplied by the caller."""

    def __init__(self, **kwargs: Any) -> None:
        del kwargs
        self.logger = logging.getLogger(__name__)

    def render_one(self, video, output: str, progress_cb=None, **kwargs: Any):
        """Render with GPU or return a descriptive failure for the caller to fall back."""
        atlas_tmp = None
        try:
            from .gpu_render import render_one

            sprite_data = kwargs.get("sprite_data")
            render_plan = kwargs.get("render_plan")
            if render_plan and not sprite_data:
                atlas_tmp = pathlib.Path(tempfile.mkdtemp(prefix="dmr_gpu_atlas_", dir=str(pathlib.Path(output).parent)))
                atlas_script = pathlib.Path(__file__).with_name("gpu_atlas.py")
                subprocess.run(
                    [sys.executable, str(atlas_script), "--plan", str(render_plan), "--output", str(atlas_tmp),
                     "--platform", str(kwargs.get("platform", "bilibili"))],
                    check=True,
                )
                sprite_data = atlas_tmp
                kwargs["sprite_data"] = sprite_data
            if not sprite_data:
                raise RuntimeError("gpu_render requires render_plan or prepared sprite_data")
            return True, render_one(
                video,
                output,
                progress_cb=progress_cb,
                **kwargs,
            )
        except Exception as exc:
            self.logger.warning("GPU 渲染不可用，将回退到 emoji_dmrender: %s", exc)
            return False, exc
        finally:
            if atlas_tmp:
                shutil.rmtree(atlas_tmp, ignore_errors=True)

    def stop(self) -> None:
        """GPU renders are subprocess-free at this adapter boundary."""

