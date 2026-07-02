"""Typed contracts for persisted checkpoint branch graph records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.statuses.checkpoint_branch import (
    CheckpointBranchStateValue,
    CheckpointBranchTurnStateValue,
)

CheckpointBranchKind = Literal["root", "child_fork"]
CheckpointBranchWorkspacePolicy = Literal[
    "continue_from_previous_execution",
    "restore_pre_execution",
    "apply_previous_execution_diff_to_clean_baseline",
    "start_from_last_passed_commit",
    "fresh_branch_from_source",
]
CheckpointBranchRuntimeContextPolicy = Literal[
    "fresh_agent_run",
    "reuse_session_new_epoch",
    "reuse_session_same_epoch",
    "external_provider_continuation",
]
CheckpointBranchPublishStatus = Literal[
    "unpublished",
    "preparing",
    "published",
    "failed",
    "archived",
]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


class CheckpointBranchSourceModel(BaseModel):
    """Pinned source evidence for a checkpoint branch root or fork."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    root_workflow_id: str | None = Field(None, alias="rootWorkflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str | None = Field(None, alias="logicalStepId", min_length=1)
    source_execution_ordinal: int | None = Field(
        None, alias="sourceExecutionOrdinal", ge=1
    )
    checkpoint_boundary: str | None = Field(
        None, alias="checkpointBoundary", min_length=1
    )
    checkpoint_ref: str | None = Field(None, alias="checkpointRef", min_length=1)
    checkpoint_digest: str | None = Field(
        None, alias="checkpointDigest", min_length=1
    )
    source_state_kind: str | None = Field(None, alias="sourceStateKind", min_length=1)
    source_state_ref: str | None = Field(None, alias="sourceStateRef", min_length=1)
    source_state_digest: str | None = Field(
        None, alias="sourceStateDigest", min_length=1
    )

    @field_validator(
        "workflow_id",
        "root_workflow_id",
        "run_id",
        "logical_step_id",
        "checkpoint_boundary",
        "checkpoint_ref",
        "checkpoint_digest",
        "source_state_kind",
        "source_state_ref",
        "source_state_digest",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _requires_checkpoint_or_typed_state(self) -> "CheckpointBranchSourceModel":
        if self.checkpoint_ref:
            if not self.checkpoint_boundary:
                raise ValueError("checkpointRef requires checkpointBoundary")
            return self
        if self.source_state_kind and self.source_state_ref:
            return self
        raise ValueError(
            "checkpoint branch source requires checkpointRef or typed sourceStateRef"
        )


class CheckpointBranchCreateModel(BaseModel):
    """Input contract for persisting a checkpoint branch record."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    branch_id: str = Field(..., alias="branchId", min_length=1)
    source: CheckpointBranchSourceModel
    label: str = Field(..., min_length=1)
    state: CheckpointBranchStateValue = "created"
    branch_kind: CheckpointBranchKind = Field("root", alias="branchKind")
    workspace_policy: CheckpointBranchWorkspacePolicy = Field(
        ..., alias="workspacePolicy"
    )
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy = Field(
        ..., alias="runtimeContextPolicy"
    )
    parent_branch_id: str | None = Field(None, alias="parentBranchId", min_length=1)
    parent_turn_id: str | None = Field(None, alias="parentTurnId", min_length=1)
    git_repository: str | None = Field(None, alias="gitRepository", min_length=1)
    git_base_branch: str | None = Field(None, alias="gitBaseBranch", min_length=1)
    git_base_commit: str | None = Field(None, alias="gitBaseCommit", min_length=1)
    git_work_branch: str | None = Field(None, alias="gitWorkBranch", min_length=1)
    created_by: str | None = Field(None, alias="createdBy", min_length=1)

    @field_validator(
        "branch_id",
        "label",
        "parent_branch_id",
        "parent_turn_id",
        "git_repository",
        "git_base_branch",
        "git_base_commit",
        "git_work_branch",
        "created_by",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_branch_identity_and_lineage(self) -> "CheckpointBranchCreateModel":
        if ":execution:" in self.branch_id:
            raise ValueError("branchId must remain distinct from Step Execution ids")
        if self.branch_kind == "child_fork" and (
            not self.parent_branch_id or not self.parent_turn_id
        ):
            raise ValueError("child checkpoint branches require parent branch and turn")
        if self.parent_turn_id and not self.parent_branch_id:
            raise ValueError("parentTurnId requires parentBranchId")
        return self


class CheckpointBranchTurnCreateModel(BaseModel):
    """Input contract for appending one immutable branch turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    branch_turn_id: str = Field(..., alias="branchTurnId", min_length=1)
    branch_id: str = Field(..., alias="branchId", min_length=1)
    parent_turn_id: str | None = Field(None, alias="parentTurnId", min_length=1)
    source_checkpoint_ref: str | None = Field(
        None, alias="sourceCheckpointRef", min_length=1
    )
    source_checkpoint_digest: str | None = Field(
        None, alias="sourceCheckpointDigest", min_length=1
    )
    source_state_kind: str | None = Field(None, alias="sourceStateKind", min_length=1)
    source_state_ref: str | None = Field(None, alias="sourceStateRef", min_length=1)
    source_state_digest: str | None = Field(
        None, alias="sourceStateDigest", min_length=1
    )
    workspace_policy: CheckpointBranchWorkspacePolicy = Field(
        ..., alias="workspacePolicy"
    )
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy = Field(
        ..., alias="runtimeContextPolicy"
    )
    instruction_ref: str = Field(..., alias="instructionRef", min_length=1)
    instruction_digest: str = Field(..., alias="instructionDigest", min_length=1)
    context_bundle_ref: str | None = Field(
        None, alias="contextBundleRef", min_length=1
    )
    created_step_execution_id: str | None = Field(
        None, alias="createdStepExecutionId", min_length=1
    )
    runtime_agent_run_id: str | None = Field(
        None, alias="runtimeAgentRunId", min_length=1
    )
    provider_session_id: str | None = Field(
        None, alias="providerSessionId", min_length=1
    )
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    status: CheckpointBranchTurnStateValue = "created"

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str) or value is None:
            return _optional_text(value)
        return value

    @model_validator(mode="after")
    def _requires_checkpoint_or_typed_state(self) -> "CheckpointBranchTurnCreateModel":
        if self.created_step_execution_id in {self.branch_id, self.branch_turn_id}:
            raise ValueError(
                "branch turn Step Execution id must differ from branch and turn ids"
            )
        if self.source_checkpoint_ref:
            return self
        if self.source_state_kind and self.source_state_ref:
            return self
        raise ValueError(
            "branch turn requires sourceCheckpointRef or typed sourceStateRef"
        )


