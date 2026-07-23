"""Strict contracts for managed ``codex_cli`` workspace cold restoration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workspace_locator_models import ManagedWorkspaceLocator


RESTORATION_EVIDENCE_CONTENT_TYPE = (
    "application/vnd.moonmind.managed-workspace-restoration+json;version=1"
)


class RestoreIdentity(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    workflow_id: str = Field(alias="workflowId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    logical_step_id: str = Field(alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(alias="executionOrdinal", ge=1)


class RestoreSource(RestoreIdentity):
    checkpoint_ref: str = Field(alias="checkpointRef", min_length=1)
    checkpoint_boundary: str = Field(alias="checkpointBoundary", min_length=1)
    source_workspace_locator: ManagedWorkspaceLocator | None = Field(
        None, alias="sourceWorkspaceLocator"
    )


class WorktreeArchiveCheckpoint(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    kind: Literal["worktree_archive"]
    base_commit: str = Field(alias="baseCommit", min_length=1)
    archive_ref: str = Field(alias="archiveRef", min_length=1)
    archive_digest: str = Field(alias="archiveDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    manifest_ref: str = Field(alias="manifestRef", min_length=1)
    manifest_digest: str = Field(
        alias="manifestDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )


class RestoreDestination(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    runtime_id: Literal["codex_cli"] = Field(alias="runtimeId")
    agent_run_id: str = Field(alias="agentRunId", min_length=1, max_length=300)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    relative_path: Literal["repo"] = Field("repo", alias="relativePath")


class ManagedWorkspaceRestoreRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_version: Literal["v1"] = Field(alias="schemaVersion")
    recovery_identity: RestoreIdentity = Field(alias="recoveryIdentity")
    source: RestoreSource
    checkpoint: WorktreeArchiveCheckpoint
    destination: RestoreDestination
    workspace_policy: Literal[
        "restore_pre_execution", "restore_publication_candidate"
    ] = Field(alias="workspacePolicy")
    resume_phase: Literal["rerun_failed_step", "resume_publication"] = Field(
        alias="resumePhase"
    )
    capability_set_version: str = Field(alias="capabilitySetVersion", min_length=1)
    capability_digest: str = Field(alias="capabilityDigest", min_length=1)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1)
    max_entry_count: int = Field(100_000, alias="maxEntryCount", ge=1, le=1_000_000)
    max_restored_bytes: int = Field(
        2 * 1024 * 1024 * 1024, alias="maxRestoredBytes", ge=1
    )

    @model_validator(mode="after")
    def validate_distinct_identities(self) -> "ManagedWorkspaceRestoreRequest":
        if (self.source.workflow_id, self.source.run_id) == (
            self.recovery_identity.workflow_id,
            self.recovery_identity.run_id,
        ):
            raise ValueError(
                "source and recovery workflow/run identities must be distinct"
            )
        locator = self.source.source_workspace_locator
        if (
            locator is not None
            and locator.agent_run_id == self.destination.agent_run_id
        ):
            raise ValueError("source workspace locator cannot be reused as destination")
        return self


class ManagedWorkspaceRestoreResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    status: Literal["succeeded"] = "succeeded"
    checkpoint_ref: str = Field(alias="checkpointRef")
    destination_workspace_locator: ManagedWorkspaceLocator = Field(
        alias="destinationWorkspaceLocator"
    )
    restoration_evidence_ref: str = Field(alias="restorationEvidenceRef")
    restoration_evidence_digest: str = Field(alias="restorationEvidenceDigest")
    base_commit: str = Field(alias="baseCommit")
    restored_entry_count: int = Field(alias="restoredEntryCount", ge=0)
    restored_bytes: int = Field(alias="restoredBytes", ge=0)
    git_status_digest: str = Field(alias="gitStatusDigest")
    idempotency_key: str = Field(alias="idempotencyKey")
    diagnostic_refs: list[str] = Field(default_factory=list, alias="diagnosticRefs")


class CheckpointRestoreError(RuntimeError):
    """Stable fail-closed restore error surfaced before agent launch."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.failure_envelope = {
            "schemaVersion": "v1",
            "failureClass": "recovery_restoration",
            "failureCode": code,
            "retryRecommendation": (
                "retry"
                if code
                in {"CHECKPOINT_ARTIFACT_MISSING", "CHECKPOINT_REPOSITORY_UNAVAILABLE"}
                else "do_not_retry"
            ),
            "message": message[:500],
        }
        super().__init__(f"{code}: {message}")
