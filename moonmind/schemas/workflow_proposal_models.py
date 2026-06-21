"""Pydantic schemas for workflow proposal APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from moonmind.workflows.proposals.models import (
    WorkflowProposalOriginSource,
    WorkflowProposalReviewPriority,
    WorkflowProposalStatus,
)


class WorkflowProposalOriginModel(BaseModel):
    """Origin metadata for a proposal."""

    model_config = ConfigDict(populate_by_name=True)

    source: WorkflowProposalOriginSource = Field(..., alias="source")
    id: Optional[str] = Field(None, alias="id")
    metadata: dict[str, Any] | None = Field(None, alias="metadata")


class WorkflowProposalPreview(BaseModel):
    """Derived summary of the canonical task payload."""

    model_config = ConfigDict(populate_by_name=True)

    repository: str = Field(..., alias="repository")
    runtime_mode: Optional[str] = Field(None, alias="runtimeMode")
    skill_id: Optional[str] = Field(None, alias="skillId")
    # legacy_run contract: taskSkills wire alias renames at the MoonMind.UserWorkflow v2 cutover
    task_skills: Optional[list[str]] = Field(None, alias="taskSkills")
    publish_mode: Optional[str] = Field(None, alias="publishMode")
    priority: Optional[int] = Field(None, alias="priority")
    max_attempts: Optional[int] = Field(None, alias="maxAttempts")
    starting_branch: Optional[str] = Field(None, alias="startingBranch")
    target_branch: Optional[str] = Field(None, alias="targetBranch")
    instructions: Optional[str] = Field(None, alias="instructions")
    preset_provenance: Optional[str] = Field(None, alias="presetProvenance")
    authored_preset_count: int = Field(0, alias="authoredPresetCount")
    step_source_kinds: list[str] = Field(default_factory=list, alias="stepSourceKinds")
    preset_source_metadata: list[dict[str, Any]] = Field(
        default_factory=list, alias="presetSourceMetadata"
    )


class WorkflowProposalModel(BaseModel):
    """Serialized proposal model."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID = Field(..., alias="id")
    status: WorkflowProposalStatus = Field(..., alias="status")
    title: str = Field(..., alias="title")
    summary: str = Field(..., alias="summary")
    category: Optional[str] = Field(None, alias="category")
    tags: list[str] = Field(default_factory=list, alias="tags")
    repository: str = Field(..., alias="repository")
    dedup_key: str = Field(..., alias="dedupKey")
    dedup_hash: str = Field(..., alias="dedupHash")
    provider: str = Field("github", alias="provider")
    external_key: Optional[str] = Field(None, alias="externalKey")
    external_url: Optional[str] = Field(None, alias="externalUrl")
    delivered_at: Optional[datetime] = Field(None, alias="deliveredAt")
    last_synced_at: Optional[datetime] = Field(None, alias="lastSyncedAt")
    # legacy_run contract: workflowSnapshotRef wire alias and DB-bound attribute (WP7/v2 cutover)
    workflow_snapshot_ref: Optional[str] = Field(None, alias="workflowSnapshotRef")
    provider_metadata: dict[str, Any] = Field(
        default_factory=dict, alias="providerMetadata"
    )
    resolved_policy: dict[str, Any] = Field(
        default_factory=dict, alias="resolvedPolicy"
    )
    review_delivery: dict[str, Any] = Field(
        default_factory=dict, alias="reviewDelivery"
    )
    review_priority: WorkflowProposalReviewPriority = Field(
        WorkflowProposalReviewPriority.NORMAL, alias="reviewPriority"
    )
    priority_override_reason: Optional[str] = Field(
        None, alias="priorityOverrideReason"
    )
    proposed_by_worker_id: Optional[str] = Field(None, alias="proposedByWorkerId")
    proposed_by_user_id: Optional[UUID] = Field(None, alias="proposedByUserId")
    promoted_at: Optional[datetime] = Field(None, alias="promotedAt")
    promoted_by_user_id: Optional[UUID] = Field(None, alias="promotedByUserId")
    decided_by_user_id: Optional[UUID] = Field(None, alias="decidedByUserId")
    decision_note: Optional[str] = Field(None, alias="decisionNote")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    origin: WorkflowProposalOriginModel = Field(..., alias="origin")
    # legacy_run contract: workflowCreateRequest wire alias is produced by agent proposal
    # skills and Temporal activity payloads; renames at the v2 cutover, not before
    workflow_create_request: dict[str, Any] = Field(..., alias="workflowCreateRequest")
    # legacy_run contract: taskPreview wire alias renames at the v2 cutover
    task_preview: Optional[WorkflowProposalPreview] = Field(None, alias="taskPreview")
    promotion_result: dict[str, Any] | None = Field(None, alias="promotionResult")
    similar: list["WorkflowProposalSimilarModel"] = Field(
        default_factory=list, alias="similar"
    )


