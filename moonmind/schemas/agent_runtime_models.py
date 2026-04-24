"""Canonical contracts for managed and external agent runtime execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Mapping, NoReturn, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas._validation import require_non_blank
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionBinding,
    canonical_codex_managed_runtime_id,
)
from moonmind.schemas.temporal_payload_policy import validate_compact_temporal_mapping

AgentKind = Literal["external", "managed"]
ExternalExecutionStyle = Literal["polling", "streaming_gateway"]
AgentRunState = Literal[
    "queued",
    "awaiting_slot",
    "launching",
    "running",
    "awaiting_callback",
    "awaiting_feedback",
    "awaiting_approval",
    "intervention_requested",
    "collecting_results",
    "completed",
    "failed",
    "canceled",
    "timed_out",
]
FailureClass = Literal[
    "user_error",
    "integration_error",
    "execution_error",
    "system_error",
]

class UnsupportedStatusError(ValueError):
    """Raised when an unknown or unsupported provider status is encountered."""
    pass

def raise_unsupported_status(raw_status: str, context: str = "") -> NoReturn:
    """Consistently format and raise an UnsupportedStatusError."""
    msg = f"Unsupported status: {raw_status!r}"
    if context:
        msg += f" (context: {context})"
    raise UnsupportedStatusError(msg)

TERMINAL_AGENT_RUN_STATES: frozenset[AgentRunState] = frozenset(
    {"completed", "failed", "canceled", "timed_out"}
)
_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "credential",
    "api_key",
    "private_key",
)
_ALLOWED_SECRET_PASSTHROUGH_ENV_KEYS: frozenset[str] = frozenset(
    {"GITHUB_TOKEN"}
)
# Non-secret metadata keys allowed in envOverrides (values are refs / env names, not raw secrets).
_ALLOWED_MANAGED_LAUNCH_METADATA_KEYS: frozenset[str] = frozenset(
    {"MANAGED_API_KEY_REF", "MANAGED_API_KEY_TARGET_ENV", "MOONMIND_PROXY_TOKEN"}
)
_LEGACY_METADATA_MAP: tuple[tuple[str, str], ...] = (
    ("tracking_ref", "trackingRef"),
    ("trackingRef", "trackingRef"),
    ("provider_status", "providerStatus"),
    ("providerStatus", "providerStatus"),
    ("normalized_status", "normalizedStatus"),
    ("normalizedStatus", "normalizedStatus"),
    ("external_url", "externalUrl"),
    ("externalUrl", "externalUrl"),
    ("url", "externalUrl"),
)
_MAX_SUMMARY_CHARS = 4096
_DURABLE_RETRIEVAL_METADATA_KEYS: tuple[str, ...] = (
    "retrievedContextArtifactPath",
    "latestContextPackRef",
    "retrievedContextTransport",
    "retrievedContextItemCount",
    "retrievalDurabilityAuthority",
    "sessionContinuityCacheStatus",
)


def extract_durable_retrieval_metadata(
    parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(parameters, Mapping):
        return {}
    metadata = parameters.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    moonmind = metadata.get("moonmind")
    if not isinstance(moonmind, Mapping):
        return {}

    compact: dict[str, Any] = {}
    for key in _DURABLE_RETRIEVAL_METADATA_KEYS:
        value = moonmind.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                compact[key] = normalized
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            compact[key] = value
    return compact

def validate_codex_oauth_profile_refs(
    *,
    runtime_id: str | None,
    credential_source: str | None,
    runtime_materialization_mode: str | None,
    volume_ref: str | None,
    volume_mount_path: str | None,
    volume_ref_field_name: str = "volumeRef",
    volume_mount_path_field_name: str = "volumeMountPath",
) -> None:
    if (
        str(runtime_id or "").strip() != "codex_cli"
        or str(credential_source or "").strip() != "oauth_volume"
        or str(runtime_materialization_mode or "").strip() != "oauth_home"
    ):
        return

    missing: list[str] = []
    if not str(volume_ref or "").strip():
        missing.append(f"{volume_ref_field_name} is required")
    if not str(volume_mount_path or "").strip():
        missing.append(f"{volume_mount_path_field_name} is required")
    if missing:
        raise ValueError("; ".join(missing))

def is_terminal_agent_run_state(status: AgentRunState) -> bool:
    """Return whether one canonical run status is terminal."""

    return status in TERMINAL_AGENT_RUN_STATES

def _contains_sensitive_key(
    value: Any,
    *,
    allowed_sensitive_keys: frozenset[str] | None = None,
) -> bool:
    normalized_allowlist = (
        frozenset(item.upper() for item in allowed_sensitive_keys)
        if allowed_sensitive_keys
        else frozenset()
    )
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS):
                if (
                    not normalized.endswith("_ref")
                    and str(key).strip().upper() not in normalized_allowlist
                ):
                    # Allow synthetic proxy tokens to inhabit sensitive keys for provider routing
                    if isinstance(nested, str) and nested.startswith("mm-proxy-token:"):
                        continue
                    return True
            if _contains_sensitive_key(
                nested,
                allowed_sensitive_keys=allowed_sensitive_keys,
            ):
                return True
        return False
    if isinstance(value, list):
        return any(
            _contains_sensitive_key(
                item, allowed_sensitive_keys=allowed_sensitive_keys
            )
            for item in value
        )
    return False

class ProfileSelector(BaseModel):
    """Dynamic routing criteria for ProviderProfileManager."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    provider_id: str | None = Field(None, alias="providerId")
    tags_any: list[str] = Field(default_factory=list, alias="tagsAny")
    tags_all: list[str] = Field(default_factory=list, alias="tagsAll")
    runtime_materialization_mode: str | None = Field(
        None, alias="runtimeMaterializationMode"
    )

