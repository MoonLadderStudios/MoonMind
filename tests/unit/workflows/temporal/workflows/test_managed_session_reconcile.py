from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal.workflows import (
    managed_session_reconcile as reconcile_module,
)
from moonmind.workflows.temporal.workflows.managed_session_reconcile import (
    MoonMindManagedSessionReconcileWorkflow,
)

@pytest.mark.asyncio
async def test_managed_session_reconcile_updates_terminal_visibility(
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
        assert activity_name == "agent_runtime.reconcile_managed_sessions"
        return {
            "managedSessionRecordsReconciled": 2,
            "degradedSessionRecords": 1,
            "sessionIds": ["sess-1", "sess-2"],
            "truncated": False,
        }

    monkeypatch.setattr(
        reconcile_module.workflow,
        "set_current_details",
        lambda value: details.append(value),
    )
    monkeypatch.setattr(
        reconcile_module.workflow,
        "upsert_search_attributes",
        lambda value: search_attributes.append(value),
    )
    monkeypatch.setattr(
        reconcile_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    result = await MoonMindManagedSessionReconcileWorkflow().run()

    assert result["managedSessionRecordsReconciled"] == 2
    assert details == [
        "Reconciling managed runtime sessions",
        "Managed runtime session reconcile complete",
    ]
    assert search_attributes == [
        {
            "SessionStatus": ["reconciling"],
            "IsDegraded": [False],
        },
        {
            "SessionStatus": ["completed"],
            "IsDegraded": [True],
        },
    ]


@pytest.mark.asyncio
async def test_managed_session_reconcile_passes_orphan_reap_summary_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_attributes: list[dict[str, list[object]]] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del payload, kwargs
        assert activity_name == "agent_runtime.reconcile_managed_sessions"
        return {
            "managedSessionRecordsReconciled": 3,
            "degradedSessionRecords": 0,
            "sessionIds": ["sess-1", "sess-2", "sess-3"],
            "truncated": False,
            "orphanContainersReaped": 4,
            "orphanSessionIdsReaped": ["sess-orphan-a", "sess-orphan-b"],
            "orphanReapSkippedRecent": 1,
            "orphanVolumesScanned": 6,
            "orphanVolumesReaped": 2,
            "orphanVolumeReapSkippedActive": 3,
            "orphanVolumeReapSkippedRecent": 1,
        }

    monkeypatch.setattr(
        reconcile_module.workflow, "set_current_details", lambda value: None
    )
    monkeypatch.setattr(
        reconcile_module.workflow,
        "upsert_search_attributes",
        lambda value: search_attributes.append(value),
    )
    monkeypatch.setattr(
        reconcile_module.workflow, "execute_activity", _execute_activity
    )

    result = await MoonMindManagedSessionReconcileWorkflow().run()

    # Orphan reap summary is surfaced through the workflow result.
    assert result["orphanContainersReaped"] == 4
    assert result["orphanSessionIdsReaped"] == ["sess-orphan-a", "sess-orphan-b"]
    assert result["orphanReapSkippedRecent"] == 1
    assert result["orphanVolumesScanned"] == 6
    assert result["orphanVolumesReaped"] == 2
    assert result["orphanVolumeReapSkippedActive"] == 3
    assert result["orphanVolumeReapSkippedRecent"] == 1
    # Reaping orphans does not by itself mark the run degraded.
    assert search_attributes[-1] == {
        "SessionStatus": ["completed"],
        "IsDegraded": [False],
    }


@pytest.mark.asyncio
async def test_managed_session_reconcile_accepts_pre_reap_activity_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-flight compatibility: an activity result without orphan keys (from a
    worker predating the orphan-reap change) must still complete cleanly."""

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del payload, kwargs
        return {
            "managedSessionRecordsReconciled": 1,
            "degradedSessionRecords": 0,
            "sessionIds": ["sess-1"],
            "truncated": False,
        }

    monkeypatch.setattr(
        reconcile_module.workflow, "set_current_details", lambda value: None
    )
    monkeypatch.setattr(
        reconcile_module.workflow, "upsert_search_attributes", lambda value: None
    )
    monkeypatch.setattr(
        reconcile_module.workflow, "execute_activity", _execute_activity
    )

    result = await MoonMindManagedSessionReconcileWorkflow().run()

    assert result["managedSessionRecordsReconciled"] == 1
    assert "orphanContainersReaped" not in result
