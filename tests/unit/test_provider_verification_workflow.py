from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "provider-verification.yml"


def _load_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Missing workflow: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_provider_verification_workflow_exists_and_is_not_pr_required() -> None:
    workflow = _load_workflow()

    assert workflow["name"] == "Run Provider Verification"

    triggers = workflow["on"]
    assert "push" not in triggers
    assert "pull_request" not in triggers
    assert triggers["workflow_dispatch"] is None
    assert triggers["schedule"] == [{"cron": "0 6 * * *"}]


def test_provider_verification_workflow_runs_jules_provider_suite_in_protected_environment() -> None:
    workflow = _load_workflow()

    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert "provider-verification" in concurrency["group"]

    job = workflow["jobs"]["provider-verification"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["environment"] == "provider-verification"

    job_env = job["env"]
    assert job_env["JULES_API_KEY"] == "${{ secrets.JULES_API_KEY }}"
    assert job_env["JULES_API_URL"] == "${{ vars.JULES_API_URL }}"
    assert job_env["MOONMIND_FORCE_LOCAL_TESTS"] == "1"

    steps = job["steps"]
    run_steps = [step for step in steps if "run" in step]
    uses_steps = [step for step in steps if "uses" in step]

    assert any("./tools/test_jules_provider.sh" in step["run"] for step in run_steps)
    assert any(
        "diagnostics-status.txt" in step["run"]
        and "docker-compose" in step["run"]
        and "logs --no-color" in step["run"]
        for step in run_steps
    )
    assert any(step.get("if") == "failure()" for step in run_steps)
    assert any(
        step["uses"] == "actions/upload-artifact@v4"
        and step.get("if") == "failure()"
        for step in uses_steps
    )
