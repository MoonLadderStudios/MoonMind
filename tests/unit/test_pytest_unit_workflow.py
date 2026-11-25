from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from tools.select_test_suites import select_suites

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
    assert "push" not in triggers
    release = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/docker-publish.yml").read_text()
    )
    assert _workflow_triggers(release)["push"]["branches"] == ["main"]
    assert release["jobs"]["source-checks"]["with"]["verification_phase"] == "source"
    assert release["jobs"]["qualify"]["with"]["verification_phase"] == "artifact"
    assert all(
        release["jobs"][owner]["uses"] == "./.github/workflows/pytest-unit-tests.yml"
        for owner in ("source-checks", "qualify")
    )
    assert triggers["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
    ]
    assert "workflow_dispatch" in triggers
    assert "schedule" in triggers

    jobs = workflow["jobs"]
    integration_job = jobs["integration-hermetic-shards"]
    assert integration_job["needs"] == "select-test-suites"
    assert (
        integration_job["if"]
        == "needs.select-test-suites.outputs.integration_hermetic == 'true'"
    )
    assert any(
        "./tools/test_integration.sh" in step.get("run", "")
        for step in integration_job["steps"]
    )

    required_job = jobs["ci-required"]
    assert {"integration-ci", "integration-hermetic"} <= set(required_job["needs"])


@pytest.mark.parametrize(
    "workflow_name", ["pytest-unit-tests.yml", "alembic-migration-gate.yml"]
)
def test_required_ci_accepts_pull_requests_to_any_base_branch(workflow_name) -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
    )
    pull_request = _workflow_triggers(workflow)["pull_request"]

    # Stacked remediation PRs target another job branch, not necessarily main.
    # Either filter can leave merge automation waiting for checks that never run.
    assert "branches" not in pull_request
    assert "branches-ignore" not in pull_request


def test_required_integration_job_uses_submodule_free_checkout() -> None:
    workflow = _load_workflow()
    checkout = workflow["jobs"]["integration-ci"]["steps"][0]

    assert checkout["uses"].startswith("actions/checkout@")
    assert "submodules" not in checkout.get("with", {})
    assert int(checkout["with"]["fetch-depth"]) == 1


def test_integration_rows_retain_row_owned_redacted_reports() -> None:
    workflow = _load_workflow()
    for owner in ("integration-hermetic-shards", "integration-ci"):
        job = workflow["jobs"][owner]
        assert job["runs-on"] == "ubuntu-latest"
        steps = {step["name"]: step for step in job["steps"]}
        collect = steps["Collect row-owned integration report and diagnostics"]
        assert collect["if"] == "always()"
        if owner == "integration-hermetic-shards":
            assert "artifacts/integration-ci" in collect["run"]
            assert "not zero tests" in collect["run"]
        else:
            assert "var/artifacts/first-run-3938" in collect["run"]
            assert "var/artifacts/journal-transition" in collect["run"]
        assert "docker-compose.test.yaml" not in collect["run"]
        assert "tools/ci/redact_diagnostics.py" in collect["run"]
        assert int(collect["timeout-minutes"]) <= 3
        upload = steps["Upload row-owned integration report and diagnostics"]
        assert upload["uses"].startswith("actions/upload-artifact@")
        assert upload["if"] == collect["if"]
        assert "${{ matrix.suite }}" in upload["with"]["name"]
        assert "${{ github.run_attempt }}" in upload["with"]["name"]
        assert upload["with"]["path"] == "/tmp/pytest-integration-ci"
        assert int(upload["timeout-minutes"]) <= 3


def _run_integration_collect(
    tmp_path: Path, suite: str, result: str = "failure"
) -> Path:
    import os
    import subprocess
    import shutil

    if shutil.which("timeout") is None:
        pytest.skip("the Linux CI diagnostics collector requires GNU timeout")
    steps = {
        step["name"]: step
        for step in _load_workflow()["jobs"]["integration-ci"]["steps"]
    }
    run = steps["Collect row-owned integration report and diagnostics"]["run"]
    out = tmp_path / "upload"
    script = run.replace("out=/tmp/pytest-integration-ci", f"out={out}")
    assert script != run
    script = script.replace("${{ matrix.suite }}", suite)
    script = re.sub(r"\$\{\{\s*github\.[^}]+\}\}", "test", script)
    (tmp_path / "tools" / "ci").mkdir(parents=True)
    (tmp_path / "tools" / "ci" / "redact_diagnostics.py").symlink_to(
        REPO_ROOT / "tools" / "ci" / "redact_diagnostics.py"
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "JOURNEY_RESULT": result},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out


def test_controller_journey_collects_journal_transition_evidence(tmp_path) -> None:
    secret = "ghp_" + "a" * 36
    journal = tmp_path / "var" / "artifacts" / "journal-transition" / "proj"
    journal.mkdir(parents=True)
    (journal / "phase-1-crash.json").write_text(f'{{"stderr": "{secret}"}}')
    (journal / "logs.txt").write_text("writer exited 71\n")
    journey = tmp_path / "var" / "artifacts" / "first-run-3938" / "controller"
    journey.mkdir(parents=True)
    (journey / "journey.log").write_text("controller step failed\n")

    out = _run_integration_collect(tmp_path, "controller-journey")

    collected = out / "journal-transition" / "proj"
    assert (collected / "logs.txt").read_text() == "writer exited 71\n"
    assert secret not in (collected / "phase-1-crash.json").read_text()
    assert (out / "first-run-3938" / "controller" / "journey.log").is_file()


def test_successful_journey_retains_timings_without_copying_saved_inputs(tmp_path):
    journey = tmp_path / "var/artifacts/first-run-3938/fresh"
    (journey / "state").mkdir(parents=True)
    (journey / "compose-startup-candidate-1.log").write_text("started\n")
    (journey / "resource-footprint-candidate.log").write_text("bounded footprint\n")
    (journey / "state/credentials.json").write_text('{"private": "saved input"}')
    out = _run_integration_collect(tmp_path, "fresh-journey", result="success")
    saved = out / "first-run-3938/fresh"
    assert (saved / "compose-startup-candidate-1.log").read_text() == "started\n"
    assert (saved / "resource-footprint-candidate.log").is_file()
    assert not (saved / "state").exists()


def test_journal_transition_evidence_stays_with_controller_journey(tmp_path) -> None:
    controller_root = tmp_path / "controller"
    (controller_root / "var" / "artifacts" / "first-run-3938").mkdir(parents=True)
    controller = _run_integration_collect(controller_root, "controller-journey")
    assert "missing var/artifacts/journal-transition" in (
        controller / "collection-status.txt"
    ).read_text()

    # Only the row that runs the journal transition test owns its evidence.
    fresh_root = tmp_path / "fresh"
    (fresh_root / "var" / "artifacts" / "journal-transition" / "proj").mkdir(
        parents=True
    )
    fresh = _run_integration_collect(fresh_root, "fresh-journey")
    assert not (fresh / "journal-transition").exists()
    assert "journal-transition" not in (fresh / "collection-status.txt").read_text()


def test_integration_rows_bound_each_phase_and_pass_fail_fast_policy() -> None:
    steps = {
        step["name"]: step
        for step in _load_workflow()["jobs"]["integration-ci"]["steps"]
    }
    hermetic = next(
        step
        for step in _load_workflow()["jobs"]["integration-hermetic-shards"]["steps"]
        if step["name"] == "Run hermetic integration CI suite"
    )
    assert hermetic["env"]["MOONMIND_INTEGRATION_FAIL_FAST"] == (
        "${{ github.event_name != 'schedule' && '1' || '0' }}"
    )
    # Include the wrapper's interrupt grace and the kill grace for each of
    # its three diagnostics/teardown commands before the native step bound.
    budget_seconds = int(hermetic["env"]["MOONMIND_INTEGRATION_TIMEOUT"])
    assert budget_seconds + 30 + 3 * (120 + 10) < int(hermetic["timeout-minutes"]) * 60
    assert 0 < int(hermetic["timeout-minutes"]) <= 35
    for name in (
        "Install the journey browser's Playwright runtime",
        "Install the journey browser OS dependencies",
        "Download the journey Chromium browser",
        "Run disposable default first-run journey",
        "Run eligible-upgrade journey",
        "Run Settings Operations controller journey",
        "Qualify active journal transition and host controller refresh",
        "Upload row-owned integration report and diagnostics",
    ):
        assert 0 < int(steps[name]["timeout-minutes"]) < 30, name


