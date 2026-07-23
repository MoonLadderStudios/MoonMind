from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    TimeoutError as TemporalTimeoutError,
    TimeoutType,
)

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRuntimeStepExecutionLaunch,
    ManagedRunRecord,
)
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionHandle,
    CodexManagedSessionLocator,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
)
from moonmind.workflows.codex_session_timeouts import (
    DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS,
    MAX_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS,
)
from moonmind.workflows.adapters.codex_session_adapter import (
    CodexSessionAdapter,
    CodexSessionRunFailedError,
    _jira_skill_blocker_summary,
    _pr_resolver_terminal_contract,
)
from moonmind.workflows.temporal.workflows.agent_run import (
    _terminal_contract_continuation_instruction,
)
from moonmind.workflows.temporal.managed_session_errors import (
    is_managed_session_locator_mismatch_error,
)
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    _CODEX_PROVIDER_USAGE_LIMIT_REACHED_REASON,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.asyncio]


def test_pr_resolver_terminal_contract_ignores_attempt_artifacts(tmp_path: Path) -> None:
    attempts = tmp_path / "var" / "pr_resolver" / "attempts"
    attempts.mkdir(parents=True)
    (attempts / "attempt-1.json").write_text('{"status":"merged"}', encoding="utf-8")

    satisfied, missing, metadata = _pr_resolver_terminal_contract(str(tmp_path))

    assert satisfied is False
    assert missing == ["var/pr_resolver/result.json"]
    assert metadata["prResolverLatestAttempt"]["attemptCount"] == 1


def test_pr_resolver_terminal_contract_requires_publish_evidence_for_merge(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "var" / "pr_resolver" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text('{"status":"merged"}', encoding="utf-8")

    satisfied, missing, _metadata = _pr_resolver_terminal_contract(str(tmp_path))

    assert satisfied is False
    assert missing == ["artifacts/publish_result.json"]
    instruction = _terminal_contract_continuation_instruction(missing)
    assert "artifacts/publish_result.json" in instruction
    assert "do not declare completion" in instruction


def _terminal_contract_test_adapter(
    tmp_path: Path,
    *,
    send_turn: Any,
    terminate_remote_session: Any = None,
) -> CodexSessionAdapter:
    binding = _binding()

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-terminal-contract",
            thread_id="thread-terminal-contract",
        )

    async def _fetch_summary(
        request: FetchCodexManagedSessionSummaryRequest,
    ) -> CodexManagedSessionSummary:
        return _summary(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
        )

    async def _publish_artifacts(
        request: PublishCodexManagedSessionArtifactsRequest,
    ) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
        )

    return CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "secret_ref"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-terminal-contract",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=_async_noop,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=terminate_remote_session or _async_noop,
        fetch_remote_summary=_fetch_summary,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )


def _pr_resolver_request(
    binding: CodexManagedSessionBinding,
    workspace_path: Path,
    *,
    timeout_seconds: float | None = None,
) -> AgentExecutionRequest:
    request = _request(
        binding,
        workspace_path=str(workspace_path),
        timeout_seconds=timeout_seconds,
    )
    return request.model_copy(
        update={"parameters": {"publishMode": "none", "selectedSkill": "pr-resolver"}}
    )

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


async def test_session_locator_mismatch_error_ignores_non_exception_cause() -> None:
    class MetadataCauseError(Exception):
        cause = {"message": "metadata"}

    assert not is_managed_session_locator_mismatch_error(MetadataCauseError("wrapper"))


def _raise_activity_error_from_application_error(error: ApplicationError) -> None:
    try:
        raise error
    except ApplicationError as exc:
        raise ActivityError(
            "activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test-worker",
            activity_type="agent_runtime.send_turn",
            activity_id="activity-1",
            retry_state=None,
        ) from exc

def _raise_activity_error_from_timeout(
    timeout_type: TimeoutType = TimeoutType.SCHEDULE_TO_CLOSE,
) -> None:
    """Mimic a Temporal activity timeout: ActivityError whose cause is a
    temporalio TimeoutError (not an ApplicationError turn error)."""
    try:
        raise TemporalTimeoutError(
            "activity ScheduleToClose timeout",
            type=timeout_type,
            last_heartbeat_details=[],
        )
    except TemporalTimeoutError as exc:
        raise ActivityError(
            "activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test-worker",
            activity_type="agent_runtime.send_turn",
            activity_id="activity-1",
            retry_state=None,
        ) from exc


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
    instruction_ref: str = "artifact:instructions",
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
        instructionRef=instruction_ref,
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

