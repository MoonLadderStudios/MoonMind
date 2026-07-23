"""Canonical, backend-neutral container-job contracts (MoonMind#3252)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas.workspace_locator_models import (
    ExternalStateLocator,
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WorkspaceLocator,
)

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


def workspace_locator_identity(locator: WorkspaceLocator) -> str:
    """Return the stable logical identity segment for a canonical #3147 locator.

    The identity is a logical name, never a host path or mount source; resolving
    it into a filesystem location is a trusted-worker/backend responsibility.
    """

    if isinstance(locator, ExternalStateLocator):
        return locator.artifact_ref
    if isinstance(locator, SandboxWorkspaceLocator):
        return locator.workspace_id
    if isinstance(locator, ManagedWorkspaceLocator):
        return f"{locator.runtime_id}-{locator.agent_run_id}"
    raise ValueError("unsupported workspace locator kind")


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
    # Preparation phases. The finer-grained phases below are emitted by the
    # durable workflow so a status reader can distinguish workspace resolution
    # from image acquisition and container startup (MoonLadderStudios/MoonMind#3258).
    # ``PREPARING`` is retained as a still-valid umbrella value so any record
    # persisted before the finer phases existed remains decodable.
    PREPARING = "preparing"
    RESOLVING_WORKSPACE = "resolving_workspace"
    WORKSPACE_NOT_VISIBLE = "workspace_not_visible"
    ACQUIRING_IMAGE = "acquiring_image"
    BUILDING_IMAGE = "building_image"
    STARTING = "starting"
    RUNNING = "running"
    CANCELING = "canceling"
    PUBLISHING_ARTIFACTS = "publishing_artifacts"
    CLEANING_UP = "cleaning_up"
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
    IMAGE_NOT_FOUND = "image_not_found"
    IMAGE_PULL_TIMEOUT = "image_pull_timeout"
    IMAGE_PULL_AUTH_FAILED = "image_pull_auth_failed"
    IMAGE_BUILD_NOT_CONFIGURED = "image_build_not_configured"
    IMAGE_BUILD_INPUTS_UNAVAILABLE = "image_build_inputs_unavailable"
    IMAGE_BUILD_TIMEOUT = "image_build_timeout"
    IMAGE_BUILD_FAILED = "image_build_failed"
    IMAGE_VALIDATION_FAILED = "image_validation_failed"
    IMAGE_PLATFORM_MISMATCH = "image_platform_mismatch"
    IMAGE_BACKEND_UNAVAILABLE = "image_backend_unavailable"
    LAUNCH = "launch"
    EXECUTION = "execution"
    TIMEOUT = "timeout"
    CANCELED = "canceled"
    INFRASTRUCTURE = "infrastructure"
    # Private-image authorization with ephemeral registry credentials (#3257).
    IMAGE_USE_DENIED = "image_use_denied"
    CREDENTIAL_UNRESOLVED = "credential_unresolved"
    REPOSITORY_SCOPE_MISMATCH = "repository_scope_mismatch"
    REGISTRY_AUTH_FAILED = "registry_auth_failed"
    CREDENTIAL_CLEANUP_FAILED = "credential_cleanup_failed"


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
    image: str | None = Field(None, min_length=1, max_length=512)
    image_source_ref: str | None = Field(
        None,
        alias="imageSourceRef",
        pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,126}[a-z0-9])?$",
        max_length=128,
    )
    workspace_ref: WorkspaceLocator = Field(alias="workspaceRef")
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

    @model_validator(mode="after")
    def exactly_one_image_authority(self) -> "ContainerJobSpec":
        if (self.image is None) == (self.image_source_ref is None):
            raise ValueError("exactly one of image or imageSourceRef is required")
        if self.image_source_ref is not None and self.registry_credential_ref is not None:
            raise ValueError(
                "registryCredentialRef cannot override a deployment-owned image source"
            )
        if self.image_source_ref is not None and self.pull_policy != "if-missing":
            raise ValueError(
                "pullPolicy cannot override a deployment-owned image source"
            )
        return self

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


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOCKER_HUB_ALIASES = frozenset(
    {"docker.io", "index.docker.io", "registry-1.docker.io", "registry.hub.docker.com"}
)


class NormalizedImageReference(ContractModel):
    """Backend-neutral registry/repository/reference decomposition (#3257).

    Normalization is authorization input only; it never carries credentials.
    """

    registry: str = Field(min_length=1, max_length=255)
    repository: str = Field(min_length=1, max_length=255)
    tag: str | None = Field(None, max_length=128)
    digest: str | None = Field(None, pattern=r"^sha256:[0-9a-f]{64}$")
    reference: str = Field(max_length=512)


def normalize_image_reference(image: str) -> NormalizedImageReference:
    """Decompose an image string into registry/repository/tag/digest.

    Mirrors Docker's reference rules closely enough for repository-scope
    authorization: an unqualified first path component defaults to ``docker.io``
    and Docker Hub short names gain the ``library/`` prefix.
    """

    raw = str(image or "").strip()
    if not raw:
        raise ValueError("image reference must not be empty")

    digest: str | None = None
    remainder = raw
    if "@" in remainder:
        remainder, _, digest_part = remainder.partition("@")
        digest = digest_part.strip()
        if not _DIGEST.fullmatch(digest):
            raise ValueError("image digest must be a sha256 reference")

    first, sep, rest = remainder.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        registry = first.lower()
        path = rest
    else:
        registry = "docker.io"
        path = remainder
    if registry in _DOCKER_HUB_ALIASES:
        registry = "docker.io"

    tag: str | None = None
    last_slash = path.rfind("/")
    last_segment = path[last_slash + 1 :]
    if ":" in last_segment:
        name_tail, _, tag_part = last_segment.rpartition(":")
        tag = tag_part.strip() or None
        path = path[: last_slash + 1] + name_tail

    if tag is None and digest is None:
        tag = "latest"

    repository = path.strip("/")
    if not repository:
        raise ValueError("image reference is missing a repository")
    if registry == "docker.io" and "/" not in repository:
        repository = f"library/{repository}"

    reference = f"{registry}/{repository}"
    if digest is not None:
        reference = f"{reference}@{digest}"
    elif tag is not None:
        reference = f"{reference}:{tag}"

    return NormalizedImageReference(
        registry=registry,
        repository=repository,
        tag=tag,
        digest=digest,
        reference=reference,
    )


class RegistryAuthorization(ContractModel):
    """Non-sensitive private-image authorization outcome (#3257).

    Records the bounded decision and authorized scope only; it never carries a
    username, token, password, or Docker auth blob. It is safe to persist and to
    cross Temporal workflow history.
    """

    authorized: bool
    registry: str = Field(min_length=1, max_length=255)
    repository: str = Field(min_length=1, max_length=255)
    reference: str = Field(max_length=512)
    credential_ref: str | None = Field(None, alias="credentialRef", max_length=1024)
    scope: str | None = Field(None, max_length=255)
    digest: str | None = Field(
        None, alias="requiredDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    failure_class: ContainerJobFailureClass | None = Field(
        None, alias="failureClass"
    )
    message: str | None = Field(None, max_length=512)

    _validate_credential_ref = field_validator("credential_ref")(
        _opaque_secret_reference
    )


CONTAINER_JOB_FAILURE_MARKER = "container-job-failure:"


class ContainerJobBackendError(RuntimeError):
    """Trusted-backend error that carries a stable container-job failure class.

    The failure class is encoded into the message with a stable marker so the
    workflow can recover it even after Temporal wraps the activity failure and
    the original Python type is lost.
    """

    def __init__(
        self, failure_class: ContainerJobFailureClass, detail: str
    ) -> None:
        self.failure_class = failure_class
        super().__init__(
            f"{CONTAINER_JOB_FAILURE_MARKER}{failure_class.value}: {detail}"
        )


def failure_class_from_exception(
    exc: BaseException,
) -> ContainerJobFailureClass | None:
    """Recover a container-job failure class from an exception, if present.

    Prefers a direct ``failure_class`` attribute (in-process activity calls) and
    falls back to parsing the stable marker embedded in the message. Temporal
    wraps an activity failure and re-raises it with the original as ``__cause__``
    (or ``__context__``), so the whole chain is walked to find either signal.
    """

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        direct = getattr(current, "failure_class", None)
        if isinstance(direct, ContainerJobFailureClass):
            return direct
        text = str(current)
        marker_index = text.find(CONTAINER_JOB_FAILURE_MARKER)
        if marker_index != -1:
            token = text[marker_index + len(CONTAINER_JOB_FAILURE_MARKER) :]
            token = token.split(":", 1)[0].strip()
            try:
                return ContainerJobFailureClass(token)
            except ValueError:
                pass
        current = current.__cause__ or current.__context__
    return None


class ImageObservation(ContractModel):
    requested_reference: str = Field(alias="requestedReference", max_length=512)
    source_kind: Literal["registry", "local-build"] | None = Field(
        None, alias="sourceKind"
    )
    image_source_ref: str | None = Field(
        None, alias="imageSourceRef", max_length=128
    )
    resolved_digest: str | None = Field(None, alias="resolvedDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    cache_present: bool = Field(False, alias="cachePresent")
    cache_hit: bool = Field(False, alias="cacheHit")
    build_key: str | None = Field(
        None, alias="buildKey", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    fresh_at_start: bool | None = Field(None, alias="freshAtStart")
    provision_action: Literal["reuse", "pull", "build", "none"] = Field(
        "none", alias="provisionAction"
    )
    provision_waited_on_lock: bool = Field(
        False, alias="provisionWaitedOnLock"
    )
    pull_lock_wait_ms: int = Field(0, alias="pullLockWaitMs", ge=0)
    pull_duration_ms: int | None = Field(None, alias="pullDurationMs", ge=0)
    build_duration_ms: int | None = Field(None, alias="buildDurationMs", ge=0)


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
    authorization: RegistryAuthorization | None = None
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


class ArtifactCollectionStatus(StrEnum):
    """Per-output collection outcome for the durable output manifest (#3258)."""

    COLLECTED = "collected"
    MISSING = "missing"
    REJECTED = "rejected"
    TRUNCATED = "truncated"


class ContainerJobArtifact(ContractModel):
    name: str = Field(max_length=255)
    # ``artifact_ref`` is empty for outputs that were declared but not collected
    # (missing/rejected); the ``collection_status`` records why.
    artifact_ref: str | None = Field(None, alias="artifactRef", max_length=1024)
    size_bytes: int = Field(0, alias="sizeBytes", ge=0)
    # ``sha256`` is only present for successfully collected content.
    sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    media_type: str | None = Field(None, alias="mediaType", max_length=255)
    relative_path: str | None = Field(None, alias="relativePath", max_length=1000)
    collection_status: ArtifactCollectionStatus = Field(
        ArtifactCollectionStatus.COLLECTED, alias="collectionStatus"
    )
    detail: str | None = Field(None, max_length=512)


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
    registry_authorization: RegistryAuthorization | None = Field(
        None, alias="registryAuthorization"
    )
    observe_interval_seconds: int = Field(
        10, alias="observeIntervalSeconds", ge=1, le=300
    )

    _valid_job_id = field_validator("job_id")(_validate_job_id)

    @model_validator(mode="before")
    @classmethod
    def normalize_v1_workspace_locator(cls, value: Any) -> Any:
        """Keep already-recorded v1 workflow histories decodable at the boundary."""
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        request = payload.get("request")
        if not isinstance(request, dict):
            return payload
        request = dict(request)
        spec = request.get("spec")
        if not isinstance(spec, dict):
            return payload
        spec = dict(spec)
        locator = spec.get("workspaceRef")
        if not isinstance(locator, dict):
            return payload
        kind = locator.get("kind")
        if kind == "artifact-workspace" and locator.get("artifactRef"):
            spec["workspaceRef"] = {
                "kind": "external_state",
                "artifactRef": locator["artifactRef"],
            }
        elif kind in {"moonmind-session", "omnigent-session"} and locator.get(
            "sessionId"
        ):
            # The v1 backend resolved these opaque identities directly below its
            # authority root. External-state preserves that legacy resolution
            # without admitting the superseded vocabulary to new submissions.
            spec["workspaceRef"] = {
                "kind": "external_state",
                "artifactRef": locator["sessionId"],
            }
        request["spec"] = spec
        payload["request"] = request
        return payload

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
    registry_authorization: RegistryAuthorization | None = Field(
        None, alias="registryAuthorization"
    )
    state: ContainerJobState | None = None
    resolved_workspace_ref: str | None = Field(
        None, alias="resolvedWorkspaceRef", max_length=1024
    )
    resolved_workspace_volume_name: str | None = Field(
        None, alias="resolvedWorkspaceVolumeName", max_length=255
    )
    resolved_workspace_volume_subpath: str | None = Field(
        None, alias="resolvedWorkspaceVolumeSubpath", max_length=1024
    )
    resolved_image_ref: str | None = Field(
        None, alias="resolvedImageRef", max_length=1024
    )
    image_observation: ImageObservation | None = Field(
        None, alias="imageObservation"
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
    events_ref: str | None = Field(None, alias="eventsRef", max_length=1024)
    # Resumable live-log cursor carried across observe polls, and the compact
    # timing/probe observations threaded into the terminal projection (#3258).
    log_cursor: str | None = Field(None, alias="logCursor", max_length=512)
    workspace_probe: str | None = Field(
        None, alias="workspaceProbe", max_length=64
    )
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    duration_ms: int | None = Field(None, alias="durationMs", ge=0)

    _valid_job_id = field_validator("job_id")(_validate_job_id)


class ContainerJobActivityResult(TemporalContractModel):
    """Bounded result returned by trusted container-job Activities."""

    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    resolved_workspace_ref: str | None = Field(
        None, alias="resolvedWorkspaceRef", max_length=1024
    )
    resolved_workspace_volume_name: str | None = Field(
        None, alias="resolvedWorkspaceVolumeName", max_length=255
    )
    resolved_workspace_volume_subpath: str | None = Field(
        None, alias="resolvedWorkspaceVolumeSubpath", max_length=1024
    )
    resolved_image_ref: str | None = Field(
        None, alias="resolvedImageRef", max_length=1024
    )
    image_observation: ImageObservation | None = Field(
        None, alias="imageObservation"
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
    # Durable observability-event journal ref (the terminal fallback for the
    # bounded live log stream) and the resumable live-log paging cursor (#3258).
    events_ref: str | None = Field(None, alias="eventsRef", max_length=1024)
    log_cursor: str | None = Field(None, alias="logCursor", max_length=512)
    # Non-sensitive workspace visibility probe result (never a source path) and
    # container start/end/duration timing observations (#3258).
    workspace_probe: str | None = Field(
        None, alias="workspaceProbe", max_length=64
    )
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    duration_ms: int | None = Field(None, alias="durationMs", ge=0)


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
