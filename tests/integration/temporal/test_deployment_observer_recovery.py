"""Exercise observer liveness and receipt recovery through a real Temporal server."""

import asyncio
import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.deployment_tools import (
    build_deployment_update_tool_definition_payload,
)
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.tool_plan_contracts import (
    ToolResult,
    parse_tool_definition,
)
from moonmind.workflows.skills.tool_registry import create_registry_snapshot
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import TemporalSkillActivities
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@workflow.defn
class DeploymentObserverJourney:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        route = build_default_activity_catalog().resolve_skill(
            parse_tool_definition(payload)
        )
        kwargs = MoonMindRunWorkflow()._execute_kwargs_for_route(route)
        kwargs["task_queue"] = workflow.info().task_queue
        # Keep the test quick; the production policy is checked separately.
        if "heartbeat_timeout" in kwargs:
            kwargs["heartbeat_timeout"] = timedelta(seconds=1)
        kwargs["retry_policy"] = RetryPolicy(
            initial_interval=timedelta(milliseconds=100),
            maximum_attempts=2,
        )
        return await workflow.execute_activity("observe-deployment", {}, **kwargs)


@pytest.mark.parametrize("legacy_snapshot", [False, True])
async def test_observer_recovers_receipt_after_lost_heartbeats(
    tmp_path,
    monkeypatch,
    legacy_snapshot,
):
    payload = build_deployment_update_tool_definition_payload()
    if legacy_snapshot:
        payload["policies"]["timeouts"].pop("heartbeat_timeout_seconds", None)
    snapshot = create_registry_snapshot(
        skills=(parse_tool_definition(payload),),
        artifact_store=InMemoryArtifactStore(),
    )
    receipt = tmp_path / "result.json"
    attempts = []
    mutations = []
    dispatcher = SkillActivityDispatcher()
    heartbeat = activity.heartbeat

    def lose_observer_connection(*details):
        # A replaced observer cannot deliver heartbeats; the independent
        # updater's durable receipt remains available to the next observer.
        if activity.info().attempt == 1 and receipt.exists():
            return
        heartbeat(*details)

    monkeypatch.setattr(activity, "heartbeat", lose_observer_connection)

    async def observe(inputs, context):
        attempts.append(activity.info().attempt)
        if not receipt.exists():
            mutations.append(context["idempotency_key"])
            # Healthy slow pre-launch work outlasts the heartbeat timeout.
            await asyncio.sleep(2)
            receipt.write_text(
                json.dumps({"status": "COMPLETED", "outputs": {"verified": True}})
            )
            if not legacy_snapshot:
                await asyncio.Event().wait()
        return ToolResult(**json.loads(receipt.read_text()))

    dispatcher.register_skill(skill_name=payload["name"], handler=observe)
    runtime = TemporalSkillActivities(dispatcher=dispatcher)

    @activity.defn(name="observe-deployment")
    async def execute(_request: dict) -> dict:
        result = await runtime.mm_tool_execute(
            invocation_payload={
                "id": "update",
                "tool": {"type": "skill", "name": payload["name"]},
                "inputs": {
                    "stack": "moonmind",
                    "image": {
                        "repository": "ghcr.io/moonladderstudios/moonmind",
                        "reference": "latest",
                    },
                },
            },
            registry_snapshot=snapshot,
            idempotency_key="same-release-operation",
        )
        return result.to_payload()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="deployment-observer-test",
            workflows=[DeploymentObserverJourney],
            activities=[execute],
            workflow_runner=UnsandboxedWorkflowRunner(),
            max_heartbeat_throttle_interval=timedelta(milliseconds=100),
        ):
            handle = await env.client.start_workflow(
                DeploymentObserverJourney.run,
                payload,
                id="observer-recovery",
                task_queue="deployment-observer-test",
            )
            result = await asyncio.wait_for(handle.result(), timeout=15)
            history = await handle.fetch_history()
        assert result["status"] == "COMPLETED"
        assert result["outputs"]["verified"]
        assert attempts == ([1] if legacy_snapshot else [1, 2])
        assert mutations == ["same-release-operation"]
        await Replayer(
            workflows=[DeploymentObserverJourney],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
