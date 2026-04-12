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
        "Reconciling managed Codex sessions",
        "Managed Codex session reconcile complete",
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

