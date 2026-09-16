from __future__ import annotations

from pathlib import Path

import pytest
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
    command = _run_command("backend-matrix", "Run selected unit suite")

    assert "python -m pytest tests/unit \\" in command
    assert "--ignore=tests/unit/workflows/temporal" in command
    assert "--ignore=tests/unit/api" in command
    assert "--ignore=tests/unit/api_service" in command
    assert (
        '-m "unit_fast and not provider_verification and not requires_credentials"'
        in command
    )
    assert "--junitxml=artifacts/pytest-backend-unit-fast.xml" in command
    assert "full_backend" not in command
    unit_fast_steps = _load_workflow()["jobs"]["backend-matrix"]["steps"]
    assert not any(
        (step.get("uses") or "").startswith("actions/setup-node@")
        or "npm run ui:build" in (step.get("run") or "")
        for step in unit_fast_steps
    )


def test_unit_workflow_keeps_api_and_temporal_ownership() -> None:
    api_command = _run_command("backend-matrix", "Run API/component suite")
    temporal_command = _run_command("backend-matrix", "Run Temporal boundary suite")

    assert "tests/unit/api tests/unit/api_service tests/component/api" in api_command
    assert (
        '-m "component and not temporal_boundary and not slow and not provider_verification and not requires_credentials"'
        in api_command
    )
    assert "--junitxml=artifacts/pytest-backend-api-component.xml" in api_command

    assert "python -m pytest tests/unit/workflows/temporal" in temporal_command
    assert (
        '-m "temporal_boundary and not slow and not provider_verification and not requires_credentials"'
        in temporal_command
    )
    assert "--junitxml=artifacts/pytest-backend-temporal-boundary.xml" in temporal_command


def test_parallel_shards_bound_hung_tests_and_spread_large_modules() -> None:
    unit_fast = _run_command("backend-matrix", "Run selected unit suite")
    api_command = _run_command("backend-matrix", "Run API/component suite")
    temporal_command = _run_command("backend-matrix", "Run Temporal boundary suite")
    reliability_command = _run_command("backend-matrix", "Run hermetic reliability shard")

    for command in (unit_fast, api_command, temporal_command):
        assert "-n auto" in command
        # A hung test must fail its own shard instead of running to the job
        # timeout; faulthandler alone only dumps stacks.
        assert "--timeout 600" in command
    # The API shard's 400-test router modules dominate a per-file distribution,
    # so component tests are distributed per test.
    assert "--dist load " in api_command or api_command.rstrip().endswith("--dist load")
    assert "--dist loadfile" not in api_command
    assert "--dist loadfile" in temporal_command
    # Reliability shards run serially with their own short per-test bound
    # (MoonLadderStudios/MoonMind#4369): a hung journey fails in 300s
    # instead of holding a shard for the serial-era 600s.
    assert "-n auto" not in reliability_command
    assert "--timeout 300" in reliability_command
    assert "--timeout 600" not in reliability_command
    # ... under an 8-minute hard step ceiling that preserves the original
    # pytest outcome for the evidence hook.
    assert "timeout 480s python -m pytest" in reliability_command
    assert "status=${PIPESTATUS[0]}" in reliability_command


def test_deterministic_conformance_is_selection_gated() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["omnigent-deterministic-conformance"]

    assert job["needs"] == "select-test-suites"
    assert (
        job["if"]
        == "needs.select-test-suites.outputs.omnigent_conformance == 'true'"
    )
    assert "omnigent_conformance" in workflow["jobs"]["select-test-suites"]["outputs"]


