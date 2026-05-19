"""Canonical contracts for managed and external agent runtime execution."""

from __future__ import annotations

import posixpath
import re
from datetime import UTC, datetime
from typing import Any, Literal, Mapping, NoReturn, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas._validation import require_non_blank
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionBinding,
    canonical_codex_managed_runtime_id,
)
from moonmind.schemas.temporal_payload_policy import validate_compact_temporal_mapping
from moonmind.schemas.workload_models import parse_cpu_units, parse_size_bytes

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
RuntimeCommandRenderStatus = Literal["ok", "unsupported", "failed", "fallback"]
RuntimeCommandRenderMode = Literal[
    "plain_prompt",
    "prompt_prefix",
    "native_command",
    "materialized_command",
    "unsupported",
]

class RuntimeCommandInvocation(BaseModel):
    """Compact backend-normalized runtime command metadata for launch rendering."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    source_path: str = Field(..., alias="sourcePath", min_length=1)
    command: str = ""
    raw_command: str = Field("", alias="rawCommand")
    args: str = ""
    instruction_body: str = Field("", alias="instructionBody")
    target_runtime: str | None = Field(None, alias="targetRuntime")
    target_step_id: str | None = Field(None, alias="targetStepId")
    detection_status: str = Field(..., alias="detectionStatus", min_length=1)
    hint_status: str = Field(..., alias="hintStatus", min_length=1)
    recognition_mode: str = Field(..., alias="recognitionMode", min_length=1)
    render_mode: RuntimeCommandRenderMode | None = Field(None, alias="renderMode")
    materialized_command: dict[str, Any] | None = Field(
        None, alias="materializedCommand"
    )
    requires_runtime_recognition: bool = Field(
        False, alias="requiresRuntimeRecognition"
    )
    runtime_capability_version: str | None = Field(
        None, alias="runtimeCapabilityVersion"
    )
    hint_catalog_version: str | None = Field(None, alias="hintCatalogVersion")
    detection_phase: str | None = Field(None, alias="detectionPhase")

    @model_validator(mode="after")
    def _recognized_runtime_command_has_text(self) -> "RuntimeCommandInvocation":
        if (
            self.requires_runtime_recognition
            and not self.raw_command.strip()
            and not self.command.strip()
        ):
            raise ValueError(
                "runtime command recognition requires non-empty rawCommand or command"
            )
        return self


class RuntimeCommandRenderResult(BaseModel):
    """Result of adapter-owned runtime command rendering before launch."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: RuntimeCommandRenderStatus
    render_mode: RuntimeCommandRenderMode | None = Field(None, alias="renderMode")
    rendered_instruction: str | None = Field(None, alias="renderedInstruction")
    native_command_payload: dict[str, Any] | None = Field(
        None, alias="nativeCommandPayload"
    )
    materialized_targets: list[dict[str, Any]] = Field(
        default_factory=list, alias="materializedTargets"
    )
    failure_reason: str | None = Field(None, alias="failureReason")
    fallback_event: dict[str, Any] | None = Field(None, alias="fallbackEvent")
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    invocation: RuntimeCommandInvocation | None = None

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
    "retrievalMode",
    "retrievalDegradedReason",
    "retrievalDisabledReason",
    "retrievalInitiationMode",
    "retrievalContextTruncated",
    "sessionContinuityCacheStatus",
)
_BOOLEAN_DURABLE_RETRIEVAL_METADATA_KEYS: frozenset[str] = frozenset({
    "retrievalContextTruncated",
})


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
        if isinstance(value, bool):
            if key in _BOOLEAN_DURABLE_RETRIEVAL_METADATA_KEYS:
                compact[key] = value
            continue
        if isinstance(value, int):
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
    runtime_command: RuntimeCommandInvocation | None = Field(
        None, alias="runtimeCommand"
    )
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
ManagedRuntimeWorkloadMode = Literal[
    "docker-sidecar",
    "docker-sidecar-rootless",
    "no-docker",
    "kubernetes-job",
]
MoonMindOpsRuntimeOperation = Literal[
    "status",
    "deploy",
    "restart",
    "rollback",
    "imageRefresh",
    "logs",
]


