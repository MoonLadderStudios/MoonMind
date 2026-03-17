"""Canonical contracts for managed and external agent runtime execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentKind = Literal["external", "managed"]
AgentRunState = Literal[
    "queued",
    "launching",
    "running",
    "awaiting_callback",
    "awaiting_approval",
    "intervention_requested",
    "collecting_results",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
]
FailureClass = Literal[
    "user_error",
    "integration_error",
    "execution_error",
    "system_error",
]

TERMINAL_AGENT_RUN_STATES: frozenset[AgentRunState] = frozenset(
    {"completed", "failed", "cancelled", "timed_out"}
)
_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "credential",
    "api_key",
    "private_key",
)
_MAX_SUMMARY_CHARS = 4096


def is_terminal_agent_run_state(status: AgentRunState) -> bool:
    """Return whether one canonical run status is terminal."""

    return status in TERMINAL_AGENT_RUN_STATES


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS):
                return True
            if _contains_sensitive_key(nested):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _require_non_blank(value: str, *, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


class AgentExecutionRequest(BaseModel):
    """Canonical request payload for true agent runtime execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    agent_kind: AgentKind = Field(..., alias="agentKind")
    agent_id: str = Field(..., alias="agentId", min_length=1)
    execution_profile_ref: str = Field(..., alias="executionProfileRef", min_length=1)
    correlation_id: str = Field(..., alias="correlationId", min_length=1)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    instruction_ref: str | None = Field(None, alias="instructionRef")
    input_refs: list[str] = Field(default_factory=list, alias="inputRefs")
    expected_output_schema: dict[str, Any] = Field(
        default_factory=dict, alias="expectedOutputSchema"
    )
    workspace_spec: dict[str, Any] = Field(default_factory=dict, alias="workspaceSpec")
    parameters: dict[str, Any] = Field(default_factory=dict, alias="parameters")
    timeout_policy: dict[str, Any] = Field(default_factory=dict, alias="timeoutPolicy")
    retry_policy: dict[str, Any] = Field(default_factory=dict, alias="retryPolicy")
    approval_policy: dict[str, Any] = Field(
        default_factory=dict, alias="approvalPolicy"
    )
    callback_policy: dict[str, Any] = Field(
        default_factory=dict, alias="callbackPolicy"
    )

    @model_validator(mode="after")
    def _validate_contract(self) -> "AgentExecutionRequest":
        self.agent_id = _require_non_blank(self.agent_id, field_name="agentId")
        self.execution_profile_ref = _require_non_blank(
            self.execution_profile_ref, field_name="executionProfileRef"
        )
        self.correlation_id = _require_non_blank(
            self.correlation_id, field_name="correlationId"
        )
        self.idempotency_key = _require_non_blank(
            self.idempotency_key, field_name="idempotencyKey"
        )
        if self.instruction_ref is not None:
            self.instruction_ref = _require_non_blank(
                self.instruction_ref, field_name="instructionRef"
            )
        self.input_refs = [
            _require_non_blank(item, field_name="inputRefs[]") for item in self.input_refs
        ]

        if _contains_sensitive_key(self.parameters):
            raise ValueError("parameters must not contain raw credential keys")
        if _contains_sensitive_key(self.workspace_spec):
            raise ValueError("workspaceSpec must not contain raw credential keys")
        return self


