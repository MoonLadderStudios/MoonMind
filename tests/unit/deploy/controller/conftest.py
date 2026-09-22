"""Unit tests for the standalone controller helpers (issue #4500).

The controller lives in deploy/controller/ and is stdlib-only. These tests
load its modules by file path so they exercise the shipped code without an
installed package.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

CONTROLLER_DIR = Path(__file__).resolve().parents[4] / "deploy" / "controller"


def load(name):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, CONTROLLER_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def controller_path(monkeypatch):
    monkeypatch.syspath_prepend(str(CONTROLLER_DIR))
    for name in ("redact", "mounts", "lock", "record", "engine", "server"):
        sys.modules.pop(name, None)
    return CONTROLLER_DIR
