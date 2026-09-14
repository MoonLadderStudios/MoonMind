"""Fixed, privileged deployment operation; never executed in an agent image.

The trusted Docker owner runs this file from its own immutable image. A short
lived holder keeps systemd from collecting the slice between setup and launch.
No workspace, credentials, Docker socket, or caller-provided program is mounted.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time


def main() -> None:
    operation, leaf, *values = sys.argv[1:]
    if not re.fullmatch(r"moonmindcpu[0-9a-f]{24}\.slice", leaf):
        raise ValueError("invalid resource pool identity")
    root = Path("/host-cgroup") / leaf
    if operation == "observe":
        if not root.exists():
            print(json.dumps({"exists": False, "populated": False}))
            return
        events = dict(
            line.split() for line in (root / "cgroup.events").read_text().splitlines()
        )
        print(
            json.dumps(
                {
                    "exists": True,
                    "populated": events.get("populated") == "1",
                    "cpuMax": (root / "cpu.max").read_text().strip(),
                }
            )
        )
        return
    if operation != "configure" or len(values) != 1:
        raise ValueError("unsupported resource operation")
    cpu_millis = int(values[0])
    if not 1 <= cpu_millis <= 1024000:
        raise ValueError("invalid CPU pool limit")
    expected = f"{cpu_millis * 100} 100000"
    (root / "cpu.max").write_text(expected)
    actual = (root / "cpu.max").read_text().strip()
    if actual != expected:
        raise RuntimeError("CPU pool enforcement differs from reservation")
    print(json.dumps({"cpuMax": actual, "ready": True}), flush=True)
    # The owner removes this holder after the workload is objectively running.
    # Worker loss cannot leave an idle, permanently running helper.
    time.sleep(180)


if __name__ == "__main__":
    main()
