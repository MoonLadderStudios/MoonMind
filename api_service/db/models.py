"""Database models used by the MoonMind API service."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from moonmind.workflows.speckit_celery.models import (
        CodexAuthVolume,
        CodexWorkerShard,
        SpecWorkflowTaskState,
    )
else:  # pragma: no cover - runtime fallbacks avoid circular imports
    CodexAuthVolume = Any  # type: ignore[assignment]
    CodexWorkerShard = Any  # type: ignore[assignment]

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    validates,
)
from sqlalchemy.types import TypeDecorator
from sqlalchemy_utils import EncryptedType

from api_service.core.encryption import (  # Added import for get_encryption_key
    get_encryption_key,
)


class Base(DeclarativeBase):
    pass


# Note: fastapi-users[sqlalchemy] uses GUID/UUID by default for id.
# If you need an Integer ID, you would use SQLAlchemyBaseUserTable[int]
# and ensure your UserManager and FastAPIUsers instances are typed accordingly.
# For this implementation, we'll stick to UUIDs as it's more common with fastapi-users.
class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "user"
    # id is inherited from SQLAlchemyBaseUserTableUUID and is a UUID type
    # email is inherited
    # hashed_password is inherited
    # is_active is inherited
    # is_superuser is inherited
    # is_verified is inherited

    hashed_password = Column(Text, nullable=True)  # Made nullable
    oidc_provider = Column(String(32), index=True, nullable=True)
    oidc_subject = Column(String(255), index=True, nullable=True)

    __table_args__ = (
        UniqueConstraint("oidc_provider", "oidc_subject", name="uq_oidc_identity"),
    )
    user_profile = relationship(
        "UserProfile", back_populates="user", uselist=False
    )  # Added relationship to UserProfile


class UserProfile(Base):
    __tablename__ = "user_profile"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Uuid, ForeignKey("user.id"), unique=True, nullable=False
    )  # Changed to Uuid

    # Example profile field
    google_api_key_encrypted = Column(
        EncryptedType(Text, get_encryption_key), nullable=True
    )
    openai_api_key_encrypted = Column(
        EncryptedType(Text, get_encryption_key), nullable=True
    )
    github_token_encrypted = Column(
        EncryptedType(Text, get_encryption_key), nullable=True
    )
    # Add other provider keys here as needed

    user = relationship("User", back_populates="user_profile")


class ManifestRecord(Base):
    __tablename__ = "manifest"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    last_indexed_at = Column(DateTime(timezone=True), nullable=True)


__all__ = [
    "Base",
    "User",
    "UserProfile",
    "ManifestRecord",
    "ApprovalGate",
    "OrchestratorActionPlan",
    "OrchestratorRun",
    "OrchestratorRunArtifact",
    "OrchestratorRunStatus",
    "OrchestratorRunPriority",
    "OrchestratorPlanStep",
    "OrchestratorPlanStepStatus",
    "OrchestratorPlanOrigin",
    "OrchestratorApprovalRequirement",
    "OrchestratorRunArtifactType",
    "OrchestratorTaskState",
    "SpecWorkflowRun",
    "SpecWorkflowRunStatus",
    "SpecWorkflowRunPhase",
    "SpecWorkflowTaskState",
    "SpecWorkflowTaskStatus",
    "SpecWorkflowTaskName",
    "WorkflowArtifact",
    "WorkflowArtifactType",
    "WorkflowCredentialAudit",
    "CodexCredentialStatus",
    "GitHubCredentialStatus",
    "CodexPreflightStatus",
]


class ApproverRoleListType(TypeDecorator):
    """Persist approver roles as a PostgreSQL ARRAY or JSON elsewhere."""

    impl = ARRAY(String(128))
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[override]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(String(128)))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        if value is None:
            return []
        return list(value)

    def process_result_value(self, value, dialect):  # type: ignore[override]
        if value is None:
            return []
        return list(value)


def _json_variant() -> JSON:
    return JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


def mutable_json_list() -> JSON:
    return MutableList.as_mutable(_json_variant())


def mutable_json_dict() -> JSON:
    return MutableDict.as_mutable(_json_variant())


class OrchestratorRunStatus(str, enum.Enum):
    """Lifecycle states tracked for orchestrator runs."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class OrchestratorRunPriority(str, enum.Enum):
    """Execution priority for orchestrator runs."""

    NORMAL = "normal"
    HIGH = "high"


class OrchestratorPlanStep(str, enum.Enum):
    """Supported steps inside an orchestrator ActionPlan."""

    ANALYZE = "analyze"
    PATCH = "patch"
    BUILD = "build"
    RESTART = "restart"
    VERIFY = "verify"
    ROLLBACK = "rollback"


