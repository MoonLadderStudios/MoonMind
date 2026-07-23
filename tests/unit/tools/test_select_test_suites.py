from __future__ import annotations

import pytest

from tools import select_test_suites
from tools.select_test_suites import select_suites


def _outputs(paths: list[str], **kwargs) -> dict[str, str]:
    return select_suites(paths, event_name="pull_request", **kwargs).as_outputs()


def test_docs_only_change_does_not_select_heavy_backend_suites() -> None:
    outputs = _outputs(["docs/Development/PreCommitWorkflow.md"])

    assert outputs == {
        "unit_fast": "false",
        "unit_slow": "false",
        "api_component": "false",
        "temporal_boundary": "false",
        "integration_ci": "false",
        "reliability_journey": "false",
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
