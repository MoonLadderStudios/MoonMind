#!/usr/bin/env python3
"""GitHub batch entrypoint for the shared portable fan-out engine."""

from __future__ import annotations

import runpy
from pathlib import Path


_EXECUTE = __name__ == "__main__"
_ENGINE_PATH = Path(__file__).resolve().parents[2] / "_shared" / "batch_workflows.py"
if not _ENGINE_PATH.is_file():
    raise RuntimeError(
        f"resolved Skill snapshot is missing the batch fan-out engine: {_ENGINE_PATH}"
    )
if _EXECUTE:
    runpy.run_path(str(_ENGINE_PATH), run_name="__main__")
else:
    globals().update(runpy.run_path(str(_ENGINE_PATH)))
