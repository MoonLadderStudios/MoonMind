"""Pydantic models for Temporal artifact API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from moonmind.core.artifacts import (
    TemporalArtifactStorageBackend,
    TemporalArtifactEncryption,
    TemporalArtifactStatus,
    TemporalArtifactRetentionClass,
    TemporalArtifactRedactionLevel,
    TemporalArtifactUploadMode,
    assert_model_agnostic_metadata,
)
from moonmind.schemas._validation import require_non_blank

class ArtifactRefModel(BaseModel):
    """JSON-serializable artifact reference passed through workflows."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_ref_v: int = Field(1, alias="artifact_ref_v")
    artifact_id: str = Field(..., alias="artifact_id")
    sha256: Optional[str] = Field(None, alias="sha256")
    size_bytes: Optional[int] = Field(None, alias="size_bytes")
    content_type: Optional[str] = Field(None, alias="content_type")
    encryption: str = Field(..., alias="encryption")
    diagnostics: Optional[dict[str, Any]] = None

class CompactArtifactRefModel(BaseModel):
    """Bounded artifact reference for execution-level convenience projections."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_ref_v: int = Field(1, alias="artifact_ref_v")
    artifact_id: str = Field(..., alias="artifact_id")

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

    def model_post_init(self, __context: Any) -> None:
        assert_model_agnostic_metadata(self.metadata, field_name="metadata")

class CreateArtifactResponse(BaseModel):
    """Creation response payload with artifact ref and upload hints."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_ref: ArtifactRefModel
    upload: ArtifactUploadModel
    diagnostics: Optional[dict[str, Any]] = None

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


class ArtifactCollectionRowModel(BaseModel):
    """Compact, identity-safe row for dashboard evidence collections."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_id: str
    created_at: datetime
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    status: TemporalArtifactStatus
    retention_class: TemporalArtifactRetentionClass
    link_type: Optional[str] = None
    label: Optional[str] = None
    workflow_id: Optional[str] = None
    run_id: Optional[str] = None
    download_url: str


class ArtifactCollectionResponse(BaseModel):
    """Paged compact rows for one independently gated evidence surface."""

    model_config = ConfigDict(populate_by_name=True)

    category: Literal["artifacts", "reports", "observability"]
    items: list[ArtifactCollectionRowModel] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50
    refreshed_at: datetime


class ArtifactSessionGroupModel(BaseModel):
    """Server-defined grouping of workflow-scoped session artifacts."""

    model_config = ConfigDict(populate_by_name=True)

    group_key: str
    title: str
    artifacts: list[ArtifactMetadataModel] = Field(default_factory=list)

class ArtifactSessionProjectionModel(BaseModel):
    """Minimal workflow-scoped session continuity projection."""

    model_config = ConfigDict(populate_by_name=True)

    # legacy_run contract — serialized projection field name; renames to
    # agent-run naming at the MoonMind.UserWorkflow v2 cutover (MM-730).
    agent_run_id: str
    session_id: str
    session_epoch: int
    grouped_artifacts: list[ArtifactSessionGroupModel] = Field(default_factory=list)
    latest_summary_ref: Optional[ArtifactRefModel] = None
    latest_checkpoint_ref: Optional[ArtifactRefModel] = None
    latest_control_event_ref: Optional[ArtifactRefModel] = None
    latest_reset_boundary_ref: Optional[ArtifactRefModel] = None

class SessionResourceModel(BaseModel):
    """Read-only session resource projection over an authorized artifact."""

    model_config = ConfigDict(populate_by_name=True)

    resource_id: str
    artifact_id: str
    group_key: str
    group_title: str
    label: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    status: TemporalArtifactStatus
    artifact_ref: ArtifactRefModel
    default_read_ref: Optional[ArtifactRefModel] = None
    preview_artifact_ref: Optional[ArtifactRefModel] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_url: str
    download_url: str

class SessionResourceListResponse(BaseModel):
    """Session-scoped read-only resource aliases backed by ArtifactRefs."""

    model_config = ConfigDict(populate_by_name=True)

    agent_run_id: str
    session_id: str
    session_epoch: int
    resources: list[SessionResourceModel] = Field(default_factory=list)

class ArtifactSessionControlRequest(BaseModel):
    """Operator control request for one workflow-scoped artifact session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(1, alias="schemaVersion")
    control_request_id: str = Field(..., alias="controlRequestId", min_length=1)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    action: Literal[
        "continue_same_session",
        "clear_session",
        "interrupt_turn",
        "cancel_session",
    ]
    message: str | None = None
    reason: str | None = None
    expected_session_epoch: int = Field(..., alias="expectedSessionEpoch", ge=1)
    expected_turn_id: str | None = Field(None, alias="expectedTurnId")
    actor_principal: str | None = Field(None, alias="actorPrincipal")

    def model_post_init(self, __context: Any) -> None:
        if self.message is not None:
            self.message = require_non_blank(self.message, field_name="message")
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        self.control_request_id = require_non_blank(self.control_request_id, field_name="controlRequestId")
        self.idempotency_key = require_non_blank(self.idempotency_key, field_name="idempotencyKey")
        if self.expected_turn_id is not None:
            self.expected_turn_id = require_non_blank(self.expected_turn_id, field_name="expectedTurnId")
        if self.action == "continue_same_session" and self.message is None:
            raise ValueError("message is required when action=continue_same_session")
        if self.action == "interrupt_turn" and self.expected_turn_id is None:
            raise ValueError("expectedTurnId is required when action=interrupt_turn")
        if self.action in {"clear_session", "cancel_session"} and self.reason is None:
            raise ValueError("reason is required for destructive session controls")

class ArtifactSessionControlResponse(BaseModel):
    """Control response envelope with the refreshed session projection."""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal[
        "continue_same_session",
        "clear_session",
        "interrupt_turn",
        "cancel_session",
    ]
    control_request_id: str = Field(..., alias="controlRequestId")
    status: Literal["accepted", "rejected", "completed", "failed", "delivery_unknown"]
    stable_reason_code: str | None = Field(None, alias="stableReasonCode")
    control_event_ref: str | None = Field(None, alias="controlEventRef")
    completed_at: datetime | None = Field(None, alias="completedAt")
    projection: ArtifactSessionProjectionModel

class PresignDownloadResponse(BaseModel):
    """Presigned-download response payload."""

    model_config = ConfigDict(populate_by_name=True)

    url: str
    expires_at: datetime
