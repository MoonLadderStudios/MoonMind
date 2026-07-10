from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"


def _load_workflow() -> dict:
    if not WORKFLOW_PATH.exists():
        pytest.skip(f"Workflow file not found at {WORKFLOW_PATH}")
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _run_command(job_name: str, step_name: str) -> str:
    workflow = _load_workflow()
    steps = workflow["jobs"][job_name]["steps"]
    command = next(
        (step["run"] for step in steps if step.get("name") == step_name and "run" in step),
        None,
    )
    assert command, f"Step {step_name!r} with run command not found"
    return command


def test_unit_fast_physically_ignores_heavy_collection_paths() -> None:
    command = _run_command("unit-fast", "Run selected unit suite")

    assert "python -m pytest tests/unit \\" in command
    assert "--ignore=tests/unit/workflows/temporal" in command
    assert "--ignore=tests/unit/api" in command
    assert "--ignore=tests/unit/api_service" in command
    assert '-m "not temporal_boundary and not component and not slow"' in command
    assert "--junitxml=artifacts/pytest-unit-fast.xml" in command


def test_unit_workflow_keeps_api_and_temporal_boundary_ownership() -> None:
    api_command = _run_command("api-component", "Run API/component suite")
    temporal_command = _run_command("temporal-boundary", "Run Temporal boundary suite")

    assert "tests/unit/api tests/unit/api_service tests/component/api" in api_command
    assert '-m "not temporal_boundary and not slow"' in api_command
    assert "component and not temporal_boundary" not in api_command
    assert "--junitxml=artifacts/pytest-api-component.xml" in api_command

    assert "python -m pytest tests/unit/workflows/temporal" in temporal_command
    assert '-m "temporal_boundary and not slow"' in temporal_command
    assert "--junitxml=artifacts/pytest-temporal-boundary.xml" in temporal_command


def test_required_unit_workflow_runs_status_token_audit() -> None:
    command = _run_command("ci-required", "Audit status token domains")

    assert "tools/audit_status_tokens.py --fail-on-unknown" in command


def test_required_unit_workflow_runs_for_merge_queue_candidates() -> None:
    workflow = _load_workflow()
    triggers = workflow.get("on", workflow.get(True)) or {}

    assert triggers.get("merge_group", {}).get("types") == ["checks_requested"]


def test_required_unit_workflow_checks_out_moonspec_submodule() -> None:
    workflow = _load_workflow()
    checkout = workflow["jobs"]["ci-required"]["steps"][0]

    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["submodules"] == "recursive"
