from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pytest-integration-ci.yml"
UNIT_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"


def _load_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Missing workflow: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _load_unit_workflow() -> dict:
    assert UNIT_WORKFLOW_PATH.exists(), f"Missing workflow: {UNIT_WORKFLOW_PATH}"
    return yaml.safe_load(UNIT_WORKFLOW_PATH.read_text(encoding="utf-8"))


def _workflow_triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


def test_pytest_integration_ci_workflow_keeps_nightly_and_manual_triggers() -> None:
    workflow = _load_workflow()

    assert workflow["name"] == "Run Pytest Integration CI"

    triggers = _workflow_triggers(workflow)
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"] == [{"cron": "47 9 * * *"}]
    assert "push" not in triggers
    assert "pull_request" not in triggers


def test_required_pytest_unit_workflow_selects_integration_ci_for_prs() -> None:
    workflow = _load_unit_workflow()

    triggers = _workflow_triggers(workflow)
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]
    assert "workflow_dispatch" in triggers
    assert "schedule" in triggers

    jobs = workflow["jobs"]
    integration_job = jobs["integration-ci"]
    assert integration_job["needs"] == "select-test-suites"
    assert (
        integration_job["if"]
        == "needs.select-test-suites.outputs.integration_ci == 'true'"
    )
    assert any(
        "./tools/test_integration.sh" in step.get("run", "")
        for step in integration_job["steps"]
    )

    required_job = jobs["ci-required"]
    assert "integration-ci" in required_job["needs"]


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