def test_image_building_jobs_share_a_layer_cache() -> None:
    workflow = _load_workflow()
    integration_steps = workflow["jobs"]["integration-ci"]["steps"]
    build = next(
        step
        for step in integration_steps
        if (step.get("uses") or "").startswith("docker/build-push-action@")
    )
    assert build["with"]["target"] == "test-runtime"
    assert build["with"]["load"] is True
    assert build["with"]["cache-from"].startswith("type=gha,")
    assert build["with"]["cache-to"].startswith("type=gha,")
    run_step = next(
        step for step in integration_steps if step.get("name") == "Run hermetic integration CI suite"
    )
    assert run_step["env"]["MOONMIND_PYTHON_TEST_IMAGE"] == build["with"]["tags"]

    exact_build = next(
        step
        for step in workflow["jobs"]["omnigent-exact-artifact"]["steps"]
        if (step.get("uses") or "").startswith("docker/build-push-action@")
    )
    assert exact_build["with"]["cache-from"].startswith("type=gha,")
    assert exact_build["with"]["cache-to"].startswith("type=gha,")


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


def test_shard_ownership_verifier_always_runs() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["verify-test-shard-ownership"]

    # Static repository invariant (MoonLadderStudios/MoonMind#3950): exclusive
    # shard ownership must gate targeted PRs too, so the job carries no
    # selection gate and ci-required aggregates it unconditionally.
    assert "if" not in job
    assert any(
        "tools/verify_test_shard_ownership.py" in step.get("run", "")
        for step in job["steps"]
    )


def test_reliability_job_runs_the_canonical_journey_suite() -> None:
    job = "backend-matrix"
    command = _run_command(job, "Run hermetic reliability shard")

    assert "tests/integration/reliability/test_" in command or "shard_files" in command
    assert "-m reliability_journey" in command
    assert "skipping until #3145 lands" not in command

    diagnostics = _run_command(job, "Collect reliability shard diagnostics")
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
        "backend-matrix",
        "unit-slow",
        "integration-ci",
        "omnigent-exact-artifact",
        "omnigent-deterministic-conformance",
        "verify-test-shard-ownership",
        "test-frontend",
        "check-generated-contracts",
    ):
        assert dependency in job["needs"]
    for removed in (
        "unit-fast",
        "api-component",
        "temporal-boundary",
        "reliability-journey-checkpoint-resume",
    ):
        assert removed not in job["needs"]


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
        "backend-matrix",
        "unit-slow",
        "integration-ci",
        "omnigent-exact-artifact",
        "omnigent-deterministic-conformance",
        "verify-test-shard-ownership",
        "test-frontend",
        "check-generated-contracts",
    ):
        assert name in script

    # Always-run aggregators and the always-run shard-ownership invariant are
    # aggregated unconditionally (MoonLadderStudios/MoonMind#3950): a failing
    # frontend or generated-contract gate must fail ci-required, and targeted
    # PRs must not trip a success-but-unselected failure on shard ownership.
    assert 'require_always "test-frontend"' in script
    assert "needs.test-frontend.result" in script
    assert 'require_always "check-generated-contracts"' in script
    assert "needs.check-generated-contracts.result" in script
    assert 'require_always "verify-test-shard-ownership"' in script
    assert "needs.verify-test-shard-ownership.result" in script
    assert 'require_selected "verify-test-shard-ownership"' not in script


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
    job = workflow["jobs"]["backend-matrix"]

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
    job = _load_workflow()["jobs"]["backend-matrix"]
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


