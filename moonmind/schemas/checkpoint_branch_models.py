"""Typed checkpoint branch API and persistence models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

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
PublishMode = Literal["none", "branch", "pull_request"]


class CheckpointBranchApiSourceModel(BaseModel):
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

    source: CheckpointBranchApiSourceModel
    label: str = Field(..., min_length=1, max_length=200)
    instructions: CheckpointBranchInstructionsModel
    workspace_policy: CheckpointBranchWorkspacePolicy = Field(..., alias="workspacePolicy")
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy = Field(
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
    workspace_policy: CheckpointBranchWorkspacePolicy = Field(
        "continue_from_previous_execution", alias="workspacePolicy"
    )
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy = Field(
        "reuse_session_new_epoch", alias="runtimeContextPolicy"
    )
    idempotency_key: str = Field(
        ..., alias="idempotencyKey", min_length=1, max_length=512
    )
    max_budget_usd: float | None = Field(None, alias="maxBudgetUsd", ge=0)


class CheckpointBranchForkRequest(CheckpointBranchContinueRequest):
    parent_turn_id: str | None = Field(None, alias="parentTurnId")
    workspace_policy: CheckpointBranchWorkspacePolicy = Field(
        "apply_previous_execution_diff_to_clean_baseline", alias="workspacePolicy"
    )
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy = Field(
        "fresh_agent_run", alias="runtimeContextPolicy"
    )


class CheckpointBranchTurnLaunchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    created_step_execution_id: str = Field(
        ..., alias="createdStepExecutionId", min_length=1, max_length=255
    )
    runtime_agent_run_id: str | None = Field(
        None, alias="runtimeAgentRunId", min_length=1, max_length=255
    )
    provider_session_id: str | None = Field(
        None, alias="providerSessionId", min_length=1, max_length=255
    )
    workspace_baseline: dict[str, Any] = Field(
        default_factory=dict, alias="workspaceBaseline"
    )
    prior_evidence_refs: list[str] = Field(
        default_factory=list, alias="priorEvidenceRefs"
    )
    bounded_summaries: list[dict[str, Any]] = Field(
        default_factory=list, alias="boundedSummaries", max_length=10
    )
    builder_metadata: dict[str, Any] = Field(
        default_factory=dict, alias="builderMetadata"
    )
    runtime_request_ref: str | None = Field(
        None, alias="runtimeRequestRef", min_length=1, max_length=1024
    )
    runtime_result_ref: str | None = Field(
        None, alias="runtimeResultRef", min_length=1, max_length=1024
    )
    diagnostics_ref: str | None = Field(
        None, alias="diagnosticsRef", min_length=1, max_length=1024
    )

    @field_validator(
        "created_step_execution_id",
        "runtime_agent_run_id",
        "provider_session_id",
        "runtime_request_ref",
        "runtime_result_ref",
        "diagnostics_ref",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("prior_evidence_refs", mode="after")
    @classmethod
    def _refs_are_bounded(cls, value: list[str]) -> list[str]:
        refs = [_optional_text(item) for item in value]
        compact = [item for item in refs if item]
        if len(compact) != len(value):
            raise ValueError("priorEvidenceRefs must contain non-empty refs")
        if len(compact) > 25:
            raise ValueError("priorEvidenceRefs must be bounded")
        return compact

    @field_validator("bounded_summaries", mode="after")
    @classmethod
    def _summaries_are_bounded(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in value:
            summary = item.get("summary")
            if summary is not None and len(str(summary)) > 1200:
                raise ValueError("boundedSummaries entries must be bounded")
        return value

    @model_validator(mode="after")
    def _requires_runtime_evidence(self) -> "CheckpointBranchTurnLaunchRequest":
        if not self.created_step_execution_id:
            raise ValueError("launch requires Step Execution evidence")
        return self


class CheckpointBranchPromoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_head_step_execution_id: str = Field(
        ..., alias="expectedHeadStepExecutionId"
    )
    expected_head_commit: str | None = Field(None, alias="expectedHeadCommit")
    accepted_output_refs: dict[str, Any] = Field(
        default_factory=dict, alias="acceptedOutputRefs"
    )
    gate_evidence: dict[str, Any] = Field(..., alias="gateEvidence")
    side_effect_disposition: dict[str, Any] = Field(..., alias="sideEffectDisposition")
    downstream_invalidation: dict[str, Any] = Field(
        default_factory=dict, alias="downstreamInvalidation"
    )
    approval_evidence: dict[str, Any] | None = Field(None, alias="approvalEvidence")
    policy_evidence: dict[str, Any] = Field(
        default_factory=dict, alias="policyEvidence"
    )
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
    context_bundle_ref: str | None = Field(None, alias="contextBundleRef")
    step_execution_manifest_ref: str | None = Field(
        None, alias="stepExecutionManifestRef"
    )
    created_step_execution_id: str | None = Field(None, alias="createdStepExecutionId")
    runtime_agent_run_id: str | None = Field(None, alias="runtimeAgentRunId")
    provider_session_id: str | None = Field(None, alias="providerSessionId")
    idempotency_key: str = Field(..., alias="idempotencyKey")
    status: str
    diagnostics: dict[str, Any] = Field(default_factory=dict)
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
    state: CheckpointBranchStateValue
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
    current_head_checkpoint_digest: str | None = Field(
        None, alias="currentHeadCheckpointDigest"
    )
    current_head_version: int | None = Field(None, alias="currentHeadVersion")
    current_head_attempt_ordinal: int | None = Field(
        None, alias="currentHeadAttemptOrdinal"
    )
    remediation_loop_id: str | None = Field(None, alias="remediationLoopId")
    remediation_head_status: str | None = Field(None, alias="remediationHeadStatus")
    latest_verification_ref: str | None = Field(
        None, alias="latestVerificationRef"
    )
    latest_verification_verdict: str | None = Field(
        None, alias="latestVerificationVerdict"
    )
    artifact_refs: dict[str, Any] = Field(default_factory=dict, alias="artifactRefs")
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
    comparison_record: dict[str, Any] = Field(
        default_factory=dict, alias="comparisonRecord"
    )


# Persistence/service contract models.
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
    current_head_checkpoint_digest: str | None = Field(
        None, alias="currentHeadCheckpointDigest"
    )
    current_head_version: int | None = Field(None, alias="currentHeadVersion")
    current_head_attempt_ordinal: int | None = Field(
        None, alias="currentHeadAttemptOrdinal"
    )
    remediation_loop_id: str | None = Field(None, alias="remediationLoopId")
    remediation_head_status: str | None = Field(None, alias="remediationHeadStatus")
    latest_verification_ref: str | None = Field(
        None, alias="latestVerificationRef"
    )
    latest_verification_verdict: str | None = Field(
        None, alias="latestVerificationVerdict"
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

    id: UUID
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

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str) or value is None:
            return _optional_text(value)
        return value


class CheckpointBranchContinueModel(BaseModel):
    """Product-level continue request for appending one branch turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    branch_turn_id: str | None = Field(None, alias="branchTurnId", min_length=1)
    workspace_policy: CheckpointBranchWorkspacePolicy | None = Field(
        None, alias="workspacePolicy"
    )
    runtime_context_policy: CheckpointBranchRuntimeContextPolicy | None = Field(
        None, alias="runtimeContextPolicy"
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


class CheckpointBranchStateUpdateModel(BaseModel):
    """Small response for branch lifecycle transition operations."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    branch_id: str = Field(..., alias="branchId")
    state: CheckpointBranchStateValue
    promoted_at: datetime | None = Field(None, alias="promotedAt")
    archived_at: datetime | None = Field(None, alias="archivedAt")

    @field_validator("promoted_at", "archived_at", mode="after")
    @classmethod
    def _ensure_utc(cls, value: datetime | None) -> datetime | None:
        # Naive values come from DB round trips that drop tzinfo (SQLite);
        # stored instants are UTC, so tag them for consistent read models.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
