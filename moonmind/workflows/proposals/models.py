"""SQLAlchemy models for workflow proposal queue entities."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from api_service.db.models import Base, mutable_json_dict, mutable_json_list

def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Return enum labels for SQLAlchemy Enum definitions."""

    return [member.value for member in enum_cls]

class WorkflowProposalStatus(str, enum.Enum):
    """Lifecycle states for workflow proposals."""

    OPEN = "open"
    PROMOTED = "promoted"
    DISMISSED = "dismissed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

class WorkflowProposalOriginSource(str, enum.Enum):
    """Accepted proposal origin sources for auditing."""

    QUEUE = "queue"
    WORKFLOW = "workflow"
    MANUAL = "manual"

class WorkflowProposalReviewPriority(str, enum.Enum):
    """Reviewer-defined triage priority."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

class WorkflowProposal(Base):
    """Control-plane record representing a follow-up workflow proposal."""

    # legacy_run contract: table/index names rename in WP7 (database migration)
    __tablename__ = "workflow_proposals"
    __table_args__ = (
        Index("ix_workflow_proposals_status_created", "status", "created_at"),
        Index("ix_workflow_proposals_origin", "origin_source", "origin_id"),
        Index("ix_workflow_proposals_repository", "repository"),
        Index("ix_workflow_proposals_dedup_hash_status", "dedup_hash", "status"),
        Index(
            "ix_workflow_proposals_provider_destination_dedup",
            "provider",
            "repository",
            "dedup_hash",
            "status",
        ),
        Index("ix_workflow_proposals_priority_created", "review_priority", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    status: Mapped[WorkflowProposalStatus] = mapped_column(
        Enum(
            WorkflowProposalStatus,
            name="workflowproposalstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=WorkflowProposalStatus.OPEN,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        mutable_json_list(), nullable=False, default=list
    )
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="github")
    external_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # legacy_run contract: DB column name; attribute renames with WP7 column migration
    workflow_snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_metadata: Mapped[dict[str, object]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    resolved_policy: Mapped[dict[str, object]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    review_priority: Mapped[WorkflowProposalReviewPriority] = mapped_column(
        Enum(
            WorkflowProposalReviewPriority,
            name="workflowproposalpriority",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=WorkflowProposalReviewPriority.NORMAL,
    )
    priority_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # legacy_run contract: DB column name; attribute renames with WP7 column migration
    workflow_create_request: Mapped[dict[str, object]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    proposed_by_worker_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    proposed_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    origin_source: Mapped[WorkflowProposalOriginSource] = mapped_column(
        Enum(
            WorkflowProposalOriginSource,
            name="workflowproposaloriginsource",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=WorkflowProposalOriginSource.MANUAL,
    )
    origin_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    origin_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    origin_metadata: Mapped[dict[str, object]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    promoted_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

class WorkflowProposalNotification(Base):
    """Audit log for high-signal proposal notification delivery."""

    # legacy_run contract: table/constraint names rename in WP7 (database migration)
    __tablename__ = "workflow_proposal_notifications"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id",
            "target",
            name="uq_workflow_proposal_notifications_proposal_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(
        # legacy_run contract: FK target table renames in WP7
        Uuid, ForeignKey("workflow_proposals.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

__all__ = [
    "WorkflowProposal",
    "WorkflowProposalStatus",
    "WorkflowProposalOriginSource",
    "WorkflowProposalReviewPriority",
    "WorkflowProposalNotification",
]
