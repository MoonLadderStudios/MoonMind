from __future__ import annotations

from datetime import timedelta

import pytest
from temporalio.common import RetryPolicy

from moonmind.schemas.managed_session_models import (
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


def _workflow_input(**overrides: object) -> CodexManagedSessionWorkflowInput:
    payload: dict[str, object] = {
        "taskRunId": "wf-run-1",
        "runtimeId": "codex_cli",
    }
    payload.update(overrides)
    return CodexManagedSessionWorkflowInput.model_validate(payload)


def test_agent_session_initializes_task_scoped_codex_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(
        _workflow_input(executionProfileRef="codex-default")
    )

    status = workflow.get_status()

    assert status["status"] == AGENT_SESSION_STATUS_ACTIVE
    assert status["binding"]["workflowId"] == "wf-run-1:session:codex_cli"
    assert status["binding"]["taskRunId"] == "wf-run-1"
    assert status["binding"]["runtimeId"] == "codex_cli"
    assert status["binding"]["sessionEpoch"] == 1
    assert status["binding"]["executionProfileRef"] == "codex-default"
    assert status["binding"]["sessionId"] == "sess:wf-run-1:codex_cli"


def test_agent_session_send_follow_up_validator_requires_runtime_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())

    with pytest.raises(ValueError, match="runtime handles are not attached yet"):
        workflow.validate_send_follow_up({"message": "Continue the task-scoped session."})


def test_agent_session_interrupt_turn_validator_rejects_stale_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
            "activeTurnId": "turn-1",
        }
    )

    with pytest.raises(ValueError, match="stale sessionEpoch"):
        workflow.validate_interrupt_turn({"sessionEpoch": 2, "reason": "Stop this turn."})


def test_agent_session_interrupt_turn_validator_requires_active_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
        }
    )

    with pytest.raises(ValueError, match="active turn"):
        workflow.validate_interrupt_turn({"sessionEpoch": 1, "reason": "Stop this turn."})


def test_agent_session_clear_session_validator_rejects_when_already_clearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
        }
    )
    workflow._status = AGENT_SESSION_STATUS_CLEARING

    with pytest.raises(ValueError, match="already clearing"):
        workflow.validate_clear_session({})


@pytest.mark.asyncio
async def test_agent_session_terminate_update_marks_termination_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())

    status = await workflow.terminate_session_update({"reason": "done"})

    assert status["status"] == AGENT_SESSION_STATUS_TERMINATING
    assert status["terminationRequested"] is True
    assert status["lastControlAction"] == "terminate_session"
    assert status["lastControlReason"] == "done"


@pytest.mark.asyncio
async def test_agent_session_send_follow_up_update_executes_session_activity_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
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
    assert captured[0][1]["reason"] == "Operator follow-up"
    assert workflow.get_status()["lastControlAction"] == "send_turn"

    for name, _payload, kwargs in captured:
        assert kwargs["task_queue"] == "mm.activity.agent_runtime"
        assert isinstance(kwargs["retry_policy"], RetryPolicy)
        route = agent_session_module.DEFAULT_ACTIVITY_CATALOG.resolve_activity(name)
        assert kwargs["start_to_close_timeout"] == timedelta(
            seconds=route.timeouts.start_to_close_seconds
        )


@pytest.mark.asyncio
async def test_agent_session_refresh_projection_uses_authoritative_binding_task_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(
        _workflow_input(sessionId="custom-session-id")
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
async def test_agent_session_interrupt_turn_update_executes_session_activity_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
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
        if activity_name == "agent_runtime.interrupt_turn":
            assert payload["sessionEpoch"] == 1
            assert payload["turnId"] == "turn-1"
            assert payload["reason"] == "Stop this turn."
            return {
                "sessionState": {
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 1,
                    "containerId": "container-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "turn-1",
                "status": "interrupted",
                "outputRefs": [],
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
                "latestSummaryRef": "art-summary-after-interrupt",
                "latestCheckpointRef": "art-checkpoint-after-interrupt",
                "latestControlEventRef": "art-control-after-interrupt",
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
                    "art-summary-after-interrupt",
                    "art-checkpoint-after-interrupt",
                    "art-control-after-interrupt",
                ],
                "latestSummaryRef": "art-summary-after-interrupt",
                "latestCheckpointRef": "art-checkpoint-after-interrupt",
                "latestControlEventRef": "art-control-after-interrupt",
                "latestResetBoundaryRef": None,
                "metadata": {},
            }
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    result = await workflow.interrupt_turn_update(
        {"sessionEpoch": 1, "reason": "Stop this turn."}
    )

    assert result["turnId"] == "turn-1"
    assert result["latestControlEventRef"] == "art-control-after-interrupt"
    assert captured == [
        "agent_runtime.interrupt_turn",
        "agent_runtime.fetch_session_summary",
        "agent_runtime.publish_session_artifacts",
    ]
    status = workflow.get_status()
    assert status["activeTurnId"] is None
    assert status["lastControlAction"] == "interrupt_turn"
    assert status["lastControlReason"] == "Stop this turn."


@pytest.mark.asyncio
async def test_agent_session_clear_session_update_executes_remote_clear_and_updates_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
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
