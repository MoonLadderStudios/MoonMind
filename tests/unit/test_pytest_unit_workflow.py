from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"
STANDALONE_INTEGRATION_PATH = (
    REPO_ROOT / ".github" / "workflows" / "pytest-integration-ci.yml"
)


def _load_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Missing workflow: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _workflow_triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


def _run_command(job_name: str, step_name: str) -> str:
    workflow = _load_workflow()
    steps = workflow["jobs"][job_name]["steps"]
    command = next(
        (
            step["run"]
            for step in steps
            if step.get("name") == step_name and "run" in step
        ),
        None,
    )
    assert command, f"Step {step_name!r} with run command not found"
    return command


def test_ci_test_suite_is_the_only_integration_ci_workflow() -> None:
    workflow = _load_workflow()

    assert workflow["name"] == "CI / Test Suite"
    assert not STANDALONE_INTEGRATION_PATH.exists()


def test_ci_test_suite_selects_integration_ci_for_required_events() -> None:
    workflow = _load_workflow()

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


def test_required_integration_job_checks_out_moonspec_submodule() -> None:
    workflow = _load_workflow()
    checkout = workflow["jobs"]["integration-ci"]["steps"][0]

    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["submodules"] == "recursive"


def test_required_integration_job_uploads_failure_diagnostics() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["integration-ci"]

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


def test_ci_required_enforces_workflow_display_name_guard() -> None:
    workflow = _load_workflow()
    run_scripts = [
        step.get("run", "") for step in workflow["jobs"]["ci-required"]["steps"]
    ]

    assert any("tools/check_github_workflow_names.py" in run for run in run_scripts)


def test_generated_contracts_use_cheap_detector_and_stable_required_status() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    detector_job = jobs["detect-openapi-contract-impact"]
    assert (
        detector_job["outputs"]["run_check"] == "${{ steps.detect.outputs.run_check }}"
    )
    detector_steps = detector_job["steps"]
    detector_checkout = detector_steps[0]
    assert detector_checkout["uses"].startswith("actions/checkout@")
    assert int(detector_checkout["with"]["fetch-depth"]) == 1
    assert any(
        "tools/check_openapi_affecting_changes.sh" in (step.get("run") or "")
        for step in detector_steps
    )
    assert not any(
        (step.get("uses") or "").startswith("actions/setup-node@")
        or (step.get("uses") or "").startswith("actions/setup-python@")
        or "npm ci" in (step.get("run") or "")
        or "uv pip install" in (step.get("run") or "")
        or "apt-get install" in (step.get("run") or "")
        for step in detector_steps
    )

    contract_job = jobs["run-generated-contracts"]
    assert contract_job["needs"] == "detect-openapi-contract-impact"
    assert (
        contract_job["if"]
        == "needs.detect-openapi-contract-impact.outputs.run_check == 'true'"
    )
    contract_steps = contract_job["steps"]
    assert any(
        (step.get("uses") or "").startswith("actions/setup-node@")
        for step in contract_steps
    )
    assert any(
        (step.get("uses") or "").startswith("actions/setup-python@")
        for step in contract_steps
    )
    assert any(
        "npm run contracts:check" in (step.get("run") or "") for step in contract_steps
    )
    assert not any(step.get("if") for step in contract_steps)

    required_job = jobs["check-generated-contracts"]
    assert required_job["needs"] == [
        "detect-openapi-contract-impact",
        "run-generated-contracts",
    ]
    assert required_job["if"] == "always()"
    required_script = "\n".join(
        (step.get("run") or "") for step in required_job["steps"] if "run" in step
    )
    assert "needs.detect-openapi-contract-impact.result" in required_script
    assert "needs.detect-openapi-contract-impact.outputs.run_check" in required_script
    assert "needs.run-generated-contracts.result" in required_script
    assert "Generated contract verification passed." in required_script
    assert "skipped intentionally" in required_script


def test_unit_fast_physically_ignores_heavy_collection_paths() -> None:
    command = _run_command("unit-fast", "Run selected unit suite")

    assert "python -m pytest tests/unit \\" in command
    assert "--ignore=tests/unit/workflows/temporal" in command
    assert "--ignore=tests/unit/api" in command
    assert "--ignore=tests/unit/api_service" in command
    assert '-m "not temporal_boundary and not component and not slow"' in command
    assert "--junitxml=artifacts/pytest-unit-fast.xml" in command


def test_unit_workflow_keeps_api_and_temporal_ownership() -> None:
    api_command = _run_command("api-component", "Run API/component suite")
    temporal_command = _run_command("temporal-boundary", "Run Temporal boundary suite")

    assert "tests/unit/api tests/unit/api_service tests/component/api" in api_command
    assert '-m "not temporal_boundary and not slow"' in api_command
    assert "component and not temporal_boundary" not in api_command
    assert "--junitxml=artifacts/pytest-api-component.xml" in api_command

    assert "python -m pytest tests/unit/workflows/temporal" in temporal_command
    assert '-m "not slow"' in temporal_command
    assert "temporal_boundary and not slow" not in temporal_command
    assert "--junitxml=artifacts/pytest-temporal-boundary.xml" in temporal_command


def test_reliability_job_runs_the_canonical_journey_suite() -> None:
    job = "reliability-journey-checkpoint-resume"
    command = _run_command(job, "Run hermetic checkpoint-resume reliability journey")

    assert "python -m pytest tests/integration/reliability" in command
    assert "-m reliability_journey" in command
    assert "skipping until #3145 lands" not in command

    diagnostics = _run_command(job, "Collect reliability journey diagnostics")
    assert "tests/integration/reliability/replays" in diagnostics


def test_required_unit_workflow_runs_status_token_audit() -> None:
    command = _run_command("ci-required", "Audit status token domains")

    assert "tools/audit_status_tokens.py --fail-on-unknown" in command


def test_required_unit_workflow_runs_for_merge_queue_candidates() -> None:
    workflow = _load_workflow()
    triggers = _workflow_triggers(workflow) or {}

    assert triggers.get("merge_group", {}).get("types") == ["checks_requested"]


def test_required_unit_workflow_checks_out_moonspec_submodule() -> None:
    workflow = _load_workflow()
    checkout = workflow["jobs"]["ci-required"]["steps"][0]

    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["submodules"] == "recursive"