def _integration_row_timeout_minutes(value: int | str, suite: str) -> int:
    if isinstance(value, int):
        return value
    match = re.fullmatch(
        r"\$\{\{ matrix\.suite == 'hermetic' && (\d+) \|\| (\d+) \}\}", value
    )
    assert match, f"Unrecognized integration timeout expression: {value}"
    return int(match.group(1 if suite == "hermetic" else 2))


@pytest.mark.parametrize("cache_hit", [False, True])
def test_hermetic_job_reserves_bounded_setup_execution_and_reporting(cache_hit) -> None:
    job = _load_workflow()["jobs"]["integration-hermetic-shards"]
    run_index = next(
        i
        for i, step in enumerate(job["steps"])
        if step["name"] == "Run hermetic integration CI suite"
    )
    by_name = {step["name"]: step for step in job["steps"]}
    checks = [
        by_name[name]
        for name in (
            "Verify isolated Docker socket proxy",
            "Verify real scoped egress connections and owned cleanup",
        )
    ]
    assert all(0 < int(step["timeout-minutes"]) <= 5 for step in checks)
    setup_minutes = 0
    for step in job["steps"][:run_index]:
        if step in checks:
            continue
        condition = step.get("if", "")
        if "cache-hit" in condition and (
            ("!= 'true'" in condition and cache_hit)
            or ("== 'true'" in condition and not cache_hit)
        ):
            continue
        minutes = int(step["timeout-minutes"])
        assert 0 < minutes <= 6, step["name"]
        setup_minutes += minutes
    assert setup_minutes <= 20
    reporting = [
        by_name[name]
        for name in (
            "Validate complete owner receipt",
            "Collect row-owned integration report and diagnostics",
            "Upload row-owned integration report and diagnostics",
        )
    ]
    assert sum(int(step["timeout-minutes"]) for step in reporting) == 5
    assert setup_minutes + sum(int(step["timeout-minutes"]) for step in checks) + int(
        job["steps"][run_index]["timeout-minutes"]
    ) + 5 + 2 <= int(job["timeout-minutes"])


def test_hermetic_job_reports_a_timing_digest_on_every_outcome() -> None:
    job = _load_workflow()["jobs"]["integration-hermetic-shards"]
    collect = next(
        step
        for step in job["steps"]
        if step["name"] == "Collect row-owned integration report and diagnostics"
    )

    # The digest reaches the job log and step summary, which stay readable
    # when artifact downloads do not, without a step of its own or budget.
    assert collect["if"] == "always()"
    command = collect["run"]
    assert "python3 tools/ci/write_backend_matrix_summary.py digest" in command
    assert '--summary "$GITHUB_STEP_SUMMARY"' in command
    for receipt in ("main", "storage-seed", "storage-verify"):
        assert f"artifacts/integration-ci-${{{{ matrix.suite }}}}/{receipt}.receipt.json" in command


def test_integration_ci_runs_each_boundary_on_its_own_row() -> None:
    jobs = _load_workflow()["jobs"]
    assert json.loads(select_suites([]).as_outputs()["integration_matrix"])[
        "suite"
    ] == ["hermetic", "fresh-journey", "upgrade-journey", "controller-journey"]
    hermetic, journeys = jobs["integration-hermetic-shards"], jobs["integration-ci"]
    assert hermetic["needs"] == "select-test-suites"
    assert "prepare-app-image" not in hermetic["needs"]
    assert "prepare-app-image" in journeys["needs"]
    assert hermetic["strategy"]["matrix"] == {"include": [
        {"suite": "hermetic-shard-1", "shard": 1},
        {"suite": "hermetic-shard-2", "shard": 2},
    ]}
    for job in (hermetic, journeys):
        assert job["strategy"]["fail-fast"] == "${{ github.event_name != 'schedule' }}"
    assert hermetic["timeout-minutes"] == 70
    assert journeys["timeout-minutes"] == 90
    assert any(
        "./tools/test_integration.sh" in step.get("run", "")
        for step in hermetic["steps"]
    )
    steps = {step["name"]: step for step in journeys["steps"]}
    for name, row, command in (
        (
            "Run disposable default first-run journey",
            "fresh-journey",
            "./tools/first_run_journey_3938.sh",
        ),
        (
            "Run eligible-upgrade journey",
            "upgrade-journey",
            "./tools/first_run_journey_3938.sh --upgrade",
        ),
        (
            "Run Settings Operations controller journey",
            "controller-journey",
            "./tools/first_run_journey_3938.sh --controller",
        ),
    ):
        assert steps[name]["if"] == f"matrix.suite == '{row}'"
        assert command in [line.strip() for line in steps[name]["run"].splitlines()]
    for name in (
        "Set up Node for the dashboard journey",
        "Install the journey browser's Playwright runtime",
        "Install the journey browser OS dependencies",
        "Download the journey Chromium browser",
    ):
        assert steps[name]["if"] == "endsWith(matrix.suite, '-journey')"


@pytest.mark.parametrize(
    ("suite", "execution_minutes"),
    [("fresh-journey", 20), ("upgrade-journey", 25), ("controller-journey", 30)],
)
@pytest.mark.parametrize("image_source", ["registry", "archive"])
def test_journey_job_reserves_setup_execution_and_reporting(
    suite: str, execution_minutes: int, image_source: str
) -> None:
    job = _load_workflow()["jobs"]["integration-ci"]
    steps = job["steps"]
    first_journey = next(
        index
        for index, step in enumerate(steps)
        if step["name"] == "Run disposable default first-run journey"
    )

    def selected(step: dict) -> bool:
        condition = step.get("if", "")
        if "env.CANDIDATE_ARCHIVE != ''" in condition and image_source != "archive":
            return False
        if "env.CANDIDATE_ARCHIVE == ''" in condition and image_source == "archive":
            return False
        return True

    setup = [step for step in steps[:first_journey] if selected(step)]
    setup_minutes = sum(
        _integration_row_timeout_minutes(step["timeout-minutes"], suite)
        for step in setup
    )
    by_name = {step["name"]: step for step in setup}
    npm = by_name["Install the journey browser's Playwright runtime"]
    system = by_name["Install the journey browser OS dependencies"]
    browser = by_name["Download the journey Chromium browser"]
    assert npm["run"].strip() == "npm ci --no-audit --no-fund"
    assert "install-deps chromium" in system["run"]
    assert "node node_modules/playwright/cli.js install chromium" in browser["run"]
    assert [int(step["timeout-minutes"]) for step in (npm, system, browser)] == [
        5,
        15,
        5,
    ]
    assert setup.index(npm) < setup.index(system) < setup.index(browser)
    before = by_name["Inspect journey setup disk capacity"]
    after = by_name["Inspect journey setup disk capacity after installation"]
    assert setup.index(before) < setup.index(npm)
    assert setup.index(after) > setup.index(browser)
    assert after["if"] == "always() && endsWith(matrix.suite, '-journey')"
    for diagnostic in (before, after):
        assert "df -h" in diagnostic["run"]
        assert "df -i" in diagnostic["run"]
    execution = [
        step for step in steps if step.get("if") == f"matrix.suite == '{suite}'"
    ]
    assert sum(int(step["timeout-minutes"]) for step in execution) == execution_minutes
    reporting = steps[
        next(
            index
            for index, step in enumerate(steps)
            if step["name"] == "Collect row-owned integration report and diagnostics"
        ) :
    ]
    reporting_minutes = sum(int(step["timeout-minutes"]) for step in reporting)
    assert reporting_minutes == 6
    fixture_cleanup_minutes = sum(
        int(step["timeout-minutes"])
        for step in steps
        if "release_image_fixture.py cleanup" in step.get("run", "") and selected(step)
    )
    # Bound every phase, including failure diagnostics; reserve runner/post-action overhead.
    assert setup_minutes == {"registry": 42, "archive": 49}[image_source]
    assert (
        setup_minutes
        + execution_minutes
        + reporting_minutes
        + fixture_cleanup_minutes
        + 4
        <= (_integration_row_timeout_minutes(job["timeout-minutes"], suite))
    )