def test_backend_matrix_consolidates_primary_suites_with_native_fail_fast() -> None:
    workflow = _load_workflow()
    assert "backend-matrix" in workflow["jobs"]
    for removed in (
        "unit-fast",
        "api-component",
        "temporal-boundary",
        "reliability-journey-checkpoint-resume",
    ):
        assert removed not in workflow["jobs"], removed
    job = workflow["jobs"]["backend-matrix"]
    assert job["needs"] == "select-test-suites"
    # Runs when any primary backend suite is selected; empty selection skips
    # the matrix intentionally without instantiating tests.
    job_if = job["if"]
    assert "needs.select-test-suites.outputs.unit_fast" in job_if
    assert "needs.select-test-suites.outputs.api_component" in job_if
    assert "needs.select-test-suites.outputs.temporal_boundary" in job_if
    assert "needs.select-test-suites.outputs.reliability_journey" in job_if
    assert job["timeout-minutes"] == 20
    strategy = job["strategy"]
    # Native matrix fail-fast for PR/merge-group validation, disabled for
    # scheduled diagnostics.
    assert "schedule" in str(strategy["fail-fast"])
    assert "github.event_name" in str(strategy["fail-fast"])
    suites = [entry["suite"] for entry in strategy["matrix"]["include"]]
    assert suites == [
        "unit-fast",
        "api-component",
        "temporal-boundary",
        "reliability-shard-1",
        "reliability-shard-2",
        "reliability-shard-3",
        "reliability-shard-4",
    ]
    shards = [
        entry.get("shard")
        for entry in strategy["matrix"]["include"]
        if entry["suite"].startswith("reliability-")
    ]
    assert shards == ["0", "1", "2", "3"]


def test_backend_matrix_keeps_setup_isolated_per_row() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["backend-matrix"]
    steps = {step["name"]: step for step in job["steps"]}
    # Fast rows never build images or start shared services.
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "docker/build-push-action" not in scripts
    assert "moonmind-python-tests:ci" not in scripts
    # Reliability rows keep isolated Compose with per-shard project names.
    start = steps["Start isolated reliability dependencies"]["run"]
    assert "moonmind-reliability-${{ matrix.suite }}" in start
    assert "tests/integration/reliability/compose.yaml up -d --wait" in start
    assert "MOONMIND_TEST_DOCKER_NETWORK=moonmind-reliability-${{ matrix.suite }}_default" in start
    assert steps["Start isolated reliability dependencies"].get("if", "").startswith(
        "startsWith(matrix.suite, 'reliability-')"
    )
    cleanup = steps["Remove isolated reliability dependencies"]
    assert cleanup["if"].startswith("always()")
    assert "moonmind-reliability-${{ matrix.suite }}" in cleanup["run"]
    # Only reliability rows touch Docker Compose; fast test commands do not.
    for name in (
        "Run selected unit suite",
        "Run API/component suite",
        "Run Temporal boundary suite",
    ):
        assert "docker compose" not in steps[name]["run"]
        assert "compose.yaml" not in steps[name]["run"]


def test_backend_matrix_reports_are_uniquely_named() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["backend-matrix"]
    steps = {step["name"]: step for step in job["steps"]}
    assert "artifacts/pytest-backend-unit-fast.xml" in steps["Run selected unit suite"]["run"]
    assert "artifacts/pytest-backend-api-component.xml" in steps["Run API/component suite"]["run"]
    assert (
        "artifacts/pytest-backend-temporal-boundary.xml"
        in steps["Run Temporal boundary suite"]["run"]
    )
    reliability_run = steps["Run hermetic reliability shard"]["run"]
    assert "artifacts/pytest-backend-${{ matrix.suite }}.xml" in reliability_run
    # Deterministic sharding matches the ownership tool's round-robin rule.
    assert "ls tests/integration/reliability/test_*.py | sort" in reliability_run
    assert "NR % 4" in reliability_run
    upload = steps["Upload reliability shard diagnostics"]
    assert upload["with"]["name"] == "pytest-${{ matrix.suite }}-diagnostics-attempt-${{ github.run_attempt }}"
    assert upload["with"]["path"] == "/tmp/pytest-${{ matrix.suite }}"


