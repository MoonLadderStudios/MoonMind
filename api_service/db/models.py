"""Database models used by the MoonMind API service."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from moonmind.workflows.speckit_celery.models import SpecWorkflowTaskState

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates
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


Index("ix_orchestrator_runs_status", OrchestratorRun.status)
Index("ix_orchestrator_runs_target_service", OrchestratorRun.target_service)
Index("ix_orchestrator_run_artifacts_run_id", OrchestratorRunArtifact.run_id)
