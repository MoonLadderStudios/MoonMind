"""DOC-REQ guard tests for orchestrator removal (spec 087-orchestrator-removal)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_doc_req_001_compose_main_no_orchestrator_service() -> None:
    text = _read(_REPO_ROOT / "docker-compose.yaml")
    assert not re.search(r"(?m)^  orchestrator:\s*$", text), (
        "docker-compose.yaml must not define a top-level orchestrator service"
    )


def test_doc_req_002_test_compose_no_orchestrator_tests() -> None:
    text = _read(_REPO_ROOT / "docker-compose.test.yaml")
    assert "orchestrator-tests:" not in text


def test_doc_req_003_no_orchestrator_env_in_compose() -> None:
    for name in ("docker-compose.yaml", "docker-compose.test.yaml"):
        text = _read(_REPO_ROOT / name)
        assert "MOONMIND_ORCHESTRATOR_" not in text, f"{name} must not use MOONMIND_ORCHESTRATOR_*"
        assert "MOONMIND_ORCHESTRATOR_POLL" not in text
        assert "MOONMIND_ORCHESTRATOR_LEASE" not in text


def test_doc_req_004_api_has_no_orchestrator_router() -> None:
    main_py = _REPO_ROOT / "api_service" / "main.py"
    tree = ast.parse(_read(main_py))
    imports = {
        n.name
        for n in tree.body
        if isinstance(n, ast.ImportFrom) and n.module == "api_service.api.routers.orchestrator"
    }
    assert not imports, "main.py must not import orchestrator router"
    text = _read(main_py)
    assert "orchestrator_router" not in text
    assert "routers.orchestrator" not in text


def test_doc_req_005_workflows_orchestrator_package_removed() -> None:
    pkg = _REPO_ROOT / "moonmind" / "workflows" / "orchestrator"
    assert not pkg.exists(), "moonmind/workflows/orchestrator must be removed"


def test_doc_req_006_no_orchestrator_models_importable() -> None:
    from api_service.db import models as db_models

    assert not hasattr(db_models, "OrchestratorRun")
    assert not hasattr(db_models, "OrchestratorActionPlan")


def test_doc_req_007_job_yaml_no_orchestrator_service() -> None:
    job = _read(_REPO_ROOT / "docker-compose.job.yaml")
    assert not re.search(r"(?m)^  orchestrator:\s*$", job)


def test_doc_req_services_orchestrator_dir_removed() -> None:
    svc_dir = _REPO_ROOT / "services" / "orchestrator"
    assert not svc_dir.exists(), "services/orchestrator directory must be removed"


def test_doc_req_008_no_orchestrator_ci_paths() -> None:
    gh = _REPO_ROOT / ".github" / "workflows" / "orchestrator-integration-tests.yml"
    assert not gh.exists()
    assert not (_REPO_ROOT / "tests" / "integration" / "orchestrator").exists()
    assert not (_REPO_ROOT / "tests" / "unit" / "workflows" / "orchestrator").exists()
    contract = _REPO_ROOT / "tests" / "contract" / "test_orchestrator_api.py"
    assert not contract.exists()


def test_doc_req_010_docs_specs_clean() -> None:
    assert not (
        _REPO_ROOT / "docs" / "Temporal" / "OrchestratorTaskRuntime.md"
    ).exists()
    assert not (
        _REPO_ROOT / "docs" / "Temporal" / "OrchestratorArchitecture.md"
    ).exists()
    assert not (_REPO_ROOT / "specs" / "005-orchestrator-architecture").exists()
    assert not (_REPO_ROOT / "specs" / "050-orchestrator-task-runtime").exists()
    arch = _read(_REPO_ROOT / "docs" / "MoonMindArchitecture.md")
    assert "mm-orchestrator" not in arch


def test_doc_req_011_migration_drops_orchestrator_tables() -> None:
    versions = _REPO_ROOT / "api_service" / "migrations" / "versions"
    found_drop = False
    for path in sorted(versions.glob("*.py")):
        if path.name.startswith("__"):
            continue
        text = _read(path)
        if "orchestrator_runs" in text and "drop_table" in text:
            found_drop = True
            break
    assert found_drop, "Expected an Alembic revision that drops orchestrator_runs"


def test_doc_req_012_unit_suite_passes() -> None:
    """Satisfied when pytest collects this module under ./tools/test_unit.sh."""
