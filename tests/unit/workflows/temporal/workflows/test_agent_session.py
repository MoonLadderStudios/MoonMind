from __future__ import annotations

from datetime import timedelta

import pytest
from temporalio.common import RetryPolicy

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionControlRequest,
    CodexManagedSessionWorkflowInput,
)
from moonmind.workflows.temporal.workflows import agent_session as agent_session_module
from moonmind.workflows.temporal.workflows.agent_session import (
    AGENT_SESSION_STATUS_ACTIVE,
    AGENT_SESSION_STATUS_CLEARING,
    AGENT_SESSION_STATUS_TERMINATING,
    MoonMindAgentSessionWorkflow,
)


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-run-1:session:codex_cli",
            "run_id": "run-session-1",
            "task_queue": "mm.workflow",
        },
    )
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(agent_session_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_session_module.workflow, "logger", logger)


def test_agent_session_initializes_task_scoped_codex_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentSessionWorkflow()
    _configure_workflow_runtime(monkeypatch)

    workflow._initialize_session(
        CodexManagedSessionWorkflowInput(
            taskRunId="wf-run-1",
            runtimeId="codex_cli",
            executionProfileRef="codex-default",
        )
    )

    status = workflow.get_status()

    assert status["status"] == AGENT_SESSION_STATUS_ACTIVE
    assert status["binding"]["workflowId"] == "wf-run-1:session:codex_cli"
    assert status["binding"]["taskRunId"] == "wf-run-1"
    assert status["binding"]["runtimeId"] == "codex_cli"
    assert status["binding"]["sessionEpoch"] == 1
    assert status["binding"]["executionProfileRef"] == "codex-default"
    assert status["binding"]["sessionId"] == "sess:wf-run-1:codex_cli"


def test_agent_session_clear_control_action_bumps_epoch_and_rotates_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentSessionWorkflow()
    _configure_workflow_runtime(monkeypatch)
    workflow._initialize_session(
        CodexManagedSessionWorkflowInput(
            taskRunId="wf-run-1",
            runtimeId="codex_cli",
        )
    )
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
            "activeTurnId": "turn-1",
        }
    )

    workflow.apply_control_action(
        CodexManagedSessionControlRequest(
            action="clear_session",
            reason="Reset stale context",
            threadId="thread-2",
        ).model_dump(by_alias=True)
    )

    status = workflow.get_status()

    assert status["status"] == AGENT_SESSION_STATUS_ACTIVE
    assert status["binding"]["sessionEpoch"] == 2
    assert status["containerId"] == "container-1"
    assert status["threadId"] == "thread-2"
    assert status["activeTurnId"] is None
    assert status["lastControlAction"] == "clear_session"
    assert status["lastControlReason"] == "Reset stale context"


def test_agent_session_terminate_control_action_marks_termination_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentSessionWorkflow()
    _configure_workflow_runtime(monkeypatch)
    workflow._initialize_session(
        CodexManagedSessionWorkflowInput(
            taskRunId="wf-run-1",
            runtimeId="codex_cli",
        )
    )

    workflow.apply_control_action({"action": "terminate_session", "reason": "done"})

    status = workflow.get_status()

    assert status["status"] == AGENT_SESSION_STATUS_TERMINATING
    assert status["terminationRequested"] is True
    assert status["lastControlAction"] == "terminate_session"
    assert status["lastControlReason"] == "done"


