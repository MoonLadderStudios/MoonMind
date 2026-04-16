"""Canonical contracts for the Codex managed session plane."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from moonmind.schemas._validation import NonBlankStr, require_non_blank
from moonmind.schemas.temporal_payload_policy import (
    MAX_TEMPORAL_METADATA_REF_CHARS,
    MAX_TEMPORAL_METADATA_STRING_CHARS,
    validate_compact_temporal_mapping,
)


HandoffSeedArtifactRef = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_TEMPORAL_METADATA_REF_CHARS,
    ),
]


ManagedSessionControlAction = Literal[
    "start_session",
    "resume_session",
    "send_turn",
    "steer_turn",
    "interrupt_turn",
    "clear_session",
    "cancel_session",
    "terminate_session",
]

ManagedSessionControlMode = Literal["remote_container"]
ManagedSessionProtocol = Literal["codex_app_server"]
ManagedSessionContainerBackend = Literal["docker"]
ManagedSessionHandleStatus = Literal[
    "launching",
    "ready",
    "busy",
    "clearing",
    "interrupted",
    "terminating",
    "terminated",
    "failed",
]
ManagedSessionTurnStatus = Literal[
    "accepted",
    "running",
    "completed",
    "interrupted",
    "failed",
]
ManagedSessionRecordStatus = Literal[
    "launching",
    "ready",
    "busy",
    "terminating",
    "terminated",
    "degraded",
    "failed",
]
ManagedSessionRequestTrackingStatus = Literal[
    "accepted",
    "completed",
    "failed",
    "superseded",
]

ClaudeRuntimeFamily = Literal["claude_code"]
ClaudeExecutionOwner = Literal["local_process", "anthropic_cloud_vm", "sdk_host"]
ClaudeSurfaceKind = Literal[
    "terminal",
    "vscode",
    "jetbrains",
    "desktop",
    "web",
    "mobile",
    "scheduler",
    "channel",
    "sdk",
]
ClaudeProjectionMode = Literal["primary", "remote_projection", "handoff"]
ClaudeSurfaceProjectionMode = Literal["primary", "remote_projection"]
ClaudeSurfaceCapability = Literal[
    "approvals",
    "diff_review",
    "notifications",
    "qr_connect",
    "keyboard_control",
]
ClaudeSessionState = Literal[
    "creating",
    "starting",
    "active",
    "waiting",
    "compacting",
    "rewinding",
    "archiving",
    "ended",
    "failed",
]
ClaudeTurnInputOrigin = Literal["human", "schedule", "channel", "sdk", "team_message"]
ClaudeTurnState = Literal[
    "submitted",
    "gathering_context",
    "pending_decision",
    "executing",
    "verifying",
    "interrupted",
    "completed",
    "failed",
]
ClaudeWorkItemKind = Literal[
    "context_read",
    "context_injection",
    "tool_call",
    "hook_call",
    "approval_request",
    "checkpoint",
    "compaction",
    "rewind",
    "subagent",
    "team_message",
    "summary",
    "telemetry_flush",
]
ClaudeWorkItemStatus = Literal[
    "queued",
    "in_progress",
    "completed",
    "failed",
    "declined",
    "canceled",
]
ClaudeSurfaceConnectionState = Literal[
    "connected",
    "disconnected",
    "reconnecting",
    "detached",
]
ClaudeSurfaceLifecycleEventName = Literal[
    "surface.attached",
    "surface.connected",
    "surface.disconnected",
    "surface.reconnecting",
    "surface.detached",
    "surface.resumed",
    "surface.handoff.created",
]
ClaudeExecutionSecurityMode = Literal[
    "local_execution",
    "remote_control_projection",
    "cloud_execution",
]
ClaudeSessionCreatedBy = Literal["user", "schedule", "channel", "sdk", "team_lead"]
ClaudeProviderMode = Literal[
    "anthropic_api",
    "bedrock",
    "vertex",
    "foundry",
    "custom_gateway",
]
ClaudeManagedSourceKind = Literal["none", "server_managed", "endpoint_managed"]
ClaudePolicySourceKind = Literal[
    "server_managed",
    "endpoint_managed",
    "local_project",
    "shared_project",
    "user",
    "cli",
]
ClaudePolicyFetchState = Literal[
    "not_applicable",
    "cache_hit",
    "fetched",
    "fetch_failed",
    "fail_closed",
]
ClaudePolicyTrustLevel = Literal[
    "endpoint_enforced",
    "server_managed_best_effort",
    "unmanaged",
]
ClaudePermissionMode = Literal[
    "default",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
]
ClaudePolicyHandshakeState = Literal[
    "ready",
    "security_dialog_required",
    "security_dialog_accepted",
    "security_dialog_rejected",
    "fail_closed",
    "blocked",
]
ClaudePolicyEventType = Literal[
    "policy.fetch.started",
    "policy.fetch.succeeded",
    "policy.fetch.failed",
    "policy.dialog.required",
    "policy.dialog.accepted",
    "policy.dialog.rejected",
    "policy.compiled",
    "policy.version.changed",
]
ClaudeDecisionStage = Literal[
    "session_state_guard",
    "pretool_hooks",
    "permission_rules",
    "protected_path_guard",
    "permission_mode_baseline",
    "sandbox_substitution",
    "auto_mode_classifier",
    "interactive_prompt_or_headless_resolution",
    "runtime_execution",
    "posttool_hooks",
    "checkpoint_capture",
]
ClaudeDecisionProposalKind = Literal[
    "tool",
    "file",
    "network",
    "mcp",
    "hook",
    "classifier",
    "prompt",
    "runtime",
    "checkpoint",
]
ClaudeDecisionOutcome = Literal[
    "proposed",
    "mutated",
    "allowed",
    "asked",
    "denied",
    "deferred",
    "canceled",
    "resolved",
    "failed",
    "executed",
]
ClaudeDecisionProvenanceSource = Literal[
    "session_state",
    "policy",
    "hook",
    "protected_path",
    "permission_mode",
    "sandbox",
    "classifier",
    "user",
    "headless_policy",
    "runtime",
    "checkpoint",
]
ClaudeDecisionEventName = Literal[
    "decision.proposed",
    "decision.mutated",
    "decision.allowed",
    "decision.asked",
    "decision.denied",
    "decision.deferred",
    "decision.canceled",
    "decision.resolved",
]
ClaudeHookSourceScope = Literal["managed", "user", "project", "plugin", "sdk"]
ClaudeHookOutcome = Literal[
    "allow",
    "deny",
    "ask",
    "mutate",
    "error",
    "noop",
    "defer",
]
ClaudeHookWorkEventName = Literal[
    "work.hook.started",
    "work.hook.completed",
    "work.hook.blocked",
]
ClaudeCheckpointTrigger = Literal[
    "user_prompt",
    "tracked_file_edit",
    "bash_side_effect",
    "external_manual_edit",
]
ClaudeCheckpointCaptureMode = Literal[
    "conversation",
    "code_and_conversation",
    "code",
    "best_effort",
    "skipped",
]
ClaudeCheckpointStatus = Literal[
    "captured",
    "skipped",
    "expired",
    "garbage_collected",
]
ClaudeCheckpointRetentionState = Literal[
    "addressable",
    "expires_at",
    "expired",
    "garbage_collected",
]
ClaudeRewindMode = Literal[
    "restore_code_and_conversation",
    "restore_conversation_only",
    "restore_code_only",
    "summarize_from_here",
]
ClaudeRewindStatus = Literal["started", "completed", "failed"]
ClaudeCheckpointWorkEventName = Literal[
    "work.checkpoint.created",
    "work.rewind.started",
    "work.rewind.completed",
]
ClaudeManagedWorkEventName = Literal[
    "work.hook.started",
    "work.hook.completed",
    "work.hook.blocked",
    "work.checkpoint.created",
    "work.rewind.started",
    "work.rewind.completed",
]
ClaudeContextSourceKind = Literal[
    "system_prompt",
    "output_style",
    "managed_claude_md",
    "project_claude_md",
    "local_claude_md",
    "auto_memory",
    "mcp_tool_manifest",
    "skill_description",
    "hook_injected_context",
    "file_read",
    "nested_claude_md",
    "path_rule",
    "invoked_skill_body",
    "runtime_summary",
    "transcript_summary",
]
ClaudeContextLoadedAt = Literal["startup", "on_demand", "post_compaction"]
ClaudeContextReinjectionPolicy = Literal[
    "always",
    "on_demand",
    "budgeted",
    "never",
    "startup_refresh",
    "configurable",
]
ClaudeContextGuidanceRole = Literal["guidance", "enforcement", "neutral"]
ClaudeContextEventName = Literal[
    "work.context.loaded",
    "work.compaction.started",
    "work.compaction.completed",
]
ClaudeChildContextReturnShape = Literal["summary", "summary_plus_metadata"]
ClaudeChildContextCommunication = Literal["caller_only"]
ClaudeChildContextLifecycleOwner = Literal["parent_turn"]
ClaudeChildContextStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "canceled",
]
ClaudeSessionGroupStatus = Literal[
    "creating",
    "active",
    "tearing_down",
    "completed",
    "failed",
    "canceled",
]
ClaudeTeamMemberRole = Literal["lead", "teammate"]
ClaudeTeamMemberStatus = Literal[
    "starting",
    "running",
    "completed",
    "failed",
    "canceled",
]
ClaudeChildWorkEventName = Literal[
    "child.subagent.started",
    "child.subagent.completed",
    "team.group.created",
    "team.member.started",
    "team.message.sent",
    "team.member.completed",
    "team.group.completed",
]
ClaudeEventSubscriptionType = Literal["session", "group", "org_policy"]
ClaudeEventFamily = Literal[
    "session",
    "surface",
    "policy",
    "turn",
    "work",
    "decision",
    "child_work",
]
ClaudeSessionEventName = Literal[
    "session.created",
    "session.started",
    "session.active",
    "session.waiting",
    "session.compacting",
    "session.rewinding",
    "session.archived",
    "session.ended",
    "session.failed",
]
ClaudeTurnEventName = Literal[
    "turn.submitted",
    "turn.gathering_context",
    "turn.pending_decision",
    "turn.executing",
    "turn.verifying",
    "turn.interrupted",
    "turn.completed",
    "turn.failed",
]
ClaudeWorkEventName = Literal[
    "work.context.loaded",
    "work.tool.requested",
    "work.tool.executed",
    "work.tool.failed",
    "work.hook.started",
    "work.hook.completed",
    "work.hook.blocked",
    "work.checkpoint.created",
    "work.compaction.started",
    "work.compaction.completed",
    "work.rewind.started",
    "work.rewind.completed",
]
ClaudeCentralStoreKind = Literal[
    "session_registry",
    "event_log",
    "policy_store",
    "context_index",
    "checkpoint_index",
    "artifact_index",
    "usage_store",
]
ClaudeStoredEvidenceKind = Literal[
    "metadata",
    "event_envelope",
    "policy_version",
    "usage_counter",
    "artifact_pointer",
    "retention_metadata",
    "telemetry_summary",
    "governance_summary",
]
ClaudeRuntimeLocalPayloadKind = Literal[
    "transcript",
    "full_file_read",
    "checkpoint_payload",
    "local_cache",
]
ClaudeRetentionClassName = Literal[
    "hot_session_metadata",
    "hot_event_log",
    "usage_rollups",
    "audit_event_metadata",
    "checkpoint_payloads",
]
ClaudeTelemetryMetricName = Literal[
    "managed_sessions_active",
    "managed_turn_duration_ms",
    "managed_decisions_total",
    "managed_hooks_total",
    "managed_checkpoints_total",
    "managed_compactions_total",
    "managed_subagent_total",
    "managed_team_sessions_total",
    "managed_policy_fetch_failures_total",
    "managed_surface_reconnects_total",
    "managed_usage_tokens",
]
ClaudeTelemetrySpanName = Literal[
    "session.bootstrap",
    "policy.resolve",
    "turn.process",
    "decision.resolve",
    "hook.execute",
    "tool.execute",
    "checkpoint.capture",
    "session.compact",
    "checkpoint.restore",
    "subagent.run",
    "team.session.run",
]
ClaudeUsageTokenDirection = Literal["input", "output", "total"]
ClaudeGovernanceControlLayer = Literal[
    "managed_settings_source_resolution",
    "permission_rules",
    "permission_mode",
    "protected_paths",
    "sandboxing",
    "hooks",
    "classifier_auto_mode",
    "interactive_dialogs",
    "runtime_isolation",
]

CLAUDE_DECISION_STAGE_ORDER: tuple[ClaudeDecisionStage, ...] = (
    "session_state_guard",
    "pretool_hooks",
    "permission_rules",
    "protected_path_guard",
    "permission_mode_baseline",
    "sandbox_substitution",
    "auto_mode_classifier",
    "interactive_prompt_or_headless_resolution",
    "runtime_execution",
    "posttool_hooks",
    "checkpoint_capture",
)
CLAUDE_DECISION_EVENT_NAMES: tuple[ClaudeDecisionEventName, ...] = (
    "decision.proposed",
    "decision.mutated",
    "decision.allowed",
    "decision.asked",
    "decision.denied",
    "decision.deferred",
    "decision.canceled",
    "decision.resolved",
)
CLAUDE_HOOK_WORK_EVENT_NAMES: tuple[ClaudeHookWorkEventName, ...] = (
    "work.hook.started",
    "work.hook.completed",
    "work.hook.blocked",
)
CLAUDE_CHECKPOINT_TRIGGERS: tuple[ClaudeCheckpointTrigger, ...] = (
    "user_prompt",
    "tracked_file_edit",
    "bash_side_effect",
    "external_manual_edit",
)
CLAUDE_CHECKPOINT_CAPTURE_MODES: tuple[ClaudeCheckpointCaptureMode, ...] = (
    "conversation",
    "code_and_conversation",
    "code",
    "best_effort",
    "skipped",
)
CLAUDE_CHECKPOINT_RETENTION_STATES: tuple[
    ClaudeCheckpointRetentionState, ...
] = (
    "addressable",
    "expires_at",
    "expired",
    "garbage_collected",
)
CLAUDE_REWIND_MODES: tuple[ClaudeRewindMode, ...] = (
    "restore_code_and_conversation",
    "restore_conversation_only",
    "restore_code_only",
    "summarize_from_here",
)
assert set(CLAUDE_REWIND_MODES) == set(get_args(ClaudeRewindMode))
CLAUDE_CHECKPOINT_WORK_EVENT_NAMES: tuple[
    ClaudeCheckpointWorkEventName, ...
] = (
    "work.checkpoint.created",
    "work.rewind.started",
    "work.rewind.completed",
)
CLAUDE_CONTEXT_STARTUP_KINDS: tuple[ClaudeContextSourceKind, ...] = (
    "system_prompt",
    "output_style",
    "managed_claude_md",
    "project_claude_md",
    "local_claude_md",
    "auto_memory",
    "mcp_tool_manifest",
    "skill_description",
    "hook_injected_context",
)
CLAUDE_CONTEXT_ON_DEMAND_KINDS: tuple[ClaudeContextSourceKind, ...] = (
    "file_read",
    "nested_claude_md",
    "path_rule",
    "invoked_skill_body",
    "runtime_summary",
)
CLAUDE_CONTEXT_EVENT_NAMES: tuple[ClaudeContextEventName, ...] = (
    "work.context.loaded",
    "work.compaction.started",
    "work.compaction.completed",
)
CLAUDE_CHILD_WORK_EVENT_NAMES: tuple[ClaudeChildWorkEventName, ...] = (
    "child.subagent.started",
    "child.subagent.completed",
    "team.group.created",
    "team.member.started",
    "team.message.sent",
    "team.member.completed",
    "team.group.completed",
)
CLAUDE_EVENT_FAMILIES: tuple[ClaudeEventFamily, ...] = (
    "session",
    "surface",
    "policy",
    "turn",
    "work",
    "decision",
    "child_work",
)
CLAUDE_SESSION_EVENT_NAMES: tuple[ClaudeSessionEventName, ...] = (
    "session.created",
    "session.started",
    "session.active",
    "session.waiting",
    "session.compacting",
    "session.rewinding",
    "session.archived",
    "session.ended",
    "session.failed",
)
CLAUDE_TURN_EVENT_NAMES: tuple[ClaudeTurnEventName, ...] = (
    "turn.submitted",
    "turn.gathering_context",
    "turn.pending_decision",
    "turn.executing",
    "turn.verifying",
    "turn.interrupted",
    "turn.completed",
    "turn.failed",
)
CLAUDE_WORK_EVENT_NAMES: tuple[ClaudeWorkEventName, ...] = (
    "work.context.loaded",
    "work.tool.requested",
    "work.tool.executed",
    "work.tool.failed",
    "work.hook.started",
    "work.hook.completed",
    "work.hook.blocked",
    "work.checkpoint.created",
    "work.compaction.started",
    "work.compaction.completed",
    "work.rewind.started",
    "work.rewind.completed",
)
CLAUDE_REQUIRED_RETENTION_CLASSES: tuple[ClaudeRetentionClassName, ...] = (
    "hot_session_metadata",
    "hot_event_log",
    "usage_rollups",
    "audit_event_metadata",
    "checkpoint_payloads",
)
CLAUDE_TELEMETRY_METRIC_NAMES: tuple[ClaudeTelemetryMetricName, ...] = (
    "managed_sessions_active",
    "managed_turn_duration_ms",
    "managed_decisions_total",
    "managed_hooks_total",
    "managed_checkpoints_total",
    "managed_compactions_total",
    "managed_subagent_total",
    "managed_team_sessions_total",
    "managed_policy_fetch_failures_total",
    "managed_surface_reconnects_total",
    "managed_usage_tokens",
)
CLAUDE_TELEMETRY_SPAN_NAMES: tuple[ClaudeTelemetrySpanName, ...] = (
    "session.bootstrap",
    "policy.resolve",
    "turn.process",
    "decision.resolve",
    "hook.execute",
    "tool.execute",
    "checkpoint.capture",
    "session.compact",
    "checkpoint.restore",
    "subagent.run",
    "team.session.run",
)
CLAUDE_SURFACE_LIFECYCLE_EVENT_NAMES: tuple[
    ClaudeSurfaceLifecycleEventName, ...
] = (
    "surface.attached",
    "surface.connected",
    "surface.disconnected",
    "surface.reconnecting",
    "surface.detached",
    "surface.resumed",
    "surface.handoff.created",
)
CLAUDE_EVENT_NAMES_BY_FAMILY: dict[ClaudeEventFamily, tuple[str, ...]] = {
    "session": CLAUDE_SESSION_EVENT_NAMES,
    "surface": CLAUDE_SURFACE_LIFECYCLE_EVENT_NAMES,
    "policy": tuple(get_args(ClaudePolicyEventType)),
    "turn": CLAUDE_TURN_EVENT_NAMES,
    "work": CLAUDE_WORK_EVENT_NAMES,
    "decision": CLAUDE_DECISION_EVENT_NAMES,
    "child_work": CLAUDE_CHILD_WORK_EVENT_NAMES,
}
_CLAUDE_CONTEXT_GUIDANCE_KINDS: frozenset[ClaudeContextSourceKind] = frozenset(
    {
        "managed_claude_md",
        "project_claude_md",
        "local_claude_md",
        "auto_memory",
        "nested_claude_md",
        "path_rule",
        "invoked_skill_body",
        "skill_description",
    }
)
_CLAUDE_CONTEXT_RETAINED_POLICIES: frozenset[
    ClaudeContextReinjectionPolicy
] = frozenset({"always", "startup_refresh", "budgeted", "configurable"})

CODEX_MANAGED_SESSION_CONTROL_ACTIONS: tuple[ManagedSessionControlAction, ...] = (
    "start_session",
    "resume_session",
    "send_turn",
    "steer_turn",
    "interrupt_turn",
    "clear_session",
    "cancel_session",
    "terminate_session",
)

CodexManagedSessionWorkflowStatus = Literal[
    "initializing",
    "active",
    "clearing",
    "terminating",
    "terminated",
]


def canonical_codex_managed_runtime_id(runtime_id: str) -> str | None:
    """Return the canonical managed-session runtime ID for Codex."""

    normalized = str(runtime_id or "").strip().lower().replace("-", "_")
    if normalized in {"codex", "codex_cli"}:
        return "codex_cli"
    return None


_ASSISTANT_TEXT_METADATA_KEYS = frozenset({"assistantText", "lastAssistantText"})
_ASSISTANT_TEXT_METADATA_MAX_BYTES = 8 * 1024


def _truncate_utf8_text(value: str, *, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _truncate_json_text(value: str, *, max_bytes: int) -> str:
    if len(json.dumps(value, allow_nan=False).encode("utf-8")) <= max_bytes:
        return value
    low = 0
    high = len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = value[:midpoint]
        encoded_size = len(json.dumps(candidate, allow_nan=False).encode("utf-8"))
        if encoded_size <= max_bytes:
            low = midpoint
        else:
            high = midpoint - 1
    return value[:low]


def _compact_managed_session_metadata(
    value: dict[str, Any],
) -> dict[str, Any]:
    """Clamp provider assistant text snippets before Temporal payload validation."""

    normalized = dict(value)
    for key in _ASSISTANT_TEXT_METADATA_KEYS:
        raw_text = normalized.get(key)
        if not isinstance(raw_text, str):
            continue
        original_chars = len(raw_text)
        compact_text = raw_text[:MAX_TEMPORAL_METADATA_STRING_CHARS]
        compact_text = _truncate_utf8_text(
            compact_text,
            max_bytes=_ASSISTANT_TEXT_METADATA_MAX_BYTES,
        )
        compact_text = _truncate_json_text(
            compact_text,
            max_bytes=_ASSISTANT_TEXT_METADATA_MAX_BYTES,
        )
        if compact_text != raw_text:
            normalized[key] = compact_text
            normalized[f"{key}Truncated"] = True
            normalized[f"{key}OriginalChars"] = original_chars
    return normalized


class CodexManagedSessionPlaneContract(BaseModel):
    """Frozen Phase 1 MVP contract for the Codex managed session plane."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_family: Literal["codex"] = "codex"
    protocol: ManagedSessionProtocol = "codex_app_server"
    container_backend: ManagedSessionContainerBackend = "docker"
    session_scope: Literal["task"] = "task"
    session_container_policy: Literal["one_container_per_task"] = (
        "one_container_per_task"
    )
    cross_task_reuse: Literal[False] = False
    log_authority: Literal["artifact_first"] = "artifact_first"
    continuity_authority: Literal["artifact_first"] = "artifact_first"
    durable_state_rule: Literal[
        "artifacts_and_bounded_workflow_metadata_are_authoritative"
    ] = "artifacts_and_bounded_workflow_metadata_are_authoritative"
    clear_behavior: Literal["new_thread_same_container_new_epoch"] = (
        "new_thread_same_container_new_epoch"
    )
    control_actions: tuple[ManagedSessionControlAction, ...] = (
        CODEX_MANAGED_SESSION_CONTROL_ACTIONS
    )

    @model_validator(mode="after")
    def _freeze_phase1_values(self) -> "CodexManagedSessionPlaneContract":
        if self.control_actions != CODEX_MANAGED_SESSION_CONTROL_ACTIONS:
            raise ValueError(
                "control_actions must match the canonical Phase 1 action set"
            )
        return self


