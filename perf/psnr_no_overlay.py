from __future__ import annotations

import pathlib
import subprocess
import tempfile
from statistics import mean

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "演示视频"
    / "00_素材与旧版"
    / "bilibili_峰哥_7月30日20点23分_中段"
    / "7月30日20点23分_中段.flv"
)
E_VIDEO = (
    ROOT
    / "演示视频"
    / "00_素材与旧版"
    / "bilibili_峰哥_7月30日20点23分_中段"
    / "旧版E.mp4"
)
G_VIDEO = ROOT / "perf" / "tmp" / "G_25_demo_source.mp4"
TIMES = (30.0, 90.0, 150.0)
PATCH = 128


def frame(video: pathlib.Path, index: int) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        path = pathlib.Path(handle.name)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-vf",
                f"select=eq(n\\,{index})",
                "-frames:v",
                "1",
                "-vsync",
                "0",
                str(path),
            ],
            check=True,
        )
        return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
    finally:
        path.unlink(missing_ok=True)


def yuv(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return np.stack(
        (
            0.299 * r + 0.587 * g + 0.114 * b,
            -0.168736 * r - 0.331264 * g + 0.5 * b,
            0.5 * r - 0.418688 * g - 0.081312 * b,
        ),
        axis=-1,
    )


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a - b) ** 2))
    return 99.0 if mse == 0 else 10.0 * np.log10((255.0 * 255.0) / mse)


e_scores, g_scores, diffs, selected = [], [], [], []
for timestamp in TIMES:
    index = int(timestamp * 25)
    src = frame(SOURCE, index)
    e = frame(E_VIDEO, index)
    g = frame(G_VIDEO, index)
    candidates = []
    for y in range(0, src.shape[0] - PATCH + 1, PATCH):
        for x in range(0, src.shape[1] - PATCH + 1, PATCH):
            src_p, e_p, g_p = (
                src[y : y + PATCH, x : x + PATCH],
                e[y : y + PATCH, x : x + PATCH],
                g[y : y + PATCH, x : x + PATCH],
            )
            e_error = float(np.mean(np.abs(src_p - e_p)))
            g_error = float(np.mean(np.abs(src_p - g_p)))
            if e_error < 5.0 and g_error < 30.0 and float(np.std(src_p)) > 12.0:
                candidates.append((e_error + g_error, y, x))
    if not candidates:
        raise RuntimeError(f"no clean patch at {timestamp}s")
    _, y, x = min(candidates)
    src_y, e_y, g_y = (
        yuv(image[y : y + PATCH, x : x + PATCH]) for image in (src, e, g)
    )
    e_scores.append(psnr(src_y, e_y))
    g_scores.append(psnr(src_y, g_y))
    diffs.append(np.mean(g_y, axis=(0, 1)) - np.mean(src_y, axis=(0, 1)))
    selected.append((timestamp, y, x))

print("selected", selected)
print("E_psnr", e_scores)
print("G_psnr", g_scores)
print(
    "E_mean",
    mean(e_scores),
    "G_mean",
    mean(g_scores),
    "delta_G_minus_E",
    mean(g_scores) - mean(e_scores),
)
print("YUV_mean_diffs", [tuple(float(value) for value in item) for item in diffs])
print("YUV_abs_max", max(abs(float(value)) for item in diffs for value in item))
