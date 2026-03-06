"""Database models used by the MoonMind API service."""

from __future__ import annotations

import enum
from datetime import datetime
from importlib import import_module
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from moonmind.workflows.speckit_celery.models import (
        CodexAuthVolume,
        CodexWorkerShard,
    )

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Return enum members as stored DB labels, not Python enum names."""

    return [member.value for member in enum_cls]


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


def _json_variant() -> JSON:
    return JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


def mutable_json_list() -> JSON:
    return MutableList.as_mutable(_json_variant())


def mutable_json_dict() -> JSON:
    return MutableDict.as_mutable(_json_variant())


class ManifestRecord(Base):
    __tablename__ = "manifest"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(80), nullable=False)
    version = Column(String(32), nullable=False, default="v0", server_default="v0")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_indexed_at = Column(DateTime(timezone=True), nullable=True)
    last_run_job_id = Column(Uuid, nullable=True)
    last_run_source = Column(String(16), nullable=True)
    last_run_status = Column(String(32), nullable=True)
    last_run_workflow_id = Column(String(64), nullable=True)
    last_run_temporal_run_id = Column(String(64), nullable=True)
    last_run_manifest_ref = Column(String(512), nullable=True)
    last_run_started_at = Column(DateTime(timezone=True), nullable=True)
    last_run_finished_at = Column(DateTime(timezone=True), nullable=True)
    state_json = Column(mutable_json_dict(), nullable=True)
    state_updated_at = Column(DateTime(timezone=True), nullable=True)


class RecurringTaskScheduleType(str, enum.Enum):
    """Supported recurring definition schedule kinds."""

    CRON = "cron"


class RecurringTaskScopeType(str, enum.Enum):
    """Scope ownership for recurring definitions."""

    PERSONAL = "personal"
    TEAM = "team"
    GLOBAL = "global"


class RecurringTaskRunOutcome(str, enum.Enum):
    """Dispatch result state for one recurring run decision."""

    PENDING_DISPATCH = "pending_dispatch"
    ENQUEUED = "enqueued"
    SKIPPED = "skipped"
    DISPATCH_ERROR = "dispatch_error"


class RecurringTaskRunTrigger(str, enum.Enum):
    """How one recurring run row was created."""

    SCHEDULE = "schedule"
    MANUAL = "manual"


class RecurringTaskDefinition(Base):
    """Persistent recurring schedule definition."""

    __tablename__ = "recurring_task_definitions"
    __table_args__ = (
        Index(
            "ix_recurring_task_definitions_enabled_next_run_at",
            "enabled",
            "next_run_at",
        ),
        Index(
            "ix_recurring_task_definitions_owner_enabled",
            "owner_user_id",
            "enabled",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schedule_type: Mapped[RecurringTaskScheduleType] = mapped_column(
        Enum(
            RecurringTaskScheduleType,
            name="recurringtaskscheduletype",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RecurringTaskScheduleType.CRON,
    )
    cron: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_dispatch_status: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    last_dispatch_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    scope_type: Mapped[RecurringTaskScopeType] = mapped_column(
        Enum(
            RecurringTaskScopeType,
            name="recurringtaskscopetype",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RecurringTaskScopeType.PERSONAL,
    )
    scope_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    target: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    policy: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
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
    runs: Mapped[list["RecurringTaskRun"]] = relationship(
        "RecurringTaskRun",
        back_populates="definition",
        cascade="all, delete-orphan",
    )


class RecurringTaskRun(Base):
    """Persistent recurring run dispatch decision row."""

    __tablename__ = "recurring_task_runs"
    __table_args__ = (
        UniqueConstraint(
            "definition_id",
            "scheduled_for",
            name="uq_recurring_task_runs_definition_scheduled_for",
        ),
        Index(
            "ix_recurring_task_runs_definition_created_at",
            "definition_id",
            "created_at",
        ),
        Index(
            "ix_recurring_task_runs_outcome_dispatch_after",
            "outcome",
            "dispatch_after",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    definition_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("recurring_task_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    trigger: Mapped[RecurringTaskRunTrigger] = mapped_column(
        Enum(
            RecurringTaskRunTrigger,
            name="recurringtaskruntrigger",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RecurringTaskRunTrigger.SCHEDULE,
    )
    outcome: Mapped[RecurringTaskRunOutcome] = mapped_column(
        Enum(
            RecurringTaskRunOutcome,
            name="recurringtaskrunoutcome",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RecurringTaskRunOutcome.PENDING_DISPATCH,
    )
    dispatch_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    dispatch_after: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    queue_job_id: Mapped[Optional[UUID]] = mapped_column(Uuid, nullable=True)
    queue_job_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    definition: Mapped[RecurringTaskDefinition] = relationship(
        "RecurringTaskDefinition",
        back_populates="runs",
    )


__all__ = [
    "Base",
    "User",
    "UserProfile",
    "ManifestRecord",
    "RecurringTaskDefinition",
    "RecurringTaskRun",
    "RecurringTaskRunOutcome",
    "RecurringTaskRunTrigger",
    "RecurringTaskScheduleType",
    "RecurringTaskScopeType",
    "ApprovalGate",
    "OrchestratorActionPlan",
    "OrchestratorRun",
    "OrchestratorRunArtifact",
    "OrchestratorTaskStep",
    "OrchestratorRunStatus",
    "OrchestratorRunPriority",
    "OrchestratorPlanStep",
    "OrchestratorPlanStepStatus",
    "OrchestratorPlanOrigin",
    "OrchestratorApprovalRequirement",
    "OrchestratorRunArtifactType",
    "OrchestratorTaskState",
    "OrchestratorTaskStepStatus",
    "TemporalWorkflowType",
    "MoonMindWorkflowState",
    "TemporalExecutionCloseStatus",
    "TemporalExecutionRecord",
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
    "TaskTemplateScopeType",
    "TaskTemplateReleaseStatus",
    "TaskStepTemplate",
    "TaskStepTemplateVersion",
    "TaskStepTemplateFavorite",
    "TaskStepTemplateRecent",
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


class TaskTemplateScopeType(str, enum.Enum):
    """Scope owner for task step template visibility."""

    GLOBAL = "global"
    TEAM = "team"
    PERSONAL = "personal"


class TaskTemplateReleaseStatus(str, enum.Enum):
    """Release lifecycle for task step template versions."""

    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


class TaskStepTemplate(Base):
    """Top-level catalog entry for reusable task step templates."""

    __tablename__ = "task_step_templates"
    __table_args__ = (
        UniqueConstraint(
            "slug", "scope_type", "scope_ref", name="uq_task_step_template_slug_scope"
        ),
        Index("ix_task_step_templates_scope", "scope_type", "scope_ref"),
        Index("ix_task_step_templates_slug", "slug"),
        Index("ix_task_step_templates_active", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_type: Mapped[TaskTemplateScopeType] = mapped_column(
        Enum(
            TaskTemplateScopeType,
            name="tasktemplatescopetype",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    scope_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(mutable_json_list(), default=list)
    required_capabilities: Mapped[list[str]] = mapped_column(
        mutable_json_list(), default=list
    )
    latest_version_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid,
        ForeignKey(
            "task_step_template_versions.id",
            name="fk_task_template_latest_version",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[UUID]] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    latest_version: Mapped[Optional["TaskStepTemplateVersion"]] = relationship(
        "TaskStepTemplateVersion", foreign_keys=[latest_version_id], post_update=True
    )
    versions: Mapped[list["TaskStepTemplateVersion"]] = relationship(
        "TaskStepTemplateVersion",
        back_populates="template",
        foreign_keys="TaskStepTemplateVersion.template_id",
        cascade="all, delete-orphan",
        order_by="TaskStepTemplateVersion.created_at",
    )
    favorites: Mapped[list["TaskStepTemplateFavorite"]] = relationship(
        "TaskStepTemplateFavorite",
        back_populates="template",
        cascade="all, delete-orphan",
    )


class TaskStepTemplateVersion(Base):
    """Immutable release of a template blueprint."""

    __tablename__ = "task_step_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "version", name="uq_task_step_template_version_label"
        ),
        Index("ix_task_step_template_versions_template", "template_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("task_step_templates.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        mutable_json_list(), nullable=False
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        mutable_json_list(), nullable=False
    )
    annotations: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    required_capabilities: Mapped[list[str]] = mapped_column(
        mutable_json_list(), default=list
    )
    max_step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    release_status: Mapped[TaskTemplateReleaseStatus] = mapped_column(
        Enum(
            TaskTemplateReleaseStatus,
            name="tasktemplatereleasestatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskTemplateReleaseStatus.DRAFT,
    )
    reviewed_by: Mapped[Optional[UUID]] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    seed_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    template: Mapped[TaskStepTemplate] = relationship(
        "TaskStepTemplate",
        back_populates="versions",
        foreign_keys=[template_id],
    )
    recents: Mapped[list["TaskStepTemplateRecent"]] = relationship(
        "TaskStepTemplateRecent",
        back_populates="template_version",
        cascade="all, delete-orphan",
    )


class TaskStepTemplateFavorite(Base):
    """User favorites for quick preset access."""

    __tablename__ = "task_step_template_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "template_id", name="uq_task_template_favorite"),
        Index("ix_task_step_template_favorites_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("task_step_templates.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    template: Mapped[TaskStepTemplate] = relationship(
        "TaskStepTemplate", back_populates="favorites"
    )


class TaskStepTemplateRecent(Base):
    """Tracks most recent template applications per user."""

    __tablename__ = "task_step_template_recents"
    __table_args__ = (
        Index("ix_task_step_template_recents_user", "user_id"),
        UniqueConstraint(
            "user_id",
            "template_version_id",
            name="uq_task_template_recent_user_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    template_version_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("task_step_template_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    template_version: Mapped[TaskStepTemplateVersion] = relationship(
        "TaskStepTemplateVersion", back_populates="recents"
    )


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


class TemporalArtifactStorageBackend(str, enum.Enum):
    """Supported backing stores for Temporal artifact bytes."""

    S3 = "s3"
    LOCAL_FS = "local_fs"


class TemporalArtifactEncryption(str, enum.Enum):
    """Encryption mode metadata recorded for each artifact."""

    SSE_KMS = "sse-kms"
    SSE_S3 = "sse-s3"
    NONE = "none"
    ENVELOPE = "envelope"


class TemporalArtifactStatus(str, enum.Enum):
    """Lifecycle status for immutable Temporal artifacts."""

    PENDING_UPLOAD = "pending_upload"
    COMPLETE = "complete"
    FAILED = "failed"
    DELETED = "deleted"


class TemporalArtifactRetentionClass(str, enum.Enum):
    """Retention policy classes for Temporal artifact lifecycle management."""

    EPHEMERAL = "ephemeral"
    STANDARD = "standard"
    LONG = "long"
    PINNED = "pinned"


class TemporalArtifactRedactionLevel(str, enum.Enum):
    """Sensitivity/redaction classification for artifact reads."""

    NONE = "none"
    PREVIEW_ONLY = "preview_only"
    RESTRICTED = "restricted"


class TemporalArtifactUploadMode(str, enum.Enum):
    """Upload mode selected when creating an artifact session."""

    SINGLE_PUT = "single_put"
    MULTIPART = "multipart"


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


class OrchestratorTaskStepStatus(str, enum.Enum):
    """Status values persisted for orchestrator task runtime steps."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class TemporalWorkflowType(str, enum.Enum):
    """Supported root workflow type catalog entries."""

    RUN = "MoonMind.Run"
    MANIFEST_INGEST = "MoonMind.ManifestIngest"


