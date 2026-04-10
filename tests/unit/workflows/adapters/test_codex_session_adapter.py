from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
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
from moonmind.workflows.codex_session_timeouts import (
    DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS,
    MAX_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS,
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


async def _prepare_turn_instructions(payload: dict[str, Any]) -> str:
    request = payload.get("request") if isinstance(payload, dict) else {}
    instruction_ref = ""
    if isinstance(request, dict):
        instruction_ref = str(
            request.get("instructionRef") or request.get("instruction_ref") or ""
        ).strip()
        parameters = request.get("parameters")
        if not instruction_ref and isinstance(parameters, dict):
            inline = str(parameters.get("instructions") or "").strip()
            if inline:
                return f"{inline}\n\nManaged Codex CLI note:"
    return f"{instruction_ref}\n\nManaged Codex CLI note:"


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
    timeout_seconds: Any | None = None,
) -> AgentExecutionRequest:
    workspace_spec = {}
    if workspace_path is not None:
        workspace_spec["workspacePath"] = workspace_path
    timeout_policy: dict[str, Any] = {}
    if timeout_seconds is not None:
        timeout_policy["timeout_seconds"] = timeout_seconds
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="artifact:instructions",
        managedSession=binding,
        workspaceSpec=workspace_spec,
        parameters={"publishMode": "none"},
        timeoutPolicy=timeout_policy,
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
    last_assistant_text: str = "Implemented through the session container.",
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
        metadata={"lastAssistantText": last_assistant_text},
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
            "artifact:observability.events.jsonl",
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
        prepare_turn_instructions=_prepare_turn_instructions,
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
    launch_payload = launch_calls[0]
    assert isinstance(launch_payload, dict)
    assert launch_payload["profile"]["runtimeId"] == "codex_cli"
    assert launch_payload["profile"]["profileId"] == "codex-default"
    launch_request = launch_payload["request"]
    assert launch_request["sessionId"] == binding.session_id
    assert launch_request["workspacePath"] == str(workspace_path)
    assert launch_request["sessionWorkspacePath"].endswith(f"{binding.task_run_id}/session")
    assert launch_request["artifactSpoolPath"].endswith(f"{binding.task_run_id}/artifacts")
    assert launch_request["codexHomePath"].endswith(f"{binding.task_run_id}/.moonmind/codex-home")
    assert launch_request["imageRef"] == "ghcr.io/moonladderstudios/moonmind:latest"
    assert (
        launch_request["turnCompletionTimeoutSeconds"]
        == DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS
    )
    assert launch_request["workspaceSpec"] == {"workspacePath": str(workspace_path)}
    assert send_turn_calls[0].instructions.startswith("artifact:instructions")
    assert "Managed Codex CLI note:" in send_turn_calls[0].instructions

    assert handle.status == "completed"
    assert handle.metadata["sessionId"] == binding.session_id
    assert handle.metadata["containerId"] == "container-1"
    assert persisted_record is not None
    assert persisted_record.run_id == binding.task_run_id
    assert persisted_record.workflow_id == "wf-agent-run-1"
    assert persisted_record.runtime_id == "codex_cli"
    assert persisted_record.status == "completed"
    assert persisted_record.workspace_path == str(workspace_path)
    assert persisted_record.stdout_artifact_ref == "artifact:stdout"
    assert persisted_record.stderr_artifact_ref == "artifact:stderr"
    assert persisted_record.diagnostics_ref == "artifact:diagnostics"
    assert (
        persisted_record.observability_events_ref
        == "artifact:observability.events.jsonl"
    )
    assert persisted_record.live_stream_capable is True
    assert status.status == "completed"
    assert result.summary == "Implemented through the session container."
    assert result.output_refs == [
        "artifact:turn-output",
        "artifact:stdout",
        "artifact:stderr",
        "artifact:diagnostics",
        "artifact:observability.events.jsonl",
        "artifact:session-summary",
        "artifact:session-checkpoint",
    ]
    assert result.metadata["instructionRef"] == "artifact:instructions"
    assert result.metadata["sessionSummary"]["latestSummaryRef"] == "artifact:session-summary"
    assert result.metadata["sessionArtifacts"]["latestCheckpointRef"] == "artifact:session-checkpoint"

    assert attach_calls == [{"containerId": "container-1", "threadId": "thread-1"}]
    assert control_calls[0] == {
        "action": "start_session",
        "containerId": "container-1",
        "threadId": "thread-1",
    }
    assert control_calls[-1]["action"] == "send_turn"
    assert control_calls[-1]["containerId"] == "container-1"
    assert control_calls[-1]["threadId"] == "thread-1"


