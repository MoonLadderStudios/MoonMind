from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase

from api_service.db.models import (
    Base,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)






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
