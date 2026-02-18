"""Pydantic schemas for task proposal APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas.agent_queue_models import CreateJobRequest, JobModel
from moonmind.workflows.task_proposals.models import (
    TaskProposalOriginSource,
    TaskProposalReviewPriority,
    TaskProposalStatus,
)


class TaskProposalOriginModel(BaseModel):
    """Origin metadata for a proposal."""

    model_config = ConfigDict(populate_by_name=True)

    source: TaskProposalOriginSource = Field(..., alias="source")
    id: Optional[UUID] = Field(None, alias="id")
    metadata: dict[str, Any] | None = Field(None, alias="metadata")


class TaskProposalTaskPreview(BaseModel):
    """Derived summary of the canonical task payload."""

    model_config = ConfigDict(populate_by_name=True)

    repository: str = Field(..., alias="repository")
    runtime_mode: Optional[str] = Field(None, alias="runtimeMode")
    skill_id: Optional[str] = Field(None, alias="skillId")
    publish_mode: Optional[str] = Field(None, alias="publishMode")
    starting_branch: Optional[str] = Field(None, alias="startingBranch")
    new_branch: Optional[str] = Field(None, alias="newBranch")
    instructions: Optional[str] = Field(None, alias="instructions")


class TaskProposalModel(BaseModel):
    """Serialized proposal model."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID = Field(..., alias="id")
    status: TaskProposalStatus = Field(..., alias="status")
    title: str = Field(..., alias="title")
    summary: str = Field(..., alias="summary")
    category: Optional[str] = Field(None, alias="category")
    tags: list[str] = Field(default_factory=list, alias="tags")
    repository: str = Field(..., alias="repository")
    dedup_key: str = Field(..., alias="dedupKey")
    dedup_hash: str = Field(..., alias="dedupHash")
    review_priority: TaskProposalReviewPriority = Field(
        TaskProposalReviewPriority.NORMAL, alias="reviewPriority"
    )
    proposed_by_worker_id: Optional[str] = Field(None, alias="proposedByWorkerId")
    proposed_by_user_id: Optional[UUID] = Field(None, alias="proposedByUserId")
    promoted_job_id: Optional[UUID] = Field(None, alias="promotedJobId")
    promoted_at: Optional[datetime] = Field(None, alias="promotedAt")
    promoted_by_user_id: Optional[UUID] = Field(None, alias="promotedByUserId")
    decided_by_user_id: Optional[UUID] = Field(None, alias="decidedByUserId")
    decision_note: Optional[str] = Field(None, alias="decisionNote")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    origin: TaskProposalOriginModel = Field(..., alias="origin")
    task_create_request: dict[str, Any] = Field(..., alias="taskCreateRequest")
    task_preview: Optional[TaskProposalTaskPreview] = Field(
        None, alias="taskPreview"
    )
    snoozed_until: Optional[datetime] = Field(None, alias="snoozedUntil")
    snoozed_by_user_id: Optional[UUID] = Field(None, alias="snoozedByUserId")
    snooze_note: Optional[str] = Field(None, alias="snoozeNote")
    snooze_history: list[dict[str, Any]] = Field(
        default_factory=list, alias="snoozeHistory"
    )
    similar: list["TaskProposalSimilarModel"] = Field(
        default_factory=list, alias="similar"
    )


class TaskProposalSimilarModel(BaseModel):
    """Slim representation of similar proposals."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(..., alias="id")
    title: str = Field(..., alias="title")
    category: Optional[str] = Field(None, alias="category")
    repository: str = Field(..., alias="repository")
    created_at: datetime = Field(..., alias="createdAt")


class TaskProposalCreateRequest(BaseModel):
    """Request payload for creating a task proposal."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., alias="title")
    summary: str = Field(..., alias="summary")
    category: Optional[str] = Field(None, alias="category")
    tags: Optional[list[str]] = Field(None, alias="tags")
    origin: TaskProposalOriginModel = Field(..., alias="origin")
    task_create_request: CreateJobRequest = Field(..., alias="taskCreateRequest")


class TaskProposalListResponse(BaseModel):
    """Response payload for listing proposals."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[TaskProposalModel] = Field(default_factory=list, alias="items")
    next_cursor: Optional[str] = Field(None, alias="nextCursor")


class TaskProposalPromoteRequest(BaseModel):
    """Optional overrides supplied during promotion."""

    model_config = ConfigDict(populate_by_name=True)

    priority: Optional[int] = Field(None, alias="priority")
    max_attempts: Optional[int] = Field(None, alias="maxAttempts")
    note: Optional[str] = Field(None, alias="note")
    task_create_request_override: Optional[CreateJobRequest] = Field(
        None, alias="taskCreateRequestOverride"
    )


class TaskProposalDismissRequest(BaseModel):
    """Dismissal note payload."""

    model_config = ConfigDict(populate_by_name=True)

    note: Optional[str] = Field(None, alias="note")


class TaskProposalPromoteResponse(BaseModel):
    """Promotion response containing proposal + queue job."""

    model_config = ConfigDict(populate_by_name=True)

    proposal: TaskProposalModel = Field(..., alias="proposal")
    job: JobModel = Field(..., alias="job")


class TaskProposalPriorityRequest(BaseModel):
    """Reviewer priority update request."""

    model_config = ConfigDict(populate_by_name=True)

    priority: str = Field(..., alias="priority")


class TaskProposalSnoozeRequest(BaseModel):
    """Snooze payload."""

    model_config = ConfigDict(populate_by_name=True)

    until: datetime = Field(..., alias="until")
    note: Optional[str] = Field(None, alias="note")
