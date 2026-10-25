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


def test_integration_rows_retain_row_owned_redacted_reports() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["integration-ci"]

    assert job["runs-on"] == "ubuntu-latest"
    steps = {step["name"]: step for step in job["steps"]}
    assert all(name != "Verify docker compose availability" for name in steps)

    # Each row collects only what its own owner wrote: the hermetic wrapper's
    # report, log and project diagnostics, or the journey's own log tree. The
    # controller-journey row also runs the journal transition test, which
    # writes its disposable project's redacted phase, ps and log evidence. No
    # shared step queries a Compose project the row did not create.
    collect = steps["Collect row-owned integration report and diagnostics"]
    assert collect["if"] == (
        "always() && (matrix.suite == 'hermetic' || failure() || cancelled())"
    )
    assert "artifacts/integration-ci" in collect["run"]
    assert "var/artifacts/first-run-3938" in collect["run"]
    assert "var/artifacts/journal-transition" in collect["run"]
    assert "docker-compose.test.yaml" not in collect["run"]
    assert "tools/ci/redact_diagnostics.py" in collect["run"]
    assert "not zero tests" in collect["run"]
    assert int(collect["timeout-minutes"]) <= 3

    upload = steps["Upload row-owned integration report and diagnostics"]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == collect["if"]
    assert "${{ matrix.suite }}" in upload["with"]["name"]
    assert "${{ github.run_attempt }}" in upload["with"]["name"]
    assert upload["with"]["path"] == "/tmp/pytest-integration-ci"
    assert int(upload["timeout-minutes"]) <= 3


