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
    _MAX_SUMMARY_CHARS,
)
from moonmind.schemas.temporal_activity_models import AgentRuntimeFetchResultInput
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


pytestmark = [pytest.mark.asyncio]


def _patch_all_enabled(_patch_id: str) -> bool:
    return True


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
    monkeypatch.setattr(agent_run_module.workflow, "patched", _patch_all_enabled)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )


def _managed_session_request(
    *,
    parameters: dict[str, Any] | None = None,
    workspace_spec: dict[str, Any] | None = None,
    instruction_ref: str = "artifact:instructions",
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId="corr-managed-1",
        idempotencyKey="idem-managed-1",
        instructionRef=instruction_ref,
        managedSession={
            "workflowId": "wf-task-1:session:codex_cli",
            "taskRunId": "wf-task-1",
            "sessionId": "sess:wf-task-1:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "codex-default",
        },
        parameters=parameters if parameters is not None else {"publishMode": "none"},
        workspaceSpec=workspace_spec if workspace_spec is not None else {},
    )


async def test_managed_session_request_preserves_explicit_empty_inputs() -> None:
    request = _managed_session_request(parameters={}, workspace_spec={})

    assert request.parameters == {}
    assert request.workspace_spec == {}


async def test_managed_fetch_result_input_ignores_legacy_workspace_branch_for_head_branch() -> None:
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={"publishMode": "pr"},
        workspace_spec={"branch": "main", "startingBranch": "main"},
    )

    activity_input = run._build_managed_fetch_result_activity_input(request)

    assert isinstance(activity_input, AgentRuntimeFetchResultInput)
    assert activity_input.target_branch == "main"
    assert activity_input.head_branch is None


async def test_managed_session_result_enrichment_omits_large_inline_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    large_instruction = "Use this request as the canonical input:\n" + (
        "Implement the workflow cleanup. " * 400
    )
    request = _managed_session_request(instruction_ref=large_instruction)

    result = run._enrich_result_metadata(
        request=request,
        result=AgentRunResult(summary="done", metadata={}),
    )

    assert result is not None
    assert result.metadata["instructionRefOmitted"] is True
    assert result.metadata["instructionRefLengthChars"] == len(large_instruction.strip())
    assert len(result.metadata["instructionRefSha256"]) == 64
    assert "instructionRef" not in result.metadata
    assert result.metadata["managedSession"]["taskRunId"] == "wf-task-1"


async def test_agent_run_uses_codex_session_adapter_for_managed_codex_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    session_adapter_requests: list[AgentExecutionRequest] = []
    loaded_snapshots: list[dict[str, Any]] = []
    requested_snapshot_workflow_ids: list[str] = []
    prepared_instruction_results: list[str] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("ManagedAgentAdapter should not be used for managedSession requests")

    class _FakeCodexSessionAdapter:
        def __init__(self, **kwargs: Any) -> None:
            self._load_session_snapshot = kwargs["load_session_snapshot"]
            self._prepare_turn_instructions = kwargs["prepare_turn_instructions"]

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            session_adapter_requests.append(request)
            snapshot_workflow_id = "wf-task-1:session:override"
            requested_snapshot_workflow_ids.append(snapshot_workflow_id)
            loaded_snapshots.append(
                await self._load_session_snapshot(snapshot_workflow_id)
            )
            prepared_instruction_results.append(
                await self._prepare_turn_instructions(
                    {
                        "request": request.model_dump(by_alias=True, exclude_none=True),
                        "workspacePath": "/work/task/repo",
                    }
                )
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
        manager_id: str,
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
        if activity_name == "agent_runtime.prepare_turn_instructions":
            return "Prepared session instructions"
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
    assert prepared_instruction_results == ["Prepared session instructions"]
    assert run.run_id == "managed-session-run-1"
    assert result.summary == "Session-backed Codex step completed."
    assert result.metadata["resultSource"] == "agent-runtime-fetch-result"
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"
    assert result.metadata["childRunId"] == "run-1"
    assert result.metadata["taskRunId"] == "wf-task-1"
    assert result.metadata["managedSession"]["sessionId"] == "sess:wf-task-1:codex_cli"
    assert [name for name, _payload in routed_calls] == [
        "agent_runtime.load_session_snapshot",
        "agent_runtime.prepare_turn_instructions",
        "agent_runtime.fetch_result",
        "agent_runtime.publish_artifacts",
    ]
    assert routed_calls[0][1]["workflowId"] == "wf-task-1:session:override"
    assert routed_calls[0][1]["taskRunId"] == "wf-task-1"
    assert routed_calls[1][1]["workspacePath"] == "/work/task/repo"
    assert isinstance(routed_calls[2][1], AgentRuntimeFetchResultInput)
    assert routed_calls[2][1].run_id == "wf-task-1"
    assert routed_calls[2][1].agent_id == "codex"


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
        manager_id: str,
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


async def test_agent_run_managed_session_passes_publish_branch_context_to_fetch_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-publish",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
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

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        run.completion_event.set()

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
        manager_id: str,
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
            return {"summary": "Managed publish success", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        if activity_name == "agent_runtime.load_session_snapshot":
            return {
                "binding": _managed_session_request().managed_session.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "status": "active",
                "containerId": None,
                "threadId": None,
                "activeTurnId": None,
                "terminationRequested": False,
            }
        if activity_name == "agent_runtime.prepare_turn_instructions":
            return "Prepared publish instructions"
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
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        _managed_session_request(
            parameters={"publishMode": "pr"},
            workspace_spec={
                "startingBranch": "main",
                "targetBranch": "feature/recover-detached-head",
            },
        )
    )

    assert result.summary == "Managed publish success"
    fetch_payload = next(
        payload for name, payload in routed_calls if name == "agent_runtime.fetch_result"
    )
    assert isinstance(fetch_payload, AgentRuntimeFetchResultInput)
    assert fetch_payload.publish_mode == "pr"
    assert fetch_payload.target_branch == "main"
    assert fetch_payload.head_branch == "feature/recover-detached-head"


async def test_agent_run_managed_session_start_runtime_error_returns_failed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    manager_signals: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            raise RuntimeError(
                "codex app-server request thread/resume failed: "
                "{'code': -32600, 'message': 'no rollout found for thread id thread-1'}"
            )

        async def status(self, run_id: str) -> AgentRunStatus:
            raise AssertionError("status should not be polled after start failure")

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            raise AssertionError("fetch_result should not run after start failure")

        async def cancel(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="canceled",
            )

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            manager_signals.append((signal_name, payload))

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
        manager_id: str,
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
        run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal
    )
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )

    result = await run.run(_managed_session_request())

    assert result.failure_class == "execution_error"
    assert "no rollout found for thread id" in result.summary
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"
    assert result.metadata["childRunId"] == "run-1"
    assert result.metadata["taskRunId"] == "wf-task-1"
    assert [name for name, _payload in routed_calls] == ["agent_runtime.publish_artifacts"]
    assert manager_signals[-1] == (
        "release_slot",
        {
            "requester_workflow_id": "wf-agent-run-1",
            "profile_id": "codex-default",
        },
    )


