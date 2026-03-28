"""Regression tests for tools/verify_vite_manifest.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import importlib.util

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_verify_module():
    path = REPO_ROOT / "tools" / "verify_vite_manifest.py"
    spec = importlib.util.spec_from_file_location("verify_vite_manifest", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_expected_entrypoints_matches_vite_config() -> None:
    mod = _load_verify_module()
    names = mod.expected_entrypoints()
    assert "tasks-list" in names
    assert "secrets" in names
    assert "workers" in names
    assert "task-detail" in names


def test_verify_vite_manifest_script_succeeds_on_repo() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "verify_vite_manifest.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