class AgentExecutionRequest(BaseModel):
    """Canonical request payload for true agent runtime execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    agent_kind: AgentKind = Field(..., alias="agentKind")
    agent_id: str = Field(..., alias="agentId", min_length=1)
    execution_profile_ref: str | None = Field(None, alias="executionProfileRef", min_length=1)
    correlation_id: str = Field(..., alias="correlationId", min_length=1)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    instruction_ref: str | None = Field(None, alias="instructionRef")
    resolved_skillset_ref: str | None = Field(None, alias="resolvedSkillsetRef")
    managed_session: CodexManagedSessionBinding | None = Field(
        None, alias="managedSession"
    )
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
    profile_selector: ProfileSelector = Field(
        default_factory=ProfileSelector, alias="profileSelector"
    )

    @model_validator(mode="after")
    def _validate_contract(self) -> "AgentExecutionRequest":
        self.agent_id = require_non_blank(self.agent_id, field_name="agentId")
        if self.execution_profile_ref is not None:
            if self.execution_profile_ref.strip().lower() == "auto":
                self.execution_profile_ref = None
            else:
                self.execution_profile_ref = require_non_blank(
                    self.execution_profile_ref, field_name="executionProfileRef"
                )
        self.correlation_id = require_non_blank(
            self.correlation_id, field_name="correlationId"
        )
        self.idempotency_key = require_non_blank(
            self.idempotency_key, field_name="idempotencyKey"
        )
        instruction_ref = self.instruction_ref
        if instruction_ref is not None:
            self.instruction_ref = require_non_blank(
                instruction_ref, field_name="instructionRef"
            )
        resolved_skillset_ref = self.resolved_skillset_ref
        if resolved_skillset_ref is not None:
            self.resolved_skillset_ref = require_non_blank(
                resolved_skillset_ref, field_name="resolvedSkillsetRef"
            )
        if self.managed_session is not None:
            canonical_runtime_id = canonical_codex_managed_runtime_id(self.agent_id)
            if self.agent_kind != "managed" or canonical_runtime_id is None:
                raise ValueError(
                    "managedSession is only supported for managed Codex runtimes"
                )
            if self.managed_session.runtime_id != canonical_runtime_id:
                raise ValueError(
                    "managedSession.runtimeId must match the managed Codex runtime"
                )
        self.input_refs = [
            require_non_blank(item, field_name="inputRefs[]")
            for item in self.input_refs
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

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _normalize(self) -> "AgentRunHandle":
        self.run_id = require_non_blank(self.run_id, field_name="runId")
        self.agent_id = require_non_blank(self.agent_id, field_name="agentId")
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

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @property
    def terminal(self) -> bool:
        return is_terminal_agent_run_state(self.status)

    @model_validator(mode="after")
    def _normalize(self) -> "AgentRunStatus":
        self.run_id = require_non_blank(self.run_id, field_name="runId")
        self.agent_id = require_non_blank(self.agent_id, field_name="agentId")
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

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_payload(self) -> "AgentRunResult":
        self.output_refs = [
            require_non_blank(item, field_name="outputRefs[]")
            for item in self.output_refs
        ]
        diagnostics_ref = self.diagnostics_ref
        if diagnostics_ref is not None:
            self.diagnostics_ref = require_non_blank(
                diagnostics_ref, field_name="diagnosticsRef"
            )
        summary = self.summary
        if summary is not None and len(summary) > _MAX_SUMMARY_CHARS:
            raise ValueError(
                f"summary must be <= {_MAX_SUMMARY_CHARS} characters to keep payloads compact"
            )
        return self

class ManagedAgentProviderProfile(BaseModel):
    """Named managed-runtime provider profile contract.

    Aligned with the DB model and provider-profile contract.  Uses
    ``credential_source`` and ``runtime_materialization_mode`` instead of the
    legacy ``auth_mode`` field (which is rejected by ``extra="forbid"``).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # -- Core identity --
    profile_id: str = Field(..., alias="profileId", min_length=1)
    runtime_id: str = Field(..., alias="runtimeId", min_length=1)
    provider_id: str | None = Field(None, alias="providerId")
    provider_label: str | None = Field(None, alias="providerLabel")
    default_model: str | None = Field(None, alias="defaultModel")
    model_overrides: dict[str, str] = Field(default_factory=dict, alias="modelOverrides")

    # -- Credential & materialization strategy (required) --
    credential_source: str = Field(..., alias="credentialSource", min_length=1)
    runtime_materialization_mode: str = Field(
        ..., alias="runtimeMaterializationMode", min_length=1
    )

    # -- Policy & routing --
    tags: list[str] = Field(default_factory=list, alias="tags")
    priority: int = Field(default=100, alias="priority", ge=0)
    max_parallel_runs: int = Field(default=1, alias="maxParallelRuns", ge=1)
    cooldown_after_429_seconds: int = Field(
        default=900, alias="cooldownAfter429Seconds", ge=0
    )
    rate_limit_policy: dict[str, Any] = Field(
        default_factory=dict, alias="rateLimitPolicy"
    )
    enabled: bool = Field(True, alias="enabled")

    # -- Volume & account binding (optional) --
    volume_ref: str | None = Field(None, alias="volumeRef")
    account_label: str | None = Field(None, alias="accountLabel")
    volume_mount_path: str | None = Field(None, alias="volumeMountPath")
    owner_user_id: str | None = Field(None, alias="ownerUserId")

    # -- Environment & config materialization --
    clear_env_keys: list[str] = Field(default_factory=list, alias="clearEnvKeys")
    env_template: dict[str, Any] = Field(default_factory=dict, alias="envTemplate")
    file_templates: list[RuntimeFileTemplate] = Field(
        default_factory=list, alias="fileTemplates"
    )
    home_path_overrides: dict[str, str] = Field(
        default_factory=dict, alias="homePathOverrides"
    )
    command_behavior: dict[str, Any] = Field(
        default_factory=dict, alias="commandBehavior"
    )
    secret_refs: dict[str, str] = Field(default_factory=dict, alias="secretRefs")

    # -- Lifecycle limits --
    max_lease_duration_seconds: int = Field(
        default=7200, alias="maxLeaseDurationSeconds", ge=60
    )

    _ALLOWED_CREDENTIAL_SOURCES: frozenset[str] = frozenset(
        {"oauth_volume", "secret_ref", "none"}
    )
    _ALLOWED_MATERIALIZATION_MODES: frozenset[str] = frozenset(
        {"oauth_home", "api_key_env", "env_bundle", "config_bundle", "composite"}
    )

    @model_validator(mode="after")
    def _validate_policy(self) -> "ManagedAgentProviderProfile":
        self.profile_id = require_non_blank(self.profile_id, field_name="profileId")
        self.runtime_id = require_non_blank(self.runtime_id, field_name="runtimeId")
        self.credential_source = require_non_blank(
            self.credential_source, field_name="credentialSource"
        )
        self.runtime_materialization_mode = require_non_blank(
            self.runtime_materialization_mode, field_name="runtimeMaterializationMode"
        )
        volume_ref = self.volume_ref
        if volume_ref is not None:
            self.volume_ref = require_non_blank(volume_ref, field_name="volumeRef")
        volume_mount_path = self.volume_mount_path
        if volume_mount_path is not None:
            self.volume_mount_path = require_non_blank(
                volume_mount_path, field_name="volumeMountPath"
            )
        account_label = self.account_label
        if account_label is not None:
            self.account_label = require_non_blank(
                account_label, field_name="accountLabel"
            )
        if _contains_sensitive_key(self.rate_limit_policy):
            raise ValueError("rateLimitPolicy must not contain raw credential keys")

        if self.credential_source not in self._ALLOWED_CREDENTIAL_SOURCES:
            allowed = ", ".join(sorted(self._ALLOWED_CREDENTIAL_SOURCES))
            raise ValueError(
                f"credentialSource must be one of: {allowed}; "
                f"got {self.credential_source!r}"
            )

        if self.runtime_materialization_mode not in self._ALLOWED_MATERIALIZATION_MODES:
            allowed = ", ".join(sorted(self._ALLOWED_MATERIALIZATION_MODES))
            raise ValueError(
                f"runtimeMaterializationMode must be one of: {allowed}; "
                f"got {self.runtime_materialization_mode!r}"
            )

        validate_codex_oauth_profile_refs(
            runtime_id=self.runtime_id,
            credential_source=self.credential_source,
            runtime_materialization_mode=self.runtime_materialization_mode,
            volume_ref=self.volume_ref,
            volume_mount_path=self.volume_mount_path,
        )

        return self

