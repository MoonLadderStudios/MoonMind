"""TDD: recurring schedules must apply step-runtime admission (MoonLadderStudios/MoonMind#3931).

A recurring request with an allowed top-level runtime but a retired
``workflow.steps[i].runtime.mode`` must be rejected before the recurring
target is stored, mirroring immediate Workflow Create per-step admission.
"""

from __future__ import annotations

import pytest

from api_service.api.routers.executions import _resolve_recurring_runtime_metadata


@pytest.mark.asyncio
async def test_recurring_step_direct_runtime_rejected_after_cutoff(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2026-01-01T00:00:00Z")
    request_payload = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            "targetRuntime": "omnigent",
            "workflow": {
                "runtime": {"mode": "omnigent"},
                "steps": [
                    {
                        "id": "legacy-step",
                        "runtime": {"mode": "codex_cli"},
                    }
                ],
            },
        },
    }
    with pytest.raises(Exception) as exc_info:
        await _resolve_recurring_runtime_metadata(request_payload, session=None)
    assert "codex_cli" in str(exc_info.value).lower() or "step" in str(
        exc_info.value
    ).lower()