class StepExecutionBranchMetadataModel(BaseModel):
    """Optional Step Execution manifest lineage for checkpoint branch turns."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    branch_id: str = Field(..., alias="branchId", min_length=1)
    branch_turn_id: str = Field(..., alias="branchTurnId", min_length=1)
    root_checkpoint_ref: str | None = Field(
        None, alias="rootCheckpointRef", min_length=1
    )
    source_state_kind: str | None = Field(None, alias="sourceStateKind", min_length=1)
    source_state_ref: str | None = Field(None, alias="sourceStateRef", min_length=1)
    source_state_digest: str | None = Field(
        None, alias="sourceStateDigest", min_length=1
    )
    parent_branch_id: str | None = Field(None, alias="parentBranchId", min_length=1)
    parent_turn_id: str | None = Field(None, alias="parentTurnId", min_length=1)
    git_work_branch: str | None = Field(None, alias="gitWorkBranch", min_length=1)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str) or value is None:
            return _optional_text(value)
        return value

    @model_validator(mode="after")
    def _requires_root_checkpoint_or_typed_state(
        self,
    ) -> "StepExecutionBranchMetadataModel":
        if self.root_checkpoint_ref:
            return self
        if self.source_state_kind and self.source_state_ref:
            return self
        raise ValueError(
            "branch manifest metadata requires rootCheckpointRef "
            "or typed sourceStateRef"
        )


class CheckpointBranchRecordModel(BaseModel):
    """Read model for a persisted checkpoint branch."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    branch_id: str = Field(..., alias="branchId")
    workflow_id: str = Field(..., alias="workflowId")
    root_workflow_id: str = Field(..., alias="rootWorkflowId")
    source_run_id: str = Field(..., alias="sourceRunId")
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    source_execution_ordinal: int | None = Field(
        None, alias="sourceExecutionOrdinal"
    )
    source_checkpoint_boundary: str | None = Field(
        None, alias="sourceCheckpointBoundary"
    )
    source_checkpoint_ref: str | None = Field(None, alias="sourceCheckpointRef")
    source_checkpoint_digest: str | None = Field(
        None, alias="sourceCheckpointDigest"
    )
    source_state_kind: str | None = Field(None, alias="sourceStateKind")
    source_state_ref: str | None = Field(None, alias="sourceStateRef")
    source_state_digest: str | None = Field(None, alias="sourceStateDigest")
    parent_branch_id: str | None = Field(None, alias="parentBranchId")
    parent_turn_id: str | None = Field(None, alias="parentTurnId")
    label: str
    state: CheckpointBranchStateValue
    branch_kind: CheckpointBranchKind = Field(..., alias="branchKind")
    workspace_policy: CheckpointBranchWorkspacePolicy = Field(
        ..., alias="workspacePolicy"
    )
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy = Field(
        ..., alias="runtimeContextPolicy"
    )
    current_head_step_execution_id: str | None = Field(
        None, alias="currentHeadStepExecutionId"
    )
    current_head_checkpoint_ref: str | None = Field(
        None, alias="currentHeadCheckpointRef"
    )
    current_head_commit: str | None = Field(None, alias="currentHeadCommit")
    pull_request_url: str | None = Field(None, alias="pullRequestUrl")
    promoted_at: datetime | None = Field(None, alias="promotedAt")
    archived_at: datetime | None = Field(None, alias="archivedAt")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")


