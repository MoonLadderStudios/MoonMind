from __future__ import annotations

from typing import Any

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionControlRequest,
    CodexManagedSessionWorkflowInput,
)
from moonmind.workflows.temporal.workflows import agent_session as agent_session_module
from moonmind.workflows.temporal.workflows.agent_session import (
    AGENT_SESSION_STATUS_ACTIVE,
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
