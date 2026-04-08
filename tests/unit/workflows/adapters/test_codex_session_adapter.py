from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionHandle,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
)
from moonmind.workflows.adapters.codex_session_adapter import CodexSessionAdapter
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


pytestmark = [pytest.mark.asyncio]


def _fake_profiles(profiles: list[dict[str, Any]]):
    async def _fetcher(*, runtime_id: str):
        return {"profiles": profiles}

    return _fetcher


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _binding() -> CodexManagedSessionBinding:
    return CodexManagedSessionBinding(
        workflowId="wf-task-1:session:codex_cli",
        taskRunId="wf-task-1",
        sessionId="sess:wf-task-1:codex_cli",
        sessionEpoch=1,
        runtimeId="codex_cli",
        executionProfileRef="codex-default",
    )


def _snapshot(
    *,
    binding: CodexManagedSessionBinding,
    container_id: str | None = None,
    thread_id: str | None = None,
    active_turn_id: str | None = None,
    session_epoch: int | None = None,
) -> CodexManagedSessionSnapshot:
    effective_binding = (
        binding.model_copy(update={"session_epoch": session_epoch})
        if session_epoch is not None
        else binding
    )
    return CodexManagedSessionSnapshot(
        binding=effective_binding,
        status="active",
        containerId=container_id,
        threadId=thread_id,
        activeTurnId=active_turn_id,
        terminationRequested=False,
    )


def _request(
    binding: CodexManagedSessionBinding,
    *,
    workspace_path: str | None = None,
) -> AgentExecutionRequest:
    workspace_spec = {}
    if workspace_path is not None:
        workspace_spec["workspacePath"] = workspace_path
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="artifact:instructions",
        managedSession=binding,
        inputRefs=["artifact:input-1"],
        workspaceSpec=workspace_spec,
        parameters={"publishMode": "none"},
    )


def _session_handle(
    *,
    session_id: str,
    session_epoch: int,
    container_id: str,
    thread_id: str,
    status: str = "ready",
) -> CodexManagedSessionHandle:
    return CodexManagedSessionHandle(
        sessionState={
            "sessionId": session_id,
            "sessionEpoch": session_epoch,
            "containerId": container_id,
            "threadId": thread_id,
            "activeTurnId": None,
        },
        status=status,
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        controlUrl=f"docker-exec://{container_id}",
    )


def _turn_response(
    *,
    session_id: str,
    session_epoch: int,
    container_id: str,
    thread_id: str,
    turn_id: str = "turn-1",
    status: str = "completed",
    assistant_text: str = "Implemented through the session container.",
) -> CodexManagedSessionTurnResponse:
    return CodexManagedSessionTurnResponse(
        sessionState={
            "sessionId": session_id,
            "sessionEpoch": session_epoch,
            "containerId": container_id,
            "threadId": thread_id,
            "activeTurnId": None,
        },
        turnId=turn_id,
        status=status,
        outputRefs=("artifact:turn-output",),
        metadata={"assistantText": assistant_text},
    )


def _summary(
    *,
    session_id: str,
    session_epoch: int,
    container_id: str,
    thread_id: str,
) -> CodexManagedSessionSummary:
    return CodexManagedSessionSummary(
        sessionState={
            "sessionId": session_id,
            "sessionEpoch": session_epoch,
            "containerId": container_id,
            "threadId": thread_id,
            "activeTurnId": None,
        },
        latestSummaryRef="artifact:session-summary",
        latestCheckpointRef="artifact:session-checkpoint",
        latestControlEventRef=None,
        latestResetBoundaryRef=None,
        metadata={"lastAssistantText": "Implemented through the session container."},
    )


def _publication(
    *,
    session_id: str,
    session_epoch: int,
    container_id: str,
    thread_id: str,
) -> CodexManagedSessionArtifactsPublication:
    return CodexManagedSessionArtifactsPublication(
        sessionState={
            "sessionId": session_id,
            "sessionEpoch": session_epoch,
            "containerId": container_id,
            "threadId": thread_id,
            "activeTurnId": None,
        },
        publishedArtifactRefs=(
            "artifact:stdout",
            "artifact:stderr",
            "artifact:diagnostics",
            "artifact:session-summary",
            "artifact:session-checkpoint",
        ),
        latestSummaryRef="artifact:session-summary",
        latestCheckpointRef="artifact:session-checkpoint",
        latestControlEventRef=None,
        latestResetBoundaryRef=None,
    )


