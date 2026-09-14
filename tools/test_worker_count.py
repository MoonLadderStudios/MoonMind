"""Choose pytest parallelism from the CPU and RAM actually available to tests."""

from __future__ import annotations

import math
import os
from pathlib import Path


def worker_count(*, cgroup: Path = Path("/sys/fs/cgroup")) -> int:
    cpus = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else (os.cpu_count() or 1)
    )
    memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    try:
        quota, period = (cgroup / "cpu.max").read_text().split()
        if quota != "max":
            cpus = min(cpus, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError, ZeroDivisionError):
        # Hosts without readable cgroup v2 CPU metadata use process affinity.
        pass
    for path in (cgroup / "memory.max", cgroup / "memory/memory.limit_in_bytes"):
        try:
            value = int(path.read_text().strip())
            if value > 0:
                memory = min(memory, value)
        except (OSError, ValueError):
            # Absent limits and "max" add no bound; retain the readable limits.
            pass
    # Importing application fixtures is expensive. Leave room for pytest's
    # controller and allocate at most one worker per remaining GiB.
    return max(1, min(cpus, (memory - 512 * 1024**2) // 1024**3))


if __name__ == "__main__":
    print(worker_count())
