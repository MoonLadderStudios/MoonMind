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
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    literal_column,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    reconstructor,
    validates,
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

def _runtime_default_model_tier() -> dict[str, Any]:
    return {
        "label": "Runtime default",
        "model": None,
        "effort": None,
        "parameters": {},
        "annotations": {},
    }

def _provider_profile_model_tiers_default_for_values(
    default_model: str | None,
    default_effort: str | None,
) -> list[dict[str, Any]]:
    if default_model is not None or default_effort is not None:
        return [
            {
                "label": "Legacy default",
                "model": default_model,
                "effort": default_effort,
                "parameters": {},
                "annotations": {},
            }
        ]
    return [_runtime_default_model_tier()]

def _provider_profile_model_tiers_default(context: Any = None) -> list[dict[str, Any]]:
    params = context.get_current_parameters() if context is not None else {}
    return _provider_profile_model_tiers_default_for_values(
        params.get("default_model"),
        params.get("default_effort"),
    )

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

class RecurringWorkflowScheduleType(str, enum.Enum):
    """Supported recurring definition schedule kinds."""

    CRON = "cron"

class RecurringWorkflowScopeType(str, enum.Enum):
    """Scope ownership for recurring definitions."""

    PERSONAL = "personal"
    GLOBAL = "global"

class RecurringWorkflowRunOutcome(str, enum.Enum):
    """Dispatch result state for one recurring run decision."""

    PENDING_DISPATCH = "pending_dispatch"
    ENQUEUED = "enqueued"
    SKIPPED = "skipped"
    DISPATCH_ERROR = "dispatch_error"

class RecurringWorkflowRunTrigger(str, enum.Enum):
    """How one recurring run row was created."""

    SCHEDULE = "schedule"
    MANUAL = "manual"