def test_backend_matrix_records_attempted_cancellation_diagnostics() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["backend-matrix"]
    steps = {step["name"]: step for step in job["steps"]}
    record = steps["Record backend-matrix cancellation diagnostics"]
    assert "failure()" in record["if"] and "cancelled()" in record["if"]
    assert "attempted-cancellation" in record["run"]
    upload = steps["Upload backend-matrix cancellation diagnostics"]
    assert "failure()" in upload["if"] and "cancelled()" in upload["if"]
    assert upload["with"]["name"] == "backend-matrix-${{ matrix.suite }}-cancellation-attempt-${{ github.run_attempt }}"
    # MoonLadderStudios/MoonMind#4371: shard diagnostics (compose logs plus
    # scoped manifests) are collected and uploaded on every selected run so
    # a slow but passing shard stays diagnosable; cancellation-only
    # bookkeeping above is unchanged.
    collect = steps["Collect reliability shard diagnostics"]
    assert collect["if"].startswith("always()")
    assert "needs.select-test-suites.outputs.reliability_journey" in collect["if"]
    shard_upload = steps["Upload reliability shard diagnostics"]
    assert shard_upload["if"].startswith("always()")
    assert "needs.select-test-suites.outputs.reliability_journey" in shard_upload["if"]


def test_backend_matrix_rows_are_selection_gated() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["backend-matrix"]
    steps = {step["name"]: step for step in job["steps"]}
    assert (
        steps["Run selected unit suite"]["if"]
        == "matrix.suite == 'unit-fast' && needs.select-test-suites.outputs.unit_fast == 'true'"
    )
    assert (
        steps["Run API/component suite"]["if"]
        == "matrix.suite == 'api-component' && needs.select-test-suites.outputs.api_component == 'true'"
    )
    assert (
        steps["Run Temporal boundary suite"]["if"]
        == "matrix.suite == 'temporal-boundary' && needs.select-test-suites.outputs.temporal_boundary == 'true'"
    )
    assert (
        steps["Run hermetic reliability shard"]["if"]
        == "startsWith(matrix.suite, 'reliability-') && needs.select-test-suites.outputs.reliability_journey == 'true'"
    )


def test_ci_required_consumes_backend_matrix_aggregate() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["ci-required"]
    assert "backend-matrix" in job["needs"]
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert 'require_selected "backend-matrix"' in script
    assert "needs.backend-matrix.result" in script
    # Selection derives from all four primary selector outputs.
    assert "needs.select-test-suites.outputs.unit_fast" in script
    assert "needs.select-test-suites.outputs.api_component" in script
    assert "needs.select-test-suites.outputs.temporal_boundary" in script
    assert "needs.select-test-suites.outputs.reliability_journey" in script
    # Never consume last-writer matrix outputs as per-row evidence.
    assert "needs.backend-matrix.outputs" not in script
    for removed in (
        'require_selected "unit-fast"',
        'require_selected "api-component"',
        'require_selected "temporal-boundary"',
        'require_selected "reliability-journey-checkpoint-resume"',
    ):
        assert removed not in script


def test_reliability_shards_enforce_short_step_and_test_deadlines() -> None:
    """MoonLadderStudios/MoonMind#4369: short reliability budgets.

    Each reliability shard bounds a hung test at 300s (down from the
    serial-era 600s) and caps the whole pytest invocation at 8 minutes via
    ``timeout 480s``. The 124 exit from ``timeout`` flows through
    ``PIPESTATUS`` so the evidence hook reports an interrupted run instead
    of masking it, and the 20-minute job timeout reserves teardown time
    (setup, 8-minute test step, bounded diagnostics and cleanup per shard
    plus evidence/upload margin).
    """
    workflow = _load_workflow()
    job = workflow["jobs"]["backend-matrix"]
    assert job["timeout-minutes"] == 20
    steps = {step["name"]: step for step in job["steps"]}
    command = steps["Run hermetic reliability shard"]["run"]
    assert "timeout 480s python -m pytest" in command
    assert "--timeout 300" in command
    assert "status=${PIPESTATUS[0]}" in command
    # The step cap must wrap the test process itself, not the log pipe, and
    # the per-shard JUnit report keeps its unique name.
    assert "2>&1 | tee artifacts/pytest-backend-${{ matrix.suite }}.log" in command
    assert "--junitxml=artifacts/pytest-backend-${{ matrix.suite }}.xml" in command