def test_selector_policy_enforces_workflow_display_name_guard() -> None:
    workflow = _load_workflow()

    preflight_scripts = [
        step.get("run", "") for step in workflow["jobs"]["select-test-suites"]["steps"]
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

    # Detection reuses the selector's changed-file list instead of a separate
    # runner that recomputes it.
    assert "detect-openapi-contract-impact" not in jobs
    selector_job = jobs["select-test-suites"]
    assert (
        selector_job["outputs"]["generated_contracts"]
        == "${{ steps.detect-openapi.outputs.generated_contracts }}"
    )
    selector_steps = selector_job["steps"]
    assert any(
        "tools/check_openapi_affecting_changes.sh" in (step.get("run") or "")
        for step in selector_steps
    )
    assert not any(
        (step.get("uses") or "").startswith("actions/setup-node@")
        or (step.get("uses") or "").startswith("actions/setup-python@")
        or "npm ci" in (step.get("run") or "")
        or "uv pip install" in (step.get("run") or "")
        or "apt-get install" in (step.get("run") or "")
        for step in selector_steps
    )

    contract_job = jobs["run-generated-contracts"]
    assert contract_job["needs"] == "select-test-suites"
    assert (
        contract_job["if"]
        == "needs.select-test-suites.outputs.generated_contracts == 'true'"
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
    # The required result is aggregated directly by ci-required.
    assert "run-generated-contracts" in jobs["ci-required"]["needs"]


def test_frontend_jobs_are_impact_aware_and_report_into_ci_required() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    static = jobs["frontend-static"]
    assert static["needs"] == "select-test-suites"
    assert static["if"] == "needs.select-test-suites.outputs.frontend_static == 'true'"
    assert any("npm run frontend:ci" in step.get("run", "") for step in static["steps"])

    browser = jobs["frontend-browser"]
    assert browser["needs"] == "select-test-suites"
    assert browser["strategy"]["fail-fast"] is False
    engines = browser["strategy"]["matrix"]["engine"]
    assert "frontend_browser_firefox" in engines
    # MoonMind#4559: WebKit runs the whole browser suite, including the
    # form/fieldset/overlay cases, on every selection that runs the matrix.
    # A second job running one of those files again in WebKit is redundant.
    assert engines.count('"webkit"') == 2
    assert "frontend-browser-webkit-targeted" not in jobs
    assert "@sha256:" in browser["container"]["image"]
    assert browser["env"]["HOME"] == "/root"
    assert not any(
        "playwright install" in step.get("run", "") for step in browser["steps"]
    )

    # The required result is aggregated directly by ci-required.
    assert {"frontend-static", "frontend-browser"} <= set(jobs["ci-required"]["needs"])


def test_playwright_package_and_container_versions_match() -> None:
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

    assert (
        "run --expected-python 3.12 -- -p tools.ci.write_backend_matrix_summary tests/unit \\"
        in command
    )
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

    assert (
        "run --expected-python 3.12 -- -p tools.ci.write_backend_matrix_summary tests/unit/workflows/temporal"
        in temporal_command
    )
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
        # Tight fast-lane per-test bounds (MoonLadderStudios/MoonMind#4369):
        # 60s unit-fast, 120s API/component and Temporal.
    assert "--timeout 60" in unit_fast
    assert "--timeout 120" in api_command
    assert "--timeout 120" in temporal_command
    for command in (unit_fast, api_command, temporal_command):
        assert "--timeout 600" not in command
    # The API shard's 400-test router modules dominate a per-file distribution,
    # so component tests are distributed per test.
    assert "--dist load " in api_command or api_command.rstrip().endswith("--dist load")
    assert "--dist loadfile" not in api_command
    assert "--dist loadfile" in temporal_command
    # Reliability shards run serially with their own short per-test bound
    # (MoonLadderStudios/MoonMind#4369, #4384): 150s PR / 300s schedule, never a
    # raised global default. Step ceilings: ~10-min PR via `timeout 600s`
    # (above the ~500s heaviest partition load), 12-min schedule via step
    # timeout-minutes.
    assert "-n auto" not in reliability_command
    assert "--timeout 600" not in reliability_command
    assert "pytest_timeout=150" in reliability_command
    assert "pytest_timeout=300" in reliability_command
    assert "step_budget=600" in reliability_command
    assert "step_budget=480" not in reliability_command
    assert "--timeout \"$pytest_timeout\"" in reliability_command


def test_deterministic_conformance_is_selection_gated() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["omnigent-deterministic-conformance"]
    assert {
        "select-test-suites",
        "preflight-policy",
        "backend-matrix",
        "integration-hermetic",
        "frontend-static",
        "unit-slow",
    } <= set(job["needs"])
    assert (
        "needs.select-test-suites.outputs.omnigent_conformance == 'true'" in job["if"]
    )
    for owner in (
        "preflight-policy",
        "backend-matrix",
        "integration-hermetic",
        "frontend-static",
    ):
        assert f"needs.{owner}.result == 'success'" in job["if"]
    assert "omnigent_conformance" in workflow["jobs"]["select-test-suites"]["outputs"]


def test_rotating_seed_sweep_runs_only_on_direct_schedule_or_dispatch() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["omnigent-fault-rotating-seeds"]
    condition = " ".join(job["if"].split())

    # A workflow_call reports its caller's event, so only an empty
    # verification phase distinguishes a direct schedule/dispatch run.
    assert "inputs.verification_phase == ''" in condition
    assert "github.event_name == 'schedule'" in condition
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "push" not in condition
    call_inputs = _workflow_triggers(workflow)["workflow_call"]["inputs"]
    assert call_inputs["verification_phase"]["default"]
    callers = [
        caller_job
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
        for caller_job in yaml.safe_load(path.read_text(encoding="utf-8"))[
            "jobs"
        ].values()
        if caller_job.get("uses") == "./.github/workflows/pytest-unit-tests.yml"
    ]
    assert len(callers) >= 3
    for caller_job in callers:
        assert caller_job.get("with", {}).get("verification_phase", "full")

    # Exploratory: nothing required, receipted or published waits on it.
    for name, other in workflow["jobs"].items():
        needs = other.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert "omnigent-fault-rotating-seeds" not in needs, name


def test_rotating_seed_sweep_fails_without_a_matching_recorded_result() -> None:
    workflow = _load_workflow()
    steps = workflow["jobs"]["omnigent-fault-rotating-seeds"]["steps"]
    names = [step.get("name") for step in steps]
    sweep = names.index("Run rotating fault-lab seed corpus")
    record = names.index("Record rotating sweep result")
    assert sweep < record < names.index("Upload rotating fault-lab diagnostics")

    record_step = steps[record]
    assert record_step["if"] == "${{ !cancelled() }}"
    assert "python -m tools.omnigent_faultlab.rotating_result" in record_step["run"]
    assert record_step["env"]["SEED_OFFSET"] == "${{ steps.window.outputs.offset }}"
    assert record_step["env"]["SEED_COUNT"] == "${{ steps.window.outputs.count }}"
    diagnostics = steps[sweep]["env"]["MOONMIND_FAULTLAB_DIAGNOSTICS_DIR"]
    assert f"--dir {diagnostics}" in record_step["run"]


def test_image_building_jobs_share_a_layer_cache() -> None:
    jobs = _load_workflow()["jobs"]
    integration_steps = jobs["integration-hermetic-shards"]["steps"]
    build = next(
        step
        for step in integration_steps
        if step.get("with", {}).get("target") == "test-runtime"
    )
    assert "cache-hit != 'true'" in build["if"]
    assert build["with"]["load"] is True
    assert build["with"]["cache-from"].startswith("type=gha,")
    assert "cache-to" not in build["with"]
    assert build["with"]["cache-from"] == "type=gha,scope=build-linux-amd64"
    runtime_cache = next(
        step for step in integration_steps if step.get("id") == "test-runtime-cache"
    )
    assert "steps.test-runtime-inputs.outputs.key" in runtime_cache["with"]["key"]
    run = next(
        step
        for step in integration_steps
        if step["name"] == "Run hermetic integration CI suite"
    )
    assert run["env"]["MOONMIND_PYTHON_TEST_IMAGE"] == build["with"]["tags"]
    producer = jobs["prepare-app-image"]
    candidate = next(
        step
        for step in producer["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    )
    assert candidate["with"]["cache-from"] == "type=gha,scope=build-linux-amd64"
    assert "cache-to" not in candidate["with"]
    for owner in (
        "integration-ci",
        "omnigent-exact-artifact",
        "omnigent-host-recovery",
    ):
        assert "prepare-app-image" in jobs[owner]["needs"]
        assert not any(
            step.get("uses", "").startswith("docker/build-push-action@")
            for step in jobs[owner]["steps"]
        )
        assert any(
            "release_image_fixture.py restore" in step.get("run", "")
            for step in jobs[owner]["steps"]
        )


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


def test_shard_ownership_verifier_runs_for_collection_inputs() -> None:
    workflow = _load_workflow()
    assert "verify-test-shard-ownership" not in workflow["jobs"]
    job = workflow["jobs"]["preflight-policy"]

    # Policy stays unconditional; changed collection inputs select the real
    # ownership verifier, including all four installed-plugin partitions.
    assert job["if"] == (
        "inputs.verification_phase != 'artifact' && inputs.verification_phase != 'recovery'"
    )
    verifier = next(
        step
        for step in job["steps"]
        if "tools/verify_test_shard_ownership.py" in step.get("run", "")
    )
    assert verifier["if"] == "steps.ownership.outputs.ownership_full == 'true'"
    install = _run_command("preflight-policy", "Install runtime dependencies via uv")
    assert "uv pip install --system -e .[tests]" in install


def test_reliability_job_runs_the_canonical_journey_suite() -> None:
    job = "backend-matrix"
    command = _run_command(job, "Run hermetic reliability shard")

    # Node-level partitioning through pytest-split
    # (MoonLadderStudios/MoonMind#4366): the whole eligible reliability
    # universe is split per node (including parameterized cases) with
    # --splits/--group, never by file list or file count.
    assert "tests/integration/reliability" in command
    assert "shard_files" not in command
    assert "reliability_shard_partition.py" not in command
    assert "--splits 4" in command
    assert "--group" in command
    assert "${{ matrix.shard }}" in command
    assert "least_duration" in command
    assert "tests/.reliability-test-durations.json" in command
    # The CI rows, the ownership verifier and the duration import share one
    # provider-free marker expression so they partition the same universe
    # (MoonLadderStudios/MoonMind#4629).
    from tools.ci.refresh_reliability_durations import RELIABILITY_MARKER_EXPR

    assert f'-m "{RELIABILITY_MARKER_EXPR}"' in command
    # The producer plugin records exact pytest node IDs in the JUnit report.
    assert "-p tools.ci.write_backend_matrix_summary" in command
    assert "skipping until #3145 lands" not in command

    diagnostics = _run_command(job, "Collect reliability shard diagnostics")
    assert "tests/integration/reliability/replays" in diagnostics


def test_reliability_shard_preserves_pytest_exit_status() -> None:
    """MoonLadderStudios/MoonMind#4366: a selected shard that collects no
    tests (pytest exit code 5) must stay visible instead of being converted
    to success. The step exits with the pytest status it measured."""
    command = _run_command("backend-matrix", "Run hermetic reliability shard")

    assert "status=${PIPESTATUS[0]}" in command
    assert "exit $status" in command


def test_reliability_shard_validates_duration_hints_before_pytest() -> None:
    """MoonLadderStudios/MoonMind#4366 R4: every shard validates the shared
    duration hints through the same small local helper. An unusable hint
    file warns and falls back to the deterministic no-history partition
    instead of skipping tests or requiring a remote timing service."""
    workflow = _load_workflow()
    steps = {step["name"]: step for step in workflow["jobs"]["backend-matrix"]["steps"]}

    validate = steps["Validate reliability duration hints"]
    assert "tools/ci/refresh_reliability_durations.py" in validate["run"]
    assert validate["run"].strip().startswith("set ")
    reliability = steps["Run hermetic reliability shard"]
    assert "durations" in reliability["run"].lower()


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
        "backend-matrix",
        "unit-slow",
        "integration-ci",
        "omnigent-exact-artifact",
        "omnigent-deterministic-conformance",
        "frontend-static",
        "frontend-browser",
        "run-generated-contracts",
    ):
        assert dependency in job["needs"]
    for removed in (
        "unit-fast",
        "api-component",
        "temporal-boundary",
        "reliability-journey-checkpoint-resume",
        # Source invariants are owned by selection and preflight-policy.
        "moonspec-projection",
        "verify-test-shard-ownership",
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
        "backend-matrix",
        "unit-slow",
        "integration-ci",
        "omnigent-exact-artifact",
        "omnigent-deterministic-conformance",
        "frontend-static",
        "frontend-browser",
        "run-generated-contracts",
    ):
        assert name in script

    # The always-run preflight invariants (policy, MoonSpec projection, shard
    # ownership) are aggregated unconditionally (MoonLadderStudios/MoonMind#3950):
    # targeted PRs must not trip a success-but-unselected failure on an
    # always-run invariant.
    assert 'require_always "preflight-policy"' in script
    assert "needs.preflight-policy.result" in script
    assert 'require_selected "preflight-policy" "false"' in script
    assert "inputs.verification_phase" in script


def test_source_ownership_runs_in_parallel_with_selector_policy_guards() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["preflight-policy"]

    # Starts immediately, in parallel with backend test selection.
    assert "needs" not in job

    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert int(checkout["with"]["fetch-depth"]) == 1
    assert "submodules" not in checkout.get("with", {})

    scripts = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["select-test-suites"]["steps"]
    )
    assert "tools/verify_workflow_terminology.py --mode runtime" in scripts
    assert "tools/check_removed_capability_semantics.py" in scripts
    assert "tools/status_domain_audit.py" in scripts
    assert "tools/check_github_workflow_names.py" in scripts


def test_unit_fast_no_longer_duplicates_policy_checks() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["backend-matrix"]

    checkout = job["steps"][0]
    assert "submodules" not in checkout.get("with", {})

    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "verify_workflow_terminology.py" not in scripts


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


def test_preflight_retains_shared_changed_file_resolution() -> None:
    job = _load_workflow()["jobs"]["preflight-policy"]
    steps = {step["name"]: step for step in job["steps"]}

    compute = steps["Compute changed files"]
    assert compute["id"] == "changed-files"
    assert '>> "$GITHUB_OUTPUT"' in compute["run"]
    # Manual and scheduled runs resolve the default-branch merge base so
    # ownership selection can use the exact diff when it is available.
    assert compute["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert ".merge_base_commit.sha" in compute["run"]
    assert "compute_changed_files.sh /tmp/changed-files.txt $base_sha" in (
        compute["run"]
    )


def test_unit_fast_initializes_moonspec_test_fixtures() -> None:
    job = _load_workflow()["jobs"]["backend-matrix"]
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "git submodule update --init --depth 1 -- moonspec" in scripts


def test_moonspec_projection_initializes_only_moonspec_submodule() -> None:
    workflow = _load_workflow()
    assert "moonspec-projection" not in workflow["jobs"]
    job = workflow["jobs"]["preflight-policy"]

    checkout = job["steps"][0]
    assert "submodules" not in checkout.get("with", {})

    runs = [step.get("run", "") for step in job["steps"]]
    scripts = "\n".join(runs)
    assert "python3 tools/sync_moonspec.py --check" in scripts
    # Does not initialize Open WebUI or Omnigent.
    assert "open-webui" not in scripts
    assert "omnigent" not in scripts

    # Ownership collection sees the submodule-free tree before projection.
    init_index = next(
        index
        for index, run in enumerate(runs)
        if "git submodule update --init --depth 1 -- moonspec" in run
    )
    walker_index = next(
        index
        for index, run in enumerate(runs)
        if "tools/verify_test_shard_ownership.py" in run
    )
    assert walker_index < init_index
    check_index = next(
        index
        for index, run in enumerate(runs)
        if "tools/sync_moonspec.py --check" in run
    )
    assert check_index > init_index


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
    compute = _run_command("select-test-suites", "Compute changed files")
    command = _run_command("select-test-suites", "Detect OpenAPI-affecting changes")

    assert "tools/ci/compute_changed_files.sh" in compute
    assert '>> "$GITHUB_OUTPUT"' in compute
    assert "tools/check_openapi_affecting_changes.sh" in command
    # An unknown change set stays conservative and runs the contract check.
    assert "steps.changed-files.outputs.resolution" in command
    assert "generated_contracts=true" in command


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
    # Each selected row reserves setup/reporting room around its test step.
    assert job["timeout-minutes"] == "${{ matrix.job_minutes }}"
    strategy = job["strategy"]
    # Native matrix fail-fast for PR/merge-group validation, disabled for
    # scheduled diagnostics.
    assert "schedule" in str(strategy["fail-fast"])
    assert "github.event_name" in str(strategy["fail-fast"])
    suites = [entry["suite"] for entry in json.loads(select_suites([]).as_outputs()["backend_matrix"])["include"]]
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
        for entry in json.loads(select_suites([]).as_outputs()["backend_matrix"])["include"]
        if entry["suite"].startswith("reliability-")
    ]
    # pytest-split groups are 1-based (MoonLadderStudios/MoonMind#4366):
    # suite reliability-shard-N runs --group N.
    assert shards == ["1", "2", "3", "4"]
    rows = {
        entry["suite"]: entry
        for entry in json.loads(select_suites([]).as_outputs()["backend_matrix"])[
            "include"
        ]
    }
    assert rows["unit-fast"]["job_minutes"] == 26
    unit_step = next(
        step for step in job["steps"] if step["name"] == "Run selected unit suite"
    )
    assert unit_step["timeout-minutes"] == 20
    assert unit_step["timeout-minutes"] + 6 <= rows["unit-fast"]["job_minutes"]
    assert "--timeout 60" in unit_step["run"]
    assert rows["api-component"]["job_minutes"] == 18
    api_step = next(
        step for step in job["steps"] if step["name"] == "Run API/component suite"
    )
    assert api_step["timeout-minutes"] + 6 <= rows["api-component"]["job_minutes"]
    assert "--timeout 120" in api_step["run"]
    assert rows["temporal-boundary"]["job_minutes"] == 20
    temporal_step = next(
        step for step in job["steps"] if step["name"] == "Run Temporal boundary suite"
    )
    assert temporal_step["timeout-minutes"] == 12
    assert temporal_step["timeout-minutes"] + 8 <= rows["temporal-boundary"]["job_minutes"]
    assert "--timeout 120" in temporal_step["run"]
    assert "-o faulthandler_timeout=90" in temporal_step["run"]
    for shard in (
        "reliability-shard-1",
        "reliability-shard-2",
        "reliability-shard-3",
        "reliability-shard-4",
    ):
        assert rows[shard]["job_minutes"] == 20


def test_backend_matrix_documents_max_parallel_deviation() -> None:
    """MoonLadderStudios/MoonMind#4366 R1: the four reliability shards run
    concurrently with isolated services; the consolidated 7-row matrix
    intentionally leaves max-parallel unset (capping at 4 would queue shards
    behind fast rows past the PR ceiling). The deviation rationale must stay
    recorded in the workflow until it is accepted on the issue."""
    workflow = _load_workflow()
    strategy = workflow["jobs"]["backend-matrix"]["strategy"]
    assert "max-parallel" not in strategy


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
    assert "moonmind-test-reliability-${{ matrix.suite }}" in start
    assert "tests/integration/reliability/compose.yaml up -d --wait" in start
    assert "MOONMIND_TEST_DOCKER_NETWORK=moonmind-test-reliability-${{ matrix.suite }}_default" in start
    assert steps["Start isolated reliability dependencies"].get("if", "").startswith(
        "startsWith(matrix.suite, 'reliability-')"
    )
    cleanup = steps["Remove isolated reliability dependencies"]
    assert cleanup["if"].startswith("always()")
    assert "moonmind-test-reliability-${{ matrix.suite }}" in cleanup["run"]
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
    # Node-level duration-balanced sharding through pytest-split
    # (MoonLadderStudios/MoonMind#4366), sharing the committed duration
    # hints with the ownership verifier. No file-count partitioning.
    assert "--splits 4" in reliability_run
    assert "tools/ci/reliability_shard_partition.py --shard" not in reliability_run
    assert "NR % 4" not in reliability_run
    # Short reliability budgets with PR-vs-schedule differentiation
    # (MoonLadderStudios/MoonMind#4369).
    assert steps["Run hermetic reliability shard"].get("timeout-minutes") == 12
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
    # Every-run retention (MoonLadderStudios/MoonMind#4371): reliability
    # dependency logs/replays/manifests upload on every run via always(),
    # while cancellation diagnostics stay failure()/cancelled()-gated.
    assert collect["if"].startswith("always()")
    assert "startsWith(matrix.suite, 'reliability-')" in collect["if"]
    upload_reliability = steps["Upload reliability shard diagnostics"]
    assert upload_reliability["if"].startswith("always()")


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
    """MoonLadderStudios/MoonMind#4369, #4384: short reliability budgets.

    Each reliability shard uses a short per-test timeout (150s PR / 300s
    schedule) under a ~10-min PR step ceiling (``timeout 600s``, above the
    ~500s heaviest duration-balanced partition load) and a 12-min schedule
    step ceiling. The 124 exit from ``timeout`` flows through ``PIPESTATUS``
    so the evidence hook reports an interrupted run instead of masking it,
    and the 20-minute per-row job bound reserves setup, diagnostics,
    cleanup, and evidence/upload margin.
    """
    workflow = _load_workflow()
    job = workflow["jobs"]["backend-matrix"]
    assert job["timeout-minutes"] == "${{ matrix.job_minutes }}"
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Run hermetic reliability shard"].get("timeout-minutes") == 12
    command = steps["Run hermetic reliability shard"]["run"]
    assert "pytest_timeout=150" in command
    assert "pytest_timeout=300" in command
    assert "step_budget=600" in command
    assert "--timeout \"$pytest_timeout\"" in command
    assert "status=${PIPESTATUS[0]}" in command
    # The step cap must wrap the test process itself, not the log pipe, and
    # the per-shard JUnit report keeps its unique name.
    assert "2>&1 | tee artifacts/pytest-backend-${{ matrix.suite }}.log" in command
    assert "--junitxml=artifacts/pytest-backend-${{ matrix.suite }}.xml" in command


def test_step_timeout_wrapper_fails_fast_on_a_hung_process() -> None:
    """Disposable probe: the ``timeout`` mechanism used for the reliability
    step ceiling terminates a non-returning process quickly with
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
    ``--timeout`` bound used on reliability shards fails a hanging test
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
    assert "moonmind-test-reliability-${{ matrix.suite }}" in steps["Start isolated reliability dependencies"]["run"]
    assert "moonmind-test-reliability-${{ matrix.suite }}" in steps["Remove isolated reliability dependencies"]["run"]
    assert "MOONMIND_TEST_DOCKER_NETWORK=moonmind-test-reliability-${{ matrix.suite }}_default" in steps[
        "Start isolated reliability dependencies"
    ]["run"]


def _run_required_gate(tmp_path, *, outputs=None, results=None, inputs=None):
    import os
    import subprocess

    selected = select_suites(["README.md"], event_name="pull_request").as_outputs()
    selected.setdefault("backend_matrix", '{"include":[]}')
    selected.setdefault("integration_matrix", '{"suite":[]}')
    selected.setdefault("generated_contracts", "false")
    selected.update(outputs or {})
    needs = {
        name: {
            "result": (
                "success"
                if name in {"select-test-suites", "preflight-policy"}
                else "skipped"
            )
        }
        for name in _load_workflow()["jobs"]["ci-required"]["needs"]
    }
    needs["select-test-suites"]["outputs"] = selected
    for name, result in (results or {}).items():
        needs.setdefault(name, {})["result"] = result
    steps = _load_workflow()["jobs"]["ci-required"]["steps"]
    script = "\n".join(step.get("run", "") for step in steps)

    def resolve(match):
        path = match.group(1).strip().split(".")
        value = {"needs": needs, "inputs": inputs or {"verification_phase": "full"}}
        for key in path:
            value = value.get(key, "")
        return str(value)

    script = re.sub(r"\$\{\{\s*((?:needs|inputs)\.[^}]+)\}\}", resolve, script)
    return subprocess.run(
        ["bash", "-c", script],
        check=False,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "NEEDS_JSON": json.dumps(needs),
        },
    )


@pytest.mark.parametrize("bad_selection", ["", "TRUE", "garbage"])
def test_required_gate_rejects_invalid_selection(tmp_path, bad_selection):
    result = _run_required_gate(tmp_path, outputs={"unit_fast": bad_selection})
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize(
    "matrix", ["", "null", '{"include":[]}', '{"include":[{"suite":"unit-fast"}]}']
)
def test_required_gate_rejects_missing_selected_matrix_row(tmp_path, matrix):
    result = _run_required_gate(
        tmp_path,
        outputs={"unit_fast": "true", "backend_matrix": matrix},
        results={"backend-matrix": "success"},
    )
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize("result", ["failure", "cancelled", "timed_out", "skipped", ""])
def test_required_gate_rejects_missing_or_failed_selected_result(tmp_path, result):
    proc = _run_required_gate(
        tmp_path,
        outputs={
            "unit_fast": "true",
            "backend_matrix": '{"include":[{"suite":"unit-fast","job_minutes":26}]}',
        },
        results={"backend-matrix": result},
    )
    assert proc.returncode != 0, proc.stdout


def test_required_gate_accepts_only_intentional_skips(tmp_path):
    assert _run_required_gate(tmp_path).returncode == 0
    assert (
        _run_required_gate(
            tmp_path, results={"select-test-suites": "failure"}
        ).returncode
        != 0
    )


@pytest.mark.parametrize("job_minutes, accepted", [(20, True), (15, False)])
def test_required_gate_validates_temporal_completion_budget(tmp_path, job_minutes, accepted):
    result = _run_required_gate(
        tmp_path,
        outputs={
            "temporal_boundary": "true",
            "backend_matrix": json.dumps(
                {"include": [{"suite": "temporal-boundary", "job_minutes": job_minutes}]}
            ),
        },
        results={"backend-matrix": "success"},
    )
    assert (result.returncode == 0) == accepted, result.stdout + result.stderr


def test_backend_and_integration_matrices_consume_selected_rows():
    jobs = _load_workflow()["jobs"]
    for job, output in [
        ("backend-matrix", "backend_matrix"),
        ("integration-ci", "installed_matrix"),
    ]:
        assert (
            jobs[job]["strategy"]["matrix"]
            == "${{ fromJSON(needs.select-test-suites.outputs." + output + ") }}"
        )


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["api_service/api/routers/automation.py"],
        ["tests/integration/host_update/test_journal_transition.py"],
    ],
)
def test_required_gate_accepts_complete_selected_results(tmp_path, paths):
    outputs = select_suites(paths, event_name="pull_request").as_outputs()
    results = {
        "backend-matrix": (
            "success" if json.loads(outputs["backend_matrix"])["include"] else "skipped"
        ),
        **{
            job: "success" if outputs[key] == "true" else "skipped"
            for job, key in (
                ("unit-slow", "unit_slow"),
                ("integration-hermetic", "integration_hermetic"),
                ("omnigent-host-recovery", "host_recovery"),
                ("omnigent-exact-artifact", "exact_artifact"),
                ("omnigent-deterministic-conformance", "omnigent_conformance"),
                ("frontend-static", "frontend_static"),
                ("frontend-browser", "frontend_browser_chromium"),
            )
        },
    }
    installed = any(
        outputs[key] == "true"
        for key in (
            "integration_fresh_journey",
            "integration_upgrade_journey",
            "integration_controller_journey",
        )
    )
    results["integration-ci"] = "success" if installed else "skipped"
    results["prepare-app-image"] = (
        "success"
        if installed or "true" in (outputs["exact_artifact"], outputs["host_recovery"])
        else "skipped"
    )
    proc = _run_required_gate(tmp_path, outputs=outputs, results=results)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("phase", "owners"),
    [
        (
            "artifact",
            {
                "omnigent-exact-artifact": "exact_artifact",
                "integration-ci": "integration_fresh_journey",
            },
        ),
        ("recovery", {"omnigent-host-recovery": "host_recovery"}),
    ],
)
def test_required_gate_qualifies_a_supplied_image_without_a_pass_through(
    tmp_path, phase, owners
):
    from tools.select_test_suites import for_phase

    outputs = for_phase(select_suites([]), phase).as_outputs()
    inputs = {
        "verification_phase": phase,
        "candidate_image": "ghcr.io/o/m@sha256:" + "a" * 64,
    }
    assert all(outputs[key] == "true" for key in owners.values())
    results = {"select-test-suites": "success", "preflight-policy": "skipped"}
    results.update({job: "success" for job in owners})

    def gate(**changes):
        return _run_required_gate(
            tmp_path, outputs=outputs, results={**results, **changes}, inputs=inputs
        )

    proc = gate()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The supplied manifest is consumed directly; an extra producer run or a
    # missing/failed consumer is still a required failure.
    assert gate(**{"prepare-app-image": "success"}).returncode != 0
    for job in owners:
        for result in ("failure", "skipped", "cancelled"):
            assert gate(**{job: result}).returncode != 0, (job, result)


@pytest.mark.parametrize(
    "bad_matrix",
    [
        '{"suite":[]}',
        '{"suite":["hermetic","hermetic"]}',
        '{"suite":["unknown"]}',
        "garbage",
    ],
)
def test_required_gate_rejects_incomplete_or_duplicate_integration_selection(
    tmp_path, bad_matrix
):
    outputs = select_suites(
        ["tests/integration/test_example.py"], event_name="pull_request"
    ).as_outputs()
    outputs["integration_matrix"] = bad_matrix
    proc = _run_required_gate(
        tmp_path,
        outputs=outputs,
        results={"backend-matrix": "success", "integration-ci": "success"},
    )
    assert proc.returncode != 0, proc.stdout


def test_required_gate_rejects_missing_or_duplicate_reliability_partition(tmp_path):
    outputs = select_suites([], event_name="pull_request").as_outputs()
    matrix = json.loads(outputs["backend_matrix"])
    matrix["include"][-1] = matrix["include"][-2]
    outputs["backend_matrix"] = json.dumps(matrix)
    results = {
        job: "success" for job in _load_workflow()["jobs"]["ci-required"]["needs"]
    }
    assert (
        _run_required_gate(tmp_path, outputs=outputs, results=results).returncode != 0
    )


@pytest.mark.parametrize("flag", ["full_backend", "full_frontend"])
def test_required_gate_rejects_empty_full_selection(tmp_path, flag):
    assert _run_required_gate(tmp_path, outputs={flag: "true"}).returncode != 0


FRONTEND_AND_CONTRACT_OWNERS = (
    ("frontend-static", "frontend_static"),
    ("frontend-browser", "frontend_browser_chromium"),
    ("run-generated-contracts", "generated_contracts"),
)


@pytest.mark.parametrize("job, selection", FRONTEND_AND_CONTRACT_OWNERS)
@pytest.mark.parametrize("result", ["failure", "cancelled", "timed_out", "skipped", ""])
def test_required_gate_rejects_failed_selected_frontend_or_contract_owner(
    tmp_path, job, selection, result
):
    proc = _run_required_gate(
        tmp_path, outputs={selection: "true"}, results={job: result}
    )
    assert proc.returncode != 0, proc.stdout
    assert f"{job} was selected but ended with result={result}" in proc.stdout


@pytest.mark.parametrize("job, selection", FRONTEND_AND_CONTRACT_OWNERS)
def test_required_gate_rejects_unselected_or_malformed_frontend_or_contract_owner(
    tmp_path, job, selection
):
    ran_unselected = _run_required_gate(tmp_path, results={job: "success"})
    assert ran_unselected.returncode != 0, ran_unselected.stdout
    for malformed in ("", "TRUE"):
        proc = _run_required_gate(tmp_path, outputs={selection: malformed})
        assert proc.returncode != 0, proc.stdout


def test_required_gate_accepts_selected_frontend_and_contract_owners(tmp_path):
    proc = _run_required_gate(
        tmp_path,
        outputs={
            "frontend_static": "true",
            "frontend_browser_chromium": "true",
            "frontend_browser_firefox": "true",
            "generated_contracts": "true",
        },
        results={job: "success" for job, _ in FRONTEND_AND_CONTRACT_OWNERS},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = (tmp_path / "summary").read_text()
    for job, _ in FRONTEND_AND_CONTRACT_OWNERS:
        assert f"| `{job}` | true | `success` |" in summary


def test_required_gate_rejects_firefox_selection_the_browser_matrix_would_skip(
    tmp_path,
):
    # The browser matrix runs only with Chromium selected, so a Firefox-only
    # selection would otherwise disappear as an intentional skip.
    proc = _run_required_gate(tmp_path, outputs={"frontend_browser_firefox": "true"})
    assert proc.returncode != 0, proc.stdout


def test_source_receipt_runs_after_intentional_skips_without_accepting_failure():
    job = _load_workflow()["jobs"]["source-receipt"]
    assert set(job["needs"]) == {"ci-required", "select-test-suites"}
    condition = job["if"]
    # An explicit status function prevents GitHub's implicit success() from
    # suppressing this proof after the source phase skips installed-image jobs.
    assert "!cancelled()" in condition
    assert "inputs.verification_phase == 'source'" in condition
    for dependency in job["needs"]:
        assert f"needs.{dependency}.result == 'success'" in condition


def test_hermetic_aggregate_requires_both_successful_owners_and_actual_collection(tmp_path):
    import os
    import subprocess

    jobs = _load_workflow()["jobs"]
    aggregate = jobs["integration-hermetic"]
    assert set(aggregate["needs"]) == {
        "select-test-suites", "preflight-policy", "integration-hermetic-shards"
    }
    assert aggregate["if"] == "always() && needs.select-test-suites.outputs.integration_hermetic == 'true'"
    steps = {step["name"]: step for step in aggregate["steps"]}
    gate = steps["Require successful physical owners and collection"]
    assert gate["env"] == {
        "SHARDS": "${{ needs.integration-hermetic-shards.result }}",
        "COLLECTION": "${{ needs.preflight-policy.result }}",
    }
    for shards in ("success", "failure", "cancelled", "skipped", ""):
        for collection in ("success", "failure", "cancelled", "skipped", ""):
            result = subprocess.run(
                ["bash", "-c", gate["run"]], cwd=tmp_path,
                env=dict(os.environ, SHARDS=shards, COLLECTION=collection),
            )
            assert (result.returncode == 0) == (shards == collection == "success")
    download = steps["Download isolated hermetic evidence"]["with"]
    assert download == {
        "pattern": "integration-ci-hermetic-shard-*-attempt-*",
        "path": "artifacts/hermetic-owners", "merge-multiple": False,
    }
    assert steps["Download actual ownership collection"]["with"] == {
        "name": "${{ needs.preflight-policy.outputs.artifact }}",
        "path": "artifacts/hermetic-ownership",
    }
    command = steps["Verify exact corpus, phase ownership, and security evidence"]["run"]
    assert "tools/ci/hermetic_shards.py aggregate" in command
    assert "--root artifacts/hermetic-owners" in command
    assert "--ownership-manifest artifacts/hermetic-ownership/test-ownership.json" in command
    assert "--ownership-attempt '${{ needs.preflight-policy.outputs.attempt }}'" in command
    assert "integration-hermetic" in jobs["ci-required"]["needs"]
    assert "integration-hermetic" in jobs["omnigent-deterministic-conformance"]["needs"]
    conformance_downloads = [
        step["with"] for step in jobs["omnigent-deterministic-conformance"]["steps"]
        if step.get("with", {}).get("pattern") == "integration-ci-hermetic-shard-*-attempt-*"
    ]
    assert len(conformance_downloads) == 1
    assert conformance_downloads[0].get("merge-multiple", False) is False


def test_hermetic_physical_rows_own_qualifications_and_isolated_receipts():
    steps = {step["name"]: step for step in _load_workflow()["jobs"]["integration-hermetic-shards"]["steps"]}
    for name in ("Verify isolated Docker socket proxy", "Verify real scoped egress connections and owned cleanup"):
        assert steps[name]["if"] == "matrix.shard == 1"
        assert "artifacts/integration-ci-${{ matrix.suite }}/" in steps[name]["run"]
    run = steps["Run hermetic integration CI suite"]
    assert run["env"]["MOONMIND_HERMETIC_SHARD"] == "${{ matrix.shard }}"
    assert run["env"]["MOONMIND_CI_RECEIPT"] == "artifacts/integration-ci-${{ matrix.suite }}/main.receipt.json"
    assert run["env"]["MOONMIND_CI_SUITE"] == "integration-${{ matrix.suite }}"
    assert "github.run_id" in run["env"]["MOONMIND_TEST_COMPOSE_PROJECT_NAME"]
    assert "github.run_attempt" in run["env"]["MOONMIND_TEST_COMPOSE_PROJECT_NAME"]


HEAVY_ADMISSION_OWNERS = (
    "backend-matrix",
    "integration-hermetic-shards",
    "prepare-app-image",
    "integration-ci",
    "prepare-recovery-images",
    "omnigent-host-recovery",
    "omnigent-exact-artifact",
)


def _admitted_job(
    job,
    *,
    preflight="success",
    phase="full",
    selected=True,
    cancelled=False,
    candidate_image="",
    recovery_images="",
    selector="success",
):
    """Evaluate the small Actions expression subset used by these actual jobs."""
    needs = job.get("needs", [])
    needs = [needs] if isinstance(needs, str) else needs
    results = {name: "success" for name in needs}
    results["preflight-policy"] = preflight
    results["select-test-suites"] = selector
    if candidate_image:
        results["prepare-app-image"] = "skipped"
    if recovery_images:
        results["prepare-recovery-images"] = "skipped"
    expression = job.get("if", "true")
    # Actions applies success() unless a status check overrides it.
    if not re.search(r"\b(?:always|cancelled|success|failure)\(\)", expression):
        if cancelled or any(results[name] != "success" for name in needs):
            return False

    def replace(match):
        path = match.group(0)
        if path.startswith("inputs."):
            return repr(
                {
                    "verification_phase": phase,
                    "candidate_image": candidate_image,
                    "recovery_images_artifact": recovery_images,
                }.get(path[7:], "")
            )
        owner, field = path[6:].split(".", 1)
        if field == "result":
            return repr(results.get(owner, ""))
        assert field.startswith("outputs.")
        return repr("true" if selected else "false")

    expression = re.sub(r"(?:needs|inputs)\.[A-Za-z0-9_.-]+", replace, expression)
    expression = expression.replace("cancelled()", repr(cancelled)).replace(
        "always()", "True"
    )
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression).strip()
    return bool(eval(expression, {"__builtins__": {}}, {}))


@pytest.mark.parametrize("owner", HEAVY_ADMISSION_OWNERS)
@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_selected_heavy_jobs_cannot_start_after_rejected_selector(owner, result):
    job = _load_workflow()["jobs"][owner]
    assert "preflight-policy" not in job["needs"]
    assert not _admitted_job(job, selector=result)
    assert _admitted_job(job)


@pytest.mark.parametrize("owner", HEAVY_ADMISSION_OWNERS)
def test_heavy_admission_preserves_selection_and_cancellation(owner):
    job = _load_workflow()["jobs"][owner]
    assert not _admitted_job(job, selected=False)
    assert not _admitted_job(job, cancelled=True)
    assert not _admitted_job(job, selector="failure")


@pytest.mark.parametrize("phase", ["artifact", "recovery"])
@pytest.mark.parametrize(
    "owner",
    [
        "integration-ci",
        "prepare-recovery-images",
        "omnigent-host-recovery",
        "omnigent-exact-artifact",
    ],
)
def test_image_phase_intentional_source_skip_does_not_deadlock(owner, phase):
    job = _load_workflow()["jobs"][owner]
    assert _admitted_job(
        job, preflight="skipped", phase=phase, candidate_image="image@sha256:test"
    )
    for result in ("failure", "cancelled", "skipped", ""):
        assert not _admitted_job(
            job, selector=result, phase=phase, candidate_image="image@sha256:test"
        )


@pytest.mark.parametrize(
    "event",
    ["pull_request", "merge_group", "workflow_dispatch", "schedule", "workflow_call"],
)
def test_fast_policy_admission_has_no_draft_or_event_deferral(event):
    jobs = _load_workflow()["jobs"]
    selected = select_suites(
        [".github/workflows/pytest-unit-tests.yml"], event_name=event
    )
    assert selected.as_outputs()["integration_hermetic"] == "true"
    for owner in HEAVY_ADMISSION_OWNERS:
        assert "github.event" not in jobs[owner]["if"]
        assert "draft" not in jobs[owner]["if"]
        assert _admitted_job(jobs[owner])
    for fast in ("frontend-static", "run-generated-contracts"):
        assert jobs[fast]["needs"] == "select-test-suites"


@pytest.mark.parametrize("preflight", ["failure", "cancelled", "skipped"])
def test_preflight_rejection_never_turns_selected_skipped_work_green(
    tmp_path, preflight
):
    selection = select_suites(
        [".github/workflows/pytest-unit-tests.yml"], event_name="pull_request"
    ).as_outputs()
    selection["generated_contracts"] = "true"
    result = _run_required_gate(
        tmp_path, outputs=selection, results={"preflight-policy": preflight}
    )
    assert result.returncode != 0
    assert "was selected but ended with result=skipped" in result.stdout


def test_preflight_wait_keeps_existing_supersession_groups_isolated():
    workflow = _load_workflow()
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    group = concurrency["group"]
    assert "github.event.pull_request.number || github.ref" in group
    assert "inputs.verification_phase && github.run_id" in group
    assert "inputs.candidate_image && github.run_id" in group
    triggers = _workflow_triggers(workflow)
    assert triggers["merge_group"]["types"] == ["checks_requested"]
    # Draft pushes are real candidates; no ready-only or draft-only admission.
    assert triggers["pull_request"]["types"] == ["opened", "synchronize", "reopened"]


FAST_POLICY_STEPS = (
    "Verify workflow terminology guardrails",
    "Check removed capability semantics",
    "Audit status token domains",
    "Reject task-specific GitHub workflow names",
)


def test_selector_fast_policy_is_single_owner_before_selection_outputs():
    jobs = _load_workflow()["jobs"]
    selector = jobs["select-test-suites"]
    steps = selector["steps"]
    select_index = next(i for i, step in enumerate(steps) if step.get("id") == "select")
    for name in FAST_POLICY_STEPS:
        owners = [
            (owner, step)
            for owner, job in jobs.items()
            for step in job.get("steps", [])
            if step.get("name") == name
        ]
        assert len(owners) == 1
        assert owners[0][0] == "select-test-suites"
        step = owners[0][1]
        assert steps.index(step) < select_index
        assert not step.get("continue-on-error", False)
        assert (
            step["if"]
            == "inputs.verification_phase != 'artifact' && inputs.verification_phase != 'recovery'"
        )
    assert "needs" not in jobs["preflight-policy"]
    for owner in HEAVY_ADMISSION_OWNERS:
        assert "preflight-policy" not in jobs[owner]["needs"]
        assert not _admitted_job(jobs[owner], selector="failure")
        assert _admitted_job(jobs[owner], preflight="failure")
    assert "preflight-policy" in jobs["ci-required"]["needs"]


def test_selector_fast_policy_executes_real_rejection_before_admission(tmp_path):
    import shutil
    import subprocess

    steps = _load_workflow()["jobs"]["select-test-suites"]["steps"]
    guard = next(step for step in steps if step.get("name") == FAST_POLICY_STEPS[0])
    for path in (
        "tools/verify_workflow_terminology.py",
        "tests/unit/api/test_executions_temporal.py",
        "tests/contract/test_temporal_execution_api.py",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / path, target)
    clean = subprocess.run(
        ["bash", "-c", guard["run"]], cwd=tmp_path, capture_output=True, text=True
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    bad = tmp_path / "tests/unit/invalid_workflow.py"
    bad.write_text("workflow_type = " + repr("MoonMind." + "Run") + "\n")
    rejected = subprocess.run(
        ["bash", "-c", guard["run"]], cwd=tmp_path, capture_output=True, text=True
    )
    assert rejected.returncode != 0
    assert "legacy-user-workflow-type" in rejected.stdout


@pytest.mark.parametrize(
    "phase,admitted",
    [
        ("", True),
        ("full", True),
        ("source", True),
        ("artifact", False),
        ("recovery", False),
    ],
)
def test_selector_fast_policy_preserves_image_phase_exclusions(phase, admitted):
    steps = _load_workflow()["jobs"]["select-test-suites"]["steps"]
    for step in steps:
        if step.get("name") in FAST_POLICY_STEPS:
            assert _admitted_job(step, phase=phase) is admitted


@pytest.mark.parametrize("preflight", ["failure", "cancelled", "skipped", ""])
def test_late_ownership_failure_blocks_merge_after_successful_selected_work(
    tmp_path, preflight
):
    selection = select_suites(
        [".github/workflows/pytest-unit-tests.yml"], event_name="pull_request"
    ).as_outputs()
    selection["generated_contracts"] = "true"
    results = {
        name: "success" for name in _load_workflow()["jobs"]["ci-required"]["needs"]
    }
    results["preflight-policy"] = preflight
    result = _run_required_gate(tmp_path, outputs=selection, results=results)
    assert result.returncode != 0
    assert "preflight-policy is always required but ended with result=" in result.stdout


def test_pull_request_triggers_cover_candidates_without_duplicate_runs():
    types = _workflow_triggers(_load_workflow())["pull_request"]["types"]
    # No job reads the draft flag, so a draft push already gets the full
    # candidate result; ready_for_review would only cancel and repeat it.
    assert "pull_request.draft" not in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "ready_for_review" not in types
    # `edited` also fires for title/body edits, whose skipped jobs would replace
    # the head's real ci-required result. A base retarget reruns through the
    # next push or a reopen, which select against the new base.
    assert "edited" not in types
    assert {"synchronize", "reopened"} <= set(types)
