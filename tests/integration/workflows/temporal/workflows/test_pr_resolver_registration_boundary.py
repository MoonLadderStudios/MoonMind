from __future__ import annotations

import asyncio
from typing import Any
import uuid

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.pr_resolver import (
    MoonMindPRResolverWorkflow,
)


@workflow.defn(name="Test.PRResolverParent")
class _ParentWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_child_workflow(
            "MoonMind.PRResolver",
            payload,
            id=f"{workflow.info().workflow_id}:resolver",
            task_queue=workflow.info().task_queue,
        )


@pytest.mark.asyncio
@pytest.mark.temporal_boundary
async def test_real_worker_accepts_parent_to_pr_resolver_child_boundary() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"pr-resolver-canary-{uuid.uuid4()}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_ParentWorkflow, MoonMindPRResolverWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            workflow_id = f"canary-{uuid.uuid4()}"
            handle = await env.client.start_workflow(
                "Test.PRResolverParent",
                {
                    "workflowType": "MoonMind.PRResolver",
                    "parentWorkflowId": workflow_id,
                    "principal": "canary",
                    "repository": "canary/no-mutation",
                    "prNumber": 1,
                    "prUrl": "https://example.invalid/canary/pull/1",
                    "stepId": "canary",
                    "correlationId": workflow_id,
                    "baseAgentRequest": {"canary": True},
                    "canaryMode": True,
                },
                id=workflow_id,
                task_queue=queue,
            )
            result = await asyncio.wait_for(handle.result(), timeout=15)
    assert result["metadata"]["canary"] is True
    assert result["metadata"]["workflowType"] == "MoonMind.PRResolver"
