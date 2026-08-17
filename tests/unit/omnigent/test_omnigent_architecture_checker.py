"""Tests for the Omnigent architecture-boundary checker.

Proves (MoonLadderStudios/MoonMind#3711) that the current layered packages pass
the checker and that each enforced rule actually fires on a violating fixture, so
the gate cannot silently rot into a no-op.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = REPO_ROOT / "tools" / "check_omnigent_architecture.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_omnigent_architecture", CHECKER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def test_current_omnigent_layers_pass_the_checker() -> None:
    findings = checker.collect_findings()
    assert findings == [], "\n".join(f.format() for f in findings)


def _analyze_source(tmp_path: Path, layer: str, source: str, monkeypatch) -> list:
    """Write ``source`` as a module in ``layer`` under a temp Omnigent root."""

    omnigent_root = tmp_path / "moonmind" / "omnigent"
    (omnigent_root / layer).mkdir(parents=True, exist_ok=True)
    module_path = omnigent_root / layer / "sample.py"
    module_path.write_text(source)
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(checker, "OMNIGENT_ROOT", omnigent_root)
    return checker.analyze_module(module_path)


def test_forbidden_infra_import_in_domain_is_flagged(tmp_path, monkeypatch) -> None:
    findings = _analyze_source(
        tmp_path, "domain", "import sqlalchemy\n", monkeypatch
    )
    rules = {f.rule for f in findings}
    assert "forbidden-infra-import" in rules


def test_application_importing_adapters_is_flagged(tmp_path, monkeypatch) -> None:
    findings = _analyze_source(
        tmp_path,
        "application",
        "from moonmind.omnigent.adapters.persistence import memory\n",
        monkeypatch,
    )
    rules = {f.rule for f in findings}
    assert "forbidden-layer-import" in rules


def test_fastapi_outside_facade_is_flagged(tmp_path, monkeypatch) -> None:
    findings = _analyze_source(
        tmp_path, "application", "import fastapi\n", monkeypatch
    )
    rules = {f.rule for f in findings}
    assert "fastapi-outside-facade" in rules


def test_env_read_outside_adapter_is_flagged(tmp_path, monkeypatch) -> None:
    findings = _analyze_source(
        tmp_path,
        "application",
        "import os\n\n\ndef f():\n    return os.environ.get('X')\n",
        monkeypatch,
    )
    rules = {f.rule for f in findings}
    assert "env-read-outside-adapter" in rules


def test_duplicate_vocabulary_outside_domain_is_flagged(tmp_path, monkeypatch) -> None:
    findings = _analyze_source(
        tmp_path,
        "adapters",
        "def coalesce_session_status(value):\n    return value\n",
        monkeypatch,
    )
    rules = {f.rule for f in findings}
    assert "duplicate-vocabulary" in rules


def test_ports_may_import_domain(tmp_path, monkeypatch) -> None:
    findings = _analyze_source(
        tmp_path,
        "ports",
        "from moonmind.omnigent.domain.session_state import SessionStatus\n",
        monkeypatch,
    )
    assert findings == []


def test_adapters_may_import_infrastructure(tmp_path, monkeypatch) -> None:
    findings = _analyze_source(
        tmp_path, "adapters", "import sqlalchemy\nimport httpx\n", monkeypatch
    )
    assert findings == []


def test_layer_cycle_detection() -> None:
    graph = {
        "domain": set(),
        "ports": {"application"},
        "application": {"ports"},
        "adapters": set(),
        "ui_facade": set(),
        "evidence": set(),
    }
    findings = checker.detect_layer_cycles(graph)
    assert any(f.rule == "layer-cycle" for f in findings)


def test_main_exit_code_clean() -> None:
    assert checker.main([]) == 0