class RuntimeProfileMount(BaseModel):
    """Declarative mount entry for managed agent runtime profiles."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = None
    mount_path: str = Field(..., alias="mountPath", min_length=1)
    source: str | None = None
    host_path: str | None = Field(None, alias="hostPath")


class RuntimeProfileOptionalCacheMount(BaseModel):
    """Deployment-approved named cache shared with the agent sidecar pair."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(..., min_length=1)
    volume_name: str = Field(..., alias="volumeName", min_length=1)
    mount_path: str = Field(..., alias="mountPath", min_length=1)
    approval_ref: str = Field(..., alias="approvalRef", min_length=1)
    read_only: bool = Field(False, alias="readOnly")

    @model_validator(mode="after")
    def _validate_cache(self) -> "RuntimeProfileOptionalCacheMount":
        self.name = require_non_blank(self.name, field_name="optionalCaches[].name")
        self.volume_name = require_non_blank(
            self.volume_name,
            field_name="optionalCaches[].volumeName",
        )
        if "/" in self.volume_name or "\\" in self.volume_name:
            raise ValueError("optionalCaches[].volumeName must be a named volume")
        self.mount_path = require_non_blank(
            self.mount_path,
            field_name="optionalCaches[].mountPath",
        )
        self.approval_ref = require_non_blank(
            self.approval_ref,
            field_name="optionalCaches[].approvalRef",
        )
        if self.mount_path.startswith("/") is False:
            raise ValueError("optionalCaches[].mountPath must be absolute")
        return self


class RuntimeProfileWorkspace(BaseModel):
    """Workspace declaration shared by agent and sidecar containers."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    volume: str | None = None
    mount_path: str = Field(..., alias="mountPath", min_length=1)
    repo_env: str | None = Field(None, alias="repoEnv")
    lifecycle: Literal["session"] = "session"


class RuntimeProfileAgentDockerClient(BaseModel):
    """Agent-side Docker CLI settings."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool = False
    compose_plugin: bool = Field(False, alias="composePlugin")
    daemon_in_agent: bool = Field(False, alias="daemonInAgent")


class RuntimeProfileAgent(BaseModel):
    """Agent container portion of a managed runtime profile."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    image: str | None = None
    workspace: RuntimeProfileWorkspace
    docker_client: RuntimeProfileAgentDockerClient = Field(
        default_factory=RuntimeProfileAgentDockerClient,
        alias="dockerClient",
    )
    env: dict[str, str] = Field(default_factory=dict)
    mounts: list[RuntimeProfileMount] = Field(default_factory=list)


class RuntimeProfileSidecarSocket(BaseModel):
    """Docker sidecar Unix socket declaration."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    path: str = Field(..., min_length=1)
    volume_name: str = Field(..., alias="volumeName", min_length=1)


class RuntimeProfileSidecarStorage(BaseModel):
    """Docker sidecar graph storage declaration."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    volume_name: str = Field(..., alias="volumeName", min_length=1)
    mount_path: str = Field(..., alias="mountPath", min_length=1)
    lifecycle: Literal["session"] = "session"
    daemon_scope: Literal["session", "shared"] = Field("session", alias="daemonScope")


class RuntimeProfileSidecarSecurity(BaseModel):
    """Docker sidecar security policy declaration."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    privileged: bool = False
    host_docker_socket: Literal["forbidden"] = Field(
        "forbidden", alias="hostDockerSocket"
    )
    moonmind_deployment_secrets: Literal["forbidden"] = Field(
        "forbidden", alias="moonmindDeploymentSecrets"
    )


class RuntimeProfileDockerSidecar(BaseModel):
    """Docker sidecar portion of a managed runtime profile."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool = False
    mode: Literal["dind", "dind-rootless"] = "dind"
    image: str | None = None
    socket: RuntimeProfileSidecarSocket | None = None
    storage: RuntimeProfileSidecarStorage | None = None
    workspace: RuntimeProfileWorkspace | None = None
    security: RuntimeProfileSidecarSecurity = Field(
        default_factory=RuntimeProfileSidecarSecurity
    )
    env: dict[str, str] = Field(default_factory=dict)
    mounts: list[RuntimeProfileMount] = Field(default_factory=list)
    optional_caches: list[RuntimeProfileOptionalCacheMount] = Field(
        default_factory=list,
        alias="optionalCaches",
    )


class RuntimeProfileSessionResources(BaseModel):
    """Session-wide limits applied by the outer runtime supervisor."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    max_runtime_seconds: int = Field(..., alias="maxRuntimeSeconds", ge=1)