class CheckpointBranchTurnRecordModel(BaseModel):
    """Read model for one persisted checkpoint branch turn."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    branch_turn_id: str = Field(..., alias="branchTurnId")
    branch_id: str = Field(..., alias="branchId")
    parent_turn_id: str | None = Field(None, alias="parentTurnId")
    source_checkpoint_ref: str | None = Field(None, alias="sourceCheckpointRef")
    source_checkpoint_digest: str | None = Field(None, alias="sourceCheckpointDigest")
    source_state_kind: str | None = Field(None, alias="sourceStateKind")
    source_state_ref: str | None = Field(None, alias="sourceStateRef")
    source_state_digest: str | None = Field(None, alias="sourceStateDigest")
    workspace_policy: CheckpointBranchWorkspacePolicy = Field(
        ..., alias="workspacePolicy"
    )
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy = Field(
        ..., alias="runtimeContextPolicy"
    )
    instruction_ref: str = Field(..., alias="instructionRef")
    instruction_digest: str = Field(..., alias="instructionDigest")
    context_bundle_ref: str | None = Field(None, alias="contextBundleRef")
    created_step_execution_id: str | None = Field(
        None, alias="createdStepExecutionId"
    )
    runtime_agent_run_id: str | None = Field(None, alias="runtimeAgentRunId")
    provider_session_id: str | None = Field(None, alias="providerSessionId")
    idempotency_key: str = Field(..., alias="idempotencyKey")
    status: CheckpointBranchTurnStateValue
    started_at: datetime | None = Field(None, alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")


class CheckpointBranchArtifactRecordModel(BaseModel):
    """Read model for branch evidence artifacts."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    branch_id: str = Field(..., alias="branchId")
    branch_turn_id: str | None = Field(None, alias="branchTurnId")
    artifact_ref: str = Field(..., alias="artifactRef")
    artifact_kind: str = Field(..., alias="artifactKind")
    created_at: datetime = Field(..., alias="createdAt")


