"""Regression coverage for MM-848 conftest import boundaries."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_fast_unit_collection_keeps_heavy_modules_unloaded() -> None:
    if os.environ.get("MM848_IMPORT_PROBE") == "1":
        assert "api_service.db.models" not in sys.modules
        assert "moonmind.workflows.temporal.client" not in sys.modules
        return

    env = os.environ.copy()
    env["MM848_IMPORT_PROBE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(Path(__file__).resolve())],
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