class RuntimeProfileContainerResources(BaseModel):
    """CPU and memory limits for one managed runtime container."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    cpu: str = Field(..., min_length=1)
    memory: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_container_resources(self) -> "RuntimeProfileContainerResources":
        self.cpu = require_non_blank(self.cpu, field_name="resources.cpu")
        parse_cpu_units(self.cpu)
        self.memory = require_non_blank(self.memory, field_name="resources.memory")
        parse_size_bytes(self.memory)
        return self


class RuntimeProfileDockerSidecarResources(RuntimeProfileContainerResources):
    """Limits for the outer Docker sidecar container."""

    ephemeral_storage: str = Field(..., alias="ephemeralStorage", min_length=1)

    @model_validator(mode="after")
    def _validate_sidecar_resources(self) -> "RuntimeProfileDockerSidecarResources":
        super()._validate_container_resources()
        self.ephemeral_storage = require_non_blank(
            self.ephemeral_storage,
            field_name="resources.dockerSidecar.ephemeralStorage",
        )
        parse_size_bytes(self.ephemeral_storage)
        return self


class RuntimeProfileNestedContainerResources(BaseModel):
    """Defaults and caps for containers created through the nested daemon."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    default_cpu: str = Field(..., alias="defaultCpu", min_length=1)
    default_memory: str = Field(..., alias="defaultMemory", min_length=1)
    max_containers: int = Field(..., alias="maxContainers", ge=1)

    @model_validator(mode="after")
    def _validate_nested_resources(self) -> "RuntimeProfileNestedContainerResources":
        self.default_cpu = require_non_blank(
            self.default_cpu,
            field_name="resources.nestedContainers.defaultCpu",
        )
        parse_cpu_units(self.default_cpu)
        self.default_memory = require_non_blank(
            self.default_memory,
            field_name="resources.nestedContainers.defaultMemory",
        )
        parse_size_bytes(self.default_memory)
        return self


class RuntimeProfileResources(BaseModel):
    """Resource envelope for a managed session and its Docker sidecar."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session: RuntimeProfileSessionResources | None = None
    agent: RuntimeProfileContainerResources | None = None
    docker_sidecar: RuntimeProfileDockerSidecarResources | None = Field(
        None,
        alias="dockerSidecar",
    )
    nested_containers: RuntimeProfileNestedContainerResources | None = Field(
        None,
        alias="nestedContainers",
    )


WorkspaceRetentionPolicy = Literal["retention_policy", "always", "never"]


class RuntimeProfileCleanupOnSessionEnd(BaseModel):
    """Cleanup actions that run when a managed session ends."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    stop_sidecar: bool = Field(True, alias="stopSidecar")
    stop_nested_containers: bool = Field(True, alias="stopNestedContainers")
    remove_docker_graph: bool = Field(True, alias="removeDockerGraph")
    remove_docker_socket: bool = Field(True, alias="removeDockerSocket")
    preserve_workspace: WorkspaceRetentionPolicy = Field(
        "retention_policy",
        alias="preserveWorkspace",
    )


class RuntimeProfileCleanupOnSidecarFailure(BaseModel):
    """Failure behavior when the sidecar daemon/container fails."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mark_docker_capability_unavailable: bool = Field(
        True,
        alias="markDockerCapabilityUnavailable",
    )
    preserve_agent_session: bool = Field(True, alias="preserveAgentSession")


class RuntimeProfileCleanupOnAgentFailure(BaseModel):
    """Failure behavior when the agent exits before normal completion."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    stop_sidecar: bool = Field(True, alias="stopSidecar")
    preserve_workspace: WorkspaceRetentionPolicy = Field(
        "retention_policy",
        alias="preserveWorkspace",
    )


