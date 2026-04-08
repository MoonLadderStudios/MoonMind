"""TDD tests for managed runtime activities — Phase 3 canonical return types.

Validates that agent_runtime_status, agent_runtime_cancel, and
agent_runtime_publish_artifacts return typed Pydantic contracts
(AgentRunStatus, AgentRunResult) instead of dict[str, Any] / None.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    AgentRunStatus,
    ManagedRunRecord,
)
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionHandle,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
)
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal import client as temporal_client_module
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> ManagedRunStore:
    return ManagedRunStore(tmp_path / "run_store")


def _save_record(
    store: ManagedRunStore,
    *,
    run_id: str,
    status: str,
    runtime_id: str = "codex_cli",
    failure_class: str | None = None,
    error_message: str | None = None,
) -> None:
    store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId=runtime_id,
            runtimeId=runtime_id,
            status=status,
            startedAt=datetime.now(tz=UTC),
            failureClass=failure_class,
            errorMessage=error_message,
        )
    )


# ---------------------------------------------------------------------------
# T1: agent_runtime_status — typed AgentRunStatus return
# ---------------------------------------------------------------------------


async def test_status_running_record_returns_typed_model(tmp_path: Path) -> None:
    """T1.1 — running record yields typed AgentRunStatus."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-1", status="running", runtime_id="codex_cli")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-1", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus), f"Expected AgentRunStatus, got {type(result)}"
    assert result.status == "running"
    assert result.agent_kind == "managed"


async def test_status_completed_record_returns_typed_model(tmp_path: Path) -> None:
    """T1.2 — completed record yields typed AgentRunStatus with correct status."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-2", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-2", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "completed"


async def test_status_failed_record_returns_typed_model_with_metadata(tmp_path: Path) -> None:
    """T1.3 — failed record yields typed AgentRunStatus with runtimeId in metadata."""
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="run-3",
        status="failed",
        runtime_id="gemini_cli",
        failure_class="execution_error",
        error_message="Process exited with code 1",
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-3", "agent_id": "gemini_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "failed"
    assert result.metadata is not None
    assert result.metadata.get("runtimeId") == "gemini_cli"


async def test_status_no_record_returns_optimistic_running(tmp_path: Path) -> None:
    """T1.4 — missing record in store yields stub AgentRunStatus with status='running'."""
    store = _make_store(tmp_path)

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "no-such-run", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "running"
    assert result.agent_kind == "managed"


async def test_status_missing_run_id_raises_error(tmp_path: Path) -> None:
    """T1.5 — missing run_id raises TemporalActivityRuntimeError."""
    store = _make_store(tmp_path)
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.agent_runtime_status({"agent_id": "codex_cli"})


# ---------------------------------------------------------------------------
# T2: agent_runtime_cancel — typed AgentRunStatus return (not None)
# ---------------------------------------------------------------------------


async def test_cancel_with_supervisor_returns_typed_status(tmp_path: Path) -> None:
    """T2.1 — cancel with supervisor returns AgentRunStatus with status='canceled'."""
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock()

    activities = TemporalAgentRuntimeActivities(
        run_supervisor=mock_supervisor,
    )
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-x"})

    assert isinstance(result, AgentRunStatus), f"Expected AgentRunStatus, got {type(result)}"
    assert result.status == "canceled"
    assert result.agent_kind == "managed"


async def test_cancel_supervisor_exception_still_returns_typed_status(tmp_path: Path) -> None:
    """T2.2 — supervisor.cancel raising an exception still yields AgentRunStatus."""
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock(side_effect=RuntimeError("supervisor failed"))

    activities = TemporalAgentRuntimeActivities(
        run_supervisor=mock_supervisor,
    )
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-y"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


async def test_cancel_no_supervisor_store_path_returns_typed_status(tmp_path: Path) -> None:
    """T2.3 — no supervisor but store update still returns AgentRunStatus."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-cancel-store", status="running")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-cancel-store"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