class RecurringWorkflowDefinition(Base):
    """Persistent recurring schedule definition."""

    # legacy_run contract: table/index/enum-type names rename in WP7
    __tablename__ = "recurring_workflow_definitions"
    __table_args__ = (
        Index(
            "ix_recurring_workflow_definitions_enabled_next_run_at",
            "enabled",
            "next_run_at",
        ),
        Index(
            "ix_recurring_workflow_definitions_owner_enabled",
            "owner_user_id",
            "enabled",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schedule_type: Mapped[RecurringWorkflowScheduleType] = mapped_column(
        Enum(
            RecurringWorkflowScheduleType,
            name="recurringworkflowscheduletype",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RecurringWorkflowScheduleType.CRON,
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
    scope_type: Mapped[RecurringWorkflowScopeType] = mapped_column(
        Enum(
            RecurringWorkflowScopeType,
            name="recurringworkflowscopetype",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RecurringWorkflowScopeType.PERSONAL,
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

    runs: Mapped[list["RecurringWorkflowRun"]] = relationship(
        "RecurringWorkflowRun",
        back_populates="definition",
        cascade="all, delete-orphan",
    )

class RecurringWorkflowRun(Base):
    """Persistent recurring run dispatch decision row."""

    # legacy_run contract: table/index/enum-type names rename in WP7
    __tablename__ = "recurring_workflow_runs"
    __table_args__ = (
        UniqueConstraint(
            "definition_id",
            "scheduled_for",
            name="uq_recurring_workflow_runs_definition_scheduled_for",
        ),
        Index(
            "ix_recurring_workflow_runs_definition_created_at",
            "definition_id",
            "created_at",
        ),
        Index(
            "ix_recurring_workflow_runs_outcome_dispatch_after",
            "outcome",
            "dispatch_after",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    definition_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("recurring_workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    trigger: Mapped[RecurringWorkflowRunTrigger] = mapped_column(
        Enum(
            RecurringWorkflowRunTrigger,
            name="recurringworkflowruntrigger",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RecurringWorkflowRunTrigger.SCHEDULE,
    )
    outcome: Mapped[RecurringWorkflowRunOutcome] = mapped_column(
        Enum(
            RecurringWorkflowRunOutcome,
            name="recurringworkflowrunoutcome",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=RecurringWorkflowRunOutcome.PENDING_DISPATCH,
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
    definition: Mapped[RecurringWorkflowDefinition] = relationship(
        "RecurringWorkflowDefinition",
        back_populates="runs",
    )


class OmnigentBridgeSession(Base):
    """Canonical Omnigent bridge session, idempotency, and evidence-ref store.

    Source design: docs/Omnigent/OmnigentBridge.md §7.1 (MM-1152, source MM-1140).
    Supersedes the ``omnigent_external_runs`` mapping formerly owned by
    ``OmnigentRunStore``; there is no parallel table, alias, or wrapper.
    """

    __tablename__ = "omnigent_bridge_sessions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_omnigent_bridge_sessions_idempotency_key"),
        Index("ix_omnigent_bridge_sessions_session", "omnigent_session_id"),
        Index("ix_omnigent_bridge_sessions_workflow", "moonmind_workflow_id"),
        Index("ix_omnigent_bridge_sessions_status", "status"),
    )

    bridge_session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    compatibility_profile: Mapped[str] = mapped_column(String(128), nullable=False)
    moonmind_workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    moonmind_run_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    moonmind_agent_run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    step_execution_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)

    provider_profile_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider_lease_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    credential_generation: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    host_binding_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    host_lease_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    effective_launch_snapshot_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )

    omnigent_endpoint_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    omnigent_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    omnigent_host_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    omnigent_runner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    omnigent_agent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    omnigent_agent_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    host_type: Mapped[str] = mapped_column(String(32), nullable=False)
    workspace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="declared", server_default="declared"
    )

    first_message_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="not_prepared",
        server_default="not_prepared",
    )
    first_message_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_message_marker: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_message_post_attempted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_message_posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_message_pending_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_message_item_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    raw_events_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    normalized_events_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    initial_snapshot_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    final_snapshot_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    capture_manifest_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    diagnostics_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    external_state_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    terminal_refs: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", mutable_json_dict(), nullable=False, default=dict
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


class OmnigentBridgeSessionEvent(Base):
    """Durable index over the Omnigent bridge session event stream.

    Source design: docs/Omnigent/OmnigentBridge.md §7.2 (MM-1152, source MM-1140).
    The DB rows are an index; full raw/normalized event bodies live in MoonMind
    artifacts. ``normalized_status`` preserves the full, non-lossy normalized
    status stream (contrasted with the coarse, coalesced session ``status``).
    """

    __tablename__ = "omnigent_bridge_session_events"
    __table_args__ = (
        Index("ix_omnigent_bridge_session_events_session", "bridge_session_id"),
        Index(
            "ix_omnigent_bridge_session_events_sequence",
            "bridge_session_id",
            "sequence",
            unique=True,
        ),
        Index(
            "ix_omnigent_bridge_session_events_dedup",
            "bridge_session_id",
            "deduplication_key",
            unique=True,
        ),
    )

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    bridge_session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    text_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", mutable_json_dict(), nullable=False, default=dict
    )


class WorkflowCheckpointBranch(Base):
    """Product-level checkpoint branch persisted separately from git refs."""

    __tablename__ = "workflow_checkpoint_branches"
    __table_args__ = (
        Index("ix_checkpoint_branches_workflow", "workflow_id"),
        Index("ix_checkpoint_branches_checkpoint", "source_checkpoint_ref"),
        Index("ix_checkpoint_branches_state", "state"),
    )

    branch_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    root_workflow_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_run_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    logical_step_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_execution_ordinal: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_checkpoint_boundary: Mapped[str] = mapped_column(String(64), nullable=False)
    source_checkpoint_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_checkpoint_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_state_kind: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_state_ref: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_state_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    parent_branch_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("workflow_checkpoint_branches.branch_id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_turn_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(
        String(64), nullable=False, default="created", server_default="created"
    )
    branch_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default="root", server_default="root"
    )
    workspace_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_context_policy: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    git_repository: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    git_base_branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    git_base_commit: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    git_work_branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    current_head_step_execution_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    current_head_checkpoint_ref: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    current_head_commit: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pull_request_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    publish_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    promotion_evidence: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    archive_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    artifact_refs: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    diagnostics: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    turns: Mapped[list["WorkflowCheckpointBranchTurn"]] = relationship(
        "WorkflowCheckpointBranchTurn",
        back_populates="branch",
        cascade="all, delete-orphan",
        foreign_keys=lambda: [WorkflowCheckpointBranchTurn.branch_id],
        order_by="WorkflowCheckpointBranchTurn.created_at",
    )
    git_binding: Mapped[Optional["WorkflowCheckpointBranchGitBinding"]] = relationship(
        "WorkflowCheckpointBranchGitBinding",
        back_populates="branch",
        cascade="all, delete-orphan",
        uselist=False,
    )
    artifacts: Mapped[list["WorkflowCheckpointBranchArtifact"]] = relationship(
        "WorkflowCheckpointBranchArtifact",
        back_populates="branch",
        cascade="all, delete-orphan",
        order_by="WorkflowCheckpointBranchArtifact.created_at",
    )


class WorkflowCheckpointBranchTurn(Base):
    """Immutable instruction-bearing turn for a checkpoint branch."""

    __tablename__ = "workflow_checkpoint_branch_turns"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_checkpoint_branch_turn_idempotency_key"
        ),
        Index("ix_checkpoint_branch_turns_branch", "branch_id"),
        Index("ix_checkpoint_branch_turns_status", "status"),
    )

    branch_turn_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    branch_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("workflow_checkpoint_branches.branch_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_turn_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey(
            "workflow_checkpoint_branch_turns.branch_turn_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    source_checkpoint_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_checkpoint_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_state_kind: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_state_ref: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_state_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    instruction_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    instruction_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    context_bundle_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    workspace_policy: Mapped[str] = mapped_column(String(96), nullable=False)
    runtime_context_policy: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    git_work_branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    workspace_restore_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    git_binding_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    step_execution_manifest_ref: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    created_step_execution_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    runtime_agent_run_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="preparing", server_default="preparing"
    )
    diagnostics: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    branch: Mapped[WorkflowCheckpointBranch] = relationship(
        "WorkflowCheckpointBranch",
        back_populates="turns",
        foreign_keys=[branch_id],
    )


class WorkflowCheckpointBranchGitBinding(Base):
    """Git/worktree/provider workspace binding for one product checkpoint branch."""

    __tablename__ = "workflow_checkpoint_branch_git_bindings"
    __table_args__ = (
        UniqueConstraint(
            "repository",
            "work_branch",
            name="uq_checkpoint_branch_git_binding_work_branch",
        ),
        Index("ix_checkpoint_branch_git_bindings_repository", "repository"),
    )

    branch_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("workflow_checkpoint_branches.branch_id", ondelete="CASCADE"),
        primary_key=True,
    )
    repository: Mapped[str] = mapped_column(String(512), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_commit: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    work_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    worktree_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    provider_workspace_ref: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    head_commit: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    patch_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    pull_request_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    workspace_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    creation_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    publish_status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="unpublished",
        server_default="unpublished",
    )
    binding_metadata: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
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

    branch: Mapped[WorkflowCheckpointBranch] = relationship(
        "WorkflowCheckpointBranch", back_populates="git_binding"
    )


