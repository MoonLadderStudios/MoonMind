"""Workflow-side adapter for managed Codex task-scoped sessions."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunState,
    AgentRunStatus,
    ManagedRunRecord,
)
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionClearRequest,
    CodexManagedSessionHandle,
    CodexManagedSessionLocator,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
    canonical_codex_managed_runtime_id,
)
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
    build_managed_profile_launch_context,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


SessionSnapshotLoader = Callable[
    [str], Awaitable[CodexManagedSessionSnapshot | Mapping[str, Any]]
]
SessionHandleSignaler = Callable[[dict[str, Any]], Awaitable[None]]
SessionControlSignaler = Callable[[dict[str, Any]], Awaitable[None]]
LaunchSessionFunc = Callable[
    [LaunchCodexManagedSessionRequest], Awaitable[CodexManagedSessionHandle | Mapping[str, Any]]
]
SessionStatusFunc = Callable[
    [CodexManagedSessionLocator], Awaitable[CodexManagedSessionHandle | Mapping[str, Any]]
]
SendTurnFunc = Callable[
    [SendCodexManagedSessionTurnRequest],
    Awaitable[CodexManagedSessionTurnResponse | Mapping[str, Any]],
]
InterruptTurnFunc = Callable[
    [InterruptCodexManagedSessionTurnRequest],
    Awaitable[CodexManagedSessionTurnResponse | Mapping[str, Any]],
]
ClearSessionFunc = Callable[
    [CodexManagedSessionClearRequest],
    Awaitable[CodexManagedSessionHandle | Mapping[str, Any]],
]
TerminateSessionFunc = Callable[
    [TerminateCodexManagedSessionRequest],
    Awaitable[CodexManagedSessionHandle | Mapping[str, Any]],
]
FetchSummaryFunc = Callable[
    [FetchCodexManagedSessionSummaryRequest],
    Awaitable[CodexManagedSessionSummary | Mapping[str, Any]],
]
PublishArtifactsFunc = Callable[
    [PublishCodexManagedSessionArtifactsRequest],
    Awaitable[CodexManagedSessionArtifactsPublication | Mapping[str, Any]],
]


class CodexSessionExecutionState(BaseModel):
    """Persisted step-scoped execution state for one session-backed managed run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(..., alias="runId")
    workflow_id: str = Field(..., alias="workflowId")
    agent_id: str = Field(..., alias="agentId")
    runtime_id: str = Field(..., alias="runtimeId")
    status: AgentRunState = Field(..., alias="status")
    started_at: datetime = Field(..., alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    locator: CodexManagedSessionLocator = Field(..., alias="locator")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    profile_id: str | None = Field(None, alias="profileId")
    result: AgentRunResult = Field(..., alias="result")


class CodexSessionAdapter(ManagedAgentAdapter):
    """Managed-session-backed ``AgentAdapter`` for Codex."""

    def __init__(
        self,
        *,
        load_session_snapshot: SessionSnapshotLoader,
        launch_session: LaunchSessionFunc,
        session_status: SessionStatusFunc,
        send_turn: SendTurnFunc,
        interrupt_turn: InterruptTurnFunc,
        clear_remote_session: ClearSessionFunc,
        terminate_remote_session: TerminateSessionFunc,
        fetch_remote_summary: FetchSummaryFunc,
        publish_remote_artifacts: PublishArtifactsFunc,
        attach_runtime_handles: SessionHandleSignaler,
        apply_session_control_action: SessionControlSignaler,
        workspace_root: str,
        session_image_ref: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._load_session_snapshot = load_session_snapshot
        self._launch_session = launch_session
        self._session_status = session_status
        self._send_turn = send_turn
        self._interrupt_turn = interrupt_turn
        self._clear_remote_session = clear_remote_session
        self._terminate_remote_session = terminate_remote_session
        self._fetch_remote_summary = fetch_remote_summary
        self._publish_remote_artifacts = publish_remote_artifacts
        self._attach_runtime_handles = attach_runtime_handles
        self._apply_session_control_action = apply_session_control_action
        self._workspace_root = Path(workspace_root).resolve()
        self._session_image_ref = str(session_image_ref).strip()
        if self._run_store is None:
            raise ValueError("CodexSessionAdapter requires run_store")

    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        binding = self._require_binding(request)
        runtime_id = self._runtime_id or canonical_codex_managed_runtime_id(request.agent_id)
        if runtime_id is None:
            raise ValueError("CodexSessionAdapter only supports managed Codex runtimes")
        profile = await self._resolve_profile(
            execution_profile_ref=request.execution_profile_ref,
            runtime_id=runtime_id,
            profile_selector=(
                request.profile_selector.model_dump(by_alias=True, exclude_none=True)
                if request.profile_selector
                else None
            ),
        )
        default_credential_source = "oauth_volume"
        launch_context = build_managed_profile_launch_context(
            profile=profile,
            runtime_for_profile=runtime_id,
            workflow_id=self._workflow_id,
            default_credential_source=default_credential_source,
        )
        self._active_profile_id = launch_context.profile_id or None

        run_id = str(uuid4())
        started_at = datetime.now(tz=UTC)
        session_handle = await self._ensure_remote_session(
            binding=binding,
            request=request,
            environment=launch_context.delta_env_overrides,
        )
        locator = self._locator_from_state(
            session_state=session_handle.session_state,
            runtime_epoch=session_handle.session_state.session_epoch,
        )
        instructions = await self._instructions_for_request(
            binding=binding,
            request=request,
        )
        turn_response = await self._coerce_turn_response(
            self._send_turn(
                SendCodexManagedSessionTurnRequest(
                    sessionId=locator.session_id,
                    sessionEpoch=locator.session_epoch,
                    containerId=locator.container_id,
                    threadId=locator.thread_id,
                    instructions=instructions,
                    inputRefs=tuple(request.input_refs),
                    metadata={
                        "correlationId": request.correlation_id,
                        "idempotencyKey": request.idempotency_key,
                    },
                )
            )
        )
        await self._signal_control_action(
            action="send_turn",
            reason=None,
            container_id=turn_response.session_state.container_id,
            thread_id=turn_response.session_state.thread_id,
            active_turn_id=turn_response.turn_id,
        )

        current_locator = self._locator_from_state(
            session_state=turn_response.session_state,
            runtime_epoch=turn_response.session_state.session_epoch,
        )
        summary = await self.fetch_session_summary(binding=binding, locator=current_locator)
        publication = await self._coerce_publication(
            self._publish_remote_artifacts(
                PublishCodexManagedSessionArtifactsRequest(
                    sessionId=current_locator.session_id,
                    sessionEpoch=current_locator.session_epoch,
                    containerId=current_locator.container_id,
                    threadId=current_locator.thread_id,
                    taskRunId=binding.task_run_id,
                    metadata={"runId": run_id, "workflowId": self._workflow_id},
                )
            )
        )

        assistant_text = str(
            turn_response.metadata.get("assistantText")
            or summary.metadata.get("lastAssistantText")
            or ""
        ).strip() or "Codex managed-session turn completed."
        output_refs = self._merge_output_refs(
            turn_response.output_refs,
            publication.published_artifact_refs,
        )
        result = AgentRunResult(
            outputRefs=output_refs,
            summary=assistant_text,
            metadata={
                "sessionSummary": summary.model_dump(mode="json", by_alias=True),
                "sessionArtifacts": publication.model_dump(mode="json", by_alias=True),
                "turnId": turn_response.turn_id,
            },
        )
        finished_at = datetime.now(tz=UTC)
        self._save_run_state(
            run_id=run_id,
            agent_id=request.agent_id,
            locator=current_locator.model_dump(mode="json", by_alias=True),
            active_turn_id=None,
            result=result.model_dump(mode="json", by_alias=True),
            status="completed",
            started_at=started_at,
            finished_at=finished_at,
            profile_id=launch_context.profile_id or None,
        )
        self._run_store.save(
            ManagedRunRecord(
                runId=run_id,
                workflowId=self._workflow_id,
                agentId=request.agent_id,
                runtimeId=runtime_id,
                status="completed",
                startedAt=started_at,
                finishedAt=finished_at,
                workspacePath=self._workspace_path_for_request(
                    binding=binding,
                    request=request,
                ),
                errorMessage=assistant_text,
            )
        )
        return AgentRunHandle(
            runId=run_id,
            agentKind="managed",
            agentId=request.agent_id,
            status="completed",
            startedAt=started_at,
            metadata={
                "profile_id": launch_context.profile_id,
                "credential_source": launch_context.credential_source,
                "env_keys_count": len(launch_context.shaped_env),
                "sessionId": current_locator.session_id,
                "sessionEpoch": current_locator.session_epoch,
                "containerId": current_locator.container_id,
            },
        )

    async def status(self, run_id: str) -> AgentRunStatus:
        state = self._load_run_state(run_id)
        if state is not None:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId=state.agent_id,
                status=state.status,
                metadata={
                    "runtimeId": state.runtime_id,
                    "sessionId": state.locator.session_id,
                    "containerId": state.locator.container_id,
                },
            )
        record = self._run_store.load(run_id)
        if record is not None:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId=record.agent_id,
                status=record.status,
                metadata={"runtimeId": record.runtime_id},
            )
        return AgentRunStatus(
            runId=run_id,
            agentKind="managed",
            agentId="codex",
            status="running",
        )

    async def fetch_result(
        self,
        run_id: str,
        *,
        pr_resolver_expected: bool = False,
    ) -> AgentRunResult:
        del pr_resolver_expected
        state = self._load_run_state(run_id)
        if state is not None:
            return state.result
        return AgentRunResult(
            summary="Codex managed-session result not found.",
            failureClass="execution_error",
        )

    async def cancel(self, run_id: str) -> AgentRunStatus:
        state = self._load_run_state(run_id)
        if state is None:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="canceled",
            )
        if state.active_turn_id:
            response = await self._coerce_turn_response(
                self._interrupt_turn(
                    InterruptCodexManagedSessionTurnRequest(
                        sessionId=state.locator.session_id,
                        sessionEpoch=state.locator.session_epoch,
                        containerId=state.locator.container_id,
                        threadId=state.locator.thread_id,
                        turnId=state.active_turn_id,
                        reason="step canceled",
                    )
                )
            )
            await self._signal_control_action(
                action="interrupt_turn",
                reason="step canceled",
                container_id=response.session_state.container_id,
                thread_id=response.session_state.thread_id,
                active_turn_id=response.turn_id,
            )

        canceled_result = AgentRunResult(
            summary="Canceled Codex managed-session turn.",
            failureClass="user_error",
            metadata=state.result.metadata,
        )
        finished_at = datetime.now(tz=UTC)
        self._save_run_state(
            run_id=state.run_id,
            agent_id=state.agent_id,
            locator=state.locator.model_dump(mode="json", by_alias=True),
            active_turn_id=None,
            result=canceled_result.model_dump(mode="json", by_alias=True),
            status="canceled",
            started_at=state.started_at,
            finished_at=finished_at,
            profile_id=state.profile_id,
        )
        record = self._run_store.load(run_id)
        if record is None:
            self._run_store.save(
                ManagedRunRecord(
                    runId=run_id,
                    workflowId=self._workflow_id,
                    agentId=state.agent_id,
                    runtimeId=state.runtime_id,
                    status="canceled",
                    startedAt=state.started_at,
                    finishedAt=finished_at,
                    errorMessage=canceled_result.summary,
                    failureClass=canceled_result.failure_class,
                )
            )
        else:
            self._run_store.update_status(
                run_id,
                "canceled",
                finished_at=finished_at,
                error_message=canceled_result.summary,
                failure_class=canceled_result.failure_class,
            )
        return AgentRunStatus(
            runId=run_id,
            agentKind="managed",
            agentId=state.agent_id,
            status="canceled",
            metadata={"runtimeId": state.runtime_id},
        )

    async def clear_session(
        self,
        *,
        binding: CodexManagedSessionBinding,
        new_thread_id: str,
        reason: str | None = None,
    ) -> CodexManagedSessionHandle:
        locator = await self._current_locator(binding)
        handle = await self._coerce_handle(
            self._clear_remote_session(
                CodexManagedSessionClearRequest(
                    sessionId=locator.session_id,
                    sessionEpoch=locator.session_epoch,
                    containerId=locator.container_id,
                    threadId=locator.thread_id,
                    newThreadId=new_thread_id,
                    reason=reason,
                )
            )
        )
        await self._attach_runtime_handles(
            {
                "containerId": handle.session_state.container_id,
                "threadId": handle.session_state.thread_id,
            }
        )
        await self._signal_control_action(
            action="clear_session",
            reason=reason,
            container_id=handle.session_state.container_id,
            thread_id=handle.session_state.thread_id,
        )
        return handle

    async def interrupt_turn(
        self,
        *,
        binding: CodexManagedSessionBinding,
        turn_id: str,
        reason: str | None = None,
    ) -> CodexManagedSessionTurnResponse:
        locator = await self._current_locator(binding)
        response = await self._coerce_turn_response(
            self._interrupt_turn(
                InterruptCodexManagedSessionTurnRequest(
                    sessionId=locator.session_id,
                    sessionEpoch=locator.session_epoch,
                    containerId=locator.container_id,
                    threadId=locator.thread_id,
                    turnId=turn_id,
                    reason=reason,
                )
            )
        )
        await self._signal_control_action(
            action="interrupt_turn",
            reason=reason,
            container_id=response.session_state.container_id,
            thread_id=response.session_state.thread_id,
            active_turn_id=response.turn_id,
        )
        return response

    async def fetch_session_summary(
        self,
        *,
        binding: CodexManagedSessionBinding,
        locator: CodexManagedSessionLocator | None = None,
    ) -> CodexManagedSessionSummary:
        active_locator = locator or await self._current_locator(binding)
        return await self._coerce_summary(
            self._fetch_remote_summary(
                FetchCodexManagedSessionSummaryRequest(
                    sessionId=active_locator.session_id,
                    sessionEpoch=active_locator.session_epoch,
                    containerId=active_locator.container_id,
                    threadId=active_locator.thread_id,
                )
            )
        )

    async def terminate_session(
        self,
        *,
        binding: CodexManagedSessionBinding,
        reason: str | None = None,
    ) -> CodexManagedSessionHandle:
        locator = await self._current_locator(binding)
        handle = await self._coerce_handle(
            self._terminate_remote_session(
                TerminateCodexManagedSessionRequest(
                    sessionId=locator.session_id,
                    sessionEpoch=locator.session_epoch,
                    containerId=locator.container_id,
                    threadId=locator.thread_id,
                    reason=reason,
                )
            )
        )
        await self._apply_session_control_action(
            {
                "action": "terminate_session",
                "reason": reason,
            }
        )
        return handle

    async def _instructions_for_request(
        self,
        *,
        binding: CodexManagedSessionBinding,
        request: AgentExecutionRequest,
    ) -> str:
        workspace_path = Path(self._workspace_path_for_request(binding=binding, request=request))
        if str(request.instruction_ref or "").strip():
            from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
                CodexCliStrategy,
            )

            await CodexCliStrategy().prepare_workspace(workspace_path, request)
        instruction_ref = str(request.instruction_ref or "").strip()
        if instruction_ref:
            return instruction_ref
        parameters = request.parameters if isinstance(request.parameters, dict) else {}
        instructions = str(parameters.get("instructions") or "").strip()
        if instructions:
            return instructions
        raise ValueError("CodexSessionAdapter requires instructionRef or parameters.instructions")

    def _require_binding(self, request: AgentExecutionRequest) -> CodexManagedSessionBinding:
        binding = request.managed_session
        if binding is None:
            raise ValueError("CodexSessionAdapter requires request.managed_session")
        return binding

    async def _ensure_remote_session(
        self,
        *,
        binding: CodexManagedSessionBinding,
        request: AgentExecutionRequest,
        environment: dict[str, str],
    ) -> CodexManagedSessionHandle:
        snapshot = await self._load_snapshot(binding.workflow_id)
        if snapshot.container_id and snapshot.thread_id:
            return await self._coerce_handle(
                self._session_status(
                    CodexManagedSessionLocator(
                        sessionId=binding.session_id,
                        sessionEpoch=snapshot.binding.session_epoch,
                        containerId=snapshot.container_id,
                        threadId=snapshot.thread_id,
                    )
                )
            )

        launch_request = LaunchCodexManagedSessionRequest(
            taskRunId=binding.task_run_id,
            workflowId=self._workflow_id,
            sessionId=binding.session_id,
            sessionEpoch=binding.session_epoch,
            threadId=self._default_thread_id(binding),
            workspacePath=self._workspace_path_for_request(binding=binding, request=request),
            sessionWorkspacePath=str(self._session_root(binding) / "session"),
            artifactSpoolPath=str(self._session_root(binding) / "artifacts"),
            codexHomePath=str(self._session_root(binding) / ".moonmind" / "codex-home"),
            imageRef=self._session_image_ref,
            environment=environment,
        )
        handle = await self._coerce_handle(self._launch_session(launch_request))
        await self._attach_runtime_handles(
            {
                "containerId": handle.session_state.container_id,
                "threadId": handle.session_state.thread_id,
            }
        )
        return handle

    async def _current_locator(
        self, binding: CodexManagedSessionBinding
    ) -> CodexManagedSessionLocator:
        snapshot = await self._load_snapshot(binding.workflow_id)
        if not snapshot.container_id or not snapshot.thread_id:
            raise ValueError("Task-scoped managed session has no runtime handles yet")
        return CodexManagedSessionLocator(
            sessionId=binding.session_id,
            sessionEpoch=snapshot.binding.session_epoch,
            containerId=snapshot.container_id,
            threadId=snapshot.thread_id,
        )

    async def _signal_control_action(
        self,
        *,
        action: str,
        reason: str | None,
        container_id: str | None,
        thread_id: str | None,
        active_turn_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"action": action}
        if reason is not None:
            payload["reason"] = reason
        if container_id is not None:
            payload["containerId"] = container_id
        if thread_id is not None:
            payload["threadId"] = thread_id
        if active_turn_id is not None:
            payload["activeTurnId"] = active_turn_id
        await self._apply_session_control_action(payload)

    def _workspace_path_for_request(
        self,
        *,
        binding: CodexManagedSessionBinding,
        request: AgentExecutionRequest,
    ) -> str:
        workspace_spec = request.workspace_spec if isinstance(request.workspace_spec, dict) else {}
        for key in (
            "workspacePath",
            "workspace_path",
            "path",
            "repoPath",
            "repo_path",
        ):
            raw_value = workspace_spec.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                return raw_value.strip()
        return str(self._session_root(binding) / "repo")

    def _session_root(self, binding: CodexManagedSessionBinding) -> Path:
        return self._workspace_root / binding.task_run_id

    def _default_thread_id(self, binding: CodexManagedSessionBinding) -> str:
        return f"thread:{binding.session_id}:{binding.session_epoch}"

    def _state_path(self, run_id: str) -> Path:
        suffix_id = f"{run_id}.codex-session"
        return self._run_store._resolve_path(suffix_id)  # type: ignore[attr-defined]

    def _load_run_state(self, run_id: str) -> CodexSessionExecutionState | None:
        path = self._state_path(run_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CodexSessionExecutionState.model_validate(payload)

    def _persist_state(self, state: CodexSessionExecutionState) -> None:
        path = self._state_path(state.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.model_dump(mode="json", by_alias=True), handle)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _save_run_state(
        self,
        *,
        run_id: str,
        agent_id: str,
        locator: Mapping[str, Any],
        active_turn_id: str | None,
        result: Mapping[str, Any],
        status: AgentRunState,
        started_at: datetime,
        finished_at: datetime | None = None,
        profile_id: str | None = None,
    ) -> None:
        self._persist_state(
            CodexSessionExecutionState(
                runId=run_id,
                workflowId=self._workflow_id,
                agentId=agent_id,
                runtimeId=self._runtime_id or "codex_cli",
                status=status,
                startedAt=started_at,
                finishedAt=finished_at,
                locator=locator,
                activeTurnId=active_turn_id,
                profileId=profile_id,
                result=result,
            )
        )

    def _merge_output_refs(self, *groups: Any) -> list[str]:
        seen: list[str] = []
        for group in groups:
            for raw_ref in group or ():
                ref = str(raw_ref).strip()
                if ref and ref not in seen:
                    seen.append(ref)
        return seen

    def _locator_from_state(
        self,
        *,
        session_state: Any,
        runtime_epoch: int,
    ) -> CodexManagedSessionLocator:
        return CodexManagedSessionLocator(
            sessionId=session_state.session_id,
            sessionEpoch=runtime_epoch,
            containerId=session_state.container_id,
            threadId=session_state.thread_id,
        )

    async def _load_snapshot(self, workflow_id: str) -> CodexManagedSessionSnapshot:
        payload = await self._load_session_snapshot(workflow_id)
        return (
            payload
            if isinstance(payload, CodexManagedSessionSnapshot)
            else CodexManagedSessionSnapshot.model_validate(payload)
        )

    async def _coerce_handle(
        self,
        awaited: Awaitable[CodexManagedSessionHandle | Mapping[str, Any]],
    ) -> CodexManagedSessionHandle:
        payload = await awaited
        return (
            payload
            if isinstance(payload, CodexManagedSessionHandle)
            else CodexManagedSessionHandle.model_validate(payload)
        )

    async def _coerce_turn_response(
        self,
        awaited: Awaitable[CodexManagedSessionTurnResponse | Mapping[str, Any]],
    ) -> CodexManagedSessionTurnResponse:
        payload = await awaited
        return (
            payload
            if isinstance(payload, CodexManagedSessionTurnResponse)
            else CodexManagedSessionTurnResponse.model_validate(payload)
        )

    async def _coerce_summary(
        self,
        awaited: Awaitable[CodexManagedSessionSummary | Mapping[str, Any]],
    ) -> CodexManagedSessionSummary:
        payload = await awaited
        return (
            payload
            if isinstance(payload, CodexManagedSessionSummary)
            else CodexManagedSessionSummary.model_validate(payload)
        )

    async def _coerce_publication(
        self,
        awaited: Awaitable[CodexManagedSessionArtifactsPublication | Mapping[str, Any]],
    ) -> CodexManagedSessionArtifactsPublication:
        payload = await awaited
        return (
            payload
            if isinstance(payload, CodexManagedSessionArtifactsPublication)
            else CodexManagedSessionArtifactsPublication.model_validate(payload)
        )


__all__ = ["CodexSessionAdapter", "CodexSessionExecutionState"]