def _run_integration_collect(tmp_path: Path, suite: str) -> Path:
    import re
    import subprocess

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
    hermetic = steps["Run hermetic integration CI suite"]
    assert hermetic["env"]["MOONMIND_INTEGRATION_FAIL_FAST"] == (
        "${{ github.event_name != 'schedule' && '1' || '0' }}"
    )
    # Include the wrapper's interrupt grace and the kill grace for each of
    # its three diagnostics/teardown commands before the native step bound.
    budget_seconds = int(hermetic["env"]["MOONMIND_INTEGRATION_TIMEOUT"])
    assert budget_seconds + 30 + 3 * (120 + 10) < int(hermetic["timeout-minutes"]) * 60
    for name in (
        "Install the journey browser's Playwright runtime",
        "Install the journey browser OS dependencies",
        "Download the journey Chromium browser",
        "Run hermetic integration CI suite",
        "Run disposable default first-run journey",
        "Run eligible-upgrade journey",
        "Run Settings Operations controller journey",
        "Qualify active journal transition and host controller refresh",
        "Upload redacted journey startup timings and resource footprints",
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


def test_hermetic_job_reserves_bounded_setup_execution_and_reporting() -> None:
    job = _load_workflow()["jobs"]["integration-ci"]
    run_index = next(
        index
        for index, step in enumerate(job["steps"])
        if step["name"] == "Run hermetic integration CI suite"
    )
    proxy_step = next(
        step
        for step in job["steps"][:run_index]
        if step["name"] == "Verify isolated Docker socket proxy"
    )
    assert proxy_step["if"] == "matrix.suite == 'hermetic'"
    proxy_minutes = int(proxy_step["timeout-minutes"])
    assert 0 < proxy_minutes <= 5
    setup_minutes = 0
    for step in job["steps"][:run_index]:
        if step is proxy_step:
            continue
        # Every setup phase must finish or time out before it can consume
        # the wrapper's cleanup and evidence-upload reserve.
        minutes = _integration_row_timeout_minutes(step["timeout-minutes"], "hermetic")
        assert 0 < minutes <= 6, step["name"]
        setup_minutes += minutes
        if "if" not in step:
            assert (
                _integration_row_timeout_minutes(
                    step["timeout-minutes"], "fresh-journey"
                )
                == 2
            )

    assert setup_minutes <= 12
    execution_minutes = int(job["steps"][run_index]["timeout-minutes"])
    report_steps = [
        step
        for step in job["steps"]
        if step["name"]
        in {
            "Collect row-owned integration report and diagnostics",
            "Upload row-owned integration report and diagnostics",
        }
    ]
    assert len(report_steps) == 2
    reporting_minutes = sum(int(step["timeout-minutes"]) for step in report_steps)
    assert reporting_minutes == 4
    # Include one minute for runner dispatch and post-action housekeeping;
    # the wrapper's complete cleanup budget is already inside its native step.
    assert (
        setup_minutes + proxy_minutes + execution_minutes + reporting_minutes + 1
        <= _integration_row_timeout_minutes(job["timeout-minutes"], "hermetic")
    )


def test_integration_ci_runs_each_boundary_on_its_own_row() -> None:
    # The hermetic suite and the three disposable Compose journeys each own
    # an isolated Compose project.
    # Running them as parallel rows keeps every boundary while the slowest
    # row, not their serial sum, bounds integration-ci.
    job = _load_workflow()["jobs"]["integration-ci"]

    assert json.loads(select_suites([]).as_outputs()["integration_matrix"])[
        "suite"
    ] == [
        "hermetic",
        "fresh-journey",
        "upgrade-journey",
        "controller-journey",
    ]
    assert job["strategy"]["fail-fast"] == "${{ github.event_name != 'schedule' }}"
    assert {
        suite: _integration_row_timeout_minutes(job["timeout-minutes"], suite)
        for suite in (
            "hermetic",
            "fresh-journey",
            "upgrade-journey",
            "controller-journey",
        )
    } == {
        "hermetic": 45,
        "fresh-journey": 85,
        "upgrade-journey": 85,
        "controller-journey": 85,
    }

    steps = {step["name"]: step for step in job["steps"]}
    row_commands = {
        "Run hermetic integration CI suite": (
            "hermetic",
            "./tools/test_integration.sh",
        ),
        "Run disposable default first-run journey": (
            "fresh-journey",
            "./tools/first_run_journey_3938.sh",
        ),
        "Run eligible-upgrade journey": (
            "upgrade-journey",
            "./tools/first_run_journey_3938.sh --upgrade",
        ),
        "Run Settings Operations controller journey": (
            "controller-journey",
            "./tools/first_run_journey_3938.sh --controller",
        ),
    }
    for name, (row, command) in row_commands.items():
        assert steps[name]["if"] == f"matrix.suite == '{row}'"
        assert command in [line.strip() for line in steps[name]["run"].splitlines()]

    # Only the journey rows drive the compiled dashboard in a browser.
    for name in (
        "Set up Node for the dashboard journey",
        "Install the journey browser's Playwright runtime",
        "Install the journey browser OS dependencies",
        "Download the journey Chromium browser",
    ):
        assert steps[name]["if"] == "endsWith(matrix.suite, '-journey')"


@pytest.mark.parametrize(
    ("suite", "execution_minutes"),
    [("fresh-journey", 20), ("upgrade-journey", 25), ("controller-journey", 20)],
)
@pytest.mark.parametrize("image_source", ["local", "registry", "archive"])
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
        for key, present in (
            ("candidate_image", image_source != "local"),
            ("candidate_oci_artifact", image_source == "archive"),
        ):
            if present and f"inputs.{key} == ''" in condition:
                return False
            if not present and f"inputs.{key} != ''" in condition:
                return False
        return condition != "matrix.suite == 'hermetic'"

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
            if step["name"]
            == "Upload redacted journey startup timings and resource footprints"
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
    assert setup_minutes == {"local": 50, "registry": 42, "archive": 49}[image_source]
    assert (
        setup_minutes
        + execution_minutes
        + reporting_minutes
        + fixture_cleanup_minutes
        + 4
        <= (_integration_row_timeout_minutes(job["timeout-minutes"], suite))
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

    required_job = jobs["check-generated-contracts"]
    assert required_job["needs"] == [
        "select-test-suites",
        "run-generated-contracts",
    ]
    assert required_job["if"] == "always()"
    required_script = "\n".join(
        (step.get("run") or "") for step in required_job["steps"] if "run" in step
    )
    assert "needs.select-test-suites.result" in required_script
    assert "needs.select-test-suites.outputs.generated_contracts" in required_script
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

    assert job["needs"] == "select-test-suites"
    assert (
        job["if"]
        == "needs.select-test-suites.outputs.omnigent_conformance == 'true'"
    )
    assert "omnigent_conformance" in workflow["jobs"]["select-test-suites"]["outputs"]


def test_image_building_jobs_share_a_layer_cache() -> None:
    workflow = _load_workflow()
    integration_steps = workflow["jobs"]["integration-ci"]["steps"]
    builds = [
        step
        for step in integration_steps
        if (step.get("uses") or "").startswith("docker/build-push-action@")
    ]
    build = next(
        step for step in builds if step["with"].get("target") == "test-runtime"
    )
    assert build["if"] == "matrix.suite == 'hermetic'"
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

    # The journey rows build the same default deployable target with the same
    # build arguments as the exact-artifact gate, so they read the scope that
    # gate writes. The gate stays the only writer: the parallel journey rows
    # export nothing.
    candidate = next(step for step in builds if "target" not in step["with"])
    assert candidate["if"] == "matrix.suite != 'hermetic' && inputs.candidate_image == ''"
    assert candidate["with"]["file"] == exact_build["with"]["file"]
    assert candidate["with"]["build-args"] == exact_build["with"]["build-args"]
    assert candidate["with"]["cache-from"] == exact_build["with"]["cache-from"]
    assert "cache-to" not in candidate["with"]
    scope = candidate["with"]["cache-from"].split("scope=")[1].split(",")[0]
    assert f"scope={scope}," in exact_build["with"]["cache-to"]


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
    assert "if" not in job
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
        "backend-matrix",
        "unit-slow",
        "integration-ci",
        "omnigent-exact-artifact",
        "omnigent-deterministic-conformance",
        "test-frontend",
        "check-generated-contracts",
    ):
        assert dependency in job["needs"]
    for removed in (
        "unit-fast",
        "api-component",
        "temporal-boundary",
        "reliability-journey-checkpoint-resume",
        # Static invariants now run inside preflight-policy.
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
        "test-frontend",
        "check-generated-contracts",
    ):
        assert name in script

    # Always-run aggregators and the always-run preflight invariants (policy,
    # MoonSpec projection, shard ownership) are aggregated unconditionally
    # (MoonLadderStudios/MoonMind#3950): a failing frontend or
    # generated-contract gate must fail ci-required, and targeted PRs must not
    # trip a success-but-unselected failure on an always-run invariant.
    assert 'require_always "test-frontend"' in script
    assert "needs.test-frontend.result" in script
    assert 'require_always "check-generated-contracts"' in script
    assert "needs.check-generated-contracts.result" in script
    assert 'require_always "preflight-policy"' in script
    assert "needs.preflight-policy.result" in script
    assert 'require_selected "preflight-policy"' not in script


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
    assert "tools/verify_workflow_terminology.py --mode runtime" in scripts
    assert "tools/check_removed_capability_semantics.py" in scripts
    assert "tools/status_domain_audit.py" in scripts
    assert "tools/audit_status_tokens.py --fail-on-unknown" in scripts
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

    # The repository policy scans and the shard-ownership collection walk the
    # working tree, so MoonSpec is initialized only after they have run and
    # they keep checking the same tree as before.
    init_index = next(
        index
        for index, run in enumerate(runs)
        if "git submodule update --init --depth 1 -- moonspec" in run
    )
    for tree_walker in (
        "tools/verify_workflow_terminology.py",
        "tools/check_removed_capability_semantics.py",
        "tools/status_domain_audit.py",
        "tools/audit_status_tokens.py",
        "tools/verify_test_shard_ownership.py",
    ):
        walker_index = next(
            index for index, run in enumerate(runs) if tree_walker in run
        )
        assert walker_index < init_index, tree_walker
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
    assert rows["unit-fast"]["job_minutes"] == 21
    unit_step = next(
        step for step in job["steps"] if step["name"] == "Run selected unit suite"
    )
    assert unit_step["timeout-minutes"] == 15
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


def _run_required_gate(tmp_path, *, outputs=None, results=None):
    import os
    import re
    import subprocess

    selected = select_suites(["README.md"], event_name="pull_request").as_outputs()
    selected.setdefault("backend_matrix", '{"include":[]}')
    selected.setdefault("integration_matrix", '{"suite":[]}')
    selected.update(outputs or {})
    needs = {
        name: {
            "result": (
                "success"
                if name
                in {
                    "select-test-suites",
                    "preflight-policy",
                    "test-frontend",
                    "check-generated-contracts",
                }
                else "skipped"
            )
        }
        for name in _load_workflow()["jobs"]["ci-required"]["needs"]
    }
    needs["select-test-suites"]["outputs"] = selected
    for name, result in (results or {}).items():
        needs[name]["result"] = result
    steps = _load_workflow()["jobs"]["ci-required"]["steps"]
    script = "\n".join(step.get("run", "") for step in steps)

    def resolve(match):
        path = match.group(1).strip().split(".")
        value = {"needs": needs}
        for key in path:
            value = value.get(key, "")
        return str(value)

    script = re.sub(r"\$\{\{\s*(needs\.[^}]+)\}\}", resolve, script)
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
            "backend_matrix": '{"include":[{"suite":"unit-fast","job_minutes":21}]}',
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
        ("integration-ci", "integration_matrix"),
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
                ("integration-ci", "integration_ci"),
                ("omnigent-exact-artifact", "exact_artifact"),
                ("omnigent-deterministic-conformance", "omnigent_conformance"),
            )
        },
    }
    proc = _run_required_gate(tmp_path, outputs=outputs, results=results)
    assert proc.returncode == 0, proc.stdout + proc.stderr


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