WorkspaceMode = Literal["tempdir", "shared", "none"]

class RuntimeFileTemplate(BaseModel):
    """Path-aware file materialization contract for managed runtime launch."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    path: str = Field(..., alias="path", min_length=1)
    format: str = Field("text", alias="format")
    merge_strategy: str = Field("replace", alias="mergeStrategy")
    content_template: Any = Field(default="", alias="contentTemplate")
    permissions: str | int | None = Field(None, alias="permissions")

    @model_validator(mode="after")
    def _validate_template(self) -> "RuntimeFileTemplate":
        self.path = require_non_blank(self.path, field_name="path")
        normalized_format = str(self.format or "text").strip().lower()
        if normalized_format not in {"text", "toml", "json"}:
            raise ValueError(
                "fileTemplates[].format must be one of: text, toml, json"
            )
        self.format = normalized_format

        normalized_merge = str(self.merge_strategy or "replace").strip().lower()
        if normalized_merge != "replace":
            raise ValueError(
                "fileTemplates[].mergeStrategy must be 'replace'"
            )
        self.merge_strategy = normalized_merge
        return self

class ManagedRuntimeProfile(BaseModel):
    """Runtime-specific execution parameters for managed agent launches."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    runtime_id: str = Field(..., alias="runtimeId", min_length=1)
    profile_id: str | None = Field(None, alias="profileId")
    provider_id: str | None = Field(None, alias="providerId")
    provider_label: str | None = Field(None, alias="providerLabel")
    auth_mode: str | None = Field(None, alias="authMode")
    credential_source: str | None = Field(None, alias="credentialSource")
    runtime_materialization_mode: str | None = Field(None, alias="runtimeMaterializationMode")
    command_behavior: dict[str, Any] = Field(default_factory=dict, alias="commandBehavior")
    command_template: list[str] = Field(default_factory=list, alias="commandTemplate")
    default_model: str | None = Field(None, alias="defaultModel")
    model_overrides: dict[str, str] = Field(default_factory=dict, alias="modelOverrides")
    default_effort: str | None = Field(None, alias="defaultEffort")
    default_timeout_seconds: int = Field(3600, alias="defaultTimeoutSeconds", ge=1)
    workspace_mode: WorkspaceMode = Field("tempdir", alias="workspaceMode")
    env_overrides: dict[str, str] = Field(default_factory=dict, alias="envOverrides")
    env_template: dict[str, Any] = Field(default_factory=dict, alias="envTemplate")
    file_templates: list[RuntimeFileTemplate] = Field(
        default_factory=list, alias="fileTemplates"
    )
    home_path_overrides: dict[str, str] = Field(default_factory=dict, alias="homePathOverrides")
    passthrough_env_keys: list[str] = Field(default_factory=list, alias="passthroughEnvKeys")
    clear_env_keys: list[str] = Field(default_factory=list, alias="clearEnvKeys")
    secret_refs: dict[str, str | dict[str, str]] = Field(default_factory=dict, alias="secretRefs")
    volume_ref: str | None = Field(None, alias="volumeRef")
    volume_mount_path: str | None = Field(None, alias="volumeMountPath")

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_file_templates_payload(
        cls,
        data: Any,
    ) -> Any:
        if not isinstance(data, dict):
            return data

        for field_name in ("fileTemplates", "file_templates"):
            raw_file_templates = data.get(field_name)
            if not isinstance(raw_file_templates, dict):
                continue

            coerced_templates = [
                {
                    "path": str(path),
                    "format": "text",
                    "mergeStrategy": "replace",
                    "contentTemplate": content,
                }
                for path, content in raw_file_templates.items()
            ]
            return {
                **data,
                field_name: coerced_templates,
            }

        return data

    @model_validator(mode="after")
    def _validate_profile(self) -> "ManagedRuntimeProfile":
        self.runtime_id = require_non_blank(
            self.runtime_id, field_name="runtimeId"
        )
        if _contains_sensitive_key(
            self.env_overrides,
            allowed_sensitive_keys=_ALLOWED_MANAGED_LAUNCH_METADATA_KEYS,
        ):
            raise ValueError("envOverrides must not contain raw credential keys")
        if self.volume_ref is not None:
            self.volume_ref = require_non_blank(
                self.volume_ref,
                field_name="volumeRef",
            )
        if self.volume_mount_path is not None:
            self.volume_mount_path = require_non_blank(
                self.volume_mount_path,
                field_name="volumeMountPath",
            )

        normalized_passthrough: list[str] = []
        seen: set[str] = set()
        for key in self.passthrough_env_keys:
            normalized_key = require_non_blank(
                str(key), field_name="passthroughEnvKeys[]"
            ).upper()
            if normalized_key not in _ALLOWED_SECRET_PASSTHROUGH_ENV_KEYS:
                supported = ", ".join(sorted(_ALLOWED_SECRET_PASSTHROUGH_ENV_KEYS))
                raise ValueError(
                    "passthroughEnvKeys contains unsupported key "
                    f"'{normalized_key}'. Supported keys: {supported}"
                )
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            normalized_passthrough.append(normalized_key)
        self.passthrough_env_keys = normalized_passthrough
        return self

