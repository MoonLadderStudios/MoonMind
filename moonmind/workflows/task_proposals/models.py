"""SQLAlchemy models for task proposal queue entities."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from api_service.db.models import Base, mutable_json_dict, mutable_json_list


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Return enum labels for SQLAlchemy Enum definitions."""

    return [member.value for member in enum_cls]


class TaskProposalStatus(str, enum.Enum):
    """Lifecycle states for task proposals."""

    OPEN = "open"
    PROMOTED = "promoted"
    DISMISSED = "dismissed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TaskProposalOriginSource(str, enum.Enum):
    """Accepted proposal origin sources for auditing."""

    QUEUE = "queue"
    ORCHESTRATOR = "orchestrator"
    WORKFLOW = "workflow"
    MANUAL = "manual"


class TaskProposalReviewPriority(str, enum.Enum):
    """Reviewer-defined triage priority."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskProposal(Base):
    """Control-plane record representing a follow-up task proposal."""

    __tablename__ = "task_proposals"
    __table_args__ = (
        Index("ix_task_proposals_status_created", "status", "created_at"),
        Index("ix_task_proposals_origin", "origin_source", "origin_id"),
        Index("ix_task_proposals_repository", "repository"),
        Index("ix_task_proposals_dedup_hash_status", "dedup_hash", "status"),
        Index("ix_task_proposals_priority_created", "review_priority", "created_at"),
        Index(
            "ix_task_proposals_snoozed_until",
            "snoozed_until",
            postgresql_where=text("snoozed_until IS NOT NULL"),
        ),
        UniqueConstraint("promoted_job_id", name="uq_task_proposals_promoted_job_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    status: Mapped[TaskProposalStatus] = mapped_column(
        Enum(
            TaskProposalStatus,
            name="taskproposalstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskProposalStatus.OPEN,
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
    review_priority: Mapped[TaskProposalReviewPriority] = mapped_column(
        Enum(
            TaskProposalReviewPriority,
            name="taskproposalpriority",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskProposalReviewPriority.NORMAL,
    )
    task_create_request: Mapped[dict[str, object]] = mapped_column(
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
    origin_source: Mapped[TaskProposalOriginSource] = mapped_column(
        Enum(
            TaskProposalOriginSource,
            name="taskproposaloriginsource",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskProposalOriginSource.MANUAL,
    )
    origin_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    origin_metadata: Mapped[dict[str, object]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    promoted_job_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("agent_jobs.id", ondelete="SET NULL"),
        nullable=True,
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
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    snoozed_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    snooze_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    snooze_history: Mapped[list[dict[str, object]]] = mapped_column(
        mutable_json_list(), nullable=False, default=list
    )
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


class TaskProposalNotification(Base):
    """Audit log for high-signal proposal notification delivery."""

    __tablename__ = "task_proposal_notifications"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id",
            "target",
            name="uq_task_proposal_notifications_proposal_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("task_proposals.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "TaskProposal",
    "TaskProposalStatus",
    "TaskProposalOriginSource",
    "TaskProposalReviewPriority",
    "TaskProposalNotification",
]
