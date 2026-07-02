"""Typed API models for checkpoint branch operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BranchState = Literal[
    "draft",
    "running",
    "blocked",
    "failed",
    "promotable",
    "published",
    "promoted",
    "archived",
    "superseded",
]
WorkspacePolicy = Literal[
    "apply_previous_execution_diff_to_clean_baseline",
    "continue_from_previous_execution",
    "from_checkpoint_worktree",
    "from_last_accepted_commit",
]
RuntimeContextPolicy = Literal[
    "fresh_agent_run",
    "reuse_session_new_epoch",
    "reuse_session_same_epoch",
    "external_provider_continuation",
]
PublishMode = Literal["none", "branch", "pull_request"]


class CheckpointBranchSourceModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str | None = Field(None, alias="workflowId")
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    execution_ordinal: int | None = Field(None, alias="executionOrdinal", ge=0)
    checkpoint_boundary: str = Field(..., alias="checkpointBoundary", min_length=1)
    checkpoint_ref: str = Field(..., alias="checkpointRef", min_length=1)
    checkpoint_digest: str | None = Field(None, alias="checkpointDigest")

    @field_validator("checkpoint_ref")
    @classmethod
    def _checkpoint_ref_must_be_artifact_ref(cls, value: str) -> str:
        if not value.startswith("artifact://"):
            raise ValueError("checkpointRef must be an artifact ref")
        return value

    @field_validator("checkpoint_boundary")
    @classmethod
    def _checkpoint_boundary_is_known(cls, value: str) -> str:
        if value not in {
            "before_execution",
            "after_execution",
            "before_recovery_restoration",
        }:
            raise ValueError("checkpointBoundary is not supported")
        return value

    @field_validator("checkpoint_digest")
    @classmethod
    def _checkpoint_digest_is_typed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("sha256:") or len(value) <= len("sha256:"):
            raise ValueError("checkpointDigest must be a sha256 digest")
        return value


class CheckpointBranchInstructionsModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str | None = Field(None, min_length=1)
    instruction_ref: str | None = Field(None, alias="instructionRef")
    instruction_digest: str | None = Field(None, alias="instructionDigest")

    @model_validator(mode="after")
    def _requires_instruction_text_or_ref(self) -> "CheckpointBranchInstructionsModel":
        if not self.text and not self.instruction_ref:
            raise ValueError("instructions require text or instructionRef")
        if self.instruction_ref and not self.instruction_digest:
            raise ValueError("instructionDigest is required with instructionRef")
        if self.instruction_digest and not self.instruction_digest.startswith(
            "sha256:"
        ):
            raise ValueError("instructionDigest must be a sha256 digest")
        return self


class CheckpointBranchCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: CheckpointBranchSourceModel
    label: str = Field(..., min_length=1, max_length=200)
    instructions: CheckpointBranchInstructionsModel
    workspace_policy: WorkspacePolicy = Field(..., alias="workspacePolicy")
    runtime_context_policy: RuntimeContextPolicy = Field(
        "fresh_agent_run", alias="runtimeContextPolicy"
    )
    publish_mode: PublishMode = Field("none", alias="publishMode")
    idempotency_key: str = Field(
        ..., alias="idempotencyKey", min_length=1, max_length=512
    )
    git_work_branch: str | None = Field(None, alias="gitWorkBranch", max_length=255)
    max_budget_usd: float | None = Field(None, alias="maxBudgetUsd", ge=0)


class CheckpointBranchContinueRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str | None = Field(None, max_length=200)
    instructions: CheckpointBranchInstructionsModel
    workspace_policy: WorkspacePolicy = Field(
        "continue_from_previous_execution", alias="workspacePolicy"
    )
    runtime_context_policy: RuntimeContextPolicy = Field(
        "reuse_session_new_epoch", alias="runtimeContextPolicy"
    )
    idempotency_key: str = Field(
        ..., alias="idempotencyKey", min_length=1, max_length=512
    )
    max_budget_usd: float | None = Field(None, alias="maxBudgetUsd", ge=0)


class CheckpointBranchForkRequest(CheckpointBranchContinueRequest):
    parent_turn_id: str | None = Field(None, alias="parentTurnId")
    workspace_policy: WorkspacePolicy = Field(
        "apply_previous_execution_diff_to_clean_baseline", alias="workspacePolicy"
    )
    runtime_context_policy: RuntimeContextPolicy = Field(
        "fresh_agent_run", alias="runtimeContextPolicy"
    )


class CheckpointBranchPromoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_head_step_execution_id: str = Field(
        ..., alias="expectedHeadStepExecutionId"
    )
    expected_head_commit: str | None = Field(None, alias="expectedHeadCommit")
    gate_evidence: dict[str, Any] = Field(..., alias="gateEvidence")
    side_effect_disposition: dict[str, Any] = Field(..., alias="sideEffectDisposition")
    approval_evidence: dict[str, Any] | None = Field(None, alias="approvalEvidence")
    policy_requires_approval: bool = Field(False, alias="policyRequiresApproval")
    idempotency_key: str = Field(
        ..., alias="idempotencyKey", min_length=1, max_length=512
    )


class CheckpointBranchArchiveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str | None = Field(None, max_length=500)
    idempotency_key: str = Field(
        ..., alias="idempotencyKey", min_length=1, max_length=512
    )


class CheckpointBranchPublishRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["branch", "pull_request"] = "pull_request"
    repository: str = Field(..., min_length=1, max_length=255)
    base_branch: str = Field(..., alias="baseBranch", min_length=1, max_length=255)
    head_branch: str = Field(..., alias="headBranch", min_length=1, max_length=255)
    provider: str = Field("github", min_length=1, max_length=64)
    idempotency_key: str = Field(
        ..., alias="idempotencyKey", min_length=1, max_length=512
    )


class CheckpointSummaryModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    checkpoint_ref: str = Field(..., alias="checkpointRef")
    checkpoint_boundary: str = Field(..., alias="checkpointBoundary")
    run_id: str | None = Field(None, alias="runId")
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    execution_ordinal: int | None = Field(None, alias="executionOrdinal")
    checkpoint_digest: str | None = Field(None, alias="checkpointDigest")


class CheckpointBranchTurnModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    branch_turn_id: str = Field(..., alias="branchTurnId")
    branch_id: str = Field(..., alias="branchId")
    parent_turn_id: str | None = Field(None, alias="parentTurnId")
    instruction_ref: str = Field(..., alias="instructionRef")
    instruction_digest: str = Field(..., alias="instructionDigest")
    source_checkpoint_ref: str = Field(..., alias="sourceCheckpointRef")
    source_checkpoint_digest: str | None = Field(None, alias="sourceCheckpointDigest")
    created_step_execution_id: str | None = Field(None, alias="createdStepExecutionId")
    idempotency_key: str = Field(..., alias="idempotencyKey")
    status: str
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")


class CheckpointBranchModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    branch_id: str = Field(..., alias="branchId")
    workflow_id: str = Field(..., alias="workflowId")
    root_workflow_id: str = Field(..., alias="rootWorkflowId")
    source_run_id: str = Field(..., alias="sourceRunId")
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    source_execution_ordinal: int | None = Field(None, alias="sourceExecutionOrdinal")
    source_checkpoint_boundary: str = Field(..., alias="sourceCheckpointBoundary")
    source_checkpoint_ref: str = Field(..., alias="sourceCheckpointRef")
    source_checkpoint_digest: str | None = Field(None, alias="sourceCheckpointDigest")
    parent_branch_id: str | None = Field(None, alias="parentBranchId")
    parent_turn_id: str | None = Field(None, alias="parentTurnId")
    label: str
    state: BranchState
    branch_kind: str = Field(..., alias="branchKind")
    workspace_policy: str = Field(..., alias="workspacePolicy")
    runtime_context_policy: str = Field(..., alias="runtimeContextPolicy")
    git_repository: str | None = Field(None, alias="gitRepository")
    git_base_branch: str | None = Field(None, alias="gitBaseBranch")
    git_work_branch: str | None = Field(None, alias="gitWorkBranch")
    current_head_step_execution_id: str | None = Field(
        None, alias="currentHeadStepExecutionId"
    )
    current_head_checkpoint_ref: str | None = Field(
        None, alias="currentHeadCheckpointRef"
    )
    current_head_commit: str | None = Field(None, alias="currentHeadCommit")
    pull_request_url: str | None = Field(None, alias="pullRequestUrl")
    publish_status: str | None = Field(None, alias="publishStatus")
    promoted_at: datetime | None = Field(None, alias="promotedAt")
    archived_at: datetime | None = Field(None, alias="archivedAt")
    created_by: str | None = Field(None, alias="createdBy")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")


class CheckpointListResponse(BaseModel):
    items: list[CheckpointSummaryModel] = Field(default_factory=list)


class CheckpointBranchListResponse(BaseModel):
    items: list[CheckpointBranchModel] = Field(default_factory=list)


class CheckpointBranchTurnListResponse(BaseModel):
    items: list[CheckpointBranchTurnModel] = Field(default_factory=list)


class CheckpointBranchCompareResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    branch_id: str = Field(..., alias="branchId")
    against_branch_id: str = Field(..., alias="againstBranchId")
    workflow_id: str = Field(..., alias="workflowId")
    branch_state: str = Field(..., alias="branchState")
    against_state: str = Field(..., alias="againstState")
    branch_head_step_execution_id: str | None = Field(
        None, alias="branchHeadStepExecutionId"
    )
    against_head_step_execution_id: str | None = Field(
        None, alias="againstHeadStepExecutionId"
    )
    summary_ref: str | None = Field(None, alias="summaryRef")
    diagnostics_refs: list[str] = Field(default_factory=list, alias="diagnosticsRefs")
