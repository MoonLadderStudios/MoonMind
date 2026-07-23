"""Strict contracts for managed-runtime-owned workspace checkpoint capture."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas.temporal_models import (
    StepExecutionCheckpointBoundary,
    StepExecutionIdentityModel,
    WorkspaceCheckpointEvidenceModel,
)
from moonmind.schemas.workspace_locator_models import ManagedWorkspaceLocator


class ManagedCheckpointCapturePolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    include_tracked: Literal[True] = Field(True, alias="includeTracked")
    include_untracked: bool = Field(True, alias="includeUntracked")
    include_ignored: Literal[False] = Field(False, alias="includeIgnored")
    redaction_profile: Literal["managed-code-workspace-v1"] = Field(
        "managed-code-workspace-v1", alias="redactionProfile"
    )
    max_file_count: int = Field(20_000, alias="maxFileCount", ge=1, le=100_000)
    max_file_bytes: int = Field(100 * 1024 * 1024, alias="maxFileBytes", ge=1)
    max_total_bytes: int = Field(1024 * 1024 * 1024, alias="maxTotalBytes", ge=1)


class ManagedWorkspaceCheckpointCaptureInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    identity: StepExecutionIdentityModel
    boundary: StepExecutionCheckpointBoundary
    checkpoint_kind: Literal["worktree_archive"] = Field(
        "worktree_archive", alias="checkpointKind"
    )
    workspace_locator: ManagedWorkspaceLocator = Field(..., alias="workspaceLocator")
    expected_runtime_id: Literal["codex_cli"] = Field(
        "codex_cli", alias="expectedRuntimeId"
    )
    capability_set_version: Literal[
        "runtime-execution-capabilities-v1",
        "runtime-execution-capabilities-v2",
        "runtime-execution-capabilities-v3",
    ] = Field(
        ..., alias="capabilitySetVersion"
    )
    capability_digest: str = Field(..., alias="capabilityDigest", min_length=1, max_length=128)
    artifact_namespace: str = Field(..., alias="artifactNamespace", min_length=1, max_length=500)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1, max_length=500)
    capture_policy: ManagedCheckpointCapturePolicy = Field(
        default_factory=ManagedCheckpointCapturePolicy, alias="capturePolicy"
    )

    @field_validator("capability_digest", "artifact_namespace", "idempotency_key")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def _runtime_matches(self) -> "ManagedWorkspaceCheckpointCaptureInput":
        if self.workspace_locator.runtime_id != self.expected_runtime_id:
            raise ValueError("WORKSPACE_IDENTITY_MISMATCH: locator runtime does not match")
        return self


class ManagedCheckpointEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    path: str
    type: Literal["file", "symlink"]
    mode: str
    size: int = Field(ge=0)
    sha256: str
    link_target: str | None = Field(None, alias="linkTarget")


class ManagedWorkspaceCheckpointCaptureResult(BaseModel):
    """Compact workflow result; archive contents and entry list remain artifacts."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    status: Literal["captured", "invalid", "unsupported", "failed"]
    checkpoint_kind: Literal["worktree_archive"] = Field(
        "worktree_archive", alias="checkpointKind"
    )
    workspace: WorkspaceCheckpointEvidenceModel | None = None
    source_workspace_locator: ManagedWorkspaceLocator = Field(..., alias="sourceWorkspaceLocator")
    diagnostic_refs: list[str] = Field(default_factory=list, alias="diagnosticRefs", max_length=10)
    idempotency_key: str = Field(..., alias="idempotencyKey")
    failure_code: str | None = Field(None, alias="failureCode", max_length=100)
    summary: str | None = Field(None, max_length=1000)