class OrchestratorPlanStepStatus(str, enum.Enum):
    """Statuses describing plan step execution progress."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SpecWorkflowRunStatus(str, enum.Enum):
    """Lifecycle states tracked for Spec workflow runs."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NO_WORK = "no_work"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class SpecWorkflowRunPhase(str, enum.Enum):
    """High-level phase executed by the Spec workflow chain."""

    DISCOVER = "discover"
    SUBMIT = "submit"
    APPLY = "apply"
    PUBLISH = "publish"
    COMPLETE = "complete"


class SpecWorkflowTaskStatus(str, enum.Enum):
    """Execution state tracked for each workflow task."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SpecWorkflowTaskName(str, enum.Enum):
    """Supported Celery task identifiers for the chain."""

    DISCOVER = "discover"
    SUBMIT = "submit"
    APPLY = "apply"
    PUBLISH = "publish"
    FINALIZE = "finalize"
    RETRY_HOOK = "retry-hook"


class CodexPreflightStatus(str, enum.Enum):
    """Codex login verification result stored on a run."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CodexCredentialStatus(str, enum.Enum):
    """Result of Codex credential validation."""

    VALID = "valid"
    INVALID = "invalid"
    EXPIRES_SOON = "expires_soon"


class GitHubCredentialStatus(str, enum.Enum):
    """Result of GitHub credential validation."""

    VALID = "valid"
    INVALID = "invalid"
    SCOPE_MISSING = "scope_missing"


class WorkflowArtifactType(str, enum.Enum):
    """Artifacts captured while the Spec workflow executes."""

    CODEX_LOGS = "codex_logs"
    CODEX_PATCH = "codex_patch"
    GH_PUSH_LOG = "gh_push_log"
    GH_PR_RESPONSE = "gh_pr_response"
    APPLY_OUTPUT = "apply_output"
    PR_PAYLOAD = "pr_payload"
    RETRY_CONTEXT = "retry_context"


class OrchestratorPlanOrigin(str, enum.Enum):
    """Source responsible for generating an ActionPlan."""

    OPERATOR = "operator"
    LLM = "llm"
    SYSTEM = "system"


class OrchestratorApprovalRequirement(str, enum.Enum):
    """Approval enforcement options for protected services."""

    NONE = "none"
    PRE_RUN = "pre-run"
    PRE_VERIFY = "pre-verify"


class OrchestratorRunArtifactType(str, enum.Enum):
    """Classifications for artifacts stored per orchestrator run."""

    PATCH_DIFF = "patch_diff"
    BUILD_LOG = "build_log"
    VERIFY_LOG = "verify_log"
    ROLLBACK_LOG = "rollback_log"
    METRICS = "metrics"
    PLAN_SNAPSHOT = "plan_snapshot"