async def test_agent_run_managed_session_start_runtime_error_truncates_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            raise RuntimeError("managed start failed: " + ("x" * 5000))

        async def status(self, run_id: str) -> AgentRunStatus:
            raise AssertionError("status should not be polled after start failure")

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            raise AssertionError("fetch_result should not run after start failure")

        async def cancel(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="canceled",
            )

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
        manager_id: str,
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
        run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal
    )
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )

    result = await run.run(_managed_session_request())

    assert result.failure_class == "execution_error"
    assert result.summary is not None
    assert len(result.summary) == _MAX_SUMMARY_CHARS
    assert result.summary.endswith("...")
    assert result.summary.startswith("managed start failed: ")


async def test_agent_run_clears_auto_profile_after_provider_cooldown_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    ensure_profile_refs: list[str | None] = []
    manager_signals: list[tuple[str, dict[str, Any]]] = []
    fetch_results = [
        AgentRunResult(
            failureClass="execution_error",
            providerErrorCode="provider_capacity",
            retryRecommendation="retry_after_cooldown",
            summary="provider capacity",
        ),
        AgentRunResult(summary="Recovered on fallback profile."),
    ]

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId=f"managed-session-run-{len(ensure_profile_refs)}",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: dict[str, Any]) -> None:
            manager_signals.append((signal_name, payload))

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
        if request_slot:
            ensure_profile_refs.append(execution_profile_ref)
            run.slot_assigned_event.set()
            run._assigned_profile_id = (
                "codex_openrouter_qwen36_plus"
                if len(ensure_profile_refs) == 1
                else "codex-default"
            )
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        run._profile_snapshots = {
            "codex_openrouter_qwen36_plus": {
                "cooldown_after_429_seconds": 0,
            },
            "codex-default": {
                "cooldown_after_429_seconds": 0,
            },
        }
        return 2

    async def fake_fetch_managed_result(**_kwargs: Any) -> AgentRunResult:
        return fetch_results.pop(0)

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(run, "_fetch_managed_result", fake_fetch_managed_result)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )

    request = _managed_session_request().model_copy(
        update={"execution_profile_ref": None}
    )

    result = await run.run(request)

    assert result.summary == "Recovered on fallback profile."
    assert ensure_profile_refs == [None, None]
    assert manager_signals[:2] == [
        (
            "report_cooldown",
            {
                "profile_id": "codex_openrouter_qwen36_plus",
                "cooldown_seconds": 0,
            },
        ),
        (
            "release_slot",
            {
                "requester_workflow_id": "wf-agent-run-1",
                "profile_id": "codex_openrouter_qwen36_plus",
            },
        ),
    ]


async def test_agent_run_keeps_legacy_session_fetch_path_when_patch_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    def _patched(patch_id: str) -> bool:
        return (
            patch_id
            != agent_run_module.MANAGED_SESSION_FETCH_RESULT_ACTIVITY_PATCH_ID
        )

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-legacy",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

        async def fetch_result(
            self,
            run_id: str,
            *,
            pr_resolver_expected: bool = False,
        ) -> AgentRunResult:
            assert run_id == "managed-session-run-legacy"
            assert pr_resolver_expected is False
            return AgentRunResult(summary="Legacy managed-session fetch path.")

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
        manager_id: str,
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
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "patched", _patched)
    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_managed_session_request())

    assert result.summary == "Legacy managed-session fetch path."
    assert [name for name, _payload in routed_calls] == [
        "agent_runtime.publish_artifacts",
    ]


async def test_agent_run_keeps_legacy_instruction_preparation_for_pre_patch_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    _configure_workflow_runtime(monkeypatch)

    def _patched(patch_id: str) -> bool:
        return (
            patch_id
            != agent_run_module.MANAGED_SESSION_PREPARE_TURN_INSTRUCTIONS_ACTIVITY_PATCH_ID
        )

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["prepare_turn_instructions"] is None

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-legacy-prepare",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

        async def fetch_result(
            self,
            run_id: str,
            *,
            pr_resolver_expected: bool = False,
        ) -> AgentRunResult:
            assert run_id == "managed-session-run-legacy-prepare"
            assert pr_resolver_expected is False
            return AgentRunResult(summary="Legacy instruction preparation path.")

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
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Legacy instruction preparation path.", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "patched", _patched)
    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_managed_session_request())

    assert result.summary == "Legacy instruction preparation path."