class ManagedRunRecord(BaseModel):
    """Durable run tracking record for managed agent executions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(..., alias="runId", min_length=1)
    workflow_id: str | None = Field(None, alias="workflowId")
    agent_id: str = Field(..., alias="agentId", min_length=1)
    runtime_id: str = Field(..., alias="runtimeId", min_length=1)
    status: AgentRunState = Field(..., alias="status")
    pid: int | None = Field(None, alias="pid")
    exit_code: int | None = Field(None, alias="exitCode")
    started_at: datetime = Field(..., alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    last_heartbeat_at: datetime | None = Field(None, alias="lastHeartbeatAt")
    workspace_path: str | None = Field(None, alias="workspacePath")
    # deprecated: use stdout_artifact_ref and stderr_artifact_ref instead
    log_artifact_ref: str | None = Field(None, alias="logArtifactRef")
    stdout_artifact_ref: str | None = Field(None, alias="stdoutArtifactRef")
    stderr_artifact_ref: str | None = Field(None, alias="stderrArtifactRef")
    merged_log_artifact_ref: str | None = Field(None, alias="mergedLogArtifactRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    observability_events_ref: str | None = Field(None, alias="observabilityEventsRef")
    last_log_offset: int | None = Field(None, alias="lastLogOffset")
    last_log_at: datetime | None = Field(None, alias="lastLogAt")
    error_message: str | None = Field(None, alias="errorMessage")
    failure_class: FailureClass | None = Field(None, alias="failureClass")
    provider_error_code: str | None = Field(None, alias="providerErrorCode")
    # Capability metadata indicating if live streaming is supported for this run
    live_stream_capable: bool | None = Field(None, alias="liveStreamCapable")
    session_id: str | None = Field(None, alias="sessionId")
    session_epoch: int | None = Field(None, alias="sessionEpoch", ge=1)
    container_id: str | None = Field(None, alias="containerId")
    thread_id: str | None = Field(None, alias="threadId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")

    @model_validator(mode="after")
    def _normalize(self) -> "ManagedRunRecord":
        self.run_id = require_non_blank(self.run_id, field_name="runId")
        if self.workflow_id is not None:
            self.workflow_id = require_non_blank(
                self.workflow_id, field_name="workflowId"
            )
        if self.observability_events_ref is not None:
            self.observability_events_ref = require_non_blank(
                self.observability_events_ref,
                field_name="observabilityEventsRef",
            )
        self.agent_id = require_non_blank(self.agent_id, field_name="agentId")
        self.runtime_id = require_non_blank(
            self.runtime_id, field_name="runtimeId"
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
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        
        finished_at = self.finished_at
        if finished_at is not None:
            if finished_at.tzinfo is None:
                self.finished_at = finished_at.replace(tzinfo=UTC)
                
        last_heartbeat_at = self.last_heartbeat_at
        if last_heartbeat_at is not None:
            if last_heartbeat_at.tzinfo is None:
                self.last_heartbeat_at = last_heartbeat_at.replace(tzinfo=UTC)
                
        last_log_at = self.last_log_at
        if last_log_at is not None:
            if last_log_at.tzinfo is None:
                self.last_log_at = last_log_at.replace(tzinfo=UTC)
                
        return self

ObservabilityEventKind = Literal[
    "stdout_chunk",
    "stderr_chunk",
    "system_annotation",
    "session_started",
    "session_resumed",
    "turn_started",
    "turn_completed",
    "turn_interrupted",
    "session_cleared",
    "session_terminated",
    "session_reset_boundary",
    "approval_requested",
    "approval_resolved",
    "summary_published",
    "checkpoint_published",
    "reset_boundary_published",
]

class RunObservabilityEvent(BaseModel):
    """Canonical MoonMind-owned observability event for live and historical logs."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    run_id: str | None = Field(
        None,
        alias="runId",
        description="Task-run identifier for filtering shared observability history",
    )
    sequence: int = Field(..., description="Monotonically increasing sequence number")
    stream: Literal["stdout", "stderr", "system", "session"] = Field(
        ..., description="Standard output, standard error, system, or session event log"
    )
    timestamp: str = Field(..., description="ISO-8601 formatted timestamp")
    text: str = Field(..., description="Decoded output text buffer content")
    offset: int | None = Field(None, description="Global byte offset of this chunk")
    kind: ObservabilityEventKind | None = Field(
        None,
        description="Optional structured observability event kind",
    )
    session_id: str | None = Field(
        None,
        alias="sessionId",
        description="Managed-session id when the event is session-scoped",
    )
    session_epoch: int | None = Field(
        None,
        alias="sessionEpoch",
        ge=1,
        description="Managed-session epoch when the event is session-scoped",
    )
    container_id: str | None = Field(
        None,
        alias="containerId",
        description="Managed-session container id when available",
    )
    thread_id: str | None = Field(
        None,
        alias="threadId",
        description="Managed-session logical thread id when available",
    )
    turn_id: str | None = Field(
        None,
        alias="turnId",
        description="Managed-session turn id when available",
    )
    active_turn_id: str | None = Field(
        None,
        alias="activeTurnId",
        description="Currently active managed-session turn id when available",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured metadata for the event row",
    )