class CodexManagedSessionState(BaseModel):
    """Identity and continuity state for one task-scoped Codex session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    container_id: NonBlankStr = Field(..., alias="containerId")
    thread_id: NonBlankStr = Field(..., alias="threadId")
    active_turn_id: NonBlankStr | None = Field(None, alias="activeTurnId")

    def clear_session(self, *, new_thread_id: str) -> "CodexManagedSessionState":
        """Advance to a new session epoch inside the existing container."""

        normalized_thread_id = require_non_blank(
            new_thread_id, field_name="threadId"
        )
        if normalized_thread_id == self.thread_id:
            raise ValueError("clear_session must create a new threadId")
        return self.model_copy(
            update={
                "session_epoch": self.session_epoch + 1,
                "thread_id": normalized_thread_id,
                "active_turn_id": None,
            }
        )


class _CodexManagedSessionRemoteContract(BaseModel):
    """Base model that freezes the remote-container managed-session boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    runtime_family: Literal["codex"] = Field("codex", alias="runtimeFamily")
    protocol: ManagedSessionProtocol = Field(
        "codex_app_server", alias="protocol"
    )
    container_backend: ManagedSessionContainerBackend = Field(
        "docker", alias="containerBackend"
    )
    control_mode: ManagedSessionControlMode = Field(
        "remote_container", alias="controlMode"
    )

    @field_validator("metadata", mode="after", check_fields=False)
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            _compact_managed_session_metadata(value),
            field_name="metadata",
        )


class CodexManagedSessionLocator(_CodexManagedSessionRemoteContract):
    """Canonical bounded identity for addressing one managed session remotely."""

    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    container_id: NonBlankStr = Field(..., alias="containerId")
    thread_id: NonBlankStr = Field(..., alias="threadId")