class RuntimeProfileCleanupPolicy(BaseModel):
    """Idempotent cleanup policy for the per-session Docker sidecar."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    idempotent: bool = True
    on_session_end: RuntimeProfileCleanupOnSessionEnd = Field(
        default_factory=RuntimeProfileCleanupOnSessionEnd,
        alias="onSessionEnd",
    )
    on_sidecar_failure: RuntimeProfileCleanupOnSidecarFailure = Field(
        default_factory=RuntimeProfileCleanupOnSidecarFailure,
        alias="onSidecarFailure",
    )
    on_agent_failure: RuntimeProfileCleanupOnAgentFailure = Field(
        default_factory=RuntimeProfileCleanupOnAgentFailure,
        alias="onAgentFailure",
    )


class RuntimeProfileDockerSidecarLaunchPlan(BaseModel):
    """Compact launch contract applied outside the nested Docker daemon."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    issue_key: str = Field("MM-695", alias="issueKey")
    apply_limits_outside_nested_daemon: bool = Field(
        True,
        alias="applyLimitsOutsideNestedDaemon",
    )
    workload_mode: ManagedRuntimeWorkloadMode = Field(..., alias="workloadMode")
    labels: dict[str, str] = Field(default_factory=dict)
    socket_volume_name: str = Field(..., alias="socketVolumeName", min_length=1)
    socket_path: str = Field(..., alias="socketPath", min_length=1)
    graph_volume_name: str = Field(..., alias="graphVolumeName", min_length=1)
    graph_mount_path: str = Field(..., alias="graphMountPath", min_length=1)
    resources: RuntimeProfileResources
    cleanup: RuntimeProfileCleanupPolicy
    optional_caches: tuple[RuntimeProfileOptionalCacheMount, ...] = Field(
        default_factory=tuple,
        alias="optionalCaches",
    )


class RuntimeProfilePolicy(BaseModel):
    """Policy declaration for managed agent runtime profile validation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    host_docker_socket: Literal["forbidden"] = Field(
        "forbidden", alias="hostDockerSocket"
    )
    shared_daemon_across_users: Literal["forbidden"] = Field(
        "forbidden", alias="sharedDaemonAcrossUsers"
    )
    moonmind_deployment_secrets_in_session: Literal["forbidden"] = Field(
        "forbidden", alias="moonmindDeploymentSecretsInSession"
    )
    app_container_control_from_session: Literal["forbidden"] = Field(
        "forbidden", alias="appContainerControlFromSession"
    )
    api_container_workload_docker_socket_access: bool = Field(
        False, alias="apiContainerWorkloadDockerSocketAccess"
    )
    kubernetes_job_runtime_supported: bool = Field(
        False, alias="kubernetesJobRuntimeSupported"
    )


class MoonMindOpsDockerBackend(BaseModel):
    """Docker backend declaration for MoonMind control-plane operations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    host_docker_access: Literal[True] = Field(True, alias="hostDockerAccess")
    component: Literal["moonmind-ops-runner"] = "moonmind-ops-runner"