LiveLogChunk = RunObservabilityEvent

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
    execution_style: ExternalExecutionStyle = Field(
        "polling",
        alias="executionStyle",
        description="polling: start/status/fetch loop; streaming_gateway: single execute activity.",
    )

def _apply_canonical_metadata(payload: dict[str, Any], metadata: dict[str, Any]) -> None:
    """Internal helper to map legacy provider fields into metadata."""
    for legacy_key, meta_key in _LEGACY_METADATA_MAP:
        # Only set metadata if the canonical key is not already present.
        # This ensures canonical fields (e.g., externalUrl) are not overwritten
        # by less-canonical fallbacks like "url".
        if legacy_key in payload and meta_key not in metadata:
            metadata[meta_key] = payload[legacy_key]

def build_canonical_start_handle(payload: dict[str, Any]) -> AgentRunHandle:
    """Build a canonical AgentRunHandle, safely mapping provider-specific top-level fields."""
    run_id = payload.get("run_id") or payload.get("runId") or payload.get("external_id")
    if not run_id:
        raise ValueError("External start handle payload is missing one of runId/run_id/external_id")

    raw_status = payload.get("status")
    kind = payload.get("agentKind") or payload.get("agent_kind")
    agent_id = payload.get("agentId") or payload.get("agent_id")
    started_at = payload.get("startedAt") or payload.get("started_at")
    
    if raw_status not in get_args(AgentRunState):
        raise_unsupported_status(str(raw_status), context=str(agent_id))
    
    metadata = dict(payload.get("metadata") or {})
    _apply_canonical_metadata(payload, metadata)

    return AgentRunHandle(
        runId=run_id,
        agentKind=kind,
        agentId=agent_id,
        status=raw_status,
        startedAt=started_at,
        metadata=metadata,
    )

