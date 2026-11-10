"""Shared process/GPU sampling helpers for perf benchmarks."""
from __future__ import annotations
import subprocess
from typing import Any

def gpu_sample() -> dict[str, float]:
    try:
        values=subprocess.check_output(["nvidia-smi","--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder","--format=csv,noheader,nounits"],text=True,stderr=subprocess.DEVNULL).strip().split(',')
        return {"gpu":float(values[0]),"encoder":float(values[1]),"decoder":float(values[2])}
    except Exception:
        return {"gpu":0.0,"encoder":0.0,"decoder":0.0}
