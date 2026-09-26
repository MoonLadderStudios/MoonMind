"""The standalone deploy controller must never depend on the application runtime.

Normal controller execution imports stdlib only. It must not import MoonMind
application modules nor require the API, DB, Temporal, artifact service,
provider manager, Omnigent, or an LLM (issue #4500, REQ-01).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CONTROLLER_DIR = Path(__file__).resolve().parents[4] / "deploy" / "controller"

FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+"
    r"(moonmind|api_service|temporalio|omnigent|codex|claude_code|openai|anthropic)\b",
    re.MULTILINE,
)


def _controller_sources():
    return sorted(CONTROLLER_DIR.glob("*.py"))


def test_controller_package_exists():
    assert CONTROLLER_DIR.is_dir(), "deploy/controller/ is the standalone owner (REQ-01)"
    assert _controller_sources(), "controller package must contain modules"


def test_no_application_imports_in_controller_sources():
    offenders = {}
    for path in _controller_sources():
        if path.name.startswith("test_"):
            continue
        text = path.read_text()
        hits = FORBIDDEN_IMPORT_RE.findall(text)
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"controller must not import application modules: {offenders}"


def test_controller_modules_import_without_application_packages(tmp_path, monkeypatch):
    for name in ("redact", "mounts", "lock", "record", "engine"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(CONTROLLER_DIR))
    # Block every application package: any such import must fail loudly.
    # monkeypatch restores already-imported packages afterwards; popping them
    # would orphan their loaded submodules for later tests in this process.
    for blocked in ("moonmind", "api_service", "temporalio", "omnigent"):
        monkeypatch.setitem(sys.modules, blocked, None)
    for name in ("redact", "mounts", "lock", "record", "engine"):
        sys.modules.pop(name, None)
        __import__(name)