class AgentRunHandle(BaseModel):
    """Run-handle payload returned by adapter start operations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(..., alias="runId", min_length=1)
    agent_kind: AgentKind = Field(..., alias="agentKind")
    agent_id: str = Field(..., alias="agentId", min_length=1)
    status: AgentRunState = Field(..., alias="status")
    started_at: datetime = Field(..., alias="startedAt")
    poll_hint_seconds: int | None = Field(None, alias="pollHintSeconds", ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def _normalize(self) -> "AgentRunHandle":
        self.run_id = _require_non_blank(self.run_id, field_name="runId")
        self.agent_id = _require_non_blank(self.agent_id, field_name="agentId")
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        return self


class AgentRunStatus(BaseModel):
    """Current lifecycle status for one active or completed run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(..., alias="runId", min_length=1)
    agent_kind: AgentKind = Field(..., alias="agentKind")
    agent_id: str = Field(..., alias="agentId", min_length=1)
    status: AgentRunState = Field(..., alias="status")
    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC), alias="observedAt"
    )
    poll_hint_seconds: int | None = Field(None, alias="pollHintSeconds", ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")

    @property
    def terminal(self) -> bool:
        return is_terminal_agent_run_state(self.status)

    @model_validator(mode="after")
    def _normalize(self) -> "AgentRunStatus":
        self.run_id = _require_non_blank(self.run_id, field_name="runId")
        self.agent_id = _require_non_blank(self.agent_id, field_name="agentId")
        if self.observed_at.tzinfo is None:
            self.observed_at = self.observed_at.replace(tzinfo=UTC)
        return self


class AgentRunResult(BaseModel):
    """Canonical result envelope for completed agent execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    output_refs: list[str] = Field(default_factory=list, alias="outputRefs")
    summary: str | None = Field(None, alias="summary")
    metrics: dict[str, Any] = Field(default_factory=dict, alias="metrics")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    failure_class: FailureClass | None = Field(None, alias="failureClass")
    provider_error_code: str | None = Field(None, alias="providerErrorCode")
    retry_recommendation: str | None = Field(None, alias="retryRecommendation")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def _validate_payload(self) -> "AgentRunResult":
        self.output_refs = [
            _require_non_blank(item, field_name="outputRefs[]") for item in self.output_refs
        ]
        if self.diagnostics_ref is not None:
            self.diagnostics_ref = _require_non_blank(
                self.diagnostics_ref, field_name="diagnosticsRef"
            )
        if self.summary is not None and len(self.summary) > _MAX_SUMMARY_CHARS:
            raise ValueError(
                f"summary must be <= {_MAX_SUMMARY_CHARS} characters to keep payloads compact"
            )
        return self


class ManagedAgentAuthProfile(BaseModel):
    """Named managed-runtime auth and execution policy contract."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    profile_id: str = Field(..., alias="profileId", min_length=1)
    runtime_id: str = Field(..., alias="runtimeId", min_length=1)
    auth_mode: str = Field(..., alias="authMode", min_length=1)
    volume_ref: str | None = Field(None, alias="volumeRef")
    account_label: str | None = Field(None, alias="accountLabel")
    max_parallel_runs: int = Field(..., alias="maxParallelRuns", ge=1)
    cooldown_after_429: int | None = Field(None, alias="cooldownAfter429", ge=0)
    rate_limit_policy: dict[str, Any] = Field(
        default_factory=dict, alias="rateLimitPolicy"
    )
    enabled: bool = Field(True, alias="enabled")

    @model_validator(mode="after")
    def _validate_policy(self) -> "ManagedAgentAuthProfile":
        self.profile_id = _require_non_blank(self.profile_id, field_name="profileId")
        self.runtime_id = _require_non_blank(self.runtime_id, field_name="runtimeId")
        self.auth_mode = _require_non_blank(self.auth_mode, field_name="authMode")
        if self.volume_ref is not None:
            self.volume_ref = _require_non_blank(self.volume_ref, field_name="volumeRef")
        if self.account_label is not None:
            self.account_label = _require_non_blank(
                self.account_label, field_name="accountLabel"
            )
        if _contains_sensitive_key(self.rate_limit_policy):
            raise ValueError("rateLimitPolicy must not contain raw credential keys")
        return self


WorkspaceMode = Literal["tempdir", "shared", "none"]


class ManagedRuntimeProfile(BaseModel):
    """Runtime-specific execution parameters for managed agent launches."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    runtime_id: str = Field(..., alias="runtimeId", min_length=1)
    command_template: list[str] = Field(..., alias="commandTemplate")
    default_model: str | None = Field(None, alias="defaultModel")
    default_effort: str | None = Field(None, alias="defaultEffort")
    default_timeout_seconds: int = Field(
        3600, alias="defaultTimeoutSeconds", ge=1
    )
    workspace_mode: WorkspaceMode = Field("tempdir", alias="workspaceMode")
    env_overrides: dict[str, str] = Field(
        default_factory=dict, alias="envOverrides"
    )

    @model_validator(mode="after")
    def _validate_profile(self) -> "ManagedRuntimeProfile":
        self.runtime_id = _require_non_blank(
            self.runtime_id, field_name="runtimeId"
        )
        if not self.command_template:
            raise ValueError("commandTemplate must not be empty")
        if _contains_sensitive_key(self.env_overrides):
            raise ValueError("envOverrides must not contain raw credential keys")
        return self


class ManagedRunRecord(BaseModel):
    """Durable run tracking record for managed agent executions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(..., alias="runId", min_length=1)
    agent_id: str = Field(..., alias="agentId", min_length=1)
    runtime_id: str = Field(..., alias="runtimeId", min_length=1)
    status: AgentRunState = Field(..., alias="status")
    pid: int | None = Field(None, alias="pid")
    exit_code: int | None = Field(None, alias="exitCode")
    started_at: datetime = Field(..., alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    last_heartbeat_at: datetime | None = Field(None, alias="lastHeartbeatAt")
    workspace_path: str | None = Field(None, alias="workspacePath")
    log_artifact_ref: str | None = Field(None, alias="logArtifactRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    error_message: str | None = Field(None, alias="errorMessage")
    failure_class: FailureClass | None = Field(None, alias="failureClass")

    @model_validator(mode="after")
    def _normalize(self) -> "ManagedRunRecord":
        self.run_id = _require_non_blank(self.run_id, field_name="runId")
        self.agent_id = _require_non_blank(self.agent_id, field_name="agentId")
        self.runtime_id = _require_non_blank(
            self.runtime_id, field_name="runtimeId"
        )
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.finished_at is not None and self.finished_at.tzinfo is None:
            self.finished_at = self.finished_at.replace(tzinfo=UTC)
        if (
            self.last_heartbeat_at is not None
            and self.last_heartbeat_at.tzinfo is None
        ):
            self.last_heartbeat_at = self.last_heartbeat_at.replace(tzinfo=UTC)
        return self



class ProviderCapabilityDescriptor(BaseModel):
    """Declares what an external agent provider supports at runtime."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    provider_name: str = Field(
        ...,
        alias="providerName",
        description="Canonical provider identifier (e.g. 'jules', 'codex_cloud').",
    )
    supports_callbacks: bool = Field(
        False,
        alias="supportsCallbacks",
        description="Whether the provider reliably delivers completion callbacks.",
    )
    supports_cancel: bool = Field(
        True,
        alias="supportsCancel",
        description="Whether the provider accepts best-effort cancel requests.",
    )
    supports_result_fetch: bool = Field(
        True,
        alias="supportsResultFetch",
        description="Whether terminal results can be fetched after completion.",
    )
    default_poll_hint_seconds: int = Field(
        15,
        alias="defaultPollHintSeconds",
        ge=1,
        description="Recommended initial polling interval in seconds.",
    )


__all__ = [
    "AgentExecutionRequest",
    "AgentKind",
    "AgentRunHandle",
    "AgentRunResult",
    "AgentRunState",
    "AgentRunStatus",
    "FailureClass",
    "ManagedAgentAuthProfile",
    "ManagedRunRecord",
    "ManagedRuntimeProfile",
    "ProviderCapabilityDescriptor",
    "TERMINAL_AGENT_RUN_STATES",
    "WorkspaceMode",
    "is_terminal_agent_run_state",
]
