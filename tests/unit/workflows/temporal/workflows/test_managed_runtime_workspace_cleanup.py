from __future__ import annotations

from typing import Any

import pytest
from temporalio.exceptions import ActivityError

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
    activity_names: list[str] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert kwargs["task_queue"]
        activity_names.append(activity_name)
        if activity_name == "agent_runtime.reclaim_docker_storage":
            assert payload == {}
            return {"pressureDetected": False, "errors": []}
        if activity_name == "agent_runtime.cleanup_managed_runtime_files":
            assert payload == {}
            return {
                "disabled": False,
                "dry_run": True,
                "scanned_run_records": 2,
                "scanned_session_records": 1,
                "eligible_roots": 1,
                "deleted_roots": 0,
                "errors": (),
            }
        assert activity_name == "artifact.lifecycle_sweep"
        assert payload == {"principal": "service:storage-maintenance"}
        return {
            "expired_candidate_count": 2,
            "soft_deleted_count": 1,
            "hard_deleted_count": 1,
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
    assert result["dockerStorage"]["pressureDetected"] is False
    assert result["artifactLifecycle"]["hard_deleted_count"] == 1
    assert result["maintenanceErrors"] == []
    assert activity_names == [
        "agent_runtime.cleanup_managed_runtime_files",
        "agent_runtime.reclaim_docker_storage",
        "artifact.lifecycle_sweep",
    ]
    assert details == [
        "Maintaining deployment storage",
        "Deployment storage maintenance complete",
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
async def test_managed_runtime_cleanup_forwards_remediation_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supplied = {
        "actionKind": "cleanup.request_janitor",
        "cleanupRef": "artifact://cleanup",
        "targetWorkflowId": "target",
        "expectedState": "pending",
        "requestId": "request-1",
    }

    async def _execute_activity(
        activity_name: str, payload: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        assert activity_name == "agent_runtime.cleanup_managed_runtime_files"
        assert payload == supplied
        return {"errors": ()}

    monkeypatch.setattr(cleanup_module.workflow, "set_current_details", lambda _: None)
    monkeypatch.setattr(
        cleanup_module.workflow, "upsert_search_attributes", lambda _: None
    )
    monkeypatch.setattr(cleanup_module.workflow, "execute_activity", _execute_activity)

    await MoonMindManagedRuntimeWorkspaceCleanupWorkflow().run(supplied)


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
        del payload, kwargs
        if activity_name == "agent_runtime.reclaim_docker_storage":
            return {"errors": []}
        if activity_name == "artifact.lifecycle_sweep":
            return {}
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


@pytest.mark.asyncio
async def test_scheduled_storage_maintenance_keeps_workspace_success_when_artifact_sweep_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_attributes: list[dict[str, list[object]]] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del payload, kwargs
        if activity_name == "agent_runtime.reclaim_docker_storage":
            return {"errors": []}
        if activity_name == "artifact.lifecycle_sweep":
            raise ActivityError(
                "artifact worker unavailable",
                scheduled_event_id=1,
                started_event_id=2,
                identity="artifact-worker",
                activity_type=activity_name,
                activity_id="artifact-sweep",
                retry_state=None,
            )
        return {"deletedRoots": 2, "errors": []}

    monkeypatch.setattr(cleanup_module.workflow, "set_current_details", lambda _: None)
    monkeypatch.setattr(
        cleanup_module.workflow,
        "upsert_search_attributes",
        lambda value: search_attributes.append(value),
    )
    monkeypatch.setattr(cleanup_module.workflow, "execute_activity", _execute_activity)

    result = await MoonMindManagedRuntimeWorkspaceCleanupWorkflow().run()

    assert result["deletedRoots"] == 2
    assert result["maintenanceErrors"] == ["Artifact lifecycle sweep failed"]
    assert search_attributes[-1]["IsDegraded"] == [True]
