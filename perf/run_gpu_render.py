"""Launch gpu_render.py with the locally installed CUDA wheels."""

from __future__ import annotations

import os
import pathlib
import runpy
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE_PACKAGES = pathlib.Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"


def configure_cuda_dlls() -> None:
    """Expose wheel DLLs to Windows without putting test paths in DMR code."""
    candidates = (
        SITE_PACKAGES / "PyNvVideoCodec",
        SITE_PACKAGES / "nvidia" / "cuda_nvrtc" / "bin",
        SITE_PACKAGES / "nvidia" / "cuda_runtime" / "bin",
    )
    for directory in candidates:
        if not directory.exists():
            continue
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(directory))
        os.environ["PATH"] = str(directory) + ";" + os.environ.get("PATH", "")


def main() -> None:
    """Run the production module as a standalone perf command."""
    configure_cuda_dlls()
    runpy.run_path(str(ROOT / "DMR" / "Render" / "gpu_render.py"), run_name="__main__")


if __name__ == "__main__":
    main()