def test_step_timeout_wrapper_fails_fast_on_a_hung_process() -> None:
    """Disposable probe: the ``timeout`` mechanism used for the 8-minute
    reliability step ceiling terminates a non-returning process quickly with
    a non-success exit instead of waiting out a production budget."""
    import shutil
    import subprocess
    import time

    if shutil.which("timeout") is None:
        pytest.skip("coreutils timeout is unavailable")
    start = time.monotonic()
    proc = subprocess.run(
        ["timeout", "2s", "sleep", "300"],
        capture_output=True,
        timeout=30,
    )
    elapsed = time.monotonic() - start
    assert proc.returncode == 124
    assert elapsed < 10


def test_pytest_timeout_fails_a_hanging_test_fast(tmp_path) -> None:
    """Disposable probe (MoonLadderStudios/MoonMind#4365 R11): the per-test
    ``--timeout 300`` bound used on reliability shards fails a hanging test
    with non-success instead of holding the shard."""
    import subprocess
    import sys
    import time

    probe = tmp_path / "test_hang_probe_4365.py"
    probe.write_text(
        "import time\n\ndef test_hang_forever():\n    time.sleep(300)\n",
        encoding="utf-8",
    )
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "--timeout", "2", "-q"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    elapsed = time.monotonic() - start
    combined = (proc.stdout or "") + (proc.stderr or "")
    if "unrecognized arguments: --timeout" in combined:
        pytest.skip("pytest-timeout plugin is unavailable")
    assert proc.returncode != 0
    assert elapsed < 60


def test_timeout_bounds_a_hanging_teardown_phase() -> None:
    """Disposable probe (MoonLadderStudios/MoonMind#4365 R11): a teardown
    phase that never returns (EXIT trap sleeping) is terminated by the
    ``timeout`` step wrapper with a non-success exit instead of hanging
    the shard's bounded diagnostics/cleanup."""
    import shutil
    import subprocess
    import time

    if shutil.which("timeout") is None or shutil.which("bash") is None:
        pytest.skip("coreutils timeout or bash is unavailable")
    start = time.monotonic()
    proc = subprocess.run(
        ["timeout", "5s", "bash", "-c", "cleanup(){ sleep 300; }; trap cleanup EXIT; sleep 1"],
        capture_output=True,
        timeout=60,
    )
    elapsed = time.monotonic() - start
    assert proc.returncode == 124
    assert elapsed < 30


def test_reliability_fixtures_reuse_registry_layers_without_shared_state() -> None:
    """MoonLadderStudios/MoonMind#4376: no redundant cache subsystem.

    The reliability Compose file builds no images (dependency layers are
    registry layers reused natively by ``docker compose up``) and mounts no
    mutable release state -- only the read-only Temporal dynamic config.
    Isolation comes from per-shard Compose project names in the workflow,
    so shards never share services, networks, or volumes.
    """
    compose_path = REPO_ROOT / "tests" / "integration" / "reliability" / "compose.yaml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert set(compose["services"]) >= {"postgres", "temporal", "minio"}
    for name, service in compose["services"].items():
        assert "build" not in service, f"{name} must not build a local image"
        assert "image" in service, f"{name} must come from a registry layer"
        for volume in service.get("volumes", []):
            assert ":ro" in str(volume), f"{name} must not share mutable state: {volume}"
    workflow = _load_workflow()
    steps = {step["name"]: step for step in workflow["jobs"]["backend-matrix"]["steps"]}
    assert "moonmind-reliability-${{ matrix.suite }}" in steps["Start isolated reliability dependencies"]["run"]
    assert "moonmind-reliability-${{ matrix.suite }}" in steps["Remove isolated reliability dependencies"]["run"]
    assert "MOONMIND_TEST_DOCKER_NETWORK=moonmind-reliability-${{ matrix.suite }}_default" in steps[
        "Start isolated reliability dependencies"
    ]["run"]
