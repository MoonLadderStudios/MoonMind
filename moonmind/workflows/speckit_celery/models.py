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

_MUTABLE_JSON = MutableDict.as_mutable(JSON().with_variant(JSONB, "postgresql"))
_TASK_PAYLOAD_TYPE = _MUTABLE_JSON


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


class SpecAutomationRunStatus(str, enum.Enum):
    """Lifecycle states for Spec Automation runs."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NO_CHANGES = "no_changes"


class SpecAutomationPhase(str, enum.Enum):
    """Phases executed during the Spec Automation pipeline."""

    PREPARE_JOB = "prepare_job"
    START_JOB_CONTAINER = "start_job_container"
    GIT_CLONE = "git_clone"
    SPECKIT_SPECIFY = "speckit_specify"
    SPECKIT_PLAN = "speckit_plan"
    SPECKIT_TASKS = "speckit_tasks"
    COMMIT_PUSH = "commit_push"
    OPEN_PR = "open_pr"
    CLEANUP = "cleanup"


class SpecAutomationTaskStatus(str, enum.Enum):
    """Per-phase task status values for Spec Automation."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class SpecAutomationArtifactType(str, enum.Enum):
    """Artifact classifications produced by Spec Automation."""

    STDOUT_LOG = "stdout_log"
    STDERR_LOG = "stderr_log"
    DIFF_SUMMARY = "diff_summary"
    COMMIT_STATUS = "commit_status"
    METRICS_SNAPSHOT = "metrics_snapshot"
    ENVIRONMENT_INFO = "environment_info"


class SpecAutomationRun(Base):
    """Represents a Spec Kit automation execution."""

    __tablename__ = "spec_automation_runs"
    __table_args__ = (
        Index("ix_spec_automation_runs_status", "status"),
        Index("ix_spec_automation_runs_repository", "repository"),
        Index("ix_spec_automation_runs_created_at", "created_at"),
        Index("ix_spec_automation_runs_external_ref", "external_ref"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(255))
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(
        String(128), nullable=False, default="main"
    )
    branch_name: Mapped[Optional[str]] = mapped_column(String(255))
    pull_request_url: Mapped[Optional[str]] = mapped_column(String(512))
    status: Mapped[SpecAutomationRunStatus] = mapped_column(
        Enum(
            SpecAutomationRunStatus,
            name="specautomationrunstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SpecAutomationRunStatus.QUEUED,
    )
    result_summary: Mapped[Optional[str]] = mapped_column(Text)
    requested_spec_input: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    worker_hostname: Mapped[Optional[str]] = mapped_column(String(255))
    job_container_id: Mapped[Optional[str]] = mapped_column(String(255))
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

    task_states: Mapped[list["SpecAutomationTaskState"]] = relationship(
        "SpecAutomationTaskState",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="SpecAutomationTaskState.created_at",
    )
    artifacts: Mapped[list["SpecAutomationArtifact"]] = relationship(
        "SpecAutomationArtifact",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="SpecAutomationArtifact.created_at",
    )
    agent_configuration: Mapped[Optional["SpecAutomationAgentConfiguration"]] = (
        relationship(
            "SpecAutomationAgentConfiguration",
            back_populates="run",
            cascade="all, delete-orphan",
            single_parent=True,
            uselist=False,
        )
    )


class SpecAutomationTaskState(Base):
    """State captured for each automation phase attempt."""

    __tablename__ = "spec_automation_task_states"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "phase",
            "attempt",
            name="uq_spec_automation_task_state_attempt",
        ),
        Index("ix_spec_automation_task_states_run_id", "run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("spec_automation_runs.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[SpecAutomationPhase] = mapped_column(
        Enum(
            SpecAutomationPhase,
            name="specautomationphase",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    status: Mapped[SpecAutomationTaskStatus] = mapped_column(
        Enum(
            SpecAutomationTaskStatus,
            name="specautomationtaskstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stdout_path: Mapped[Optional[str]] = mapped_column(String(1024))
    stderr_path: Mapped[Optional[str]] = mapped_column(String(1024))
    metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(_MUTABLE_JSON)
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

    run: Mapped[SpecAutomationRun] = relationship(
        "SpecAutomationRun", back_populates="task_states"
    )
    artifacts: Mapped[list["SpecAutomationArtifact"]] = relationship(
        "SpecAutomationArtifact",
        back_populates="task_state",
    )


class SpecAutomationArtifact(Base):
    """Artifacts emitted during automation (logs, diffs, metrics snapshots)."""

    __tablename__ = "spec_automation_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "artifact_type",
            "storage_path",
            name="uq_spec_automation_artifact_path",
        ),
        Index("ix_spec_automation_artifacts_run_id", "run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("spec_automation_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_state_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid,
        ForeignKey("spec_automation_task_states.id", ondelete="SET NULL"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[SpecAutomationArtifactType] = mapped_column(
        Enum(
            SpecAutomationArtifactType,
            name="specautomationartifacttype",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(128))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_phase: Mapped[Optional[SpecAutomationPhase]] = mapped_column(
        Enum(
            SpecAutomationPhase,
            name="specautomationphase",
            native_enum=True,
            validate_strings=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    run: Mapped[SpecAutomationRun] = relationship(
        "SpecAutomationRun", back_populates="artifacts"
    )
    task_state: Mapped[Optional[SpecAutomationTaskState]] = relationship(
        "SpecAutomationTaskState", back_populates="artifacts"
    )


class SpecAutomationAgentConfiguration(Base):
    """Snapshot of the agent configuration used for a run."""

    __tablename__ = "spec_automation_agent_configs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_spec_automation_agent_config_run"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("spec_automation_runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_backend: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_pack_version: Mapped[Optional[str]] = mapped_column(String(128))
    runtime_env: Mapped[Optional[dict[str, Any]]] = mapped_column(_MUTABLE_JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    run: Mapped[SpecAutomationRun] = relationship(
        "SpecAutomationRun", back_populates="agent_configuration"
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
    "SpecAutomationRun",
    "SpecAutomationRunStatus",
    "SpecAutomationPhase",
    "SpecAutomationTaskState",
    "SpecAutomationTaskStatus",
    "SpecAutomationArtifact",
    "SpecAutomationArtifactType",
    "SpecAutomationAgentConfiguration",
]
