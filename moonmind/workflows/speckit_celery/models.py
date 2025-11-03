"""SQLAlchemy models for Spec Kit Celery workflow persistence."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api_service.db.models import Base

_TASK_PAYLOAD_TYPE = MutableDict.as_mutable(JSON().with_variant(JSONB, "postgresql"))


class SpecWorkflowRunStatus(str, enum.Enum):
    """Lifecycle states for a workflow run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SpecWorkflowRunPhase(str, enum.Enum):
    """Logical phase executed by the workflow chain."""

    DISCOVER = "discover"
    SUBMIT = "submit"
    APPLY = "apply"
    PUBLISH = "publish"
    COMPLETE = "complete"


class SpecWorkflowTaskStatus(str, enum.Enum):
    """Per-task execution states."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class CodexCredentialStatus(str, enum.Enum):
    """Credential states for Codex validation."""

    VALID = "valid"
    INVALID = "invalid"
    EXPIRES_SOON = "expires_soon"


class GitHubCredentialStatus(str, enum.Enum):
    """Credential states for GitHub validation."""

    VALID = "valid"
    INVALID = "invalid"
    SCOPE_MISSING = "scope_missing"


@dataclass(slots=True)
class CredentialAuditResult:
    """Represents the outcome of a credential validation attempt."""

    codex_status: "CodexCredentialStatus"
    github_status: "GitHubCredentialStatus"
    notes: Optional[str] = None

    def is_valid(self) -> bool:
        """Return ``True`` when both Codex and GitHub credentials are valid."""

        return (
            self.codex_status is CodexCredentialStatus.VALID
            and self.github_status is GitHubCredentialStatus.VALID
        )


class WorkflowArtifactType(str, enum.Enum):
    """Supported artifact classifications."""

    CODEX_LOGS = "codex_logs"
    CODEX_PATCH = "codex_patch"
    GH_PUSH_LOG = "gh_push_log"
    GH_PR_RESPONSE = "gh_pr_response"


class SpecWorkflowRun(Base):
    """Persisted record for a full Spec Kit workflow execution."""

    __tablename__ = "spec_workflow_runs"
    __table_args__ = (
        Index("ix_spec_workflow_runs_feature_key", "feature_key"),
        Index("ix_spec_workflow_runs_status", "status"),
        Index("ix_spec_workflow_runs_created_by", "created_by"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(255), nullable=False)
    celery_chain_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[SpecWorkflowRunStatus] = mapped_column(
        Enum(
            SpecWorkflowRunStatus,
            name="specworkflowrunstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SpecWorkflowRunStatus.PENDING,
    )
    phase: Mapped[SpecWorkflowRunPhase] = mapped_column(
        Enum(
            SpecWorkflowRunPhase,
            name="specworkflowrunphase",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SpecWorkflowRunPhase.DISCOVER,
    )
    branch_name: Mapped[Optional[str]] = mapped_column(String(255))
    pr_url: Mapped[Optional[str]] = mapped_column(String(512))
    codex_task_id: Mapped[Optional[str]] = mapped_column(String(255))
    codex_logs_path: Mapped[Optional[str]] = mapped_column(String(1024))
    codex_patch_path: Mapped[Optional[str]] = mapped_column(String(1024))
    artifacts_path: Mapped[Optional[str]] = mapped_column(String(512))
    created_by: Mapped[Optional[UUID]] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )

    task_states: Mapped[list["SpecWorkflowTaskState"]] = relationship(
        "SpecWorkflowTaskState",
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        order_by="SpecWorkflowTaskState.created_at",
    )
    artifacts: Mapped[list["WorkflowArtifact"]] = relationship(
        "WorkflowArtifact",
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        order_by="WorkflowArtifact.created_at",
    )
    credential_audit: Mapped[Optional["WorkflowCredentialAudit"]] = relationship(
        "WorkflowCredentialAudit",
        back_populates="workflow_run",
        uselist=False,
        cascade="all, delete-orphan",
    )


class SpecWorkflowTaskState(Base):
    """Individual Celery task execution state for a workflow run."""

    __tablename__ = "spec_workflow_task_states"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "task_name",
            "attempt",
            name="uq_spec_workflow_task_state_attempt",
        ),
        Index("ix_spec_workflow_task_states_run_id", "workflow_run_id"),
        Index(
            "ix_spec_workflow_task_states_failed",
            "workflow_run_id",
            postgresql_where=text("status = 'failed'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[SpecWorkflowTaskStatus] = mapped_column(
        Enum(
            SpecWorkflowTaskStatus,
            name="specworkflowtaskstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        _TASK_PAYLOAD_TYPE, nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )

    workflow_run: Mapped[SpecWorkflowRun] = relationship(
        "SpecWorkflowRun", back_populates="task_states"
    )


class WorkflowCredentialAudit(Base):
    """Credential verification results associated with a workflow run."""

    __tablename__ = "workflow_credential_audits"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", name="uq_workflow_credential_audit_run"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    codex_status: Mapped[CodexCredentialStatus] = mapped_column(
        Enum(
            CodexCredentialStatus,
            name="workflowcodexcredentialstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    github_status: Mapped[GitHubCredentialStatus] = mapped_column(
        Enum(
            GitHubCredentialStatus,
            name="workflowgithubcredentialstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)

    workflow_run: Mapped[SpecWorkflowRun] = relationship(
        "SpecWorkflowRun", back_populates="credential_audit"
    )


class WorkflowArtifact(Base):
    """Artifact generated by the workflow chain (logs, patches, PR responses)."""

    __tablename__ = "workflow_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "artifact_type", "path", name="uq_workflow_artifact_path"
        ),
        Index("ix_workflow_artifacts_run_id", "workflow_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[WorkflowArtifactType] = mapped_column(
        Enum(
            WorkflowArtifactType,
            name="workflowartifacttype",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    workflow_run: Mapped[SpecWorkflowRun] = relationship(
        "SpecWorkflowRun", back_populates="artifacts"
    )


__all__ = [
    "SpecWorkflowRun",
    "SpecWorkflowRunStatus",
    "SpecWorkflowRunPhase",
    "SpecWorkflowTaskState",
    "SpecWorkflowTaskStatus",
    "WorkflowCredentialAudit",
    "CodexCredentialStatus",
    "GitHubCredentialStatus",
    "WorkflowArtifact",
    "WorkflowArtifactType",
    "CredentialAuditResult",
]
