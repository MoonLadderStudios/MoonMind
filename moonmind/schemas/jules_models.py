"""Pydantic schemas for Jules API requests and responses."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class JulesCreateTaskRequest(BaseModel):
    """Request payload for creating a Jules task."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., alias="title")
    description: str = Field(..., alias="description")
    metadata: Optional[dict[str, Any]] = Field(None, alias="metadata")


class JulesResolveTaskRequest(BaseModel):
    """Request payload for finishing a Jules task."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(..., alias="taskId")
    resolution_notes: str = Field(..., alias="resolutionNotes")
    status: str = Field("completed", alias="status")


class JulesGetTaskRequest(BaseModel):
    """Request payload for retrieving a Jules task."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(..., alias="taskId")


class JulesTaskResponse(BaseModel):
    """Response payload for Jules task operations."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    task_id: str = Field(..., alias="taskId")
    status: str = Field(..., alias="status")
    url: Optional[str] = Field(None, alias="url")


__all__ = [
    "JulesCreateTaskRequest",
    "JulesResolveTaskRequest",
    "JulesGetTaskRequest",
    "JulesTaskResponse",
]
