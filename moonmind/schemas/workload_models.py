"""Canonical contracts for Docker-backed workload execution."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.schemas._validation import NonBlankStr, require_non_blank


_ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_CONTAINER_NAME_SAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")
_SIZE_PATTERN = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[kmgtp]?i?b?)?$", re.I)
_SIZE_MULTIPLIERS: dict[str, int] = {
    "": 1,
    "b": 1,
    "k": 1000,
    "kb": 1000,
    "m": 1000**2,
    "mb": 1000**2,
    "g": 1000**3,
    "gb": 1000**3,
    "t": 1000**4,
    "tb": 1000**4,
    "ki": 1024,
    "kib": 1024,
    "mi": 1024**2,
    "mib": 1024**2,
    "gi": 1024**3,
    "gib": 1024**3,
    "ti": 1024**4,
    "tib": 1024**4,
}


WorkloadStatus = Literal["succeeded", "failed", "timed_out", "canceled"]
WorkloadKind = Literal["one_shot"]
WorkloadNetworkPolicy = Literal["none", "bridge"]
WorkloadDeviceMode = Literal["none"]


def parse_cpu_units(value: str) -> float:
    """Parse a Docker CPU quota-style value for numeric comparison."""

    normalized = require_non_blank(value, field_name="cpu").lower()
    if normalized.endswith("m"):
        raw = normalized[:-1]
        if not raw:
            raise ValueError("cpu must be a positive number")
        parsed = float(raw) / 1000
    else:
        parsed = float(normalized)
    if parsed <= 0:
        raise ValueError("cpu must be positive")
    return parsed


def parse_size_bytes(value: str) -> int:
    """Parse a Docker size string such as ``512m`` or ``2g``."""

    normalized = require_non_blank(value, field_name="size").lower()
    match = _SIZE_PATTERN.match(normalized)
    if match is None:
        raise ValueError(f"invalid size value: {value!r}")
    amount = float(match.group("value"))
    unit = match.group("unit") or ""
    multiplier = _SIZE_MULTIPLIERS.get(unit)
    if multiplier is None:
        raise ValueError(f"invalid size unit: {unit!r}")
    parsed = int(amount * multiplier)
    if parsed <= 0:
        raise ValueError("size must be positive")
    return parsed


def workload_container_name(
    *,
    task_run_id: str,
    step_id: str,
    attempt: int,
) -> str:
    """Return the deterministic Phase 1 workload container name."""

    task = _sanitize_name_part(task_run_id)
    step = _sanitize_name_part(step_id)
    return f"mm-workload-{task}-{step}-{attempt}"


def _sanitize_name_part(value: str) -> str:
    normalized = _CONTAINER_NAME_SAFE_PATTERN.sub("-", str(value).strip())
    normalized = normalized.strip("-._")
    return normalized or "unknown"


def _normalize_env_name(value: str, *, field_name: str) -> str:
    normalized = require_non_blank(value, field_name=field_name).upper()
    if _ENV_NAME_PATTERN.match(normalized) is None:
        raise ValueError(
            f"{field_name} must be an uppercase environment variable name"
        )
    return normalized


def _image_has_tag_or_digest(image: str) -> bool:
    if "@sha256:" in image:
        return True
    last_segment = image.rsplit("/", 1)[-1]
    return ":" in last_segment


class WorkloadMount(BaseModel):
    """Allowed container mount declaration for a runner profile."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    type: str = Field("volume", alias="type")
    source: NonBlankStr = Field(..., alias="source")
    target: NonBlankStr = Field(..., alias="target")
    read_only: bool = Field(False, alias="readOnly")

    @model_validator(mode="after")
    def _validate_mount(self) -> "WorkloadMount":
        if self.type != "volume":
            raise ValueError("mount type must be volume")
        if self.source.startswith("/") or ".." in self.source.split("/"):
            raise ValueError("mount source must be a Docker named volume")
        if not self.target.startswith("/work/") and self.target != "/work":
            raise ValueError("mount target must be under /work")
        if self.target == "/var/run/docker.sock" or "docker.sock" in self.target:
            raise ValueError("mount target must not expose the Docker socket")
        return self