async def test_start_launches_missing_task_scoped_session_and_persists_result(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.task_run_id / "repo"
    launch_calls: list[Any] = []
    attach_calls: list[dict[str, Any]] = []
    control_calls: list[dict[str, Any]] = []
    send_turn_calls: list[Any] = []

    async def _load_snapshot(workflow_id: str) -> CodexManagedSessionSnapshot:
        assert workflow_id == binding.workflow_id
        return _snapshot(binding=binding)

    async def _launch_session(request: Any) -> CodexManagedSessionHandle:
        launch_calls.append(request)
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        raise AssertionError("session_status should not be used before launch")

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _publish_artifacts(_request: Any) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _attach_runtime_handles(payload: dict[str, Any]) -> None:
        attach_calls.append(payload)

    async def _apply_control_action(payload: dict[str, Any]) -> None:
        control_calls.append(payload)

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "oauth_volume"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=run_store,
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=_session_status,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_fetch_summary,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_attach_runtime_handles,
        apply_session_control_action=_apply_control_action,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    handle = await adapter.start(_request(binding, workspace_path=str(workspace_path)))
    status = await adapter.status(handle.run_id)
    result = await adapter.fetch_result(handle.run_id)
    persisted_record = run_store.load(binding.task_run_id)

    assert len(launch_calls) == 1
    launch_request = launch_calls[0]
    assert launch_request.session_id == binding.session_id
    assert launch_request.workspace_path == str(workspace_path)
    assert launch_request.session_workspace_path.endswith(f"{binding.task_run_id}/session")
    assert launch_request.artifact_spool_path.endswith(f"{binding.task_run_id}/artifacts")
    assert launch_request.codex_home_path.endswith(f"{binding.task_run_id}/.moonmind/codex-home")
    assert launch_request.image_ref == "ghcr.io/moonladderstudios/moonmind:latest"
    assert launch_request.workspace_spec == {"workspacePath": str(workspace_path)}
    assert send_turn_calls[0].instructions.startswith("artifact:instructions")
    assert "Managed Codex CLI note:" in send_turn_calls[0].instructions
    assert send_turn_calls[0].input_refs == ("artifact:input-1",)

    assert handle.status == "completed"
    assert handle.metadata["sessionId"] == binding.session_id
    assert handle.metadata["containerId"] == "container-1"
    assert persisted_record is not None
    assert persisted_record.run_id == binding.task_run_id
    assert persisted_record.workflow_id == "wf-agent-run-1"
    assert persisted_record.runtime_id == "codex_cli"
    assert persisted_record.status == "completed"
    assert persisted_record.stdout_artifact_ref == "artifact:stdout"
    assert persisted_record.stderr_artifact_ref == "artifact:stderr"
    assert persisted_record.diagnostics_ref == "artifact:diagnostics"
    assert persisted_record.live_stream_capable is False
    assert status.status == "completed"
    assert result.summary == "Implemented through the session container."
    assert result.output_refs == [
        "artifact:turn-output",
        "artifact:stdout",
        "artifact:stderr",
        "artifact:diagnostics",
        "artifact:session-summary",
        "artifact:session-checkpoint",
    ]
    assert result.metadata["instructionRef"] == "artifact:instructions"
    assert result.metadata["sessionSummary"]["latestSummaryRef"] == "artifact:session-summary"
    assert result.metadata["sessionArtifacts"]["latestCheckpointRef"] == "artifact:session-checkpoint"

    assert attach_calls == [{"containerId": "container-1", "threadId": "thread-1"}]
    assert control_calls[-1]["action"] == "send_turn"
    assert control_calls[-1]["containerId"] == "container-1"
    assert control_calls[-1]["threadId"] == "thread-1"


async def test_start_reuses_existing_task_scoped_session_without_launching(
    tmp_path: Path,
) -> None:
    binding = _binding()
    session_status_calls: list[Any] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-existing",
            thread_id="thread-existing",
        )

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        raise AssertionError("launch_session should not be called for a ready session")

    async def _session_status(request: Any) -> CodexManagedSessionHandle:
        session_status_calls.append(request)
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-existing",
            thread_id="thread-existing",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-existing",
            thread_id="thread-existing",
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-existing",
            thread_id="thread-existing",
        )

    async def _publish_artifacts(_request: Any) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-existing",
            thread_id="thread-existing",
        )

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "secret_ref"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=_session_status,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_fetch_summary,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    handle = await adapter.start(_request(binding))

    assert handle.metadata["containerId"] == "container-existing"
    assert len(session_status_calls) == 1
    assert session_status_calls[0].container_id == "container-existing"
    assert session_status_calls[0].thread_id == "thread-existing"