async def test_start_launches_missing_workflow_scoped_session_and_persists_result(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
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
        task_workflow_id="wf-user-1",
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

    request = _request(binding, workspace_path=str(workspace_path))
    request.step_execution = AgentRuntimeStepExecutionLaunch(
        workflowId="wf-user-1",
        runId="run-user-1",
        logicalStepId="queue-github-issues",
        executionOrdinal=1,
        stepExecutionId="wf-user-1:run-user-1:queue-github-issues:execution:1",
        runtimeContextPolicy="fresh_agent_run",
    )
    request.parameters["metadata"] = {
        "moonmind": {
            "latestContextPackRef": "artifacts/context/rag-context-abc123.json",
            "retrievedContextArtifactPath": "artifacts/context/rag-context-abc123.json",
            "retrievedContextTransport": "direct",
            "retrievedContextItemCount": 2,
            "retrievalDurabilityAuthority": "artifact_ref",
            "sessionContinuityCacheStatus": "advisory_only",
        }
    }

    handle = await adapter.start(request)
    status = await adapter.status(handle.run_id)
    result = await adapter.fetch_result(handle.run_id)
    persisted_record = run_store.load(binding.agent_run_id)

    assert len(launch_calls) == 1
    launch_payload = launch_calls[0]
    assert isinstance(launch_payload, dict)
    assert launch_payload["profile"]["runtimeId"] == "codex_cli"
    assert launch_payload["profile"]["profileId"] == "codex-default"
    launch_request = launch_payload["request"]
    assert launch_request["sessionId"] == binding.session_id
    assert launch_request["workspacePath"] == str(workspace_path)
    assert launch_request["sessionWorkspacePath"].endswith(f"{binding.agent_run_id}/session")
    assert launch_request["artifactSpoolPath"].endswith(f"{binding.agent_run_id}/artifacts")
    assert launch_request["codexHomePath"].endswith(f"{binding.agent_run_id}/.moonmind/codex-home")
    assert launch_request["imageRef"] == "ghcr.io/moonladderstudios/moonmind:latest"
    assert (
        launch_request["turnCompletionTimeoutSeconds"]
        == DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS
    )
    assert launch_request["workspaceSpec"] == {"workspacePath": str(workspace_path)}
    assert launch_request["metadata"]["latestContextPackRef"] == "artifacts/context/rag-context-abc123.json"
    assert launch_request["metadata"]["retrievalDurabilityAuthority"] == "artifact_ref"
    assert send_turn_calls[0].instructions.startswith("artifact:instructions")
    assert "Managed Codex CLI note:" in send_turn_calls[0].instructions

    assert handle.status == "completed"
    assert handle.metadata["sessionId"] == binding.session_id
    assert handle.metadata["containerId"] == "container-1"
    assert persisted_record is not None
    assert persisted_record.run_id == binding.agent_run_id
    assert persisted_record.workflow_id == "wf-agent-run-1"
    assert persisted_record.runtime_id == "codex_cli"
    assert persisted_record.status == "completed"
    assert persisted_record.owner_run_id == "run-user-1"
    assert persisted_record.logical_step_id == "queue-github-issues"
    assert persisted_record.execution_ordinal == 1
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
    assert result.metadata["workspacePath"] == str(workspace_path)
    assert (
        result.metadata["sessionSummary"]["latestSummaryRef"]
        == "artifact:session-summary"
    )
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

async def test_start_compacts_session_result_metadata_for_workflow_payload(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    large_summary_text = "s" * 7_800
    large_publication_detail = "p" * 7_800

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        workspace_path.mkdir(parents=True)
        (workspace_path / ".git").mkdir()
        (workspace_path / ".launch-complete").write_text("ready\n", encoding="utf-8")
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_locator: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
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
            last_assistant_text=large_summary_text,
        )

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        return CodexManagedSessionArtifactsPublication(
            sessionState={
                "sessionId": binding.session_id,
                "sessionEpoch": binding.session_epoch,
                "containerId": "container-1",
                "threadId": "thread-1",
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
            metadata={
                "status": "completed",
                "stdoutArtifactRef": "artifact:stdout",
                "stderrArtifactRef": "artifact:stderr",
                "diagnosticsRef": "artifact:diagnostics",
                "observabilityEventsRef": "artifact:observability.events.jsonl",
                "verboseRuntimeDetail": large_publication_detail,
            },
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

    handle = await adapter.start(_request(binding, workspace_path=str(workspace_path)))
    result = await adapter.fetch_result(handle.run_id)
    persisted_record = run_store.load(binding.agent_run_id)

    assert (
        result.metadata["sessionSummary"]["latestSummaryRef"]
        == "artifact:session-summary"
    )
    assert "metadata" not in result.metadata["sessionSummary"]
    assert result.metadata["sessionArtifacts"]["publishedArtifactRefs"] == [
        "artifact:stdout",
        "artifact:stderr",
        "artifact:diagnostics",
        "artifact:observability.events.jsonl",
        "artifact:session-summary",
        "artifact:session-checkpoint",
    ]
    assert result.metadata["sessionArtifacts"]["metadata"] == {
        "status": "completed",
        "stdoutArtifactRef": "artifact:stdout",
        "stderrArtifactRef": "artifact:stderr",
        "diagnosticsRef": "artifact:diagnostics",
        "observabilityEventsRef": "artifact:observability.events.jsonl",
    }
    assert "verboseRuntimeDetail" not in result.metadata["sessionArtifacts"]["metadata"]
    assert len(json.dumps(result.metadata, sort_keys=True)) < 16_384
    assert persisted_record is not None
    assert persisted_record.stdout_artifact_ref == "artifact:stdout"
    assert persisted_record.diagnostics_ref == "artifact:diagnostics"
    assert (
        persisted_record.observability_events_ref
        == "artifact:observability.events.jsonl"
    )

async def test_start_prepares_turn_instructions_after_cold_session_launch(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    call_order: list[str] = []
    send_turn_calls: list[Any] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        call_order.append("launch")
        workspace_path.mkdir(parents=True)
        (workspace_path / ".git").mkdir()
        (workspace_path / ".launch-complete").write_text("ready\n", encoding="utf-8")
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _prepare_after_launch(payload: dict[str, Any]) -> str:
        assert payload["workspacePath"] == str(workspace_path)
        if payload.get("metadataOnly") is True:
            call_order.append("preflight")
            assert not (workspace_path / ".launch-complete").exists()
            return {"durableRetrievalMetadata": {}}
        call_order.append("prepare")
        assert (workspace_path / ".launch-complete").is_file()
        return "prepared after launch\n\nManaged Codex CLI note:"

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        call_order.append("send")
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
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_after_launch,
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

    await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    assert call_order == ["preflight", "launch", "prepare", "send"]
    assert send_turn_calls[0].instructions.startswith("prepared after launch")

async def test_start_can_preserve_pre_launch_instruction_preparation_order(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    call_order: list[str] = []
    send_turn_calls: list[Any] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        call_order.append("load_snapshot")
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        call_order.append("launch")
        workspace_path.mkdir(parents=True)
        (workspace_path / ".launch-complete").write_text("ready\n", encoding="utf-8")
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _prepare_before_launch(payload: dict[str, Any]) -> str:
        call_order.append("prepare")
        assert payload.get("skipSkillMaterialization") is not True
        assert not (workspace_path / ".launch-complete").exists()
        return "prepared before launch\n\nManaged Codex CLI note:"

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        call_order.append("send")
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
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        load_session_snapshot=_load_snapshot,
        launch_session=_launch_session,
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_before_launch,
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
        defer_turn_instructions_until_session_launch=False,
    )

    await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    assert call_order == ["prepare", "load_snapshot", "launch", "send"]
    assert send_turn_calls[0].instructions.startswith("prepared before launch")

async def test_start_passes_managed_session_dood_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MOONMIND_WORKFLOW_DOCKER_MODE", "unrestricted")
    monkeypatch.setenv("MOONMIND_AGENT_WORKSPACES_VOLUME_NAME", "agent_workspaces_test")
    binding = _binding()
    workspace_root = tmp_path / "agent_jobs"
    workspace_path = workspace_root / binding.agent_run_id / "repo"
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

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
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

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "oauth_volume"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        task_workflow_id="wf-parent-task-1",
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
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
        apply_session_control_action=_async_noop,
        workspace_root=str(workspace_root),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    launch_environment = launch_calls[0]["request"]["environment"]
    assert launch_environment["MOONMIND_WORKDIR"] == str(workspace_root)
    assert launch_environment["MOONMIND_JOB_ID"] == binding.agent_run_id
    assert launch_environment["MOONMIND_TASK_WORKFLOW_ID"] == "wf-parent-task-1"
    assert launch_environment["MOONMIND_AGENT_RUN_ID"] == binding.agent_run_id
    assert (
        launch_environment["MOONMIND_CONTAINER_WORKSPACE_VOLUME"]
        == "agent_workspaces_test"
    )
    assert launch_environment["MOONMIND_WORKFLOW_DOCKER_MODE"] == "unrestricted"


def _make_adapter_for_environment_test(
    *,
    tmp_path: Path,
    task_workflow_id: str | None,
) -> CodexSessionAdapter:
    return CodexSessionAdapter(
        profile_fetcher=_fake_profiles(
            [{"profile_id": "codex-default", "credential_source": "oauth_volume"}]
        ),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-agent-run-1",
        task_workflow_id=task_workflow_id,
        runtime_id="codex_cli",
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
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


async def test_managed_session_launch_environment_overrides_incoming_task_identity(
    tmp_path: Path,
) -> None:
    binding = _binding()
    adapter = _make_adapter_for_environment_test(
        tmp_path=tmp_path,
        task_workflow_id="wf-parent-task-trusted",
    )

    spoofed_environment = {
        "MOONMIND_TASK_WORKFLOW_ID": "wf-attacker-task",
        "MOONMIND_AGENT_RUN_ID": "wf-attacker-run",
        "OTHER_VAR": "preserved",
    }

    launch_environment = adapter._managed_session_launch_environment(
        binding=binding,
        environment=spoofed_environment,
    )

    assert launch_environment["MOONMIND_TASK_WORKFLOW_ID"] == "wf-parent-task-trusted"
    assert launch_environment["MOONMIND_AGENT_RUN_ID"] == binding.agent_run_id
    assert launch_environment["OTHER_VAR"] == "preserved"


async def test_managed_session_launch_environment_drops_task_workflow_when_unset(
    tmp_path: Path,
) -> None:
    binding = _binding()
    adapter = _make_adapter_for_environment_test(
        tmp_path=tmp_path,
        task_workflow_id=None,
    )

    spoofed_environment = {
        "MOONMIND_TASK_WORKFLOW_ID": "wf-attacker-task",
        "MOONMIND_AGENT_RUN_ID": "wf-attacker-run",
    }

    launch_environment = adapter._managed_session_launch_environment(
        binding=binding,
        environment=spoofed_environment,
    )

    assert "MOONMIND_TASK_WORKFLOW_ID" not in launch_environment
    assert launch_environment["MOONMIND_AGENT_RUN_ID"] == binding.agent_run_id


async def test_start_omits_large_inline_instruction_from_result_metadata(
    tmp_path: Path,
) -> None:
    binding = _binding()
    large_instruction = "Use this request as the canonical input:\n" + (
        "Implement the workflow cleanup. " * 400
    )

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
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

    handle = await adapter.start(_request(binding, instruction_ref=large_instruction))
    result = await adapter.fetch_result(handle.run_id)

    assert result.metadata["instructionRefOmitted"] is True
    assert result.metadata["instructionRefLengthChars"] == len(large_instruction.strip())
    assert len(result.metadata["instructionRefSha256"]) == 64
    assert "instructionRef" not in result.metadata

async def test_start_preserves_completed_codex_turn_with_usage_limit_summary(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    quota_summary = (
        "You've hit your usage limit. To get more access now, send a request "
        "to your admin or try again at 4:45 AM."
    )

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
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
        run_store=run_store,
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
                assistant_text=quota_summary,
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
                last_assistant_text=quota_summary,
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

    handle = await adapter.start(_request(binding, workspace_path=str(workspace_path)))
    result = await adapter.fetch_result(handle.run_id)

    assert handle.status == "completed"
    assert result.summary == quota_summary
    assert result.failure_class is None
    assert result.provider_error_code is None
    assert result.retry_recommendation is None
    assert "providerFailure" not in result.metadata

    persisted_record = run_store.load(binding.agent_run_id)
    await adapter.fetch_result(handle.run_id)
    assert persisted_record is not None
    assert persisted_record.status == "completed"
    assert persisted_record.failure_class is None
    assert persisted_record.provider_error_code is None

async def test_start_passes_oauth_profile_auth_target_to_launch_session(
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
                    "profile_id": "codex-oauth",
                    "runtime_id": "codex_cli",
                    "provider_id": "openai",
                    "credential_source": "oauth_volume",
                    "runtime_materialization_mode": "oauth_home",
                    "volume_ref": "codex_auth_volume",
                    "volume_mount_path": "/home/app/.codex-auth",
                    "max_parallel_runs": 1,
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

    await adapter.start(
        _request(binding).model_copy(update={"execution_profile_ref": "codex-oauth"})
    )

    assert len(launch_calls) == 1
    launch_payload = launch_calls[0]
    launch_request = launch_payload["request"]
    assert launch_request["environment"]["MANAGED_AUTH_VOLUME_PATH"] == (
        "/home/app/.codex-auth"
    )
    assert launch_request["environment"]["MOONMIND_EXECUTION_PROFILE_REF"] == (
        "codex-oauth"
    )
    assert launch_request["codexHomePath"].endswith(
        f"{binding.agent_run_id}/.moonmind/codex-home"
    )
    assert launch_request["codexHomePath"] != (
        launch_request["environment"]["MANAGED_AUTH_VOLUME_PATH"]
    )
    assert launch_payload["profile"]["credentialSource"] == "oauth_volume"

async def test_start_persists_running_live_capable_record_before_send_turn_completes(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
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

    persisted_running = run_store.load(binding.agent_run_id)
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
    assert persisted_running.error_message is None

    release_send_turn.set()
    await start_task

    persisted_completed = run_store.load(binding.agent_run_id)
    assert persisted_completed is not None
    assert persisted_completed.status == "completed"
    assert persisted_completed.live_stream_capable is True

async def test_start_fails_jira_issue_creator_when_no_issues_created(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    assistant_text = (
        "I could not create the Jira stories because Jira access is not configured. "
        "No Jira issues were created."
    )

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_locator: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            assistant_text=assistant_text,
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            last_assistant_text=assistant_text,
        )

    async def _publish_artifacts(_request: Any) -> CodexManagedSessionArtifactsPublication:
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
    request = _request(binding, workspace_path=str(workspace_path))
    request.parameters["metadata"] = {
        "moonmind": {
            "selectedSkill": "jira-issue-creator",
            "stepLedger": {
                "logicalStepId": "tpl:jira-breakdown:1.0.0:02:demo",
                "attempt": 1,
                "scope": "step",
            },
        },
    }

    with pytest.raises(CodexSessionRunFailedError) as exc_info:
        await adapter.start(request)

    persisted_record = run_store.load(binding.agent_run_id)
    assert exc_info.value.agent_run_result.failure_class == "execution_error"
    assert "No Jira issues were created" in exc_info.value.agent_run_result.summary
    assert persisted_record is not None
    assert persisted_record.status == "failed"
    assert persisted_record.failure_class == "execution_error"

async def test_start_fails_jira_pr_verify_when_issue_body_unavailable(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    bridge_payloads: list[dict[str, Any]] = []
    assistant_text = (
        "I could not read the Jira issue body from Atlassian in this runtime "
        "without an authenticated Jira session, so this check is based on the "
        "PR/branch scope."
    )

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_locator: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            assistant_text=assistant_text,
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            last_assistant_text=assistant_text,
        )

    async def _publish_artifacts(_request: Any) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _publish_bridge_events(payload: dict[str, Any]) -> None:
        bridge_payloads.append(dict(payload))

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
        publish_bridge_events=_publish_bridge_events,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )
    request = _request(binding, workspace_path=str(workspace_path))
    request.parameters["metadata"] = {
        "moonmind": {
            "selectedSkill": "jira-pr-verify",
        },
    }
    request.parameters["communication"] = {"mode": "omnigent_bridge"}

    with pytest.raises(CodexSessionRunFailedError) as exc_info:
        await adapter.start(request)

    persisted_record = run_store.load(binding.agent_run_id)
    assert exc_info.value.agent_run_result.failure_class == "execution_error"
    assert (
        "could not read the Jira issue body"
        in exc_info.value.agent_run_result.summary
    )
    assert persisted_record is not None
    assert persisted_record.status == "failed"
    assert persisted_record.failure_class == "execution_error"
    assert [item["phase"] for item in bridge_payloads] == ["started", "terminal"]
    assert bridge_payloads[1]["terminalStatus"] == "failed"
    assert bridge_payloads[1]["turnResponse"]["status"] == "completed"

async def test_jira_verify_blocker_summary_detects_comment_posting_failure() -> None:
    summary = _jira_skill_blocker_summary(
        parameters={
            "metadata": {
                "moonmind": {
                    "selectedSkill": "jira-verify",
                },
            },
        },
        assistant_text="jira.add_comment failed with HTTP 403: policy denied",
    )

    assert summary == "jira.add_comment failed with HTTP 403: policy denied"


@pytest.mark.asyncio
async def test_direct_bridge_publishes_typed_active_observations_separately() -> None:
    payloads: list[dict[str, Any]] = []

    async def publish(payload: dict[str, Any]) -> None:
        payloads.append(payload)

    adapter = object.__new__(CodexSessionAdapter)
    adapter._publish_bridge_events = publish
    binding = CodexManagedSessionBinding(
        workflowId="workflow-3418",
        agentRunId="run-3418",
        sessionId="session-3418",
        sessionEpoch=1,
        runtimeId="codex_cli",
        executionProfileRef="codex-default",
    )
    request = _request(binding, workspace_path="/tmp/workspace-3418")
    request.parameters["communication"] = {"mode": "omnigent_bridge"}
    response = _turn_response(
        session_id=binding.session_id,
        session_epoch=1,
        container_id="container-1",
        thread_id="thread-1",
        assistant_text="done",
    ).model_copy(
        update={
            "metadata": {
                "observabilityEvents": [
                    {
                        "kind": "tool_call_started",
                        "turnId": "turn-1",
                        "metadata": {"sourceEventId": "source-1"},
                    }
                ]
            }
        }
    )

    await adapter._publish_direct_codex_bridge_active(
        request=request,
        binding=binding,
        locator=CodexManagedSessionLocator(
            sessionId=binding.session_id,
            sessionEpoch=1,
            containerId="container-1",
            threadId="thread-1",
        ),
        turn_response=response,
    )

    assert len(payloads) == 1
    assert payloads[0]["phase"] == "active"
    assert payloads[0]["turnId"] == response.turn_id
    assert payloads[0]["observations"][0]["kind"] == "tool_call_started"

async def test_jira_verify_blocker_summary_detects_auth_error_code() -> None:
    summary = _jira_skill_blocker_summary(
        parameters={
            "metadata": {
                "moonmind": {
                    "selectedSkill": "jira-verify",
                },
            },
        },
        assistant_text="jira_auth_failed",
    )

    assert summary == "jira_auth_failed"

async def test_start_allows_jira_issue_creator_mixed_output_with_created_issue_keys(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    assistant_text = (
        "An earlier attempt reported that no Jira issues were created.\n\n"
        "Created issue keys:\n"
        "- PROJ-1\n"
        "- PROJ-2"
    )

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_locator: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            assistant_text=assistant_text,
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            last_assistant_text=assistant_text,
        )

    async def _publish_artifacts(_request: Any) -> CodexManagedSessionArtifactsPublication:
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
    request = _request(binding, workspace_path=str(workspace_path))
    request.parameters["selectedSkill"] = "jira-issue-creator"

    handle = await adapter.start(request)
    result = await adapter.fetch_result(handle.run_id)

    persisted_record = run_store.load(binding.agent_run_id)
    assert handle.status == "completed"
    assert persisted_record is not None
    assert persisted_record.status == "completed"
    assert persisted_record.failure_class is None
    assert result.failure_class is None
    assert result.summary == assistant_text

async def test_start_raises_when_send_turn_returns_failed_status(tmp_path: Path) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    summary_calls: list[Any] = []
    publication_calls: list[Any] = []
    bridge_payloads: list[dict[str, Any]] = []
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

    async def _publish_bridge_events(payload: dict[str, Any]) -> None:
        bridge_payloads.append(dict(payload))

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
        publish_bridge_events=_publish_bridge_events,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    request = _request(binding, workspace_path=str(workspace_path))
    request.parameters["communication"] = {"mode": "omnigent_bridge"}

    with pytest.raises(RuntimeError) as excinfo:
        await adapter.start(request)

    assert str(excinfo.value) == expected_reason
    assert summary_calls == []
    assert len(publication_calls) == 1
    assert [item["phase"] for item in bridge_payloads] == ["started", "terminal"]
    assert bridge_payloads[1]["terminalStatus"] == "failed"
    assert bridge_payloads[1]["summary"]["latestSummaryRef"] == "artifact:session-summary"
    assert bridge_payloads[1]["turnResponse"]["status"] == "failed"
    persisted_record = run_store.load(binding.agent_run_id)
    assert persisted_record is not None
    assert persisted_record.status == "failed"
    assert persisted_record.workspace_path == str(workspace_path)
    assert persisted_record.live_stream_capable is True
    assert persisted_record.error_message == expected_reason
    assert persisted_record.failure_class == "execution_error"
    assert persisted_record.stdout_artifact_ref == "artifact:stdout"
    assert persisted_record.stderr_artifact_ref == "artifact:stderr"
    assert persisted_record.diagnostics_ref == "artifact:diagnostics"
    assert persisted_record.session_id == binding.session_id
    assert persisted_record.session_epoch == binding.session_epoch + 1
    assert persisted_record.container_id == "container-2"
    assert persisted_record.thread_id == "thread-2"

async def test_start_resets_session_once_after_empty_assistant_activity_failure(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    send_turn_calls: list[Any] = []
    clear_calls: list[Any] = []
    attach_calls: list[dict[str, Any]] = []
    control_calls: list[dict[str, Any]] = []
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    reset_thread_id = "thread-1:empty-output-reset"

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        if len(send_turn_calls) == 1:
            metadata = {
                "reason": "codex app-server turn/completed produced no assistant output",
                "failureClass": "transient",
                "failureCause": "app_server_protocol_empty_turn",
                "turnFailureEvidence": {"schemaVersion": "v1"},
            }
            _raise_activity_error_from_application_error(
                ApplicationError(
                    metadata["reason"],
                    metadata,
                    type="CodexTransientTurnError",
                    non_retryable=False,
                )
            )
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
            turn_id="turn-after-reset",
            status="completed",
            assistant_text="Recovered after session reset.",
        )

    async def _clear_remote_session(request: Any) -> CodexManagedSessionHandle:
        clear_calls.append(request)
        assert request.new_thread_id == reset_thread_id
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
            last_assistant_text="Recovered after session reset.",
        )

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
        )

    async def _apply_control_action(payload: dict[str, Any]) -> None:
        control_calls.append(payload)

    async def _attach_runtime_handles(payload: dict[str, Any]) -> None:
        attach_calls.append(payload)

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
        launch_session=_async_noop,
        session_status=_session_status,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_clear_remote_session,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_fetch_summary,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_attach_runtime_handles,
        apply_session_control_action=_apply_control_action,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    handle = await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    assert handle.status == "completed"
    assert len(send_turn_calls) == 2
    assert len(clear_calls) == 1
    assert send_turn_calls[1].thread_id == reset_thread_id
    assert {
        "sessionEpoch": 2,
        "containerId": "container-1",
        "threadId": reset_thread_id,
        "activeTurnId": None,
    } in attach_calls
    assert {
        "action": "clear_session",
        "reason": "retry_after_empty_assistant_output",
        "sessionEpoch": 2,
        "containerId": "container-1",
        "threadId": reset_thread_id,
    } in control_calls
    persisted_record = run_store.load(binding.agent_run_id)
    result = await adapter.fetch_result(handle.run_id)
    assert persisted_record is not None
    assert persisted_record.status == "completed"
    assert persisted_record.session_epoch == 2
    assert persisted_record.thread_id == reset_thread_id
    assert result.metadata["turnMetadata"]["sessionInterventions"] == [
        {
            "action": "clear_session",
            "reason": "retry_after_empty_assistant_output",
            "fromThreadId": "thread-1",
            "toThreadId": reset_thread_id,
            "fromSessionEpoch": 1,
            "toSessionEpoch": 2,
        }
    ]


@pytest.mark.asyncio
async def test_start_resets_session_after_prefixed_empty_assistant_activity_failure(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    send_turn_calls: list[Any] = []
    clear_calls: list[Any] = []
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    reset_thread_id = "thread-1:empty-output-reset"

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        if len(send_turn_calls) == 1:
            metadata = {
                "reason": (
                    "turn failed: codex app-server turn/completed produced "
                    "no assistant output"
                ),
                "failureClass": "transient",
                "retryRecommendedAction": "clear_session",
                "turnFailureEvidence": {"schemaVersion": "v1"},
            }
            _raise_activity_error_from_application_error(
                ApplicationError(
                    metadata["reason"],
                    metadata,
                    type="CodexTransientTurnError",
                    non_retryable=True,
                )
            )
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
            turn_id="turn-after-reset",
            status="completed",
            assistant_text="Recovered after session reset.",
        )

    async def _clear_remote_session(request: Any) -> CodexManagedSessionHandle:
        clear_calls.append(request)
        assert request.new_thread_id == reset_thread_id
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
            last_assistant_text="Recovered after session reset.",
        )

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
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
        launch_session=_async_noop,
        session_status=_session_status,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_clear_remote_session,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_fetch_summary,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    handle = await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    assert handle.status == "completed"
    assert len(send_turn_calls) == 2
    assert len(clear_calls) == 1
    assert send_turn_calls[1].thread_id == reset_thread_id


async def test_start_marks_empty_assistant_retry_exhausted(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    reset_thread_id = "thread-1:empty-output-reset"
    second_reset_thread_id = "thread-1:empty-output-reset:empty-output-reset"
    send_turn_calls: list[Any] = []
    clear_calls: list[Any] = []
    run_store = ManagedRunStore(tmp_path / "managed_runs")

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        metadata = {
            "reason": "codex app-server turn/completed produced no assistant output",
            "failureClass": "transient",
            "failureCause": "app_server_protocol_empty_turn",
            "retryRecommendedAction": "clear_session",
            "turnFailureEvidence": {"schemaVersion": "v1"},
        }
        _raise_activity_error_from_application_error(
            ApplicationError(
                metadata["reason"],
                metadata,
                type="CodexTransientTurnError",
                non_retryable=True,
            )
        )

    async def _clear_remote_session(request: Any) -> CodexManagedSessionHandle:
        clear_calls.append(request)
        assert request.session_epoch == len(clear_calls)
        assert request.thread_id == (
            "thread-1" if len(clear_calls) == 1 else reset_thread_id
        )
        expected_thread_id = (
            reset_thread_id if len(clear_calls) == 1 else second_reset_thread_id
        )
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=1 + len(clear_calls),
            container_id="container-1",
            thread_id=expected_thread_id,
        )

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=binding.session_id,
            session_epoch=2,
            container_id="container-1",
            thread_id=reset_thread_id,
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
        launch_session=_async_noop,
        session_status=_session_status,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_clear_remote_session,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    with pytest.raises(CodexSessionRunFailedError) as excinfo:
        await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    assert len(send_turn_calls) == 3
    assert len(clear_calls) == 2
    assert clear_calls[0].request_id == (
        f"{binding.agent_run_id}:empty-assistant-clear:1:thread-1"
    )
    assert clear_calls[1].request_id == (
        f"{binding.agent_run_id}:empty-assistant-clear:2:{reset_thread_id}"
    )
    turn_metadata = excinfo.value.agent_run_result.metadata["turnMetadata"]
    assert turn_metadata["selfHealExhausted"] is True
    assert turn_metadata["selfHealAction"] == "clear_session"
    assert turn_metadata["selfHealAttempts"] == 2
    assert turn_metadata["sessionInterventions"][0]["toSessionEpoch"] == 2
    assert turn_metadata["sessionInterventions"][1]["toSessionEpoch"] == 3


async def test_start_recovers_after_second_empty_assistant_clear(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    reset_thread_id = "thread-1:empty-output-reset"
    second_reset_thread_id = "thread-1:empty-output-reset:empty-output-reset"
    send_turn_calls: list[Any] = []
    clear_calls: list[Any] = []
    run_store = ManagedRunStore(tmp_path / "managed_runs")

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        if len(send_turn_calls) < 3:
            metadata = {
                "reason": "codex app-server turn/completed produced no assistant output",
                "failureClass": "transient",
                "failureCause": "app_server_protocol_empty_turn",
                "retryRecommendedAction": "clear_session",
                "turnFailureEvidence": {"schemaVersion": "v1"},
            }
            _raise_activity_error_from_application_error(
                ApplicationError(
                    metadata["reason"],
                    metadata,
                    type="CodexTransientTurnError",
                    non_retryable=True,
                )
            )
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=3,
            container_id="container-1",
            thread_id=second_reset_thread_id,
            turn_id="turn-after-second-reset",
            status="completed",
            assistant_text="Recovered after second session reset.",
        )

    async def _clear_remote_session(request: Any) -> CodexManagedSessionHandle:
        clear_calls.append(request)
        assert request.session_epoch == len(clear_calls)
        assert request.thread_id == (
            "thread-1" if len(clear_calls) == 1 else reset_thread_id
        )
        expected_thread_id = (
            reset_thread_id if len(clear_calls) == 1 else second_reset_thread_id
        )
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=1 + len(clear_calls),
            container_id="container-1",
            thread_id=expected_thread_id,
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        return _summary(
            session_id=binding.session_id,
            session_epoch=3,
            container_id="container-1",
            thread_id=second_reset_thread_id,
            last_assistant_text="Recovered after second session reset.",
        )

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=binding.session_id,
            session_epoch=3,
            container_id="container-1",
            thread_id=second_reset_thread_id,
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
        launch_session=_async_noop,
        session_status=_session_status,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_clear_remote_session,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_fetch_summary,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    handle = await adapter.start(_request(binding, workspace_path=str(workspace_path)))
    result = await adapter.fetch_result(handle.run_id)

    assert handle.status == "completed"
    assert len(send_turn_calls) == 3
    assert len(clear_calls) == 2
    assert send_turn_calls[2].thread_id == second_reset_thread_id
    assert result.metadata["turnMetadata"]["sessionInterventions"] == [
        {
            "action": "clear_session",
            "reason": "retry_after_empty_assistant_output",
            "fromThreadId": "thread-1",
            "toThreadId": reset_thread_id,
            "fromSessionEpoch": 1,
            "toSessionEpoch": 2,
        },
        {
            "action": "clear_session",
            "reason": "retry_after_empty_assistant_output",
            "fromThreadId": reset_thread_id,
            "toThreadId": second_reset_thread_id,
            "fromSessionEpoch": 2,
            "toSessionEpoch": 3,
        },
    ]


async def test_start_does_not_clear_session_for_codex_usage_limit_failure(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    send_turn_calls: list[Any] = []
    clear_remote_session = AsyncMock()

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(
            binding=binding,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _session_status(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(request: Any) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        metadata = {
            "reason": _CODEX_PROVIDER_USAGE_LIMIT_REACHED_REASON,
            "failureClass": "integration_error",
        }
        _raise_activity_error_from_application_error(
            ApplicationError(
                metadata["reason"],
                metadata,
                type="CodexPermanentTurnError",
                non_retryable=True,
            )
        )

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
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
        launch_session=_async_noop,
        session_status=_session_status,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=clear_remote_session,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    with pytest.raises(CodexSessionRunFailedError) as excinfo:
        await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    result = excinfo.value.agent_run_result
    assert len(send_turn_calls) == 1
    clear_remote_session.assert_not_awaited()
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "429"
    assert result.retry_recommendation == "retry_after_cooldown"
    provider_failure = result.metadata["providerFailure"]
    assert provider_failure["providerErrorClass"] == "rate_limit"
    assert provider_failure["providerErrorCode"] == "429"
    assert provider_failure["retryRecommendation"] == "retry_after_cooldown"
    assert result.metadata["turnMetadata"]["failureClass"] == "integration_error"


async def test_start_classifies_codex_provider_capacity_failure_and_publishes_artifacts(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    reason = (
        "provider emitted verbose diagnostics "
        + ("x" * 5000)
        + " http 503 high demand"
    )

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            status="failed",
            assistant_text="",
        ).model_copy(update={"metadata": {"reason": reason}})

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
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
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(),
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    with pytest.raises(RuntimeError) as excinfo:
        await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    result = excinfo.value.agent_run_result
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "provider_capacity"
    assert result.retry_recommendation == "retry_after_cooldown"
    assert "high demand" not in result.summary
    assert result.output_refs == [
        "artifact:turn-output",
        "artifact:stdout",
        "artifact:stderr",
        "artifact:diagnostics",
        "artifact:observability.events.jsonl",
        "artifact:session-summary",
        "artifact:session-checkpoint",
    ]
    provider_failure = result.metadata["providerFailure"]
    assert provider_failure["providerErrorCode"] == "provider_capacity"
    # MM-882: the adapter emits the canonical structured failure envelope with a
    # distinct sanitized summary and no raw provider text ("high demand").
    assert provider_failure["providerErrorClass"] == "capacity"
    assert provider_failure["retryRecommendation"] == "retry_after_cooldown"
    assert "high demand" not in provider_failure["sanitizedSummary"].lower()
    assert "reason" not in provider_failure
    assert result.metadata["profileId"] == "codex-default"

    persisted_record = run_store.load(binding.agent_run_id)
    assert persisted_record is not None
    assert persisted_record.failure_class == "integration_error"
    assert persisted_record.provider_error_code == "provider_capacity"
    assert persisted_record.stdout_artifact_ref == "artifact:stdout"
    assert persisted_record.stderr_artifact_ref == "artifact:stderr"
    assert persisted_record.diagnostics_ref == "artifact:diagnostics"

async def test_start_classifies_send_turn_timeout_with_actionable_summary(
    tmp_path: Path,
) -> None:
    """A Temporal activity timeout on send_turn must surface a descriptive,
    operator-actionable failed result instead of an empty/bare-token summary.

    Regression for mm:4b897068, where a timed-out codex turn produced
    failure_class="execution_error" with summary=null.
    """
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        _raise_activity_error_from_timeout()
        raise AssertionError("unreachable")  # pragma: no cover

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
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
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

    with pytest.raises(CodexSessionRunFailedError) as excinfo:
        await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    result = excinfo.value.agent_run_result
    assert result.failure_class == "execution_error"
    assert result.summary
    assert result.summary not in {
        "user_error",
        "integration_error",
        "execution_error",
        "system_error",
    }
    assert "timed out" in result.summary.lower()
    assert "schedule to close" in result.summary.lower()
    assert result.metadata.get("turnStatus") == "timed_out"

    persisted_record = run_store.load(binding.agent_run_id)
    assert persisted_record is not None
    assert persisted_record.failure_class == "execution_error"


async def test_start_classifies_codex_auth_failure_as_user_error(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    reason = "turn failed: http 401"

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
            status="failed",
            assistant_text="",
        ).model_copy(update={"metadata": {"reason": reason}})

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
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
        session_status=AsyncMock(),
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_send_turn,
        interrupt_turn=_async_noop,
        clear_remote_session=_async_noop,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=AsyncMock(),
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    with pytest.raises(RuntimeError) as excinfo:
        await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    result = excinfo.value.agent_run_result
    assert result.failure_class == "user_error"
    assert result.provider_error_code == "401"
    assert result.retry_recommendation == "reauthenticate"
    provider_failure = result.metadata["providerFailure"]
    assert provider_failure["providerErrorCode"] == "401"
    # MM-882: canonical auth failure class with a distinct sanitized summary.
    assert provider_failure["providerErrorClass"] == "auth"
    assert "reason" not in provider_failure
    assert "authentication" in provider_failure["sanitizedSummary"].lower()
    assert result.metadata["profileId"] == "codex-default"

    persisted_record = run_store.load(binding.agent_run_id)
    assert persisted_record is not None
    assert persisted_record.failure_class == "user_error"
    assert persisted_record.provider_error_code == "401"

async def test_publish_failure_artifacts_logs_best_effort_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    binding = _binding()
    run_store = ManagedRunStore(tmp_path / "managed_runs")

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        raise RuntimeError("artifact store unavailable")

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles([]),
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
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )
    caplog.set_level(
        "WARNING",
        logger="moonmind.workflows.adapters.codex_session_adapter",
    )

    publication = await adapter._publish_failure_artifacts(
        locator=CodexManagedSessionLocator(
            sessionId=binding.session_id,
            sessionEpoch=binding.session_epoch,
            containerId="container-1",
            threadId="thread-1",
        ),
        managed_run_id=binding.agent_run_id,
        run_id="run-1",
    )

    assert publication is None
    assert (
        "Failed to publish Codex session failure artifacts for run run-1"
        in caplog.text
    )
    assert "artifact store unavailable" in caplog.text

async def test_publish_failure_artifacts_preserves_cancellation(
    tmp_path: Path,
) -> None:
    binding = _binding()
    run_store = ManagedRunStore(tmp_path / "managed_runs")

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        raise asyncio.CancelledError

    adapter = CodexSessionAdapter(
        profile_fetcher=_fake_profiles([]),
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
        publish_remote_artifacts=_publish_artifacts,
        attach_runtime_handles=_async_noop,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    with pytest.raises(asyncio.CancelledError):
        await adapter._publish_failure_artifacts(
            locator=CodexManagedSessionLocator(
                sessionId=binding.session_id,
                sessionEpoch=binding.session_epoch,
                containerId="container-1",
                threadId="thread-1",
            ),
            managed_run_id=binding.agent_run_id,
            run_id="run-1",
        )

@pytest.mark.parametrize(
    ("failure_stage", "expected_message"),
    [
        ("prepare", "instructions unavailable"),
        ("send_turn", "transport blew up"),
        ("fetch_summary", "summary unavailable"),
        ("publish_artifacts", "artifact publication failed"),
    ],
)
async def test_start_finalizes_failed_record_for_post_save_exceptions(
    tmp_path: Path,
    failure_stage: str,
    expected_message: str,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    run_store = ManagedRunStore(tmp_path / "managed_runs")

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

    async def _prepare(payload: dict[str, Any]) -> str:
        if failure_stage == "prepare":
            raise ValueError(expected_message)
        return await _prepare_turn_instructions(payload)

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
        if failure_stage == "send_turn":
            raise RuntimeError(expected_message)
        return _turn_response(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch + 1,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _fetch_summary(_request: Any) -> CodexManagedSessionSummary:
        if failure_stage == "fetch_summary":
            raise RuntimeError(expected_message)
        return _summary(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch + 1,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _publish_artifacts(
        _request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        if failure_stage == "publish_artifacts":
            raise RuntimeError(expected_message)
        return _publication(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch + 1,
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
        prepare_turn_instructions=_prepare,
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

    with pytest.raises(Exception, match=expected_message):
        await adapter.start(_request(binding, workspace_path=str(workspace_path)))

    persisted_record = run_store.load(binding.agent_run_id)

    assert persisted_record is not None
    assert persisted_record.status == "failed"
    assert persisted_record.error_message == expected_message
    assert persisted_record.failure_class == "execution_error"
    assert persisted_record.session_id == binding.session_id
    expected_epoch = binding.session_epoch if failure_stage in {"prepare", "send_turn"} else 2
    assert persisted_record.session_epoch == expected_epoch
    assert persisted_record.live_stream_capable is True

async def test_start_marks_run_failed_when_post_turn_follow_up_raises(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
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
    persisted_record = run_store.load(binding.agent_run_id)
    assert persisted_record is not None
    assert persisted_record.status == "failed"
    assert persisted_record.error_message == summary_error
    assert persisted_record.failure_class == "execution_error"
    assert persisted_record.live_stream_capable is True
    assert persisted_record.session_epoch == binding.session_epoch + 1
    assert persisted_record.container_id == "container-2"
    assert persisted_record.thread_id == "thread-2"

async def test_start_resolves_workspace_path_once_per_turn(tmp_path: Path) -> None:
    binding = _binding()
    expected_workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
    workspace_path_calls = 0

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-1",
            thread_id="thread-1",
        )

    async def _send_turn(_request: Any) -> CodexManagedSessionTurnResponse:
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

    original_workspace_path_for_request = adapter._workspace_path_for_request

    def _counting_workspace_path_for_request(
        *,
        binding: CodexManagedSessionBinding,
        request: AgentExecutionRequest,
    ) -> str:
        nonlocal workspace_path_calls
        workspace_path_calls += 1
        return original_workspace_path_for_request(binding=binding, request=request)

    adapter._workspace_path_for_request = _counting_workspace_path_for_request

    await adapter.start(_request(binding, workspace_path=str(expected_workspace_path)))

    assert workspace_path_calls == 1

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
                    "default_model": "qwen/qwen3.6-plus",
                    "secret_refs": {"provider_api_key": "env://OPENROUTER_API_KEY"},
                    "home_path_overrides": {
                        "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
                    },
                    "env_template": {"OPENAI_BASE_URL": "https://openrouter.ai/api/v1"},
                    "file_templates": [
                        {
                            "path": "{{runtime_support_dir}}/codex-home/config.toml",
                            "contentTemplate": "model = 'qwen/qwen3.6-plus'",
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
    assert profile["defaultModel"] == "qwen/qwen3.6-plus"
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
    expected_workspace_path = tmp_path / "agent_jobs" / binding.agent_run_id / "repo"
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

async def test_start_populates_launch_metadata_from_prepared_turn_request(
    tmp_path: Path,
) -> None:
    binding = _binding()
    launch_calls: list[Any] = []
    prepare_calls: list[bool] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        return _snapshot(binding=binding)

    async def _prepare(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["includePreparedRequestMetadata"] is True
        prepare_calls.append(bool(payload.get("skipSkillMaterialization")))
        return {
            "instructions": "Injected context instruction\n\nManaged Codex CLI note:",
            "activeSkillsDir": "/work/runtime/skills_active/snapshot-1",
            "durableRetrievalMetadata": {
                "latestContextPackRef": "artifacts/context/rag-context-prepared.json",
                "retrievedContextArtifactPath": "artifacts/context/rag-context-prepared.json",
                "retrievedContextTransport": "direct",
                "retrievedContextItemCount": 2,
                "retrievalDurabilityAuthority": "artifact_ref",
                "sessionContinuityCacheStatus": "advisory_only",
            },
        }

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
        prepare_turn_instructions=_prepare,
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

    request = _request(binding)

    await adapter.start(request)

    assert len(launch_calls) == 1
    assert prepare_calls == [False, False]
    launch_request = launch_calls[0]["request"]
    assert (
        launch_request["environment"]["MOONMIND_ACTIVE_SKILLS_DIR"]
        == "/work/runtime/skills_active/snapshot-1"
    )
    assert (
        launch_request["metadata"]["latestContextPackRef"]
        == "artifacts/context/rag-context-prepared.json"
    )
    assert launch_request["metadata"]["retrievalDurabilityAuthority"] == "artifact_ref"
    assert (
        request.parameters["metadata"]["moonmind"]["latestContextPackRef"]
        == "artifacts/context/rag-context-prepared.json"
    )
    assert (
        request.parameters["metadata"]["moonmind"]["retrievalDurabilityAuthority"]
        == "artifact_ref"
    )

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

async def test_start_reuses_existing_workflow_scoped_session_without_launching(
    tmp_path: Path,
) -> None:
    binding = _binding()
    session_status_calls: list[Any] = []
    send_turn_calls: list[SendCodexManagedSessionTurnRequest] = []
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

    async def _send_turn(
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
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

    request = _request(binding)
    request.step_execution = AgentRuntimeStepExecutionLaunch(
        workflowId="wf-user-1",
        runId="run-user-1",
        logicalStepId="queue-github-issues",
        executionOrdinal=2,
        stepExecutionId="wf-user-1:run-user-1:queue-github-issues:execution:2",
        runtimeContextPolicy="reuse_session_same_epoch",
    )
    request.parameters["_moonmindActiveSkillsDir"] = (
        "/work/runtime/skills_active/snapshot-retry"
    )

    handle = await adapter.start(request)

    assert handle.metadata["containerId"] == "container-existing"
    assert len(session_status_calls) == 1
    assert session_status_calls[0].container_id == "container-existing"
    assert session_status_calls[0].thread_id == "thread-existing"
    assert send_turn_calls[0].environment == {
        "MOONMIND_ACTIVE_SKILLS_DIR": "/work/runtime/skills_active/snapshot-retry",
        "MOONMIND_STEP_EXECUTION_ID": (
            "wf-user-1:run-user-1:queue-github-issues:execution:2"
        ),
    }
    assert request.parameters["_moonmindActiveSkillsDir"] == (
        "/work/runtime/skills_active/snapshot-retry"
    )
    assert control_calls[0] == {
        "action": "resume_session",
        "containerId": "container-existing",
        "threadId": "thread-existing",
    }


async def test_start_retries_existing_session_status_after_locator_mismatch(
    tmp_path: Path,
) -> None:
    binding = _binding().model_copy(update={"session_epoch": 3})
    load_snapshot_calls: list[str] = []
    session_status_calls: list[CodexManagedSessionLocator] = []
    send_turn_calls: list[SendCodexManagedSessionTurnRequest] = []
    control_calls: list[dict[str, Any]] = []

    async def _load_snapshot(workflow_id: str) -> CodexManagedSessionSnapshot:
        load_snapshot_calls.append(workflow_id)
        return _snapshot(
            binding=binding,
            container_id="container-cleared",
            thread_id="thread-cleared",
        )

    async def _launch_session(_request: Any) -> CodexManagedSessionHandle:
        raise AssertionError("launch_session should not be called for a ready session")

    async def _session_status(
        request: CodexManagedSessionLocator,
    ) -> CodexManagedSessionHandle:
        session_status_calls.append(request)
        if len(session_status_calls) == 1:
            _raise_activity_error_from_application_error(
                ApplicationError(
                    "sessionEpoch does not match the active managed session",
                    type="RuntimeError",
                )
            )
        return _session_handle(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
        )

    async def _send_turn(
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        return _turn_response(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
        )

    async def _fetch_summary(
        request: FetchCodexManagedSessionSummaryRequest,
    ) -> CodexManagedSessionSummary:
        return _summary(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
        )

    async def _publish_artifacts(
        request: PublishCodexManagedSessionArtifactsRequest,
    ) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
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

    assert handle.status == "completed"
    assert len(load_snapshot_calls) == 2
    assert len(session_status_calls) == 2
    assert session_status_calls[0].session_epoch == 3
    assert session_status_calls[1].session_epoch == 3
    assert send_turn_calls[0].session_epoch == 3
    assert send_turn_calls[0].thread_id == "thread-cleared"
    assert control_calls[0] == {
        "action": "resume_session",
        "containerId": "container-cleared",
        "threadId": "thread-cleared",
    }

async def test_start_relaunches_session_when_refreshed_locator_still_mismatches(
    tmp_path: Path,
) -> None:
    binding = _binding().model_copy(update={"session_epoch": 7})
    stale_snapshot_binding = binding.model_copy(update={"session_epoch": 6})
    load_snapshot_calls: list[str] = []
    session_status_calls: list[CodexManagedSessionLocator] = []
    launch_calls: list[Any] = []
    attach_calls: list[dict[str, Any]] = []
    send_turn_calls: list[SendCodexManagedSessionTurnRequest] = []
    control_calls: list[dict[str, Any]] = []

    async def _load_snapshot(workflow_id: str) -> CodexManagedSessionSnapshot:
        load_snapshot_calls.append(workflow_id)
        return _snapshot(
            binding=stale_snapshot_binding,
            container_id="container-stale",
            thread_id="thread-stale",
        )

    async def _session_status(
        request: CodexManagedSessionLocator,
    ) -> CodexManagedSessionHandle:
        session_status_calls.append(request)
        _raise_activity_error_from_application_error(
            ApplicationError(
                "sessionEpoch does not match the active managed session",
                type="RuntimeError",
            )
        )

    async def _launch_session(request: Any) -> CodexManagedSessionHandle:
        launch_calls.append(request)
        return _session_handle(
            session_id=binding.session_id,
            session_epoch=binding.session_epoch,
            container_id="container-fresh",
            thread_id="thread-fresh",
        )

    async def _send_turn(
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        send_turn_calls.append(request)
        return _turn_response(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
        )

    async def _fetch_summary(
        request: FetchCodexManagedSessionSummaryRequest,
    ) -> CodexManagedSessionSummary:
        return _summary(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
        )

    async def _publish_artifacts(
        request: PublishCodexManagedSessionArtifactsRequest,
    ) -> CodexManagedSessionArtifactsPublication:
        return _publication(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
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

    handle = await adapter.start(_request(binding))

    assert handle.status == "completed"
    assert len(load_snapshot_calls) == 2
    assert len(session_status_calls) == 2
    assert len(launch_calls) == 1
    assert session_status_calls[0].container_id == "container-stale"
    assert session_status_calls[0].session_epoch == 6
    assert session_status_calls[1].thread_id == "thread-stale"
    assert session_status_calls[1].session_epoch == 6
    launch_request = launch_calls[0]["request"]
    assert launch_request["sessionEpoch"] == 7
    assert launch_request["threadId"] == f"thread:{binding.session_id}:7"
    assert send_turn_calls[0].container_id == "container-fresh"
    assert send_turn_calls[0].thread_id == "thread-fresh"
    assert attach_calls == [
        {"containerId": "container-fresh", "threadId": "thread-fresh"}
    ]
    assert control_calls[0] == {
        "action": "start_session",
        "containerId": "container-fresh",
        "threadId": "thread-fresh",
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

    handle = await adapter.clear_session(
        binding=binding,
        new_thread_id="thread-2",
        reason="reset",
    )

    assert handle.session_state.session_epoch == 2
    assert handle.session_state.thread_id == "thread-2"
    assert attach_calls == [
        {
            "sessionEpoch": 2,
            "containerId": "container-1",
            "threadId": "thread-2",
            "activeTurnId": None,
        }
    ]
    assert control_calls == [
        {
            "action": "clear_session",
            "reason": "reset",
            "sessionEpoch": 2,
            "containerId": "container-1",
            "threadId": "thread-2",
        }
    ]


async def test_clear_session_refreshes_locator_after_epoch_mismatch(
    tmp_path: Path,
) -> None:
    binding = _binding()
    refreshed_binding = binding.model_copy(update={"session_epoch": 2})
    reset_thread_id = "thread-1:empty-output-reset"
    load_calls = 0
    clear_calls: list[Any] = []
    status_calls: list[Any] = []
    attach_calls: list[dict[str, Any]] = []

    async def _load_snapshot(_workflow_id: str) -> CodexManagedSessionSnapshot:
        nonlocal load_calls
        load_calls += 1
        if load_calls == 1:
            return _snapshot(
                binding=binding,
                container_id="container-1",
                thread_id="thread-1",
            )
        return _snapshot(
            binding=refreshed_binding,
            container_id="container-1",
            thread_id=reset_thread_id,
        )

    async def _clear_remote_session(request: Any) -> CodexManagedSessionHandle:
        clear_calls.append(request)
        assert request.request_id == "clear-request-1"
        _raise_activity_error_from_application_error(
            ApplicationError(
                "sessionEpoch does not match the active managed session",
                type="RuntimeError",
            )
        )

    async def _session_status(request: Any) -> CodexManagedSessionHandle:
        status_calls.append(request)
        return _session_handle(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
        )

    async def _attach_runtime_handles(payload: dict[str, Any]) -> None:
        attach_calls.append(payload)

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
        session_status=_session_status,
        prepare_turn_instructions=_prepare_turn_instructions,
        send_turn=_async_noop,
        interrupt_turn=_async_noop,
        clear_remote_session=_clear_remote_session,
        terminate_remote_session=_async_noop,
        fetch_remote_summary=_async_noop,
        publish_remote_artifacts=_async_noop,
        attach_runtime_handles=_attach_runtime_handles,
        apply_session_control_action=_async_noop,
        workspace_root=str(tmp_path / "agent_jobs"),
        session_image_ref="ghcr.io/moonladderstudios/moonmind:latest",
    )

    handle = await adapter.clear_session(
        binding=binding,
        new_thread_id=reset_thread_id,
        reason="retry_after_empty_assistant_output",
        request_id="clear-request-1",
    )

    assert handle.session_state.session_epoch == 2
    assert handle.session_state.thread_id == reset_thread_id
    assert len(clear_calls) == 1
    assert len(status_calls) == 1
    assert attach_calls == [
        {
            "sessionEpoch": 2,
            "containerId": "container-1",
            "threadId": reset_thread_id,
            "activeTurnId": None,
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
    assert result.summary == "Canceled managed session turn."

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
        run_id=binding.agent_run_id,
        agent_id="codex",
        managed_run_id=binding.agent_run_id,
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

    persisted_record = run_store.load(binding.agent_run_id)

    assert persisted_record is not None
    assert persisted_record.workspace_path is None

async def test_save_run_state_clears_active_turn_id_when_explicitly_none(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workspace_path = str(tmp_path / "agent_jobs" / binding.agent_run_id / "repo")
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
        run_id=binding.agent_run_id,
        agent_id="codex",
        managed_run_id=binding.agent_run_id,
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
        run_id=binding.agent_run_id,
        agent_id="codex",
        managed_run_id=binding.agent_run_id,
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

    persisted_record = run_store.load(binding.agent_run_id)

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
            "summary": "Managed session turn completed.",
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

async def test_fetch_result_treats_pr_resolver_reenter_gate_as_continuation(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "attempts_exhausted",\n'
            '  "mergeAutomationDisposition": "reenter_gate",\n'
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
            "summary": "Managed session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(
        run_id,
        pr_resolver_expected=True,
        pr_resolver_merge_gate_owned=True,
    )

    assert result.failure_class is None
    assert result.metadata["mergeAutomationDisposition"] == "reenter_gate"

async def test_fetch_result_reports_standalone_pr_resolver_reentry_as_failure(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "attempts_exhausted",\n'
            '  "mergeAutomationDisposition": "reenter_gate",\n'
            '  "final_reason": "ci_failures",\n'
            '  "next_step": "run_fix_ci_skill"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-standalone-ci"
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
            "summary": "Managed session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(run_id, pr_resolver_expected=True)

    assert result.failure_class == "execution_error"
    assert result.summary is not None
    assert "pr-resolver reported status 'attempts_exhausted'" in result.summary
    assert "ci_failures" in result.summary
    assert "next_step=run_fix_ci_skill" in result.summary
    assert "mergeAutomationDisposition" not in result.metadata

async def test_fetch_result_clears_generic_failed_exit_for_pr_resolver_reentry(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "blocked",\n'
            '  "final_reason": "ci_running",\n'
            '  "next_step": "wait_for_ci_and_retry_finalize"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-ci-reenter"
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="failed",
            failureClass="execution_error",
            errorMessage="Process exited with code 3",
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
            "summary": "Process exited with code 3",
            "failureClass": "execution_error",
            "metadata": {},
        },
        status="failed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(
        run_id,
        pr_resolver_expected=True,
        pr_resolver_merge_gate_owned=True,
    )

    assert result.failure_class is None
    assert result.summary == "pr-resolver requested merge automation re-entry."
    assert result.metadata["mergeAutomationDisposition"] == "reenter_gate"

async def test_fetch_result_maps_merged_pr_resolver_artifact_metadata(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "merged",\n'
            '  "reason": "already_merged",\n'
            '  "headSha": "abc123"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-merged"
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
            "summary": "Managed session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(run_id, pr_resolver_expected=True)

    assert result.failure_class is None
    assert result.metadata["mergeAutomationDisposition"] == "already_merged"
    assert result.metadata["headSha"] == "abc123"


async def test_fetch_result_maps_final_state_field_pr_resolver_artifact_metadata(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    artifacts_path = workspace_path / "artifacts"
    artifacts_path.mkdir(parents=True)
    (artifacts_path / "pr_resolver_result.json").write_text(
        (
            "{\n"
            '  "pr": 1643,\n'
            '  "url": "https://github.com/MoonLadderStudios/Tactics/pull/1643",\n'
            '  "final_state": "merged",\n'
            '  "head_commit": "d00d0200ac76b6425d8ba03cf7859382ad50ce5e"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-final-state-merged"
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
            "summary": "Managed session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(run_id, pr_resolver_expected=True)

    assert result.failure_class is None
    assert result.metadata["mergeAutomationDisposition"] == "merged"
    assert (
        result.metadata["headSha"]
        == "d00d0200ac76b6425d8ba03cf7859382ad50ce5e"
    )


async def test_fetch_result_prefers_explicit_pr_resolver_disposition(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "merged",\n'
            '  "merge_outcome": "merged",\n'
            '  "mergeAutomationDisposition": "already_merged",\n'
            '  "reason": "ci_complete"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-explicit-disposition"
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
            "summary": "Managed session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(run_id, pr_resolver_expected=True)

    assert result.failure_class is None
    assert result.metadata["mergeAutomationDisposition"] == "already_merged"


async def test_fetch_result_maps_final_state_merged_pr_resolver_artifact_metadata(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    artifacts_path = workspace_path / "artifacts"
    artifacts_path.mkdir(parents=True)
    (artifacts_path / "pr_resolver_result.json").write_text(
        (
            "{\n"
            '  "generatedAt": "2026-04-25T15:34:55.886536Z",\n'
            '  "headSha": "   ",\n'
            '  "final": {\n'
            '    "state": "MERGED",\n'
            '    "mergedAt": "2026-04-25T15:34:22Z",\n'
            '    "headRefOid": "49061ed20f6b2260ba9564e71f4f896e3f96d3df"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-final-merged"
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
            "summary": "Managed session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(run_id, pr_resolver_expected=True)

    assert result.failure_class is None
    assert result.metadata["mergeAutomationDisposition"] == "merged"
    assert (
        result.metadata["headSha"]
        == "49061ed20f6b2260ba9564e71f4f896e3f96d3df"
    )


async def test_fetch_result_maps_outcome_merged_pr_resolver_artifact_metadata(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    artifacts_path = workspace_path / "artifacts"
    artifacts_path.mkdir(parents=True)
    (artifacts_path / "pr_resolver_result.json").write_text(
        (
            "{\n"
            '  "pr": 1640,\n'
            '  "url": "https://github.com/MoonLadderStudios/Tactics/pull/1640",\n'
            '  "status": "success",\n'
            '  "outcome": "merged",\n'
            '  "merged_at": "2026-04-25T20:58:09Z",\n'
            '  "head_oid": "1abd16796a984860ca922cd1d6d22c42db34be6b"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    run_id = "run-result-pr-outcome-merged"
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
            "summary": "Managed session turn completed.",
            "metadata": {},
        },
        status="completed",
        started_at=datetime.now(tz=UTC),
    )

    result = await adapter.fetch_result(run_id, pr_resolver_expected=True)

    assert result.failure_class is None
    assert result.metadata["mergeAutomationDisposition"] == "merged"
    assert (
        result.metadata["headSha"]
        == "1abd16796a984860ca922cd1d6d22c42db34be6b"
    )