class OrchestratorTaskState(str, enum.Enum):
    """Celery state transitions recorded for orchestrator steps."""

    PENDING = "PENDING"
    STARTED = "STARTED"
    RETRY = "RETRY"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class ApprovalGate(Base):
    """Approval policies applied to orchestrator runs."""

    __tablename__ = "approval_gates"
    __table_args__ = (
        UniqueConstraint("service_name", name="uq_approval_gates_service_name"),
        CheckConstraint(
            "valid_for_minutes >= 5", name="ck_approval_gates_min_duration"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement: Mapped[OrchestratorApprovalRequirement] = mapped_column(
        Enum(
            OrchestratorApprovalRequirement,
            name="orchestratorapprovalrequirement",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=OrchestratorApprovalRequirement.NONE,
        server_default=OrchestratorApprovalRequirement.NONE.value,
    )
    approver_roles: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ApproverRoleListType()),
        nullable=False,
        default=list,
    )
    valid_for_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
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

    runs: Mapped[list["OrchestratorRun"]] = relationship(
        "OrchestratorRun",
        back_populates="approval_gate",
    )

    @validates("approver_roles", "requirement")
    def _validate_roles(
        self,
        key: str,
        value: Any,
    ) -> Any:
        roles: list[str]
        requirement = self.requirement
        if key == "approver_roles":
            roles = list(value or [])
        else:
            roles = list(self.approver_roles or [])
        if key == "requirement":
            requirement = value

        if (
            requirement is not None
            and requirement != OrchestratorApprovalRequirement.NONE
            and len(roles) == 0
        ):
            raise ValueError(
                "approver_roles must be provided when approvals are required"
            )
        return value


class OrchestratorActionPlan(Base):
    """Serialized representation of an orchestrator ActionPlan."""

    __tablename__ = "orchestrator_action_plans"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        mutable_json_list(), nullable=False
    )
    service_context: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    generated_by: Mapped[OrchestratorPlanOrigin] = mapped_column(
        Enum(
            OrchestratorPlanOrigin,
            name="orchestratorplanorigin",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=OrchestratorPlanOrigin.SYSTEM,
        server_default=OrchestratorPlanOrigin.SYSTEM.value,
    )

    runs: Mapped[list["OrchestratorRun"]] = relationship(
        "OrchestratorRun",
        back_populates="action_plan",
    )


class OrchestratorRun(Base):
    """Top-level record describing a single orchestrator execution."""

    __tablename__ = "orchestrator_runs"
    __table_args__ = (
        CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_orchestrator_runs_timestamps",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    target_service: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[OrchestratorRunPriority] = mapped_column(
        Enum(
            OrchestratorRunPriority,
            name="orchestratorrunpriority",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=OrchestratorRunPriority.NORMAL,
        server_default=OrchestratorRunPriority.NORMAL.value,
    )
    status: Mapped[OrchestratorRunStatus] = mapped_column(
        Enum(
            OrchestratorRunStatus,
            name="orchestratorrunstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=OrchestratorRunStatus.PENDING,
        server_default=OrchestratorRunStatus.PENDING.value,
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approval_token: Mapped[Optional[str]] = mapped_column(
        EncryptedType(Text, get_encryption_key), nullable=True
    )
    metrics_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    artifact_root: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    action_plan_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("orchestrator_action_plans.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approval_gate_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid,
        ForeignKey("approval_gates.id", ondelete="SET NULL"),
        nullable=True,
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

    action_plan: Mapped[OrchestratorActionPlan] = relationship(
        "OrchestratorActionPlan",
        back_populates="runs",
    )
    approval_gate: Mapped[Optional[ApprovalGate]] = relationship(
        "ApprovalGate",
        back_populates="runs",
    )
    artifacts: Mapped[list["OrchestratorRunArtifact"]] = relationship(
        "OrchestratorRunArtifact",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="OrchestratorRunArtifact.created_at",
    )
    task_states: Mapped[list["SpecWorkflowTaskState"]] = relationship(
        "SpecWorkflowTaskState",
        back_populates="orchestrator_run",
        cascade="all, delete-orphan",
        order_by="SpecWorkflowTaskState.created_at",
    )


class OrchestratorRunArtifact(Base):
    """Metadata describing files captured during orchestrator runs."""

    __tablename__ = "orchestrator_run_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "artifact_type", "path", name="uq_orchestrator_artifact_path"
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_orchestrator_artifacts_size_non_negative",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("orchestrator_runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[OrchestratorRunArtifactType] = mapped_column(
        Enum(
            OrchestratorRunArtifactType,
            name="orchestratorrunartifacttype",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[OrchestratorRun] = relationship(
        "OrchestratorRun",
        back_populates="artifacts",
    )


class WorkflowCredentialAudit(Base):
    """Credential verification metadata recorded for each workflow run."""

    __tablename__ = "workflow_credential_audits"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", name="uq_workflow_credential_audit_run"),
        Index("ix_workflow_credential_audits_run", "workflow_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
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
        server_default=func.now(),
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    codex_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    codex_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    github_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    github_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    environment_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    workflow_run: Mapped["SpecWorkflowRun"] = relationship(
        "SpecWorkflowRun",
        back_populates="credential_audit",
        foreign_keys=[workflow_run_id],
    )


class SpecWorkflowRun(Base):
    """Top-level record per Spec workflow execution."""

    __tablename__ = "spec_workflow_runs"
    __table_args__ = (
        Index("ix_spec_workflow_runs_feature_key", "feature_key"),
        Index("ix_spec_workflow_runs_status", "status"),
        Index("ix_spec_workflow_runs_requested_by", "requested_by_user_id"),
        Index("ix_spec_workflow_runs_created_by", "created_by"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    feature_key: Mapped[str] = mapped_column(String(255), nullable=False)
    celery_chain_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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
    branch_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pr_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    repository: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    requested_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[UUID]] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    codex_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    codex_queue: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("codex_worker_shards.queue_name", ondelete="SET NULL"),
        nullable=True,
    )
    codex_volume: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("codex_auth_volumes.name", ondelete="SET NULL"),
        nullable=True,
    )
    codex_preflight_status: Mapped[Optional[CodexPreflightStatus]] = mapped_column(
        Enum(
            CodexPreflightStatus,
            name="codexpreflightstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    codex_preflight_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    codex_logs_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    codex_patch_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    artifacts_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    current_task_name: Mapped[Optional[SpecWorkflowTaskName]] = mapped_column(
        Enum(
            SpecWorkflowTaskName,
            name="specworkflowtaskname",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
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
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

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
    credential_audit: Mapped[Optional[WorkflowCredentialAudit]] = relationship(
        "WorkflowCredentialAudit",
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        foreign_keys=lambda: [WorkflowCredentialAudit.workflow_run_id],
        uselist=False,
    )
    requested_by: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=lambda: [SpecWorkflowRun.requested_by_user_id],
    )
    created_by_user: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=lambda: [SpecWorkflowRun.created_by],
    )
    codex_auth_volume: Mapped[Optional["CodexAuthVolume"]] = relationship(
        "CodexAuthVolume",
        primaryjoin="SpecWorkflowRun.codex_volume == CodexAuthVolume.name",
        foreign_keys=lambda: [SpecWorkflowRun.codex_volume],
    )
    codex_shard: Mapped[Optional["CodexWorkerShard"]] = relationship(
        "CodexWorkerShard",
        primaryjoin="SpecWorkflowRun.codex_queue == CodexWorkerShard.queue_name",
        foreign_keys=lambda: [SpecWorkflowRun.codex_queue],
    )


class WorkflowArtifact(Base):
    """Artifact metadata captured for a workflow run."""

    __tablename__ = "workflow_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "artifact_type", "path", name="uq_workflow_artifact_path"
        ),
        Index("ix_workflow_artifacts_run_id", "workflow_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
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
    content_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    workflow_run: Mapped[SpecWorkflowRun] = relationship(
        "SpecWorkflowRun", back_populates="artifacts"
    )


class SpecWorkflowTaskState(Base):
    """Per-task execution status persisted for monitoring."""

    __tablename__ = "spec_workflow_task_states"
    __table_args__ = (
        CheckConstraint(
            "(workflow_run_id IS NOT NULL AND orchestrator_run_id IS NULL) OR "
            "(workflow_run_id IS NULL AND orchestrator_run_id IS NOT NULL)",
            name="ck_spec_workflow_task_state_run_id_exclusive",
        ),
        UniqueConstraint(
            "workflow_run_id", "task_name", "attempt", name="uq_spec_workflow_task_state_attempt"
        ),
        UniqueConstraint(
            "orchestrator_run_id",
            "plan_step",
            "attempt",
            name="uq_orchestrator_task_state_attempt",
        ),
        Index("ix_spec_workflow_task_states_run_id", "workflow_run_id"),
        Index(
            "ix_spec_workflow_task_states_failed",
            "workflow_run_id",
            postgresql_where=text("status = 'failed'"),
        ),
        Index(
            "ix_spec_workflow_task_states_orchestrator_run_id",
            "orchestrator_run_id",
        ),
        CheckConstraint(
            "(orchestrator_run_id IS NULL) OR (plan_step IS NOT NULL)",
            name="ck_spec_workflow_task_state_orchestrator_plan_step",
        ),
        CheckConstraint(
            "(workflow_run_id IS NULL) OR (task_name IS NOT NULL)",
            name="ck_spec_workflow_task_state_task_name_required",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_run_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid,
        ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    orchestrator_run_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid,
        ForeignKey("orchestrator_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    task_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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
        mutable_json_dict(), nullable=True
    )
    plan_step: Mapped[Optional[OrchestratorPlanStep]] = mapped_column(
        Enum(
            OrchestratorPlanStep,
            name="orchestratorplanstep",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    plan_step_status: Mapped[Optional[OrchestratorPlanStepStatus]] = mapped_column(
        Enum(
            OrchestratorPlanStepStatus,
            name="orchestratorplanstepstatus",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    celery_state: Mapped[Optional[OrchestratorTaskState]] = mapped_column(
        Enum(
            OrchestratorTaskState,
            name="orchestratortaskstate",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_refs: Mapped[Optional[list[str]]] = mapped_column(
        mutable_json_list(), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
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

    workflow_run: Mapped[Optional[SpecWorkflowRun]] = relationship(
        "SpecWorkflowRun",
        back_populates="task_states",
    )
    orchestrator_run: Mapped[Optional[OrchestratorRun]] = relationship(
        "OrchestratorRun",
        back_populates="task_states",
    )


Index("ix_orchestrator_runs_status", OrchestratorRun.status)
Index("ix_orchestrator_runs_target_service", OrchestratorRun.target_service)
Index("ix_orchestrator_run_artifacts_run_id", OrchestratorRunArtifact.run_id)