class WorkflowCheckpointBranchArtifact(Base):
    """Artifact refs attached to checkpoint branch or branch-turn evidence."""

    __tablename__ = "workflow_checkpoint_branch_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "branch_id",
            "branch_turn_id",
            "artifact_kind",
            name="uq_checkpoint_branch_artifact_kind",
        ),
        Index("ix_checkpoint_branch_artifacts_branch", "branch_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    branch_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("workflow_checkpoint_branches.branch_id", ondelete="CASCADE"),
        nullable=False,
    )
    branch_turn_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("workflow_checkpoint_branch_turns.branch_turn_id", ondelete="CASCADE"),
        nullable=True,
    )
    artifact_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    branch: Mapped[WorkflowCheckpointBranch] = relationship(
        "WorkflowCheckpointBranch", back_populates="artifacts"
    )


__all__ = [
    "Base",
    "User",
    "UserProfile",
    "ManifestRecord",
    "RecurringWorkflowDefinition",
    "RecurringWorkflowRun",
    "RecurringWorkflowRunOutcome",
    "RecurringWorkflowRunTrigger",
    "RecurringWorkflowScheduleType",
    "RecurringWorkflowScopeType",
    "OmnigentBridgeSession",
    "OmnigentBridgeSessionEvent",
    "WorkflowCheckpointBranch",
    "WorkflowCheckpointBranchTurn",
    "WorkflowCheckpointBranchGitBinding",
    "WorkflowCheckpointBranchArtifact",
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
    "PresetScopeType",
    "PresetReleaseStatus",
    "Preset",
    "PresetFavorite",
    "PresetRecent",
    "SecretStatus",
    "ManagedSecret",
    "AgentSkillSourceKind",
    "AgentSkillFormat",
    "AgentSkillDefinition",
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


_DEFAULT_SETTINGS_SUBJECT_ID = "00000000-0000-0000-0000-000000000000"


class SettingsOverride(Base):
    """Sparse persisted user/workspace override for one settings key."""

    __tablename__ = "settings_overrides"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "workspace_id",
            "user_id",
            "key",
            name="uq_settings_overrides_scope_subject_key",
        ),
        CheckConstraint(
            "scope in ('user', 'workspace')",
            name="ck_settings_overrides_scope",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid,
        nullable=False,
        default=lambda: UUID(_DEFAULT_SETTINGS_SUBJECT_ID),
        server_default=text(f"'{_DEFAULT_SETTINGS_SUBJECT_ID}'"),
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        nullable=False,
        default=lambda: UUID(_DEFAULT_SETTINGS_SUBJECT_ID),
        server_default=text(f"'{_DEFAULT_SETTINGS_SUBJECT_ID}'"),
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value_json: Mapped[Any | None] = mapped_column(_json_variant(), nullable=True)
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    value_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SettingsAuditEvent(Base):
    """Durable non-secret settings change audit event."""

    __tablename__ = "settings_audit_events"
    __table_args__ = (
        Index("ix_settings_audit_events_key_scope", "key", "scope"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid,
        nullable=False,
        default=lambda: UUID(_DEFAULT_SETTINGS_SUBJECT_ID),
        server_default=text(f"'{_DEFAULT_SETTINGS_SUBJECT_ID}'"),
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        nullable=False,
        default=lambda: UUID(_DEFAULT_SETTINGS_SUBJECT_ID),
        server_default=text(f"'{_DEFAULT_SETTINGS_SUBJECT_ID}'"),
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    old_value_json: Mapped[Any | None] = mapped_column(_json_variant(), nullable=True)
    new_value_json: Mapped[Any | None] = mapped_column(_json_variant(), nullable=True)
    redacted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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

class PresetScopeType(str, enum.Enum):
    """Scope owner for preset visibility."""

    GLOBAL = "global"
    PERSONAL = "personal"

class PresetReleaseStatus(str, enum.Enum):
    """Lifecycle state for current preset definitions."""

    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"

class Preset(Base):
    """Top-level catalog entry for reusable presets."""

    # legacy_run contract: table/index/constraint names rename in WP7
    __tablename__ = "presets"
    __table_args__ = (
        UniqueConstraint(
            "slug", "scope_type", "scope_ref", name="uq_preset_slug_scope"
        ),
        Index("ix_presets_scope", "scope_type", "scope_ref"),
        Index("ix_presets_slug", "slug"),
        Index("ix_presets_active", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_type: Mapped[PresetScopeType] = mapped_column(
        Enum(
            PresetScopeType,
            name="presetscopetype",
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
    inputs_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        mutable_json_list(), nullable=False, default=list
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        mutable_json_list(), nullable=False, default=list
    )
    annotations: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    max_step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    release_status: Mapped[PresetReleaseStatus] = mapped_column(
        Enum(
            PresetReleaseStatus,
            name="presetreleasestatus",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=PresetReleaseStatus.DRAFT,
    )
    reviewed_by: Mapped[Optional[UUID]] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    seed_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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

    favorites: Mapped[list["PresetFavorite"]] = relationship(
        "PresetFavorite",
        back_populates="template",
        cascade="all, delete-orphan",
    )

class PresetFavorite(Base):
    """User favorites for quick preset access."""

    # legacy_run contract: table/index/constraint names rename in WP7
    __tablename__ = "preset_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "template_id", name="uq_preset_favorite"),
        Index("ix_preset_favorites_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("presets.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    template: Mapped[Preset] = relationship(
        "Preset", back_populates="favorites"
    )

class PresetRecent(Base):
    """Tracks most recent template applications per user."""

    # legacy_run contract: table/index/constraint names rename in WP7
    __tablename__ = "preset_recents"
    __table_args__ = (
        Index("ix_preset_recents_user", "user_id"),
        UniqueConstraint(
            "user_id",
            "template_id",
            name="uq_preset_recent_user_template",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("presets.id", ondelete="CASCADE"),
        nullable=False,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    template: Mapped[Preset] = relationship("Preset")

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
from moonmind.statuses.close_status import TemporalExecutionCloseStatus
from moonmind.statuses.checkpoint_branch import (
    CheckpointBranchState,
    CheckpointBranchTurnState,
)
from moonmind.statuses.workflow import MoonMindWorkflowState

class TemporalWorkflowType(str, enum.Enum):
    """Supported root workflow type catalog entries."""

    USER_WORKFLOW = "MoonMind.UserWorkflow"
    MANIFEST_INGEST = "MoonMind.ManifestIngest"
    PROVIDER_PROFILE_MANAGER = "MoonMind.ProviderProfileManager"

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

    workflow_id: Mapped[str] = mapped_column(String(255), primary_key=True)
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
    finish_outcome_code: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    finish_summary_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
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

class CheckpointBranchKind(str, enum.Enum):
    """Product-level checkpoint branch type."""

    ROOT = "root"
    CHILD_FORK = "child_fork"


class CheckpointBranchWorkspacePolicy(str, enum.Enum):
    """Workspace preparation policy recorded for branch work."""

    CONTINUE_FROM_PREVIOUS_EXECUTION = "continue_from_previous_execution"
    RESTORE_PRE_EXECUTION = "restore_pre_execution"
    APPLY_PREVIOUS_EXECUTION_DIFF_TO_CLEAN_BASELINE = (
        "apply_previous_execution_diff_to_clean_baseline"
    )
    START_FROM_LAST_PASSED_COMMIT = "start_from_last_passed_commit"
    FRESH_BRANCH_FROM_SOURCE = "fresh_branch_from_source"


class CheckpointBranchRuntimeContextPolicy(str, enum.Enum):
    """Runtime/session context policy recorded for branch work."""

    FRESH_AGENT_RUN = "fresh_agent_run"
    REUSE_SESSION_NEW_EPOCH = "reuse_session_new_epoch"
    REUSE_SESSION_SAME_EPOCH = "reuse_session_same_epoch"
    EXTERNAL_PROVIDER_CONTINUATION = "external_provider_continuation"


class CheckpointBranchPublishStatus(str, enum.Enum):
    """Publication lifecycle for git bindings associated with a product branch."""

    UNPUBLISHED = "unpublished"
    PREPARING = "preparing"
    PUBLISHED = "published"
    FAILED = "failed"
    ARCHIVED = "archived"


class TemporalExecutionDependency(Base):
    """Durable direct dependency edge between top-level executions."""

    __tablename__ = "execution_dependencies"
    __table_args__ = (
        Index(
            "ix_execution_dependencies_dependent_workflow_id",
            "dependent_workflow_id",
        ),
        Index(
            "ix_execution_dependencies_prerequisite_workflow_id",
            "prerequisite_workflow_id",
        ),
    )

    dependent_workflow_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("temporal_execution_sources.workflow_id", ondelete="CASCADE"),
        primary_key=True,
    )
    prerequisite_workflow_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("temporal_execution_sources.workflow_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

class TemporalExecutionRemediationLink(Base):
    """Durable directed relationship from a remediation run to its target."""

    __tablename__ = "execution_remediation_links"
    __table_args__ = (
        Index(
            "ix_execution_remediation_links_target_workflow_id",
            "target_workflow_id",
        ),
    )

    remediation_workflow_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("temporal_execution_sources.workflow_id", ondelete="CASCADE"),
        primary_key=True,
    )
    remediation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_workflow_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("temporal_execution_sources.workflow_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    authority_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="created", server_default="created"
    )
    trigger_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    context_artifact_ref: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("temporal_artifacts.artifact_id", ondelete="SET NULL"),
        nullable=True,
    )
    active_lock_scope: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    active_lock_holder: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    latest_action_summary: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    outcome: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    mutation_guard_lock_state: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
    )
    mutation_guard_ledger_state: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
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

class WorkflowCheckpointBranchOperation(Base):
    """Idempotency ledger for branch side-effecting operations."""

    __tablename__ = "workflow_checkpoint_branch_operations"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "idempotency_key",
            name="uq_workflow_checkpoint_branch_operations_workflow_idempotency",
        ),
        Index(
            "ix_workflow_checkpoint_branch_operations_branch",
            "branch_id",
            "operation",
        ),
    )

    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("temporal_execution_sources.workflow_id", ondelete="CASCADE"),
        nullable=False,
    )
    branch_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    branch_turn_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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

    workflow_id: Mapped[str] = mapped_column(String(255), primary_key=True)
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
    finish_outcome_code: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    finish_summary_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        mutable_json_dict(), nullable=True
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
        String(255),
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

class WorkflowExecutionSourceMapping(Base):
    """Persisted global workflow execution index for canonical source resolution."""

    __tablename__ = "workflow_execution_source_mappings"
    __table_args__ = (
        Index("ix_workflow_execution_source_mappings_source_entry", "source", "entry"),
        Index(
            "ix_workflow_execution_source_mappings_source_record_id",
            "source",
            "source_record_id",
        ),
    )

    workflow_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    entry: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
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

    @validates("workflow_id")
    def _validate_workflow_id(self, _key: str, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("workflow_id is required")
        return normalized

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
    """Rate limit handling policy for a managed agent provider profile."""

    BACKOFF = "backoff"
    QUEUE = "queue"
    FAIL_FAST = "fail_fast"

class ProviderProfileAuthState(str, enum.Enum):
    """Persisted provider-profile credential activation state."""

    NOT_CONFIGURED = "not_configured"
    OAUTH_PENDING = "oauth_pending"
    API_KEY_PENDING = "api_key_pending"
    CONNECTED = "connected"
    VALIDATION_FAILED = "validation_failed"
    DISCONNECTED = "disconnected"

class ProviderProfileDisabledReason(str, enum.Enum):
    """Persisted reason a provider profile is disabled."""

    MISSING_CREDENTIALS = "missing_credentials"
    AUTH_INVALID = "auth_invalid"
    USER_DISABLED = "user_disabled"
    POLICY_DISABLED = "policy_disabled"
    DISCONNECTED = "disconnected"

class ProviderProfileAuthMethod(str, enum.Enum):
    """Last verified auth method for provider-profile activation."""

    OAUTH_VOLUME = "oauth_volume"
    SECRET_REF = "secret_ref"
    MANUAL = "manual"

class ManagedAgentProviderProfile(Base):
    """Named provider configuration and execution policy for a managed agent runtime."""

    __tablename__ = "managed_agent_provider_profiles"
    __table_args__ = (
        Index("ix_provider_profiles_runtime", "runtime_id"),
        Index("ix_provider_profiles_provider", "provider_id"),
        Index("ix_provider_profiles_runtime_provider", "runtime_id", "provider_id"),
        Index("ix_provider_profiles_enabled", "enabled"),
        Index("ix_provider_profiles_auth_state", "auth_state"),
        Index(
            "ix_provider_profiles_readiness",
            "runtime_id",
            "provider_id",
            "enabled",
            "auth_state",
        ),
        CheckConstraint(
            "auth_state IN ("
            "'not_configured', 'oauth_pending', 'api_key_pending', "
            "'connected', 'validation_failed', 'disconnected'"
            ")",
            name="ck_provider_profiles_auth_state",
        ),
        CheckConstraint(
            "disabled_reason IS NULL OR disabled_reason IN ("
            "'missing_credentials', 'auth_invalid', 'user_disabled', "
            "'policy_disabled', 'disconnected'"
            ")",
            name="ck_provider_profiles_disabled_reason",
        ),
        CheckConstraint(
            "last_auth_method IS NULL OR last_auth_method IN ("
            "'oauth_volume', 'secret_ref', 'manual'"
            ")",
            name="ck_provider_profiles_last_auth_method",
        ),
        CheckConstraint(
            "default_model_tier >= 1",
            name="ck_provider_profiles_default_model_tier_positive",
        ),
        CheckConstraint(
            "NOT (runtime_id = 'codex_cli' AND credential_source = 'oauth_volume' "
            "AND runtime_materialization_mode = 'oauth_home') OR max_parallel_runs = 1",
            name="ck_provider_profiles_codex_oauth_exclusive_capacity",
        ),
        CheckConstraint(
            "credential_generation >= 1",
            name="ck_provider_profiles_credential_generation_positive",
        ),
        Index(
            "ux_provider_profiles_runtime_default",
            "runtime_id",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default = true"),
        ),
    )

    profile_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    credential_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    runtime_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown", server_default=text("'unknown'"))
    provider_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    default_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    default_effort: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model_tiers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=_provider_profile_model_tiers_default,
        server_default=literal_column(
            """'[{"label":"Runtime default","model":null,"effort":null,"parameters":{},"annotations":{}}]'"""
        ),
    )
    default_model_tier: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    model_overrides: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if kwargs.get("model_tiers") is None:
            self.model_tiers = _provider_profile_model_tiers_default_for_values(
                self.default_model,
                self.default_effort,
            )

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
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    tags: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default=text("100"))

    auth_state: Mapped[ProviderProfileAuthState] = mapped_column(
        Enum(
            ProviderProfileAuthState,
            name="providerprofileauthstate",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ProviderProfileAuthState.NOT_CONFIGURED,
        server_default=ProviderProfileAuthState.NOT_CONFIGURED.value,
    )
    disabled_reason: Mapped[Optional[ProviderProfileDisabledReason]] = mapped_column(
        Enum(
            ProviderProfileDisabledReason,
            name="providerprofiledisabledreason",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    first_authenticated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_auth_method: Mapped[Optional[ProviderProfileAuthMethod]] = mapped_column(
        Enum(
            ProviderProfileAuthMethod,
            name="providerprofileauthmethod",
            native_enum=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=True,
    )

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
    lease_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purpose: Mapped[str] = mapped_column(
        String(64), nullable=False, default="execution_direct", server_default=text("'execution_direct'")
    )
    owner_is_workflow: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    step_execution_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    oauth_session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OmnigentOAuthHostBindingRecord(Base):
    """Durable, secret-free configuration for one profile-bound OAuth host."""

    __tablename__ = "omnigent_oauth_host_bindings"
    __table_args__ = (
        UniqueConstraint("provider_profile_id", name="uq_omnigent_oauth_binding_profile"),
        UniqueConstraint(
            "binding_ref",
            "provider_profile_id",
            name="uq_omnigent_oauth_binding_ref_profile",
        ),
    )

    binding_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider_profile_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("managed_agent_provider_profiles.profile_id", ondelete="CASCADE"), nullable=False
    )
    endpoint_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    harness: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_mount_template_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    static_host_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    host_launch_profile_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    execution_profile_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    launch_policy_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    effective_launch_snapshot_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.validate_credential_mount_ref()

    @reconstructor
    def _validate_loaded_credential_mount_ref(self) -> None:
        self.validate_credential_mount_ref()

    def validate_credential_mount_ref(self) -> None:
        """Validate the portable mount contract on construction and ORM load."""

        from moonmind.schemas.agent_runtime_models import CredentialMountRef

        mount_ref = CredentialMountRef.model_validate(
            self.credential_mount_template_json
        )
        if mount_ref.auth_volume_ref.provider_profile_id != self.provider_profile_id:
            raise ValueError(
                "credential_mount_template_json must belong to provider_profile_id"
            )


class OmnigentHostAuthProfileRecord(Base):
    """Singleton durable owner for safe embedded host-auth lifecycle metadata."""

    __tablename__ = "omnigent_host_auth_profiles"

    profile_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OmnigentOAuthHostLeaseRecord(Base):
    """Durable lifecycle state for the single host consuming an OAuth profile."""

    __tablename__ = "omnigent_oauth_host_leases"
    __table_args__ = (
        UniqueConstraint("provider_lease_id", name="uq_omnigent_oauth_host_provider_lease"),
        UniqueConstraint("idempotency_key", name="uq_omnigent_oauth_host_idempotency"),
        CheckConstraint(
            "status IN ('allocating','starting','ready','assigned','draining','stopped','failed')",
            name="ck_omnigent_oauth_host_lease_status",
        ),
        ForeignKeyConstraint(
            ["binding_ref", "provider_profile_id"],
            [
                "omnigent_oauth_host_bindings.binding_ref",
                "omnigent_oauth_host_bindings.provider_profile_id",
            ],
            name="fk_omnigent_oauth_host_lease_binding_profile",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "credential_generation >= 1",
            name="ck_omnigent_oauth_host_lease_generation",
        ),
        CheckConstraint(
            "host_auth_generation IS NULL OR host_auth_generation >= 1",
            name="ck_omnigent_oauth_host_lease_host_auth_generation",
        ),
        CheckConstraint(
            "expires_at > acquired_at",
            name="ck_omnigent_oauth_host_lease_expiry",
        ),
        Index(
            "ux_omnigent_oauth_host_lease_active_profile",
            "provider_profile_id",
            unique=True,
            sqlite_where=text("status IN ('allocating','starting','ready','assigned','draining')"),
            postgresql_where=text("status IN ('allocating','starting','ready','assigned','draining')"),
        ),
        Index("ix_omnigent_oauth_host_lease_profile", "provider_profile_id"),
        Index("ix_omnigent_oauth_host_lease_workflow", "holder_workflow_id"),
        Index("ix_omnigent_oauth_host_lease_expiry", "expires_at"),
    )

    lease_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider_profile_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("managed_agent_provider_profiles.profile_id", ondelete="CASCADE"), nullable=False
    )
    provider_lease_id: Mapped[str] = mapped_column(String(255), nullable=False)
    binding_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    # Embedded host/server authentication is intentionally distinct from the
    # Provider Profile OAuth credential generation above.
    host_auth_profile_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    host_auth_generation: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    holder_workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_run_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    lease_purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    container_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    container_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    omnigent_host_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    omnigent_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bridge_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    effective_launch_snapshot_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    host_capabilities_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    host_readiness: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ready_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    draining_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cleanup_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    def validate_binding_generation(
        self,
        *,
        binding: OmnigentOAuthHostBindingRecord,
        profile: ManagedAgentProviderProfile,
    ) -> None:
        """Reject a lease that does not match its binding and credential generation."""

        binding.validate_credential_mount_ref()
        from moonmind.schemas.agent_runtime_models import CredentialMountRef

        mount_ref = CredentialMountRef.model_validate(
            binding.credential_mount_template_json
        )
        mount_generation = mount_ref.auth_volume_ref.credential_generation
        if (
            binding.binding_ref != self.binding_ref
            or binding.provider_profile_id != self.provider_profile_id
            or profile.profile_id != self.provider_profile_id
        ):
            raise ValueError("host lease binding must belong to provider_profile_id")
        if (
            self.credential_generation != mount_generation
            or self.credential_generation != profile.credential_generation
        ):
            raise ValueError(
                "host lease credential_generation must match binding and profile"
            )


@event.listens_for(OmnigentOAuthHostLeaseRecord, "before_insert")
@event.listens_for(OmnigentOAuthHostLeaseRecord, "before_update")
def _validate_omnigent_oauth_host_lease_generation(
    _mapper: Any,
    connection: Any,
    target: OmnigentOAuthHostLeaseRecord,
) -> None:
    """Fail closed when a host lease carries stale credential identity."""

    binding_table = OmnigentOAuthHostBindingRecord.__table__
    profile_table = ManagedAgentProviderProfile.__table__
    binding_row = connection.execute(
        select(
            binding_table.c.provider_profile_id,
            binding_table.c.credential_mount_template_json,
        ).where(binding_table.c.binding_ref == target.binding_ref)
    ).mappings().one_or_none()
    if binding_row is None:
        raise ValueError("host lease binding_ref does not exist")
    profile_generation = connection.execute(
        select(profile_table.c.credential_generation).where(
            profile_table.c.profile_id == target.provider_profile_id
        )
    ).scalar_one_or_none()
    if profile_generation is None:
        raise ValueError("host lease provider_profile_id does not exist")

    from moonmind.schemas.agent_runtime_models import CredentialMountRef

    mount_ref = CredentialMountRef.model_validate(
        binding_row["credential_mount_template_json"]
    )
    if (
        target.credential_generation
        != mount_ref.auth_volume_ref.credential_generation
        or target.credential_generation != profile_generation
    ):
        raise ValueError(
            "host lease credential_generation must match binding and profile"
        )

class AgentSkillSourceKind(str, enum.Enum):
    """Source provenance for a resolved skill."""

    BUILT_IN = "built_in"
    DEPLOYMENT = "deployment"
    REPO = "repo"
    LOCAL = "local"

class AgentSkillFormat(str, enum.Enum):
    """Supported payload formatting for skill content."""

    MARKDOWN = "markdown"
    BUNDLE = "bundle"

class AgentSkillDefinition(Base):
    """Current definition and content evidence for a reusable agent skill."""

    __tablename__ = "agent_skill_definitions"
    __table_args__ = (
        Index("ix_agent_skill_definitions_slug", "slug", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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
    artifact_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    content_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
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


def _default_container_job_auxiliary_outcome() -> dict[str, str]:
    from moonmind.schemas.container_job_models import AuxiliaryOutcomeState

    return {"state": AuxiliaryOutcomeState.NOT_ATTEMPTED.value}


class ContainerJobRecord(Base):
    """API-owned durable container-job identity and compact observations."""

    __tablename__ = "container_jobs"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "idempotency_key", name="uq_container_jobs_owner_idempotency"),
        Index("ix_container_jobs_owner_created", "owner_type", "owner_id", "created_at"),
    )

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_json: Mapped[dict[str, Any]] = mapped_column(mutable_json_dict(), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(mutable_json_dict(), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    backend_kind: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    backend_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    image_observation_json: Mapped[Optional[dict[str, Any]]] = mapped_column(mutable_json_dict(), nullable=True)
    authorization_observation_json: Mapped[Optional[dict[str, Any]]] = mapped_column(mutable_json_dict(), nullable=True)
    terminal_outcome_json: Mapped[Optional[dict[str, Any]]] = mapped_column(mutable_json_dict(), nullable=True)
    publication_outcome_json: Mapped[dict[str, Any]] = mapped_column(mutable_json_dict(), nullable=False, default=_default_container_job_auxiliary_outcome)
    cleanup_outcome_json: Mapped[dict[str, Any]] = mapped_column(mutable_json_dict(), nullable=False, default=_default_container_job_auxiliary_outcome)
    logs_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    artifacts_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    # Durable observability-event journal ref (terminal live-log fallback) and
    # compact non-sensitive execution observations (MoonLadderStudios/MoonMind#3258).
    events_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    workspace_probe: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cancel_idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

def _register_workflow_model_dependencies() -> None:
    """Import workflow ORM models so string relationships can resolve."""

    if TYPE_CHECKING:
        return

    import_module("moonmind.workflows.automation.models")

_register_workflow_model_dependencies()
