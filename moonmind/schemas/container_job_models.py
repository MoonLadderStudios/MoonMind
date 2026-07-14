"""Canonical, backend-neutral container-job contracts (MoonMind#3252)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTAINER_JOB_CONTRACT_VERSION = "v1"
MAX_TEMPORAL_PAYLOAD_BYTES = 64 * 1024
MAX_LOG_PAGE_ENTRIES = 500
MAX_ARTIFACT_PAGE_ENTRIES = 200
_JOB_ID = re.compile(r"^container-job:[0-9a-f]{32}$")
_SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|auth|credential|docker_host|registry_auth)", re.I)
_FORBIDDEN_KEYS = {
    "dockerhost", "dockerurl", "socketpath", "tlspath", "hostpath", "sourcepath",
    "privileged", "devices", "pidmode", "ipcmode", "utsmode", "usernsmode",
    "label", "labels", "ownershiplabel", "registrycredentials",
}


def _validate_job_id(value: str) -> str:
    if not _JOB_ID.fullmatch(value):
        raise ValueError("jobId must be a container-job identifier")
    return value


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", use_enum_values=True)


class TemporalContractModel(ContractModel):
    """Compact public or worker contract permitted to cross workflow history."""

    @model_validator(mode="before")
    @classmethod
    def reject_sensitive_or_authority_fields(cls, value: Any) -> Any:
        _reject_forbidden(value)
        return value

    @model_validator(mode="after")
    def temporal_size(self) -> "TemporalContractModel":
        ensure_temporal_safe(self)
        return self


class ContainerJobState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    ACQUIRING_IMAGE = "acquiring_image"
    RUNNING = "running"
    CANCELING = "canceling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


class ContainerJobFailureClass(StrEnum):
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    WORKSPACE = "workspace"
    IMAGE = "image"
    LAUNCH = "launch"
    EXECUTION = "execution"
    TIMEOUT = "timeout"
    CANCELED = "canceled"
    INFRASTRUCTURE = "infrastructure"


class AuxiliaryOutcomeState(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OwnerIdentity(ContractModel):
    principal_id: str = Field(alias="principalId", min_length=1, max_length=255)
    principal_type: Literal["user", "service", "system"] = Field(alias="principalType")


class SourceCorrelation(ContractModel):
    source: Literal["http", "mcp", "workflow", "managed_session", "omnigent"]
    caller_request_id: str | None = Field(None, alias="callerRequestId", max_length=255)
    workflow_id: str | None = Field(None, alias="workflowId", max_length=255)
    run_id: str | None = Field(None, alias="runId", max_length=255)
    step_id: str | None = Field(None, alias="stepId", max_length=255)
    managed_session_id: str | None = Field(None, alias="managedSessionId", max_length=255)
    agent_run_id: str | None = Field(None, alias="agentRunId", max_length=255)
    omnigent_session_id: str | None = Field(None, alias="omnigentSessionId", max_length=255)
    omnigent_conversation_id: str | None = Field(None, alias="omnigentConversationId", max_length=255)


def _opaque_secret_reference(value: str | None) -> str | None:
    if value is not None and "://" not in value:
        raise ValueError("secret references must use an opaque reference URI")
    return value


class EnvironmentOverride(ContractModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    value: str | None = Field(None, max_length=4096)
    secret_ref: str | None = Field(None, alias="secretRef", max_length=1024)

    _validate_secret_ref = field_validator("secret_ref")(_opaque_secret_reference)

    @model_validator(mode="after")
    def exactly_one_value(self) -> "EnvironmentOverride":
        if (self.value is None) == (self.secret_ref is None):
            raise ValueError("exactly one of value or secretRef is required")
        if _SECRET_KEY.search(self.name) and self.value is not None:
            raise ValueError("sensitive environment keys require secretRef")
        return self


class CacheMount(ContractModel):
    cache_ref: str = Field(alias="cacheRef", min_length=1, max_length=255)
    target: str = Field(pattern=r"^/[A-Za-z0-9._/@+-]+(?:/[A-Za-z0-9._@+-]+)*$", max_length=512)
    read_only: bool = Field(False, alias="readOnly")


class ResourceLimits(ContractModel):
    cpu_millis: int = Field(alias="cpuMillis", ge=1, le=128000)
    memory_mib: int = Field(alias="memoryMiB", ge=16, le=1048576)
    pids: int = Field(256, ge=16, le=32768)


class OutputDeclaration(ContractModel):
    name: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(alias="relativePath", min_length=1, max_length=1000)

    @field_validator("relative_path")
    @classmethod
    def relative_only(cls, value: str) -> str:
        parts = value.replace("\\", "/").split("/")
        if value.startswith("/") or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("relativePath must be normalized and relative")
        return "/".join(parts)


class ContainerJobSpec(ContractModel):
    image: str = Field(min_length=1, max_length=512)
    workspace_ref: dict[str, Any] = Field(alias="workspaceRef")
    command: list[str] = Field(default_factory=list, max_length=128)
    entrypoint: list[str] = Field(default_factory=list, max_length=32)
    workdir: str = Field("/workspace", pattern=r"^/workspace(?:/[^/]+)*$", max_length=512)
    environment: list[EnvironmentOverride] = Field(default_factory=list, max_length=128)
    caches: list[CacheMount] = Field(default_factory=list, max_length=32)
    network_mode: Literal["none", "bridge"] = Field("none", alias="networkMode")
    resources: ResourceLimits
    timeout_seconds: int = Field(1800, alias="timeoutSeconds", ge=1, le=86400)
    pull_policy: Literal["if-missing", "always", "never"] = Field("if-missing", alias="pullPolicy")
    registry_credential_ref: str | None = Field(None, alias="registryCredentialRef", max_length=1024)
    outputs: list[OutputDeclaration] = Field(default_factory=list, max_length=64)

    _validate_registry_credential_ref = field_validator("registry_credential_ref")(_opaque_secret_reference)

    @field_validator("workspace_ref")
    @classmethod
    def valid_workspace_ref(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Validate through the canonical typed workspace-locator contract
        # (MoonMind#3147/#3255) instead of a bespoke Docker-only identity model.
        # The original compact dict is returned unchanged so the shared
        # HTTP/MCP/Temporal serialization stays deterministic.
        from moonmind.schemas.workspace_locator_models import (
            CONTAINER_JOB_WORKSPACE_ADAPTER,
        )

        if not isinstance(value, dict):
            raise ValueError("workspaceRef must be a typed logical locator object")
        CONTAINER_JOB_WORKSPACE_ADAPTER.validate_python(value)
        return value

    @field_validator("workdir")
    @classmethod
    def normalized_workdir(cls, value: str) -> str:
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("workdir must remain within /workspace")
        return value

    @field_validator("command", "entrypoint")
    @classmethod
    def bounded_argv(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 4096 for item in value):
            raise ValueError("argv entries must contain 1..4096 characters")
        return value


class ContainerJobSubmitRequest(TemporalContractModel):
    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=255)
    source: SourceCorrelation
    spec: ContainerJobSpec


class ContainerJobAccepted(TemporalContractModel):
    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    job_id: str = Field(alias="jobId")
    state: Literal["queued"] = "queued"
    replayed: bool = False
    created_at: datetime = Field(alias="createdAt")

    @field_validator("job_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _validate_job_id(value)


class ImageObservation(ContractModel):
    requested_reference: str = Field(alias="requestedReference", max_length=512)
    resolved_digest: str | None = Field(None, alias="resolvedDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    cache_present: bool = Field(False, alias="cachePresent")
    cache_hit: bool = Field(False, alias="cacheHit")
    pull_lock_wait_ms: int = Field(0, alias="pullLockWaitMs", ge=0)
    pull_duration_ms: int | None = Field(None, alias="pullDurationMs", ge=0)


class TerminalOutcome(ContractModel):
    exit_code: int | None = Field(None, alias="exitCode")
    failure_class: ContainerJobFailureClass | None = Field(None, alias="failureClass")
    message: str | None = Field(None, max_length=2048)


class AuxiliaryOutcome(ContractModel):
    state: AuxiliaryOutcomeState
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef", max_length=1024)


class ContainerJobStatus(TemporalContractModel):
    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    job_id: str = Field(alias="jobId")
    state: ContainerJobState
    backend_kind: str | None = Field(None, alias="backendKind", max_length=64)
    backend_ref: str | None = Field(None, alias="backendRef", max_length=255)
    image: ImageObservation | None = None
    terminal: TerminalOutcome | None = None
    publication: AuxiliaryOutcome = Field(default_factory=lambda: AuxiliaryOutcome(state="not_attempted"))
    cleanup: AuxiliaryOutcome = Field(default_factory=lambda: AuxiliaryOutcome(state="not_attempted"))
    logs_ref: str | None = Field(None, alias="logsRef", max_length=1024)
    artifacts_ref: str | None = Field(None, alias="artifactsRef", max_length=1024)
    updated_at: datetime = Field(alias="updatedAt")

    _valid_job_id = field_validator("job_id")(_validate_job_id)


class ContainerJobLogQuery(ContractModel):
    cursor: str | None = Field(None, max_length=512)
    limit: int = Field(100, ge=1, le=MAX_LOG_PAGE_ENTRIES)


class ContainerJobLogEntry(ContractModel):
    sequence: int = Field(ge=0)
    timestamp: datetime
    stream: Literal["stdout", "stderr", "system"]
    text: str = Field(max_length=8192)


class ContainerJobLogPage(ContractModel):
    job_id: str = Field(alias="jobId")
    entries: list[ContainerJobLogEntry] = Field(max_length=MAX_LOG_PAGE_ENTRIES)
    next_cursor: str | None = Field(None, alias="nextCursor", max_length=512)

    _valid_job_id = field_validator("job_id")(_validate_job_id)


class ContainerJobArtifact(ContractModel):
    name: str = Field(max_length=255)
    artifact_ref: str = Field(alias="artifactRef", max_length=1024)
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContainerJobArtifactPage(ContractModel):
    job_id: str = Field(alias="jobId")
    artifacts: list[ContainerJobArtifact] = Field(max_length=MAX_ARTIFACT_PAGE_ENTRIES)
    next_cursor: str | None = Field(None, alias="nextCursor", max_length=512)
    publication: AuxiliaryOutcome

    _valid_job_id = field_validator("job_id")(_validate_job_id)


class ContainerJobCancelRequest(TemporalContractModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=255)
    reason: str | None = Field(None, max_length=512)


class ContainerJobCancelResult(TemporalContractModel):
    job_id: str = Field(alias="jobId")
    state: ContainerJobState
    accepted: bool
    replayed: bool = False

    _valid_job_id = field_validator("job_id")(_validate_job_id)


class ResolvedContainerLaunchPlan(TemporalContractModel):
    """Trusted-worker-only plan; backendRef is selected by deployment code."""
    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    job_id: str = Field(alias="jobId")
    backend_kind: str = Field(alias="backendKind", max_length=64)
    backend_ref: str = Field(alias="backendRef", max_length=255)
    resolved_workspace_ref: str = Field(alias="resolvedWorkspaceRef", max_length=1024)
    spec: ContainerJobSpec

    _valid_job_id = field_validator("job_id")(_validate_job_id)


class ContainerJobWorkflowInput(TemporalContractModel):
    """Compact input for the versioned ``MoonMind.ContainerJob`` workflow."""

    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    job_id: str = Field(alias="jobId")
    owner: OwnerIdentity = Field(
        default_factory=lambda: OwnerIdentity(
            principalId="container_job", principalType="system"
        )
    )
    request: ContainerJobSubmitRequest
    observe_interval_seconds: int = Field(
        10, alias="observeIntervalSeconds", ge=1, le=300
    )

    _valid_job_id = field_validator("job_id")(_validate_job_id)

    @property
    def ownership_token(self) -> str:
        return f"{self.job_id}:{self.contract_version}"


class ContainerJobActivityRequest(TemporalContractModel):
    """Typed, retry-safe request crossing a container-job Activity boundary."""

    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    job_id: str = Field(alias="jobId")
    owner: OwnerIdentity = Field(
        default_factory=lambda: OwnerIdentity(
            principalId="container_job", principalType="system"
        )
    )
    ownership_token: str = Field(alias="ownershipToken", min_length=1, max_length=300)
    request: ContainerJobSubmitRequest
    state: ContainerJobState | None = None
    resolved_workspace_ref: str | None = Field(
        None, alias="resolvedWorkspaceRef", max_length=1024
    )
    resolved_image_ref: str | None = Field(
        None, alias="resolvedImageRef", max_length=1024
    )
    container_ref: str | None = Field(None, alias="containerRef", max_length=1024)
    terminal_state: ContainerJobState | None = Field(None, alias="terminalState")
    projection_sequence: int = Field(0, alias="projectionSequence", ge=0)
    publication_token: str | None = Field(
        None, alias="publicationToken", max_length=300
    )
    exit_code: int | None = Field(None, alias="exitCode")
    failure_class: ContainerJobFailureClass | None = Field(None, alias="failureClass")
    message: str | None = Field(None, max_length=2048)
    publication: AuxiliaryOutcome | None = None
    cleanup_outcome: AuxiliaryOutcome | None = Field(None, alias="cleanup")
    logs_ref: str | None = Field(None, alias="logsRef", max_length=1024)
    artifacts_ref: str | None = Field(None, alias="artifactsRef", max_length=1024)

    _valid_job_id = field_validator("job_id")(_validate_job_id)


class ContainerJobActivityResult(TemporalContractModel):
    """Bounded result returned by trusted container-job Activities."""

    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    resolved_workspace_ref: str | None = Field(
        None, alias="resolvedWorkspaceRef", max_length=1024
    )
    resolved_image_ref: str | None = Field(
        None, alias="resolvedImageRef", max_length=1024
    )
    container_ref: str | None = Field(None, alias="containerRef", max_length=1024)
    running: bool | None = None
    terminal_state: ContainerJobState | None = Field(None, alias="terminalState")
    exit_code: int | None = Field(None, alias="exitCode")
    logs_ref: str | None = Field(None, alias="logsRef", max_length=1024)
    artifacts_ref: str | None = Field(None, alias="artifactsRef", max_length=1024)
    diagnostics_ref: str | None = Field(
        None, alias="diagnosticsRef", max_length=1024
    )


class ContainerJobWorkflowResult(TemporalContractModel):
    """Authoritative terminal snapshot; auxiliary failures cannot replace outcome."""

    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    job_id: str = Field(alias="jobId")
    state: ContainerJobState
    terminal: TerminalOutcome
    publication: AuxiliaryOutcome
    cleanup: AuxiliaryOutcome
    logs_ref: str | None = Field(None, alias="logsRef", max_length=1024)
    artifacts_ref: str | None = Field(None, alias="artifactsRef", max_length=1024)
    projection_sequence: int = Field(alias="projectionSequence", ge=0)
    projection_repair_required: bool = Field(alias="projectionRepairRequired")

    _valid_job_id = field_validator("job_id")(_validate_job_id)


def container_job_workflow_id(job_id: str) -> str:
    """Return stable start-or-attach identity for a durable container job."""

    return f"container-job-workflow:{_validate_job_id(job_id)}"


def _reject_forbidden(value: Any, path: str = "request") -> None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = re.sub(r"[^a-z0-9]", "", str(raw_key).lower())
            if key in _FORBIDDEN_KEYS or _SECRET_KEY.fullmatch(key):
                raise ValueError(f"{path}.{raw_key} is forbidden")
            _reject_forbidden(nested, f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_forbidden(nested, f"{path}[{index}]")


def ensure_temporal_safe(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    _reject_forbidden(payload)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_TEMPORAL_PAYLOAD_BYTES:
        raise ValueError(f"payload must serialize to <= {MAX_TEMPORAL_PAYLOAD_BYTES} bytes")
    return encoded
