"""Pydantic schemas for the Spec Kit workflow API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from moonmind.workflows.speckit_celery import models


class WorkflowTaskStateModel(BaseModel):
    """Schema for individual Celery task states."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(None, alias="id")
    task_name: str = Field(..., alias="taskName")
    status: models.SpecWorkflowTaskStatus = Field(..., alias="status")
    attempt: int = Field(..., alias="attempt")
    payload: dict[str, Any] | None = Field(None, alias="payload")
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    created_at: datetime | None = Field(None, alias="createdAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")


class WorkflowTaskSummaryModel(BaseModel):
    """Schema capturing the latest state per workflow task."""

    model_config = ConfigDict(populate_by_name=True)

    task_name: str = Field(..., alias="taskName")
    status: models.SpecWorkflowTaskStatus = Field(..., alias="status")
    attempt: int = Field(..., alias="attempt")
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")


class WorkflowArtifactModel(BaseModel):
    """Schema for stored workflow artifacts."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(None, alias="id")
    artifact_type: models.WorkflowArtifactType = Field(..., alias="artifactType")
    path: str = Field(..., alias="path")
    created_at: datetime | None = Field(None, alias="createdAt")


class WorkflowCredentialAuditModel(BaseModel):
    """Schema describing credential validation results."""

    model_config = ConfigDict(populate_by_name=True)

    codex_status: models.CodexCredentialStatus = Field(..., alias="codexStatus")
    github_status: models.GitHubCredentialStatus = Field(..., alias="githubStatus")
    checked_at: datetime | None = Field(None, alias="checkedAt")
    notes: Optional[str] = Field(None, alias="notes")


class SpecWorkflowRunModel(BaseModel):
    """Full representation of a workflow run exposed via the API."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(..., alias="id")
    feature_key: str = Field(..., alias="featureKey")
    status: models.SpecWorkflowRunStatus = Field(..., alias="status")
    phase: models.SpecWorkflowRunPhase = Field(..., alias="phase")
    branch_name: Optional[str] = Field(None, alias="branchName")
    pr_url: Optional[str] = Field(None, alias="prUrl")
    codex_task_id: Optional[str] = Field(None, alias="codexTaskId")
    codex_logs_path: Optional[str] = Field(None, alias="codexLogsPath")
    codex_patch_path: Optional[str] = Field(None, alias="codexPatchPath")
    celery_chain_id: Optional[str] = Field(None, alias="celeryChainId")
    created_by: Optional[UUID] = Field(None, alias="createdBy")
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    artifacts_path: Optional[str] = Field(None, alias="artifactsPath")
    created_at: datetime | None = Field(None, alias="createdAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")
    tasks: list[WorkflowTaskStateModel] = Field(default_factory=list, alias="tasks")
    task_summary: list[WorkflowTaskSummaryModel] = Field(
        default_factory=list, alias="taskSummary"
    )
    artifacts: list[WorkflowArtifactModel] = Field(
        default_factory=list, alias="artifacts"
    )
    credential_audit: WorkflowCredentialAuditModel | None = Field(
        None, alias="credentialAudit"
    )


class CreateWorkflowRunRequest(BaseModel):
    """Request payload for triggering a workflow run."""

    model_config = ConfigDict(populate_by_name=True)

    feature_key: Optional[str] = Field(None, alias="featureKey")
    force_phase: Optional[Literal["discover", "submit", "apply", "publish"]] = Field(
        None, alias="forcePhase"
    )
    created_by: Optional[UUID] = Field(None, alias="createdBy")


class WorkflowRunCollectionResponse(BaseModel):
    """Envelope returned when listing workflow runs."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[SpecWorkflowRunModel] = Field(default_factory=list, alias="items")
    next_cursor: Optional[str] = Field(None, alias="nextCursor")


class RetryWorkflowRunRequest(BaseModel):
    """Request payload for retrying a failed workflow run."""

    model_config = ConfigDict(populate_by_name=True)

    notes: Optional[str] = Field(None, alias="notes", max_length=1024)


__all__ = [
    "SpecWorkflowRunModel",
    "WorkflowTaskStateModel",
    "WorkflowTaskSummaryModel",
    "WorkflowArtifactModel",
    "WorkflowCredentialAuditModel",
    "CreateWorkflowRunRequest",
    "WorkflowRunCollectionResponse",
    "RetryWorkflowRunRequest",
]
