from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from temporalio.common import RetryPolicy

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionSnapshot,
    CodexManagedSessionWorkflowInput,
)
from moonmind.workflows.temporal.workflows import agent_session as agent_session_module
from moonmind.workflows.temporal.workflows.agent_session import (
    AGENT_SESSION_STATUS_ACTIVE,
    AGENT_SESSION_STATUS_CLEARING,
    AGENT_SESSION_STATUS_TERMINATING,
    MAX_REQUEST_TRACKING_ENTRIES,
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


def _assert_forbidden_metadata_absent(value: object) -> None:
    rendered = str(value)
    for forbidden in (
        "Write a private implementation plan",
        "terminal scrollback",
        "raw log line",
        "ghp_secret_token",
        "password=hunter2",
        "traceback body",
    ):
        assert forbidden not in rendered


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


def test_agent_session_initializes_bounded_temporal_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    current_details: list[str] = []
    search_attributes: list[dict[str, object]] = []
    monkeypatch.setattr(
        agent_session_module.workflow,
        "set_current_details",
        lambda details: current_details.append(details),
    )
    monkeypatch.setattr(
        agent_session_module.workflow,
        "upsert_search_attributes",
        lambda attributes: search_attributes.append(attributes),
    )

    MoonMindAgentSessionWorkflow(_workflow_input())

    assert current_details == [
        "Codex managed session session started | "
        "session=sess:wf-run-1:codex_cli | runtime=codex_cli | "
        "epoch=1 | status=active"
    ]
    assert search_attributes == [
        {
            "TaskRunId": ["wf-run-1"],
            "RuntimeId": ["codex_cli"],
            "SessionId": ["sess:wf-run-1:codex_cli"],
            "SessionEpoch": [1],
            "SessionStatus": ["active"],
            "IsDegraded": [False],
        }
    ]


def test_agent_session_visibility_and_activity_summaries_exclude_forbidden_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    current_details: list[str] = []
    search_attributes: list[dict[str, object]] = []
    monkeypatch.setattr(
        agent_session_module.workflow,
        "set_current_details",
        lambda details: current_details.append(details),
    )
    monkeypatch.setattr(
        agent_session_module.workflow,
        "upsert_search_attributes",
        lambda attributes: search_attributes.append(attributes),
    )
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())

    summary = workflow._activity_summary(
        "agent_runtime.send_turn",
        {
            "sessionId": "sess:wf-run-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
            "turnId": "turn-1",
            "instructions": "Write a private implementation plan",
            "transcript": "terminal scrollback",
            "rawLog": "raw log line",
            "token": "ghp_secret_token",
            "password": "password=hunter2",
            "error": "traceback body",
        },
    )

    assert "Send managed Codex turn" in summary
    _assert_forbidden_metadata_absent(summary)
    _assert_forbidden_metadata_absent(current_details)
    _assert_forbidden_metadata_absent(search_attributes)


def test_agent_session_send_follow_up_validator_allows_pre_handle_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())

    workflow.validate_send_follow_up({"message": "Continue the task-scoped session."})


def test_agent_session_workflow_input_carries_request_tracking_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow_input = _workflow_input(
        requestTrackingState=[
            {
                "requestId": "request-1",
                "action": "send_turn",
                "sessionEpoch": 1,
                "status": "completed",
                "resultRef": "art-control-1",
            }
        ]
    )
    workflow = MoonMindAgentSessionWorkflow(workflow_input)

    status = workflow.get_status()
    snapshot = CodexManagedSessionSnapshot.model_validate(status)

    assert status["requestTrackingState"] == [
        {
            "requestId": "request-1",
            "action": "send_turn",
            "sessionEpoch": 1,
            "status": "completed",
            "resultRef": "art-control-1",
        }
    ]
    assert snapshot.request_tracking_state[0].request_id == "request-1"


def test_agent_session_restore_caps_request_tracking_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow_input = _workflow_input(
        requestTrackingState=[
            {
                "requestId": f"request-{index}",
                "action": "send_turn",
                "sessionEpoch": 1,
                "status": "completed",
            }
            for index in range(MAX_REQUEST_TRACKING_ENTRIES + 5)
        ]
    )

    workflow = MoonMindAgentSessionWorkflow(workflow_input)

    status = workflow.get_status()
    restored_entries = status["requestTrackingState"]
    assert len(restored_entries) == MAX_REQUEST_TRACKING_ENTRIES
    assert restored_entries[0]["requestId"] == "request-5"
    assert restored_entries[-1]["requestId"] == "request-104"


def test_agent_session_request_tracking_rejects_cross_action_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    workflow._record_request_tracking(
        request_id="request-1",
        action="send_turn",
        status="accepted",
    )

    with pytest.raises(ValueError, match="already used for action send_turn"):
        workflow._validate_request_not_completed(
            request_id="request-1",
            action="clear_session",
        )


