from fastapi_users.db import SQLAlchemyBaseUserTable
from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase

from api_service.db.models import (
    Base,
    User,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)

def test_user_model_inheritance():
    """Test that the User model inherits from SQLAlchemyBaseUserTable and Base."""
    assert issubclass(User, SQLAlchemyBaseUserTable)
    assert issubclass(User, Base)

def test_user_model_columns():
    """Test that the User model has the expected columns."""
    inspector = inspect(User)
    columns = [column.key for column in inspector.columns]

    # Columns inherited from SQLAlchemyBaseUserTable
    assert "id" in columns
    assert "email" in columns
    assert "hashed_password" in columns
    assert "is_active" in columns
    assert "is_superuser" in columns
    assert "is_verified" in columns

    # Check types (optional, but good for completeness)
    # Note: id is a UUID/GUID type which doesn't implement python_type
    # We can check that it's a UUID-like type
    from sqlalchemy.dialects.postgresql import UUID

    id_type = inspector.columns["id"].type
    id_type_str = str(id_type).upper()

    # Check if it's a UUID, GUID, or has UUID-like characteristics
    is_uuid_like = (
        isinstance(id_type, UUID)
        or "GUID" in id_type_str
        or "UUID" in id_type_str
        or "CHAR(36)" in id_type_str  # GUID often renders as CHAR(36)
    )
    assert (
        is_uuid_like
    ), f"Expected UUID-like type, got {type(id_type)} with string representation '{id_type_str}'"

    assert inspector.columns["email"].type.python_type is str
    assert inspector.columns["hashed_password"].type.python_type is str
    assert inspector.columns["is_active"].type.python_type is bool
    assert inspector.columns["is_superuser"].type.python_type is bool
    assert inspector.columns["is_verified"].type.python_type is bool

def test_base_model_inheritance():
    """Test that the Base model inherits from DeclarativeBase."""
    assert issubclass(Base, DeclarativeBase)


def test_checkpoint_branch_persistence_models_expose_binding_columns():
    """MM-1090 requires product branch and git work branch to be persisted separately."""

    branch_columns = set(inspect(WorkflowCheckpointBranch).columns.keys())
    turn_columns = set(inspect(WorkflowCheckpointBranchTurn).columns.keys())
    binding_columns = set(inspect(WorkflowCheckpointBranchGitBinding).columns.keys())
    artifact_columns = set(inspect(WorkflowCheckpointBranchArtifact).columns.keys())

    assert {"branch_id", "workspace_policy", "git_work_branch"} <= branch_columns
    assert {
        "current_head_checkpoint_digest",
        "current_head_version",
        "current_head_attempt_ordinal",
        "remediation_loop_id",
        "remediation_head_status",
        "latest_verification_ref",
        "latest_verification_verdict",
    } <= branch_columns
    assert {
        "branch_turn_id",
        "branch_id",
        "workspace_policy",
        "git_work_branch",
        "workspace_restore_ref",
        "git_binding_ref",
        "step_execution_manifest_ref",
    } <= turn_columns
    assert {
        "branch_id",
        "repository",
        "base_branch",
        "base_commit",
        "work_branch",
        "worktree_ref",
        "provider_workspace_ref",
        "workspace_policy",
        "creation_mode",
        "binding_metadata",
    } <= binding_columns
    assert {
        "branch_id",
        "branch_turn_id",
        "artifact_kind",
        "artifact_ref",
        "content_type",
    } <= artifact_columns
