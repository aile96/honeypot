"""Host resource checks for lab startup."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping


def _available_memory_mb() -> int:
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    if Path("/usr/sbin/sysctl").exists() or Path("/sbin/sysctl").exists():
        completed = subprocess.run(["sysctl", "-n", "hw.memsize"], text=True, capture_output=True, check=False)
        if completed.returncode == 0 and completed.stdout.strip().isdigit():
            return int(completed.stdout.strip()) // 1024 // 1024
    return 0


def _cpu_count() -> int:
    return os.cpu_count() or 0


def check_resources(config: Mapping[str, object]) -> None:
    required_mem = int(config.get("REQUIRED_AVAIL_MEM_MB", 0))
    required_cpu = int(config.get("REQUIRED_CPUS", 0))
    available_mem = _available_memory_mb()
    cpus = _cpu_count()
    if required_mem and available_mem and available_mem < required_mem:
        raise RuntimeError(f"Not enough available RAM: {available_mem} MB available, need {required_mem} MB")
    if required_mem and not available_mem:
        raise RuntimeError(f"Unable to determine available RAM; need {required_mem} MB")
    if required_cpu and cpus < required_cpu:
        raise RuntimeError(f"Not enough CPU cores: {cpus} available, need {required_cpu}")
