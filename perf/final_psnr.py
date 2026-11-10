"""Compare clean 128px patches of final E/G outputs with the source."""

from pathlib import Path
import subprocess
import tempfile
from statistics import mean

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "perf/samples/final_2h/原始/少年不太冷_11月9日14点20分.flv"
E_VIDEO = ROOT / "perf/tmp/cpu_telemetry_v2/E_final_2h_v2.mp4"
G_VIDEO = ROOT / "perf/tmp/cpu_telemetry_v2/G_final_2h_v2.mp4"
TIMES = (1800.0, 3600.0, 5400.0)
PATCH = 128


def frame(path: Path, seconds: float) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        target = Path(handle.name)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(seconds), "-i", str(path),
             "-frames:v", "1", str(target)], check=True
        )
        return np.asarray(Image.open(target).convert("RGB"), dtype=np.float32)
    finally:
        target.unlink(missing_ok=True)


def yuv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return np.stack((0.299 * r + 0.587 * g + 0.114 * b,
                     -0.168736 * r - 0.331264 * g + 0.5 * b,
                     0.5 * r - 0.418688 * g - 0.081312 * b), axis=-1)


def psnr(a, b):
    mse = float(np.mean((a - b) ** 2))
    return 99.0 if mse == 0 else 10 * np.log10(255 * 255 / mse)


e_scores, g_scores, diffs, selected = [], [], [], []
for timestamp in TIMES:
    src, old, gpu = (frame(path, timestamp) for path in (SOURCE, E_VIDEO, G_VIDEO))
    candidates = []
    for y in range(0, src.shape[0] - PATCH + 1, PATCH):
        for x in range(0, src.shape[1] - PATCH + 1, PATCH):
            src_p, old_p, gpu_p = (image[y:y + PATCH, x:x + PATCH] for image in (src, old, gpu))
            old_error = float(np.mean(np.abs(src_p - old_p)))
            gpu_error = float(np.mean(np.abs(src_p - gpu_p)))
            if old_error < 6 and gpu_error < 30 and float(np.std(src_p)) > 12:
                candidates.append((old_error + gpu_error, y, x))
    if not candidates:
        raise RuntimeError(f"no clean patch at {timestamp}s")
    _, y, x = min(candidates)
    src_y, old_y, gpu_y = (yuv(image[y:y + PATCH, x:x + PATCH]) for image in (src, old, gpu))
    e_scores.append(psnr(src_y, old_y))
    g_scores.append(psnr(src_y, gpu_y))
    diffs.append(np.mean(gpu_y, axis=(0, 1)) - np.mean(src_y, axis=(0, 1)))
    selected.append((timestamp, y, x))

print({"selected": selected, "E_psnr": e_scores, "G_psnr": g_scores,
       "E_mean": mean(e_scores), "G_mean": mean(g_scores),
       "G_minus_E": mean(g_scores) - mean(e_scores),
       "YUV_mean_diffs": [tuple(float(v) for v in item) for item in diffs],
       "YUV_abs_max": max(abs(float(v)) for item in diffs for v in item)})
