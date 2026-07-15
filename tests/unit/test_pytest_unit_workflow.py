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


def test_required_integration_job_uses_submodule_free_checkout() -> None:
    workflow = _load_workflow()
    checkout = workflow["jobs"]["integration-ci"]["steps"][0]

    assert checkout["uses"].startswith("actions/checkout@")
    assert "submodules" not in checkout.get("with", {})
    assert int(checkout["with"]["fetch-depth"]) == 1


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


def test_preflight_policy_enforces_workflow_display_name_guard() -> None:
    workflow = _load_workflow()

    preflight_scripts = [
        step.get("run", "") for step in workflow["jobs"]["preflight-policy"]["steps"]
    ]
    assert any(
        "tools/check_github_workflow_names.py" in run for run in preflight_scripts
    )

    ci_required_scripts = [
        step.get("run", "") for step in workflow["jobs"]["ci-required"]["steps"]
    ]
    assert not any(
        "tools/check_github_workflow_names.py" in run for run in ci_required_scripts
    )


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


def test_frontend_jobs_are_impact_aware_and_keep_stable_aggregator() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    static = jobs["frontend-static"]
    assert static["needs"] == "select-test-suites"
    assert static["if"] == "needs.select-test-suites.outputs.frontend_static == 'true'"
    assert any("npm run frontend:ci" in step.get("run", "") for step in static["steps"])

    browser = jobs["frontend-browser"]
    assert browser["needs"] == "select-test-suites"
    assert browser["strategy"]["fail-fast"] is False
    assert "frontend_browser_firefox" in browser["strategy"]["matrix"]["engine"]
    assert "@sha256:" in browser["container"]["image"]
    assert browser["env"]["HOME"] == "/root"
    assert not any(
        "playwright install" in step.get("run", "") for step in browser["steps"]
    )

    aggregator = jobs["test-frontend"]
    assert aggregator["if"] == "always()"
    assert aggregator["needs"] == [
        "select-test-suites",
        "frontend-static",
        "frontend-browser",
    ]
    assert not any("uses" in step for step in aggregator["steps"])
    script = "\n".join(step.get("run", "") for step in aggregator["steps"])
    assert "frontend-static was selected" in script
    assert "frontend-browser was selected" in script
    assert "skipped intentionally" in script


def test_playwright_package_and_container_versions_match() -> None:
    import json

    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    version = package["devDependencies"]["playwright"]
    assert version[0].isdigit(), "Playwright must be an exact dependency"
    image = _load_workflow()["jobs"]["frontend-browser"]["container"]["image"]
    assert f":v{version}-noble@sha256:" in image


def test_frontend_workflow_does_not_upload_dashboard_dist() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "dashboard-dist" not in workflow_text


def test_unit_fast_physically_ignores_heavy_collection_paths() -> None:
    command = _run_command("unit-fast", "Run selected unit suite")

    assert "python -m pytest tests/unit \\" in command
    assert "--ignore=tests/unit/workflows/temporal" in command
    assert "--ignore=tests/unit/api" in command
    assert "--ignore=tests/unit/api_service" in command
    assert (
        '-m "unit_fast and not provider_verification and not requires_credentials"'
        in command
    )
    assert "--junitxml=artifacts/pytest-unit-fast.xml" in command
    assert "full_backend" not in command
    unit_fast_steps = _load_workflow()["jobs"]["unit-fast"]["steps"]
    assert not any(
        (step.get("uses") or "").startswith("actions/setup-node@")
        or "npm run ui:build" in (step.get("run") or "")
        for step in unit_fast_steps
    )


