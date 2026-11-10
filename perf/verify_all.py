"""Fast pre-commit checks for the refactored renderer."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import json


ROOT = pathlib.Path(__file__).resolve().parents[1]


def source_files() -> list[pathlib.Path]:
    return [*ROOT.glob("DMR/**/*.py"), *ROOT.glob("perf/*.py")]


def probe(path: pathlib.Path) -> dict[str, str]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=r_frame_rate,avg_frame_rate,nb_frames,duration,bit_rate", "-of", "json", str(path)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return {key: str(value) for key, value in stream.items()}


def main() -> int:
    failures: list[str] = []
    test = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT)
    if test.returncode:
        failures.append("pytest")
    forbidden = re.compile(r"D:\\DanmakuRender(?!-perf)")
    for path in source_files():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if forbidden.search(line):
                failures.append(f"external path: {path}")
    cache_refs = []
    for path in source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "emoticon-dir" in text:
            cache_refs.append((path, text))
    for path, text in cache_refs:
        if "emoji_pack" not in text and path.name != "emoji_render_mt.py":
            failures.append(f"emoticon cache path: {path}")
    renderer = (ROOT / "DMR" / "Render" / "gpu_render.py").read_text(encoding="utf-8")
    if renderer.count("for packet in encoder.Encode(") != 1:
        failures.append("gpu_render must have one encoding loop")
    if "framerate=frame_rate" in renderer or "reference_fps" in renderer:
        failures.append("stale encoder frame-rate workaround")
    for candidate, expected_fps in ((ROOT / "perf" / "tmp" / "G_25_refactored.mp4", 25),
                                    (ROOT / "perf" / "tmp" / "G_60_refactored.mp4", 60)):
        if not candidate.exists():
            continue
        try:
            metadata = probe(candidate)
            bitrate = int(float(metadata["bit_rate"]))
            fps = float(metadata["avg_frame_rate"].split("/")[0]) / float(metadata["avg_frame_rate"].split("/")[1])
            if bitrate > 4_400_000 or abs(fps - expected_fps) > 0.1:
                failures.append(f"output limit/fps: {candidate.name}")
        except (KeyError, ValueError, subprocess.CalledProcessError, ZeroDivisionError) as error:
            failures.append(f"ffprobe: {candidate.name}: {error}")
    if failures:
        print("verify_all failed:", *failures, sep="\n- ")
        return 1
    print("verify_all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
