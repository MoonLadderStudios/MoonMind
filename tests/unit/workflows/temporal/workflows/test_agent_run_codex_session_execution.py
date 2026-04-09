from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


pytestmark = [pytest.mark.asyncio]


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-agent-run-1",
            "run_id": "run-1",
            "search_attributes": {},
            "parent": None,
        },
    )
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "logger", logger)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )


def _managed_session_request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId="corr-managed-1",
        idempotencyKey="idem-managed-1",
        instructionRef="artifact:instructions",
        managedSession={
            "workflowId": "wf-task-1:session:codex_cli",
            "taskRunId": "wf-task-1",
            "sessionId": "sess:wf-task-1:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "codex-default",
        },
        parameters={"publishMode": "none"},
    )


async def test_agent_run_uses_codex_session_adapter_for_managed_codex_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    session_adapter_requests: list[AgentExecutionRequest] = []
    loaded_snapshots: list[dict[str, Any]] = []
    requested_snapshot_workflow_ids: list[str] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("ManagedAgentAdapter should not be used for managedSession requests")

    class _FakeCodexSessionAdapter:
        def __init__(self, **kwargs: Any) -> None:
            self._load_session_snapshot = kwargs["load_session_snapshot"]

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            session_adapter_requests.append(request)
            snapshot_workflow_id = "wf-task-1:session:override"
            requested_snapshot_workflow_ids.append(snapshot_workflow_id)
            loaded_snapshots.append(
                await self._load_session_snapshot(snapshot_workflow_id)
            )
            return AgentRunHandle(
                runId="managed-session-run-1",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
                metadata={"sessionId": request.managed_session.session_id},
            )

        async def status(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="completed",
            )

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            raise AssertionError(
                "Terminal managed-session runs should fetch results through "
                "agent_runtime.fetch_result"
            )

        async def cancel(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="canceled",
            )

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.load_session_snapshot":
            return {
                "binding": {
                    "workflowId": "wf-task-1:session:codex_cli",
                    "taskRunId": "wf-task-1",
                    "sessionId": "sess:wf-task-1:codex_cli",
                    "sessionEpoch": 1,
                    "runtimeId": "codex_cli",
                    "executionProfileRef": "codex-default",
                },
                "status": "active",
                "containerId": None,
                "threadId": None,
                "activeTurnId": None,
                "terminationRequested": False,
            }
        if activity_name == "agent_runtime.fetch_result":
            return {
                "summary": "Session-backed Codex step completed.",
                "metadata": {"resultSource": "agent-runtime-fetch-result"},
            }
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_managed_session_request())

    assert session_adapter_requests[0].managed_session is not None
    assert requested_snapshot_workflow_ids == ["wf-task-1:session:override"]
    assert loaded_snapshots == [
        {
            "binding": {
                "workflowId": "wf-task-1:session:codex_cli",
                "taskRunId": "wf-task-1",
                "sessionId": "sess:wf-task-1:codex_cli",
                "sessionEpoch": 1,
                "runtimeId": "codex_cli",
                "executionProfileRef": "codex-default",
            },
            "status": "active",
            "containerId": None,
            "threadId": None,
            "activeTurnId": None,
            "terminationRequested": False,
        }
    ]
    assert run.run_id == "managed-session-run-1"
    assert result.summary == "Session-backed Codex step completed."
    assert result.metadata["resultSource"] == "agent-runtime-fetch-result"
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"
    assert result.metadata["childRunId"] == "run-1"
    assert result.metadata["taskRunId"] == "wf-task-1"
    assert result.metadata["managedSession"]["sessionId"] == "sess:wf-task-1:codex_cli"
    assert [name for name, _payload in routed_calls] == [
        "agent_runtime.load_session_snapshot",
        "agent_runtime.fetch_result",
        "agent_runtime.publish_artifacts",
    ]
    assert routed_calls[0][1]["workflowId"] == "wf-task-1:session:override"
    assert routed_calls[0][1]["taskRunId"] == "wf-task-1"
    assert routed_calls[1][1] == {
        "run_id": "managed-session-run-1",
        "agent_id": "codex",
    }


async def test_agent_run_keeps_managed_adapter_for_non_session_managed_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    managed_requests: list[AgentExecutionRequest] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            managed_requests.append(request)
            return AgentRunHandle(
                runId="managed-run-1",
                agentKind="managed",
                agentId=request.agent_id,
                status="running",
                startedAt=agent_run_module.workflow.now(),
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("CodexSessionAdapter should not be used without managedSession")

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        run.completion_event.set()

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Managed adapter path", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            executionProfileRef="codex-default",
            correlationId="corr-managed-plain-1",
            idempotencyKey="idem-managed-plain-1",
            parameters={"publishMode": "none"},
        )
    )

    assert len(managed_requests) == 1
    assert managed_requests[0].managed_session is None
    assert result.summary == "Managed adapter path"
    assert result.metadata["taskRunId"] == "managed-run-1"
    assert [name for name, _payload in routed_calls] == [
        "agent_runtime.fetch_result",
        "agent_runtime.publish_artifacts",
    ]