def test_unit_workflow_keeps_api_and_temporal_ownership() -> None:
    api_command = _run_command("api-component", "Run API/component suite")
    temporal_command = _run_command("temporal-boundary", "Run Temporal boundary suite")

    assert "tests/unit/api tests/unit/api_service tests/component/api" in api_command
    assert (
        '-m "component and not temporal_boundary and not slow and not provider_verification and not requires_credentials"'
        in api_command
    )
    assert "--junitxml=artifacts/pytest-api-component.xml" in api_command

    assert "python -m pytest tests/unit/workflows/temporal" in temporal_command
    assert (
        '-m "temporal_boundary and not slow and not provider_verification and not requires_credentials"'
        in temporal_command
    )
    assert "--junitxml=artifacts/pytest-temporal-boundary.xml" in temporal_command


def test_unit_slow_has_separate_non_parallel_job_and_required_contract() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["unit-slow"]
    command = _run_command("unit-slow", "Run slow unit suite")

    assert job["if"] == "needs.select-test-suites.outputs.unit_slow == 'true'"
    assert job["timeout-minutes"] == 45
    assert (
        '-m "slow and not provider_verification and not requires_credentials and not integration"'
        in command
    )
    assert "-n " not in command
    assert "--junitxml=artifacts/pytest-unit-slow.xml" in command
    assert "unit-slow" in workflow["jobs"]["ci-required"]["needs"]


def test_full_backend_runs_shard_ownership_verifier() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["verify-test-shard-ownership"]

    assert job["if"] == "needs.select-test-suites.outputs.full_backend == 'true'"
    assert any(
        "tools/verify_test_shard_ownership.py" in step.get("run", "")
        for step in job["steps"]
    )


def test_reliability_job_runs_the_canonical_journey_suite() -> None:
    job = "reliability-journey-checkpoint-resume"
    command = _run_command(job, "Run hermetic checkpoint-resume reliability journey")

    assert "python -m pytest tests/integration/reliability" in command
    assert "-m reliability_journey" in command
    assert "skipping until #3145 lands" not in command

    diagnostics = _run_command(job, "Collect reliability journey diagnostics")
    assert "tests/integration/reliability/replays" in diagnostics


def test_preflight_policy_runs_status_token_audit() -> None:
    command = _run_command("preflight-policy", "Audit status tokens")

    assert "tools/audit_status_tokens.py --fail-on-unknown" in command

    ci_required_scripts = "\n".join(
        step.get("run", "")
        for step in _load_workflow()["jobs"]["ci-required"]["steps"]
    )
    assert "audit_status_tokens.py" not in ci_required_scripts


def test_required_unit_workflow_runs_for_merge_queue_candidates() -> None:
    workflow = _load_workflow()
    triggers = _workflow_triggers(workflow) or {}

    assert triggers.get("merge_group", {}).get("types") == ["checks_requested"]


def test_ci_required_is_pure_result_aggregator() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["ci-required"]

    assert job["if"] == "always()"
    assert int(job["timeout-minutes"]) <= 10

    # No checkout, no action-based setup, no submodules of any kind.
    for step in job["steps"]:
        assert "uses" not in step, f"ci-required must not use actions: {step.get('uses')}"

    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "actions/checkout" not in scripts
    assert "submodule" not in scripts
    assert "tools/" not in scripts
    assert "pytest" not in scripts

    for dependency in (
        "select-test-suites",
        "preflight-policy",
        "moonspec-projection",
        "unit-fast",
        "unit-slow",
        "api-component",
        "temporal-boundary",
        "integration-ci",
        "reliability-journey-checkpoint-resume",
        "verify-test-shard-ownership",
    ):
        assert dependency in job["needs"]


def test_ci_required_reports_all_failures_before_exiting() -> None:
    script = "\n".join(
        step.get("run", "")
        for step in _load_workflow()["jobs"]["ci-required"]["steps"]
    )

    # Accumulates failures instead of exiting on the first failing dependency.
    assert "failures=$((failures + 1))" in script
    assert 'if [[ "$failures" -gt 0 ]]; then' in script
    assert "set -uo pipefail" in script
    # Emits an annotation per failure rather than a single early exit.
    assert script.count("::error::") >= 3

    for name in (
        "select-test-suites",
        "preflight-policy",
        "moonspec-projection",
        "unit-fast",
        "unit-slow",
        "api-component",
        "temporal-boundary",
        "integration-ci",
        "reliability-journey-checkpoint-resume",
        "verify-test-shard-ownership",
    ):
        assert name in script