@pytest.mark.asyncio
async def test_agent_session_request_tracking_prefers_temporal_update_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(
        _workflow_input(containerId="container-1", threadId="thread-1")
    )
    monkeypatch.setattr(
        agent_session_module.workflow,
        "current_update_info",
        lambda: agent_session_module.workflow.UpdateInfo(
            id="temporal-update-1",
            name="SendFollowUp",
        ),
    )

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        del payload
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
                "latestSummaryRef": "art-summary-2",
            }
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    await workflow.send_follow_up(
        {
            "message": "Continue the task-scoped session.",
            "requestId": "caller-request-1",
        }
    )

    assert workflow.get_status()["requestTrackingState"] == [
        {
            "requestId": "temporal-update-1",
            "action": "send_turn",
            "sessionEpoch": 1,
            "status": "completed",
            "resultRef": "art-summary-2",
        }
    ]


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
async def test_agent_session_terminate_update_executes_remote_terminate_when_handles_exist(
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

    captured: list[tuple[str, dict[str, object], dict[str, object]]] = []

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        captured.append((activity_name, payload, kwargs))
        if activity_name == "agent_runtime.terminate_session":
            assert workflow._termination_requested is False
            return {
                "sessionState": {
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 1,
                    "containerId": "container-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "status": "terminated",
                "imageRef": "moonmind:latest",
                "controlUrl": "docker-exec://container-1",
                "metadata": {},
            }
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    status = await workflow.terminate_session_update({"reason": "done"})

    assert [name for name, _, _ in captured] == ["agent_runtime.terminate_session"]
    assert captured[0][1]["containerId"] == "container-1"
    assert captured[0][1]["threadId"] == "thread-1"
    assert status["status"] == AGENT_SESSION_STATUS_TERMINATING
    assert status["terminationRequested"] is True
    assert status["activeTurnId"] is None


@pytest.mark.asyncio
async def test_agent_session_terminate_update_keeps_terminating_when_remote_terminate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []

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
        {
            "info": lambda *a, **k: None,
            "warning": lambda _self, message, *args: warnings.append(message % args),
        },
    )()
    monkeypatch.setattr(agent_session_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_session_module.workflow, "logger", logger)

    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
            "activeTurnId": "turn-1",
        }
    )

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        del payload, kwargs
        if activity_name == "agent_runtime.terminate_session":
            raise RuntimeError("terminate failed")
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    with pytest.raises(RuntimeError, match="terminate failed"):
        await workflow.terminate_session_update(
            {"reason": "done", "requestId": "request-terminate-1"}
        )

    status = workflow.get_status()
    assert status["status"] == AGENT_SESSION_STATUS_TERMINATING
    assert status["terminationRequested"] is False
    assert status["requestTrackingState"] == [
        {
            "requestId": "request-terminate-1",
            "action": "terminate_session",
            "sessionEpoch": 1,
            "status": "failed",
            "resultRef": None,
        }
    ]
    assert warnings == []