class MoonMindOpsRuntime(BaseModel):
    """Dedicated control-plane Docker runtime hidden from managed sessions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["MoonMindOpsRuntime"] = "MoonMindOpsRuntime"
    name: str = Field("docker-admin-runtime", min_length=1)
    purpose: Literal["moonmind-application-operations"] = (
        "moonmind-application-operations"
    )
    backend: Literal["docker"] = "docker"
    exposed_to_managed_agents: Literal[False] = Field(
        False, alias="exposedToManagedAgents"
    )
    allowed_operations: tuple[MoonMindOpsRuntimeOperation, ...] = Field(
        ("status", "deploy", "restart", "rollback", "imageRefresh", "logs"),
        alias="allowedOperations",
    )
    docker_backend: MoonMindOpsDockerBackend = Field(
        default_factory=MoonMindOpsDockerBackend,
        alias="dockerBackend",
    )

    @model_validator(mode="after")
    def _validate_ops_runtime(self) -> "MoonMindOpsRuntime":
        if not self.allowed_operations:
            raise ValueError("allowedOperations must include at least one operation")
        if len(self.allowed_operations) != len(set(self.allowed_operations)):
            raise ValueError("allowedOperations must not contain duplicate operations")
        return self


def moonmind_ops_runtime_contract() -> MoonMindOpsRuntime:
    """Return the closed MM-694 ops runtime contract for Docker admin access."""

    return MoonMindOpsRuntime()


def _image_is_pinned(image: str | None) -> bool:
    text = str(image or "").strip()
    if not text:
        return False
    if "@sha256:" in text:
        return True
    last_segment = text.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return False
    tag = last_segment.rsplit(":", 1)[-1].strip().lower()
    return bool(tag) and tag != "latest"


def _normalize_posix_path(value: str) -> str:
    collapsed = re.sub(r"/+", "/", value)
    return posixpath.normpath(collapsed)


def _mounts_host_docker_socket(mounts: list[RuntimeProfileMount]) -> bool:
    target = "/var/run/docker.sock"
    for mount in mounts:
        for candidate in (mount.mount_path, mount.source, mount.host_path):
            text = str(candidate or "").strip()
            if not text:
                continue
            try:
                if _normalize_posix_path(text) == target:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _mounts_arbitrary_host_path(mounts: list[RuntimeProfileMount]) -> bool:
    for mount in mounts:
        if str(mount.host_path or "").strip():
            return True
        source = str(mount.source or "").strip()
        if not source:
            continue
        if source in {".", ".."} or source.startswith(("./", "../")):
            return True
        try:
            normalized = _normalize_posix_path(source)
            if normalized.startswith("/") or normalized in {".", ".."}:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _normalize_profile_labels(labels: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in labels.items():
        key = require_non_blank(str(raw_key), field_name="labels key")
        value = require_non_blank(str(raw_value), field_name=f"labels[{key}]")
        normalized[key] = value
    return normalized


class ManagedAgentRuntimeProfile(BaseModel):
    """Validated managed-session runtime profile for Docker sidecar capability."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workload_mode: ManagedRuntimeWorkloadMode = Field(..., alias="workloadMode")
    workspace: RuntimeProfileWorkspace
    agent: RuntimeProfileAgent
    docker_sidecar: RuntimeProfileDockerSidecar | None = Field(
        None, alias="dockerSidecar"
    )
    resources: RuntimeProfileResources = Field(
        default_factory=RuntimeProfileResources
    )
    cleanup: RuntimeProfileCleanupPolicy = Field(
        default_factory=RuntimeProfileCleanupPolicy
    )
    readiness: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    policy: RuntimeProfilePolicy = Field(default_factory=RuntimeProfilePolicy)

    @model_validator(mode="after")
    def _validate_runtime_profile(self) -> "ManagedAgentRuntimeProfile":
        self.labels = _normalize_profile_labels(self.labels)
        if _contains_sensitive_key(self.agent.env):
            raise ValueError(
                "agent.env must not receive deployment credentials or unrelated "
                "session tokens; credential exposure would leak MoonMind or "
                "cross-session authority into the workload"
            )
        sidecar = self.docker_sidecar
        if sidecar is not None and _contains_sensitive_key(sidecar.env):
            raise ValueError(
                "dockerSidecar.env must not receive deployment credentials or "
                "unrelated session tokens; credential exposure would leak MoonMind "
                "or cross-session authority into the workload"
            )
        if _mounts_host_docker_socket(self.agent.mounts) or (
            sidecar is not None and _mounts_host_docker_socket(sidecar.mounts)
        ):
            raise ValueError(
                "host Docker socket must not be mounted; exposing "
                "/var/run/docker.sock would grant host-level Docker control"
            )
        if _mounts_arbitrary_host_path(self.agent.mounts) or (
            sidecar is not None and _mounts_arbitrary_host_path(sidecar.mounts)
        ):
            raise ValueError(
                "runtime profile mounts must use declared session volumes or "
                "deployment-approved optionalCaches; arbitrary host path mounts "
                "are not allowed"
            )
        if self.policy.api_container_workload_docker_socket_access:
            raise ValueError(
                "API container must not have normal workload Docker socket access; "
                "workload Docker control belongs to the isolated session sidecar"
            )

        if self.workload_mode == "kubernetes-job":
            self._validate_kubernetes_job_profile(sidecar)
            return self

        if self.workload_mode == "no-docker":
            if self.agent.docker_client.enabled:
                raise ValueError(
                    "agent.dockerClient.enabled must be false for no-docker profiles; "
                    "task instructions cannot raise Docker capability"
                )
            if sidecar is not None and sidecar.enabled:
                raise ValueError(
                    "dockerSidecar.enabled must be false for no-docker profiles; "
                    "task instructions cannot raise Docker capability"
                )
            if "DOCKER_HOST" in self.agent.env:
                raise ValueError(
                    "agent.env.DOCKER_HOST must not be set for no-docker profiles; "
                    "task instructions cannot raise Docker capability"
                )
            return self

        if sidecar is None or not sidecar.enabled:
            raise ValueError(
                "dockerSidecar.enabled must be true for Docker-capable profiles; "
                "otherwise agent Docker commands would have no per-session daemon"
            )
        if not self.agent.docker_client.enabled:
            raise ValueError(
                "agent.dockerClient.enabled must be true when dockerSidecar.enabled "
                "is true; otherwise Docker commands cannot reach the sidecar daemon"
            )
        if self.agent.docker_client.daemon_in_agent:
            raise ValueError(
                "agent.dockerClient.daemonInAgent must be false; running the daemon "
                "inside the agent breaks sidecar isolation"
            )
        if sidecar.socket is None:
            raise ValueError(
                "dockerSidecar.socket.path is required; DOCKER_HOST cannot be "
                "validated without the declared sidecar socket"
            )
        expected_docker_host = f"unix://{sidecar.socket.path}"
        actual_docker_host = str(self.agent.env.get("DOCKER_HOST") or "").strip()
        if actual_docker_host != expected_docker_host:
            raise ValueError(
                "agent.env.DOCKER_HOST must point at dockerSidecar.socket.path; "
                "otherwise Docker commands may reach the wrong daemon or fail"
            )
        sidecar_workspace_path = (
            sidecar.workspace.mount_path
            if sidecar.workspace
            else self.workspace.mount_path
        )
        if self.agent.workspace.mount_path != sidecar_workspace_path:
            raise ValueError(
                "agent and dockerSidecar workspace mount paths must match; "
                "otherwise docker run bind mounts resolve against a different "
                "filesystem path"
            )
        if not _image_is_pinned(sidecar.image):
            raise ValueError(
                "dockerSidecar.image sidecar image must be pinned to a non-latest "
                "tag or digest; floating images make launches non-reproducible"
            )
        if sidecar.storage is None:
            raise ValueError(
                "dockerSidecar.storage is required; the Docker graph volume must be "
                "declared per session"
            )
        if sidecar.storage.daemon_scope != "session":
            raise ValueError(
                "Docker daemon scope must be per session; shared daemons can expose "
                "containers, images, and credentials across sessions or users"
            )
        self._validate_sidecar_resources()
        self._validate_sidecar_cleanup()
        return self

    def _validate_kubernetes_job_profile(
        self, sidecar: RuntimeProfileDockerSidecar | None
    ) -> None:
        if not self.policy.kubernetes_job_runtime_supported:
            raise ValueError(
                "workloadMode kubernetes-job requires explicit deployment support "
                "via policy.kubernetesJobRuntimeSupported"
            )
        if self.agent.docker_client.enabled:
            raise ValueError(
                "agent.dockerClient.enabled must be false for kubernetes-job profiles; "
                "Kubernetes Job workloads do not expose a Docker daemon to the agent"
            )
        if "DOCKER_HOST" in self.agent.env:
            raise ValueError(
                "agent.env.DOCKER_HOST must not be set for kubernetes-job profiles; "
                "Kubernetes Job workloads are requested through MoonMind capability"
            )
        if sidecar is not None and sidecar.enabled:
            raise ValueError(
                "dockerSidecar.enabled must be false for kubernetes-job profiles; "
                "Kubernetes Job workloads are not Docker sidecar materializations"
            )
        if self.resources.docker_sidecar is not None:
            raise ValueError(
                "resources.dockerSidecar must be omitted for kubernetes-job profiles; "
                "backend-specific rendering owns Kubernetes Job resource mapping"
            )
        if self.resources.nested_containers is not None:
            raise ValueError(
                "resources.nestedContainers must be omitted for kubernetes-job "
                "profiles; Kubernetes Job mode does not use nested Docker containers"
            )

    def _validate_sidecar_resources(self) -> None:
        required_resources = (
            ("session", "resources.session.maxRuntimeSeconds"),
            ("agent", "resources.agent"),
            ("docker_sidecar", "resources.dockerSidecar"),
            ("nested_containers", "resources.nestedContainers"),
        )
        missing = [
            label
            for field_name, label in required_resources
            if getattr(self.resources, field_name) is None
        ]
        if missing:
            raise ValueError(
                "Docker sidecar profiles must declare outer resource limits: "
                + ", ".join(missing)
            )
        assert self.resources.docker_sidecar is not None
        assert self.resources.nested_containers is not None
        nested_cpu = parse_cpu_units(self.resources.nested_containers.default_cpu)
        sidecar_cpu = parse_cpu_units(self.resources.docker_sidecar.cpu)
        if nested_cpu > sidecar_cpu:
            raise ValueError(
                "resources.nestedContainers.defaultCpu must not exceed "
                "resources.dockerSidecar.cpu"
            )
        if parse_size_bytes(
            self.resources.nested_containers.default_memory
        ) > parse_size_bytes(self.resources.docker_sidecar.memory):
            raise ValueError(
                "resources.nestedContainers.defaultMemory must not exceed "
                "resources.dockerSidecar.memory"
            )

    def _validate_sidecar_cleanup(self) -> None:
        cleanup_checks = (
            (
                self.cleanup.idempotent,
                "cleanup.idempotent must be true for Docker sidecar profiles",
            ),
            (
                self.cleanup.on_session_end.stop_sidecar,
                "cleanup.onSessionEnd.stopSidecar must be true",
            ),
            (
                self.cleanup.on_session_end.stop_nested_containers,
                "cleanup.onSessionEnd.stopNestedContainers must be true",
            ),
            (
                self.cleanup.on_session_end.remove_docker_graph,
                "cleanup.onSessionEnd.removeDockerGraph must be true",
            ),
            (
                self.cleanup.on_session_end.remove_docker_socket,
                "cleanup.onSessionEnd.removeDockerSocket must be true",
            ),
            (
                self.cleanup.on_sidecar_failure.mark_docker_capability_unavailable,
                "cleanup.onSidecarFailure.markDockerCapabilityUnavailable must be true",
            ),
            (
                self.cleanup.on_sidecar_failure.preserve_agent_session,
                "cleanup.onSidecarFailure.preserveAgentSession must be true",
            ),
            (
                self.cleanup.on_agent_failure.stop_sidecar,
                "cleanup.onAgentFailure.stopSidecar must be true",
            ),
        )
        for valid, message in cleanup_checks:
            if not valid:
                raise ValueError(message)


