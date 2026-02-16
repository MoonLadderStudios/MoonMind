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

from api_service.db.models import (
    Base,
    CodexCredentialStatus,
    CodexPreflightStatus,
    GitHubCredentialStatus,
    SpecWorkflowRun,
    SpecWorkflowRunPhase,
    SpecWorkflowRunStatus,
    SpecWorkflowTaskState,
    SpecWorkflowTaskStatus,
    WorkflowArtifact,
    WorkflowArtifactType,
    WorkflowCredentialAudit,
)

_MUTABLE_JSON = MutableDict.as_mutable(JSON().with_variant(JSONB, "postgresql"))


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Return enum member values so SQLAlchemy stores lowercase labels."""

    return [member.value for member in enum_cls]


class CodexAuthVolumeStatus(str, enum.Enum):
    """Health states for persisted Codex authentication volumes."""

    READY = "ready"
    NEEDS_AUTH = "needs_auth"
    ERROR = "error"


class CodexWorkerShardStatus(str, enum.Enum):
    """Lifecycle states for Codex-focused Celery workers."""

    ACTIVE = "active"
    DRAINING = "draining"
    OFFLINE = "offline"


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


class CodexAuthVolume(Base):
    """Persistent Codex authentication volume mapped to a worker shard."""

    __tablename__ = "codex_auth_volumes"
    __table_args__ = (
        UniqueConstraint(
            "worker_affinity", name="uq_codex_auth_volumes_worker_affinity"
        ),
    )

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    worker_affinity: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[CodexAuthVolumeStatus] = mapped_column(
        Enum(
            CodexAuthVolumeStatus,
            name="codexauthvolumestatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=CodexAuthVolumeStatus.NEEDS_AUTH,
    )
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
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

    shard: Mapped[Optional["CodexWorkerShard"]] = relationship(
        "CodexWorkerShard", back_populates="volume", uselist=False
    )
    runs: Mapped[list[SpecWorkflowRun]] = relationship(
        SpecWorkflowRun,
        back_populates="codex_auth_volume",
        primaryjoin="CodexAuthVolume.name == SpecWorkflowRun.codex_volume",
    )


class CodexWorkerShard(Base):
    """Celery worker dedicated to Codex tasks and its routing metadata."""

    __tablename__ = "codex_worker_shards"
    __table_args__ = (
        UniqueConstraint("volume_name", name="uq_codex_worker_shards_volume_name"),
    )

    queue_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    volume_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("codex_auth_volumes.name", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[CodexWorkerShardStatus] = mapped_column(
        Enum(
            CodexWorkerShardStatus,
            name="codexworkershardstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=CodexWorkerShardStatus.ACTIVE,
    )
    hash_modulo: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    worker_hostname: Mapped[Optional[str]] = mapped_column(String(255))
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

    volume: Mapped[CodexAuthVolume] = relationship(
        CodexAuthVolume, back_populates="shard", foreign_keys=[volume_name]
    )
    runs: Mapped[list[SpecWorkflowRun]] = relationship(
        SpecWorkflowRun,
        back_populates="codex_shard",
        primaryjoin="CodexWorkerShard.queue_name == SpecWorkflowRun.codex_queue",
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
    SPECIFY = "speckit_specify"
    PLAN = "speckit_plan"
    TASKS = "speckit_tasks"
    ANALYZE = "speckit_analyze"
    IMPLEMENT = "speckit_implement"
    # Backward-compatible aliases for persisted values and legacy clients.
    SPECKIT_SPECIFY = SPECIFY
    SPECKIT_PLAN = PLAN
    SPECKIT_TASKS = TASKS
    SPECKIT_ANALYZE = ANALYZE
    SPECKIT_IMPLEMENT = IMPLEMENT
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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    status: Mapped[SpecAutomationTaskStatus] = mapped_column(
        Enum(
            SpecAutomationTaskStatus,
            name="specautomationtaskstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stdout_path: Mapped[Optional[str]] = mapped_column(String(1024))
    stderr_path: Mapped[Optional[str]] = mapped_column(String(1024))
    metadata_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        "metadata", _MUTABLE_JSON
    )
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

    def get_metadata(self) -> Optional[dict[str, Any]]:
        """Return the persisted task metadata payload."""

        return self.metadata_payload

    def set_metadata(self, value: Optional[dict[str, Any]]) -> None:
        """Assign the task metadata payload."""

        self.metadata_payload = value

    def get_skill_execution_metadata(self) -> Optional[dict[str, Any]]:
        """Return normalized skills execution metadata for this phase attempt."""

        metadata = self.get_metadata()
        if not isinstance(metadata, dict):
            metadata = {}

        selected_skill = metadata.get("selectedSkill")
        execution_path = metadata.get("executionPath")
        used_skills = metadata.get("usedSkills")
        used_fallback = metadata.get("usedFallback")
        shadow_mode_requested = metadata.get("shadowModeRequested")

        if isinstance(selected_skill, str):
            selected_skill = selected_skill.strip() or None
        else:
            selected_skill = None

        if isinstance(execution_path, str):
            execution_path = execution_path.strip() or None
        else:
            execution_path = None

        if selected_skill is None and self.phase.value.startswith("speckit_"):
            selected_skill = "speckit"
        if execution_path is None and selected_skill == "speckit":
            execution_path = "skill"

        def _coerce_bool(value: Any) -> Optional[bool]:
            if isinstance(value, bool):
                return value
            return None

        used_skills_bool = _coerce_bool(used_skills)
        used_fallback_bool = _coerce_bool(used_fallback)
        shadow_mode_bool = _coerce_bool(shadow_mode_requested)

        if used_skills_bool is None and execution_path is not None:
            used_skills_bool = execution_path != "direct_only"
        if used_fallback_bool is None and execution_path is not None:
            used_fallback_bool = execution_path == "direct_fallback"

        if (
            selected_skill is None
            and execution_path is None
            and used_skills_bool is None
            and used_fallback_bool is None
            and shadow_mode_bool is None
        ):
            return None

        return {
            "selectedSkill": selected_skill,
            "executionPath": execution_path,
            "usedSkills": used_skills_bool,
            "usedFallback": used_fallback_bool,
            "shadowModeRequested": shadow_mode_bool,
        }


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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
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
    "CodexPreflightStatus",
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