@pytest.mark.asyncio
async def test_agent_session_cancel_interrupts_active_turn_without_terminating(
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
            assert payload["turnId"] == "turn-1"
            assert payload["reason"] == "operator cancel"
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
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    status = await workflow.cancel_session_update({"reason": "operator cancel"})

    assert captured == ["agent_runtime.interrupt_turn"]
    assert status["status"] == AGENT_SESSION_STATUS_ACTIVE
    assert status["terminationRequested"] is False
    assert status["activeTurnId"] is None
    assert status["lastControlAction"] == "cancel_session"
    assert status["lastControlReason"] == "operator cancel"


@pytest.mark.asyncio
async def test_agent_session_completed_identified_request_rejects_duplicate_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(
        _workflow_input(
            containerId="container-1",
            threadId="thread-1",
            requestTrackingState=[
                {
                    "requestId": "request-1",
                    "action": "send_turn",
                    "sessionEpoch": 1,
                    "status": "completed",
                    "resultRef": "art-summary-1",
                }
            ],
        )
    )

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        del payload
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    with pytest.raises(ValueError, match="already completed"):
        await workflow.send_follow_up(
            {
                "message": "Do not replay this identified request.",
                "requestId": "request-1",
            }
        )


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
        {
            "message": "Continue the task-scoped session.",
            "reason": "Operator follow-up",
            "requestId": "request-send-1",
        }
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
    status = workflow.get_status()
    assert status["lastControlAction"] == "send_turn"
    assert status["requestTrackingState"] == [
        {
            "requestId": "request-send-1",
            "action": "send_turn",
            "sessionEpoch": 1,
            "status": "completed",
            "resultRef": "art-summary-2",
        }
    ]

    for name, _payload, kwargs in captured:
        assert kwargs["task_queue"] == "mm.activity.agent_runtime"
        assert isinstance(kwargs["retry_policy"], RetryPolicy)
        assert "summary" in kwargs
        route = agent_session_module.DEFAULT_ACTIVITY_CATALOG.resolve_activity(name)
        assert kwargs["start_to_close_timeout"] == timedelta(
            seconds=route.timeouts.start_to_close_seconds
        )

    assert captured[0][2]["summary"] == (
        "Send managed Codex turn: "
        "sessionId=sess:wf-run-1:codex_cli, sessionEpoch=1, "
        "containerId=container-1, threadId=thread-1"
    )


@pytest.mark.asyncio
async def test_agent_session_updates_visibility_on_major_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    current_details: list[str] = []
    search_attributes: list[dict[str, object]] = []
    monkeypatch.setattr(
        agent_session_module.workflow,
        "set_current_details",
        lambda details: current_details.append(details),
    )
    monkeypatch.setattr(
        agent_session_module.workflow,
        "upsert_search_attributes",
        lambda attributes: search_attributes.append(attributes),
    )
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    workflow.attach_runtime_handles(
        {
            "containerId": "container-1",
            "threadId": "thread-1",
            "activeTurnId": "turn-1",
        }
    )

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        if activity_name == "agent_runtime.interrupt_turn":
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
                "latestSummaryRef": "art-summary-after-interrupt",
                "latestCheckpointRef": "art-checkpoint-after-interrupt",
                "latestControlEventRef": "art-control-after-interrupt",
            }
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    await workflow.interrupt_turn_update(
        {"sessionEpoch": 1, "reason": "Stop this turn."}
    )

    assert any("active turn running" in details for details in current_details)
    assert any("interrupted" in details for details in current_details)
    assert current_details[-1].endswith(
        "checkpointRef=art-checkpoint-after-interrupt | "
        "controlRef=art-control-after-interrupt"
    )
    assert search_attributes[-1] == {
        "TaskRunId": ["wf-run-1"],
        "RuntimeId": ["codex_cli"],
        "SessionId": ["sess:wf-run-1:codex_cli"],
        "SessionEpoch": [1],
        "SessionStatus": ["active"],
        "IsDegraded": [False],
    }


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
    assert status["status"] == AGENT_SESSION_STATUS_ACTIVE
    assert status["binding"]["sessionEpoch"] == 2
    assert status["threadId"] == "thread-2"
    assert status["activeTurnId"] is None
    assert status["lastControlAction"] == "clear_session"
    assert status["lastControlReason"] == "Reset stale context"


