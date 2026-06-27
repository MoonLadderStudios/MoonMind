from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal.workflows import (
    managed_runtime_workspace_cleanup as cleanup_module,
)
from moonmind.workflows.temporal.workflows.managed_runtime_workspace_cleanup import (
    MoonMindManagedRuntimeWorkspaceCleanupWorkflow,
)


@pytest.mark.asyncio
async def test_managed_runtime_workspace_cleanup_invokes_cleanup_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    details: list[str] = []
    search_attributes: list[dict[str, list[object]]] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_name == "agent_runtime.cleanup_managed_runtime_files"
        assert payload == {}
        assert kwargs["task_queue"]
        return {
            "disabled": False,
            "dry_run": True,
            "scanned_run_records": 2,
            "scanned_session_records": 1,
            "eligible_roots": 1,
            "deleted_roots": 0,
            "errors": (),
        }

    monkeypatch.setattr(
        cleanup_module.workflow,
        "set_current_details",
        lambda value: details.append(value),
    )
    monkeypatch.setattr(
        cleanup_module.workflow,
        "upsert_search_attributes",
        lambda value: search_attributes.append(value),
    )
    monkeypatch.setattr(
        cleanup_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    result = await MoonMindManagedRuntimeWorkspaceCleanupWorkflow().run()

    assert result["dry_run"] is True
    assert result["eligible_roots"] == 1
    assert details == [
        "Cleaning retained managed runtime files",
        "Managed runtime file cleanup complete",
    ]
    assert search_attributes == [
        {
            "SessionStatus": ["cleanup"],
            "IsDegraded": [False],
        },
        {
            "SessionStatus": ["completed"],
            "IsDegraded": [False],
        },
    ]


@pytest.mark.asyncio
async def test_managed_runtime_workspace_cleanup_marks_errors_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_attributes: list[dict[str, list[object]]] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del activity_name, payload, kwargs
        return {
            "disabled": False,
            "dry_run": True,
            "errors": ("store_read_failed:RuntimeError",),
        }

    monkeypatch.setattr(
        cleanup_module.workflow, "set_current_details", lambda value: None
    )
    monkeypatch.setattr(
        cleanup_module.workflow,
        "upsert_search_attributes",
        lambda value: search_attributes.append(value),
    )
    monkeypatch.setattr(
        cleanup_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    result = await MoonMindManagedRuntimeWorkspaceCleanupWorkflow().run()

    assert result["errors"] == ("store_read_failed:RuntimeError",)
    assert search_attributes[-1] == {
        "SessionStatus": ["completed"],
        "IsDegraded": [True],
    }