class WorkflowProposalSimilarModel(BaseModel):
    """Slim representation of similar proposals."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(..., alias="id")
    title: str = Field(..., alias="title")
    category: Optional[str] = Field(None, alias="category")
    repository: str = Field(..., alias="repository")
    created_at: datetime = Field(..., alias="createdAt")


class WorkflowProposalCreateRequest(BaseModel):
    """Request payload for creating a workflow proposal."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., alias="title")
    summary: str = Field(..., alias="summary")
    category: Optional[str] = Field(None, alias="category")
    tags: Optional[list[str]] = Field(None, alias="tags")
    origin: WorkflowProposalOriginModel = Field(..., alias="origin")
    # legacy_run contract: workflowCreateRequest wire alias is produced by agent proposal
    # skills and Temporal activity payloads; renames at the v2 cutover, not before
    workflow_create_request: dict[str, Any] = Field(..., alias="workflowCreateRequest")
    review_priority: Optional[str] = Field(None, alias="reviewPriority")
    provider: Optional[str] = Field(None, alias="provider")
    provider_metadata: dict[str, Any] | None = Field(None, alias="providerMetadata")
    resolved_policy: dict[str, Any] | None = Field(None, alias="resolvedPolicy")


class WorkflowProposalListResponse(BaseModel):
    """Response payload for listing proposals."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[WorkflowProposalModel] = Field(default_factory=list, alias="items")
    next_cursor: Optional[str] = Field(None, alias="nextCursor")


class WorkflowProposalPromoteRequest(BaseModel):
    """Optional overrides supplied during promotion."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    priority: Optional[int] = Field(None, alias="priority")
    max_attempts: Optional[int] = Field(None, alias="maxAttempts")
    note: Optional[str] = Field(None, alias="note")
    runtime_mode: Optional[str] = Field(
        None,
        alias="runtimeMode",
        description=(
            "Shortcut to override only the agent runtime mode (e.g. gemini_cli, "
            "jules, codex) while preserving the reviewed proposal payload."
        ),
    )


class WorkflowProposalDismissRequest(BaseModel):
    """Dismissal note payload."""

    model_config = ConfigDict(populate_by_name=True)

    note: Optional[str] = Field(None, alias="note")


class WorkflowProposalPromoteResponse(BaseModel):
    """Promotion response containing proposal + queue job."""

    model_config = ConfigDict(populate_by_name=True)

    proposal: WorkflowProposalModel = Field(..., alias="proposal")
    promoted_execution_id: str = Field(
        ..., alias="promotedExecutionId", title="PromotedExecutionId"
    )


class WorkflowProposalPriorityRequest(BaseModel):
    """Reviewer priority update request."""

    model_config = ConfigDict(populate_by_name=True)

    priority: str = Field(..., alias="priority")


class WorkflowProposalProviderAuthenticityModel(BaseModel):
    """Provider authenticity verification summary for decision ingress."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    verified: bool = Field(False, alias="verified")
    method: Optional[str] = Field(None, alias="method")


class WorkflowProposalProviderDecisionRequest(BaseModel):
    """Trusted provider decision ingress payload."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    provider: str = Field(..., alias="provider")
    external_key: str = Field(..., alias="externalKey")
    provider_event_id: str = Field(..., alias="providerEventId")
    actor: str = Field(..., alias="actor")
    action: Optional[str] = Field(None, alias="action")
    body: str = Field("", alias="body")
    note: Optional[str] = Field(None, alias="note")
    observed_at: Optional[datetime] = Field(None, alias="observedAt")
    authenticity: WorkflowProposalProviderAuthenticityModel = Field(
        default_factory=WorkflowProposalProviderAuthenticityModel,
        alias="authenticity",
    )
    runtime_mode: Optional[str] = Field(None, alias="runtimeMode")
    priority: Optional[int] = Field(None, alias="priority")
    max_attempts: Optional[int] = Field(None, alias="maxAttempts")
    external_state: Optional[str] = Field(None, alias="externalState")


class WorkflowProposalProviderDecisionResponse(BaseModel):
    """Sanitized provider decision ingestion response."""

    model_config = ConfigDict(populate_by_name=True)

    accepted: bool = Field(..., alias="accepted")
    decision: Optional[str] = Field(None, alias="decision")
    reason: Optional[str] = Field(None, alias="reason")
    actor: str = Field(..., alias="actor")
    provider_event_id: str = Field(..., alias="providerEventId")
    note: Optional[str] = Field(None, alias="note")
    priority: Optional[str] = Field(None, alias="priority")
    execution_priority: Optional[int] = Field(None, alias="executionPriority")
    max_attempts: Optional[int] = Field(None, alias="maxAttempts")
    defer_until: Optional[str] = Field(None, alias="deferUntil")
    runtime_mode: Optional[str] = Field(None, alias="runtimeMode")
    resulting_external_state: Optional[str] = Field(
        None, alias="resultingExternalState"
    )
    promoted_execution_id: Optional[str] = Field(None, alias="promotedExecutionId")
    proposal: WorkflowProposalModel = Field(..., alias="proposal")