@pytest.mark.asyncio
async def test_agent_session_send_follow_up_update_executes_session_activity_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentSessionWorkflow()
    _configure_workflow_runtime(monkeypatch)
    workflow._initialize_session(
        CodexManagedSessionWorkflowInput(
            taskRunId="wf-run-1",
            runtimeId="codex_cli",
        )
    )
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
        }
    )

    captured: list[tuple[str, dict[str, object], dict[str, object]]] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        captured.append((activity_name, payload, kwargs))
        if activity_name == "agent_runtime.send_turn":
            return {
                "sessionState": {
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 1,
                    "containerId": "container-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "turn-2",
                "status": "completed",
                "outputRefs": ["art-follow-up-output"],
                "metadata": {},
            }
        if activity_name == "agent_runtime.fetch_session_summary":
            return {
                "sessionState": {
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 1,
                    "containerId": "container-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "latestSummaryRef": "art-summary-2",
                "latestCheckpointRef": "art-checkpoint-2",
                "latestControlEventRef": None,
                "latestResetBoundaryRef": None,
                "metadata": {},
            }
        if activity_name == "agent_runtime.publish_session_artifacts":
            return {
                "sessionState": {
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 1,
                    "containerId": "container-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "publishedArtifactRefs": [
                    "art-follow-up-output",
                    "art-summary-2",
                    "art-checkpoint-2",
                ],
                "latestSummaryRef": "art-summary-2",
                "latestCheckpointRef": "art-checkpoint-2",
                "latestControlEventRef": None,
                "latestResetBoundaryRef": None,
                "metadata": {},
            }
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    result = await workflow.send_follow_up(
        {"message": "Continue the task-scoped session.", "reason": "Operator follow-up"}
    )

    assert result["turnId"] == "turn-2"
    assert result["latestSummaryRef"] == "art-summary-2"
    assert result["latestCheckpointRef"] == "art-checkpoint-2"
    assert [name for name, _, _ in captured] == [
        "agent_runtime.send_turn",
        "agent_runtime.fetch_session_summary",
        "agent_runtime.publish_session_artifacts",
    ]
    assert captured[0][1]["instructions"] == "Continue the task-scoped session."
    assert workflow.get_status()["lastControlAction"] == "send_turn"

    for _name, _payload, kwargs in captured:
        assert kwargs["task_queue"] == "mm.activity.agent_runtime"
        assert isinstance(kwargs["retry_policy"], RetryPolicy)
        assert kwargs["start_to_close_timeout"] == timedelta(seconds=60)


@pytest.mark.asyncio
async def test_agent_session_refresh_projection_uses_authoritative_binding_task_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentSessionWorkflow()
    _configure_workflow_runtime(monkeypatch)
    workflow._initialize_session(
        CodexManagedSessionWorkflowInput(
            taskRunId="wf-run-1",
            runtimeId="codex_cli",
            sessionId="custom-session-id",
        )
    )
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
        }
    )

    publication_payloads: list[dict[str, object]] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        if activity_name == "agent_runtime.send_turn":
            return {
                "sessionState": {
                    "sessionId": "custom-session-id",
                    "sessionEpoch": 1,
                    "containerId": "container-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "turn-2",
                "status": "completed",
            }
        if activity_name == "agent_runtime.fetch_session_summary":
            return {
                "sessionState": {
                    "sessionId": "custom-session-id",
                    "sessionEpoch": 1,
                    "containerId": "container-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
            }
        if activity_name == "agent_runtime.publish_session_artifacts":
            publication_payloads.append(payload)
            return {
                "sessionState": {
                    "sessionId": "custom-session-id",
                    "sessionEpoch": 1,
                    "containerId": "container-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
            }
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    await workflow.send_follow_up({"message": "Continue the custom session."})

    assert len(publication_payloads) == 1
    assert publication_payloads[0]["sessionId"] == "custom-session-id"
    assert publication_payloads[0]["sessionEpoch"] == 1
    assert publication_payloads[0]["containerId"] == "container-1"
    assert publication_payloads[0]["threadId"] == "thread-1"
    assert publication_payloads[0]["taskRunId"] == "wf-run-1"
    assert publication_payloads[0]["metadata"] == {
        "action": "send_follow_up",
        "reason": None,
    }


@pytest.mark.asyncio
async def test_agent_session_clear_session_update_executes_remote_clear_and_updates_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentSessionWorkflow()
    _configure_workflow_runtime(monkeypatch)
    workflow._initialize_session(
        CodexManagedSessionWorkflowInput(
            taskRunId="wf-run-1",
            runtimeId="codex_cli",
        )
    )
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
            "activeTurnId": "turn-1",
        }
    )

    captured: list[str] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        captured.append(activity_name)
        assert workflow.get_status()["status"] == AGENT_SESSION_STATUS_CLEARING
        if activity_name == "agent_runtime.clear_session":
            assert payload["newThreadId"] == "thread:sess:wf-run-1:codex_cli:2"
            return {
                "sessionState": {
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 2,
                    "containerId": "container-1",
                    "threadId": "thread-2",
                    "activeTurnId": None,
                },
                "status": "ready",
                "imageRef": "moonmind:latest",
                "controlUrl": "http://session-control",
                "metadata": {},
            }
        if activity_name == "agent_runtime.fetch_session_summary":
            return {
                "sessionState": {
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 2,
                    "containerId": "container-1",
                    "threadId": "thread-2",
                    "activeTurnId": None,
                },
                "latestSummaryRef": "art-summary-epoch-2",
                "latestCheckpointRef": "art-checkpoint-epoch-2",
                "latestControlEventRef": "art-control-epoch-2",
                "latestResetBoundaryRef": "art-reset-epoch-2",
                "metadata": {},
            }
        if activity_name == "agent_runtime.publish_session_artifacts":
            return {
                "sessionState": {
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 2,
                    "containerId": "container-1",
                    "threadId": "thread-2",
                    "activeTurnId": None,
                },
                "publishedArtifactRefs": [
                    "art-summary-epoch-2",
                    "art-checkpoint-epoch-2",
                    "art-control-epoch-2",
                    "art-reset-epoch-2",
                ],
                "latestSummaryRef": "art-summary-epoch-2",
                "latestCheckpointRef": "art-checkpoint-epoch-2",
                "latestControlEventRef": "art-control-epoch-2",
                "latestResetBoundaryRef": "art-reset-epoch-2",
                "metadata": {},
            }
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    result = await workflow.clear_session_update({"reason": "Reset stale context"})

    assert result["sessionState"]["sessionEpoch"] == 2
    assert result["latestResetBoundaryRef"] == "art-reset-epoch-2"
    assert captured == [
        "agent_runtime.clear_session",
        "agent_runtime.fetch_session_summary",
        "agent_runtime.publish_session_artifacts",
    ]
    status = workflow.get_status()
    assert status["binding"]["sessionEpoch"] == 2
    assert status["threadId"] == "thread-2"
    assert status["activeTurnId"] is None
    assert status["lastControlAction"] == "clear_session"
    assert status["lastControlReason"] == "Reset stale context"