class LaunchCodexManagedSessionRequest(_CodexManagedSessionRemoteContract):
    """Launch contract for a task-scoped remote Codex session container."""

    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    workflow_id: NonBlankStr | None = Field(None, alias="workflowId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(1, alias="sessionEpoch", ge=1)
    thread_id: NonBlankStr = Field(..., alias="threadId")
    workspace_path: NonBlankStr = Field(..., alias="workspacePath")
    session_workspace_path: NonBlankStr = Field(
        ..., alias="sessionWorkspacePath"
    )
    artifact_spool_path: NonBlankStr = Field(..., alias="artifactSpoolPath")
    codex_home_path: NonBlankStr = Field(..., alias="codexHomePath")
    image_ref: NonBlankStr = Field(..., alias="imageRef")
    turn_completion_timeout_seconds: int = Field(
        3600,
        alias="turnCompletionTimeoutSeconds",
        ge=1,
    )
    environment: dict[str, str] = Field(default_factory=dict, alias="environment")
    workspace_spec: dict[str, Any] = Field(default_factory=dict, alias="workspaceSpec")

    @model_validator(mode="after")
    def _normalize_environment(self) -> "LaunchCodexManagedSessionRequest":
        normalized: dict[str, str] = {}
        for raw_key, raw_value in self.environment.items():
            key = require_non_blank(str(raw_key), field_name="environment key")
            normalized[key] = str(raw_value)
        self.environment = normalized
        self.workspace_spec = (
            dict(self.workspace_spec)
            if isinstance(self.workspace_spec, dict)
            else {}
        )
        return self


class SendCodexManagedSessionTurnRequest(CodexManagedSessionLocator):
    """Send a new turn to the remote session container."""

    instructions: NonBlankStr = Field(..., alias="instructions")
    reason: NonBlankStr | None = Field(None, alias="reason")


class SteerCodexManagedSessionTurnRequest(CodexManagedSessionLocator):
    """Provide follow-up steering to an in-flight turn."""

    turn_id: NonBlankStr = Field(..., alias="turnId")
    instructions: NonBlankStr = Field(..., alias="instructions")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class InterruptCodexManagedSessionTurnRequest(CodexManagedSessionLocator):
    """Interrupt an in-flight turn on the remote session container."""

    turn_id: NonBlankStr = Field(..., alias="turnId")
    reason: NonBlankStr | None = Field(None, alias="reason")


class CodexManagedSessionClearRequest(CodexManagedSessionLocator):
    """Explicit session clear/reset request with a new thread boundary."""

    new_thread_id: NonBlankStr = Field(..., alias="newThreadId")
    reason: NonBlankStr | None = Field(None, alias="reason")

    @model_validator(mode="after")
    def _require_new_thread(self) -> "CodexManagedSessionClearRequest":
        if self.new_thread_id == self.thread_id:
            raise ValueError("newThreadId must differ from threadId")
        return self


class TerminateCodexManagedSessionRequest(CodexManagedSessionLocator):
    """Terminate a remote managed session container."""

    reason: NonBlankStr | None = Field(None, alias="reason")


class FetchCodexManagedSessionSummaryRequest(CodexManagedSessionLocator):
    """Fetch the latest continuity summary/checkpoint refs for one session."""

    include_artifact_refs: bool = Field(True, alias="includeArtifactRefs")


class PublishCodexManagedSessionArtifactsRequest(CodexManagedSessionLocator):
    """Publish continuity artifacts for the remote session container."""

    task_run_id: NonBlankStr | None = Field(None, alias="taskRunId")
    step_run_id: NonBlankStr | None = Field(None, alias="stepRunId")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionHandle(_CodexManagedSessionRemoteContract):
    """Remote managed-session handle returned by launch/status/clear/terminate."""

    session_state: CodexManagedSessionState = Field(..., alias="sessionState")
    status: ManagedSessionHandleStatus = Field(..., alias="status")
    image_ref: NonBlankStr | None = Field(None, alias="imageRef")
    control_url: NonBlankStr | None = Field(None, alias="controlUrl")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionTurnResponse(_CodexManagedSessionRemoteContract):
    """Typed response for turn send/steer/interrupt activity calls."""

    session_state: CodexManagedSessionState = Field(..., alias="sessionState")
    turn_id: NonBlankStr = Field(..., alias="turnId")
    status: ManagedSessionTurnStatus = Field(..., alias="status")
    output_refs: tuple[NonBlankStr, ...] = Field(default=(), alias="outputRefs")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionSummary(_CodexManagedSessionRemoteContract):
    """Latest continuity projection refs for one managed session."""

    session_state: CodexManagedSessionState = Field(..., alias="sessionState")
    latest_summary_ref: NonBlankStr | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: NonBlankStr | None = Field(
        None, alias="latestCheckpointRef"
    )
    latest_control_event_ref: NonBlankStr | None = Field(
        None, alias="latestControlEventRef"
    )
    latest_reset_boundary_ref: NonBlankStr | None = Field(
        None, alias="latestResetBoundaryRef"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionArtifactsPublication(_CodexManagedSessionRemoteContract):
    """Artifact publication result for one managed session."""

    session_state: CodexManagedSessionState = Field(..., alias="sessionState")
    published_artifact_refs: tuple[NonBlankStr, ...] = Field(
        default=(), alias="publishedArtifactRefs"
    )
    latest_summary_ref: NonBlankStr | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: NonBlankStr | None = Field(
        None, alias="latestCheckpointRef"
    )
    latest_control_event_ref: NonBlankStr | None = Field(
        None, alias="latestControlEventRef"
    )
    latest_reset_boundary_ref: NonBlankStr | None = Field(
        None, alias="latestResetBoundaryRef"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionRecord(BaseModel):
    """Durable session-level supervision record for one managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    container_id: NonBlankStr = Field(..., alias="containerId")
    thread_id: NonBlankStr = Field(..., alias="threadId")
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    image_ref: NonBlankStr = Field(..., alias="imageRef")
    control_url: NonBlankStr = Field(..., alias="controlUrl")
    status: ManagedSessionRecordStatus = Field(..., alias="status")
    workspace_path: NonBlankStr = Field(..., alias="workspacePath")
    session_workspace_path: NonBlankStr = Field(..., alias="sessionWorkspacePath")
    artifact_spool_path: NonBlankStr = Field(..., alias="artifactSpoolPath")
    stdout_artifact_ref: str | None = Field(None, alias="stdoutArtifactRef")
    stderr_artifact_ref: str | None = Field(None, alias="stderrArtifactRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    observability_events_ref: str | None = Field(
        None,
        alias="observabilityEventsRef",
    )
    latest_summary_ref: str | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: str | None = Field(None, alias="latestCheckpointRef")
    latest_control_event_ref: str | None = Field(None, alias="latestControlEventRef")
    latest_reset_boundary_ref: str | None = Field(None, alias="latestResetBoundaryRef")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    last_log_offset: int | None = Field(None, alias="lastLogOffset", ge=0)
    stdout_log_offset: int | None = Field(None, alias="stdoutLogOffset", ge=0)
    stderr_log_offset: int | None = Field(None, alias="stderrLogOffset", ge=0)
    last_log_at: datetime | None = Field(None, alias="lastLogAt")
    error_message: str | None = Field(None, alias="errorMessage")
    started_at: datetime = Field(..., alias="startedAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionRecord":
        self.session_id = require_non_blank(self.session_id, field_name="sessionId")
        self.task_run_id = require_non_blank(self.task_run_id, field_name="taskRunId")
        self.container_id = require_non_blank(self.container_id, field_name="containerId")
        self.thread_id = require_non_blank(self.thread_id, field_name="threadId")
        runtime_id = canonical_codex_managed_runtime_id(self.runtime_id)
        if runtime_id is None:
            raise ValueError("runtimeId must identify a managed Codex runtime")
        self.runtime_id = runtime_id
        self.image_ref = require_non_blank(self.image_ref, field_name="imageRef")
        self.control_url = require_non_blank(self.control_url, field_name="controlUrl")
        self.workspace_path = require_non_blank(self.workspace_path, field_name="workspacePath")
        self.session_workspace_path = require_non_blank(
            self.session_workspace_path,
            field_name="sessionWorkspacePath",
        )
        self.artifact_spool_path = require_non_blank(
            self.artifact_spool_path,
            field_name="artifactSpoolPath",
        )
        if self.stdout_artifact_ref is not None:
            self.stdout_artifact_ref = require_non_blank(
                self.stdout_artifact_ref,
                field_name="stdoutArtifactRef",
            )
        if self.stderr_artifact_ref is not None:
            self.stderr_artifact_ref = require_non_blank(
                self.stderr_artifact_ref,
                field_name="stderrArtifactRef",
            )
        if self.diagnostics_ref is not None:
            self.diagnostics_ref = require_non_blank(
                self.diagnostics_ref,
                field_name="diagnosticsRef",
            )
        if self.observability_events_ref is not None:
            self.observability_events_ref = require_non_blank(
                self.observability_events_ref,
                field_name="observabilityEventsRef",
            )
        if self.latest_summary_ref is not None:
            self.latest_summary_ref = require_non_blank(
                self.latest_summary_ref,
                field_name="latestSummaryRef",
            )
        if self.latest_checkpoint_ref is not None:
            self.latest_checkpoint_ref = require_non_blank(
                self.latest_checkpoint_ref,
                field_name="latestCheckpointRef",
            )
        if self.latest_control_event_ref is not None:
            self.latest_control_event_ref = require_non_blank(
                self.latest_control_event_ref,
                field_name="latestControlEventRef",
            )
        if self.latest_reset_boundary_ref is not None:
            self.latest_reset_boundary_ref = require_non_blank(
                self.latest_reset_boundary_ref,
                field_name="latestResetBoundaryRef",
            )
        if self.active_turn_id is not None:
            self.active_turn_id = require_non_blank(
                self.active_turn_id,
                field_name="activeTurnId",
            )
        if self.error_message is not None:
            self.error_message = require_non_blank(
                self.error_message,
                field_name="errorMessage",
            )
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.last_log_at is not None and self.last_log_at.tzinfo is None:
            self.last_log_at = self.last_log_at.replace(tzinfo=UTC)
        if self.updated_at is not None and self.updated_at.tzinfo is None:
            self.updated_at = self.updated_at.replace(tzinfo=UTC)
        return self

    def session_state(self) -> CodexManagedSessionState:
        return CodexManagedSessionState(
            sessionId=self.session_id,
            sessionEpoch=self.session_epoch,
            containerId=self.container_id,
            threadId=self.thread_id,
            activeTurnId=self.active_turn_id,
        )

    def published_artifact_refs(self) -> tuple[str, ...]:
        refs: list[str] = []
        for ref in (
            self.stdout_artifact_ref,
            self.stderr_artifact_ref,
            self.diagnostics_ref,
            self.observability_events_ref,
            self.latest_summary_ref,
            self.latest_checkpoint_ref,
            self.latest_control_event_ref,
            self.latest_reset_boundary_ref,
        ):
            if ref and ref not in refs:
                refs.append(ref)
        return tuple(refs)


class CodexManagedSessionBinding(BaseModel):
    """Bounded task-scoped session binding passed across workflow boundaries."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: NonBlankStr = Field(..., alias="workflowId")
    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(1, alias="sessionEpoch", ge=1)
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    execution_profile_ref: str | None = Field(None, alias="executionProfileRef")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionBinding":
        self.workflow_id = require_non_blank(self.workflow_id, field_name="workflowId")
        self.task_run_id = require_non_blank(self.task_run_id, field_name="taskRunId")
        self.session_id = require_non_blank(self.session_id, field_name="sessionId")
        runtime_id = canonical_codex_managed_runtime_id(self.runtime_id)
        if runtime_id is None:
            raise ValueError("runtimeId must identify a managed Codex runtime")
        self.runtime_id = runtime_id
        if self.execution_profile_ref is not None:
            self.execution_profile_ref = require_non_blank(
                self.execution_profile_ref,
                field_name="executionProfileRef",
            )
        return self

    @classmethod
    def from_input(
        cls,
        *,
        workflow_id: str,
        session_input: "CodexManagedSessionWorkflowInput",
    ) -> "CodexManagedSessionBinding":
        runtime_id = session_input.runtime_id
        session_id = session_input.session_id or f"sess:{session_input.task_run_id}:{runtime_id}"
        return cls(
            workflowId=workflow_id,
            taskRunId=session_input.task_run_id,
            sessionId=session_id,
            sessionEpoch=session_input.session_epoch,
            runtimeId=runtime_id,
            executionProfileRef=session_input.execution_profile_ref,
        )


class CodexManagedSessionWorkflowInput(BaseModel):
    """Workflow input for one task-scoped Codex managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    execution_profile_ref: str | None = Field(None, alias="executionProfileRef")
    session_id: str | None = Field(None, alias="sessionId")
    session_epoch: int = Field(1, alias="sessionEpoch", ge=1)
    container_id: str | None = Field(None, alias="containerId")
    thread_id: str | None = Field(None, alias="threadId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    last_control_action: ManagedSessionControlAction | None = Field(
        None, alias="lastControlAction"
    )
    last_control_reason: str | None = Field(None, alias="lastControlReason")
    latest_summary_ref: str | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: str | None = Field(None, alias="latestCheckpointRef")
    latest_control_event_ref: str | None = Field(None, alias="latestControlEventRef")
    latest_reset_boundary_ref: str | None = Field(None, alias="latestResetBoundaryRef")
    continue_as_new_event_threshold: int | None = Field(
        None, alias="continueAsNewEventThreshold", ge=1
    )
    request_tracking_state: tuple["CodexManagedSessionRequestTrackingEntry", ...] = Field(
        default=(), alias="requestTrackingState"
    )

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionWorkflowInput":
        self.task_run_id = require_non_blank(self.task_run_id, field_name="taskRunId")
        runtime_id = canonical_codex_managed_runtime_id(self.runtime_id)
        if runtime_id is None:
            raise ValueError("runtimeId must identify a managed Codex runtime")
        self.runtime_id = runtime_id
        if self.execution_profile_ref is not None:
            self.execution_profile_ref = require_non_blank(
                self.execution_profile_ref,
                field_name="executionProfileRef",
            )
        if self.session_id is not None:
            self.session_id = require_non_blank(self.session_id, field_name="sessionId")
        if self.container_id is not None:
            self.container_id = require_non_blank(
                self.container_id,
                field_name="containerId",
            )
        if self.thread_id is not None:
            self.thread_id = require_non_blank(self.thread_id, field_name="threadId")
        if self.active_turn_id is not None:
            self.active_turn_id = require_non_blank(
                self.active_turn_id,
                field_name="activeTurnId",
            )
        if self.last_control_reason is not None:
            self.last_control_reason = require_non_blank(
                self.last_control_reason,
                field_name="lastControlReason",
            )
        if self.latest_summary_ref is not None:
            self.latest_summary_ref = require_non_blank(
                self.latest_summary_ref,
                field_name="latestSummaryRef",
            )
        if self.latest_checkpoint_ref is not None:
            self.latest_checkpoint_ref = require_non_blank(
                self.latest_checkpoint_ref,
                field_name="latestCheckpointRef",
            )
        if self.latest_control_event_ref is not None:
            self.latest_control_event_ref = require_non_blank(
                self.latest_control_event_ref,
                field_name="latestControlEventRef",
            )
        if self.latest_reset_boundary_ref is not None:
            self.latest_reset_boundary_ref = require_non_blank(
                self.latest_reset_boundary_ref,
                field_name="latestResetBoundaryRef",
            )
        return self


class CodexManagedSessionRequestTrackingEntry(BaseModel):
    """Compact metadata for an identified mutating control request."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: NonBlankStr = Field(..., alias="requestId")
    action: ManagedSessionControlAction = Field(..., alias="action")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    status: ManagedSessionRequestTrackingStatus = Field(..., alias="status")
    result_ref: str | None = Field(None, alias="resultRef")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionRequestTrackingEntry":
        self.request_id = require_non_blank(self.request_id, field_name="requestId")
        if self.result_ref is not None:
            self.result_ref = require_non_blank(self.result_ref, field_name="resultRef")
        return self


class CodexManagedSessionSendFollowUpRequest(BaseModel):
    """Typed workflow update request for sending a follow-up turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    message: NonBlankStr = Field(..., alias="message")
    reason: str | None = Field(None, alias="reason")
    request_id: str | None = Field(None, alias="requestId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionSendFollowUpRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.request_id is not None:
            self.request_id = require_non_blank(self.request_id, field_name="requestId")
        return self


class CodexManagedSessionInterruptRequest(BaseModel):
    """Typed workflow update request for interrupting an active turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    reason: str | None = Field(None, alias="reason")
    request_id: str | None = Field(None, alias="requestId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionInterruptRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.request_id is not None:
            self.request_id = require_non_blank(self.request_id, field_name="requestId")
        return self


class CodexManagedSessionSteerRequest(BaseModel):
    """Typed workflow update request for steering an active turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    message: NonBlankStr = Field(..., alias="message")
    reason: str | None = Field(None, alias="reason")
    request_id: str | None = Field(None, alias="requestId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionSteerRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.request_id is not None:
            self.request_id = require_non_blank(self.request_id, field_name="requestId")
        return self


class CodexManagedSessionWorkflowControlRequest(BaseModel):
    """Typed workflow update request for clear/cancel/terminate operations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reason: str | None = Field(None, alias="reason")
    request_id: str | None = Field(None, alias="requestId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionWorkflowControlRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.request_id is not None:
            self.request_id = require_non_blank(self.request_id, field_name="requestId")
        return self


class CodexManagedSessionAttachRuntimeHandlesSignal(BaseModel):
    """Typed workflow signal for attaching bounded runtime handles."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_epoch: int | None = Field(None, alias="sessionEpoch", ge=1)
    container_id: str | None = Field(None, alias="containerId")
    thread_id: str | None = Field(None, alias="threadId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    last_control_action: ManagedSessionControlAction | None = Field(
        None, alias="lastControlAction"
    )
    last_control_reason: str | None = Field(None, alias="lastControlReason")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionAttachRuntimeHandlesSignal":
        if self.container_id is not None:
            self.container_id = require_non_blank(
                self.container_id,
                field_name="containerId",
            )
        if self.thread_id is not None:
            self.thread_id = require_non_blank(self.thread_id, field_name="threadId")
        if self.active_turn_id is not None:
            self.active_turn_id = require_non_blank(
                self.active_turn_id,
                field_name="activeTurnId",
            )
        if self.last_control_reason is not None:
            self.last_control_reason = require_non_blank(
                self.last_control_reason,
                field_name="lastControlReason",
            )
        return self


class CodexManagedSessionClearUpdateRequest(CodexManagedSessionWorkflowControlRequest):
    """Typed workflow update request for clearing session context."""


class CodexManagedSessionCancelUpdateRequest(CodexManagedSessionWorkflowControlRequest):
    """Typed workflow update request for canceling the active session turn."""


class CodexManagedSessionTerminateUpdateRequest(CodexManagedSessionWorkflowControlRequest):
    """Typed workflow update request for terminating the managed session."""


class CodexManagedSessionSnapshot(BaseModel):
    """Workflow-owned snapshot of one task-scoped Codex session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    binding: CodexManagedSessionBinding = Field(..., alias="binding")
    status: CodexManagedSessionWorkflowStatus = Field(..., alias="status")
    container_id: str | None = Field(None, alias="containerId")
    thread_id: str | None = Field(None, alias="threadId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    last_control_action: ManagedSessionControlAction | None = Field(
        None, alias="lastControlAction"
    )
    last_control_reason: str | None = Field(None, alias="lastControlReason")
    latest_summary_ref: str | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: str | None = Field(None, alias="latestCheckpointRef")
    latest_control_event_ref: str | None = Field(None, alias="latestControlEventRef")
    latest_reset_boundary_ref: str | None = Field(None, alias="latestResetBoundaryRef")
    termination_requested: bool = Field(False, alias="terminationRequested")
    request_tracking_state: tuple[CodexManagedSessionRequestTrackingEntry, ...] = Field(
        default=(), alias="requestTrackingState"
    )


class ClaudeSurfaceBinding(BaseModel):
    """Durable representation of one surface attached to a Claude session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    surface_id: NonBlankStr = Field(..., alias="surfaceId")
    surface_kind: ClaudeSurfaceKind = Field(..., alias="surfaceKind")
    projection_mode: ClaudeSurfaceProjectionMode = Field(
        ..., alias="projectionMode"
    )
    connection_state: ClaudeSurfaceConnectionState = Field(
        "connected", alias="connectionState"
    )
    interactive: bool = Field(..., alias="interactive")
    capabilities: tuple[ClaudeSurfaceCapability, ...] = ()
    last_seen_at: datetime | None = Field(None, alias="lastSeenAt")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeSurfaceBinding":
        if self.last_seen_at is not None and self.last_seen_at.tzinfo is None:
            self.last_seen_at = self.last_seen_at.replace(tzinfo=UTC)
        return self


class ClaudeManagedSession(BaseModel):
    """Canonical Claude Code session record for the shared session plane."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    runtime_family: ClaudeRuntimeFamily = Field(
        "claude_code", alias="runtimeFamily"
    )
    execution_owner: ClaudeExecutionOwner = Field(..., alias="executionOwner")
    state: ClaudeSessionState = Field(..., alias="state")
    primary_surface: ClaudeSurfaceKind = Field(..., alias="primarySurface")
    projection_mode: ClaudeProjectionMode = Field(..., alias="projectionMode")
    surface_bindings: tuple[ClaudeSurfaceBinding, ...] = Field(
        default=(), alias="surfaceBindings"
    )
    active_turn_id: NonBlankStr | None = Field(None, alias="activeTurnId")
    parent_session_id: NonBlankStr | None = Field(None, alias="parentSessionId")
    fork_of_session_id: NonBlankStr | None = Field(None, alias="forkOfSessionId")
    handoff_from_session_id: NonBlankStr | None = Field(
        None, alias="handoffFromSessionId"
    )
    handoff_seed_artifact_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="handoffSeedArtifactRefs"
    )
    session_group_id: NonBlankStr | None = Field(None, alias="sessionGroupId")
    created_by: ClaudeSessionCreatedBy = Field(..., alias="createdBy")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    ended_at: datetime | None = Field(None, alias="endedAt")
    extensions: dict[str, Any] = Field(default_factory=dict, alias="extensions")

    @field_validator("extensions", mode="after")
    @classmethod
    def _validate_extensions(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="extensions")

    @model_validator(mode="after")
    def _validate_invariants(self) -> "ClaudeManagedSession":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if self.updated_at.tzinfo is None:
            self.updated_at = self.updated_at.replace(tzinfo=UTC)
        if self.ended_at is not None and self.ended_at.tzinfo is None:
            self.ended_at = self.ended_at.replace(tzinfo=UTC)
        primary_bindings = [
            binding
            for binding in self.surface_bindings
            if binding.projection_mode == "primary"
        ]
        if len(primary_bindings) > 1:
            raise ValueError("Claude session can have only one primary surface")
        if (
            primary_bindings
            and primary_bindings[0].surface_kind != self.primary_surface
        ):
            raise ValueError(
                "primarySurface must match the primary surface binding kind"
            )
        if self.projection_mode == "handoff":
            if self.execution_owner != "anthropic_cloud_vm":
                raise ValueError("handoff sessions must use anthropic_cloud_vm")
            if self.handoff_from_session_id is None:
                raise ValueError("handoff sessions require handoffFromSessionId")
        elif self.handoff_seed_artifact_refs or self.handoff_from_session_id:
            raise ValueError("handoff lineage fields require handoff projection")
        return self

    def with_surface_binding(
        self,
        *,
        surface_id: str,
        surface_kind: ClaudeSurfaceKind,
        projection_mode: ClaudeSurfaceProjectionMode,
        interactive: bool,
        updated_at: datetime,
        connection_state: ClaudeSurfaceConnectionState = "connected",
        capabilities: tuple[ClaudeSurfaceCapability, ...] = (),
        last_seen_at: datetime | None = None,
    ) -> "ClaudeManagedSession":
        """Return a copy with an added or replaced surface binding."""

        next_surface_id = require_non_blank(surface_id, field_name="surfaceId")
        binding = ClaudeSurfaceBinding(
            surfaceId=next_surface_id,
            surfaceKind=surface_kind,
            projectionMode=projection_mode,
            connectionState=connection_state,
            interactive=interactive,
            capabilities=capabilities,
            lastSeenAt=last_seen_at,
        )
        existing_same_surface = tuple(
            existing
            for existing in self.surface_bindings
            if existing.surface_id == next_surface_id
        )
        if (
            projection_mode != "primary"
            and any(
                existing.projection_mode == "primary"
                for existing in existing_same_surface
            )
        ):
            raise ValueError("primary surface binding cannot become a projection")
        existing_without_surface = tuple(
            existing
            for existing in self.surface_bindings
            if existing.surface_id != next_surface_id
        )
        if projection_mode == "primary":
            for existing in existing_without_surface:
                if existing.projection_mode == "primary":
                    raise ValueError(
                        "Claude session can have only one primary surface"
                    )
        payload = self.model_dump(by_alias=True)
        payload.update(
            {
                "surfaceBindings": (*existing_without_surface, binding),
                "updatedAt": updated_at,
            }
        )
        if projection_mode == "primary":
            payload["primarySurface"] = surface_kind
        return type(self)(**payload)

    def with_remote_projection(
        self,
        *,
        surface_id: str,
        surface_kind: ClaudeSurfaceKind,
        interactive: bool,
        updated_at: datetime,
    ) -> "ClaudeManagedSession":
        """Return a copy with an added Remote Control projection surface."""

        return self.with_surface_binding(
            surface_id=surface_id,
            surface_kind=surface_kind,
            projection_mode="remote_projection",
            interactive=interactive,
            updated_at=updated_at,
            connection_state="connected",
            last_seen_at=updated_at,
        )

    def with_surface_connection_state(
        self,
        *,
        surface_id: str,
        connection_state: ClaudeSurfaceConnectionState,
        updated_at: datetime,
    ) -> "ClaudeManagedSession":
        """Return a copy with one surface binding's connection state updated."""

        next_surface_id = require_non_blank(surface_id, field_name="surfaceId")
        next_bindings: list[ClaudeSurfaceBinding] = []
        found = False
        for binding in self.surface_bindings:
            if binding.surface_id != next_surface_id:
                next_bindings.append(binding)
                continue
            found = True
            next_bindings.append(
                binding.model_copy(
                    deep=True,
                    update={
                        "connection_state": connection_state,
                        "last_seen_at": updated_at,
                    },
                )
            )
        if not found:
            raise ValueError(f"surfaceId {next_surface_id!r} is not attached")
        return self.model_copy(
            deep=True,
            update={
                "surface_bindings": tuple(next_bindings),
                "updated_at": updated_at,
            },
        )

    def resume_on_surface(
        self,
        *,
        surface_id: str,
        surface_kind: ClaudeSurfaceKind,
        interactive: bool,
        updated_at: datetime,
        capabilities: tuple[ClaudeSurfaceCapability, ...] = (),
    ) -> "ClaudeManagedSession":
        """Return a copy resumed on a new primary surface without handoff."""

        non_primary = tuple(
            binding
            for binding in self.surface_bindings
            if binding.projection_mode != "primary"
        )
        resumed = self.model_copy(
            deep=True,
            update={
                "surface_bindings": non_primary,
                "primary_surface": surface_kind,
                "updated_at": updated_at,
            },
        )
        return resumed.with_surface_binding(
            surface_id=surface_id,
            surface_kind=surface_kind,
            projection_mode="primary",
            interactive=interactive,
            updated_at=updated_at,
            connection_state="connected",
            capabilities=capabilities,
            last_seen_at=updated_at,
        )

    def cloud_handoff(
        self,
        *,
        session_id: str,
        primary_surface: ClaudeSurfaceKind,
        created_by: ClaudeSessionCreatedBy,
        created_at: datetime,
        seed_artifact_refs: tuple[str, ...] = (),
    ) -> "ClaudeManagedSession":
        """Create a distinct cloud-owned session with lineage to this session."""

        destination_session_id = require_non_blank(
            session_id, field_name="sessionId"
        )
        if destination_session_id == self.session_id:
            raise ValueError("cloud_handoff must create a distinct sessionId")
        return ClaudeManagedSession(
            sessionId=destination_session_id,
            executionOwner="anthropic_cloud_vm",
            state="creating",
            primarySurface=primary_surface,
            projectionMode="handoff",
            surfaceBindings=(),
            handoffFromSessionId=self.session_id,
            handoffSeedArtifactRefs=seed_artifact_refs,
            createdBy=created_by,
            createdAt=created_at,
            updatedAt=created_at,
        )


class ClaudeSurfaceLifecycleEvent(BaseModel):
    """Normalized event for Claude surface lifecycle and handoff activity."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_id: NonBlankStr = Field(..., alias="eventId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    surface_id: NonBlankStr | None = Field(None, alias="surfaceId")
    event_name: ClaudeSurfaceLifecycleEventName = Field(..., alias="eventName")
    source_session_id: NonBlankStr | None = Field(None, alias="sourceSessionId")
    destination_session_id: NonBlankStr | None = Field(
        None, alias="destinationSessionId"
    )
    handoff_seed_artifact_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="handoffSeedArtifactRefs"
    )
    occurred_at: datetime = Field(..., alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_event_shape(self) -> "ClaudeSurfaceLifecycleEvent":
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=UTC)
        if self.event_name == "surface.handoff.created":
            if self.source_session_id is None:
                raise ValueError(
                    "sourceSessionId is required for surface.handoff.created"
                )
            if self.destination_session_id is None:
                raise ValueError(
                    "destinationSessionId is required for surface.handoff.created"
                )
            return self
        if self.surface_id is None:
            raise ValueError("surfaceId is required for surface lifecycle events")
        if (
            self.source_session_id is not None
            or self.destination_session_id is not None
        ):
            raise ValueError("handoff lineage fields require surface.handoff.created")
        if self.handoff_seed_artifact_refs:
            raise ValueError(
                "handoffSeedArtifactRefs require surface.handoff.created"
            )
        return self


class ClaudeSurfaceHandoffFixtureFlow(BaseModel):
    """Deterministic provider-free fixture flow for MM-348 boundaries."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_session: ClaudeManagedSession = Field(..., alias="sourceSession")
    projected_session: ClaudeManagedSession = Field(..., alias="projectedSession")
    disconnected_session: ClaudeManagedSession = Field(
        ..., alias="disconnectedSession"
    )
    reconnected_session: ClaudeManagedSession = Field(..., alias="reconnectedSession")
    resumed_session: ClaudeManagedSession = Field(..., alias="resumedSession")
    cloud_session: ClaudeManagedSession = Field(..., alias="cloudSession")
    events: tuple[ClaudeSurfaceLifecycleEvent, ...]


def classify_claude_execution_security_mode(
    session: ClaudeManagedSession,
) -> ClaudeExecutionSecurityMode:
    """Classify execution locality for security and compliance reporting."""

    if session.execution_owner == "anthropic_cloud_vm":
        return "cloud_execution"
    if any(
        binding.projection_mode == "remote_projection"
        for binding in session.surface_bindings
    ):
        return "remote_control_projection"
    return "local_execution"


def build_claude_surface_handoff_fixture_flow(
    *,
    source_session_id: str,
    terminal_surface_id: str,
    web_surface_id: str,
    resumed_surface_id: str,
    cloud_session_id: str,
    created_at: datetime,
    seed_artifact_refs: tuple[str, ...],
) -> ClaudeSurfaceHandoffFixtureFlow:
    """Build a deterministic multi-surface and handoff flow for boundary tests."""

    source_session = ClaudeManagedSession(
        sessionId=source_session_id,
        executionOwner="local_process",
        state="active",
        primarySurface="terminal",
        projectionMode="primary",
        createdBy="user",
        createdAt=created_at,
        updatedAt=created_at,
    ).with_surface_binding(
        surface_id=terminal_surface_id,
        surface_kind="terminal",
        projection_mode="primary",
        interactive=True,
        capabilities=("approvals", "diff_review"),
        updated_at=created_at,
        last_seen_at=created_at,
    )
    projected_session = source_session.with_remote_projection(
        surface_id=web_surface_id,
        surface_kind="web",
        interactive=True,
        updated_at=created_at,
    )
    disconnected_session = projected_session.with_surface_connection_state(
        surface_id=web_surface_id,
        connection_state="disconnected",
        updated_at=created_at,
    )
    reconnecting_session = disconnected_session.with_surface_connection_state(
        surface_id=web_surface_id,
        connection_state="reconnecting",
        updated_at=created_at,
    )
    reconnected_session = reconnecting_session.with_surface_connection_state(
        surface_id=web_surface_id,
        connection_state="connected",
        updated_at=created_at,
    )
    resumed_session = reconnected_session.resume_on_surface(
        surface_id=resumed_surface_id,
        surface_kind="desktop",
        interactive=True,
        capabilities=("approvals", "keyboard_control"),
        updated_at=created_at,
    )
    cloud_session = resumed_session.cloud_handoff(
        session_id=cloud_session_id,
        primary_surface="web",
        created_by="user",
        created_at=created_at,
        seed_artifact_refs=seed_artifact_refs,
    )
    events = (
        ClaudeSurfaceLifecycleEvent(
            eventId=f"{terminal_surface_id}:attached",
            sessionId=source_session_id,
            surfaceId=terminal_surface_id,
            eventName="surface.attached",
            occurredAt=created_at,
        ),
        ClaudeSurfaceLifecycleEvent(
            eventId=f"{web_surface_id}:attached",
            sessionId=source_session_id,
            surfaceId=web_surface_id,
            eventName="surface.attached",
            occurredAt=created_at,
            metadata={"projectionMode": "remote_projection"},
        ),
        ClaudeSurfaceLifecycleEvent(
            eventId=f"{web_surface_id}:disconnected",
            sessionId=source_session_id,
            surfaceId=web_surface_id,
            eventName="surface.disconnected",
            occurredAt=created_at,
        ),
        ClaudeSurfaceLifecycleEvent(
            eventId=f"{web_surface_id}:reconnecting",
            sessionId=source_session_id,
            surfaceId=web_surface_id,
            eventName="surface.reconnecting",
            occurredAt=created_at,
        ),
        ClaudeSurfaceLifecycleEvent(
            eventId=f"{web_surface_id}:connected",
            sessionId=source_session_id,
            surfaceId=web_surface_id,
            eventName="surface.connected",
            occurredAt=created_at,
        ),
        ClaudeSurfaceLifecycleEvent(
            eventId=f"{resumed_surface_id}:resumed",
            sessionId=source_session_id,
            surfaceId=resumed_surface_id,
            eventName="surface.resumed",
            occurredAt=created_at,
        ),
        ClaudeSurfaceLifecycleEvent(
            eventId=f"{cloud_session_id}:handoff",
            sessionId=cloud_session_id,
            eventName="surface.handoff.created",
            sourceSessionId=source_session_id,
            destinationSessionId=cloud_session_id,
            handoffSeedArtifactRefs=seed_artifact_refs,
            occurredAt=created_at,
        ),
    )
    return ClaudeSurfaceHandoffFixtureFlow(
        sourceSession=source_session,
        projectedSession=projected_session,
        disconnectedSession=disconnected_session,
        reconnectedSession=reconnected_session,
        resumedSession=resumed_session,
        cloudSession=cloud_session,
        events=events,
    )


class ClaudeManagedTurn(BaseModel):
    """Bounded input turn processed within a Claude managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    turn_id: NonBlankStr = Field(..., alias="turnId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    input_origin: ClaudeTurnInputOrigin = Field(..., alias="inputOrigin")
    state: ClaudeTurnState = Field(..., alias="state")
    summary: str | None = Field(None, alias="summary")
    started_at: datetime = Field(..., alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")

    @model_validator(mode="after")
    def _validate_datetimes(self) -> "ClaudeManagedTurn":
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            self.completed_at = self.completed_at.replace(tzinfo=UTC)
        return self


class ClaudeManagedWorkItem(BaseModel):
    """Event-bearing work unit emitted during a Claude managed turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    item_id: NonBlankStr = Field(..., alias="itemId")
    turn_id: NonBlankStr = Field(..., alias="turnId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    kind: ClaudeWorkItemKind = Field(..., alias="kind")
    status: ClaudeWorkItemStatus = Field(..., alias="status")
    event_name: ClaudeManagedWorkEventName | None = Field(None, alias="eventName")
    payload: dict[str, Any] = Field(default_factory=dict, alias="payload")
    started_at: datetime = Field(..., alias="startedAt")
    ended_at: datetime | None = Field(None, alias="endedAt")

    @field_validator("payload", mode="after")
    @classmethod
    def _validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="payload")

    @model_validator(mode="after")
    def _validate_invariants(self) -> "ClaudeManagedWorkItem":
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.ended_at is not None and self.ended_at.tzinfo is None:
            self.ended_at = self.ended_at.replace(tzinfo=UTC)
        if (
            self.event_name in CLAUDE_HOOK_WORK_EVENT_NAMES
            and self.kind != "hook_call"
        ):
            raise ValueError("Hook event names require hook_call work items")
        if self.event_name == "work.checkpoint.created" and self.kind != "checkpoint":
            raise ValueError(
                "Checkpoint and rewind event names require checkpoint or rewind work items"
            )
        if (
            self.event_name
            in {"work.rewind.started", "work.rewind.completed"}
            and self.kind != "rewind"
        ):
            raise ValueError(
                "Checkpoint and rewind event names require checkpoint or rewind work items"
            )
        return self


class ClaudeCheckpointCaptureDecision(BaseModel):
    """Documented checkpoint capture decision for one Claude runtime trigger."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    trigger: ClaudeCheckpointTrigger
    should_create_checkpoint: bool = Field(..., alias="shouldCreateCheckpoint")
    capture_mode: ClaudeCheckpointCaptureMode = Field(..., alias="captureMode")
    reason: NonBlankStr


def claude_checkpoint_capture_decision(
    trigger: ClaudeCheckpointTrigger,
) -> ClaudeCheckpointCaptureDecision:
    """Return the documented default checkpoint capture decision."""

    decisions: dict[
        ClaudeCheckpointTrigger, tuple[bool, ClaudeCheckpointCaptureMode, str]
    ] = {
        "user_prompt": (
            True,
            "conversation",
            "User prompts create conversation rewind points.",
        ),
        "tracked_file_edit": (
            True,
            "code_and_conversation",
            "Tracked file edits create restorable code and conversation state.",
        ),
        "bash_side_effect": (
            False,
            "skipped",
            "Bash side effects do not create code-state checkpoints by default.",
        ),
        "external_manual_edit": (
            True,
            "best_effort",
            "Manual external edits are represented as best-effort checkpoints.",
        ),
    }
    if trigger not in decisions:
        raise ValueError(f"Unsupported Claude checkpoint trigger: {trigger}")
    should_create, capture_mode, reason = decisions[trigger]
    return ClaudeCheckpointCaptureDecision(
        trigger=trigger,
        shouldCreateCheckpoint=should_create,
        captureMode=capture_mode,
        reason=reason,
    )


class ClaudeCheckpoint(BaseModel):
    """Bounded metadata for one Claude session checkpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    checkpoint_id: NonBlankStr = Field(..., alias="checkpointId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    turn_id: NonBlankStr | None = Field(None, alias="turnId")
    trigger: ClaudeCheckpointTrigger
    capture_mode: ClaudeCheckpointCaptureMode = Field(..., alias="captureMode")
    status: ClaudeCheckpointStatus = "captured"
    storage_ref: NonBlankStr = Field(..., alias="storageRef")
    is_active: bool = Field(False, alias="isActive")
    retention_state: ClaudeCheckpointRetentionState = Field(
        "addressable", alias="retentionState"
    )
    created_at: datetime = Field(..., alias="createdAt")
    expires_at: datetime | None = Field(None, alias="expiresAt")
    rewound_from_checkpoint_id: NonBlankStr | None = Field(
        None, alias="rewoundFromCheckpointId"
    )
    event_log_ref: NonBlankStr | None = Field(None, alias="eventLogRef")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_invariants(self) -> "ClaudeCheckpoint":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            self.expires_at = self.expires_at.replace(tzinfo=UTC)
        if (
            self.trigger == "bash_side_effect"
            and self.capture_mode in {"code", "code_and_conversation"}
        ):
            raise ValueError(
                "Bash side effects do not create code-state checkpoints by default"
            )
        if (
            self.trigger == "external_manual_edit"
            and self.capture_mode != "best_effort"
        ):
            raise ValueError("Manual external edits must use best_effort capture")
        return self


class ClaudeCheckpointIndex(BaseModel):
    """Operator-visible checkpoint metadata for one Claude session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    active_checkpoint_id: NonBlankStr | None = Field(
        None, alias="activeCheckpointId"
    )
    checkpoints: tuple[ClaudeCheckpoint, ...] = Field(...)
    generated_at: datetime = Field(..., alias="generatedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_invariants(self) -> "ClaudeCheckpointIndex":
        if self.generated_at.tzinfo is None:
            self.generated_at = self.generated_at.replace(tzinfo=UTC)
        checkpoint_ids = {checkpoint.checkpoint_id for checkpoint in self.checkpoints}
        if self.active_checkpoint_id is not None and (
            self.active_checkpoint_id not in checkpoint_ids
        ):
            raise ValueError("activeCheckpointId must refer to a listed checkpoint")
        for checkpoint in self.checkpoints:
            if checkpoint.session_id != self.session_id:
                raise ValueError("CheckpointIndex cannot mix sessionId values")
        return self


class ClaudeRewindRequest(BaseModel):
    """Validated request to restore or summarize from a Claude checkpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: NonBlankStr = Field(..., alias="requestId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    checkpoint_id: NonBlankStr = Field(..., alias="checkpointId")
    mode: ClaudeRewindMode
    instructions: str | None = None
    requested_at: datetime = Field(..., alias="requestedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("instructions", mode="after")
    @classmethod
    def _validate_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_non_blank(value, field_name="instructions")

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeRewindRequest":
        if self.requested_at.tzinfo is None:
            self.requested_at = self.requested_at.replace(tzinfo=UTC)
        return self


class ClaudeRewindResult(BaseModel):
    """Provenance-preserving result for a Claude rewind operation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    result_id: NonBlankStr = Field(..., alias="resultId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    request_id: NonBlankStr = Field(..., alias="requestId")
    source_checkpoint_id: NonBlankStr = Field(..., alias="sourceCheckpointId")
    previous_active_checkpoint_id: NonBlankStr | None = Field(
        None, alias="previousActiveCheckpointId"
    )
    active_checkpoint_id: NonBlankStr = Field(..., alias="activeCheckpointId")
    mode: ClaudeRewindMode
    status: ClaudeRewindStatus
    rewound_from_checkpoint_id: NonBlankStr | None = Field(
        None, alias="rewoundFromCheckpointId"
    )
    preserved_event_log_ref: NonBlankStr = Field(..., alias="preservedEventLogRef")
    summary_ref: NonBlankStr | None = Field(None, alias="summaryRef")
    code_state_restored: bool = Field(..., alias="codeStateRestored")
    conversation_state_restored: bool = Field(..., alias="conversationStateRestored")
    created_at: datetime = Field(..., alias="createdAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_invariants(self) -> "ClaudeRewindResult":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if self.status != "completed":
            return self
        if self.mode == "summarize_from_here":
            if self.summary_ref is None:
                raise ValueError("summarize_from_here requires summaryRef")
            if self.code_state_restored:
                raise ValueError("summarize_from_here cannot restore code state")
            if not self.conversation_state_restored:
                raise ValueError(
                    "summarize_from_here must restore conversation state"
                )
        if self.mode == "restore_code_only" and self.conversation_state_restored:
            raise ValueError("restore_code_only cannot restore conversation state")
        if self.mode == "restore_code_only" and not self.code_state_restored:
            raise ValueError("restore_code_only must restore code state")
        if self.mode == "restore_conversation_only" and self.code_state_restored:
            raise ValueError("restore_conversation_only cannot restore code state")
        if (
            self.mode == "restore_conversation_only"
            and not self.conversation_state_restored
        ):
            raise ValueError(
                "restore_conversation_only must restore conversation state"
            )
        if (
            self.mode == "restore_code_and_conversation"
            and not (self.code_state_restored and self.conversation_state_restored)
        ):
            raise ValueError(
                "restore_code_and_conversation must restore code and conversation state"
            )
        return self


def create_claude_checkpoint_work_item(
    *,
    item_id: str,
    turn_id: str,
    session_id: str,
    checkpoint_id: str,
    created_at: datetime,
    payload: dict[str, Any] | None = None,
) -> ClaudeManagedWorkItem:
    """Create bounded work evidence for checkpoint capture."""

    next_payload = dict(payload or {})
    next_payload["checkpointId"] = checkpoint_id
    return ClaudeManagedWorkItem(
        itemId=item_id,
        turnId=turn_id,
        sessionId=session_id,
        kind="checkpoint",
        status="completed",
        eventName="work.checkpoint.created",
        payload=next_payload,
        startedAt=created_at,
        endedAt=created_at,
    )


def create_claude_rewind_work_items(
    *,
    started_item_id: str,
    completed_item_id: str,
    turn_id: str,
    session_id: str,
    request_id: str,
    result_id: str,
    source_checkpoint_id: str,
    active_checkpoint_id: str,
    created_at: datetime,
) -> tuple[ClaudeManagedWorkItem, ClaudeManagedWorkItem]:
    """Create bounded work evidence for a completed rewind lifecycle."""

    started = ClaudeManagedWorkItem(
        itemId=started_item_id,
        turnId=turn_id,
        sessionId=session_id,
        kind="rewind",
        status="in_progress",
        eventName="work.rewind.started",
        payload={
            "requestId": request_id,
            "sourceCheckpointId": source_checkpoint_id,
        },
        startedAt=created_at,
    )
    completed = ClaudeManagedWorkItem(
        itemId=completed_item_id,
        turnId=turn_id,
        sessionId=session_id,
        kind="rewind",
        status="completed",
        eventName="work.rewind.completed",
        payload={
            "requestId": request_id,
            "resultId": result_id,
            "sourceCheckpointId": source_checkpoint_id,
            "activeCheckpointId": active_checkpoint_id,
        },
        startedAt=created_at,
        endedAt=created_at,
    )
    return started, completed


def claude_default_reinjection_policy(
    kind: ClaudeContextSourceKind,
) -> ClaudeContextReinjectionPolicy:
    """Return the documented default reinjection policy for a context kind."""

    policies: dict[ClaudeContextSourceKind, ClaudeContextReinjectionPolicy] = {
        "system_prompt": "always",
        "output_style": "always",
        "managed_claude_md": "always",
        "project_claude_md": "always",
        "local_claude_md": "always",
        "auto_memory": "always",
        "mcp_tool_manifest": "startup_refresh",
        "skill_description": "startup_refresh",
        "hook_injected_context": "configurable",
        "file_read": "never",
        "nested_claude_md": "on_demand",
        "path_rule": "on_demand",
        "invoked_skill_body": "budgeted",
        "runtime_summary": "on_demand",
        "transcript_summary": "always",
    }
    return policies[kind]


class ClaudeContextSegment(BaseModel):
    """Bounded metadata for one Claude context source."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    segment_id: NonBlankStr = Field(..., alias="segmentId")
    kind: ClaudeContextSourceKind
    source_ref: NonBlankStr = Field(..., alias="sourceRef")
    loaded_at: ClaudeContextLoadedAt = Field(..., alias="loadedAt")
    reinjection_policy: ClaudeContextReinjectionPolicy = Field(
        ..., alias="reinjectionPolicy"
    )
    guidance_role: ClaudeContextGuidanceRole = Field(..., alias="guidanceRole")
    token_budget_hint: int | None = Field(
        None, alias="tokenBudgetHint", ge=0
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_guidance_boundary(self) -> "ClaudeContextSegment":
        if (
            self.kind in _CLAUDE_CONTEXT_GUIDANCE_KINDS
            and self.guidance_role == "enforcement"
        ):
            raise ValueError(
                "Claude guidance and memory context cannot be enforcement sources"
            )
        return self


class ClaudeContextSnapshot(BaseModel):
    """Immutable context metadata for a Claude managed session epoch."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    snapshot_id: NonBlankStr = Field(..., alias="snapshotId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    turn_id: NonBlankStr | None = Field(None, alias="turnId")
    compaction_epoch: int = Field(..., alias="compactionEpoch", ge=0)
    segments: tuple[ClaudeContextSegment, ...] = Field(..., min_length=1)
    created_at: datetime = Field(..., alias="createdAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeContextSnapshot":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        return self


class ClaudeContextEvent(BaseModel):
    """Normalized event for Claude context loading and compaction."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_id: NonBlankStr = Field(..., alias="eventId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    turn_id: NonBlankStr | None = Field(None, alias="turnId")
    snapshot_id: NonBlankStr | None = Field(None, alias="snapshotId")
    work_item_id: NonBlankStr | None = Field(None, alias="workItemId")
    event_name: ClaudeContextEventName = Field(..., alias="eventName")
    occurred_at: datetime = Field(..., alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeContextEvent":
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=UTC)
        return self


class ClaudeContextCompactionResult(BaseModel):
    """Deterministic output for a Claude context compaction boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    snapshot: ClaudeContextSnapshot
    work_item: ClaudeManagedWorkItem = Field(..., alias="workItem")
    events: tuple[ClaudeContextEvent, ...]


class ClaudeChildWorkUsage(BaseModel):
    """Bounded usage accounting for Claude child work."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    input_tokens: int = Field(0, alias="inputTokens", ge=0)
    output_tokens: int = Field(0, alias="outputTokens", ge=0)
    total_tokens: int = Field(..., alias="totalTokens", ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_total(self) -> "ClaudeChildWorkUsage":
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError(
                "totalTokens must be greater than or equal to "
                "inputTokens + outputTokens"
            )
        return self


class ClaudeChildContext(BaseModel):
    """Parent-owned subagent child context for one Claude turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    child_context_id: NonBlankStr = Field(..., alias="childContextId")
    parent_session_id: NonBlankStr = Field(..., alias="parentSessionId")
    parent_turn_id: NonBlankStr = Field(..., alias="parentTurnId")
    profile: NonBlankStr
    context_window: Literal["isolated"] = Field("isolated", alias="contextWindow")
    return_shape: ClaudeChildContextReturnShape = Field(..., alias="returnShape")
    communication: ClaudeChildContextCommunication = "caller_only"
    lifecycle_owner: ClaudeChildContextLifecycleOwner = Field(
        "parent_turn", alias="lifecycleOwner"
    )
    status: ClaudeChildContextStatus
    usage: ClaudeChildWorkUsage | None = None
    started_at: datetime = Field(..., alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        compact = validate_compact_temporal_mapping(value, field_name="metadata")
        promotion_keys = {"promotedSessionId", "promotionTarget", "peerSessionId"}
        if promotion_keys.intersection(compact):
            raise ValueError(
                "Subagent promotion to sibling session is out of scope for MM-347"
            )
        return compact

    @model_validator(mode="after")
    def _validate_datetimes(self) -> "ClaudeChildContext":
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.completed_at is not None:
            if self.completed_at.tzinfo is None:
                self.completed_at = self.completed_at.replace(tzinfo=UTC)
            if self.completed_at < self.started_at:
                raise ValueError("completedAt cannot precede startedAt")
        return self


class ClaudeSessionGroup(BaseModel):
    """Grouped sibling sessions for a Claude agent team."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_group_id: NonBlankStr = Field(..., alias="sessionGroupId")
    lead_session_id: NonBlankStr = Field(..., alias="leadSessionId")
    status: ClaudeSessionGroupStatus
    usage: ClaudeChildWorkUsage | None = None
    created_at: datetime = Field(..., alias="createdAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_datetimes(self) -> "ClaudeSessionGroup":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if self.completed_at is not None:
            if self.completed_at.tzinfo is None:
                self.completed_at = self.completed_at.replace(tzinfo=UTC)
            if self.completed_at < self.created_at:
                raise ValueError("completedAt cannot precede createdAt")
        return self


class ClaudeTeamMemberSession(BaseModel):
    """Distinct managed session participating in a Claude agent team."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_group_id: NonBlankStr = Field(..., alias="sessionGroupId")
    role: ClaudeTeamMemberRole
    status: ClaudeTeamMemberStatus
    usage: ClaudeChildWorkUsage | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")


class ClaudeTeamMessage(BaseModel):
    """Direct peer message exchanged inside one Claude session group."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    message_id: NonBlankStr = Field(..., alias="messageId")
    session_group_id: NonBlankStr = Field(..., alias="sessionGroupId")
    sender_session_id: NonBlankStr = Field(..., alias="senderSessionId")
    peer_session_id: NonBlankStr = Field(..., alias="peerSessionId")
    sent_at: datetime = Field(..., alias="sentAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_message(self) -> "ClaudeTeamMessage":
        if self.sender_session_id == self.peer_session_id:
            raise ValueError("peerSessionId must differ from senderSessionId")
        if self.sent_at.tzinfo is None:
            self.sent_at = self.sent_at.replace(tzinfo=UTC)
        return self


class ClaudeChildWorkEvent(BaseModel):
    """Normalized event for Claude subagent and team child work."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_id: NonBlankStr = Field(..., alias="eventId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    turn_id: NonBlankStr | None = Field(None, alias="turnId")
    child_context_id: NonBlankStr | None = Field(None, alias="childContextId")
    session_group_id: NonBlankStr | None = Field(None, alias="sessionGroupId")
    peer_session_id: NonBlankStr | None = Field(None, alias="peerSessionId")
    event_name: ClaudeChildWorkEventName = Field(..., alias="eventName")
    occurred_at: datetime = Field(..., alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_event_shape(self) -> "ClaudeChildWorkEvent":
        if self.event_name.startswith("child."):
            if not self.child_context_id:
                raise ValueError("childContextId is required for subagent events")
            if not self.turn_id:
                raise ValueError("turnId is required for subagent events")
        if self.event_name.startswith("team.") and not self.session_group_id:
            raise ValueError("sessionGroupId is required for team events")
        if self.event_name == "team.message.sent" and not self.peer_session_id:
            raise ValueError("peerSessionId is required for team.message.sent")
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=UTC)
        return self


class ClaudeChildWorkFixtureFlow(BaseModel):
    """Deterministic provider-free fixture flow for child-work boundaries."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    parent_session: ClaudeManagedSession = Field(..., alias="parentSession")
    child_context: ClaudeChildContext = Field(..., alias="childContext")
    session_group: ClaudeSessionGroup = Field(..., alias="sessionGroup")
    team_members: tuple[ClaudeTeamMemberSession, ...] = Field(
        ..., alias="teamMembers", min_length=2
    )
    team_message: ClaudeTeamMessage = Field(..., alias="teamMessage")
    events: tuple[ClaudeChildWorkEvent, ...]


_PAYLOAD_LIGHT_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "sourcecode",
        "source_code",
        "transcript",
        "fullfileread",
        "full_file_read",
        "checkpointpayload",
        "checkpoint_payload",
        "localcache",
        "local_cache",
    }
)


def _validate_payload_light_metadata(
    value: dict[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    compact = validate_compact_temporal_mapping(value, field_name=field_name)
    _reject_payload_light_keys(compact, path=field_name)
    return compact


def _reject_payload_light_keys(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = key.replace("-", "_").replace(" ", "_").lower()
            compact = normalized.replace("_", "")
            if (
                normalized in _PAYLOAD_LIGHT_FORBIDDEN_KEYS
                or compact in _PAYLOAD_LIGHT_FORBIDDEN_KEYS
            ):
                raise ValueError(
                    f"{path} must remain payload-light; key {key!r} embeds "
                    "runtime-local payload content"
                )
            _reject_payload_light_keys(nested, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_payload_light_keys(nested, path=f"{path}[{index}]")


class ClaudeEventSubscription(BaseModel):
    """Bounded subscription request for Claude evidence streams."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    subscription_id: NonBlankStr = Field(..., alias="subscriptionId")
    subscription_type: ClaudeEventSubscriptionType = Field(
        ..., alias="subscriptionType"
    )
    scope_id: NonBlankStr = Field(..., alias="scopeId")
    event_families: tuple[ClaudeEventFamily, ...] = Field(
        ..., alias="eventFamilies", min_length=1
    )
    created_at: datetime = Field(..., alias="createdAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeEventSubscription":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        return self


class ClaudeEventEnvelope(BaseModel):
    """Append-only normalized event envelope for Claude governance telemetry."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_id: NonBlankStr = Field(..., alias="eventId")
    event_family: ClaudeEventFamily = Field(..., alias="eventFamily")
    event_name: NonBlankStr = Field(..., alias="eventName")
    session_id: NonBlankStr | None = Field(None, alias="sessionId")
    session_group_id: NonBlankStr | None = Field(None, alias="sessionGroupId")
    policy_envelope_id: NonBlankStr | None = Field(
        None, alias="policyEnvelopeId"
    )
    turn_id: NonBlankStr | None = Field(None, alias="turnId")
    work_item_id: NonBlankStr | None = Field(None, alias="workItemId")
    child_context_id: NonBlankStr | None = Field(None, alias="childContextId")
    surface_id: NonBlankStr | None = Field(None, alias="surfaceId")
    occurred_at: datetime = Field(..., alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_event_shape(self) -> "ClaudeEventEnvelope":
        allowed = CLAUDE_EVENT_NAMES_BY_FAMILY[self.event_family]
        if self.event_name not in allowed:
            raise ValueError(
                f"eventName {self.event_name!r} is not valid for "
                f"eventFamily {self.event_family!r}"
            )
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=UTC)
        if self.event_family in {
            "session",
            "surface",
            "policy",
            "turn",
            "work",
            "decision",
            "child_work",
        } and not self.session_id:
            raise ValueError("sessionId is required for Claude event envelopes")
        if self.event_family == "surface" and not self.surface_id:
            raise ValueError("surfaceId is required for surface events")
        if self.event_family == "policy" and not self.policy_envelope_id:
            raise ValueError("policyEnvelopeId is required for policy events")
        if self.event_family == "turn" and not self.turn_id:
            raise ValueError("turnId is required for turn events")
        if self.event_family == "work" and not self.work_item_id:
            raise ValueError("workItemId is required for work events")
        if self.event_family == "decision" and not self.turn_id:
            raise ValueError("turnId is required for decision events")
        if self.event_family == "child_work":
            if self.event_name.startswith("team.") and not self.session_group_id:
                raise ValueError("sessionGroupId is required for team events")
            if self.event_name.startswith("child.") and not self.child_context_id:
                raise ValueError("childContextId is required for child events")
        return self


class ClaudeStorageEvidence(BaseModel):
    """Payload-light evidence describing Claude central and runtime-local stores."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    evidence_id: NonBlankStr = Field(..., alias="evidenceId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    central_store: ClaudeCentralStoreKind = Field(..., alias="centralStore")
    stored_kinds: tuple[ClaudeStoredEvidenceKind, ...] = Field(
        ..., alias="storedKinds", min_length=1
    )
    artifact_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="artifactRefs"
    )
    runtime_local_payload_kinds: tuple[ClaudeRuntimeLocalPayloadKind, ...] = Field(
        default=(), alias="runtimeLocalPayloadKinds"
    )
    payload_light: bool = Field(True, alias="payloadLight")
    created_at: datetime = Field(..., alias="createdAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_evidence(self) -> "ClaudeStorageEvidence":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if self.payload_light:
            self.metadata = _validate_payload_light_metadata(
                self.metadata, field_name="metadata"
            )
        else:
            self.metadata = validate_compact_temporal_mapping(
                self.metadata, field_name="metadata"
            )
        return self


class ClaudeRetentionClass(BaseModel):
    """One policy-controlled Claude retention class."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    class_name: ClaudeRetentionClassName = Field(..., alias="className")
    retention_value: NonBlankStr = Field(..., alias="retentionValue")
    policy_controlled: bool = Field(..., alias="policyControlled")

    @model_validator(mode="after")
    def _validate_policy_controlled(self) -> "ClaudeRetentionClass":
        if not self.policy_controlled:
            raise ValueError("retention classes must be policy-controlled")
        return self


class ClaudeRetentionEvidence(BaseModel):
    """Policy-controlled retention evidence for Claude governance telemetry."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    retention_id: NonBlankStr = Field(..., alias="retentionId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    classes: tuple[ClaudeRetentionClass, ...] = Field(..., min_length=1)
    policy_ref: HandoffSeedArtifactRef = Field(..., alias="policyRef")
    created_at: datetime = Field(..., alias="createdAt")

    @model_validator(mode="after")
    def _validate_classes(self) -> "ClaudeRetentionEvidence":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        names = tuple(item.class_name for item in self.classes)
        if len(set(names)) != len(names):
            raise ValueError("retention classes must be unique")
        if set(names) != set(CLAUDE_REQUIRED_RETENTION_CLASSES):
            raise ValueError("all required retention classes must be present")
        return self


class ClaudeTelemetryMetric(BaseModel):
    """Normalized Claude OpenTelemetry metric in the shared schema."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    metric_name: ClaudeTelemetryMetricName = Field(..., alias="metricName")
    value: float
    dimensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("dimensions", mode="after")
    @classmethod
    def _validate_dimensions(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="dimensions")


class ClaudeTelemetrySpan(BaseModel):
    """Normalized Claude OpenTelemetry trace span."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    span_name: ClaudeTelemetrySpanName = Field(..., alias="spanName")
    duration_ms: int = Field(..., alias="durationMs", ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes", mode="after")
    @classmethod
    def _validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="attributes")


class ClaudeTelemetryEvidence(BaseModel):
    """Normalized telemetry evidence derived from Claude observations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    telemetry_id: NonBlankStr = Field(..., alias="telemetryId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    metrics: tuple[ClaudeTelemetryMetric, ...] = ()
    event_envelopes: tuple[ClaudeEventEnvelope, ...] = Field(
        default=(), alias="eventEnvelopes"
    )
    trace_spans: tuple[ClaudeTelemetrySpan, ...] = Field(
        default=(), alias="traceSpans"
    )
    created_at: datetime = Field(..., alias="createdAt")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeTelemetryEvidence":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        return self


class ClaudeUsageRollup(BaseModel):
    """Usage rollup across Claude governance dimensions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    usage_rollup_id: NonBlankStr = Field(..., alias="usageRollupId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_group_id: NonBlankStr | None = Field(None, alias="sessionGroupId")
    user_id: NonBlankStr = Field(..., alias="userId")
    workspace_id: NonBlankStr = Field(..., alias="workspaceId")
    runtime_family: ClaudeRuntimeFamily = Field(..., alias="runtimeFamily")
    provider_mode: ClaudeProviderMode = Field(..., alias="providerMode")
    token_direction: ClaudeUsageTokenDirection = Field(..., alias="tokenDirection")
    token_count: int = Field(..., alias="tokenCount", ge=0)
    child_context_id: NonBlankStr | None = Field(None, alias="childContextId")
    team_member_session_id: NonBlankStr | None = Field(
        None, alias="teamMemberSessionId"
    )
    included_in_parent_rollup: bool = Field(
        False, alias="includedInParentRollup"
    )
    created_at: datetime = Field(..., alias="createdAt")

    @model_validator(mode="after")
    def _validate_rollup(self) -> "ClaudeUsageRollup":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if self.child_context_id and self.team_member_session_id:
            raise ValueError(
                "childContextId and teamMemberSessionId cannot both be set"
            )
        if self.included_in_parent_rollup and not (
            self.child_context_id or self.team_member_session_id
        ):
            raise ValueError(
                "includedInParentRollup requires child or team rollup and "
                "cannot mark an independent parent rollup"
            )
        return self


class ClaudeGovernanceEvidence(BaseModel):
    """Auditor-facing governance evidence for one Claude managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    governance_id: NonBlankStr = Field(..., alias="governanceId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    policy_trust_level: ClaudePolicyTrustLevel = Field(..., alias="policyTrustLevel")
    provider_mode: ClaudeProviderMode = Field(..., alias="providerMode")
    execution_security_mode: ClaudeExecutionSecurityMode = Field(
        ..., alias="executionSecurityMode"
    )
    control_layers: tuple[ClaudeGovernanceControlLayer, ...] = Field(
        default=(), alias="controlLayers"
    )
    protected_path_policy: NonBlankStr | None = Field(
        None, alias="protectedPathPolicy"
    )
    hook_audits: tuple[ClaudeHookAudit, ...] = Field(default=(), alias="hookAudits")
    storage_evidence_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="storageEvidenceRefs"
    )
    retention_evidence_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="retentionEvidenceRefs"
    )
    telemetry_evidence_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="telemetryEvidenceRefs"
    )
    usage_rollup_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="usageRollupRefs"
    )
    created_at: datetime = Field(..., alias="createdAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_governance(self) -> "ClaudeGovernanceEvidence":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if "protected_paths" in self.control_layers and not self.protected_path_policy:
            raise ValueError(
                "protectedPathPolicy is required when protected_paths are governed"
            )
        return self


class ClaudeComplianceExportView(BaseModel):
    """Compliance export view over bounded governance telemetry evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    export_id: NonBlankStr = Field(..., alias="exportId")
    governance: ClaudeGovernanceEvidence
    storage_summary_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="storageSummaryRefs"
    )
    retention_summary_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="retentionSummaryRefs"
    )
    telemetry_summary_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="telemetrySummaryRefs"
    )
    usage_summary_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="usageSummaryRefs"
    )
    created_at: datetime = Field(..., alias="createdAt")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeComplianceExportView":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        return self


class ClaudeProviderDashboardSummary(BaseModel):
    """Provider-mode-aware dashboard summary derived from governance evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    dashboard_id: NonBlankStr = Field(..., alias="dashboardId")
    provider_mode: ClaudeProviderMode = Field(..., alias="providerMode")
    policy_trust_levels: tuple[ClaudePolicyTrustLevel, ...] = Field(
        default=(), alias="policyTrustLevels"
    )
    execution_security_modes: tuple[ClaudeExecutionSecurityMode, ...] = Field(
        default=(), alias="executionSecurityModes"
    )
    telemetry_evidence_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="telemetryEvidenceRefs"
    )
    usage_rollup_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="usageRollupRefs"
    )
    governance_evidence_refs: tuple[HandoffSeedArtifactRef, ...] = Field(
        default=(), alias="governanceEvidenceRefs"
    )
    created_at: datetime = Field(..., alias="createdAt")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeProviderDashboardSummary":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        return self


class ClaudeGovernanceTelemetryFixtureFlow(BaseModel):
    """Deterministic provider-free fixture flow for MM-349 boundaries."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    subscription: ClaudeEventSubscription
    events: tuple[ClaudeEventEnvelope, ...]
    storage_evidence: ClaudeStorageEvidence = Field(..., alias="storageEvidence")
    retention_evidence: ClaudeRetentionEvidence = Field(..., alias="retentionEvidence")
    telemetry_evidence: ClaudeTelemetryEvidence = Field(..., alias="telemetryEvidence")
    usage_rollups: tuple[ClaudeUsageRollup, ...] = Field(..., alias="usageRollups")
    governance_evidence: ClaudeGovernanceEvidence = Field(
        ..., alias="governanceEvidence"
    )
    compliance_export: ClaudeComplianceExportView = Field(..., alias="complianceExport")
    dashboard_summary: ClaudeProviderDashboardSummary = Field(
        ..., alias="dashboardSummary"
    )


def build_claude_governance_telemetry_fixture_flow(
    *,
    session_id: str,
    session_group_id: str,
    user_id: str,
    workspace_id: str,
    policy_envelope_id: str,
    created_at: datetime,
) -> ClaudeGovernanceTelemetryFixtureFlow:
    """Build a deterministic governance telemetry flow for boundary tests."""

    subscription = ClaudeEventSubscription(
        subscriptionId=f"{session_id}:subscription",
        subscriptionType="session",
        scopeId=session_id,
        eventFamilies=CLAUDE_EVENT_FAMILIES,
        createdAt=created_at,
    )
    events = (
        ClaudeEventEnvelope(
            eventId="event-session-active",
            eventFamily="session",
            eventName="session.active",
            sessionId=session_id,
            occurredAt=created_at,
        ),
        ClaudeEventEnvelope(
            eventId="event-surface-reconnected",
            eventFamily="surface",
            eventName="surface.connected",
            sessionId=session_id,
            surfaceId="surface-web",
            occurredAt=created_at,
        ),
        ClaudeEventEnvelope(
            eventId="event-policy-compiled",
            eventFamily="policy",
            eventName="policy.compiled",
            sessionId=session_id,
            policyEnvelopeId=policy_envelope_id,
            occurredAt=created_at,
        ),
        ClaudeEventEnvelope(
            eventId="event-turn-executing",
            eventFamily="turn",
            eventName="turn.executing",
            sessionId=session_id,
            turnId="turn-1",
            occurredAt=created_at,
        ),
        ClaudeEventEnvelope(
            eventId="event-work-checkpoint",
            eventFamily="work",
            eventName="work.checkpoint.created",
            sessionId=session_id,
            workItemId="work-checkpoint-1",
            occurredAt=created_at,
        ),
        ClaudeEventEnvelope(
            eventId="event-decision-allowed",
            eventFamily="decision",
            eventName="decision.allowed",
            sessionId=session_id,
            turnId="turn-1",
            occurredAt=created_at,
        ),
        ClaudeEventEnvelope(
            eventId="event-team-message",
            eventFamily="child_work",
            eventName="team.message.sent",
            sessionId=session_id,
            sessionGroupId=session_group_id,
            occurredAt=created_at,
        ),
    )
    storage_evidence = ClaudeStorageEvidence(
        evidenceId="storage-1",
        sessionId=session_id,
        centralStore="event_log",
        storedKinds=(
            "metadata",
            "event_envelope",
            "artifact_pointer",
            "telemetry_summary",
            "governance_summary",
        ),
        artifactRefs=(f"artifact://{session_id}/audit",),
        runtimeLocalPayloadKinds=(
            "transcript",
            "full_file_read",
            "checkpoint_payload",
            "local_cache",
        ),
        payloadLight=True,
        createdAt=created_at,
        metadata={"summaryRef": f"artifact://{session_id}/summary"},
    )
    retention_evidence = ClaudeRetentionEvidence(
        retentionId="retention-1",
        sessionId=session_id,
        classes=(
            ClaudeRetentionClass(
                className="hot_session_metadata",
                retentionValue="30d",
                policyControlled=True,
            ),
            ClaudeRetentionClass(
                className="hot_event_log",
                retentionValue="30d",
                policyControlled=True,
            ),
            ClaudeRetentionClass(
                className="usage_rollups",
                retentionValue="90d",
                policyControlled=True,
            ),
            ClaudeRetentionClass(
                className="audit_event_metadata",
                retentionValue="org_policy",
                policyControlled=True,
            ),
            ClaudeRetentionClass(
                className="checkpoint_payloads",
                retentionValue="runtime_local_default",
                policyControlled=True,
            ),
        ),
        policyRef="policy://retention/default",
        createdAt=created_at,
    )
    telemetry_evidence = ClaudeTelemetryEvidence(
        telemetryId="telemetry-1",
        sessionId=session_id,
        metrics=(
            ClaudeTelemetryMetric(
                metricName="managed_sessions_active",
                value=1,
                dimensions={"runtimeKind": "claude_code"},
            ),
            ClaudeTelemetryMetric(
                metricName="managed_usage_tokens",
                value=42,
                dimensions={"provider": "anthropic_api", "direction": "total"},
            ),
        ),
        eventEnvelopes=(events[0],),
        traceSpans=(
            ClaudeTelemetrySpan(
                spanName="session.bootstrap",
                durationMs=10,
                attributes={"provider": "anthropic_api"},
            ),
            ClaudeTelemetrySpan(
                spanName="turn.process",
                durationMs=32,
                attributes={"sessionKind": "managed"},
            ),
        ),
        createdAt=created_at,
    )
    usage_rollups = (
        ClaudeUsageRollup(
            usageRollupId="usage-input",
            sessionId=session_id,
            sessionGroupId=session_group_id,
            userId=user_id,
            workspaceId=workspace_id,
            runtimeFamily="claude_code",
            providerMode="anthropic_api",
            tokenDirection="input",
            tokenCount=20,
            childContextId="child-1",
            includedInParentRollup=True,
            createdAt=created_at,
        ),
        ClaudeUsageRollup(
            usageRollupId="usage-output",
            sessionId=session_id,
            sessionGroupId=session_group_id,
            userId=user_id,
            workspaceId=workspace_id,
            runtimeFamily="claude_code",
            providerMode="anthropic_api",
            tokenDirection="output",
            tokenCount=22,
            teamMemberSessionId="team-member-1",
            includedInParentRollup=True,
            createdAt=created_at,
        ),
        ClaudeUsageRollup(
            usageRollupId="usage-total",
            sessionId=session_id,
            sessionGroupId=session_group_id,
            userId=user_id,
            workspaceId=workspace_id,
            runtimeFamily="claude_code",
            providerMode="anthropic_api",
            tokenDirection="total",
            tokenCount=42,
            createdAt=created_at,
        ),
    )
    hook_audit = ClaudeHookAudit(
        auditId="hook-audit-1",
        sessionId=session_id,
        turnId="turn-1",
        hookName="audit-write",
        sourceScope="managed",
        eventType="PreToolUse",
        matcher="Write",
        outcome="allow",
        createdAt=created_at,
    )
    governance_evidence = ClaudeGovernanceEvidence(
        governanceId="governance-1",
        sessionId=session_id,
        policyTrustLevel="endpoint_enforced",
        providerMode="anthropic_api",
        executionSecurityMode="remote_control_projection",
        controlLayers=(
            "managed_settings_source_resolution",
            "permission_rules",
            "permission_mode",
            "protected_paths",
            "sandboxing",
            "hooks",
            "classifier_auto_mode",
            "interactive_dialogs",
            "runtime_isolation",
        ),
        protectedPathPolicy="protected paths require explicit operator approval",
        hookAudits=(hook_audit,),
        storageEvidenceRefs=("storage-1",),
        retentionEvidenceRefs=("retention-1",),
        telemetryEvidenceRefs=("telemetry-1",),
        usageRollupRefs=("usage-input", "usage-output", "usage-total"),
        createdAt=created_at,
    )
    compliance_export = ClaudeComplianceExportView(
        exportId="export-1",
        governance=governance_evidence,
        storageSummaryRefs=("storage-1",),
        retentionSummaryRefs=("retention-1",),
        telemetrySummaryRefs=("telemetry-1",),
        usageSummaryRefs=("usage-input", "usage-output", "usage-total"),
        createdAt=created_at,
    )
    dashboard_summary = ClaudeProviderDashboardSummary(
        dashboardId="dashboard-1",
        providerMode="anthropic_api",
        policyTrustLevels=("endpoint_enforced",),
        executionSecurityModes=("remote_control_projection",),
        telemetryEvidenceRefs=("telemetry-1",),
        usageRollupRefs=("usage-input", "usage-output", "usage-total"),
        governanceEvidenceRefs=("governance-1",),
        createdAt=created_at,
    )
    return ClaudeGovernanceTelemetryFixtureFlow(
        subscription=subscription,
        events=events,
        storageEvidence=storage_evidence,
        retentionEvidence=retention_evidence,
        telemetryEvidence=telemetry_evidence,
        usageRollups=usage_rollups,
        governanceEvidence=governance_evidence,
        complianceExport=compliance_export,
        dashboardSummary=dashboard_summary,
    )


def validate_claude_team_message_membership(
    *,
    message: ClaudeTeamMessage,
    members: tuple[ClaudeTeamMemberSession, ...],
) -> None:
    """Validate that a team message stays inside one session group."""

    by_session_id = {member.session_id: member for member in members}
    sender = by_session_id.get(message.sender_session_id)
    peer = by_session_id.get(message.peer_session_id)
    if sender is None or peer is None:
        raise ValueError("sender and peer must both be team members")
    if (
        sender.session_group_id != message.session_group_id
        or peer.session_group_id != message.session_group_id
    ):
        raise ValueError("sender and peer must belong to the same session group")


def build_claude_child_work_fixture_flow(
    *,
    parent_session_id: str,
    parent_turn_id: str,
    child_context_id: str,
    session_group_id: str,
    lead_session_id: str,
    teammate_session_id: str,
    message_id: str,
    created_at: datetime,
    metadata: dict[str, Any] | None = None,
) -> ClaudeChildWorkFixtureFlow:
    """Build a deterministic child-work flow for schema boundary tests."""

    parent_session = ClaudeManagedSession(
        sessionId=parent_session_id,
        executionOwner="local_process",
        state="active",
        primarySurface="terminal",
        projectionMode="primary",
        activeTurnId=parent_turn_id,
        createdBy="user",
        createdAt=created_at,
        updatedAt=created_at,
    )
    child_context = ClaudeChildContext(
        childContextId=child_context_id,
        parentSessionId=parent_session_id,
        parentTurnId=parent_turn_id,
        profile="researcher",
        returnShape="summary_plus_metadata",
        status="completed",
        usage=ClaudeChildWorkUsage(
            inputTokens=1000,
            outputTokens=400,
            totalTokens=1400,
            metadata={"rollupTarget": "parent_session"},
        ),
        startedAt=created_at,
        completedAt=created_at,
        metadata=metadata or {},
    )
    session_group = ClaudeSessionGroup(
        sessionGroupId=session_group_id,
        leadSessionId=lead_session_id,
        status="completed",
        usage=ClaudeChildWorkUsage(
            inputTokens=1200,
            outputTokens=800,
            totalTokens=2000,
            metadata={"rollupTarget": "session_group"},
        ),
        createdAt=created_at,
        completedAt=created_at,
    )
    lead = ClaudeTeamMemberSession(
        sessionId=lead_session_id,
        sessionGroupId=session_group_id,
        role="lead",
        status="completed",
        usage=ClaudeChildWorkUsage(
            inputTokens=700,
            outputTokens=500,
            totalTokens=1200,
            metadata={"rollupTarget": "session_group"},
        ),
    )
    teammate = ClaudeTeamMemberSession(
        sessionId=teammate_session_id,
        sessionGroupId=session_group_id,
        role="teammate",
        status="completed",
        usage=ClaudeChildWorkUsage(
            inputTokens=500,
            outputTokens=300,
            totalTokens=800,
            metadata={"rollupTarget": "session_group"},
        ),
    )
    team_message = ClaudeTeamMessage(
        messageId=message_id,
        sessionGroupId=session_group_id,
        senderSessionId=lead_session_id,
        peerSessionId=teammate_session_id,
        sentAt=created_at,
        metadata={"messageRef": f"artifact://messages/{message_id}"},
    )
    validate_claude_team_message_membership(
        message=team_message,
        members=(lead, teammate),
    )
    events = (
        ClaudeChildWorkEvent(
            eventId=f"{child_context_id}:started",
            sessionId=parent_session_id,
            turnId=parent_turn_id,
            childContextId=child_context_id,
            eventName="child.subagent.started",
            occurredAt=created_at,
        ),
        ClaudeChildWorkEvent(
            eventId=f"{child_context_id}:completed",
            sessionId=parent_session_id,
            turnId=parent_turn_id,
            childContextId=child_context_id,
            eventName="child.subagent.completed",
            occurredAt=created_at,
        ),
        ClaudeChildWorkEvent(
            eventId=f"{session_group_id}:created",
            sessionId=lead_session_id,
            sessionGroupId=session_group_id,
            eventName="team.group.created",
            occurredAt=created_at,
        ),
        ClaudeChildWorkEvent(
            eventId=f"{lead_session_id}:started",
            sessionId=lead_session_id,
            sessionGroupId=session_group_id,
            eventName="team.member.started",
            occurredAt=created_at,
        ),
        ClaudeChildWorkEvent(
            eventId=f"{teammate_session_id}:started",
            sessionId=teammate_session_id,
            sessionGroupId=session_group_id,
            eventName="team.member.started",
            occurredAt=created_at,
        ),
        ClaudeChildWorkEvent(
            eventId=f"{message_id}:sent",
            sessionId=lead_session_id,
            sessionGroupId=session_group_id,
            peerSessionId=teammate_session_id,
            eventName="team.message.sent",
            occurredAt=created_at,
        ),
        ClaudeChildWorkEvent(
            eventId=f"{lead_session_id}:completed",
            sessionId=lead_session_id,
            sessionGroupId=session_group_id,
            eventName="team.member.completed",
            occurredAt=created_at,
        ),
        ClaudeChildWorkEvent(
            eventId=f"{teammate_session_id}:completed",
            sessionId=teammate_session_id,
            sessionGroupId=session_group_id,
            eventName="team.member.completed",
            occurredAt=created_at,
        ),
        ClaudeChildWorkEvent(
            eventId=f"{session_group_id}:completed",
            sessionId=lead_session_id,
            sessionGroupId=session_group_id,
            eventName="team.group.completed",
            occurredAt=created_at,
        ),
    )
    return ClaudeChildWorkFixtureFlow(
        parentSession=parent_session,
        childContext=child_context,
        sessionGroup=session_group,
        teamMembers=(lead, teammate),
        teamMessage=team_message,
        events=events,
    )


def compact_claude_context_snapshot(
    *,
    snapshot: ClaudeContextSnapshot,
    snapshot_id: str,
    work_item_id: str,
    created_at: datetime,
    turn_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ClaudeContextCompactionResult:
    """Create a post-compaction snapshot and bounded work/event evidence."""

    next_turn_id = turn_id if turn_id is not None else snapshot.turn_id
    retained_segments = tuple(
        segment.model_copy(
            deep=True,
            update={
                "loaded_at": "post_compaction",
            }
        )
        for segment in snapshot.segments
        if segment.reinjection_policy in _CLAUDE_CONTEXT_RETAINED_POLICIES
    )
    next_snapshot = ClaudeContextSnapshot(
        snapshotId=snapshot_id,
        sessionId=snapshot.session_id,
        turnId=next_turn_id,
        compactionEpoch=snapshot.compaction_epoch + 1,
        segments=retained_segments,
        createdAt=created_at,
        metadata=metadata or {},
    )
    omitted_segment_ids = [
        segment.segment_id
        for segment in snapshot.segments
        if segment.reinjection_policy not in _CLAUDE_CONTEXT_RETAINED_POLICIES
    ]
    work_item = ClaudeManagedWorkItem(
        itemId=work_item_id,
        turnId=next_turn_id or "context-compaction",
        sessionId=snapshot.session_id,
        kind="compaction",
        status="completed",
        payload={
            "previousSnapshotId": snapshot.snapshot_id,
            "nextSnapshotId": next_snapshot.snapshot_id,
            "previousEpoch": snapshot.compaction_epoch,
            "nextEpoch": next_snapshot.compaction_epoch,
            "retainedSegmentIds": [
                segment.segment_id for segment in retained_segments
            ],
            "omittedSegmentIds": omitted_segment_ids,
        },
        startedAt=created_at,
        endedAt=created_at,
    )
    events = (
        ClaudeContextEvent(
            eventId=f"{work_item_id}:started",
            sessionId=snapshot.session_id,
            turnId=next_turn_id,
            snapshotId=snapshot.snapshot_id,
            workItemId=work_item_id,
            eventName="work.compaction.started",
            occurredAt=created_at,
            metadata={"previousSnapshotId": snapshot.snapshot_id},
        ),
        ClaudeContextEvent(
            eventId=f"{work_item_id}:completed",
            sessionId=snapshot.session_id,
            turnId=next_turn_id,
            snapshotId=next_snapshot.snapshot_id,
            workItemId=work_item_id,
            eventName="work.compaction.completed",
            occurredAt=created_at,
            metadata={
                "previousSnapshotId": snapshot.snapshot_id,
                "nextSnapshotId": next_snapshot.snapshot_id,
                "retainedSegmentCount": len(retained_segments),
                "omittedSegmentCount": len(omitted_segment_ids),
            },
        ),
    )
    return ClaudeContextCompactionResult(
        snapshot=next_snapshot,
        workItem=work_item,
        events=events,
    )


def _decision_event_for_outcome(
    outcome: ClaudeDecisionOutcome,
) -> ClaudeDecisionEventName:
    if outcome == "allowed":
        return "decision.allowed"
    if outcome == "asked":
        return "decision.asked"
    if outcome == "denied":
        return "decision.denied"
    if outcome == "deferred":
        return "decision.deferred"
    if outcome == "canceled":
        return "decision.canceled"
    if outcome == "mutated":
        return "decision.mutated"
    if outcome == "proposed":
        return "decision.proposed"
    return "decision.resolved"


class ClaudeDecisionPoint(BaseModel):
    """Normalized decision-stage record for Claude managed sessions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    decision_id: NonBlankStr = Field(..., alias="decisionId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    turn_id: NonBlankStr = Field(..., alias="turnId")
    work_item_id: NonBlankStr | None = Field(None, alias="workItemId")
    proposal_kind: ClaudeDecisionProposalKind = Field(..., alias="proposalKind")
    origin_stage: ClaudeDecisionStage = Field(..., alias="originStage")
    outcome: ClaudeDecisionOutcome = Field(..., alias="outcome")
    provenance_source: ClaudeDecisionProvenanceSource = Field(
        ..., alias="provenanceSource"
    )
    event_name: ClaudeDecisionEventName = Field(..., alias="eventName")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_invariants(self) -> "ClaudeDecisionPoint":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if (
            self.origin_stage == "protected_path_guard"
            and self.outcome == "allowed"
        ):
            raise ValueError("Protected path decisions cannot be allowed")
        if (
            self.origin_stage == "protected_path_guard"
            and self.provenance_source != "protected_path"
        ):
            raise ValueError(
                "Protected path decisions must use protected_path provenance"
            )
        if self.provenance_source == "protected_path" and (
            self.origin_stage != "protected_path_guard"
            or self.outcome not in {"asked", "denied", "deferred"}
        ):
            raise ValueError(
                "Protected path decisions must ask, deny, or defer at protected_path_guard"
            )
        if self.provenance_source == "classifier" and (
            self.origin_stage != "auto_mode_classifier"
        ):
            raise ValueError(
                "Classifier decisions must originate from auto_mode_classifier"
            )
        if self.provenance_source == "headless_policy" and (
            self.origin_stage != "interactive_prompt_or_headless_resolution"
            or self.outcome not in {"denied", "deferred"}
        ):
            raise ValueError("Headless decisions must deny or defer")
        return self

    @classmethod
    def protected_path(
        cls,
        *,
        decision_id: str,
        session_id: str,
        turn_id: str,
        proposal_kind: ClaudeDecisionProposalKind,
        outcome: Literal["asked", "denied", "deferred"],
        created_at: datetime,
        work_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ClaudeDecisionPoint":
        if outcome == "allowed":
            raise ValueError("Protected path decisions cannot be allowed")
        return cls(
            decisionId=decision_id,
            sessionId=session_id,
            turnId=turn_id,
            workItemId=work_item_id,
            proposalKind=proposal_kind,
            originStage="protected_path_guard",
            outcome=outcome,
            provenanceSource="protected_path",
            eventName=_decision_event_for_outcome(outcome),
            metadata=metadata or {},
            createdAt=created_at,
        )

    @classmethod
    def classifier(
        cls,
        *,
        decision_id: str,
        session_id: str,
        turn_id: str,
        proposal_kind: ClaudeDecisionProposalKind,
        outcome: ClaudeDecisionOutcome,
        created_at: datetime,
        work_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ClaudeDecisionPoint":
        return cls(
            decisionId=decision_id,
            sessionId=session_id,
            turnId=turn_id,
            workItemId=work_item_id,
            proposalKind=proposal_kind,
            originStage="auto_mode_classifier",
            outcome=outcome,
            provenanceSource="classifier",
            eventName=_decision_event_for_outcome(outcome),
            metadata=metadata or {},
            createdAt=created_at,
        )

    @classmethod
    def headless_resolution(
        cls,
        *,
        decision_id: str,
        session_id: str,
        turn_id: str,
        proposal_kind: ClaudeDecisionProposalKind,
        outcome: Literal["denied", "deferred"],
        created_at: datetime,
        work_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ClaudeDecisionPoint":
        if outcome not in {"denied", "deferred"}:
            raise ValueError("Headless decisions must deny or defer")
        return cls(
            decisionId=decision_id,
            sessionId=session_id,
            turnId=turn_id,
            workItemId=work_item_id,
            proposalKind=proposal_kind,
            originStage="interactive_prompt_or_headless_resolution",
            outcome=outcome,
            provenanceSource="headless_policy",
            eventName=_decision_event_for_outcome(outcome),
            metadata=metadata or {},
            createdAt=created_at,
        )

    @classmethod
    def hook_tightened(
        cls,
        *,
        decision_id: str,
        session_id: str,
        turn_id: str,
        proposal_kind: ClaudeDecisionProposalKind,
        origin_stage: Literal["pretool_hooks", "posttool_hooks"],
        outcome: Literal["asked", "denied", "deferred", "mutated"],
        created_at: datetime,
        work_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ClaudeDecisionPoint":
        if origin_stage not in {"pretool_hooks", "posttool_hooks"}:
            raise ValueError(
                "Hook decisions must originate from pretool_hooks or posttool_hooks"
            )
        next_metadata = dict(metadata or {})
        next_metadata["hookTightened"] = True
        return cls(
            decisionId=decision_id,
            sessionId=session_id,
            turnId=turn_id,
            workItemId=work_item_id,
            proposalKind=proposal_kind,
            originStage=origin_stage,
            outcome=outcome,
            provenanceSource="hook",
            eventName=_decision_event_for_outcome(outcome),
            metadata=next_metadata,
            createdAt=created_at,
        )


class ClaudeHookAudit(BaseModel):
    """Normalized Claude hook execution audit record."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    audit_id: NonBlankStr = Field(..., alias="auditId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    turn_id: NonBlankStr = Field(..., alias="turnId")
    decision_id: NonBlankStr | None = Field(None, alias="decisionId")
    hook_name: NonBlankStr = Field(..., alias="hookName")
    source_scope: ClaudeHookSourceScope = Field(..., alias="sourceScope")
    event_type: NonBlankStr = Field(..., alias="eventType")
    matcher: NonBlankStr = Field(..., alias="matcher")
    outcome: ClaudeHookOutcome = Field(..., alias="outcome")
    audit_data: dict[str, Any] = Field(default_factory=dict, alias="auditData")
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("audit_data", mode="after")
    @classmethod
    def _validate_audit_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="auditData")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeHookAudit":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        return self


class ClaudePolicyPermissions(BaseModel):
    """Compiled Claude permission controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mode: ClaudePermissionMode = "default"
    allow: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = Field(
        default=(), alias="protectedPaths"
    )
    auto_mode_enabled: bool = Field(False, alias="autoModeEnabled")
    bypass_disabled: bool = Field(False, alias="bypassDisabled")
    auto_disabled: bool = Field(False, alias="autoDisabled")


class ClaudePolicySandbox(BaseModel):
    """Compiled Claude sandbox controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool = False
    filesystem_scope: dict[str, Any] = Field(
        default_factory=dict, alias="filesystemScope"
    )
    network_scope: dict[str, Any] = Field(
        default_factory=dict, alias="networkScope"
    )

    @field_validator("filesystem_scope", "network_scope", mode="after")
    @classmethod
    def _validate_scope(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="scope")


class ClaudePolicyHooks(BaseModel):
    """Compiled Claude hook controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    allow_managed_only: bool = Field(False, alias="allowManagedOnly")
    registry_hash: str | None = Field(None, alias="registryHash")


class ClaudePolicyMcp(BaseModel):
    """Compiled Claude MCP controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    allowed_servers: tuple[str, ...] = Field(
        default=(), alias="allowedServers"
    )
    denied_servers: tuple[str, ...] = Field(default=(), alias="deniedServers")
    allow_managed_only: bool = Field(False, alias="allowManagedOnly")


class ClaudePolicyMemory(BaseModel):
    """Compiled Claude memory controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    include_auto_memory: bool = Field(True, alias="includeAutoMemory")
    managed_claude_md_enabled: bool = Field(
        False, alias="managedClaudeMdEnabled"
    )
    excludes: tuple[str, ...] = ()


class ClaudePolicyBootstrapTemplate(BaseModel):
    """Claude BootstrapPreferences represented as bootstrap templates only."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["bootstrap_template"] = "bootstrap_template"
    name: NonBlankStr
    value: Any


class ClaudePolicySource(BaseModel):
    """Candidate policy source for Claude managed policy resolution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_kind: ClaudePolicySourceKind = Field(..., alias="sourceKind")
    settings: dict[str, Any] = Field(default_factory=dict)
    fetch_state: ClaudePolicyFetchState = Field("fetched", alias="fetchState")
    supported: bool = True
    risky_controls: tuple[str, ...] = Field(
        default=(), alias="riskyControls"
    )
    version: str | None = None

    @field_validator("settings", mode="after")
    @classmethod
    def _validate_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="settings")


class ClaudePolicyEnvelope(BaseModel):
    """Versioned effective policy attached to a Claude managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    policy_envelope_id: NonBlankStr = Field(..., alias="policyEnvelopeId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    provider_mode: ClaudeProviderMode = Field(..., alias="providerMode")
    managed_source_kind: ClaudeManagedSourceKind = Field(
        ..., alias="managedSourceKind"
    )
    policy_fetch_state: ClaudePolicyFetchState = Field(
        ..., alias="policyFetchState"
    )
    managed_source_version: str | None = Field(
        None, alias="managedSourceVersion"
    )
    policy_trust_level: ClaudePolicyTrustLevel = Field(
        ..., alias="policyTrustLevel"
    )
    permissions: ClaudePolicyPermissions = Field(
        default_factory=ClaudePolicyPermissions
    )
    sandbox: ClaudePolicySandbox = Field(default_factory=ClaudePolicySandbox)
    hooks: ClaudePolicyHooks = Field(default_factory=ClaudePolicyHooks)
    mcp: ClaudePolicyMcp = Field(default_factory=ClaudePolicyMcp)
    memory: ClaudePolicyMemory = Field(default_factory=ClaudePolicyMemory)
    bootstrap_templates: tuple[ClaudePolicyBootstrapTemplate, ...] = Field(
        default=(), alias="bootstrapTemplates"
    )
    security_dialog_required: bool = Field(
        False, alias="securityDialogRequired"
    )
    version: int = Field(..., ge=1)
    effective_settings: dict[str, Any] = Field(
        default_factory=dict, alias="effectiveSettings"
    )
    observability_sources: tuple[ClaudePolicySourceKind, ...] = Field(
        default=(), alias="observabilitySources"
    )
    admin_visibility: dict[str, Any] = Field(
        default_factory=dict, alias="adminVisibility"
    )
    user_visibility: dict[str, Any] = Field(
        default_factory=dict, alias="userVisibility"
    )

    @field_validator(
        "effective_settings",
        "admin_visibility",
        "user_visibility",
        mode="after",
    )
    @classmethod
    def _validate_mapping(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="policy")


class ClaudePolicyHandshake(BaseModel):
    """Startup readiness after Claude policy resolution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    policy_envelope_id: NonBlankStr | None = Field(
        None, alias="policyEnvelopeId"
    )
    state: ClaudePolicyHandshakeState
    reason: str | None = None
    interactive: bool


class ClaudePolicyEvent(BaseModel):
    """Append-only Claude policy lifecycle event."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_id: NonBlankStr = Field(..., alias="eventId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    policy_envelope_id: NonBlankStr | None = Field(
        None, alias="policyEnvelopeId"
    )
    event_type: ClaudePolicyEventType = Field(..., alias="eventType")
    occurred_at: datetime = Field(..., alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudePolicyEvent":
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=UTC)
        return self


_MANAGED_SOURCE_ORDER: tuple[ClaudePolicySourceKind, ...] = (
    "server_managed",
    "endpoint_managed",
)
_LOWER_SCOPE_SOURCE_KINDS: frozenset[ClaudePolicySourceKind] = frozenset(
    {"local_project", "shared_project", "user", "cli"}
)


def _selected_managed_source(
    sources: tuple[ClaudePolicySource, ...],
) -> ClaudePolicySource | None:
    for source_kind in _MANAGED_SOURCE_ORDER:
        for source in sources:
            if (
                source.source_kind == source_kind
                and source.supported
                and bool(source.settings)
            ):
                return source
    return None


def _fail_closed_managed_source(
    sources: tuple[ClaudePolicySource, ...],
) -> ClaudePolicySource | None:
    server_source = next(
        (
            source
            for source in sources
            if source.source_kind == "server_managed" and source.supported
        ),
        None,
    )
    if server_source is not None:
        if server_source.fetch_state == "fail_closed":
            return server_source
        if server_source.settings:
            return None

    endpoint_source = next(
        (
            source
            for source in sources
            if source.source_kind == "endpoint_managed" and source.supported
        ),
        None,
    )
    if (
        endpoint_source is not None
        and endpoint_source.fetch_state == "fail_closed"
    ):
        return endpoint_source
    return None


def _bootstrap_templates(
    settings: dict[str, Any],
) -> tuple[ClaudePolicyBootstrapTemplate, ...]:
    raw_preferences = settings.get("bootstrapPreferences") or ()
    templates: list[ClaudePolicyBootstrapTemplate] = []
    if isinstance(raw_preferences, list | tuple):
        for index, item in enumerate(raw_preferences):
            if isinstance(item, dict):
                name = item.get("name") or f"bootstrap-{index + 1}"
                value = item.get("value")
            else:
                name = f"bootstrap-{index + 1}"
                value = item
            templates.append(
                ClaudePolicyBootstrapTemplate(name=str(name), value=value)
            )
    return tuple(templates)


def _effective_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in settings.items()
        if key not in {"bootstrapPreferences", "managedDefaults"}
    }


def _policy_permissions(settings: dict[str, Any]) -> ClaudePolicyPermissions:
    permissions = settings.get("permissions")
    if isinstance(permissions, dict):
        return ClaudePolicyPermissions.model_validate(permissions)
    return ClaudePolicyPermissions()


def _policy_sandbox(settings: dict[str, Any]) -> ClaudePolicySandbox:
    sandbox = settings.get("sandbox")
    if isinstance(sandbox, dict):
        return ClaudePolicySandbox.model_validate(sandbox)
    return ClaudePolicySandbox()


def _policy_hooks(settings: dict[str, Any]) -> ClaudePolicyHooks:
    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        return ClaudePolicyHooks.model_validate(hooks)
    return ClaudePolicyHooks()


def _policy_mcp(settings: dict[str, Any]) -> ClaudePolicyMcp:
    mcp = settings.get("mcp")
    if isinstance(mcp, dict):
        return ClaudePolicyMcp.model_validate(mcp)
    return ClaudePolicyMcp()


def _policy_memory(settings: dict[str, Any]) -> ClaudePolicyMemory:
    memory = settings.get("memory")
    if isinstance(memory, dict):
        return ClaudePolicyMemory.model_validate(memory)
    return ClaudePolicyMemory()


def _policy_trust_level(
    managed_source_kind: ClaudeManagedSourceKind,
) -> ClaudePolicyTrustLevel:
    if managed_source_kind == "endpoint_managed":
        return "endpoint_enforced"
    if managed_source_kind == "server_managed":
        return "server_managed_best_effort"
    return "unmanaged"


def _policy_event(
    *,
    policy_envelope_id: str | None,
    session_id: str,
    event_type: ClaudePolicyEventType,
    occurred_at: datetime,
    sequence: int,
    metadata: dict[str, Any] | None = None,
) -> ClaudePolicyEvent:
    stable_id_part = policy_envelope_id or session_id
    return ClaudePolicyEvent(
        eventId=f"{stable_id_part}:{sequence}:{event_type}",
        sessionId=session_id,
        policyEnvelopeId=policy_envelope_id,
        eventType=event_type,
        occurredAt=occurred_at,
        metadata=metadata or {},
    )


def resolve_claude_policy_envelope(
    *,
    session_id: str,
    policy_envelope_id: str,
    provider_mode: ClaudeProviderMode,
    sources: tuple[ClaudePolicySource, ...],
    version: int = 1,
    interactive: bool = False,
    fail_closed_on_refresh_failure: bool = False,
    occurred_at: datetime | None = None,
) -> tuple[
    ClaudePolicyEnvelope | None,
    ClaudePolicyHandshake,
    tuple[ClaudePolicyEvent, ...],
]:
    """Resolve compact Claude policy fixture sources into an effective envelope."""

    event_time = occurred_at or datetime.now(UTC)
    events: list[ClaudePolicyEvent] = [
        _policy_event(
            policy_envelope_id=policy_envelope_id,
            session_id=session_id,
            event_type="policy.fetch.started",
            occurred_at=event_time,
            sequence=1,
        )
    ]

    fail_closed_source = (
        _fail_closed_managed_source(sources)
        if fail_closed_on_refresh_failure
        else None
    )
    if fail_closed_source is not None:
        events.append(
            _policy_event(
                policy_envelope_id=None,
                session_id=session_id,
                event_type="policy.fetch.failed",
                occurred_at=event_time,
                sequence=2,
                metadata={
                    "fetchState": "fail_closed",
                    "sourceKind": fail_closed_source.source_kind,
                },
            )
        )
        return (
            None,
            ClaudePolicyHandshake(
                sessionId=session_id,
                policyEnvelopeId=None,
                state="fail_closed",
                reason="Policy refresh failed in fail-closed mode.",
                interactive=interactive,
            ),
            tuple(events),
        )

    selected_source = _selected_managed_source(sources)
    managed_source_kind: ClaudeManagedSourceKind = (
        selected_source.source_kind if selected_source is not None else "none"
    )
    settings = selected_source.settings if selected_source is not None else {}
    fetch_state: ClaudePolicyFetchState = (
        selected_source.fetch_state
        if selected_source is not None
        else "not_applicable"
    )
    source_version = selected_source.version if selected_source is not None else None
    trust_level = _policy_trust_level(managed_source_kind)
    risky_controls = selected_source.risky_controls if selected_source else ()
    security_dialog_required = bool(risky_controls)
    observability_sources = tuple(
        source.source_kind
        for source in sources
        if source.source_kind in _LOWER_SCOPE_SOURCE_KINDS and source.settings
    )

    fetch_event_type: ClaudePolicyEventType = (
        "policy.fetch.failed"
        if fetch_state in {"fetch_failed", "fail_closed"}
        else "policy.fetch.succeeded"
    )
    events.append(
        _policy_event(
            policy_envelope_id=policy_envelope_id,
            session_id=session_id,
            event_type=fetch_event_type,
            occurred_at=event_time,
            sequence=2,
            metadata={"fetchState": fetch_state},
        )
    )

    admin_visibility = {
        "fetchState": fetch_state,
        "managedSourceKind": managed_source_kind,
        "policyTrustLevel": trust_level,
        "observabilitySources": list(observability_sources),
    }
    user_visibility = {
        "status": "managed" if managed_source_kind != "none" else "unmanaged"
    }
    envelope = ClaudePolicyEnvelope(
        policyEnvelopeId=policy_envelope_id,
        sessionId=session_id,
        providerMode=provider_mode,
        managedSourceKind=managed_source_kind,
        policyFetchState=fetch_state,
        managedSourceVersion=source_version,
        policyTrustLevel=trust_level,
        permissions=_policy_permissions(settings),
        sandbox=_policy_sandbox(settings),
        hooks=_policy_hooks(settings),
        mcp=_policy_mcp(settings),
        memory=_policy_memory(settings),
        bootstrapTemplates=_bootstrap_templates(settings),
        securityDialogRequired=security_dialog_required,
        version=version,
        effectiveSettings=_effective_settings(settings),
        observabilitySources=observability_sources,
        adminVisibility=admin_visibility,
        userVisibility=user_visibility,
    )

    events.append(
        _policy_event(
            policy_envelope_id=policy_envelope_id,
            session_id=session_id,
            event_type="policy.compiled",
            occurred_at=event_time,
            sequence=3,
        )
    )
    events.append(
        _policy_event(
            policy_envelope_id=policy_envelope_id,
            session_id=session_id,
            event_type="policy.version.changed",
            occurred_at=event_time,
            sequence=4,
            metadata={"version": version},
        )
    )

    if security_dialog_required:
        handshake_state: ClaudePolicyHandshakeState = (
            "security_dialog_required" if interactive else "blocked"
        )
        reason = (
            "Risky managed controls require a security dialog."
            if interactive
            else "Risky managed controls require a security dialog in a non-interactive session."
        )
        events.append(
            _policy_event(
                policy_envelope_id=policy_envelope_id,
                session_id=session_id,
                event_type="policy.dialog.required",
                occurred_at=event_time,
                sequence=5,
                metadata={"riskyControls": list(risky_controls)},
            )
        )
    else:
        handshake_state = "ready"
        reason = None

    return (
        envelope,
        ClaudePolicyHandshake(
            sessionId=session_id,
            policyEnvelopeId=policy_envelope_id,
            state=handshake_state,
            reason=reason,
            interactive=interactive,
        ),
        tuple(events),
    )


__all__ = [
    "CODEX_MANAGED_SESSION_CONTROL_ACTIONS",
    "CLAUDE_CHILD_WORK_EVENT_NAMES",
    "CLAUDE_CHECKPOINT_CAPTURE_MODES",
    "CLAUDE_CHECKPOINT_RETENTION_STATES",
    "CLAUDE_CHECKPOINT_TRIGGERS",
    "CLAUDE_CHECKPOINT_WORK_EVENT_NAMES",
    "CLAUDE_CONTEXT_EVENT_NAMES",
    "CLAUDE_CONTEXT_ON_DEMAND_KINDS",
    "CLAUDE_CONTEXT_STARTUP_KINDS",
    "CLAUDE_DECISION_EVENT_NAMES",
    "CLAUDE_DECISION_STAGE_ORDER",
    "CLAUDE_EVENT_FAMILIES",
    "CLAUDE_EVENT_NAMES_BY_FAMILY",
    "CLAUDE_HOOK_WORK_EVENT_NAMES",
    "CLAUDE_REQUIRED_RETENTION_CLASSES",
    "CLAUDE_SURFACE_LIFECYCLE_EVENT_NAMES",
    "CLAUDE_TELEMETRY_METRIC_NAMES",
    "CLAUDE_TELEMETRY_SPAN_NAMES",
    "CLAUDE_TURN_EVENT_NAMES",
    "CLAUDE_WORK_EVENT_NAMES",
    "ClaudeCentralStoreKind",
    "ClaudeChildContext",
    "ClaudeChildContextCommunication",
    "ClaudeChildContextLifecycleOwner",
    "ClaudeChildContextReturnShape",
    "ClaudeChildContextStatus",
    "ClaudeChildWorkEvent",
    "ClaudeChildWorkEventName",
    "ClaudeChildWorkFixtureFlow",
    "ClaudeChildWorkUsage",
    "ClaudeContextCompactionResult",
    "ClaudeContextEvent",
    "ClaudeContextEventName",
    "ClaudeContextGuidanceRole",
    "ClaudeContextLoadedAt",
    "ClaudeContextReinjectionPolicy",
    "ClaudeContextSegment",
    "ClaudeContextSnapshot",
    "ClaudeContextSourceKind",
    "ClaudeCheckpoint",
    "ClaudeCheckpointCaptureDecision",
    "ClaudeCheckpointCaptureMode",
    "ClaudeCheckpointIndex",
    "ClaudeCheckpointRetentionState",
    "ClaudeCheckpointStatus",
    "ClaudeCheckpointTrigger",
    "ClaudeCheckpointWorkEventName",
    "ClaudeDecisionEventName",
    "ClaudeDecisionOutcome",
    "ClaudeDecisionPoint",
    "ClaudeDecisionProposalKind",
    "ClaudeDecisionProvenanceSource",
    "ClaudeDecisionStage",
    "ClaudeEventEnvelope",
    "ClaudeEventFamily",
    "ClaudeEventSubscription",
    "ClaudeEventSubscriptionType",
    "ClaudeExecutionOwner",
    "ClaudeExecutionSecurityMode",
    "ClaudeComplianceExportView",
    "ClaudeGovernanceControlLayer",
    "ClaudeGovernanceEvidence",
    "ClaudeGovernanceTelemetryFixtureFlow",
    "ClaudeHookAudit",
    "ClaudeHookOutcome",
    "ClaudeHookSourceScope",
    "ClaudeHookWorkEventName",
    "ClaudeManagedSession",
    "ClaudeManagedSourceKind",
    "ClaudeManagedTurn",
    "ClaudeManagedWorkEventName",
    "ClaudeManagedWorkItem",
    "ClaudePermissionMode",
    "ClaudePolicyBootstrapTemplate",
    "ClaudePolicyEnvelope",
    "ClaudePolicyEvent",
    "ClaudePolicyEventType",
    "ClaudePolicyFetchState",
    "ClaudePolicyHandshake",
    "ClaudePolicyHandshakeState",
    "ClaudePolicyHooks",
    "ClaudePolicyMcp",
    "ClaudePolicyMemory",
    "ClaudePolicyPermissions",
    "ClaudePolicySandbox",
    "ClaudePolicySource",
    "ClaudePolicySourceKind",
    "ClaudePolicyTrustLevel",
    "ClaudeProjectionMode",
    "ClaudeProviderMode",
    "ClaudeProviderDashboardSummary",
    "ClaudeRetentionClass",
    "ClaudeRetentionClassName",
    "ClaudeRetentionEvidence",
    "ClaudeRuntimeFamily",
    "ClaudeRewindMode",
    "ClaudeRewindRequest",
    "ClaudeRewindResult",
    "ClaudeRewindStatus",
    "ClaudeSessionCreatedBy",
    "ClaudeSessionGroup",
    "ClaudeSessionGroupStatus",
    "ClaudeSessionState",
    "ClaudeSessionEventName",
    "ClaudeStorageEvidence",
    "ClaudeStoredEvidenceKind",
    "ClaudeSurfaceBinding",
    "ClaudeSurfaceCapability",
    "ClaudeSurfaceConnectionState",
    "ClaudeSurfaceHandoffFixtureFlow",
    "ClaudeSurfaceKind",
    "ClaudeSurfaceLifecycleEvent",
    "ClaudeSurfaceLifecycleEventName",
    "ClaudeTelemetryEvidence",
    "ClaudeTelemetryMetric",
    "ClaudeTelemetryMetricName",
    "ClaudeTelemetrySpan",
    "ClaudeTelemetrySpanName",
    "ClaudeTeamMemberRole",
    "ClaudeTeamMemberSession",
    "ClaudeTeamMemberStatus",
    "ClaudeTeamMessage",
    "ClaudeTurnInputOrigin",
    "ClaudeTurnEventName",
    "ClaudeTurnState",
    "ClaudeUsageRollup",
    "ClaudeUsageTokenDirection",
    "ClaudeRuntimeLocalPayloadKind",
    "ClaudeWorkEventName",
    "ClaudeWorkItemKind",
    "ClaudeWorkItemStatus",
    "CodexManagedSessionArtifactsPublication",
    "CodexManagedSessionAttachRuntimeHandlesSignal",
    "CodexManagedSessionBinding",
    "CodexManagedSessionCancelUpdateRequest",
    "CodexManagedSessionClearRequest",
    "CodexManagedSessionClearUpdateRequest",
    "CodexManagedSessionHandle",
    "CodexManagedSessionInterruptRequest",
    "CodexManagedSessionLocator",
    "CodexManagedSessionPlaneContract",
    "CodexManagedSessionRecord",
    "CodexManagedSessionRequestTrackingEntry",
    "CodexManagedSessionSendFollowUpRequest",
    "CodexManagedSessionSnapshot",
    "CodexManagedSessionState",
    "CodexManagedSessionSteerRequest",
    "CodexManagedSessionSummary",
    "CodexManagedSessionTerminateUpdateRequest",
    "CodexManagedSessionTurnResponse",
    "CodexManagedSessionWorkflowControlRequest",
    "CodexManagedSessionWorkflowInput",
    "CodexManagedSessionWorkflowStatus",
    "FetchCodexManagedSessionSummaryRequest",
    "InterruptCodexManagedSessionTurnRequest",
    "LaunchCodexManagedSessionRequest",
    "ManagedSessionContainerBackend",
    "ManagedSessionControlAction",
    "ManagedSessionControlMode",
    "ManagedSessionHandleStatus",
    "ManagedSessionProtocol",
    "ManagedSessionRecordStatus",
    "ManagedSessionRequestTrackingStatus",
    "ManagedSessionTurnStatus",
    "PublishCodexManagedSessionArtifactsRequest",
    "build_claude_child_work_fixture_flow",
    "build_claude_governance_telemetry_fixture_flow",
    "build_claude_surface_handoff_fixture_flow",
    "claude_checkpoint_capture_decision",
    "claude_default_reinjection_policy",
    "classify_claude_execution_security_mode",
    "compact_claude_context_snapshot",
    "create_claude_checkpoint_work_item",
    "create_claude_rewind_work_items",
    "resolve_claude_policy_envelope",
    "SendCodexManagedSessionTurnRequest",
    "SteerCodexManagedSessionTurnRequest",
    "TerminateCodexManagedSessionRequest",
    "canonical_codex_managed_runtime_id",
    "validate_claude_team_message_membership",
]
