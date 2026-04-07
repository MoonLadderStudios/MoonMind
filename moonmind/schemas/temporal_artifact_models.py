"""Pydantic models for Temporal artifact API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from api_service.db import models as db_models
from moonmind.core.artifacts import (
    TemporalArtifactStorageBackend,
    TemporalArtifactEncryption,
    TemporalArtifactStatus,
    TemporalArtifactRetentionClass,
    TemporalArtifactRedactionLevel,
    TemporalArtifactUploadMode,
)


class ArtifactRefModel(BaseModel):
    """JSON-serializable artifact reference passed through workflows."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_ref_v: int = Field(1, alias="artifact_ref_v")
    artifact_id: str = Field(..., alias="artifact_id")
    sha256: Optional[str] = Field(None, alias="sha256")
    size_bytes: Optional[int] = Field(None, alias="size_bytes")
    content_type: Optional[str] = Field(None, alias="content_type")
    encryption: str = Field(..., alias="encryption")


class ArtifactExecutionLinkModel(BaseModel):
    """Execution linkage metadata associated with an artifact."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    workflow_id: str
    run_id: str
    link_type: str
    label: Optional[str] = None
    created_at: datetime
    created_by_activity_type: Optional[str] = None
    created_by_worker: Optional[str] = None


class ArtifactUploadModel(BaseModel):
    """Upload descriptor for local-dev upload completion."""

    model_config = ConfigDict(populate_by_name=True)

    mode: str
    upload_url: Optional[str] = None
    upload_id: Optional[str] = None
    expires_at: datetime
    max_size_bytes: int
    required_headers: dict[str, str] = Field(default_factory=dict)


class ArtifactCreateExecutionLinkRequest(BaseModel):
    """Optional initial execution linkage for artifact creation."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    workflow_id: str
    run_id: str
    link_type: str
    label: Optional[str] = None
    created_by_activity_type: Optional[str] = None
    created_by_worker: Optional[str] = None


class CreateArtifactRequest(BaseModel):
    """Create a pending artifact and return upload details."""

    model_config = ConfigDict(populate_by_name=True)

    content_type: Optional[str] = None
    size_bytes: Optional[int] = Field(None, ge=0)
    sha256: Optional[str] = None
    retention_class: Optional[TemporalArtifactRetentionClass] = None
    link: Optional[ArtifactCreateExecutionLinkRequest] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    redaction_level: TemporalArtifactRedactionLevel = Field(
        TemporalArtifactRedactionLevel.NONE
    )
    encryption: TemporalArtifactEncryption = Field(
        TemporalArtifactEncryption.NONE
    )


class CreateArtifactResponse(BaseModel):
    """Creation response payload with artifact ref and upload hints."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_ref: ArtifactRefModel
    upload: ArtifactUploadModel


class CompleteArtifactPartModel(BaseModel):
    """Multipart completion part descriptor (reserved for compatibility)."""

    model_config = ConfigDict(populate_by_name=True)

    part_number: int
    etag: str


class PresignUploadPartRequest(BaseModel):
    """Request payload for multipart part presign."""

    model_config = ConfigDict(populate_by_name=True)

    part_number: int = Field(..., ge=1)


class PresignUploadPartResponse(BaseModel):
    """Response payload for multipart part presign."""

    model_config = ConfigDict(populate_by_name=True)

    part_number: int
    url: str
    expires_at: datetime
    required_headers: dict[str, str] = Field(default_factory=dict)


class CompleteArtifactRequest(BaseModel):
    """Request body for multipart completion compatibility."""

    model_config = ConfigDict(populate_by_name=True)

    parts: list[CompleteArtifactPartModel] = Field(default_factory=list)


class LinkArtifactRequest(BaseModel):
    """Link an existing artifact to one execution reference."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    workflow_id: str
    run_id: str
    link_type: str
    label: Optional[str] = None
    created_by_activity_type: Optional[str] = None
    created_by_worker: Optional[str] = None


class PinArtifactRequest(BaseModel):
    """Request body for explicit artifact pinning."""

    model_config = ConfigDict(populate_by_name=True)

    reason: Optional[str] = None


class ArtifactMetadataModel(BaseModel):
    """Expanded metadata model returned by artifact metadata endpoints."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_id: str
    created_at: datetime
    created_by_principal: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    storage_backend: TemporalArtifactStorageBackend
    storage_key: str
    encryption: TemporalArtifactEncryption
    status: TemporalArtifactStatus
    retention_class: TemporalArtifactRetentionClass
    expires_at: Optional[datetime] = None
    redaction_level: TemporalArtifactRedactionLevel
    metadata: dict[str, Any] = Field(default_factory=dict)
    links: list[ArtifactExecutionLinkModel] = Field(default_factory=list)
    pinned: bool = False
    artifact_ref: ArtifactRefModel
    preview_artifact_ref: Optional[ArtifactRefModel] = None
    raw_access_allowed: bool = True
    default_read_ref: Optional[ArtifactRefModel] = None
    download_url: Optional[str] = None
    download_expires_at: Optional[datetime] = None


class ArtifactListResponse(BaseModel):
    """List response envelope for execution-scoped artifact queries."""

    model_config = ConfigDict(populate_by_name=True)

    artifacts: list[ArtifactMetadataModel] = Field(default_factory=list)


class ArtifactSessionGroupModel(BaseModel):
    """Server-defined grouping of task-scoped session artifacts."""

    model_config = ConfigDict(populate_by_name=True)

    group_key: str
    title: str
    artifacts: list[ArtifactMetadataModel] = Field(default_factory=list)


class ArtifactSessionProjectionModel(BaseModel):
    """Minimal task-scoped session continuity projection."""

    model_config = ConfigDict(populate_by_name=True)

    task_run_id: str
    session_id: str
    session_epoch: int
    grouped_artifacts: list[ArtifactSessionGroupModel] = Field(default_factory=list)
    latest_summary_ref: Optional[ArtifactRefModel] = None
    latest_checkpoint_ref: Optional[ArtifactRefModel] = None
    latest_control_event_ref: Optional[ArtifactRefModel] = None
    latest_reset_boundary_ref: Optional[ArtifactRefModel] = None


class PresignDownloadResponse(BaseModel):
    """Presigned-download response payload."""

    model_config = ConfigDict(populate_by_name=True)

    url: str
    expires_at: datetime
