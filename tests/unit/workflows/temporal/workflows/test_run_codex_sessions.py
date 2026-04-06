from __future__ import annotations

from typing import Any

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.managed_session_models import CodexManagedSessionBinding
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-run-1",
            "run_id": "run-1",
            "task_queue": "mm.workflow",
        },
    )
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_module.workflow, "logger", logger)


def _managed_request(agent_id: str = "codex") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId=agent_id,
        correlationId="corr-1",
        idempotencyKey="idem-1",
        executionProfileRef="codex-default",
    )


@pytest.mark.asyncio
async def test_run_starts_one_task_scoped_codex_session_and_reuses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    _configure_workflow_runtime(monkeypatch)
    start_calls: list[tuple[str, Any, str, str]] = []

    class _FakeHandle:
        async def signal(self, _signal_name: str, _payload: Any = None) -> None:
            return None

    async def fake_start_child_workflow(
        workflow_name: str,
        payload: Any,
        *,
        id: str,
        task_queue: str,
        **_kwargs: Any,
    ) -> _FakeHandle:
        start_calls.append((workflow_name, payload, id, task_queue))
        return _FakeHandle()

    monkeypatch.setattr(
        run_module.workflow,
        "start_child_workflow",
        fake_start_child_workflow,
    )

    first = await workflow._maybe_bind_task_scoped_session(_managed_request("codex"))
    second = await workflow._maybe_bind_task_scoped_session(
        _managed_request("codex_cli")
    )

    assert len(start_calls) == 1
    workflow_name, payload, workflow_id, task_queue = start_calls[0]
    assert workflow_name == "MoonMind.AgentSession"
    assert workflow_id == "wf-run-1:session:codex_cli"
    assert task_queue == run_module.WORKFLOW_TASK_QUEUE
    assert payload.task_run_id == "wf-run-1"
    assert payload.runtime_id == "codex_cli"
    assert payload.execution_profile_ref == "codex-default"
    assert first.managed_session is not None
    assert second.managed_session is not None
    assert first.managed_session == second.managed_session
    assert first.managed_session.session_id == "sess:wf-run-1:codex_cli"
    assert first.managed_session.session_epoch == 1


@pytest.mark.asyncio
async def test_run_skips_task_scoped_session_for_non_codex_managed_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    _configure_workflow_runtime(monkeypatch)
    started = False

    async def fake_start_child_workflow(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal started
        started = True
        raise AssertionError("start_child_workflow should not be called")

    monkeypatch.setattr(
        run_module.workflow,
        "start_child_workflow",
        fake_start_child_workflow,
    )

    request = await workflow._maybe_bind_task_scoped_session(
        _managed_request("claude_code")
    )

    assert request.managed_session is None
    assert started is False


@pytest.mark.asyncio
async def test_run_terminates_active_task_scoped_codex_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    _configure_workflow_runtime(monkeypatch)
    signal_calls: list[tuple[str, Any]] = []

    class _FakeHandle:
        async def signal(self, signal_name: str, payload: Any = None) -> None:
            signal_calls.append((signal_name, payload))

    workflow._codex_session_handle = _FakeHandle()
    workflow._codex_session_binding = CodexManagedSessionBinding(
        workflowId="wf-run-1:session:codex_cli",
        taskRunId="wf-run-1",
        sessionId="sess:wf-run-1:codex_cli",
        sessionEpoch=1,
        runtimeId="codex_cli",
        executionProfileRef="codex-default",
    )

    await workflow._terminate_task_scoped_sessions(reason="success")

    assert signal_calls == [
        (
            "control_action",
            {
                "action": "terminate_session",
                "reason": "success",
            },
        )
    ]
    assert workflow._codex_session_handle is None
    assert workflow._codex_session_binding is None