class MoonMindWorkflowState(str, enum.Enum):
    """Domain lifecycle states exposed for dashboard filtering."""

    INITIALIZING = "initializing"
    PLANNING = "planning"
    EXECUTING = "executing"
    AWAITING_EXTERNAL = "awaiting_external"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class TemporalExecutionCloseStatus(str, enum.Enum):
    """Terminal Temporal close statuses tracked for invariant checks."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    TERMINATED = "terminated"
    TIMED_OUT = "timed_out"
    CONTINUED_AS_NEW = "continued_as_new"


class TemporalExecutionRecord(Base):
    """Temporal execution projection used for lifecycle APIs and filtering."""

    __tablename__ = "temporal_executions"
    __table_args__ = (
        Index(
            "ix_temporal_executions_state_updated_at",
            "state",
            "updated_at",
        ),
        Index(
            "ix_temporal_executions_owner_state",
            "owner_id",
            "state",
        ),
        Index(
            "ix_temporal_executions_type_updated_at",
            "workflow_type",
            "updated_at",
        ),
        UniqueConstraint(
            "create_idempotency_key",
            "owner_id",
            "workflow_type",
            name="uq_temporal_executions_create_idempotency_owner_type",
        ),
    )

    workflow_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    namespace: Mapped[str] = mapped_column(
        String(128), nullable=False, default="moonmind"
    )
    workflow_type: Mapped[TemporalWorkflowType] = mapped_column(
        Enum(
            TemporalWorkflowType,
            name="temporalworkflowtype",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    state: Mapped[MoonMindWorkflowState] = mapped_column(
        Enum(
            MoonMindWorkflowState,
            name="moonmindworkflowstate",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=MoonMindWorkflowState.INITIALIZING,
        server_default=MoonMindWorkflowState.INITIALIZING.value,
    )
    close_status: Mapped[Optional[TemporalExecutionCloseStatus]] = mapped_column(
        Enum(
            TemporalExecutionCloseStatus,
            name="temporalexecutionclosestatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    entry: Mapped[str] = mapped_column(String(16), nullable=False)
    search_attributes: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    memo: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    artifact_refs: Mapped[list[str]] = mapped_column(
        mutable_json_list(), nullable=False, default=list
    )
    input_ref: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    plan_ref: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    manifest_ref: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    pending_parameters_patch: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    awaiting_external: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wait_cycle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rerun_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    last_update_idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    last_update_response: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
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
    task_steps: Mapped[list["OrchestratorTaskStep"]] = relationship(
        "OrchestratorTaskStep",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="OrchestratorTaskStep.step_index",
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
            values_callable=_enum_values,
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


class OrchestratorTaskStep(Base):
    """Arbitrary orchestrator task steps persisted outside the fixed enum flow."""

    __tablename__ = "orchestrator_task_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "step_id", name="uq_orchestrator_task_step_id"),
        CheckConstraint("step_index >= 0", name="ck_orchestrator_task_step_index"),
        CheckConstraint(
            "attempt >= 1", name="ck_orchestrator_task_step_attempt_positive"
        ),
        Index("ix_orchestrator_task_steps_task_id", "task_id", "step_index"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("orchestrator_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    skill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    skill_args: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    status: Mapped[OrchestratorTaskStepStatus] = mapped_column(
        Enum(
            OrchestratorTaskStepStatus,
            name="orchestratortaskstepstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=OrchestratorTaskStepStatus.QUEUED,
        server_default=OrchestratorTaskStepStatus.QUEUED.value,
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_refs: Mapped[list[str]] = mapped_column(
        mutable_json_list(), nullable=False, default=list
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    run: Mapped[OrchestratorRun] = relationship(
        "OrchestratorRun",
        back_populates="task_steps",
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
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    github_status: Mapped[GitHubCredentialStatus] = mapped_column(
        Enum(
            GitHubCredentialStatus,
            name="workflowgithubcredentialstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
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


class TemporalArtifact(Base):
    """Metadata index row for one Temporal artifact blob."""

    __tablename__ = "temporal_artifacts"
    __table_args__ = (
        Index("ix_temporal_artifacts_created_at", "created_at"),
        Index("ix_temporal_artifacts_status", "status"),
        Index("ix_temporal_artifacts_expires_at", "expires_at"),
        Index("ix_temporal_artifacts_deleted_at", "deleted_at"),
        Index("ix_temporal_artifacts_hard_deleted_at", "hard_deleted_at"),
    )

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by_principal: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    content_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    storage_backend: Mapped[TemporalArtifactStorageBackend] = mapped_column(
        Enum(
            TemporalArtifactStorageBackend,
            name="temporalartifactstoragebackend",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalArtifactStorageBackend.S3,
        server_default=TemporalArtifactStorageBackend.S3.value,
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    encryption: Mapped[TemporalArtifactEncryption] = mapped_column(
        Enum(
            TemporalArtifactEncryption,
            name="temporalartifactencryption",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalArtifactEncryption.NONE,
        server_default=TemporalArtifactEncryption.NONE.value,
    )
    status: Mapped[TemporalArtifactStatus] = mapped_column(
        Enum(
            TemporalArtifactStatus,
            name="temporalartifactstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalArtifactStatus.PENDING_UPLOAD,
        server_default=TemporalArtifactStatus.PENDING_UPLOAD.value,
    )
    retention_class: Mapped[TemporalArtifactRetentionClass] = mapped_column(
        Enum(
            TemporalArtifactRetentionClass,
            name="temporalartifactretentionclass",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalArtifactRetentionClass.STANDARD,
        server_default=TemporalArtifactRetentionClass.STANDARD.value,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    redaction_level: Mapped[TemporalArtifactRedactionLevel] = mapped_column(
        Enum(
            TemporalArtifactRedactionLevel,
            name="temporalartifactredactionlevel",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalArtifactRedactionLevel.NONE,
        server_default=TemporalArtifactRedactionLevel.NONE.value,
    )
    upload_mode: Mapped[TemporalArtifactUploadMode] = mapped_column(
        Enum(
            TemporalArtifactUploadMode,
            name="temporalartifactuploadmode",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalArtifactUploadMode.SINGLE_PUT,
        server_default=TemporalArtifactUploadMode.SINGLE_PUT.value,
    )
    upload_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    upload_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    hard_deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    tombstoned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_lifecycle_run_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        mutable_json_dict(),
        nullable=False,
        default=dict,
    )

    links: Mapped[list["TemporalArtifactLink"]] = relationship(
        "TemporalArtifactLink",
        back_populates="artifact",
        cascade="all, delete-orphan",
        order_by="TemporalArtifactLink.created_at",
    )
    pins: Mapped[list["TemporalArtifactPin"]] = relationship(
        "TemporalArtifactPin",
        back_populates="artifact",
        cascade="all, delete-orphan",
        order_by="TemporalArtifactPin.pinned_at",
    )


class TemporalArtifactLink(Base):
    """Execution linkage row giving an artifact semantic meaning."""

    __tablename__ = "temporal_artifact_links"
    __table_args__ = (
        Index(
            "ix_temporal_artifact_links_execution",
            "namespace",
            "workflow_id",
            "run_id",
            "link_type",
            "created_at",
        ),
        Index(
            "ix_temporal_artifact_links_artifact_id",
            "artifact_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    artifact_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("temporal_artifacts.artifact_id", ondelete="CASCADE"),
        nullable=False,
    )
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    link_type: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by_activity_type: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    created_by_worker: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    artifact: Mapped[TemporalArtifact] = relationship(
        "TemporalArtifact",
        back_populates="links",
    )


class TemporalArtifactPin(Base):
    """Optional explicit pin row for artifacts exempt from lifecycle cleanup."""

    __tablename__ = "temporal_artifact_pins"
    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_temporal_artifact_pins_artifact_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    artifact_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("temporal_artifacts.artifact_id", ondelete="CASCADE"),
        nullable=False,
    )
    pinned_by_principal: Mapped[str] = mapped_column(String(255), nullable=False)
    pinned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    artifact: Mapped[TemporalArtifact] = relationship(
        "TemporalArtifact",
        back_populates="pins",
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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
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
        primaryjoin=lambda: SpecWorkflowRun.id
        == WorkflowCredentialAudit.workflow_run_id,
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
            values_callable=_enum_values,
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
            "workflow_run_id",
            "task_name",
            "attempt",
            name="uq_spec_workflow_task_state_attempt",
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
            values_callable=_enum_values,
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
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    plan_step_status: Mapped[Optional[OrchestratorPlanStepStatus]] = mapped_column(
        Enum(
            OrchestratorPlanStepStatus,
            name="orchestratorplanstepstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    celery_state: Mapped[Optional[OrchestratorTaskState]] = mapped_column(
        Enum(
            OrchestratorTaskState,
            name="orchestratortaskstate",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_paths: Mapped[Optional[list[str]]] = mapped_column(
        "artifact_refs", mutable_json_list(), nullable=True
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


def _register_workflow_model_dependencies() -> None:
    """Import workflow ORM models so string relationships can resolve."""

    if TYPE_CHECKING:
        return

    import_module("moonmind.workflows.speckit_celery.models")
    import_module("moonmind.workflows.agent_queue.models")


_register_workflow_model_dependencies()
