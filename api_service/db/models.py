"""Database models used by the MoonMind API service."""

from __future__ import annotations

import enum
from datetime import datetime
from importlib import import_module
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID, uuid4


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
)
from sqlalchemy.types import TypeDecorator
from sqlalchemy_utils import StringEncryptedType

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
        StringEncryptedType(String, get_encryption_key), nullable=True
    )
    openai_api_key_encrypted = Column(
        StringEncryptedType(String, get_encryption_key), nullable=True
    )
    github_token_encrypted = Column(
        StringEncryptedType(String, get_encryption_key), nullable=True
    )
    anthropic_api_key_encrypted = Column(
        StringEncryptedType(String, get_encryption_key), nullable=True
    )

    agent_skill_repo_sources_enabled = Column(Boolean, nullable=False, default=True)
    agent_skill_local_sources_enabled = Column(Boolean, nullable=False, default=False)

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
    temporal_schedule_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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
    temporal_workflow_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    temporal_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
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
    "TemporalWorkflowType",
    "MoonMindWorkflowState",
    "TemporalExecutionCloseStatus",
    "TemporalExecutionOwnerType",
    "TemporalExecutionProjectionSyncState",
    "TemporalExecutionProjectionSourceMode",
    "TemporalExecutionCanonicalRecord",
    "TemporalExecutionRecord",
    "TemporalIntegrationCorrelationRecord",
    "WorkflowRun",
    "WorkflowRunStatus",
    "WorkflowRunPhase",
    "WorkflowTaskState",
    "WorkflowTaskStatus",
    "WorkflowTaskName",
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
    "SecretStatus",
    "ManagedSecret",
    "AgentSkillSourceKind",
    "AgentSkillFormat",
    "AgentSkillDefinition",
    "AgentSkillVersion",
    "SkillSet",
    "SkillSetEntry",
]


class SecretStatus(str, enum.Enum):
    """Lifecycle state for a MoonMind managed secret."""

    ACTIVE = "active"
    DISABLED = "disabled"
    ROTATED = "rotated"
    DELETED = "deleted"
    INVALID = "invalid"


