from __future__ import annotations

from tools import select_test_suites
from tools.select_test_suites import select_suites


def _outputs(paths: list[str], **kwargs) -> dict[str, str]:
    return select_suites(paths, event_name="pull_request", **kwargs).as_outputs()


def test_docs_only_change_does_not_select_heavy_backend_suites() -> None:
    outputs = _outputs(["docs/Development/PreCommitWorkflow.md"])

    assert outputs == {
        "unit_fast": "false",
        "api_component": "false",
        "temporal_boundary": "false",
        "integration_ci": "false",
        "full_backend": "false",
    }


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


def test_temporal_schema_change_selects_temporal_boundary() -> None:
    outputs = _outputs(["moonmind/schemas/temporal_activity_models.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["temporal_boundary"] == "true"


def test_integration_test_change_selects_integration_ci() -> None:
    outputs = _outputs(["tests/integration/api/test_workflow_console_routes.py"])

    assert outputs["unit_fast"] == "true"
    assert outputs["integration_ci"] == "true"


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
