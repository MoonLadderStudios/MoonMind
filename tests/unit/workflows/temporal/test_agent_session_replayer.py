from __future__ import annotations

import asyncio
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.schemas.managed_session_models import CodexManagedSessionWorkflowInput
from moonmind.workflows.temporal.workflows import agent_session as agent_session_module
from moonmind.workflows.temporal.workflows.agent_session import (
    MoonMindAgentSessionWorkflow,
)


@activity.defn(name="agent_runtime.terminate_session")
async def mock_replay_terminate_session(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionState": {
            "sessionId": payload["sessionId"],
            "sessionEpoch": payload["sessionEpoch"],
            "containerId": payload["containerId"],
            "threadId": payload["threadId"],
            "activeTurnId": None,
        },
        "status": "terminated",
        "imageRef": "moonmind-codex:test",
        "controlUrl": f"docker-exec://{payload['containerId']}",
        "metadata": {"containerRemoved": True, "supervisionFinalized": True},
    }


@activity.defn(name="agent_runtime.fetch_session_summary")
async def mock_replay_fetch_session_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionState": {
            "sessionId": payload["sessionId"],
            "sessionEpoch": payload["sessionEpoch"],
            "containerId": payload["containerId"],
            "threadId": payload["threadId"],
            "activeTurnId": None,
        },
        "latestSummaryRef": f"artifact://session/{payload['sessionEpoch']}/summary",
        "latestCheckpointRef": f"artifact://session/{payload['sessionEpoch']}/checkpoint",
        "latestControlEventRef": None,
        "latestResetBoundaryRef": None,
        "metadata": {},
    }


@activity.defn(name="agent_runtime.publish_session_artifacts")
async def mock_replay_publish_session_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionState": {
            "sessionId": payload["sessionId"],
            "sessionEpoch": payload["sessionEpoch"],
            "containerId": payload["containerId"],
            "threadId": payload["threadId"],
            "activeTurnId": None,
        },
        "publishedArtifactRefs": [
            f"artifact://session/{payload['sessionEpoch']}/terminate"
        ],
        "latestSummaryRef": f"artifact://session/{payload['sessionEpoch']}/summary",
        "latestCheckpointRef": f"artifact://session/{payload['sessionEpoch']}/checkpoint",
        "latestControlEventRef": f"artifact://session/{payload['sessionEpoch']}/terminate/control",
        "latestResetBoundaryRef": None,
        "metadata": {},
    }


async def _wait_for_status(
    handle: Any,
    predicate: Any,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        status = await handle.query("get_status")
        if predicate(status):
            return status
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Timed out waiting for status; last={status!r}")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_agent_session_terminate_history_replays_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_session_module.workflow,
        "set_current_details",
        lambda _details: None,
    )
    monkeypatch.setattr(
        agent_session_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    session_input = CodexManagedSessionWorkflowInput.model_validate(
        {
            "taskRunId": "task-run-session-replay",
            "runtimeId": "codex_cli",
            "sessionId": "sess:unit:replay",
            "sessionEpoch": 1,
        }
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        await env.client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=env.client.namespace,
                search_attributes={
                    "TaskRunId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "RuntimeId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "SessionId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "SessionEpoch": IndexedValueType.INDEXED_VALUE_TYPE_INT,
                    "SessionStatus": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "IsDegraded": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
                },
            )
        )
        async with Worker(
            env.client,
            task_queue="agent-session-replay-workflow",
            workflows=[MoonMindAgentSessionWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            async with Worker(
                env.client,
                task_queue=settings.temporal.activity_agent_runtime_task_queue,
                activities=[
                    mock_replay_terminate_session,
                    mock_replay_fetch_session_summary,
                    mock_replay_publish_session_artifacts,
                ],
            ):
                handle = await env.client.start_workflow(
                    MoonMindAgentSessionWorkflow.run,
                    session_input,
                    id="test-agent-session-replay",
                    task_queue="agent-session-replay-workflow",
                )
                await handle.signal(
                    "attach_runtime_handles",
                    {"containerId": "ctr-replay", "threadId": "thread-replay"},
                )
                await _wait_for_status(
                    handle,
                    lambda status: status.get("containerId") == "ctr-replay"
                    and status.get("threadId") == "thread-replay",
                )
                await handle.execute_update(
                    "TerminateSession",
                    {
                        "reason": "operator finished",
                        "requestId": "request-terminate-replay",
                    },
                )
                result = await handle.result()
                assert result["status"] == "terminated"

                history = await handle.fetch_history()

    replayer = Replayer(
        workflows=[MoonMindAgentSessionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(history)