async def test_start_persists_running_live_capable_record_before_send_turn_completes(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.task_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    send_turn_started = asyncio.Event()
    release_send_turn = asyncio.Event()

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        raise AssertionError("session_status should not be used before launch")

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_started.set()
        await release_send_turn.wait()
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

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
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(
            return_value=_summary(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        publish_remote_artifacts=AsyncMock(
            return_value=_publication(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    start_task = asyncio.create_task(
        adapter.start(_request(binding, workspace_path=str(workspace_path)))
    )
    await asyncio.wait_for(send_turn_started.wait(), timeout=1)

    persisted_running = run_store.load(binding.task_run_id)
    assert persisted_running is not None
    assert persisted_running.status == "running"
    assert persisted_running.finished_at is None
    assert persisted_running.workspace_path == str(workspace_path)
    assert persisted_running.live_stream_capable is True
    assert persisted_running.session_id == binding.session_id
    assert persisted_running.session_epoch == binding.session_epoch
    assert persisted_running.container_id == "container-1"
    assert persisted_running.thread_id == "thread-1"
    assert persisted_running.active_turn_id is None
    assert persisted_running.observability_events_ref is None

    release_send_turn.set()
    await start_task

    persisted_completed = run_store.load(binding.task_run_id)
    assert persisted_completed is not None
    assert persisted_completed.status == "completed"
    assert persisted_completed.live_stream_capable is True


async def test_start_raises_when_send_turn_returns_failed_status(tmp_path: Path) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.task_run_id / "repo"
    summary_calls: list[Any] = []
    publication_calls: list[Any] = []
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    oversized_reason = "turn failed: " + ("x" * 5000)
    expected_reason = oversized_reason[:4096]

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        raise AssertionError("session_status should not be used before launch")

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch + 1,
            container_id="container-2",
            thread_id="thread-2",
            status="failed",
            assistant_text="",
        ).model_copy(update={"metadata": {"reason": oversized_reason}})

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        summary_calls.append(_request)
        return _summary(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            last_assistant_text="",
        )

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        publication_calls.append(_request)
        return _publication(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

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
        prepare_turn_instructions=_prepare_turn_instructions,
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

    with pytest.raises(RuntimeError) as excinfo:
        await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    assert str(excinfo.value) == expected_reason
    assert summary_calls == []
    assert publication_calls == []
    persisted_record = run_store.load(binding.task_run_id)
    assert persisted_record is not None
    assert persisted_record.status == "failed"
    assert persisted_record.workspace_path == str(workspace_path)
    assert persisted_record.live_stream_capable is True
    assert persisted_record.error_message == expected_reason
    assert persisted_record.session_id == binding.session_id
    assert persisted_record.session_epoch == binding.session_epoch + 1
    assert persisted_record.container_id == "container-2"
    assert persisted_record.thread_id == "thread-2"


async def test_start_marks_run_failed_when_post_turn_follow_up_raises(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.task_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    signal_calls: list[Any] = []
    summary_error = "summary fetch failed after send_turn"

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        raise AssertionError("session_status should not be used before launch")

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch + 1,
            container_id="container-2",
            thread_id="thread-2",
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        raise RuntimeError(summary_error)

    async def _signal_action(payload: dict[str, Any]) -> None:
        signal_calls.append(payload)

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
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_fetch_summary,
        publish_remote_artifacts=AsyncMock(),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_signal_action,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    with pytest.raises(RuntimeError, match=summary_error):
        await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    assert signal_calls[-1] == {
        "action": "send_turn",
        "containerId": "container-2",
        "threadId": "thread-2",
    }
    persisted_record = run_store.load(binding.task_run_id)
    assert persisted_record is not None
    assert persisted_record.status == "failed"
    assert persisted_record.error_message == summary_error
    assert persisted_record.live_stream_capable is True
    assert persisted_record.session_epoch == binding.session_epoch + 1
    assert persisted_record.container_id == "container-2"
    assert persisted_record.thread_id == "thread-2"


async def test_start_passes_profile_materialization_payload_to_launch_session(
    tmp_path: Path,
) -> None:
    binding = _binding()
    launch_calls: list[Any] = []

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

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [
                {
                    "profile_id": "codex_openrouter_qwen36_plus",
                    "provider_id": "openrouter",
                    "credential_source": "secret_ref",
                    "default_model": "qwen/qwen3.6-plus:free",
                    "secret_refs": {"provider_api_key": "env://OPENROUTER_API_KEY"},
                    "home_path_overrides": {
                        "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
                    },
                    "env_template": {"OPENAI_BASE_URL": "https://openrouter.ai/api/v1"},
                    "file_templates": [
                        {
                            "path": "{{runtime_support_dir}}/codex-home/config.toml",
                            "contentTemplate": "model = 'qwen/qwen3.6-plus:free'",
                        }
                    ],
                }
            ]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=AsyncMock(
            return_value=_turn_response(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(
            return_value=_summary(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        publish_remote_artifacts=AsyncMock(
            return_value=_publication(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    request = _request(binding).model_copy(
        update={"execution_profile_ref": "codex_openrouter_qwen36_plus"}
    )
    await adapter.start(request)

    assert len(launch_calls) == 1
    launch_payload = launch_calls[0]
    profile = launch_payload["profile"]
    assert profile["profileId"] == "codex_openrouter_qwen36_plus"
    assert profile["credentialSource"] == "secret_ref"
    assert profile["defaultModel"] == "qwen/qwen3.6-plus:free"
    assert profile["secretRefs"] == {"provider_api_key": "env://OPENROUTER_API_KEY"}
    assert profile["homePathOverrides"] == {
        "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
    }


async def test_start_passes_task_timeout_policy_to_launch_session(
    tmp_path: Path,
) -> None:
    binding = _binding()
    launch_calls: list[Any] = []

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

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "oauth_volume"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=AsyncMock(
            return_value=_turn_response(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(
            return_value=_summary(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        publish_remote_artifacts=AsyncMock(
            return_value=_publication(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    await adapter.start(_request(binding, timeout_seconds=1800))

    assert len(launch_calls) == 1
    launch_payload = launch_calls[0]
    assert launch_payload["request"]["turnCompletionTimeoutSeconds"] == 1800


async def test_start_uses_profile_default_timeout_when_request_timeout_missing(
    tmp_path: Path,
) -> None:
    binding = _binding()
    launch_calls: list[Any] = []

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

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [
                {
                    "profile_id": "codex-default",
                    "credential_source": "oauth_volume",
                    "default_timeout_seconds": 1800,
                }
            ]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed-runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=AsyncMock(
            return_value=_turn_response(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(
            return_value=_summary(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        publish_remote_artifacts=AsyncMock(
            return_value=_publication(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    await adapter.start(_request(binding))

    assert len(launch_calls) == 1
    launch_payload = launch_calls[0]
    assert launch_payload["request"]["turnCompletionTimeoutSeconds"] == 1800


async def test_start_clamps_requested_timeout_to_supported_send_turn_budget(
    tmp_path: Path,
) -> None:
    binding = _binding()
    launch_calls: list[Any] = []

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

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "oauth_volume"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed-runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=AsyncMock(
            return_value=_turn_response(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(
            return_value=_summary(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        publish_remote_artifacts=AsyncMock(
            return_value=_publication(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    await adapter.start(_request(binding, timeout_seconds=7200))

    assert len(launch_calls) == 1
    launch_payload = launch_calls[0]
    assert (
        launch_payload["request"]["turnCompletionTimeoutSeconds"]
        == MAX_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS
    )


async def test_start_falls_back_to_clamped_profile_default_on_timeout_overflow(
    tmp_path: Path,
) -> None:
    binding = _binding()
    launch_calls: list[Any] = []

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

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [
                {
                    "profile_id": "codex-default",
                    "credential_source": "oauth_volume",
                    "default_timeout_seconds": 7200,
                }
            ]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed-runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=AsyncMock(
            return_value=_turn_response(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(
            return_value=_summary(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        publish_remote_artifacts=AsyncMock(
            return_value=_publication(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    await adapter.start(_request(binding, timeout_seconds="inf"))

    assert len(launch_calls) == 1
    launch_payload = launch_calls[0]
    assert (
        launch_payload["request"]["turnCompletionTimeoutSeconds"]
        == MAX_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS
    )


async def test_start_delegates_turn_instruction_preparation_before_sending_turn(
    tmp_path: Path,
) -> None:
    binding = _binding()
    expected_workspace_path = tmp_path / "agent_jobs" / binding.task_run_id / "repo"
    send_turn_calls: list[Any] = []
    prepared_payloads: list[dict[str, Any]] = []

    async def _custom_prepare_turn_instructions(payload: dict[str, Any]) -> str:
        prepared_payloads.append(payload)
        assert payload["workspacePath"] == str(expected_workspace_path)
        assert payload["request"]["instructionRef"] == "artifact:instructions"
        return "Injected context instruction\n\nManaged Codex CLI note:"

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "oauth_volume"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed-runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=AsyncMock(),
        prepare_turn_instructions=_custom_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(
            return_value=_summary(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        publish_remote_artifacts=AsyncMock(
            return_value=_publication(
                session_id=binding.session_id,
                session_epoch=binding.session_epoch,
                container_id="container-1",
                thread_id="thread-1",
            )
        ),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    await adapter.start(_request(binding, workspace_path=str(expected_workspace_path)))

    assert prepared_payloads
    assert send_turn_calls
    assert send_turn_calls[0].instructions.startswith("Injected context instruction")
    assert "Managed Codex CLI note:" in send_turn_calls[0].instructions


async def test_start_rejects_non_text_input_refs_for_session_turns(
    tmp_path: Path,
) -> None:
    binding = _binding()
    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "oauth_volume"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed-runs"),
        load_session_snapshot=AsyncMock(),
        launch_session=AsyncMock(),
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=AsyncMock(),
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(),
        publish_remote_artifacts=AsyncMock(),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )
    request = _request(binding).model_copy(update={"input_refs": ["artifact:input-1"]})

    with pytest.raises(
        ValueError,
        match="does not support inputRefs",
    ):
        await adapter.start(request)


async def test_start_reuses_existing_task_scoped_session_without_launching(
    tmp_path: Path,
) -> None:
    binding = _binding()
    session_status_calls: list[Any] = []
    control_calls: list[dict[str, Any]] = []

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
        launch_session=_launch_session,
        session_status=_session_status,
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

    handle = await adapter.start(_request(binding))

    assert handle.metadata["containerId"] == "container-existing"
    assert len(session_status_calls) == 1
    assert session_status_calls[0].container_id == "container-existing"
    assert session_status_calls[0].thread_id == "thread-existing"
    assert control_calls[0] == {
        "action": "resume_session",
        "containerId": "container-existing",
        "threadId": "thread-existing",
    }


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
        prepare_turn_instructions=_prepare_turn_instructions,
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
        prepare_turn_instructions=_prepare_turn_instructions,
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


async def test_save_run_state_persists_blank_workspace_path_as_none(
    tmp_path: Path,
) -> None:
    binding = _binding()
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "secret_ref"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=run_store,
        load_session_snapshot=AsyncMock(),
        launch_session=AsyncMock(),
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=AsyncMock(),
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(),
        publish_remote_artifacts=AsyncMock(),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    adapter._save_run_state(
        run_id=binding.task_run_id,
        agent_id="codex",
        managed_run_id=binding.task_run_id,
        binding=binding,
        workspace_path="   ",
        locator={
            "sessionId": binding.session_id,
            "sessionEpoch": binding.session_epoch,
            "containerId": "container-1",
            "threadId": "thread-1",
        },
        active_turn_id=None,
        result={
            "summary": "Completed",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
        finished_at=datetime.now(tz=UTC),
    )

    persisted_record = run_store.load(binding.task_run_id)

    assert persisted_record is not None
    assert persisted_record.workspace_path is None


async def test_save_run_state_clears_active_turn_id_when_explicitly_none(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = str(tmp_path / "agent_jobs" / binding.task_run_id / "repo")
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "secret_ref"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        runtime_id="codex_cli",
        run_store=run_store,
        load_session_snapshot=AsyncMock(),
        launch_session=AsyncMock(),
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=AsyncMock(),
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(),
        publish_remote_artifacts=AsyncMock(),
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    adapter._save_run_state(
        run_id=binding.task_run_id,
        agent_id="codex",
        managed_run_id=binding.task_run_id,
        binding=binding,
        workspace_path=workspace_path,
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

    adapter._save_run_state(
        run_id=binding.task_run_id,
        agent_id="codex",
        managed_run_id=binding.task_run_id,
        binding=binding,
        workspace_path=workspace_path,
        locator={
            "sessionId": binding.session_id,
            "sessionEpoch": binding.session_epoch,
            "containerId": "container-1",
            "threadId": "thread-1",
        },
        active_turn_id=None,
        result={
            "summary": "Completed",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
        finished_at=datetime.now(tz=UTC),
    )

    persisted_record = run_store.load(binding.task_run_id)

    assert persisted_record is not None
    assert persisted_record.active_turn_id is None


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
        prepare_turn_instructions=_prepare_turn_instructions,
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


async def test_fetch_result_maps_failed_pr_resolver_artifact_for_completed_run(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "failed",\n'
            '  "final_reason": "pr_not_found",\n'
            '  "next_step": "done"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-failure"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(tz=UTC),
            workspacePath=str(workspace_path),
        )
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
        run_store=run_store,
        load_session_snapshot=_async_noop,
        launch_session=_async_noop,
        session_status=_async_noop,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_async_noop,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_async_noop,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    adapter._save_run_state(
        run_id=run_id,
        agent_id="codex_cli",
        locator={
            "sessionId": "sess:wf-task-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
        },
        active_turn_id=None,
        result={
            "summary": "Codex managed-session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(run_id, pr_resolver_expected=True)

    assert result.failure_class == "execution_error"
    assert result.summary is not None
    assert "pr-resolver reported status 'failed'" in result.summary
    assert "pr_not_found" in result.summary


async def test_fetch_result_maps_blocked_pr_resolver_artifact_for_completed_run(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "attempts_exhausted",\n'
            '  "final_reason": "actionable_comments",\n'
            '  "next_step": "run_fix_comments_skill"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-blocked"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(tz=UTC),
            workspacePath=str(workspace_path),
        )
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
        run_store=run_store,
        load_session_snapshot=_async_noop,
        launch_session=_async_noop,
        session_status=_async_noop,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_async_noop,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_async_noop,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    adapter._save_run_state(
        run_id=run_id,
        agent_id="codex_cli",
        locator={
            "sessionId": "sess:wf-task-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
        },
        active_turn_id=None,
        result={
            "summary": "Codex managed-session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(run_id, pr_resolver_expected=True)

    assert result.failure_class == "user_error"
    assert result.summary is not None
    assert "pr-resolver reported status 'attempts_exhausted'" in result.summary
    assert "run_fix_comments_skill" in result.summary
