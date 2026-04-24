from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pytest-integration-ci.yml"

def _load_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Missing workflow: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

def test_required_pytest_integration_ci_workflow_exists_and_has_expected_triggers() -> None:
    workflow = _load_workflow()

    assert workflow["name"] == "Run Pytest Integration CI"

    triggers = workflow["on"]
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]

def test_required_pytest_integration_ci_workflow_runs_hermetic_runner_and_uploads_failure_diagnostics() -> None:
    workflow = _load_workflow()

    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert "pytest-integration-ci" in concurrency["group"]

    job = workflow["jobs"]["test-integration-ci"]
    assert job["runs-on"] == "ubuntu-latest"

    steps = job["steps"]
    run_steps = [step for step in steps if "run" in step]
    uses_steps = [step for step in steps if "uses" in step]

    assert any("./tools/test_integration.sh" in step["run"] for step in run_steps)
    assert any(
        "diagnostics-status.txt" in step["run"]
        and "docker-compose" in step["run"]
        and "logs --no-color" in step["run"]
        for step in run_steps
    )
    assert all(step["name"] != "Verify docker compose availability" for step in steps)
    assert any(step.get("if") == "failure()" for step in run_steps)
    assert any(
        step["uses"].startswith("actions/upload-artifact@")
        and step.get("if") == "failure()"
        for step in uses_steps
    )