async def test_cancel_external_kind_returns_typed_status(tmp_path: Path) -> None:
    """T2.4 — external/unknown kind path still returns AgentRunStatus (best-effort)."""
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_cancel({"agent_kind": "external", "run_id": "ext-run"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


# ---------------------------------------------------------------------------
# T3: agent_runtime_publish_artifacts — typed AgentRunResult return
# ---------------------------------------------------------------------------


async def test_publish_artifacts_no_service_returns_result_unchanged() -> None:
    """T3.1 — no artifact service configured → passthrough (returns input model)."""
    original = AgentRunResult(summary="done", failure_class=None)
    activities = TemporalAgentRuntimeActivities()  # no artifact_service

    result = await activities.agent_runtime_publish_artifacts(original)

    assert isinstance(result, AgentRunResult)
    assert result.summary == "done"


async def test_publish_artifacts_none_input_returns_none() -> None:
    """T3.3 — None input returns None."""
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_publish_artifacts(None)
    assert result is None


async def test_publish_artifacts_stamps_step_metadata_when_context_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_metadata: list[dict[str, object] | None] = []

    async def fake_write_json_artifact(
        _service: object,
        *,
        principal: str,
        payload: object,
        execution_ref: object = None,
        metadata_json: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        del principal, payload, execution_ref
        captured_metadata.append(metadata_json)
        return SimpleNamespace(artifact_id=f"art_{len(captured_metadata)}")

    monkeypatch.setattr(
        activity_runtime_module,
        "_write_json_artifact",
        fake_write_json_artifact,
    )

    activities = TemporalAgentRuntimeActivities(artifact_service=object())
    result = await activities.agent_runtime_publish_artifacts(
        AgentRunResult(
            summary="done",
            metadata={
                "moonmind": {
                    "stepLedger": {
                        "logicalStepId": "delegate-agent",
                        "attempt": 2,
                        "scope": "step",
                    }
                }
            },
        )
    )

    assert isinstance(result, AgentRunResult)
    assert len(captured_metadata) == 2
    assert captured_metadata[0]["step_id"] == "delegate-agent"
    assert captured_metadata[0]["attempt"] == 2
    assert captured_metadata[0]["scope"] == "step"
    assert captured_metadata[1]["step_id"] == "delegate-agent"


async def test_fetch_result_exposes_task_run_and_runtime_artifact_metadata(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.save(
        ManagedRunRecord(
            runId="550e8400-e29b-41d4-a716-446655440000",
            workflowId="wf-parent-1",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(tz=UTC),
            stdoutArtifactRef="art_stdout_1",
            stderrArtifactRef="art_stderr_1",
            mergedLogArtifactRef="art_merged_1",
            diagnosticsRef="art_diag_1",
        )
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result(
        {"run_id": "550e8400-e29b-41d4-a716-446655440000", "agent_id": "codex_cli"}
    )

    assert isinstance(result, AgentRunResult)
    assert result.metadata["taskRunId"] == "550e8400-e29b-41d4-a716-446655440000"
    assert result.metadata["stdoutArtifactRef"] == "art_stdout_1"
    assert result.metadata["stderrArtifactRef"] == "art_stderr_1"
    assert result.metadata["mergedLogArtifactRef"] == "art_merged_1"
    assert result.metadata["diagnosticsRef"] == "art_diag_1"


# ---------------------------------------------------------------------------
# T4: session-oriented agent_runtime activities — typed managed-session returns
# ---------------------------------------------------------------------------


async def test_launch_session_requires_session_controller() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="session_controller is required for agent_runtime.launch_session",
    ):
        await activities.agent_runtime_launch_session(
            {
                "taskRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": "/work/task/repo",
                "sessionWorkspacePath": "/work/task/session",
                "artifactSpoolPath": "/work/task/artifacts",
                "codexHomePath": "/work/task/codex-home",
                "imageRef": "moonmind:latest",
            }
        )


async def test_launch_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_launch_session(
        {
            "taskRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.session_state.container_id == "ctr-1"
    controller.launch_session.assert_awaited_once()


async def test_load_session_snapshot_queries_session_workflow_via_client_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_handle = AsyncMock()
    workflow_handle.query = AsyncMock(
        return_value=CodexManagedSessionSnapshot(
            binding=CodexManagedSessionBinding(
                workflowId="wf-task-1:session:codex_cli",
                taskRunId="wf-task-1",
                sessionId="sess:wf-task-1:codex_cli",
                sessionEpoch=1,
                runtimeId="codex_cli",
                executionProfileRef="codex-default",
            ),
            status="active",
            containerId="ctr-1",
            threadId="thread-1",
            activeTurnId=None,
            terminationRequested=False,
        ).model_dump(mode="json", by_alias=True)
    )
    created_adapters: list[object] = []

    class _FakeTemporalClientAdapter:
        def __init__(self) -> None:
            created_adapters.append(self)

        async def get_workflow_handle(self, workflow_id: str) -> AsyncMock:
            assert workflow_id == "wf-task-1:session:codex_cli"
            return workflow_handle

    monkeypatch.setattr(
        temporal_client_module,
        "TemporalClientAdapter",
        _FakeTemporalClientAdapter,
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_load_session_snapshot(
        {
            "workflowId": "wf-task-1:session:codex_cli",
            "taskRunId": "wf-task-1",
            "sessionId": "sess:wf-task-1:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "codex-default",
        }
    )

    assert isinstance(result, CodexManagedSessionSnapshot)
    assert result.binding.workflow_id == "wf-task-1:session:codex_cli"
    assert result.container_id == "ctr-1"
    assert len(created_adapters) == 1
    workflow_handle.query.assert_awaited_once_with("get_status")


async def test_load_session_snapshot_reuses_client_adapter_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_handle = AsyncMock()
    workflow_handle.query = AsyncMock(
        return_value=CodexManagedSessionSnapshot(
            binding=CodexManagedSessionBinding(
                workflowId="wf-task-1:session:codex_cli",
                taskRunId="wf-task-1",
                sessionId="sess:wf-task-1:codex_cli",
                sessionEpoch=1,
                runtimeId="codex_cli",
                executionProfileRef="codex-default",
            ),
            status="active",
            containerId="ctr-1",
            threadId="thread-1",
            activeTurnId=None,
            terminationRequested=False,
        ).model_dump(mode="json", by_alias=True)
    )
    created_adapters: list[object] = []

    class _FakeTemporalClientAdapter:
        def __init__(self) -> None:
            created_adapters.append(self)

        async def get_workflow_handle(self, workflow_id: str) -> AsyncMock:
            assert workflow_id == "wf-task-1:session:codex_cli"
            return workflow_handle

    monkeypatch.setattr(
        temporal_client_module,
        "TemporalClientAdapter",
        _FakeTemporalClientAdapter,
    )

    activities = TemporalAgentRuntimeActivities()
    payload = {
        "workflowId": "wf-task-1:session:codex_cli",
        "taskRunId": "wf-task-1",
        "sessionId": "sess:wf-task-1:codex_cli",
        "sessionEpoch": 1,
        "runtimeId": "codex_cli",
        "executionProfileRef": "codex-default",
    }

    await activities.agent_runtime_load_session_snapshot(payload)
    await activities.agent_runtime_load_session_snapshot(payload)

    assert len(created_adapters) == 1
    assert workflow_handle.query.await_count == 2


async def test_session_status_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.session_status = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="busy",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_session_status(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.status == "busy"


async def test_send_turn_accepts_base_model_payloads_and_preserves_concrete_type() -> None:
    class _SendTurnEnvelope(BaseModel):
        session_id: str
        session_epoch: int
        container_id: str
        thread_id: str
        instructions: str

    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_send_turn(
        _SendTurnEnvelope(
            session_id="sess-1",
            session_epoch=1,
            container_id="ctr-1",
            thread_id="thread-1",
            instructions="Inspect the workspace",
        )
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    validated_request = controller.send_turn.await_args.args[0]
    assert validated_request.__class__.__name__ == "SendCodexManagedSessionTurnRequest"
    assert validated_request.instructions == "Inspect the workspace"
    assert result.turn_id == "turn-1"


async def test_send_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_send_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "instructions": "Inspect the workspace",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.turn_id == "turn-1"


async def test_steer_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.steer_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_steer_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "turnId": "turn-1",
            "instructions": "Focus on the failing test",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.status == "running"


async def test_interrupt_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.interrupt_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            turnId="turn-1",
            status="interrupted",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_interrupt_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "turnId": "turn-1",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.status == "interrupted"


async def test_clear_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.clear_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            status="ready",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_clear_session(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "newThreadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.session_state.session_epoch == 2


async def test_terminate_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.terminate_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            status="terminated",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_terminate_session(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.status == "terminated"


async def test_fetch_session_summary_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.fetch_session_summary = AsyncMock(
        return_value=CodexManagedSessionSummary(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            latestSummaryRef="art-summary",
            latestCheckpointRef="art-checkpoint",
            latestControlEventRef="art-control",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_fetch_session_summary(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionSummary)
    assert result.latest_summary_ref == "art-summary"
    assert result.latest_checkpoint_ref == "art-checkpoint"
    assert result.latest_control_event_ref == "art-control"


async def test_publish_session_artifacts_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.publish_session_artifacts = AsyncMock(
        return_value=CodexManagedSessionArtifactsPublication(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            publishedArtifactRefs=("art-summary", "art-checkpoint"),
            latestSummaryRef="art-summary",
            latestCheckpointRef="art-checkpoint",
            latestControlEventRef="art-control",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_publish_session_artifacts(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionArtifactsPublication)
    assert result.published_artifact_refs == ("art-summary", "art-checkpoint")
    assert result.latest_checkpoint_ref == "art-checkpoint"
    assert result.latest_control_event_ref == "art-control"


# ---------------------------------------------------------------------------
# T5: agent_runtime_fetch_result — typed AgentRunResult return
# ---------------------------------------------------------------------------


async def test_fetch_result_completed_returns_typed_model(tmp_path: Path) -> None:
    """T5.1 — completed run returns typed AgentRunResult with failure_class=None."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-1", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "fr-1"})

    assert isinstance(result, AgentRunResult), f"Expected AgentRunResult, got {type(result)}"
    assert result.failure_class is None


async def test_fetch_result_failed_returns_typed_model(tmp_path: Path) -> None:
    """T5.2 — failed run returns typed AgentRunResult with correct failure_class."""
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="fr-2",
        status="failed",
        failure_class="execution_error",
        error_message="Process exited with code 1",
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "fr-2"})

    assert isinstance(result, AgentRunResult)
    assert result.failure_class == "execution_error"


async def test_fetch_result_forwards_pr_resolver_expected_flag(tmp_path: Path) -> None:
    """T5.3 — pr-resolver expectation reaches the managed adapter."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-pr", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary="blocked",
                failure_class="user_error",
            )
        )

        result = await activities.agent_runtime_fetch_result(
            {"run_id": "fr-pr", "pr_resolver_expected": True}
        )

        adapter.fetch_result.assert_awaited_once_with(
            "fr-pr", pr_resolver_expected=True
        )
        assert result.failure_class == "user_error"


async def test_fetch_result_string_request_defaults_pr_resolver_expected_false(
    tmp_path: Path,
) -> None:
    """T5.4 — string request path must not call mapping-only accessors."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-string", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(return_value=AgentRunResult(summary="ok"))

        result = await activities.agent_runtime_fetch_result("fr-string")

        adapter.fetch_result.assert_awaited_once_with(
            "fr-string", pr_resolver_expected=False
        )
        assert result.summary == "ok"


async def test_fetch_result_no_record_returns_empty_typed_model(tmp_path: Path) -> None:
    """T5.5 — no record in store returns empty AgentRunResult (not None, not dict)."""
    store = _make_store(tmp_path)

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "no-such"})

    assert isinstance(result, AgentRunResult)


async def test_fetch_result_missing_run_id_raises_error(tmp_path: Path) -> None:
    """T5.6 — missing run_id raises TemporalActivityRuntimeError."""
    store = _make_store(tmp_path)
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.agent_runtime_fetch_result({"agent_id": "codex_cli"})


# ---------------------------------------------------------------------------
# Boundary & Serialization tests
# ---------------------------------------------------------------------------

from datetime import timedelta
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

@workflow.defn(name="AgentRuntimeStatusBoundaryTest")
class AgentRuntimeStatusBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunStatus:
        return await workflow.execute_activity(
            "agent_runtime.status",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeFetchResultBoundaryTest")
class AgentRuntimeFetchResultBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunResult:
        return await workflow.execute_activity(
            "agent_runtime.fetch_result",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )


@workflow.defn(name="AgentRuntimeBuildLaunchContextBoundaryTest")
class AgentRuntimeBuildLaunchContextBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> dict[str, Any]:
        return await workflow.execute_activity(
            "agent_runtime.build_launch_context",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeCancelBoundaryTest")
class AgentRuntimeCancelBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunStatus:
        return await workflow.execute_activity(
            "agent_runtime.cancel",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimePublishArtifactsBoundaryTest")
class AgentRuntimePublishArtifactsBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunResult | None:
        return await workflow.execute_activity(
            "agent_runtime.publish_artifacts",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

async def test_agent_runtime_status_temporal_boundary(tmp_path: Path) -> None:
    """Validate Temporal boundary serialization for typed Pydantic return matches contract."""
    from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog

    store = _make_store(tmp_path)
    _save_record(store, run_id="boundary-1", status="completed")

    activities_impl = TemporalAgentRuntimeActivities(run_store=store)
    from temporalio import activity

    @activity.defn(name="agent_runtime.status")
    async def _agent_runtime_status_wrapper(request: dict) -> AgentRunStatus:
        return await activities_impl.agent_runtime_status(request)

    handlers = [_agent_runtime_status_wrapper]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue",
            workflows=[AgentRuntimeStatusBoundaryTest],
            activities=handlers,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeStatusBoundaryTest.run,
                {"run_id": "boundary-1", "agent_id": "codex_cli"},
                id="boundary-test-status",
                task_queue="boundary-test-queue",
            )

            assert isinstance(result, AgentRunStatus)
            assert result.status == "completed"


@pytest.mark.asyncio
async def test_agent_runtime_build_launch_context_temporal_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test_token")
    monkeypatch.setenv("MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION", "1")
    activities_impl = TemporalAgentRuntimeActivities()
    from temporalio import activity

    @activity.defn(name="agent_runtime.build_launch_context")
    async def _agent_runtime_build_launch_context_wrapper(
        request: dict,
    ) -> dict[str, Any]:
        return await activities_impl.agent_runtime_build_launch_context(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-build-launch-context",
            workflows=[AgentRuntimeBuildLaunchContextBoundaryTest],
            activities=[_agent_runtime_build_launch_context_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeBuildLaunchContextBoundaryTest.run,
                {
                    "profile": {
                        "profile_id": "proxy-prof",
                        "credential_source": "secret_ref",
                        "tags": ["proxy-first"],
                        "provider_id": "anthropic",
                        "secret_refs": {"anthropic_api_key": "db://123"},
                    },
                    "runtime_for_profile": "claude_code",
                    "workflow_id": "wf-boundary",
                    "default_credential_source": "secret_ref",
                },
                id="boundary-test-build-launch-context",
                task_queue="boundary-test-queue-build-launch-context",
            )

            assert result["profile_id"] == "proxy-prof"
            assert "MOONMIND_PROXY_TOKEN" in result["delta_env_overrides"]
            assert "GITHUB_TOKEN" in result["passthrough_env_keys"]


@pytest.mark.asyncio
async def test_agent_runtime_fetch_result_temporal_boundary(tmp_path: Path) -> None:
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="boundary-1", status="completed")

    activities_impl = TemporalAgentRuntimeActivities(run_store=store)
    from temporalio import activity

    @activity.defn(name="agent_runtime.fetch_result")
    async def _agent_runtime_fetch_wrapper(request: dict) -> AgentRunResult:
        res = await activities_impl.agent_runtime_fetch_result(request)
        if hasattr(res, "model_copy"):
            return res.model_copy()
        return res

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-fetch",
            workflows=[AgentRuntimeFetchResultBoundaryTest],
            activities=[_agent_runtime_fetch_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            with patch("moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter", autospec=True) as MockAdapter:
                instance = MockAdapter.return_value
                instance.fetch_result = AsyncMock(return_value=AgentRunResult(summary="ok", failure_class=None))

                result = await env.client.execute_workflow(
                    AgentRuntimeFetchResultBoundaryTest.run,
                    {
                        "run_id": "boundary-1",
                        "agent_id": "claude",
                        "pr_resolver_expected": True,
                    },
                    id="boundary-test-fetch",
                    task_queue="boundary-test-queue-fetch",
                )

                assert isinstance(result, AgentRunResult)
                assert result.summary == "ok"
                instance.fetch_result.assert_awaited_once_with(
                    "boundary-1", pr_resolver_expected=True
                )


@pytest.mark.asyncio
async def test_agent_runtime_cancel_temporal_boundary() -> None:
    from unittest.mock import MagicMock
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock()
    activities_impl = TemporalAgentRuntimeActivities(
        run_store=MagicMock(),
        run_supervisor=mock_supervisor,
    )
    from temporalio import activity

    @activity.defn(name="agent_runtime.cancel")
    async def _agent_runtime_cancel_wrapper(request: dict) -> AgentRunStatus:
        return await activities_impl.agent_runtime_cancel(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-cancel",
            workflows=[AgentRuntimeCancelBoundaryTest],
            activities=[_agent_runtime_cancel_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeCancelBoundaryTest.run,
                {"run_id": "c-1", "agent_id": "c"},
                id="boundary-test-cancel",
                task_queue="boundary-test-queue-cancel",
            )

            assert isinstance(result, AgentRunStatus)
            assert result.status == "canceled"


@pytest.mark.asyncio
async def test_agent_runtime_publish_temporal_boundary() -> None:
    from unittest.mock import MagicMock
    activities_impl = TemporalAgentRuntimeActivities(run_store=MagicMock())
    from temporalio import activity

    @activity.defn(name="agent_runtime.publish_artifacts")
    async def _agent_runtime_publish_wrapper(request: dict) -> AgentRunResult | None:
        return await activities_impl.agent_runtime_publish_artifacts(None)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-pub",
            workflows=[AgentRuntimePublishArtifactsBoundaryTest],
            activities=[_agent_runtime_publish_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimePublishArtifactsBoundaryTest.run,
                {},
                id="boundary-test-pub",
                task_queue="boundary-test-queue-pub",
            )

            assert result is None


@workflow.defn(name="AgentRuntimeLaunchSessionBoundaryTest")
class AgentRuntimeLaunchSessionBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> CodexManagedSessionHandle:
        return await workflow.execute_activity(
            "agent_runtime.launch_session",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )


@workflow.defn(name="AgentRuntimeSendTurnBoundaryTest")
class AgentRuntimeSendTurnBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> CodexManagedSessionTurnResponse:
        return await workflow.execute_activity(
            "agent_runtime.send_turn",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_agent_runtime_launch_session_temporal_boundary() -> None:
    from temporalio import activity

    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-boundary",
                "sessionEpoch": 1,
                "containerId": "ctr-boundary",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities_impl = TemporalAgentRuntimeActivities(session_controller=controller)

    @activity.defn(name="agent_runtime.launch_session")
    async def _agent_runtime_launch_session_wrapper(
        request: dict,
    ) -> CodexManagedSessionHandle:
        return await activities_impl.agent_runtime_launch_session(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-launch-session",
            workflows=[AgentRuntimeLaunchSessionBoundaryTest],
            activities=[_agent_runtime_launch_session_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeLaunchSessionBoundaryTest.run,
                {
                    "taskRunId": "task-1",
                    "sessionId": "sess-boundary",
                    "threadId": "thread-1",
                    "workspacePath": "/work/task/repo",
                    "sessionWorkspacePath": "/work/task/session",
                    "artifactSpoolPath": "/work/task/artifacts",
                    "codexHomePath": "/work/task/codex-home",
                    "imageRef": "moonmind:latest",
                },
                id="boundary-test-launch-session",
                task_queue="boundary-test-queue-launch-session",
            )

            assert isinstance(result, CodexManagedSessionHandle)
            assert result.session_state.container_id == "ctr-boundary"


@pytest.mark.asyncio
async def test_agent_runtime_send_turn_temporal_boundary() -> None:
    from temporalio import activity

    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-boundary",
                "sessionEpoch": 1,
                "containerId": "ctr-boundary",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities_impl = TemporalAgentRuntimeActivities(session_controller=controller)

    @activity.defn(name="agent_runtime.send_turn")
    async def _agent_runtime_send_turn_wrapper(
        request: dict,
    ) -> CodexManagedSessionTurnResponse:
        return await activities_impl.agent_runtime_send_turn(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-send-turn",
            workflows=[AgentRuntimeSendTurnBoundaryTest],
            activities=[_agent_runtime_send_turn_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeSendTurnBoundaryTest.run,
                {
                    "sessionId": "sess-boundary",
                    "sessionEpoch": 1,
                    "containerId": "ctr-boundary",
                    "threadId": "thread-1",
                    "instructions": "Inspect the repo state",
                },
                id="boundary-test-send-turn",
                task_queue="boundary-test-queue-send-turn",
            )

            assert isinstance(result, CodexManagedSessionTurnResponse)
            assert result.turn_id == "turn-1"