def resolve_managed_runtime_workload_mode(
    profile: ManagedAgentRuntimeProfile,
    *,
    task_requested_workload_mode: str | None = None,
) -> ManagedRuntimeWorkloadMode:
    """Return profile workload mode and reject task-level capability elevation."""

    requested = str(task_requested_workload_mode or "").strip()
    if requested and requested != profile.workload_mode:
        raise ValueError(
            "task instructions cannot raise Docker capability; workloadMode is "
            "read from deployment/profile configuration"
        )
    return profile.workload_mode


def build_docker_sidecar_launch_plan(
    profile: ManagedAgentRuntimeProfile,
) -> RuntimeProfileDockerSidecarLaunchPlan | None:
    """Return the compact MM-695 launch contract for Docker sidecar profiles."""

    if profile.workload_mode in {"no-docker", "kubernetes-job"}:
        return None
    sidecar = profile.docker_sidecar
    if sidecar is None or sidecar.socket is None or sidecar.storage is None:
        raise ValueError("validated Docker sidecar profile is missing sidecar shape")
    return RuntimeProfileDockerSidecarLaunchPlan(
        workloadMode=profile.workload_mode,
        labels=profile.labels,
        socketVolumeName=sidecar.socket.volume_name,
        socketPath=sidecar.socket.path,
        graphVolumeName=sidecar.storage.volume_name,
        graphMountPath=sidecar.storage.mount_path,
        resources=profile.resources,
        cleanup=profile.cleanup,
        optionalCaches=tuple(sidecar.optional_caches),
    )

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
    supports_skills_on_demand_activation: bool = Field(
        False,
        alias="supportsSkillsOnDemandActivation",
        description=(
            "Whether the provider can receive live Skills On Demand activation "
            "refreshes during a run."
        ),
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
    "RuntimeCommandInvocation",
    "RuntimeCommandRenderMode",
    "RuntimeCommandRenderResult",
    "RuntimeCommandRenderStatus",
    "ManagedAgentProviderProfile",
    "ManagedAgentRuntimeProfile",
    "ManagedRunRecord",
    "ManagedRuntimeProfile",
    "ManagedRuntimeWorkloadMode",
    "MoonMindOpsRuntime",
    "MoonMindOpsRuntimeOperation",
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
    "moonmind_ops_runtime_contract",
    "resolve_managed_runtime_workload_mode",
]
