"""A network configuration analysis runs as a source-free tool step (#5441).

It needs no agent and no managed runtime: the exports are captured artifacts,
so the run submits the typed job without a workspace.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

pytest.importorskip("temporalio")

from moonmind.schemas.repository_scanner_models import NetworkConfigAssessment
from moonmind.security.network_configs import build_network_config_job


@pytest.mark.asyncio
async def test_network_config_tool_step_submits_without_workspace_or_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.temporal.workflows import run as run_module
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    submitted: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str, payload: Any, **_kwargs: Any
    ) -> Any:
        if activity_type == "container_job.submit":
            submitted.append(payload)
            return {"jobId": "container-job:0123456789abcdef0123456789abcdef"}
        if activity_type == "container_job.cancel":
            return {"jobId": "container-job:0123456789abcdef0123456789abcdef"}
        raise AssertionError(activity_type)

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._cancel_requested = True
    workflow._target_runtime = ""
    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        type(
            "WorkflowInfo",
            (),
            {
                "task_queue": "mm.workflow.user.v2",
                "namespace": "default",
                "workflow_id": "wf-network",
                "run_id": "run-1",
                "search_attributes": {},
            },
        ),
    )
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    spec = build_network_config_job(
        NetworkConfigAssessment.model_validate(
            {
                "exports": [
                    {
                        "artifactRef": "art_export",
                        "fileName": "edge.cfg",
                        "device": "edge-rtr-1",
                        "exportedAt": "2026-11-27T08:00:00Z",
                        "supplier": "Network team",
                    }
                ]
            }
        )
    )
    await workflow._execute_container_job_tool(
        node_inputs={"idempotencyKey": "wf-network:job:1", "spec": spec},
        node_id="plan-job",
        execution_ordinal=1,
    )
    [payload] = submitted
    request = payload["request"]
    assert "workspaceRef" not in request["spec"]
    assert request["spec"]["networkMode"] == "none"
    assert request["source"]["workflowId"] == "wf-network"
