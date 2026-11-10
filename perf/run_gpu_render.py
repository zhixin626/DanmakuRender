"""Invoke the production GPU renderer with the active environment."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from DMR.Render.gpu_render import load_cuda_modules, main

def configure_cuda_dlls():
    load_cuda_modules()

if __name__ == "__main__":
    main()
