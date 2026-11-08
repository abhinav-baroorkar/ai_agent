"""RAM and GPU memory snapshots. Never raises: missing GPU just gives None."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

import psutil

_MB = 1024 * 1024


@dataclass(frozen=True)
class ResourceSnapshot:
    ram_used_mb: float
    ram_total_mb: float
    process_rss_mb: float
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _vram_mb() -> tuple[float | None, float | None]:
    """GPU 0 memory via NVML. Jetson has no NVML (shared RAM), so this returns None there."""
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            info = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0))
            return float(info.used) / _MB, float(info.total) / _MB
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None, None


def snapshot_resources(include_gpu: bool = True) -> ResourceSnapshot:
    vm = psutil.virtual_memory()
    rss = psutil.Process(os.getpid()).memory_info().rss
    used, total = _vram_mb() if include_gpu else (None, None)
    return ResourceSnapshot(
        ram_used_mb=round((vm.total - vm.available) / _MB, 1),
        ram_total_mb=round(vm.total / _MB, 1),
        process_rss_mb=round(rss / _MB, 1),
        vram_used_mb=None if used is None else round(used, 1),
        vram_total_mb=None if total is None else round(total, 1),
    )
