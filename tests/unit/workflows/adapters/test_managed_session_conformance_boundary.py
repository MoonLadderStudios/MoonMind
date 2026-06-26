"""Adapter-boundary conformance tests for the managed-session suite (MM-883).

These tests drive the real :class:`CodexSessionAdapter` through the managed-session
lifecycle and assert that the invocation request shapes and trace/artifact
correlation surfaces match the capability descriptor the adapter advertises to the
shared conformance suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionClearRequest,
    CodexManagedSessionHandle,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    SendCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)
from moonmind.workflows.adapters.codex_session_adapter import CodexSessionAdapter
from moonmind.workflows.temporal.managed_session_conformance import (
    evaluate_managed_session_conformance,
)

pytestmark = [pytest.mark.asyncio]


def _fake_profiles(profiles: list[dict[str, Any]]):
    async def _fetcher(*, runtime_id: str):
        return {"profiles": profiles}

    return _fetcher


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    return None


async def _prepare_turn_instructions(payload: dict[str, Any]) -> str:
    request = payload.get("request") if isinstance(payload, dict) else {}
    instruction_ref = ""
    if isinstance(request, dict):
        instruction_ref = str(
            request.get("instructionRef") or request.get("instruction_ref") or ""
        ).strip()
    return f"{instruction_ref}\n\nManaged Codex CLI note:"


def _binding() -> CodexManagedSessionBinding:
    return CodexManagedSessionBinding(
        workflowId="wf-task-1:session:codex_cli",
        agentRunId="wf-task-1",
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
) -> CodexManagedSessionSnapshot:
    return CodexManagedSessionSnapshot(
        binding=binding,
        status="active",
        containerId=container_id,
        threadId=thread_id,
        activeTurnId=None,
        terminationRequested=False,
    )


def _request(
    binding: CodexManagedSessionBinding,
    *,
    workspace_path: str,
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="artifact:instructions",
        managedSession=binding,
        workspaceSpec={"workspacePath": workspace_path},
        parameters={"publishMode": "none"},
        timeoutPolicy={},
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
    binding: CodexManagedSessionBinding,
    container_id: str,
    thread_id: str,
    status: str = "completed",
) -> CodexManagedSessionTurnResponse:
    return CodexManagedSessionTurnResponse(
        sessionState={
            "sessionId": binding.session_id,
            "sessionEpoch": binding.session_epoch,
            "containerId": container_id,
            "threadId": thread_id,
            "activeTurnId": None,
        },
        turnId="turn-1",
        status=status,
        outputRefs=("artifact:turn-output",),
        metadata={"assistantText": "Implemented through the session container."},
    )


def _summary(
    *, binding: CodexManagedSessionBinding, container_id: str, thread_id: str
) -> CodexManagedSessionSummary:
    return CodexManagedSessionSummary(
        sessionState={
            "sessionId": binding.session_id,
            "sessionEpoch": binding.session_epoch,
            "containerId": container_id,
            "threadId": thread_id,
            "activeTurnId": None,
        },
        latestSummaryRef="artifact:session-summary",
        latestCheckpointRef="artifact:session-checkpoint",
        metadata={"lastAssistantText": "Implemented through the session container."},
    )


def _publication(
    *, binding: CodexManagedSessionBinding, container_id: str, thread_id: str
) -> CodexManagedSessionArtifactsPublication:
    return CodexManagedSessionArtifactsPublication(
        sessionState={
            "sessionId": binding.session_id,
            "sessionEpoch": binding.session_epoch,
            "containerId": container_id,
            "threadId": thread_id,
            "activeTurnId": None,
        },
        publishedArtifactRefs=(
            "artifact:stdout",
            "artifact:stderr",
            "artifact:diagnostics",
            "artifact:observability.events.jsonl",
            "artifact:session-summary",
            "artifact:session-checkpoint",
        ),
        latestSummaryRef="artifact:session-summary",
        latestCheckpointRef="artifact:session-checkpoint",
    )


def _descriptor_invocation(behavior: str) -> str | None:
    support = CodexSessionAdapter.managed_session_capabilities().behavior(behavior)
    assert support is not None, behavior
    return support.invocation


async def test_launch_and_turn_boundary_shapes_and_correlation(tmp_path: Path) -> None:
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    launch_calls: list[Any] = []
    send_turn_calls: list[Any] = []
    control_calls: list[dict[str, Any]] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(request: Any) -> CodexManagedSessionHandle:
        launch_calls.append(request)
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        return _turn_response(
            binding=binding, container_id="container-1", thread_id="thread-1"
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(binding=binding, container_id="container-1", thread_id="thread-1")

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            binding=binding, container_id="container-1", thread_id="thread-1"
        )

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
        session_status=_async_noop,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_fetch_summary,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_apply_control_action,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    request = _request(binding, workspace_path=str(workspace_path))
    handle = await adapter.start(request)
    result = await adapter.fetch_result(handle.run_id)
    persisted_record = run_store.load(binding.agent_run_id)

    # --- launch boundary invocation shape ---
    assert len(launch_calls) == 1
    launch_request = LaunchCodexManagedSessionRequest.model_validate(
        launch_calls[0]["request"]
    )
    assert launch_request.session_id == binding.session_id
    assert _descriptor_invocation("launch") == "LaunchCodexManagedSessionRequest"

    # --- turn-control boundary invocation shape ---
    assert len(send_turn_calls) == 1
    assert isinstance(send_turn_calls[0], SendCodexManagedSessionTurnRequest)
    assert _descriptor_invocation("turn_control") == "SendCodexManagedSessionTurnRequest"

    # --- trace/artifact correlation ---
    assert request.correlation_id == "corr-1"
    assert handle.metadata["sessionId"] == binding.session_id
    assert handle.metadata["containerId"] == "container-1"
    assert handle.metadata["sessionEpoch"] == binding.session_epoch
    assert persisted_record is not None
    assert (
        persisted_record.observability_events_ref
        == "artifact:observability.events.jsonl"
    )
    # checkpoint <-> artifact correlation flows back into the run result.
    assert (
        result.metadata["sessionArtifacts"]["latestCheckpointRef"]
        == "artifact:session-checkpoint"
    )
    # control events correlate to the same session container/thread.
    start_event = next(c for c in control_calls if c["action"] == "start_session")
    send_event = next(c for c in control_calls if c["action"] == "send_turn")
    assert start_event["containerId"] == "container-1"
    assert send_event["containerId"] == "container-1"
    assert send_event["threadId"] == "thread-1"


def _control_adapter(
    *,
    binding: CodexManagedSessionBinding,
    load_snapshot,
    interrupt_turn=_async_noop,
    clear_remote_session=_async_noop,
    terminate_remote_session=_async_noop,
    apply_control_action=_async_noop,
    attach_runtime_handles=_async_noop,
) -> CodexSessionAdapter:
    return CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "oauth_volume"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=None,
        load_session_snapshot=load_snapshot,
        launch_session=_async_noop,
        session_status=_async_noop,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_async_noop,
        interrupt_turn=interrupt_turn,
        clear_remote_session=clear_remote_session,
        terminate_remote_session=terminate_remote_session,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_async_noop,
        attach_runtime_handles=attach_runtime_handles,
        apply_session_control_action=apply_control_action,
        workspace_root="/work/agent_jobs",
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )


async def test_interrupt_boundary_invocation_shape() -> None:
    binding = _binding()
    interrupt_calls: list[Any] = []
    control_calls: list[dict[str, Any]] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding, container_id="container-1", thread_id="thread-1")

    async def _interrupt_turn(request: Any) -> CodexManagedSessionTurnResponse:
        interrupt_calls.append(request)
        return _turn_response(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
            status="interrupted",
        )

    async def _apply_control_action(payload: dict[str, Any]) -> None:
        control_calls.append(payload)

    adapter = _control_adapter(
        binding=binding,
        load_snapshot=_load_snapshot,
        interrupt_turn=_interrupt_turn,
        apply_control_action=_apply_control_action,
    )

    response = await adapter.interrupt_turn(
        binding=binding, turn_id="turn-1", reason="operator interrupt"
    )

    assert len(interrupt_calls) == 1
    assert isinstance(interrupt_calls[0], InterruptCodexManagedSessionTurnRequest)
    assert interrupt_calls[0].turn_id == "turn-1"
    assert response.status == "interrupted"
    assert _descriptor_invocation("interrupt") == "InterruptCodexManagedSessionTurnRequest"
    assert any(c["action"] == "interrupt_turn" for c in control_calls)


async def test_reset_epoch_boundary_invocation_shape() -> None:
    binding = _binding()
    clear_calls: list[Any] = []
    control_calls: list[dict[str, Any]] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding, container_id="container-1", thread_id="thread-1")

    async def _clear_remote_session(request: Any) -> CodexManagedSessionHandle:
        clear_calls.append(request)
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch + 1,
            container_id="container-1",
            thread_id="thread-2",
        )

    async def _apply_control_action(payload: dict[str, Any]) -> None:
        control_calls.append(payload)

    adapter = _control_adapter(
        binding=binding,
        load_snapshot=_load_snapshot,
        clear_remote_session=_clear_remote_session,
        apply_control_action=_apply_control_action,
    )

    handle = await adapter.clear_session(
        binding=binding, new_thread_id="thread-2", reason="reset"
    )

    assert len(clear_calls) == 1
    assert isinstance(clear_calls[0], CodexManagedSessionClearRequest)
    assert clear_calls[0].new_thread_id == "thread-2"
    # reset advances to a new continuity interval (epoch increment, same container).
    assert handle.session_state.session_epoch == binding.session_epoch + 1
    assert handle.session_state.container_id == "container-1"
    assert handle.session_state.thread_id == "thread-2"
    assert _descriptor_invocation("reset_epoch") == "CodexManagedSessionClearRequest"
    assert any(c["action"] == "clear_session" for c in control_calls)


async def test_terminate_boundary_invocation_shape() -> None:
    binding = _binding()
    terminate_calls: list[Any] = []
    control_calls: list[dict[str, Any]] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding, container_id="container-1", thread_id="thread-1")

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

    adapter = _control_adapter(
        binding=binding,
        load_snapshot=_load_snapshot,
        terminate_remote_session=_terminate_remote_session,
        apply_control_action=_apply_control_action,
    )

    handle = await adapter.terminate_session(binding=binding, reason="run complete")

    assert len(terminate_calls) == 1
    assert isinstance(terminate_calls[0], TerminateCodexManagedSessionRequest)
    assert handle.status == "terminated"
    assert _descriptor_invocation("terminate") == "TerminateCodexManagedSessionRequest"
    assert any(c["action"] == "terminate_session" for c in control_calls)


async def test_adapter_capability_descriptor_runs_conformance_at_boundary() -> None:
    capabilities = CodexSessionAdapter.managed_session_capabilities()

    report = evaluate_managed_session_conformance(capabilities)

    assert capabilities.runtime_id == "codex_cli"
    assert report["sessionCapable"] is True
    assert report["capabilityGaps"] == []