class ManagedSecret(Base):
    """Encrypted durable storage for SecretRefs."""

    __tablename__ = "managed_secrets"
    __table_args__ = (
        Index("ix_managed_secrets_slug", "slug", unique=True),
        Index("ix_managed_secrets_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    ciphertext: Mapped[str] = mapped_column(StringEncryptedType(Text, get_encryption_key), nullable=False)
    status: Mapped[SecretStatus] = mapped_column(
        Enum(
            SecretStatus,
            name="secretstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=SecretStatus.ACTIVE,
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
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


class TaskTemplateScopeType(str, enum.Enum):
    """Scope owner for task step template visibility."""

    GLOBAL = "global"
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


class WorkflowRunStatus(str, enum.Enum):
    """Lifecycle states tracked for Spec workflow runs."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NO_WORK = "no_work"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class WorkflowRunPhase(str, enum.Enum):
    """High-level phase executed by the Spec workflow chain."""

    DISCOVER = "discover"
    SUBMIT = "submit"
    APPLY = "apply"
    PUBLISH = "publish"
    COMPLETE = "complete"


class WorkflowTaskStatus(str, enum.Enum):
    """Execution state tracked for each workflow task."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowTaskName(str, enum.Enum):
    """Supported workflow task identifiers for the chain."""

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


from moonmind.core.artifacts import (
    TemporalArtifactStorageBackend,
    TemporalArtifactEncryption,
    TemporalArtifactStatus,
    TemporalArtifactRetentionClass,
    TemporalArtifactRedactionLevel,
    TemporalArtifactUploadMode,
)


class TemporalWorkflowType(str, enum.Enum):
    """Supported root workflow type catalog entries."""

    RUN = "MoonMind.Run"
    MANIFEST_INGEST = "MoonMind.ManifestIngest"
    PROVIDER_PROFILE_MANAGER = "MoonMind.ProviderProfileManager"


class MoonMindWorkflowState(str, enum.Enum):
    """Domain lifecycle states exposed for dashboard filtering."""

    SCHEDULED = "scheduled"
    INITIALIZING = "initializing"
    WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"
    PLANNING = "planning"
    AWAITING_SLOT = "awaiting_slot"
    EXECUTING = "executing"
    PROPOSALS = "proposals"
    AWAITING_EXTERNAL = "awaiting_external"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
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


class TemporalExecutionOwnerType(str, enum.Enum):
    """Owner class mirrored into the execution projection and Visibility model."""

    USER = "user"
    SYSTEM = "system"
    SERVICE = "service"


class TemporalExecutionProjectionSyncState(str, enum.Enum):
    """Freshness marker for the execution projection row."""

    FRESH = "fresh"
    STALE = "stale"
    REPAIR_PENDING = "repair_pending"
    ORPHANED = "orphaned"


class TemporalExecutionProjectionSourceMode(str, enum.Enum):
    """How authoritative the current projection row is meant to be."""

    PROJECTION_ONLY = "projection_only"
    MIXED = "mixed"
    TEMPORAL_AUTHORITATIVE = "temporal_authoritative"


class TemporalExecutionCanonicalRecord(Base):
    """Authoritative execution state mirrored from the Temporal control plane."""

    __tablename__ = "temporal_execution_sources"
    __table_args__ = (
        Index(
            "ix_temporal_execution_sources_state_updated_at",
            "state",
            "updated_at",
        ),
        Index(
            "ix_temporal_execution_sources_owner_state",
            "owner_id",
            "state",
        ),
        Index(
            "ix_temporal_execution_sources_type_updated_at",
            "workflow_type",
            "updated_at",
        ),
        UniqueConstraint(
            "create_idempotency_key",
            "owner_id",
            "owner_type",
            "workflow_type",
            name="uq_temporal_execution_sources_create_idempotency_owner_type",
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
    owner_type: Mapped[TemporalExecutionOwnerType] = mapped_column(
        Enum(
            TemporalExecutionOwnerType,
            name="temporalexecutionownertype",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalExecutionOwnerType.USER,
        server_default=TemporalExecutionOwnerType.USER.value,
    )
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
    integration_state: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    pending_parameters_patch: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    awaiting_external: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    waiting_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    attention_required: Mapped[bool] = mapped_column(
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
            "owner_type",
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
    owner_type: Mapped[TemporalExecutionOwnerType] = mapped_column(
        Enum(
            TemporalExecutionOwnerType,
            name="temporalexecutionownertype",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalExecutionOwnerType.USER,
        server_default=TemporalExecutionOwnerType.USER.value,
    )
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
    integration_state: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    pending_parameters_patch: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    awaiting_external: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    waiting_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    attention_required: Mapped[bool] = mapped_column(
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
    projection_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sync_state: Mapped[TemporalExecutionProjectionSyncState] = mapped_column(
        Enum(
            TemporalExecutionProjectionSyncState,
            name="temporalexecutionprojectionsyncstate",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalExecutionProjectionSyncState.FRESH,
        server_default=TemporalExecutionProjectionSyncState.FRESH.value,
    )
    sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_mode: Mapped[TemporalExecutionProjectionSourceMode] = mapped_column(
        Enum(
            TemporalExecutionProjectionSourceMode,
            name="temporalexecutionprojectionsourcemode",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TemporalExecutionProjectionSourceMode.PROJECTION_ONLY,
        server_default=TemporalExecutionProjectionSourceMode.PROJECTION_ONLY.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    _IDENTIFIER_ALIASES = ("task:", "workflow:", "execution:")

    @classmethod
    def canonicalize_identifier(cls, raw_identifier: str) -> str:
        """Normalize temporary compatibility aliases back to workflowId."""

        candidate = str(raw_identifier or "").strip()
        for prefix in cls._IDENTIFIER_ALIASES:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix) :].strip()
                break
        return candidate


class TemporalIntegrationCorrelationRecord(Base):
    """Durable lookup record for resolving integration callbacks to workflows."""

    __tablename__ = "temporal_integration_correlations"
    __table_args__ = (
        UniqueConstraint(
            "integration_name",
            "callback_correlation_key",
            name="uq_temporal_integration_correlations_callback_key",
        ),
        UniqueConstraint(
            "integration_name",
            "external_operation_id",
            name="uq_temporal_integration_correlations_operation_id",
        ),
        Index(
            "ix_temporal_integration_correlations_workflow_status",
            "workflow_id",
            "lifecycle_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    integration_name: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    callback_correlation_key: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    external_operation_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    workflow_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("temporal_executions.workflow_id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class TaskSourceMapping(Base):
    """Persisted global task index for canonical source resolution."""

    __tablename__ = "task_source_mappings"
    __table_args__ = (
        Index("ix_task_source_mappings_source_entry", "source", "entry"),
        Index(
            "ix_task_source_mappings_source_record_id",
            "source",
            "source_record_id",
        ),
    )

    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    entry: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    owner_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
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

    workflow_run: Mapped["WorkflowRun"] = relationship(
        "WorkflowRun",
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


class WorkflowRun(Base):
    """Top-level record per Spec workflow execution."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_feature_key", "feature_key"),
        Index("ix_workflow_runs_status", "status"),
        Index("ix_workflow_runs_requested_by", "requested_by_user_id"),
        Index("ix_workflow_runs_created_by", "created_by"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    feature_key: Mapped[str] = mapped_column(String(255), nullable=False)
    legacy_chain_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[WorkflowRunStatus] = mapped_column(
        Enum(
            WorkflowRunStatus,
            name="specworkflowrunstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=WorkflowRunStatus.PENDING,
    )
    phase: Mapped[WorkflowRunPhase] = mapped_column(
        Enum(
            WorkflowRunPhase,
            name="specworkflowrunphase",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=WorkflowRunPhase.DISCOVER,
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
    current_task_name: Mapped[Optional[WorkflowTaskName]] = mapped_column(
        Enum(
            WorkflowTaskName,
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

    task_states: Mapped[list["WorkflowTaskState"]] = relationship(
        "WorkflowTaskState",
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        order_by="WorkflowTaskState.created_at",
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
        primaryjoin=lambda: WorkflowRun.id
        == WorkflowCredentialAudit.workflow_run_id,
        foreign_keys=lambda: [WorkflowCredentialAudit.workflow_run_id],
        uselist=False,
    )
    requested_by: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=lambda: [WorkflowRun.requested_by_user_id],
    )
    created_by_user: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=lambda: [WorkflowRun.created_by],
    )
    codex_auth_volume: Mapped[Optional["CodexAuthVolume"]] = relationship(
        "CodexAuthVolume",
        primaryjoin="WorkflowRun.codex_volume == CodexAuthVolume.name",
        foreign_keys=lambda: [WorkflowRun.codex_volume],
    )
    codex_shard: Mapped[Optional["CodexWorkerShard"]] = relationship(
        "CodexWorkerShard",
        primaryjoin="WorkflowRun.codex_queue == CodexWorkerShard.queue_name",
        foreign_keys=lambda: [WorkflowRun.codex_queue],
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
        Uuid, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
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

    workflow_run: Mapped[WorkflowRun] = relationship(
        "WorkflowRun", back_populates="artifacts"
    )


class WorkflowTaskState(Base):
    """Per-task execution status persisted for monitoring."""

    __tablename__ = "workflow_task_states"
    __table_args__ = (
        CheckConstraint(
            "workflow_run_id IS NOT NULL",
            name="ck_workflow_task_state_workflow_run_required",
        ),
        UniqueConstraint(
            "workflow_run_id",
            "task_name",
            "attempt",
            name="uq_workflow_task_state_attempt",
        ),
        Index("ix_workflow_task_states_run_id", "workflow_run_id"),
        Index(
            "ix_workflow_task_states_failed",
            "workflow_run_id",
            postgresql_where=text("status = 'failed'"),
        ),
        CheckConstraint(
            "task_name IS NOT NULL",
            name="ck_workflow_task_state_task_name_required",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[WorkflowTaskStatus] = mapped_column(
        Enum(
            WorkflowTaskStatus,
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
    worker_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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

    workflow_run: Mapped[WorkflowRun] = relationship(
        "WorkflowRun",
        back_populates="task_states",
    )


class ProviderCredentialSource(str, enum.Enum):
    OAUTH_VOLUME = "oauth_volume"
    SECRET_REF = "secret_ref"
    NONE = "none"


class RuntimeMaterializationMode(str, enum.Enum):
    OAUTH_HOME = "oauth_home"
    API_KEY_ENV = "api_key_env"
    ENV_BUNDLE = "env_bundle"
    CONFIG_BUNDLE = "config_bundle"
    COMPOSITE = "composite"


class ManagedAgentRateLimitPolicy(str, enum.Enum):
    """Rate limit handling policy for a managed agent auth profile."""

    BACKOFF = "backoff"
    QUEUE = "queue"
    FAIL_FAST = "fail_fast"


class ManagedAgentProviderProfile(Base):
    """Named provider configuration and execution policy for a managed agent runtime."""

    __tablename__ = "managed_agent_provider_profiles"
    __table_args__ = (
        Index("ix_provider_profiles_runtime", "runtime_id"),
        Index("ix_provider_profiles_provider", "provider_id"),
        Index("ix_provider_profiles_enabled", "enabled"),
    )

    profile_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    runtime_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown", server_default=text("'unknown'"))
    provider_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    default_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    model_overrides: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    credential_source: Mapped[ProviderCredentialSource] = mapped_column(
        Enum(
            ProviderCredentialSource,
            name="providercredentialsource",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ProviderCredentialSource.NONE,
        server_default=ProviderCredentialSource.NONE.value,
    )
    runtime_materialization_mode: Mapped[RuntimeMaterializationMode] = mapped_column(
        Enum(
            RuntimeMaterializationMode,
            name="runtimematerializationmode",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RuntimeMaterializationMode.COMPOSITE,
        server_default=RuntimeMaterializationMode.COMPOSITE.value,
    )

    account_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    
    tags: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default=text("100"))

    volume_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    volume_mount_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
    secret_refs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    clear_env_keys: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    env_template: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    file_templates: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    home_path_overrides: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    command_behavior: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    max_parallel_runs: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    cooldown_after_429_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=900, server_default=text("900")
    )
    rate_limit_policy: Mapped[ManagedAgentRateLimitPolicy] = mapped_column(
        Enum(
            ManagedAgentRateLimitPolicy,
            name="managedagentratelimitpolicy",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ManagedAgentRateLimitPolicy.BACKOFF,
        server_default=ManagedAgentRateLimitPolicy.BACKOFF.value,
    )
    max_lease_duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=7200, server_default=text("7200")
    )
    owner_user_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid, nullable=True
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


class OAuthSessionStatus(str, enum.Enum):
    """Lifecycle status for a managed agent OAuth session."""

    PENDING = "pending"
    STARTING = "starting"
    BRIDGE_READY = "bridge_ready"
    AWAITING_USER = "awaiting_user"
    VERIFYING = "verifying"
    REGISTERING_PROFILE = "registering_profile"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ManagedAgentOAuthSession(Base):
    """OAuth session for managed agents (browser runner transport removed)."""

    __tablename__ = "managed_agent_oauth_sessions"
    __table_args__ = (
        Index("ix_oauth_sessions_profile", "profile_id"),
        Index("ix_oauth_sessions_status", "status"),
    )

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    runtime_id: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_mode: Mapped[ProviderCredentialSource] = mapped_column(
        Enum(
            ProviderCredentialSource,
            name="providercredentialsource",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ProviderCredentialSource.OAUTH_VOLUME,
        server_default=ProviderCredentialSource.OAUTH_VOLUME.value,
    )
    session_transport: Mapped[str] = mapped_column(
        String(64), nullable=False, default="none", server_default=text("'none'")
    )
    volume_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    volume_mount_path: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    status: Mapped[OAuthSessionStatus] = mapped_column(
        Enum(
            OAuthSessionStatus,
            name="oauthsessionstatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=OAuthSessionStatus.PENDING,
        server_default=OAuthSessionStatus.PENDING.value,
    )
    requested_by_user_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    account_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    terminal_session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    terminal_bridge_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    container_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    worker_service: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentJobLiveSessionProvider(str, enum.Enum):
    NONE = "none"


class AgentJobLiveSessionStatus(str, enum.Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    READY = "ready"
    REVOKED = "revoked"
    ENDED = "ended"
    ERROR = "error"


class TaskRunLiveSession(Base):
    __tablename__ = "task_run_live_sessions"
    __table_args__ = (
        Index("ix_task_run_live_sessions_status_expires_at", "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_run_id: Mapped[UUID] = mapped_column(Uuid, unique=True, index=True)
    provider: Mapped[AgentJobLiveSessionProvider] = mapped_column(
        Enum(
            AgentJobLiveSessionProvider,
            name="agentjoblivesessionprovider",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            validate_strings=True,
        )
    )
    status: Mapped[AgentJobLiveSessionStatus] = mapped_column(
        Enum(
            AgentJobLiveSessionStatus,
            name="agentjoblivesessionstatus",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            validate_strings=True,
        )
    )
    ready_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    worker_hostname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    live_session_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    live_session_socket_path: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    attach_ro: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attach_rw_encrypted: Mapped[Optional[str]] = mapped_column(
        StringEncryptedType(key=get_encryption_key), nullable=True
    )
    web_ro: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    web_rw_encrypted: Mapped[Optional[str]] = mapped_column(
        StringEncryptedType(key=get_encryption_key), nullable=True
    )
    rw_granted_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProviderProfileSlotLease(Base):
    """Persisted slot lease state for ProviderProfileManager crash recovery.

    This table stores the active slot leases in the DB so they survive
    manager restarts. When the ProviderProfileManager starts, it loads leases
    from this table and sends slot_assigned to any running workflows.
    """

    __tablename__ = "provider_profile_slot_leases"
    __table_args__ = (
        Index("ix_provider_slot_leases_runtime", "runtime_id"),
        Index("ix_provider_slot_leases_workflow", "workflow_id"),
        UniqueConstraint("runtime_id", "workflow_id", name="uq_provider_slot_lease_runtime_workflow"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runtime_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentSkillSourceKind(str, enum.Enum):
    """Source provenance for a resolved skill."""

    BUILT_IN = "built_in"
    DEPLOYMENT = "deployment"
    REPO = "repo"
    LOCAL = "local"


class AgentSkillFormat(str, enum.Enum):
    """Supported payload formatting inside a given skill version."""

    MARKDOWN = "markdown"
    BUNDLE = "bundle"


class AgentSkillDefinition(Base):
    """Core definition for a reusable agent skill block/bundle."""

    __tablename__ = "agent_skill_definitions"
    __table_args__ = (
        Index("ix_agent_skill_definitions_slug", "slug", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    versions: Mapped[list["AgentSkillVersion"]] = relationship(
        "AgentSkillVersion",
        back_populates="skill",
        cascade="all, delete-orphan",
        order_by="AgentSkillVersion.created_at",
    )

    @property
    def latest_version(self) -> str | None:
        """Return the version string of the most recently created version."""
        return self.versions[-1].version_string if self.versions else None


class AgentSkillVersion(Base):
    """Immutable version release pointing to blob contents in artifact storage."""

    __tablename__ = "agent_skill_versions"
    __table_args__ = (
        UniqueConstraint(
            "skill_id", "version_string", name="uq_agent_skill_version_string"
        ),
        Index("ix_agent_skill_versions_skill", "skill_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_skill_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version_string: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[AgentSkillFormat] = mapped_column(
        Enum(
            AgentSkillFormat,
            name="agentskillformat",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=AgentSkillFormat.MARKDOWN,
    )
    artifact_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    skill: Mapped[AgentSkillDefinition] = relationship(
        "AgentSkillDefinition", back_populates="versions"
    )


class SkillSet(Base):
    """A collection of selected skills for task context grouping."""

    __tablename__ = "skill_sets"
    __table_args__ = (
        Index("ix_skill_sets_slug", "slug", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    entries: Mapped[list["SkillSetEntry"]] = relationship(
        "SkillSetEntry",
        back_populates="skill_set",
        cascade="all, delete-orphan",
    )


class SkillSetEntry(Base):
    """Membership rule explicitly including an agent skill block within a SkillSet."""

    __tablename__ = "skill_set_entries"
    __table_args__ = (
        UniqueConstraint("skill_set_id", "skill_id", name="uq_skill_set_entry"),
        Index("ix_skill_set_entries_set", "skill_set_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_set_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("skill_sets.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_skill_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version_constraint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    skill_set: Mapped[SkillSet] = relationship(
        "SkillSet", back_populates="entries"
    )
    skill: Mapped[AgentSkillDefinition] = relationship(
        "AgentSkillDefinition"
    )

    @property
    def skill_slug(self) -> str:
        """Return the slug of the associated skill."""
        return self.skill.slug


def _register_workflow_model_dependencies() -> None:
    """Import workflow ORM models so string relationships can resolve."""

    if TYPE_CHECKING:
        return

    import_module("moonmind.workflows.automation.models")


_register_workflow_model_dependencies()
