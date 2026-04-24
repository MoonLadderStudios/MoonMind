from __future__ import annotations

import asyncio
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.schemas.managed_session_models import CodexManagedSessionWorkflowInput
from moonmind.workflows.temporal.workflows.agent_session import (
    MoonMindAgentSessionWorkflow,
)

# NOTE: This test uses the Temporal time-skipping test server and is not
# suitable for the required integration_ci suite.
pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

RUNTIME_EVENTS: list[tuple[str, dict[str, Any]]] = []

async def _register_session_search_attributes(env: WorkflowEnvironment) -> None:
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

def _session_state(payload: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    state = {
        "sessionId": payload["sessionId"],
        "sessionEpoch": payload["sessionEpoch"],
        "containerId": payload["containerId"],
        "threadId": payload["threadId"],
        "activeTurnId": payload.get("activeTurnId"),
    }
    state.update(overrides)
    return state

@activity.defn(name="agent_runtime.send_turn")
async def mock_send_turn(payload: dict[str, Any]) -> dict[str, Any]:
    RUNTIME_EVENTS.append(("send_turn", dict(payload)))
    return {
        "sessionState": _session_state(payload, activeTurnId=None),
        "turnId": "turn-send-1",
        "status": "completed",
        "outputRefs": ["artifact://turn/send-1"],
        "metadata": {},
    }

@activity.defn(name="agent_runtime.interrupt_turn")
async def mock_interrupt_turn(payload: dict[str, Any]) -> dict[str, Any]:
    RUNTIME_EVENTS.append(("interrupt_turn", dict(payload)))
    return {
        "sessionState": _session_state(payload, activeTurnId=None),
        "turnId": payload["turnId"],
        "status": "interrupted",
        "outputRefs": ["artifact://turn/interrupted"],
        "metadata": {"event": "turn_interrupted"},
    }

@activity.defn(name="agent_runtime.clear_session")
async def mock_clear_session(payload: dict[str, Any]) -> dict[str, Any]:
    RUNTIME_EVENTS.append(("clear_session", dict(payload)))
    return {
        "sessionState": _session_state(
            payload,
            sessionEpoch=payload["sessionEpoch"] + 1,
            threadId=payload["newThreadId"],
            activeTurnId=None,
        ),
        "status": "ready",
        "imageRef": "moonmind-codex:test",
        "controlUrl": f"docker-exec://{payload['containerId']}",
        "metadata": {},
    }

@activity.defn(name="agent_runtime.terminate_session")
async def mock_terminate_session(payload: dict[str, Any]) -> dict[str, Any]:
    RUNTIME_EVENTS.append(("terminate_session", dict(payload)))
    return {
        "sessionState": _session_state(payload, activeTurnId=None),
        "status": "terminated",
        "imageRef": "moonmind-codex:test",
        "controlUrl": f"docker-exec://{payload['containerId']}",
        "metadata": {"containerRemoved": True, "supervisionFinalized": True},
    }

@activity.defn(name="agent_runtime.fetch_session_summary")
async def mock_fetch_session_summary(payload: dict[str, Any]) -> dict[str, Any]:
    RUNTIME_EVENTS.append(("fetch_session_summary", dict(payload)))
    return {
        "sessionState": _session_state(payload),
        "latestSummaryRef": f"artifact://session/{payload['sessionEpoch']}/summary",
        "latestCheckpointRef": f"artifact://session/{payload['sessionEpoch']}/checkpoint",
        "latestControlEventRef": None,
        "latestResetBoundaryRef": None,
        "metadata": {},
    }

@activity.defn(name="agent_runtime.publish_session_artifacts")
async def mock_publish_session_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    RUNTIME_EVENTS.append(("publish_session_artifacts", dict(payload)))
    action = str((payload.get("metadata") or {}).get("action") or "session")
    epoch = payload["sessionEpoch"]
    return {
        "sessionState": _session_state(payload),
        "publishedArtifactRefs": [f"artifact://session/{epoch}/{action}"],
        "latestSummaryRef": f"artifact://session/{epoch}/summary",
        "latestCheckpointRef": f"artifact://session/{epoch}/checkpoint",
        "latestControlEventRef": f"artifact://session/{epoch}/{action}/control",
        "latestResetBoundaryRef": (
            f"artifact://session/{epoch}/reset"
            if action == "clear_session"
            else None
        ),
        "metadata": {},
    }

async def test_agent_session_lifecycle_updates_preserve_clear_cancel_and_terminate_contracts() -> None:
    RUNTIME_EVENTS.clear()
    session_input = CodexManagedSessionWorkflowInput.model_validate(
        {
            "taskRunId": "task-run-session-lifecycle",
            "runtimeId": "codex_cli",
            "sessionId": "sess:integration:lifecycle",
            "sessionEpoch": 1,
        }
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_session_search_attributes(env)
        async with Worker(
            env.client,
            task_queue="agent-session-lifecycle-workflow",
            workflows=[MoonMindAgentSessionWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            async with Worker(
                env.client,
                task_queue=settings.temporal.activity_agent_runtime_task_queue,
                activities=[
                    mock_send_turn,
                    mock_interrupt_turn,
                    mock_clear_session,
                    mock_terminate_session,
                    mock_fetch_session_summary,
                    mock_publish_session_artifacts,
                ],
            ):
                handle = await env.client.start_workflow(
                    MoonMindAgentSessionWorkflow.run,
                    session_input,
                    id="test-agent-session-lifecycle-contract",
                    task_queue="agent-session-lifecycle-workflow",
                )

                await handle.signal(
                    "attach_runtime_handles",
                    {"containerId": "ctr-lifecycle", "threadId": "thread-1"},
                )
                started = await _wait_for_status(
                    handle,
                    lambda value: value["containerId"] == "ctr-lifecycle"
                    and value["threadId"] == "thread-1",
                )
                assert started["binding"]["sessionEpoch"] == 1

                send_result = await handle.execute_update(
                    "SendFollowUp",
                    {
                        "message": "continue without leaking this prompt to metadata",
                        "reason": "operator follow-up",
                        "requestId": "request-send-1",
                    },
                )
                assert send_result["turnId"] == "turn-send-1"
                sent = await handle.query("get_status")
                assert sent["latestSummaryRef"] == "artifact://session/1/summary"
                assert sent["terminationRequested"] is False

                clear_result = await handle.execute_update(
                    "ClearSession",
                    {"reason": "reset context", "requestId": "request-clear-1"},
                )
                clear_state = clear_result["sessionState"]
                assert clear_state["sessionId"] == "sess:integration:lifecycle"
                assert clear_state["containerId"] == "ctr-lifecycle"
                assert clear_state["sessionEpoch"] == 2
                assert clear_state["threadId"] != "thread-1"
                assert clear_state["activeTurnId"] is None
                cleared = await handle.query("get_status")
                assert cleared["binding"]["sessionEpoch"] == 2
                assert cleared["latestControlEventRef"] == (
                    "artifact://session/2/clear_session/control"
                )
                assert cleared["latestResetBoundaryRef"] == "artifact://session/2/reset"

                await handle.signal(
                    "attach_runtime_handles",
                    {"activeTurnId": "turn-active-2"},
                )
                interrupted = await handle.execute_update(
                    "InterruptTurn",
                    {
                        "sessionEpoch": 2,
                        "reason": "operator interrupt",
                        "requestId": "request-interrupt-1",
                    },
                )
                assert interrupted["status"] == "interrupted"
                after_interrupt = await handle.query("get_status")
                assert after_interrupt["activeTurnId"] is None
                assert after_interrupt["lastControlAction"] == "interrupt_turn"

                await handle.signal(
                    "attach_runtime_handles",
                    {"activeTurnId": "turn-active-3"},
                )
                canceled = await handle.execute_update(
                    "CancelSession",
                    {"reason": "operator cancel", "requestId": "request-cancel-1"},
                )
                assert canceled["status"] == "active"
                assert canceled["terminationRequested"] is False
                assert canceled["lastControlAction"] == "cancel_session"
                assert canceled["containerId"] == "ctr-lifecycle"

                terminating = await handle.execute_update(
                    "TerminateSession",
                    {
                        "reason": "operator finished",
                        "requestId": "request-terminate-1",
                    },
                )
                assert terminating["terminationRequested"] is True

                final_status = await handle.result()
                assert final_status["status"] == "terminated"
                assert final_status["terminationRequested"] is True

    event_names = [name for name, _payload in RUNTIME_EVENTS]
    assert event_names.count("send_turn") == 1
    assert event_names.count("clear_session") == 1
    assert event_names.count("interrupt_turn") == 2
    assert event_names.count("terminate_session") == 1
    assert RUNTIME_EVENTS[-1][0] == "terminate_session"
    assert RUNTIME_EVENTS[-1][1]["containerId"] == "ctr-lifecycle"
