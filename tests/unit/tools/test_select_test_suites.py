from __future__ import annotations

from pathlib import Path

import pytest

from tools import select_test_suites
from tools.select_test_suites import (
    OMNIGENT_CONFORMANCE_INPUT_EXACT,
    OMNIGENT_CONTRACT_GATE_KEYS,
    is_exact_artifact_owned,
    is_omnigent_conformance_input,
    is_omnigent_contract_owned,
    select_suites,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _outputs(paths: list[str], **kwargs) -> dict[str, str]:
    return select_suites(paths, event_name="pull_request", **kwargs).as_outputs()


@pytest.mark.parametrize(
    "changed_path",
    ["AGENTS.md", "docs/Development/PreCommitWorkflow.md"],
)
def test_docs_only_change_does_not_select_heavy_backend_suites(
    changed_path: str,
) -> None:
    outputs = _outputs([changed_path])

    assert outputs == {
        "unit_fast": "true",
        "unit_slow": "false",
        "api_component": "false",
        "temporal_boundary": "false",
        "integration_ci": "false",
        "reliability_journey": "false",
        "exact_artifact": "false",
        "omnigent_conformance": "false",
        "full_backend": "false",
        "frontend_static": "false",
        "frontend_browser_chromium": "false",
        "frontend_browser_firefox": "false",
        "full_frontend": "false",
    }


def test_backend_only_change_skips_frontend() -> None:
    outputs = _outputs(["api_service/services/execution_service.py"])
    assert outputs["frontend_static"] == "false"
    assert outputs["frontend_browser_chromium"] == "false"
    assert outputs["frontend_browser_firefox"] == "false"


def test_frontend_source_selects_static_and_chromium() -> None:
    outputs = _outputs(["frontend/src/components/Workflow.tsx"])
    assert outputs["frontend_static"] == "true"
    assert outputs["frontend_browser_chromium"] == "true"
    assert outputs["frontend_browser_firefox"] == "false"


def test_generated_openapi_client_selects_static_only() -> None:
    outputs = _outputs(["frontend/src/generated/openapi.ts"])
    assert outputs["frontend_static"] == "true"
    assert outputs["frontend_browser_chromium"] == "false"
    assert outputs["frontend_browser_firefox"] == "false"


def test_browser_sensitive_changes_select_both_engines() -> None:
    for path in (
        "frontend/src/browser/layout.browser.test.ts",
        "frontend/src/styles/dashboard.css",
        "frontend/vitest.browser.config.ts",
    ):
        outputs = _outputs([path])
        assert outputs["frontend_static"] == "true"
        assert outputs["frontend_browser_chromium"] == "true"
        assert outputs["frontend_browser_firefox"] == "true"


def test_package_lock_selects_full_frontend() -> None:
    outputs = _outputs(["package-lock.json"])
    assert outputs["full_frontend"] == "true"
    assert outputs["frontend_static"] == "true"
    assert outputs["frontend_browser_chromium"] == "true"
    assert outputs["frontend_browser_firefox"] == "true"


def test_api_router_change_selects_unit_fast_and_component() -> None:
    outputs = _outputs(["api_service/api/routers/workflow_console.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["api_component"] == "true"
    assert outputs["temporal_boundary"] == "false"
    assert outputs["integration_ci"] == "false"
    assert outputs["full_backend"] == "false"


def test_db_change_selects_component_and_integration_ci() -> None:
    outputs = _outputs(["api_service/db/models.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["api_component"] == "true"
    assert outputs["integration_ci"] == "true"
    assert outputs["temporal_boundary"] == "false"


def test_service_change_selects_component_suite() -> None:
    outputs = _outputs(["api_service/services/execution_service.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["api_component"] == "true"
    assert outputs["integration_ci"] == "false"


def test_projection_sync_change_selects_its_integration_boundary() -> None:
    """MoonLadderStudios/MoonMind#3927: select projection repair coverage."""
    outputs = _outputs(["api_service/core/sync.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["integration_ci"] == "true"
    assert outputs["full_backend"] == "false"


def test_temporal_workflow_change_selects_temporal_boundary() -> None:
    outputs = _outputs(["moonmind/workflows/temporal/workflows/run.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["temporal_boundary"] == "true"
    assert outputs["api_component"] == "false"
    assert outputs["reliability_journey"] == "true"


def test_workflow_visible_adapters_select_boundary_and_reliability() -> None:
    for path in (
        "moonmind/workflows/adapters/managed_agent_adapter.py",
        "moonmind/workflows/adapters/codex_session_adapter.py",
    ):
        outputs = _outputs([path])

        assert outputs["temporal_boundary"] == "true"
        assert outputs["reliability_journey"] == "true"


def test_reliability_journey_production_seams_are_selected() -> None:
    paths = (
        "moonmind/workflows/temporal/workflows/run.py",
        "moonmind/workflows/temporal/checkpoint_policy.py",
        "moonmind/schemas/agent_runtime_models.py",
        "moonmind/schemas/execution_checkpoint_models.py",
        "moonmind/schemas/temporal_models.py",
        ".agents/skills/pr-resolver/SKILL.md",
        ".agents/skills/pr-resolver/tools/orchestrate.py",
        "api_service/Dockerfile",
        "api_service/docker/install_cli_tooling.sh",
        "tests/integration/reliability/replays/incomplete-terminal-contract/manifest.json",
        "tests/helpers/codex_session_runtime.py",
        ".github/workflows/pytest-unit-tests.yml",
        "tools/start-worker.sh",
        "moonmind/agents/codex_worker/worker.py",
        "moonmind/schemas/workspace_locator_models.py",
        "moonmind/schemas/recovery_models.py",
        "api_service/services/checkpoint_branch_service.py",
        "api_service/migrations/versions/999_checkpoint_replay.py",
    )

    for path in paths:
        assert _outputs([path])["reliability_journey"] == "true", path


def test_unknown_backend_path_fails_open_to_reliability_journey() -> None:
    outputs = _outputs(["new_runtime_backend/worker.py"])

    assert outputs["full_backend"] == "true"
    assert outputs["reliability_journey"] == "true"


def test_managed_session_schema_selects_boundary_and_reliability() -> None:
    outputs = _outputs(["moonmind/schemas/managed_session_models.py"])

    assert outputs["temporal_boundary"] == "true"
    assert outputs["reliability_journey"] == "true"


def test_mixed_frontend_and_adapter_change_selects_reliability() -> None:
    outputs = _outputs(
        [
            "frontend/src/components/Workflow.tsx",
            "moonmind/workflows/adapters/managed_agent_adapter.py",
        ]
    )

    assert outputs["temporal_boundary"] == "true"
    assert outputs["reliability_journey"] == "true"


def test_temporal_schema_change_selects_temporal_boundary() -> None:
    outputs = _outputs(["moonmind/schemas/temporal_activity_models.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["temporal_boundary"] == "true"


def test_temporal_catalog_generator_change_selects_temporal_boundary() -> None:
    """MoonLadderStudios/MoonMind#3959: generator, generated reference, and
    lifecycle-anchor doc must select the drift/composition checks."""
    for path in (
        "tools/generate_temporal_catalog.py",
        "docs/Temporal/WorkflowTypeCatalogGenerated.md",
        "docs/Temporal/WorkflowTypeCatalogAndLifecycle.md",
        "tests/unit/workflows/temporal/test_workflow_catalog_generator.py",
        "services/temporal/scripts/bootstrap-namespace.sh",
    ):
        outputs = _outputs([path])

        assert outputs["unit_fast"] == "true", path
        assert outputs["temporal_boundary"] == "true", path


def test_integration_test_change_selects_integration_ci() -> None:
    outputs = _outputs(["tests/integration/api/test_workflow_console_routes.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["integration_ci"] == "true"


def test_reliability_only_change_does_not_select_integration_ci() -> None:
    outputs = _outputs(["tests/integration/reliability/test_checkpoint_resume.py"])

    assert outputs["reliability_journey"] == "true"
    assert outputs["integration_ci"] == "false"


def test_known_slow_test_change_selects_unit_slow() -> None:
    outputs = _outputs(["tests/unit/api/routers/test_agent_runs.py"])

    assert outputs["unit_slow"] == "true"


def test_full_backend_includes_unit_slow() -> None:
    outputs = select_suites([], event_name="schedule").as_outputs()

    assert outputs["unit_slow"] == "true"


def test_api_service_migration_change_selects_integration_ci() -> None:
    outputs = _outputs(["api_service/migrations/versions/123_add_table.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["integration_ci"] == "true"


def test_pyproject_change_selects_full_backend() -> None:
    outputs = _outputs(["pyproject.toml"])

    assert all(value == "true" for value in outputs.values())


def test_workflow_change_selects_full_backend() -> None:
    outputs = _outputs([".github/workflows/pytest-unit-tests.yml"])

    assert all(value == "true" for value in outputs.values())


def test_unit_runner_change_selects_full_backend() -> None:
    outputs = _outputs(["tools/test_unit.sh"])

    assert all(value == "true" for value in outputs.values())


def test_empty_changed_file_input_selects_full_backend() -> None:
    outputs = _outputs([])

    assert all(value == "true" for value in outputs.values())


def test_unknown_path_fails_open_to_full_backend() -> None:
    outputs = _outputs(["Makefile"])

    assert all(value == "true" for value in outputs.values())


def test_main_push_selects_full_backend() -> None:
    outputs = select_suites(
        ["docs/Development/PreCommitWorkflow.md"],
        event_name="push",
        ref_name="main",
    ).as_outputs()

    assert all(value == "true" for value in outputs.values())


def test_manual_dispatch_selects_full_backend() -> None:
    outputs = select_suites(
        ["docs/Development/PreCommitWorkflow.md"],
        event_name="workflow_dispatch",
        ref_name="feature",
    ).as_outputs()

    assert all(value == "true" for value in outputs.values())


def test_main_rejects_interactive_stdin(monkeypatch, capsys) -> None:
    class _InteractiveStdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(select_test_suites.sys, "stdin", _InteractiveStdin())

    assert select_test_suites.main() == 1
    assert "expects a list of changed files via stdin" in capsys.readouterr().err


@pytest.mark.parametrize(
    "changed_path",
    [
        "api_service/api/routers/workflows.py",
        "api_service/services/artifact_service.py",
        "api_service/services/managed_agent_provider_profiles.py",
        "api_service/services/omnigent_hosts.py",
        "api_service/services/workspace_checkpoints.py",
        "moonmind/omnigent/bridge_store.py",
        "moonmind/workflows/temporal/workflows/run_bounded_story_loop.py",
        "tests/integration/omnigent/test_embedded_recovery.py",
    ],
)
def test_cumulative_remediation_boundaries_select_reliability_journey(
    changed_path: str,
) -> None:
    outputs = _outputs([changed_path])

    assert outputs["unit_fast"] == "true"
    assert outputs["reliability_journey"] == "true"


# --- Omnigent contract-owner inventory (MoonLadderStudios/MoonMind#3710) ---


@pytest.mark.parametrize(
    "changed_path",
    [
        # Core Omnigent runtime.
        "moonmind/omnigent/execute.py",
        "moonmind/omnigent/conformance.py",
        # Omnigent Temporal adapters, activities, and workflows.
        "moonmind/workflows/adapters/omnigent_agent_adapter.py",
        "moonmind/workflows/temporal/activities/omnigent_activities.py",
        "moonmind/workflows/temporal/workflows/omnigent_oauth_host_janitor.py",
        # Omnigent runtime schemas / compiled-intent contracts.
        "moonmind/schemas/workspace_intent.py",
        # Omnigent API and native-UI routers.
        "api_service/api/routers/omnigent_bridge.py",
        "api_service/api/routers/omnigent_native_ui.py",
        "api_service/services/omnigent_hosts.py",
        # Omnigent compatibility, conformance, and fault fixtures/tooling.
        "tools/run_omnigent_live_conformance.py",
        "tools/build_omnigent_conformance_report.py",
        "tests/integration/omnigent/test_embedded_recovery.py",
        "tests/unit/omnigent/test_conformance.py",
    ],
)
def test_omnigent_owned_change_selects_the_complete_contract_gate(
    changed_path: str,
) -> None:
    assert is_omnigent_contract_owned(changed_path), changed_path
    outputs = _outputs([changed_path])

    for key in OMNIGENT_CONTRACT_GATE_KEYS:
        assert outputs[key] == "true", (changed_path, key)


@pytest.mark.parametrize(
    "changed_path",
    [
        "frontend/src/features/workflow-native-chat/useWorkflowChatBinding.ts",
        "frontend/src/entrypoints/WorkflowChatNative.tsx",
        "frontend/src/entrypoints/workflow-detail.tsx",
        "frontend/src/lib/workflowDetailRoutes.ts",
    ],
)
def test_omnigent_frontend_integration_selects_gate_and_browser(
    changed_path: str,
) -> None:
    assert is_omnigent_contract_owned(changed_path), changed_path
    outputs = _outputs([changed_path])

    for key in OMNIGENT_CONTRACT_GATE_KEYS:
        assert outputs[key] == "true", (changed_path, key)
    # Native-UI / facade behavior must additionally exercise the compiled
    # production browser suite.
    assert outputs["frontend_static"] == "true"
    assert outputs["frontend_browser_chromium"] == "true"


def test_omnigent_native_ui_facade_backend_change_selects_browser() -> None:
    outputs = _outputs(["api_service/api/routers/omnigent_native_ui.py"])

    assert outputs["frontend_browser_chromium"] == "true"


def test_omnigent_core_backend_change_does_not_require_browser() -> None:
    # A non-facade backend Omnigent change selects the full backend contract
    # gate but should not unnecessarily pull in the browser suite.
    outputs = _outputs(["moonmind/omnigent/policies.py"])

    for key in OMNIGENT_CONTRACT_GATE_KEYS:
        assert outputs[key] == "true", key
    assert outputs["frontend_browser_chromium"] == "false"


def test_non_omnigent_paths_are_not_contract_owned() -> None:
    for path in (
        "api_service/api/routers/workflow_console.py",
        "api_service/services/execution_service.py",
        "moonmind/workflows/temporal/workflows/run.py",
        "docs/Development/PreCommitWorkflow.md",
        "frontend/src/components/Workflow.tsx",
    ):
        assert not is_omnigent_contract_owned(path), path


def test_docs_only_change_never_selects_omnigent_gate() -> None:
    outputs = _outputs(["docs/Omnigent/Overview.md"])

    for key in OMNIGENT_CONTRACT_GATE_KEYS:
        assert outputs[key] == ("true" if key == "unit_fast" else "false"), key


def test_markdown_renamed_away_still_selects_unit_fast() -> None:
    # compute_changed_files.sh reports both rename endpoints, so the removed
    # Markdown target keeps selecting the fast shard that owns the link,
    # metadata, and contract checks for unchanged callers.
    outputs = _outputs(["docs/Guide.md", "docs/Guide.txt"])

    assert outputs["unit_fast"] == "true"


# --- Tier-1 exact deployable-artifact gate (MoonLadderStudios/MoonMind#3710) ---


@pytest.mark.parametrize(
    "changed_path",
    [
        # Dependency and lockfile changes must always select the gate.
        "package-lock.json",
        "package.json",
        "poetry.lock",
        "pyproject.toml",
        # Dockerfiles, Compose, startup scripts, and runtime entrypoints.
        "api_service/Dockerfile",
        "docker-compose.test.yaml",
        "docker-compose.yaml",
        # The production API command installed as the image CMD.
        "api_service/entrypoint.sh",
        "api_service/docker/moonmind-docker-wrapper.sh",
        "tools/start-worker.sh",
        # The exact-artifact gate implementation itself.
        "moonmind/omnigent/exact_artifact_conformance.py",
        "tools/omnigent_exact_artifact_probe.py",
        "tools/run_omnigent_exact_artifact_conformance.py",
    ],
)
def test_deployable_artifact_change_selects_exact_artifact(changed_path: str) -> None:
    # Every parametrized path is a real repository path, so a typo cannot mask a
    # missing inventory entry (the ``api_service/docker/entrypoint.sh`` gap).
    assert (REPO_ROOT / changed_path).exists(), changed_path
    assert is_exact_artifact_owned(changed_path), changed_path
    assert _outputs([changed_path])["exact_artifact"] == "true", changed_path


@pytest.mark.parametrize(
    "inventory_name",
    ["EXACT_ARTIFACT_EXACT", "OMNIGENT_CONTRACT_EXACT", "OMNIGENT_FACADE_EXACT"],
)
def test_exact_path_inventories_reference_real_repository_paths(
    inventory_name: str,
) -> None:
    """An exact-path inventory entry that does not exist owns nothing.

    Prefix and glob rules may legitimately anticipate future files, but an
    exact path is only useful if it is the path the repository actually uses.
    """
    inventory = getattr(select_test_suites, inventory_name)
    missing = sorted(path for path in inventory if not (REPO_ROOT / path).exists())
    assert not missing, f"{inventory_name} references nonexistent paths: {missing}"


def test_omnigent_owned_change_selects_exact_artifact() -> None:
    outputs = _outputs(["moonmind/omnigent/policies.py"])
    assert outputs["exact_artifact"] == "true"


def test_non_deployable_backend_change_does_not_select_exact_artifact() -> None:
    for path in (
        "api_service/services/execution_service.py",
        "api_service/api/routers/workflow_console.py",
    ):
        outputs = _outputs([path])
        assert outputs["exact_artifact"] == "false", path


def test_docs_only_change_does_not_select_exact_artifact() -> None:
    assert _outputs(["docs/Omnigent/Overview.md"])["exact_artifact"] == "false"
    assert not is_exact_artifact_owned("docs/Omnigent/Overview.md")


def test_omnigent_owned_change_selects_deterministic_conformance() -> None:
    outputs = _outputs(["moonmind/omnigent/native_ui.py"])

    assert outputs["omnigent_conformance"] == "true"


@pytest.mark.parametrize(
    "changed_path",
    [
        "api_service/api/routers/executions.py",
        "moonmind/workflows/temporal/workflows/run.py",
        "tests/integration/temporal/test_compose_foundation.py",
        "docs/Omnigent/Overview.md",
    ],
)
def test_non_omnigent_change_skips_deterministic_conformance(changed_path: str) -> None:
    assert _outputs([changed_path])["omnigent_conformance"] == "false", changed_path


def test_full_verification_events_select_deterministic_conformance() -> None:
    outputs = select_suites(
        ["docs/Development/PreCommitWorkflow.md"],
        event_name="push",
        ref_name="main",
    ).as_outputs()

    assert outputs["omnigent_conformance"] == "true"
    assert "omnigent_conformance" in OMNIGENT_CONTRACT_GATE_KEYS


def test_conformance_runner_inputs_select_deterministic_conformance_only() -> None:
    for path in sorted(OMNIGENT_CONFORMANCE_INPUT_EXACT):
        outputs = _outputs([path])
        assert outputs["omnigent_conformance"] == "true", path
        assert outputs["full_backend"] == "false", path
        # An evidence input outside the owned inventory does not elevate the
        # complete Omnigent contract gate; its owning shard still runs it.
        if not is_omnigent_contract_owned(path):
            assert outputs["exact_artifact"] == "false", path


def test_conformance_input_inventory_matches_the_runner() -> None:
    from tools import run_omnigent_conformance as runner

    executed = {
        argument
        for command in runner.COMMANDS
        for argument in command
        if isinstance(argument, str)
        and argument.startswith(("tests/", "frontend/"))
        and not argument.startswith("--")
    }
    evidence = {path for paths in runner.EVIDENCE_GROUPS.values() for path in paths}
    profile = str(runner.PROFILE.relative_to(REPO_ROOT)).replace("\\", "/")
    report_builder = "tools/build_omnigent_conformance_report.py"

    for path in executed | evidence | {profile, report_builder}:
        # Directory arguments select through their prefix rule; probe a file
        # inside them so the check matches the way changed paths arrive.
        candidate = f"{path}/probe.py" if (REPO_ROOT / path).is_dir() else path
        assert is_omnigent_conformance_input(candidate), path
    for path in OMNIGENT_CONFORMANCE_INPUT_EXACT:
        assert (REPO_ROOT / path).exists(), path


@pytest.mark.parametrize(
    "path",
    sorted(select_test_suites.PROFILE_AUTHORING_EXACT)
    + [
        "frontend/src/runtime/runtimeTargets.ts",
        "moonmind/omnigent/runtime_provider_rollout.py",
        "moonmind/omnigent/harness_platform/planner.py",
    ],
)
def test_profile_authoring_changes_run_renderer_and_admission_replay(path):
    outputs = _outputs([path])
    assert outputs["frontend_static"] == "true"
    assert outputs["unit_fast"] == "true"
    assert outputs["api_component"] == "true"


@pytest.mark.parametrize(
    "path",
    [
        "moonmind/workflows/skills/deployment_release.py",
        "moonmind/workflows/temporal/worker_runtime.py",
        "api_service/services/recurring_workflows_service.py",
        "api_service/api/routers/recurring_workflows.py",
        "frontend/src/entrypoints/schedules.tsx",
        "tests/fixtures/reliability/release_worker.py",
    ],
)
def test_recurring_availability_boundaries_require_the_real_recovery_journeys(path):
    assert _outputs([path])["reliability_journey"] == "true"


def test_profile_execution_selection_test_change_runs_renderer_and_admission():
    """MoonLadderStudios/MoonMind#3950 R3: the #4033 regression's second half
    lives at tests/unit/api_service/ and must select the same renderer +
    admission pair as the routers-half, not only the generic api_component."""
    outputs = _outputs(["tests/unit/api_service/test_profile_execution_selection.py"])
    assert outputs["unit_fast"] == "true"
    assert outputs["api_component"] == "true"
    assert outputs["frontend_static"] == "true"


@pytest.mark.parametrize(
    "changed_path",
    [
        "moonmind/omnigent/compatibility.py",
        "moonmind/omnigent/effective_capabilities.py",
    ],
)
def test_native_capability_owners_select_browser_and_contract_gate(changed_path):
    """MoonLadderStudios/MoonMind#3950 R5 journey B: the versioned network-
    surface compatibility map and the capability inventory it gates own the
    compiled native UI/facade behavior. A change must select the complete
    Omnigent contract gate plus the compiled production browser suite."""
    assert (REPO_ROOT / changed_path).exists(), changed_path
    outputs = _outputs([changed_path])
    for key in OMNIGENT_CONTRACT_GATE_KEYS:
        assert outputs[key] == "true", (changed_path, key)
    assert outputs["frontend_static"] == "true"
    assert outputs["frontend_browser_chromium"] == "true"


def test_unknown_and_empty_diffs_conservatively_select_full_verification():
    """MoonLadderStudios/MoonMind#3950 R2: unknown/missing diffs select the
    required full corpus — conservative means more verification, never
    green-by-default."""
    for paths in ([], ["Makefile"], ["some/new/tool.sh"]):
        outputs = _outputs(paths)
        assert outputs["full_backend"] == "true", paths
        assert all(value == "true" for value in outputs.values()), paths
    # A mixed known + unknown diff still fails open to the full corpus.
    mixed = _outputs(["docs/Guide.md", "totally-unknown-path-xyz"])
    assert all(value == "true" for value in mixed.values())


def _aggregator_probe_script(cases: list[tuple[str, str, str]]) -> str:
    """Build a bash probe running the exact ci-required gate semantics.

    The function bodies below are verbatim copies of the
    `.github/workflows/pytest-unit-tests.yml` ci-required aggregator. The
    wiring assertions in the test below pin those bodies to the workflow
    file, so this probe executes the shipped guard rather than a detached
    reimplementation: commenting out a call, making it unreachable, or
    resetting `failures` before the final check breaks the wiring
    assertions, while weakening the conditional logic breaks the
    file-content pins and the matrix expectations here.
    """
    lines = [
        "set -uo pipefail",
        "failures=0",
        "record() { :; }",
        "require_always() {",
        '  local name="$1" result="$2"',
        '  record "$name" "always" "$result"',
        '  if [[ "$result" != "success" ]]; then',
        "    failures=$((failures + 1))",
        "  fi",
        "}",
        "require_selected() {",
        '  local name="$1" selected="$2" result="$3"',
        '  record "$name" "$selected" "$result"',
        '  if [[ "$selected" == "true" ]]; then',
        '    if [[ "$result" != "success" ]]; then',
        "      failures=$((failures + 1))",
        "    fi",
        '  elif [[ "$result" != "skipped" ]]; then',
        "    failures=$((failures + 1))",
        "  fi",
        "}",
    ]
    for name, selected, result in cases:
        lines.append(f'require_selected "{name}" "{selected}" "{result}"')
    lines.append('if [[ "$failures" -gt 0 ]]; then exit 1; fi')
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def _run_aggregator(cases: list[tuple[str, str, str]]) -> int:
    import subprocess

    proc = subprocess.run(
        ["bash", "-c", _aggregator_probe_script(cases)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode


def test_ci_required_aggregator_fails_on_bad_selected_results():
    """MoonLadderStudios/MoonMind#3950 R2/R9: the ci-required aggregator must
    fail when a selected gate fails, is canceled, is unexpectedly skipped, is
    absent, or produces no required result — and must fail when an unselected
    gate reports anything other than skipped. Asserted against the workflow
    structure so a YAML edit cannot silently weaken the guard, and executed
    against the gate semantics so string presence alone cannot stand in for
    the false-green behavior."""
    import subprocess

    workflow = (REPO_ROOT / ".github/workflows/pytest-unit-tests.yml").read_text()
    # Selected gates fail on any non-success result.
    assert "if [[ \"$result\" != \"success\" ]]; then" in workflow
    assert "was selected but ended with result=" in workflow
    # Unselected gates must report skipped; anything else fails.
    assert "elif [[ \"$result\" != \"skipped\" ]]; then" in workflow
    assert "was not selected but ended with result=" in workflow
    # Always-required gates (selector itself, shard ownership, frontend and
    # generated-contract aggregators) fail on any non-success.
    assert '"select-test-suites"' in workflow
    assert '"verify-test-shard-ownership"' in workflow
    assert '"test-frontend"' in workflow
    assert '"check-generated-contracts"' in workflow
    # Every selected backend gate is aggregated through require_selected.
    # The four primary backend suites share one native matrix aggregate
    # (MoonLadderStudios/MoonMind#4377); per-row completion is never inferred
    # from last-writer matrix outputs.
    for gate in (
        "backend-matrix",
        "unit-slow",
        "integration-ci",
        "omnigent-exact-artifact",
        "omnigent-deterministic-conformance",
    ):
        assert f'require_selected "{gate}"' in workflow, gate
    for removed in (
        "reliability-journey-checkpoint-resume",
    ):
        assert f'require_selected "{removed}"' not in workflow, removed
    # The consolidated fast/boundary suites are enforced through the matrix
    # aggregate, not as individual selected gates.
    for consolidated in (
        "unit-fast",
        "api-component",
        "temporal-boundary",
    ):
        assert f'require_selected "{consolidated}"' not in workflow, consolidated
    # The matrix aggregate derives selection from all four selector outputs.
    assert 'needs.select-test-suites.outputs.unit_fast' in workflow
    assert 'needs.select-test-suites.outputs.api_component' in workflow
    assert 'needs.select-test-suites.outputs.temporal_boundary' in workflow
    assert 'needs.select-test-suites.outputs.reliability_journey' in workflow
    assert 'needs.backend-matrix.result' in workflow
    # Wiring: each require_selected call must be reachable, not commented
    # out. A commented call still contains the string but never executes.
    reachable = [
        line
        for line in workflow.splitlines()
        if 'require_selected "' in line and not line.lstrip().startswith("#")
    ]
    for gate in (
        "backend-matrix",
        "unit-slow",
        "integration-ci",
        "omnigent-exact-artifact",
        "omnigent-deterministic-conformance",
    ):
        assert any(f'require_selected "{gate}"' in line for line in reachable), gate
    # Wiring: the failure accumulator must terminate the job. Resetting
    # `failures` after the gate calls or dropping the final guard would let
    # a failing gate report success.
    assert 'if [[ "$failures" -gt 0 ]]; then' in workflow
    tail = workflow.split('if [[ "$failures" -gt 0 ]]; then')[-1]
    assert "exit 1" in tail.split("All required backend checks passed.")[0]
    # No `failures=0` reset may appear after the first require_selected call.
    first_call = workflow.index('require_selected "backend-matrix"')
    assert "failures=0" not in workflow[first_call:]
    # Execute the gate semantics: selected gates pass only on success.
    assert _run_aggregator([("unit-fast", "true", "success")]) == 0
    assert _run_aggregator([("unit-fast", "true", "failure")]) == 1
    assert _run_aggregator([("unit-fast", "true", "cancelled")]) == 1
    assert _run_aggregator([("unit-fast", "true", "skipped")]) == 1
    assert _run_aggregator([("unit-fast", "true", "")]) == 1
    # Unselected gates pass only when skipped; any other result fails.
    assert _run_aggregator([("unit-fast", "false", "skipped")]) == 0
    assert _run_aggregator([("unit-fast", "false", "success")]) == 1
    assert _run_aggregator([("unit-fast", "false", "failure")]) == 1
    assert _run_aggregator([("unit-fast", "false", "")]) == 1
    # Mixed matrix: one bad gate among good ones still fails the aggregate.
    assert (
        _run_aggregator(
            [
                ("unit-fast", "true", "success"),
                ("api-component", "true", "failure"),
                ("integration-ci", "false", "skipped"),
            ]
        )
        == 1
    )
    assert (
        _run_aggregator(
            [
                ("unit-fast", "true", "success"),
                ("api-component", "true", "success"),
                ("integration-ci", "false", "skipped"),
            ]
        )
        == 0
    )
    # The probe itself is pinned to the shipped file: the workflow must
    # contain the exact conditional structure the probe executes.
    assert workflow.count('if [[ "$result" != "success" ]]; then') >= 1
    assert workflow.count('elif [[ "$result" != "skipped" ]]; then') >= 1
    # Sanity: bash is available for the probe above.
    assert subprocess.run(["bash", "--version"], capture_output=True).returncode == 0


def test_ci_required_backend_matrix_states():
    """MoonLadderStudios/MoonMind#4366 R7: every selected reliability shard
    funnels through the backend-matrix aggregate, so a failed, timed-out,
    canceled, or unexpectedly skipped matrix entry fails ci-required while
    an intentionally unselected matrix stays an intentional skip."""
    assert _run_aggregator([("backend-matrix", "true", "success")]) == 0
    assert _run_aggregator([("backend-matrix", "true", "failure")]) == 1
    assert _run_aggregator([("backend-matrix", "true", "cancelled")]) == 1
    # A selected matrix that reports skipped did not run its shards.
    assert _run_aggregator([("backend-matrix", "true", "skipped")]) == 1
    assert _run_aggregator([("backend-matrix", "true", "")]) == 1
    # An intentionally unselected matrix remains an intentional skip; any
    # other result for an unselected matrix fails.
    assert _run_aggregator([("backend-matrix", "false", "skipped")]) == 0
    assert _run_aggregator([("backend-matrix", "false", "success")]) == 1
    assert _run_aggregator([("backend-matrix", "false", "failure")]) == 1


@pytest.mark.parametrize(
    "changed_path",
    [
        "tests/.reliability-test-durations.json",
        "tools/ci/reliability_shard_weights.json",
        "tools/ci/refresh_reliability_durations.py",
        "tools/ci/write_backend_matrix_summary.py",
    ],
)
def test_reliability_sharding_inputs_select_the_reliability_corpus(changed_path):
    """MoonLadderStudios/MoonMind#4366 R8: dependency-adjacent timing-hint
    and sharding-evidence changes select the reliability corpus (whose
    shards consume them), without escalating to full verification or
    pulling in unrelated suites."""
    assert (REPO_ROOT / changed_path).exists(), changed_path
    outputs = _outputs([changed_path])
    assert outputs["reliability_journey"] == "true", changed_path
    assert outputs["integration_ci"] == "false", changed_path
    assert outputs["full_backend"] == "false", changed_path


def test_selector_documents_qualified_infra_ownership():
    """MoonLadderStudios/MoonMind#3950 R6: the selector header must name the
    qualified #3885/#3832 owners of the PostgreSQL/Temporal/Docker boundaries
    and disclaim hermetic unit suites as evidence of those boundaries, so a
    future edit cannot silently drop the linkage. The comment itself is the
    linkage evidence; this test is only its regression guard."""
    source = (REPO_ROOT / "tools/select_test_suites.py").read_text()
    # Qualified-infra owners are named explicitly.
    assert "MoonLadderStudios/MoonMind#3885" in source
    assert "MoonLadderStudios/MoonMind#3832" in source
    assert "docs/Omnigent/ConcurrencyQualification.md" in source
    assert ".github/workflows/omnigent-concurrency-qualification.yml" in source
    assert "docs/Omnigent/SharedHostImage.md" in source
    assert "moonmind/omnigent/harness_platform/shared_host_conformance.py" in source
    # Hermetic lanes are disclaimed as infra evidence.
    assert "are NOT evidence of the" in source
    assert "PostgreSQL/Temporal/Docker" in source
    # Referenced owners exist on disk.
    for owned in (
        "docs/Omnigent/ConcurrencyQualification.md",
        ".github/workflows/omnigent-concurrency-qualification.yml",
        "tests/integration/omnigent/test_exact_docker_n_way_concurrency.py",
        "tests/provider/omnigent/test_omnigent_concurrency.py",
        "docs/Omnigent/SharedHostImage.md",
        "moonmind/omnigent/harness_platform/shared_host_conformance.py",
    ):
        assert (REPO_ROOT / owned).exists(), owned


def test_shared_resource_changes_require_real_docker_journey():
    from tools.select_test_suites import select_suites

    for path in (
        "moonmind/schemas/container_job_models.py",
        "moonmind/container_job_cli.py",
        "services/omnigent/scripts/moonmind-container-cli.py",
        "tools/test_worker_count.py",
        "tests/integration/reliability/test_container_job_authority_journey.py",
    ):
        assert select_suites([path]).reliability_journey, path
