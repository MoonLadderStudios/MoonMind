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
async def test_managed_runtime_workspace_cleanup_updates_operator_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    details: list[str] = []
    search_attributes: list[dict[str, list[object]]] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del payload, kwargs
        assert activity_name == "agent_runtime.cleanup_managed_runtime_files"
        return {
            "disabled": False,
            "dry_run": True,
            "scannedRunRecords": 2,
            "scannedSessionRecords": 1,
            "scannedWorkspaceRoots": 1,
            "eligibleRoots": 1,
            "estimatedDeletedBytes": 42,
            "candidateSamples": [
                {
                    "resource_class": "workspace_root",
                    "safe_path": "store:workspaces/mm-workflow",
                    "classification": "eligible",
                    "reason": "all retained-state safety gates passed",
                    "estimated_bytes": 42,
                }
            ],
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

    assert result["eligibleRoots"] == 1
    assert result["candidateSamples"][0]["safe_path"] == "store:workspaces/mm-workflow"
    assert details == [
        "Cleaning retained managed runtime state",
        "Managed runtime retained-state cleanup complete",
    ]
    assert search_attributes == [
        {
            "SessionStatus": ["cleaning_retained_state"],
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
        return {"disabled": False, "dry_run": True, "errors": ["store:x: ValueError"]}

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

    await MoonMindManagedRuntimeWorkspaceCleanupWorkflow().run()

    assert search_attributes[-1] == {
        "SessionStatus": ["completed"],
        "IsDegraded": [True],
    }