class CheckpointBranchGraphModel(BaseModel):
    """Branch graph read model with append-only evidence."""

    model_config = ConfigDict(populate_by_name=True)

    branch: CheckpointBranchRecordModel
    turns: list[CheckpointBranchTurnRecordModel] = Field(default_factory=list)
    artifacts: list[CheckpointBranchArtifactRecordModel] = Field(default_factory=list)


class CheckpointBranchGraphListModel(BaseModel):
    """List response for checkpoint branch graph records."""

    items: list[CheckpointBranchGraphModel] = Field(default_factory=list)


class CheckpointBranchGraphCreateModel(CheckpointBranchCreateModel):
    """Product-level branch create request including the first branch turn."""

    branch_turn_id: str | None = Field(None, alias="branchTurnId", min_length=1)
    instruction_ref: str = Field(..., alias="instructionRef", min_length=1)
    instruction_digest: str = Field(..., alias="instructionDigest", min_length=1)
    context_bundle_ref: str | None = Field(
        None, alias="contextBundleRef", min_length=1
    )
    created_step_execution_id: str | None = Field(
        None, alias="createdStepExecutionId", min_length=1
    )
    runtime_agent_run_id: str | None = Field(
        None, alias="runtimeAgentRunId", min_length=1
    )
    provider_session_id: str | None = Field(
        None, alias="providerSessionId", min_length=1
    )
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)


class CheckpointBranchContinueModel(BaseModel):
    """Product-level continue request for appending one branch turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    branch_turn_id: str | None = Field(None, alias="branchTurnId", min_length=1)
    instruction_ref: str = Field(..., alias="instructionRef", min_length=1)
    instruction_digest: str = Field(..., alias="instructionDigest", min_length=1)
    context_bundle_ref: str | None = Field(
        None, alias="contextBundleRef", min_length=1
    )
    created_step_execution_id: str | None = Field(
        None, alias="createdStepExecutionId", min_length=1
    )
    runtime_agent_run_id: str | None = Field(
        None, alias="runtimeAgentRunId", min_length=1
    )
    provider_session_id: str | None = Field(
        None, alias="providerSessionId", min_length=1
    )
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str) or value is None:
            return _optional_text(value)
        return value


class CheckpointBranchForkModel(BaseModel):
    """Product-level child fork request."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    branch_id: str = Field(..., alias="branchId", min_length=1)
    label: str = Field(..., min_length=1)
    parent_turn_id: str = Field(..., alias="parentTurnId", min_length=1)
    workspace_policy: CheckpointBranchWorkspacePolicy = Field(
        ..., alias="workspacePolicy"
    )
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy = Field(
        ..., alias="runtimeContextPolicy"
    )
    branch_turn_id: str | None = Field(None, alias="branchTurnId", min_length=1)
    instruction_ref: str = Field(..., alias="instructionRef", min_length=1)
    instruction_digest: str = Field(..., alias="instructionDigest", min_length=1)
    context_bundle_ref: str | None = Field(
        None, alias="contextBundleRef", min_length=1
    )
    created_step_execution_id: str | None = Field(
        None, alias="createdStepExecutionId", min_length=1
    )
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str) or value is None:
            return _optional_text(value)
        return value


class CheckpointBranchPublishReadyModel(BaseModel):
    """Mark a branch candidate ready for publication without promotion."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    artifact_ref: str | None = Field(None, alias="artifactRef", min_length=1)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str) or value is None:
            return _optional_text(value)
        return value


class CheckpointBranchArchiveModel(BaseModel):
    """Archive a branch without deleting its evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _strip_idempotency_key(cls, value: Any) -> str | None:
        return _optional_text(value)


class CheckpointBranchStateUpdateModel(BaseModel):
    """Small response for branch lifecycle transition operations."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    branch_id: str = Field(..., alias="branchId")
    state: CheckpointBranchStateValue
    promoted_at: datetime | None = Field(None, alias="promotedAt")
    archived_at: datetime | None = Field(None, alias="archivedAt")