def test_agent_session_legacy_control_action_signal_replays_clear_session(
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

    workflow.apply_control_action(
        {
            "action": "clear_session",
            "reason": "Replay old clear event",
            "threadId": "thread-2",
        }
    )

    status = workflow.get_status()
    assert status["status"] == AGENT_SESSION_STATUS_ACTIVE
    assert status["binding"]["sessionEpoch"] == 2
    assert status["threadId"] == "thread-2"
    assert status["activeTurnId"] is None
    assert status["lastControlAction"] == "clear_session"
    assert status["lastControlReason"] == "Replay old clear event"


@pytest.mark.asyncio
async def test_agent_session_async_mutators_wait_for_workflow_lock(
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
        if activity_name == "agent_runtime.clear_session":
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
                "publishedArtifactRefs": [],
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

    await workflow._mutation_lock.acquire()
    clear_task = asyncio.create_task(
        workflow.clear_session_update({"reason": "Reset stale context"})
    )
    await asyncio.sleep(0)

    assert not captured

    workflow._mutation_lock.release()
    clear_result = await clear_task

    assert clear_result["sessionState"]["sessionEpoch"] == 2
    assert captured == [
        "agent_runtime.clear_session",
        "agent_runtime.fetch_session_summary",
        "agent_runtime.publish_session_artifacts",
    ]


@pytest.mark.asyncio
async def test_agent_session_send_follow_up_waits_for_runtime_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    wait_calls = 0
    activity_names: list[str] = []

    async def _wait_condition(predicate, **_kwargs: object) -> None:
        nonlocal wait_calls
        wait_calls += 1
        assert predicate() is False
        workflow.attach_runtime_handles(
            {
                "containerId": "container-1",
                "threadId": "thread-1",
            }
        )
        assert predicate() is True

    async def _execute_activity(
        activity_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        activity_names.append(activity_name)
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
                "latestSummaryRef": "art-summary-2",
            }
        raise AssertionError(f"unexpected activity: {activity_name}")

    monkeypatch.setattr(agent_session_module.workflow, "wait_condition", _wait_condition)
    monkeypatch.setattr(
        agent_session_module.workflow,
        "execute_activity",
        _execute_activity,
    )

    result = await workflow.send_follow_up({"message": "Continue after launch."})

    assert wait_calls == 1
    assert result["latestSummaryRef"] == "art-summary-2"
    assert workflow.get_status()["latestSummaryRef"] == "art-summary-2"
    assert activity_names == [
        "agent_runtime.send_turn",
        "agent_runtime.fetch_session_summary",
        "agent_runtime.publish_session_artifacts",
    ]


@pytest.mark.asyncio
async def test_agent_session_run_waits_for_handlers_before_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    workflow._termination_requested = True
    waited_for_handlers = False
    monkeypatch.setattr(agent_session_module.workflow, "all_handlers_finished", True)

    async def _wait_condition(predicate, **_kwargs: object) -> None:
        nonlocal waited_for_handlers
        waited_for_handlers = True
        assert predicate() is True

    monkeypatch.setattr(agent_session_module.workflow, "wait_condition", _wait_condition)

    status = await workflow.run(_workflow_input())

    assert waited_for_handlers is True
    assert status["status"] == "terminated"


@pytest.mark.asyncio
async def test_agent_session_run_waits_for_state_change_without_timeout_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindAgentSessionWorkflow(_workflow_input())
    state_waits = 0
    monkeypatch.setattr(agent_session_module.workflow, "all_handlers_finished", True)

    async def _wait_condition(predicate, **kwargs: object) -> None:
        nonlocal state_waits
        assert "timeout" not in kwargs
        if workflow._termination_requested:
            assert predicate() is True
            return
        state_waits += 1
        assert predicate() is False
        workflow._termination_requested = True
        assert predicate() is True

    monkeypatch.setattr(agent_session_module.workflow, "wait_condition", _wait_condition)

    status = await workflow.run(_workflow_input())

    assert state_waits == 1
    assert status["status"] == "terminated"


@pytest.mark.asyncio
async def test_agent_session_continue_as_new_carries_bounded_session_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-run-1:session:codex_cli",
            "run_id": "run-session-1",
            "task_queue": "mm.workflow",
            "get_current_history_length": lambda _self: 12,
            "is_continue_as_new_suggested": False,
        },
    )()
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(agent_session_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(agent_session_module.workflow, "logger", logger)

    workflow = MoonMindAgentSessionWorkflow(
        _workflow_input(
            executionProfileRef="codex-default",
            containerId="container-1",
            threadId="thread-1",
            activeTurnId="turn-1",
            lastControlAction="send_turn",
            lastControlReason="operator follow-up",
            latestSummaryRef="art-summary",
            latestCheckpointRef="art-checkpoint",
            latestControlEventRef="art-control",
            latestResetBoundaryRef="art-reset",
            continueAsNewEventThreshold=10,
            requestTrackingState=[
                {
                    "requestId": "request-send-1",
                    "action": "send_turn",
                    "sessionEpoch": 1,
                    "status": "completed",
                    "resultRef": "art-summary",
                }
            ],
        )
    )
    waited_for_handlers = False
    captured_payload: dict[str, object] | None = None
    wait_calls = 0
    monkeypatch.setattr(agent_session_module.workflow, "all_handlers_finished", True)

    async def _wait_condition(predicate, **_kwargs: object) -> None:
        nonlocal wait_calls, waited_for_handlers
        wait_calls += 1
        if wait_calls == 1:
            assert predicate() is True
            return
        assert predicate() is True
        waited_for_handlers = True

    def _continue_as_new(payload: dict[str, object]) -> None:
        nonlocal captured_payload
        captured_payload = payload
        raise RuntimeError("continue-as-new requested")

    monkeypatch.setattr(agent_session_module.workflow, "wait_condition", _wait_condition)
    monkeypatch.setattr(
        agent_session_module.workflow,
        "continue_as_new",
        _continue_as_new,
    )

    with pytest.raises(RuntimeError, match="continue-as-new requested"):
        await workflow.run(_workflow_input())

    assert waited_for_handlers is True
    assert captured_payload == {
        "taskRunId": "wf-run-1",
        "runtimeId": "codex_cli",
        "sessionId": "sess:wf-run-1:codex_cli",
        "sessionEpoch": 1,
        "executionProfileRef": "codex-default",
        "containerId": "container-1",
        "threadId": "thread-1",
        "activeTurnId": "turn-1",
        "lastControlAction": "send_turn",
        "lastControlReason": "operator follow-up",
        "latestSummaryRef": "art-summary",
        "latestCheckpointRef": "art-checkpoint",
        "latestControlEventRef": "art-control",
        "latestResetBoundaryRef": "art-reset",
        "continueAsNewEventThreshold": 10,
        "requestTrackingState": [
            {
                "requestId": "request-send-1",
                "action": "send_turn",
                "sessionEpoch": 1,
                "status": "completed",
                "resultRef": "art-summary",
            }
        ],
    }
