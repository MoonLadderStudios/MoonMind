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
        "unit_fast": "false",
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
        assert outputs[key] == "false", key


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
