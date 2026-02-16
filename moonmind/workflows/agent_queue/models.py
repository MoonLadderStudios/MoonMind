"""SQLAlchemy models for the Agent Queue MVP."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from api_service.db.models import Base, mutable_json_dict, mutable_json_list


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Return enum values so SQLAlchemy persists lowercase labels, not names."""

    return [member.value for member in enum_cls]


class AgentJobStatus(str, enum.Enum):
    """Lifecycle states for queue jobs."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class AgentJobEventLevel(str, enum.Enum):
    """Supported levels for queue event entries."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class AgentJob(Base):
    """Persistent queue row used by producers and workers."""

    __tablename__ = "agent_jobs"
    __table_args__ = (
        Index(
            "ix_agent_jobs_status_priority_created_at",
            "status",
            "priority",
            "created_at",
        ),
        Index("ix_agent_jobs_type_status_created_at", "type", "status", "created_at"),
        Index("ix_agent_jobs_lease_expires_at", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AgentJobStatus] = mapped_column(
        Enum(
            AgentJobStatus,
            name="agentjobstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=AgentJobStatus.QUEUED,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    affinity_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifacts_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    artifacts: Mapped[list["AgentJobArtifact"]] = relationship(
        "AgentJobArtifact",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["AgentJobEvent"]] = relationship(
        "AgentJobEvent",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class AgentJobArtifact(Base):
    """Metadata row describing one uploaded queue artifact."""

    __tablename__ = "agent_job_artifacts"
    __table_args__ = (
        Index("ix_agent_job_artifacts_job_id_created_at", "job_id", "created_at"),
        Index("ix_agent_job_artifacts_job_id_name", "job_id", "name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    digest: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
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

    job: Mapped[AgentJob] = relationship("AgentJob", back_populates="artifacts")


class AgentJobEvent(Base):
    """Append-only event row for queue job lifecycle updates."""

    __tablename__ = "agent_job_events"
    __table_args__ = (
        Index("ix_agent_job_events_job_id_created_at", "job_id", "created_at"),
        Index("ix_agent_job_events_level_created_at", "level", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    level: Mapped[AgentJobEventLevel] = mapped_column(
        Enum(
            AgentJobEventLevel,
            name="agentjobeventlevel",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=AgentJobEventLevel.INFO,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(),
        nullable=True,
        default=None,
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

    job: Mapped[AgentJob] = relationship("AgentJob", back_populates="events")


class AgentWorkerToken(Base):
    """Stored worker token metadata used for policy enforcement."""

    __tablename__ = "agent_worker_tokens"
    __table_args__ = (
        Index("ix_agent_worker_tokens_worker_id", "worker_id"),
        Index("ix_agent_worker_tokens_is_active", "is_active"),
        Index("ix_agent_worker_tokens_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    allowed_repositories: Mapped[Optional[list[str]]] = mapped_column(
        mutable_json_list(),
        nullable=True,
        default=None,
    )
    allowed_job_types: Mapped[Optional[list[str]]] = mapped_column(
        mutable_json_list(),
        nullable=True,
        default=None,
    )
    capabilities: Mapped[Optional[list[str]]] = mapped_column(
        mutable_json_list(),
        nullable=True,
        default=None,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
