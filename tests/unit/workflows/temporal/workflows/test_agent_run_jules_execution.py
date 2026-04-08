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


def _request(**overrides: Any) -> AgentExecutionRequest:
    payload = {
        "agentKind": "external",
        "agentId": "jules",
        "executionProfileRef": "profile:jules-default",
        "correlationId": "corr-1",
        "idempotencyKey": "idem-1",
        "instructionRef": "Implement the requested change.",
        "workspaceSpec": {"startingBranch": "feature-branch"},
        "parameters": {"publishMode": "none"},
    }
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


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


async def test_agent_run_jules_starts_new_run_instead_of_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.resolve_adapter_metadata":
            return {"agent_id": payload, "execution_style": "polling"}
        if activity_name == "integration.jules.start":
            return {"external_id": "new-session-1", "status": "queued"}
        if activity_name == "integration.jules.status":
            return {"normalized_status": "completed"}
        if activity_name == "integration.jules.fetch_result":
            return {"summary": "Done", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        _request(
            parameters={
                "publishMode": "none",
                "jules_session_id": "legacy-session-42",
            }
        )
    )

    assert routed_calls[0][0] == "integration.resolve_adapter_metadata"
    assert routed_calls[1][0] == "integration.jules.start"
    assert all(name != "integration.jules.send_message" for name, _ in routed_calls)
    assert run.run_id == "new-session-1"
    assert result.failure_class is None
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"
    assert result.metadata["childRunId"] == "run-1"


async def test_agent_run_jules_branch_publish_failure_maps_to_non_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.resolve_adapter_metadata":
            return {"agent_id": payload, "execution_style": "polling"}
        if activity_name == "integration.jules.start":
            return {"external_id": "session-1", "status": "queued"}
        if activity_name == "integration.jules.status":
            return {"normalized_status": "completed"}
        if activity_name == "integration.jules.fetch_result":
            return {
                "summary": "Provider reported success.",
                "metadata": {
                    "pullRequestUrl": "https://github.com/org/repo/pull/123",
                },
            }
        if activity_name == "repo.merge_pr":
            return {"merged": False, "summary": "Merge rejected"}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        _request(
            workspaceSpec={"startingBranch": "feature-branch", "targetBranch": "main"},
            parameters={"publishMode": "branch", "targetBranch": "main"},
        )
    )

    assert any(name == "repo.merge_pr" for name, _ in routed_calls)
    merge_payload = next(payload for name, payload in routed_calls if name == "repo.merge_pr")
    assert merge_payload == {
        "pr_url": "https://github.com/org/repo/pull/123",
        "target_branch": "main",
    }
    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "branch_publish_failed"
    assert result.metadata["publishOutcome"] == "publish_failed"


async def test_agent_run_managed_passes_commit_message_override_to_fetch_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-run-1",
                agentKind="managed",
                agentId=request.agent_id,
                status="running",
                startedAt=agent_run_module.workflow.now(),
            )

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
        run._assigned_profile_id = execution_profile_ref or "default-managed"
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
            return {"summary": "Managed success", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module,
        "ManagedAgentAdapter",
        _FakeManagedAgentAdapter,
    )
    monkeypatch.setattr(
        run,
        "_ensure_manager_and_signal",
        fake_ensure_manager_and_signal,
    )
    monkeypatch.setattr(
        run,
        "_sync_manager_profiles",
        fake_sync_manager_profiles,
    )
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            correlationId="corr-managed-1",
            idempotencyKey="idem-managed-1",
            parameters={
                "publishMode": "pr",
                "commitMessage": "Use producer commit text",
            },
            workspaceSpec={"startingBranch": "main"},
        )
    )

    assert result.summary == "Managed success"
    fetch_payload = next(
        payload for name, payload in routed_calls if name == "agent_runtime.fetch_result"
    )
    assert fetch_payload["publish_mode"] == "pr"
    assert fetch_payload["commit_message"] == "Use producer commit text"


async def test_agent_run_managed_preserves_task_scoped_session_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("ManagedAgentAdapter should not be used for managedSession requests")

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-2",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

        async def status(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex_cli",
                status="completed",
            )

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            return AgentRunResult(summary="Managed success", metadata={})

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        run.completion_event.set()

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

        async def query(self, query_name: str) -> dict[str, Any]:
            return {
                "binding": {
                    "workflowId": "wf-run-1:session:codex_cli",
                    "taskRunId": "wf-run-1",
                    "sessionId": "sess:wf-run-1:codex_cli",
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

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "default-managed"
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
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module,
        "ManagedAgentAdapter",
        _FakeManagedAgentAdapter,
    )
    monkeypatch.setattr(
        agent_run_module,
        "CodexSessionAdapter",
        _FakeCodexSessionAdapter,
    )
    monkeypatch.setattr(
        run,
        "_ensure_manager_and_signal",
        fake_ensure_manager_and_signal,
    )
    monkeypatch.setattr(
        run,
        "_sync_manager_profiles",
        fake_sync_manager_profiles,
    )
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            correlationId="corr-managed-2",
            idempotencyKey="idem-managed-2",
            executionProfileRef="codex-default",
            managedSession={
                "workflowId": "wf-run-1:session:codex_cli",
                "taskRunId": "wf-run-1",
                "sessionId": "sess:wf-run-1:codex_cli",
                "sessionEpoch": 1,
                "runtimeId": "codex_cli",
            },
        )
    )

    assert result.metadata["managedSession"] == {
        "workflowId": "wf-run-1:session:codex_cli",
        "taskRunId": "wf-run-1",
        "sessionId": "sess:wf-run-1:codex_cli",
        "sessionEpoch": 1,
        "runtimeId": "codex_cli",
        "executionProfileRef": None,
    }
