"""Workflow-side adapter for managed Codex task-scoped sessions."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunState,
    AgentRunStatus,
    ManagedRunRecord,
    ManagedRuntimeProfile,
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
from moonmind.schemas.temporal_payload_policy import compact_temporal_ref_metadata
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
    ManagedProfileLaunchContext,
    _derive_pr_resolver_failure,
    _derive_pr_resolver_metadata,
    _current_time,
    _generate_run_id,
    _is_generic_process_exit_summary,
    build_managed_profile_launch_context,
    default_credential_source_for_runtime,
)
from moonmind.workflows.agent_skills.selection import selected_agent_skill
from moonmind.workflows.codex_session_timeouts import (
    MAX_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS,
)
from moonmind.workflows.provider_failures import classify_provider_failure
from moonmind.workflows.tasks.runtime_defaults import resolve_runtime_defaults
from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
    append_managed_codex_runtime_note,
)

SessionSnapshotLoader = Callable[
    [str], Awaitable[CodexManagedSessionSnapshot | Mapping[str, Any]]
]
SessionHandleSignaler = Callable[[dict[str, Any]], Awaitable[None]]
SessionControlSignaler = Callable[[dict[str, Any]], Awaitable[None]]
LaunchSessionFunc = Callable[
    [Mapping[str, Any] | LaunchCodexManagedSessionRequest],
    Awaitable[CodexManagedSessionHandle | Mapping[str, Any]],
]
SessionStatusFunc = Callable[
    [CodexManagedSessionLocator], Awaitable[CodexManagedSessionHandle | Mapping[str, Any]]
]
PrepareTurnInstructionsFunc = Callable[[dict[str, Any]], Awaitable[str | Mapping[str, Any]]]
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

_MAX_AGENT_RUN_RESULT_SUMMARY_CHARS = 4096
_JIRA_CREATED_ISSUE_KEYS_PATTERN = re.compile(
    r"\b(?:created\s+(?:jira\s+)?(?:issues?|stories?|tickets?)|"
    r"created\s+(?:issue\s+)?keys?|issue\s+keys?\s+created)\b"
    r"[\s\S]{0,240}?\b[A-Z][A-Z0-9]+-\d+\b",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)

def _result_ref_metadata(
    *,
    instruction_ref: str | None,
    resolved_skillset_ref: str | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    metadata.update(compact_temporal_ref_metadata("instructionRef", instruction_ref))
    metadata.update(
        compact_temporal_ref_metadata("resolvedSkillsetRef", resolved_skillset_ref)
    )
    return metadata

def _clamp_agent_run_result_summary(summary: Any, *, default: str) -> str:
    normalized = str(summary or "").strip() or default
    if len(normalized) <= _MAX_AGENT_RUN_RESULT_SUMMARY_CHARS:
        return normalized
    truncated = normalized[:_MAX_AGENT_RUN_RESULT_SUMMARY_CHARS].rstrip()
    return truncated or normalized[:_MAX_AGENT_RUN_RESULT_SUMMARY_CHARS]

def _jira_skill_blocker_summary(
    *,
    parameters: Mapping[str, Any] | None,
    assistant_text: str,
) -> str | None:
    selected_skill = selected_agent_skill(parameters)
    if selected_skill not in {"jira-issue-creator", "jira-pr-verify", "jira-verify"}:
        return None
    normalized = " ".join(str(assistant_text or "").lower().split())
    if not normalized:
        return None
    if selected_skill == "jira-issue-creator":
        if _JIRA_CREATED_ISSUE_KEYS_PATTERN.search(str(assistant_text or "")):
            return None
        blocker_markers = (
            "no jira issues were created",
            "could not create the jira",
            "could not create jira",
            "jira access is not configured",
            "jira_auth_failed",
            "jira tools are not enabled",
            "jira tool calls are unavailable",
            "no jira mcp connector/tool is available",
        )
        default_summary = "Jira issue creation was blocked."
    elif selected_skill == "jira-pr-verify":
        blocker_markers = (
            "could not read the jira issue body",
            "could not fetch the jira issue",
            "could not access jira",
            "jira issue body is unavailable",
            "jira content is not already available",
            "no authenticated jira session",
            "without an authenticated jira session",
            "trusted jira content is unavailable",
            "jira access is not configured",
            "jira_auth_failed",
            "jira tools are not enabled",
            "jira tool calls are unavailable",
            "no jira mcp connector/tool is available",
        )
        default_summary = "Jira PR verification was blocked."
    else:
        blocker_markers = (
            "could not read the jira issue body",
            "could not fetch the jira issue",
            "could not access jira",
            "could not post the jira comment",
            "could not add the jira comment",
            "jira.add_comment failed",
            "jira comment cannot be posted",
            "jira issue body is unavailable",
            "no authenticated jira session",
            "without an authenticated jira session",
            "trusted jira content is unavailable",
            "jira access is not configured",
            "jira_auth_failed",
            "jira tools are not enabled",
            "jira tool calls are unavailable",
            "no jira mcp connector/tool is available",
        )
        default_summary = "Jira verification was blocked."
    if any(marker in normalized for marker in blocker_markers):
        return _clamp_agent_run_result_summary(
            assistant_text,
            default=default_summary,
        )
    return None

class CodexSessionRunFailedError(RuntimeError):
    """Raised when a Codex session run persisted a structured failed result."""

    def __init__(self, message: str, *, result: AgentRunResult) -> None:
        super().__init__(message)
        self.agent_run_result = result

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
    managed_run_id: str | None = Field(None, alias="managedRunId")
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
        prepare_turn_instructions: PrepareTurnInstructionsFunc | None,
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
        self._prepare_turn_instructions = prepare_turn_instructions
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
        self._run_states: dict[str, CodexSessionExecutionState] = {}

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
        default_credential_source = default_credential_source_for_runtime(runtime_id)
        if self._launch_context_builder is not None:
            built_context = await self._launch_context_builder(
                profile=profile,
                runtime_for_profile=runtime_id,
                workflow_id=self._workflow_id,
                default_credential_source=default_credential_source,
            )
            launch_context = (
                built_context
                if isinstance(built_context, ManagedProfileLaunchContext)
                else ManagedProfileLaunchContext(**built_context)
            )
        else:
            launch_context = build_managed_profile_launch_context(
                profile=profile,
                runtime_for_profile=runtime_id,
                workflow_id=self._workflow_id,
                default_credential_source=default_credential_source,
            )
        self._active_profile_id = launch_context.profile_id or None

        run_id = _generate_run_id()
        started_at = _current_time()
        original_instruction_ref = str(request.instruction_ref or "").strip() or None
        original_skillset_ref = str(request.resolved_skillset_ref or "").strip() or None
        workspace_path = self._workspace_path_for_request(
            binding=binding,
            request=request,
        )
        if request.input_refs:
            raise ValueError(
                "CodexSessionAdapter does not support inputRefs for managed session turns"
            )
        session_handle = await self._ensure_remote_session(
            binding=binding,
            request=request,
            workspace_path=workspace_path,
            environment=launch_context.delta_env_overrides,
            profile=self._profile_for_launch(
                runtime_id=runtime_id,
                profile=profile,
                launch_context=launch_context,
            ),
        )
        locator = self._locator_from_state(
            session_state=session_handle.session_state,
            runtime_epoch=session_handle.session_state.session_epoch,
        )
        initial_result = AgentRunResult(
            outputRefs=[],
            summary=None,
            metadata=_result_ref_metadata(
                instruction_ref=original_instruction_ref,
                resolved_skillset_ref=original_skillset_ref,
            ),
        )
        self._save_run_state(
            run_id=run_id,
            agent_id=request.agent_id,
            managed_run_id=binding.task_run_id,
            binding=binding,
            workspace_path=workspace_path,
            locator=locator.model_dump(mode="json", by_alias=True),
            active_turn_id=session_handle.session_state.active_turn_id,
            result=initial_result.model_dump(mode="json", by_alias=True),
            status="running",
            started_at=started_at,
            finished_at=None,
            profile_id=launch_context.profile_id or None,
        )

        current_locator = locator
        current_active_turn_id = session_handle.session_state.active_turn_id
        turn_id: str | None = None
        failed_state_persisted = False

        try:
            instructions = await self._instructions_for_request(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
            )
            turn_response = await self._coerce_turn_response(
                self._send_turn(
                    SendCodexManagedSessionTurnRequest(
                        sessionId=current_locator.session_id,
                        sessionEpoch=current_locator.session_epoch,
                        containerId=current_locator.container_id,
                        threadId=current_locator.thread_id,
                        instructions=instructions,
                    )
                )
            )
            turn_id = turn_response.turn_id
            current_locator = self._locator_from_state(
                session_state=turn_response.session_state,
                runtime_epoch=turn_response.session_state.session_epoch,
            )
            current_active_turn_id = turn_response.session_state.active_turn_id
            if turn_response.status != "completed":
                raw_reason = turn_response.metadata.get("reason")
                reason = _clamp_agent_run_result_summary(
                    raw_reason,
                    default=(
                        "Codex managed-session turn failed"
                        f" with status '{turn_response.status}'"
                    ),
                )
                publication = await self._publish_failure_artifacts(
                    locator=current_locator,
                    managed_run_id=binding.task_run_id,
                    run_id=run_id,
                )
                failure_result = self._persist_failed_run_state(
                    run_id=run_id,
                    agent_id=request.agent_id,
                    managed_run_id=binding.task_run_id,
                    binding=binding,
                    workspace_path=workspace_path,
                    locator=current_locator.model_dump(mode="json", by_alias=True),
                    active_turn_id=current_active_turn_id,
                    summary=raw_reason,
                    default_summary=reason,
                    output_refs=self._merge_output_refs(
                        turn_response.output_refs,
                        (
                            publication.published_artifact_refs
                            if publication is not None
                            else ()
                        ),
                    ),
                    started_at=started_at,
                    finished_at=_current_time(),
                    instruction_ref=original_instruction_ref,
                    resolved_skillset_ref=original_skillset_ref,
                    turn_id=turn_id,
                    profile_id=launch_context.profile_id or None,
                    session_artifacts=(
                        publication.model_dump(mode="json", by_alias=True)
                        if publication is not None
                        else None
                    ),
                    turn_status=turn_response.status,
                    turn_metadata=turn_response.metadata,
                )
                failed_state_persisted = True
                raise CodexSessionRunFailedError(reason, result=failure_result)

            publication: CodexManagedSessionArtifactsPublication | None = None
            try:
                await self._signal_control_action(
                    action="send_turn",
                    reason=None,
                    container_id=turn_response.session_state.container_id,
                    thread_id=turn_response.session_state.thread_id,
                )
                summary = await self.fetch_session_summary(
                    binding=binding,
                    locator=current_locator,
                )
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

                assistant_text = _clamp_agent_run_result_summary(
                    turn_response.metadata.get("assistantText")
                    or summary.metadata.get("lastAssistantText"),
                    default="Codex managed-session turn completed.",
                )
                output_refs = self._merge_output_refs(
                    turn_response.output_refs,
                    publication.published_artifact_refs,
                )
                result = AgentRunResult(
                    outputRefs=output_refs,
                    summary=assistant_text,
                    metadata={
                        **_result_ref_metadata(
                            instruction_ref=original_instruction_ref,
                            resolved_skillset_ref=original_skillset_ref,
                        ),
                        "sessionSummary": summary.model_dump(mode="json", by_alias=True),
                        "sessionArtifacts": publication.model_dump(mode="json", by_alias=True),
                        "turnId": turn_id,
                    },
                )
                jira_blocker_summary = _jira_skill_blocker_summary(
                    parameters=request.parameters,
                    assistant_text=assistant_text,
                )
                if jira_blocker_summary is not None:
                    result = result.model_copy(
                        update={
                            "summary": jira_blocker_summary,
                            "failure_class": "execution_error",
                        }
                    )
            except Exception as exc:
                failure_result = self._persist_failed_run_state(
                    run_id=run_id,
                    agent_id=request.agent_id,
                    managed_run_id=binding.task_run_id,
                    binding=binding,
                    workspace_path=workspace_path,
                    locator=current_locator.model_dump(mode="json", by_alias=True),
                    active_turn_id=current_active_turn_id,
                    summary=exc,
                    default_summary="Codex managed-session turn failed",
                    output_refs=self._merge_output_refs(
                        turn_response.output_refs,
                        publication.published_artifact_refs if publication is not None else (),
                    ),
                    started_at=started_at,
                    finished_at=_current_time(),
                    instruction_ref=original_instruction_ref,
                    resolved_skillset_ref=original_skillset_ref,
                    turn_id=turn_id,
                    profile_id=launch_context.profile_id or None,
                    session_artifacts=(
                        publication.model_dump(mode="json", by_alias=True)
                        if publication is not None
                        else None
                    ),
                )
                failed_state_persisted = True
                raise CodexSessionRunFailedError(str(exc), result=failure_result) from exc

            if result.failure_class is not None:
                self._save_run_state(
                    run_id=run_id,
                    agent_id=request.agent_id,
                    managed_run_id=binding.task_run_id,
                    binding=binding,
                    workspace_path=workspace_path,
                    locator=current_locator.model_dump(mode="json", by_alias=True),
                    active_turn_id=None,
                    result=result.model_dump(mode="json", by_alias=True),
                    status="failed",
                    started_at=started_at,
                    finished_at=_current_time(),
                    profile_id=launch_context.profile_id or None,
                )
                failed_state_persisted = True
                raise CodexSessionRunFailedError(result.summary, result=result)

            self._save_run_state(
                run_id=run_id,
                agent_id=request.agent_id,
                managed_run_id=binding.task_run_id,
                binding=binding,
                workspace_path=workspace_path,
                locator=current_locator.model_dump(mode="json", by_alias=True),
                active_turn_id=None,
                result=result.model_dump(mode="json", by_alias=True),
                status="completed",
                started_at=started_at,
                finished_at=_current_time(),
                profile_id=launch_context.profile_id or None,
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
                    "env_keys_count": launch_context.env_keys_count,
                    "sessionId": current_locator.session_id,
                    "sessionEpoch": current_locator.session_epoch,
                    "containerId": current_locator.container_id,
                },
            )
        except Exception as exc:
            if not failed_state_persisted:
                failure_result = self._persist_failed_run_state(
                    run_id=run_id,
                    agent_id=request.agent_id,
                    managed_run_id=binding.task_run_id,
                    binding=binding,
                    workspace_path=workspace_path,
                    locator=current_locator.model_dump(mode="json", by_alias=True),
                    active_turn_id=current_active_turn_id,
                    summary=exc,
                    default_summary="Codex managed-session turn failed",
                    started_at=started_at,
                    finished_at=_current_time(),
                    instruction_ref=original_instruction_ref,
                    resolved_skillset_ref=original_skillset_ref,
                    turn_id=turn_id,
                    profile_id=launch_context.profile_id or None,
                )
                raise CodexSessionRunFailedError(
                    str(exc) or "Codex managed-session turn failed",
                    result=failure_result,
                ) from exc
            raise

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
        state = self._load_run_state(run_id)
        if state is not None:
            result = state.result
            if self._run_store is not None and pr_resolver_expected:
                record = self._run_store.load(run_id)
                if record is not None:
                    failure_class = result.failure_class
                    summary = str(result.summary or "").strip()
                    metadata = dict(result.metadata)
                    metadata.update(_derive_pr_resolver_metadata(record.workspace_path))
                    derived_failure_class, derived_summary = (
                        _derive_pr_resolver_failure(record.workspace_path)
                    )
                    if derived_failure_class is not None:
                        should_apply_derived = False
                        if record.status == "completed" and failure_class is None:
                            should_apply_derived = True
                        elif (
                            record.status == "failed"
                            and failure_class in {None, "execution_error"}
                            and _is_generic_process_exit_summary(summary)
                        ):
                            should_apply_derived = True
                        if should_apply_derived:
                            result = result.model_copy(
                                update={
                                    "failure_class": derived_failure_class,
                                    "summary": derived_summary or summary,
                                    "metadata": metadata,
                                }
                            )
                        elif metadata != result.metadata:
                            result = result.model_copy(update={"metadata": metadata})
                    elif metadata != result.metadata:
                        result = result.model_copy(update={"metadata": metadata})
            return result
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
        finished_at = _current_time()
        self._save_run_state(
            run_id=state.run_id,
            agent_id=state.agent_id,
            managed_run_id=state.managed_run_id,
            binding=None,
            workspace_path=None,
            locator=state.locator.model_dump(mode="json", by_alias=True),
            active_turn_id=None,
            result=canceled_result.model_dump(mode="json", by_alias=True),
            status="canceled",
            started_at=state.started_at,
            finished_at=finished_at,
            profile_id=state.profile_id,
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
        workspace_path: str,
    ) -> str:
        if self._prepare_turn_instructions is None:
            return await self._legacy_instructions_for_request(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
            )
        prepared = await self._prepare_turn_instructions(
            {
                "request": request.model_dump(by_alias=True, exclude_none=True),
                "workspacePath": workspace_path,
            }
        )
        if isinstance(prepared, Mapping):
            prepared = prepared.get("instructions")
        instructions = str(prepared or "").strip()
        if instructions:
            return instructions
        raise ValueError(
            "CodexSessionAdapter requires prepare_turn_instructions to return non-empty text"
        )

    async def _legacy_instructions_for_request(
        self,
        *,
        binding: CodexManagedSessionBinding,
        request: AgentExecutionRequest,
        workspace_path: str,
    ) -> str:
        instruction_ref = str(request.instruction_ref or "").strip()
        if instruction_ref:
            from moonmind.rag.context_injection import ContextInjectionService

            workspace_path = Path(workspace_path)
            service = ContextInjectionService()
            await service.inject_context(
                request=request,
                workspace_path=workspace_path,
            )
            return append_managed_codex_runtime_note(
                str(request.instruction_ref or instruction_ref).strip()
            )
        parameters = request.parameters if isinstance(request.parameters, dict) else {}
        instructions = str(parameters.get("instructions") or "").strip()
        if instructions:
            return append_managed_codex_runtime_note(instructions)
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
        workspace_path: str,
        environment: dict[str, str],
        profile: ManagedRuntimeProfile,
    ) -> CodexManagedSessionHandle:
        snapshot = await self._load_snapshot(binding.workflow_id)
        if snapshot.container_id and snapshot.thread_id:
            handle = await self._coerce_handle(
                self._session_status(
                    CodexManagedSessionLocator(
                        sessionId=binding.session_id,
                        sessionEpoch=snapshot.binding.session_epoch,
                        containerId=snapshot.container_id,
                        threadId=snapshot.thread_id,
                    )
                )
            )
            await self._signal_control_action(
                action="resume_session",
                reason=None,
                container_id=handle.session_state.container_id,
                thread_id=handle.session_state.thread_id,
                active_turn_id=handle.session_state.active_turn_id,
            )
            return handle

        active_binding = snapshot.binding
        turn_completion_timeout_seconds = self._turn_completion_timeout_seconds(
            request=request,
            profile=profile,
        )
        launch_request = LaunchCodexManagedSessionRequest(
            taskRunId=active_binding.task_run_id,
            workflowId=self._workflow_id,
            sessionId=active_binding.session_id,
            sessionEpoch=active_binding.session_epoch,
            threadId=self._default_thread_id(active_binding),
            workspacePath=workspace_path,
            sessionWorkspacePath=str(self._session_root(binding) / "session"),
            artifactSpoolPath=str(self._session_root(binding) / "artifacts"),
            codexHomePath=str(self._session_root(binding) / ".moonmind" / "codex-home"),
            imageRef=self._session_image_ref,
            turnCompletionTimeoutSeconds=turn_completion_timeout_seconds,
            environment=environment,
            workspaceSpec=(
                dict(request.workspace_spec)
                if isinstance(request.workspace_spec, dict)
                else {}
            ),
        )
        launch_payload = {
            "request": launch_request.model_dump(mode="json", by_alias=True),
            "profile": profile.model_dump(mode="json", by_alias=True),
        }
        handle = await self._coerce_handle(self._launch_session(launch_payload))
        await self._attach_runtime_handles(
            {
                "containerId": handle.session_state.container_id,
                "threadId": handle.session_state.thread_id,
            }
        )
        await self._signal_control_action(
            action="start_session",
            reason=None,
            container_id=handle.session_state.container_id,
            thread_id=handle.session_state.thread_id,
            active_turn_id=handle.session_state.active_turn_id,
        )
        return handle

    @staticmethod
    def _turn_completion_timeout_seconds(
        *,
        request: AgentExecutionRequest,
        profile: ManagedRuntimeProfile,
    ) -> int:
        timeout_policy = request.timeout_policy if isinstance(request.timeout_policy, dict) else {}
        raw_timeout = timeout_policy.get("timeout_seconds")
        timeout_seconds: int | None = None
        if raw_timeout is not None:
            try:
                timeout_seconds = int(float(raw_timeout))
            except (TypeError, ValueError, OverflowError):
                timeout_seconds = None
        if timeout_seconds is None or timeout_seconds < 1:
            timeout_seconds = profile.default_timeout_seconds
        # Keep the runtime wait budget inside the fixed Temporal activity budget.
        return min(timeout_seconds, MAX_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS)

    def _profile_for_launch(
        self,
        *,
        runtime_id: str,
        profile: Mapping[str, Any],
        launch_context: ManagedProfileLaunchContext,
    ) -> ManagedRuntimeProfile:
        runtime_default_model, runtime_default_effort = resolve_runtime_defaults(
            runtime_id
        )
        return ManagedRuntimeProfile(
            profileId=launch_context.profile_id,
            runtimeId=runtime_id,
            providerId=profile.get("provider_id"),
            providerLabel=profile.get("provider_label"),
            authMode=profile.get("auth_mode"),
            credentialSource=launch_context.credential_source,
            runtimeMaterializationMode=profile.get("runtime_materialization_mode"),
            commandBehavior=profile.get("command_behavior") or {},
            commandTemplate=profile.get("command_template") or [],
            defaultModel=profile.get("default_model") or runtime_default_model,
            modelOverrides=profile.get("model_overrides") or {},
            defaultEffort=profile.get("default_effort") or runtime_default_effort,
            defaultTimeoutSeconds=profile.get("default_timeout_seconds") or profile.get(
                "defaultTimeoutSeconds", 3600
            ),
            envOverrides=launch_context.delta_env_overrides,
            envTemplate=profile.get("env_template") or {},
            fileTemplates=profile.get("file_templates") or [],
            homePathOverrides=profile.get("home_path_overrides") or {},
            passthroughEnvKeys=launch_context.passthrough_env_keys,
            clearEnvKeys=profile.get("clear_env_keys") or [],
            secretRefs=profile.get("secret_refs") or {},
            volumeRef=profile.get("volume_ref"),
            volumeMountPath=profile.get("volume_mount_path"),
        )

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

    def _load_run_state(self, run_id: str) -> CodexSessionExecutionState | None:
        return self._run_states.get(run_id)

    def _save_run_state(
        self,
        *,
        run_id: str,
        agent_id: str,
        managed_run_id: str | None = None,
        binding: CodexManagedSessionBinding | None = None,
        workspace_path: str | None = None,
        locator: Mapping[str, Any],
        active_turn_id: str | None,
        result: Mapping[str, Any],
        status: AgentRunState,
        started_at: datetime,
        finished_at: datetime | None = None,
        profile_id: str | None = None,
    ) -> None:
        self._run_states[run_id] = CodexSessionExecutionState(
            runId=run_id,
            workflowId=self._workflow_id,
            agentId=agent_id,
            runtimeId=self._runtime_id or "codex_cli",
            status=status,
            startedAt=started_at,
            finishedAt=finished_at,
            managedRunId=managed_run_id,
            locator=locator,
            activeTurnId=active_turn_id,
            profileId=profile_id,
            result=result,
        )
        self._persist_managed_run_record(
            run_id=run_id,
            agent_id=agent_id,
            managed_run_id=managed_run_id,
            binding=binding,
            workspace_path=workspace_path,
            locator=locator,
            active_turn_id=active_turn_id,
            result=result,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _persist_managed_run_record(
        self,
        *,
        run_id: str,
        agent_id: str,
        managed_run_id: str | None,
        binding: CodexManagedSessionBinding | None,
        workspace_path: str | None,
        locator: Mapping[str, Any],
        active_turn_id: str | None,
        result: Mapping[str, Any],
        status: AgentRunState,
        started_at: datetime,
        finished_at: datetime | None,
    ) -> None:
        if self._run_store is None:
            return

        record_key = str(managed_run_id or "").strip() or run_id
        existing = self._run_store.load(record_key)
        session_artifacts = result.get("metadata", {}).get("sessionArtifacts")
        session_artifact_metadata = (
            session_artifacts.get("metadata", {})
            if isinstance(session_artifacts, Mapping)
            else {}
        )
        published_artifact_refs = (
            tuple(session_artifacts.get("publishedArtifactRefs", ()))
            if isinstance(session_artifacts, Mapping)
            else ()
        )

        def _artifact_ref(value: Any, *, fallback: str | None = None) -> str | None:
            normalized = str(value or "").strip()
            return normalized or fallback

        def _infer_runtime_artifact_ref(
            kind: str,
            *,
            fallback: str | None = None,
        ) -> str | None:
            normalized_kind = kind.strip().lower()
            for raw_ref in published_artifact_refs:
                ref = str(raw_ref or "").strip()
                lowered = ref.lower()
                if not ref:
                    continue
                if normalized_kind == "stdout" and "stdout" in lowered:
                    return ref
                if normalized_kind == "stderr" and "stderr" in lowered:
                    return ref
                if normalized_kind == "diagnostics" and "diagnostics" in lowered:
                    return ref
                if (
                    normalized_kind == "observability"
                    and "observability.events" in lowered
                ):
                    return ref
            return fallback

        runtime_id = self._runtime_id or "codex_cli"
        summary = str(result.get("summary") or "").strip() or None
        raw_locator_session_epoch = locator.get("sessionEpoch")
        locator_session_epoch: int | None
        try:
            locator_session_epoch = int(raw_locator_session_epoch)
        except (TypeError, ValueError):
            locator_session_epoch = None
        if locator_session_epoch is not None and locator_session_epoch < 1:
            locator_session_epoch = None
        resolved_workspace_path = (
            (str(workspace_path or "").strip() or None)
            if binding is not None
            else (existing.workspace_path if existing is not None else None)
        )
        live_stream_capable = (
            bool(resolved_workspace_path)
            if resolved_workspace_path is not None
            else (existing.live_stream_capable if existing is not None else None)
        )
        session_id = str(locator.get("sessionId") or "").strip() or None
        session_epoch_value = locator.get("sessionEpoch")
        session_epoch = (
            session_epoch_value if isinstance(session_epoch_value, int) else None
        )
        container_id = str(locator.get("containerId") or "").strip() or None
        thread_id = str(locator.get("threadId") or "").strip() or None
        record = ManagedRunRecord(
            runId=record_key,
            workflowId=self._workflow_id,
            agentId=agent_id,
            runtimeId=runtime_id,
            status=status,
            startedAt=started_at,
            finishedAt=finished_at,
            workspacePath=resolved_workspace_path,
            stdoutArtifactRef=_artifact_ref(
                session_artifact_metadata.get("stdoutArtifactRef"),
                fallback=_infer_runtime_artifact_ref(
                    "stdout",
                    fallback=existing.stdout_artifact_ref if existing is not None else None,
                ),
            ),
            stderrArtifactRef=_artifact_ref(
                session_artifact_metadata.get("stderrArtifactRef"),
                fallback=_infer_runtime_artifact_ref(
                    "stderr",
                    fallback=existing.stderr_artifact_ref if existing is not None else None,
                ),
            ),
            diagnosticsRef=_artifact_ref(
                session_artifact_metadata.get("diagnosticsRef"),
                fallback=_infer_runtime_artifact_ref(
                    "diagnostics",
                    fallback=existing.diagnostics_ref if existing is not None else None,
                ),
            ),
            observabilityEventsRef=_artifact_ref(
                session_artifact_metadata.get("observabilityEventsRef"),
                fallback=(
                    _infer_runtime_artifact_ref(
                        "observability",
                        fallback=(
                            existing.observability_events_ref
                            if existing is not None
                            else None
                        ),
                    )
                ),
            ),
            errorMessage=summary if status in {"failed", "canceled", "timed_out"} else None,
            failureClass=result.get("failureClass"),
            providerErrorCode=_artifact_ref(result.get("providerErrorCode")),
            liveStreamCapable=live_stream_capable,
            sessionId=session_id
            or (binding.session_id if binding is not None else None)
            or (existing.session_id if existing is not None else None),
            sessionEpoch=session_epoch
            or (binding.session_epoch if binding is not None else None)
            or (existing.session_epoch if existing is not None else None),
            containerId=container_id or (existing.container_id if existing is not None else None),
            threadId=thread_id or (existing.thread_id if existing is not None else None),
            activeTurnId=active_turn_id,
        )
        self._run_store.save(record)

    def _persist_failed_run_state(
        self,
        *,
        run_id: str,
        agent_id: str,
        managed_run_id: str | None,
        binding: CodexManagedSessionBinding | None,
        workspace_path: str | None,
        locator: Mapping[str, Any],
        active_turn_id: str | None,
        summary: Any,
        default_summary: str,
        started_at: datetime,
        finished_at: datetime,
        instruction_ref: str | None,
        resolved_skillset_ref: str | None,
        profile_id: str | None,
        output_refs: tuple[str, ...] | list[str] = (),
        turn_id: str | None = None,
        session_artifacts: Mapping[str, Any] | None = None,
        turn_status: str | None = None,
        turn_metadata: Mapping[str, Any] | None = None,
    ) -> AgentRunResult:
        classification_source = (
            summary if str(summary or "").strip() else default_summary
        )
        classification = classify_provider_failure(classification_source)
        summary_text = _clamp_agent_run_result_summary(
            summary,
            default=default_summary,
        )
        metadata = _result_ref_metadata(
            instruction_ref=instruction_ref,
            resolved_skillset_ref=resolved_skillset_ref,
        )
        if profile_id:
            metadata["profileId"] = profile_id
        if turn_id:
            metadata["turnId"] = turn_id
        if turn_status:
            metadata["turnStatus"] = turn_status
        if turn_metadata:
            metadata["turnMetadata"] = dict(turn_metadata)
        if session_artifacts is not None:
            metadata["sessionArtifacts"] = dict(session_artifacts)
        if classification is not None:
            metadata["providerFailure"] = {
                "providerErrorCode": classification.provider_error_code,
                "retryRecommendation": classification.retry_recommendation,
                "reason": summary_text,
            }
        failure_result = AgentRunResult(
            outputRefs=list(output_refs),
            summary=summary_text,
            failureClass=(
                classification.failure_class
                if classification is not None
                else "execution_error"
            ),
            providerErrorCode=(
                classification.provider_error_code
                if classification is not None
                else None
            ),
            retryRecommendation=(
                classification.retry_recommendation
                if classification is not None
                else None
            ),
            metadata=metadata,
        )
        self._save_run_state(
            run_id=run_id,
            agent_id=agent_id,
            managed_run_id=managed_run_id,
            binding=binding,
            workspace_path=workspace_path,
            locator=locator,
            active_turn_id=active_turn_id,
            result=failure_result.model_dump(mode="json", by_alias=True),
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
            profile_id=profile_id,
        )
        return failure_result

    async def _publish_failure_artifacts(
        self,
        *,
        locator: CodexManagedSessionLocator,
        managed_run_id: str | None,
        run_id: str,
    ) -> CodexManagedSessionArtifactsPublication | None:
        try:
            return await self._coerce_publication(
                self._publish_remote_artifacts(
                    PublishCodexManagedSessionArtifactsRequest(
                        sessionId=locator.session_id,
                        sessionEpoch=locator.session_epoch,
                        containerId=locator.container_id,
                        threadId=locator.thread_id,
                        taskRunId=managed_run_id or run_id,
                        metadata={"runId": run_id, "workflowId": self._workflow_id},
                    )
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Failed to publish Codex session failure artifacts for run %s: %s",
                run_id,
                exc,
                exc_info=True,
            )
            return None

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