def test_preflight_policy_runs_in_parallel_and_owns_policy_guards() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["preflight-policy"]

    # Starts immediately, in parallel with backend test selection.
    assert "needs" not in job

    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert int(checkout["with"]["fetch-depth"]) == 1
    assert "submodules" not in checkout.get("with", {})

    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "./tools/check_terminology.sh" in scripts
    assert "tools/verify_workflow_terminology.py --mode all" in scripts
    assert "tools/check_removed_capability_semantics.py" in scripts
    assert "tools/status_domain_audit.py" in scripts
    assert "tools/audit_status_tokens.py --fail-on-unknown" in scripts
    assert "tools/check_github_workflow_names.py" in scripts
    assert (
        "tools/validate_agent_session_deployment_safety.py --changed-files-file"
        in scripts
    )


def test_unit_fast_no_longer_duplicates_policy_checks() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["unit-fast"]

    checkout = job["steps"][0]
    assert "submodules" not in checkout.get("with", {})

    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "check_terminology.sh" not in scripts
    assert "verify_workflow_terminology.py" not in scripts
    assert "validate_agent_session_deployment_safety.py" not in scripts


def test_selector_uses_shallow_submodule_free_checkout_and_shared_helper() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["select-test-suites"]

    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert int(checkout["with"]["fetch-depth"]) == 1
    assert "submodules" not in checkout.get("with", {})

    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "tools/ci/compute_changed_files.sh" in scripts
    assert "tools/select_test_suites.py" in scripts


def test_preflight_blocks_deployment_safety_when_changed_files_are_unknown() -> None:
    job = _load_workflow()["jobs"]["preflight-policy"]
    steps = {step["name"]: step for step in job["steps"]}

    compute = steps["Compute changed files"]
    assert compute["id"] == "changed-files"
    assert '>> "$GITHUB_OUTPUT"' in compute["run"]
    assert steps["Validate AgentSession deployment safety"]["if"] == (
        "steps.changed-files.outputs.resolution == 'known'"
    )
    assert steps["Block deployment safety validation on an unknown diff"]["if"] == (
        "steps.changed-files.outputs.resolution != 'known'"
    )


def test_unit_fast_initializes_moonspec_test_fixtures() -> None:
    job = _load_workflow()["jobs"]["unit-fast"]
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "git submodule update --init --depth 1 -- moonspec" in scripts


def test_moonspec_projection_initializes_only_moonspec_submodule() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["moonspec-projection"]

    checkout = job["steps"][0]
    assert "submodules" not in checkout.get("with", {})

    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "git submodule update --init --depth 1 -- moonspec" in scripts
    # Does not initialize Open WebUI or Omnigent.
    assert "open-webui" not in scripts
    assert "omnigent" not in scripts


def test_no_backend_ci_job_uses_recursive_submodules() -> None:
    assert "submodules: recursive" not in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_shared_changed_file_helper_covers_supported_events() -> None:
    helper = REPO_ROOT / "tools" / "ci" / "compute_changed_files.sh"
    assert helper.exists()

    text = helper.read_text(encoding="utf-8")
    assert "ensure_commit_available" in text
    assert "pull_request" in text
    assert "merge_group" in text
    assert "push" in text
    assert "resolution=unknown" in text
    assert "resolution=known" in text


def test_generated_contract_detector_uses_shared_helper() -> None:
    command = _run_command(
        "detect-openapi-contract-impact", "Detect OpenAPI-affecting changes"
    )

    assert "tools/ci/compute_changed_files.sh" in command
    assert "tools/check_openapi_affecting_changes.sh" in command
    assert "resolution=unknown" in command