class WorkloadResourceOverrides(BaseModel):
    """Per-request resource overrides capped by the selected runner profile."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    cpu: str | None = Field(None, alias="cpu")
    memory: str | None = Field(None, alias="memory")
    shm_size: str | None = Field(None, alias="shmSize")

    @model_validator(mode="after")
    def _validate_values(self) -> "WorkloadResourceOverrides":
        if self.cpu is not None:
            self.cpu = require_non_blank(self.cpu, field_name="cpu")
            parse_cpu_units(self.cpu)
        if self.memory is not None:
            self.memory = require_non_blank(self.memory, field_name="memory")
            parse_size_bytes(self.memory)
        if self.shm_size is not None:
            self.shm_size = require_non_blank(self.shm_size, field_name="shmSize")
            parse_size_bytes(self.shm_size)
        return self


class RunnerResourceProfile(BaseModel):
    """Default and maximum resources for one runner profile."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    cpu: NonBlankStr | None = Field(None, alias="cpu")
    memory: NonBlankStr | None = Field(None, alias="memory")
    shm_size: NonBlankStr | None = Field(None, alias="shmSize")
    max_cpu: NonBlankStr | None = Field(None, alias="maxCpu")
    max_memory: NonBlankStr | None = Field(None, alias="maxMemory")
    max_shm_size: NonBlankStr | None = Field(None, alias="maxShmSize")

    @model_validator(mode="before")
    @classmethod
    def _accept_snake_case(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        aliases = {
            "shm_size": "shmSize",
            "max_cpu": "maxCpu",
            "max_memory": "maxMemory",
            "max_shm_size": "maxShmSize",
        }
        return {aliases.get(str(key), key): item for key, item in value.items()}

    @model_validator(mode="after")
    def _validate_limits(self) -> "RunnerResourceProfile":
        if self.cpu is not None:
            parse_cpu_units(self.cpu)
        if self.max_cpu is not None:
            parse_cpu_units(self.max_cpu)
        if self.cpu is not None and self.max_cpu is not None:
            if parse_cpu_units(self.cpu) > parse_cpu_units(self.max_cpu):
                raise ValueError("cpu default must not exceed maxCpu")

        if self.memory is not None:
            parse_size_bytes(self.memory)
        if self.max_memory is not None:
            parse_size_bytes(self.max_memory)
        if self.memory is not None and self.max_memory is not None:
            if parse_size_bytes(self.memory) > parse_size_bytes(self.max_memory):
                raise ValueError("memory default must not exceed maxMemory")

        if self.shm_size is not None:
            parse_size_bytes(self.shm_size)
        if self.max_shm_size is not None:
            parse_size_bytes(self.max_shm_size)
        if self.shm_size is not None and self.max_shm_size is not None:
            if parse_size_bytes(self.shm_size) > parse_size_bytes(self.max_shm_size):
                raise ValueError("shmSize default must not exceed maxShmSize")
        return self


class WorkloadCleanupPolicy(BaseModel):
    """Cleanup behavior for a workload container."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    remove_container_on_exit: bool = Field(True, alias="removeContainerOnExit")
    kill_grace_seconds: int = Field(30, alias="killGraceSeconds", ge=0)

    @model_validator(mode="before")
    @classmethod
    def _accept_snake_case(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        aliases = {
            "remove_container_on_exit": "removeContainerOnExit",
            "kill_grace_seconds": "killGraceSeconds",
        }
        return {aliases.get(str(key), key): item for key, item in value.items()}


class WorkloadDevicePolicy(BaseModel):
    """Device access policy for Phase 1 runner profiles."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mode: WorkloadDeviceMode = Field("none", alias="mode")


class RunnerProfile(BaseModel):
    """Deployment-owned workload runner profile."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: NonBlankStr = Field(..., alias="id")
    kind: WorkloadKind = Field("one_shot", alias="kind")
    image: NonBlankStr = Field(..., alias="image")
    entrypoint: tuple[NonBlankStr, ...] = Field(default_factory=tuple, alias="entrypoint")
    command_wrapper: tuple[NonBlankStr, ...] = Field(
        default_factory=tuple,
        alias="commandWrapper",
    )
    workdir_template: NonBlankStr = Field(..., alias="workdirTemplate")
    required_mounts: tuple[WorkloadMount, ...] = Field(
        default_factory=tuple,
        alias="requiredMounts",
    )
    optional_mounts: tuple[WorkloadMount, ...] = Field(
        default_factory=tuple,
        alias="optionalMounts",
    )
    env_allowlist: tuple[str, ...] = Field(default_factory=tuple, alias="envAllowlist")
    network_policy: WorkloadNetworkPolicy = Field("none", alias="networkPolicy")
    resources: RunnerResourceProfile = Field(
        default_factory=RunnerResourceProfile,
        alias="resources",
    )
    timeout_seconds: int = Field(300, alias="timeoutSeconds", ge=1)
    max_timeout_seconds: int | None = Field(None, alias="maxTimeoutSeconds", ge=1)
    cleanup: WorkloadCleanupPolicy = Field(
        default_factory=WorkloadCleanupPolicy,
        alias="cleanup",
    )
    device_policy: WorkloadDevicePolicy = Field(
        default_factory=WorkloadDevicePolicy,
        alias="devicePolicy",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_snake_case(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        aliases = {
            "command_wrapper": "commandWrapper",
            "workdir_template": "workdirTemplate",
            "required_mounts": "requiredMounts",
            "optional_mounts": "optionalMounts",
            "env_allowlist": "envAllowlist",
            "network_policy": "networkPolicy",
            "timeout_seconds": "timeoutSeconds",
            "max_timeout_seconds": "maxTimeoutSeconds",
            "device_policy": "devicePolicy",
        }
        return {aliases.get(str(key), key): item for key, item in value.items()}

    @model_validator(mode="after")
    def _validate_profile(self) -> "RunnerProfile":
        if not _image_has_tag_or_digest(self.image):
            raise ValueError("image must include an explicit tag or digest")
        if self.image.rsplit("/", 1)[-1].endswith(":latest"):
            raise ValueError("image must not use the latest tag")
        if not self.workdir_template.startswith("/work/"):
            raise ValueError("workdirTemplate must be under /work")
        if not self.required_mounts:
            raise ValueError("requiredMounts must include a workspace mount")
        self.env_allowlist = tuple(
            dict.fromkeys(
                _normalize_env_name(key, field_name="envAllowlist[]")
                for key in self.env_allowlist
            )
        )
        if self.max_timeout_seconds is not None:
            if self.timeout_seconds > self.max_timeout_seconds:
                raise ValueError("timeoutSeconds must not exceed maxTimeoutSeconds")
        return self


class WorkloadOwnershipMetadata(BaseModel):
    """Deterministic ownership labels for a workload request."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["workload"] = Field("workload", alias="kind")
    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    step_id: NonBlankStr = Field(..., alias="stepId")
    attempt: int = Field(..., alias="attempt", ge=1)
    tool_name: NonBlankStr = Field(..., alias="toolName")
    workload_profile: NonBlankStr = Field(..., alias="workloadProfile")
    session_id: NonBlankStr | None = Field(None, alias="sessionId")
    session_epoch: int | None = Field(None, alias="sessionEpoch", ge=1)

    @property
    def labels(self) -> dict[str, str]:
        labels = {
            "moonmind.kind": self.kind,
            "moonmind.task_run_id": self.task_run_id,
            "moonmind.step_id": self.step_id,
            "moonmind.attempt": str(self.attempt),
            "moonmind.tool_name": self.tool_name,
            "moonmind.workload_profile": self.workload_profile,
        }
        if self.session_id is not None:
            labels["moonmind.session_id"] = self.session_id
        if self.session_epoch is not None:
            labels["moonmind.session_epoch"] = str(self.session_epoch)
        return labels


class WorkloadRequest(BaseModel):
    """Canonical request payload for one Docker-backed workload."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    profile_id: NonBlankStr = Field(..., alias="profileId")
    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    step_id: NonBlankStr = Field(..., alias="stepId")
    attempt: int = Field(..., alias="attempt", ge=1)
    tool_name: NonBlankStr = Field(..., alias="toolName")
    repo_dir: NonBlankStr = Field(..., alias="repoDir")
    artifacts_dir: NonBlankStr = Field(..., alias="artifactsDir")
    command: tuple[NonBlankStr, ...] = Field(..., alias="command", min_length=1)
    env_overrides: dict[str, str] = Field(default_factory=dict, alias="envOverrides")
    timeout_seconds: int | None = Field(None, alias="timeoutSeconds", ge=1)
    resources: WorkloadResourceOverrides = Field(
        default_factory=WorkloadResourceOverrides,
        alias="resources",
    )
    session_id: NonBlankStr | None = Field(None, alias="sessionId")
    session_epoch: int | None = Field(None, alias="sessionEpoch", ge=1)
    source_turn_id: NonBlankStr | None = Field(None, alias="sourceTurnId")

    @model_validator(mode="after")
    def _normalize_request(self) -> "WorkloadRequest":
        self.command = tuple(
            require_non_blank(item, field_name="command[]") for item in self.command
        )
        normalized_env: dict[str, str] = {}
        for raw_key, raw_value in self.env_overrides.items():
            key = _normalize_env_name(str(raw_key), field_name="envOverrides key")
            normalized_env[key] = str(raw_value)
        self.env_overrides = normalized_env
        if self.session_id is None:
            if self.session_epoch is not None or self.source_turn_id is not None:
                raise ValueError(
                    "sessionEpoch/sourceTurnId require sessionId association metadata"
                )
        return self

    def ownership_metadata(self) -> WorkloadOwnershipMetadata:
        return WorkloadOwnershipMetadata(
            taskRunId=self.task_run_id,
            stepId=self.step_id,
            attempt=self.attempt,
            toolName=self.tool_name,
            workloadProfile=self.profile_id,
            sessionId=self.session_id,
            sessionEpoch=self.session_epoch,
        )

    @property
    def container_name(self) -> str:
        return workload_container_name(
            task_run_id=self.task_run_id,
            step_id=self.step_id,
            attempt=self.attempt,
        )


class ValidatedWorkloadRequest(BaseModel):
    """Profile-aware validated workload request."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request: WorkloadRequest = Field(..., alias="request")
    profile: RunnerProfile = Field(..., alias="profile")
    ownership: WorkloadOwnershipMetadata = Field(..., alias="ownership")
    container_name: NonBlankStr = Field(..., alias="containerName")


class WorkloadResult(BaseModel):
    """Bounded workload execution result metadata."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: NonBlankStr = Field(..., alias="requestId")
    profile_id: NonBlankStr = Field(..., alias="profileId")
    status: WorkloadStatus = Field(..., alias="status")
    labels: dict[str, str] = Field(default_factory=dict, alias="labels")
    exit_code: int | None = Field(None, alias="exitCode")
    started_at: datetime | None = Field(None, alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    duration_seconds: float | None = Field(None, alias="durationSeconds", ge=0)
    timeout_reason: str | None = Field(None, alias="timeoutReason")
    stdout_ref: str | None = Field(None, alias="stdoutRef")
    stderr_ref: str | None = Field(None, alias="stderrRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    output_refs: dict[str, str] = Field(default_factory=dict, alias="outputRefs")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def _normalize_result(self) -> "WorkloadResult":
        for field_name in (
            "started_at",
            "completed_at",
        ):
            value = getattr(self, field_name)
            if isinstance(value, datetime) and value.tzinfo is None:
                setattr(self, field_name, value.replace(tzinfo=UTC))
        self.labels = {str(key): str(value) for key, value in self.labels.items()}
        self.output_refs = {
            require_non_blank(key, field_name="outputRefs key"): require_non_blank(
                value,
                field_name="outputRefs value",
            )
            for key, value in self.output_refs.items()
        }
        return self