def build_canonical_status(payload: dict[str, Any]) -> AgentRunStatus:
    """Build a canonical AgentRunStatus, safely mapping provider-specific top-level fields."""
    run_id = payload.get("run_id") or payload.get("runId") or payload.get("external_id")
    if not run_id:
        raise ValueError("External status payload is missing one of runId/run_id/external_id")
        
    kind = payload.get("agentKind") or payload.get("agent_kind")
    agent_id = payload.get("agentId") or payload.get("agent_id")
    raw_status = payload.get("status")
    
    if raw_status not in get_args(AgentRunState):
        raise_unsupported_status(str(raw_status), context=str(agent_id))

    metadata = dict(payload.get("metadata") or {})
    _apply_canonical_metadata(payload, metadata)

    return AgentRunStatus(
        runId=run_id,
        agentKind=kind,
        agentId=agent_id,
        status=raw_status,
        metadata=metadata,
    )

def build_canonical_result(payload: dict[str, Any]) -> AgentRunResult:
    """Build a canonical AgentRunResult payload, safely filtering top-level fields."""
    metadata = dict(payload.get("metadata") or {})
    _apply_canonical_metadata(payload, metadata)

    # Extract only known fields to avoid ValidationError from extra provider data
    known_keys = AgentRunResult.model_fields.keys() | {f.alias for f in AgentRunResult.model_fields.values() if f.alias}
    data = {k: v for k, v in payload.items() if k in known_keys}
    data["metadata"] = metadata
    return AgentRunResult(**data)

__all__ = [
    "AgentExecutionRequest",
    "AgentKind",
    "ExternalExecutionStyle",
    "AgentRunHandle",
    "AgentRunResult",
    "RunObservabilityEvent",
    "LiveLogChunk",
    "AgentRunState",
    "AgentRunStatus",
    "FailureClass",
    "ManagedAgentProviderProfile",
    "ManagedRunRecord",
    "ManagedRuntimeProfile",
    "ProfileSelector",
    "ProviderCapabilityDescriptor",
    "TERMINAL_AGENT_RUN_STATES",
    "WorkspaceMode",
    "is_terminal_agent_run_state",
    "validate_codex_oauth_profile_refs",
    "UnsupportedStatusError",
    "raise_unsupported_status",
    "build_canonical_start_handle",
    "build_canonical_status",
    "build_canonical_result",
]