async def test_clear_session_rotates_epoch_and_signals_session_workflow(
    tmp_path: Path,
) -> None:
    binding = _binding()
    attach_calls: list[dict[str, Any]] = []
    control_calls: list[dict[str, Any]] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _clear_remote_session(request: Any) -> CodexManagedSessionHandle:
        assert request.new_thread_id == "thread-2"
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id="thread-2",
        )

    async def _attach_runtime_handles(payload: dict[str, Any]) -> None:
        attach_calls.append(payload)

    async def _apply_control_action(payload: dict[str, Any]) -> None:
        control_calls.append(payload)

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "secret_ref"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_async_noop,
        session_status=_async_noop,
        send_turn=_async_noop,
        interrupt_turn=_async_noop,
        clear_remote_session=_clear_remote_session,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_async_noop,
        attach_runtime_handles=_attach_runtime_handles,
        apply_session_control_action=_apply_control_action,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    handle = await adapter.clear_session(binding=binding, new_thread_id="thread-2", reason="reset")

    assert handle.session_state.session_epoch == 2
    assert handle.session_state.thread_id == "thread-2"
    assert attach_calls == [{"containerId": "container-1", "threadId": "thread-2"}]
    assert control_calls == [
        {
            "action": "clear_session",
            "reason": "reset",
            "containerId": "container-1",
            "threadId": "thread-2",
        }
    ]


async def test_cancel_interrupts_active_turn_and_marks_run_canceled(
    tmp_path: Path,
) -> None:
    binding = _binding()
    interrupt_calls: list[Any] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
            active_turn_id="turn-active",
        )

    async def _interrupt_turn(request: Any) -> CodexManagedSessionTurnResponse:
        interrupt_calls.append(request)
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            turn_id="turn-active",
            status="interrupted",
            assistant_text="Interrupted.",
        )

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "secret_ref"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_async_noop,
        session_status=_async_noop,
        send_turn=_async_noop,
        interrupt_turn=_interrupt_turn,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_async_noop,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    run_id = "run-cancel-1"
    adapter._save_run_state(
        run_id=run_id,
        agent_id="codex",
        locator={
            "sessionId": binding.session_id,
            "sessionEpoch": binding.session_epoch,
            "containerId": "container-1",
            "threadId": "thread-1",
        },
        active_turn_id="turn-active",
        result={
            "summary": "Still running",
            "metadata": {},
        },
        status="running",
        started_at=datetime.now(tz=UTC),
    )

    status = await adapter.cancel(run_id)
    result = await adapter.fetch_result(run_id)

    assert interrupt_calls[0].turn_id == "turn-active"
    assert status.status == "canceled"
    assert result.failure_class == "user_error"
    assert result.summary == "Canceled Codex managed-session turn."


async def test_terminate_session_uses_remote_session_control_surface(
    tmp_path: Path,
) -> None:
    binding = _binding()
    terminate_calls: list[Any] = []
    control_calls: list[dict[str, Any]] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _terminate_remote_session(request: Any) -> CodexManagedSessionHandle:
        terminate_calls.append(request)
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            status="terminated",
        )

    async def _apply_control_action(payload: dict[str, Any]) -> None:
        control_calls.append(payload)

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "secret_ref"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_async_noop,
        session_status=_async_noop,
        send_turn=_async_noop,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_terminate_remote_session,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_async_noop,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_apply_control_action,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    handle = await adapter.terminate_session(binding=binding, reason="task-complete")

    assert terminate_calls[0].container_id == "container-1"
    assert handle.status == "terminated"
    assert control_calls == [{"action": "terminate_session", "reason": "task-complete"}]
