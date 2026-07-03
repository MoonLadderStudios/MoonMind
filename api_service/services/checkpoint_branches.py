"""Checkpoint branch launch preparation service for MM-1090.

The service keeps repository-mutating checkpoint branch preparation at an
activity/service boundary: it validates git isolation, emits launch evidence,
and persists the product-branch-to-git-branch binding before agent work starts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)
from moonmind.workflows.checkpoint_branches import (
    CHECKPOINT_BRANCH_GIT_BINDING_CONTENT_TYPE,
    CHECKPOINT_BRANCH_WORKSPACE_RESTORE_CONTENT_TYPE,
    CheckpointBranchGitBindingError,
    CheckpointBranchGitBindingInput,
    CheckpointBranchGitBindingResult,
    prepare_checkpoint_branch_git_binding,
)

CheckpointBranchArtifactWriter = Callable[
    [str, Mapping[str, Any], str], Awaitable[tuple[str, str | None]]
]


@dataclass(frozen=True)
class CheckpointBranchWorkspacePreparation:
    """Persisted checkpoint branch launch evidence."""

    branch_id: str
    branch_turn_id: str | None
    git_work_branch: str
    workspace_policy: str
    workspace_restore_ref: str
    git_binding_ref: str
    workspace_restore_digest: str | None
    git_binding_digest: str | None
    binding: CheckpointBranchGitBindingResult


async def prepare_checkpoint_branch_workspace(
    *,
    session: AsyncSession,
    binding_input: Mapping[str, Any],
    known_refs: set[str] | frozenset[str],
    current_ref: str | None,
    instruction_ref: str,
    instruction_digest: str,
    artifact_writer: CheckpointBranchArtifactWriter,
    source_checkpoint_boundary: str = "after_execution",
    root_workflow_id: str | None = None,
    source_run_id: str | None = None,
    source_execution_ordinal: int | None = None,
    parent_branch_id: str | None = None,
    parent_turn_id: str | None = None,
    runtime_context_policy: str | None = None,
    step_execution_manifest_ref: str | None = None,
    created_step_execution_id: str | None = None,
    created_at: datetime | None = None,
) -> CheckpointBranchWorkspacePreparation:
    """Validate, emit artifacts, and persist a checkpoint branch git binding."""

    try:
        validated_input = CheckpointBranchGitBindingInput.model_validate(binding_input)
    except ValueError as exc:
        raise CheckpointBranchGitBindingError(
            "invalid_binding", f"checkpoint branch git binding input invalid: {exc}"
        ) from exc

    existing_bindings = await _existing_bindings_by_work_branch(
        session=session,
        repository=validated_input.repository,
    )
    prepared = prepare_checkpoint_branch_git_binding(
        binding_input,
        known_refs=known_refs,
        existing_bindings_by_work_branch=existing_bindings,
        current_ref=current_ref,
        created_at=created_at or datetime.now(UTC),
    )
    workspace_restore_ref, workspace_restore_digest = await artifact_writer(
        "runtime.branch.workspace_restore.json",
        prepared.workspace_restore_payload,
        CHECKPOINT_BRANCH_WORKSPACE_RESTORE_CONTENT_TYPE,
    )
    git_binding_ref, git_binding_digest = await artifact_writer(
        "runtime.branch.git_binding.json",
        prepared.git_binding_payload,
        CHECKPOINT_BRANCH_GIT_BINDING_CONTENT_TYPE,
    )

    await _persist_prepared_checkpoint_branch(
        session=session,
        prepared=prepared,
        workspace_restore_ref=workspace_restore_ref,
        workspace_restore_digest=workspace_restore_digest,
        git_binding_ref=git_binding_ref,
        git_binding_digest=git_binding_digest,
        instruction_ref=instruction_ref,
        instruction_digest=instruction_digest,
        source_checkpoint_boundary=source_checkpoint_boundary,
        root_workflow_id=root_workflow_id,
        source_run_id=source_run_id,
        source_execution_ordinal=source_execution_ordinal,
        parent_branch_id=parent_branch_id,
        parent_turn_id=parent_turn_id,
        runtime_context_policy=runtime_context_policy,
        step_execution_manifest_ref=step_execution_manifest_ref,
        created_step_execution_id=created_step_execution_id,
    )

    return CheckpointBranchWorkspacePreparation(
        branch_id=prepared.binding.product_branch_id,
        branch_turn_id=prepared.binding.branch_turn_id,
        git_work_branch=prepared.binding.work_branch,
        workspace_policy=str(prepared.binding.workspace_policy),
        workspace_restore_ref=workspace_restore_ref,
        git_binding_ref=git_binding_ref,
        workspace_restore_digest=workspace_restore_digest,
        git_binding_digest=git_binding_digest,
        binding=prepared,
    )


async def _existing_bindings_by_work_branch(
    *, session: AsyncSession, repository: str
) -> dict[str, dict[str, str]]:
    if not repository:
        return {}
    result = await session.execute(
        select(WorkflowCheckpointBranchGitBinding).where(
            func.lower(WorkflowCheckpointBranchGitBinding.repository)
            == repository.lower()
        )
    )
    bindings: dict[str, dict[str, str]] = {}
    for binding in result.scalars():
        metadata = dict(binding.binding_metadata or {})
        ownership = dict(metadata.get("ownership") or {})
        bindings[binding.work_branch] = {
            "productBranchId": binding.branch_id,
            "repository": binding.repository,
            "idempotencyKey": str(ownership.get("idempotencyKey") or ""),
            "baseBranch": str(ownership.get("baseBranch") or binding.base_branch),
            "baseCommit": str(ownership.get("baseCommit") or binding.base_commit or ""),
            "workspacePolicy": str(
                ownership.get("workspacePolicy") or binding.workspace_policy
            ),
            "creationMode": str(ownership.get("creationMode") or binding.creation_mode),
        }
    return bindings


async def _persist_prepared_checkpoint_branch(
    *,
    session: AsyncSession,
    prepared: CheckpointBranchGitBindingResult,
    workspace_restore_ref: str,
    workspace_restore_digest: str | None,
    git_binding_ref: str,
    git_binding_digest: str | None,
    instruction_ref: str,
    instruction_digest: str,
    source_checkpoint_boundary: str,
    root_workflow_id: str | None,
    source_run_id: str | None,
    source_execution_ordinal: int | None,
    parent_branch_id: str | None,
    parent_turn_id: str | None,
    runtime_context_policy: str | None,
    step_execution_manifest_ref: str | None,
    created_step_execution_id: str | None,
) -> None:
    binding = prepared.binding
    branch = await session.get(WorkflowCheckpointBranch, binding.product_branch_id)
    artifact_refs = {
        "workspace_restore": workspace_restore_ref,
        "git_binding": git_binding_ref,
    }
    if step_execution_manifest_ref:
        artifact_refs["step_execution_manifest"] = step_execution_manifest_ref
    diagnostics = dict(prepared.diagnostics)
    diagnostics["workspaceRestoreRef"] = workspace_restore_ref
    diagnostics["gitBindingRef"] = git_binding_ref
    if branch is None:
        branch = WorkflowCheckpointBranch(
            branch_id=binding.product_branch_id,
            workflow_id=str(prepared.diagnostics["workflowId"]),
            root_workflow_id=root_workflow_id,
            source_run_id=source_run_id,
            logical_step_id=binding.logical_step_id,
            source_execution_ordinal=source_execution_ordinal,
            source_checkpoint_boundary=source_checkpoint_boundary,
            source_checkpoint_ref=binding.source_checkpoint_ref,
            source_checkpoint_digest=binding.source_checkpoint_digest,
            parent_branch_id=parent_branch_id,
            parent_turn_id=parent_turn_id,
            label=binding.label,
            state="preparing",
            workspace_policy=str(binding.workspace_policy),
            runtime_context_policy=runtime_context_policy,
            git_repository=binding.repository,
            git_base_branch=binding.base_branch,
            git_base_commit=binding.base_commit,
            git_work_branch=binding.work_branch,
            artifact_refs=artifact_refs,
            diagnostics=diagnostics,
        )
        session.add(branch)
    else:
        branch.state = "preparing"
        branch.workspace_policy = str(binding.workspace_policy)
        branch.runtime_context_policy = runtime_context_policy
        branch.logical_step_id = binding.logical_step_id
        branch.label = binding.label
        branch.git_repository = binding.repository
        branch.git_base_branch = binding.base_branch
        branch.git_base_commit = binding.base_commit
        branch.git_work_branch = binding.work_branch
        branch.artifact_refs = {**(branch.artifact_refs or {}), **artifact_refs}
        branch.diagnostics = {**(branch.diagnostics or {}), **diagnostics}

    git_binding = await session.get(
        WorkflowCheckpointBranchGitBinding, binding.product_branch_id
    )
    binding_metadata = {
        "binding": prepared.git_binding_payload,
        "workspaceRestoreArtifact": workspace_restore_ref,
        "gitBindingArtifact": git_binding_ref,
        "workspaceBaseline": prepared.git_binding_payload.get("workspaceBaseline"),
        "ownership": {
            "idempotencyKey": binding.idempotency_key,
            "baseBranch": binding.base_branch,
            "baseCommit": binding.base_commit,
            "workspacePolicy": str(binding.workspace_policy),
            "creationMode": str(binding.creation_mode),
        },
    }
    if git_binding is None:
        git_binding = WorkflowCheckpointBranchGitBinding(
            branch_id=binding.product_branch_id,
            repository=binding.repository,
            base_branch=binding.base_branch,
            base_commit=binding.base_commit,
            work_branch=binding.work_branch,
            worktree_ref=binding.worktree_ref,
            provider_workspace_ref=binding.provider_workspace_ref,
            head_commit=binding.head_commit,
            patch_ref=binding.patch_ref,
            pull_request_url=binding.pull_request_url,
            workspace_policy=str(binding.workspace_policy),
            creation_mode=str(binding.creation_mode),
            publish_status=binding.publish_status,
            binding_metadata=binding_metadata,
        )
        session.add(git_binding)
    else:
        git_binding.repository = binding.repository
        git_binding.base_branch = binding.base_branch
        git_binding.base_commit = binding.base_commit
        git_binding.work_branch = binding.work_branch
        git_binding.worktree_ref = binding.worktree_ref
        git_binding.provider_workspace_ref = binding.provider_workspace_ref
        git_binding.head_commit = binding.head_commit
        git_binding.patch_ref = binding.patch_ref
        git_binding.pull_request_url = binding.pull_request_url
        git_binding.workspace_policy = str(binding.workspace_policy)
        git_binding.creation_mode = str(binding.creation_mode)
        git_binding.binding_metadata = binding_metadata

    if binding.branch_turn_id:
        await _persist_branch_turn(
            session=session,
            prepared=prepared,
            workspace_restore_ref=workspace_restore_ref,
            git_binding_ref=git_binding_ref,
            instruction_ref=instruction_ref,
            instruction_digest=instruction_digest,
            parent_turn_id=parent_turn_id,
            step_execution_manifest_ref=step_execution_manifest_ref,
            created_step_execution_id=created_step_execution_id,
        )

    await _upsert_artifact_ref(
        session=session,
        branch_id=binding.product_branch_id,
        branch_turn_id=binding.branch_turn_id,
        artifact_kind="runtime.branch.workspace_restore.json",
        artifact_ref=workspace_restore_ref,
        content_type=CHECKPOINT_BRANCH_WORKSPACE_RESTORE_CONTENT_TYPE,
        digest=workspace_restore_digest,
    )
    await _upsert_artifact_ref(
        session=session,
        branch_id=binding.product_branch_id,
        branch_turn_id=binding.branch_turn_id,
        artifact_kind="runtime.branch.git_binding.json",
        artifact_ref=git_binding_ref,
        content_type=CHECKPOINT_BRANCH_GIT_BINDING_CONTENT_TYPE,
        digest=git_binding_digest,
    )
    await session.flush()


async def _persist_branch_turn(
    *,
    session: AsyncSession,
    prepared: CheckpointBranchGitBindingResult,
    workspace_restore_ref: str,
    git_binding_ref: str,
    instruction_ref: str,
    instruction_digest: str,
    parent_turn_id: str | None,
    step_execution_manifest_ref: str | None,
    created_step_execution_id: str | None,
) -> None:
    binding = prepared.binding
    assert binding.branch_turn_id is not None
    turn = await session.get(WorkflowCheckpointBranchTurn, binding.branch_turn_id)
    diagnostics = dict(prepared.branch_turn_metadata or {})
    diagnostics["gitBinding"] = prepared.diagnostics["gitBinding"]
    if turn is None:
        turn = WorkflowCheckpointBranchTurn(
            branch_turn_id=binding.branch_turn_id,
            branch_id=binding.product_branch_id,
            parent_turn_id=parent_turn_id,
            source_checkpoint_ref=binding.source_checkpoint_ref,
            source_checkpoint_digest=binding.source_checkpoint_digest,
            instruction_ref=instruction_ref,
            instruction_digest=instruction_digest,
            workspace_policy=str(binding.workspace_policy),
            git_work_branch=binding.work_branch,
            workspace_restore_ref=workspace_restore_ref,
            git_binding_ref=git_binding_ref,
            step_execution_manifest_ref=step_execution_manifest_ref,
            created_step_execution_id=created_step_execution_id,
            idempotency_key=binding.idempotency_key,
            status="preparing",
            diagnostics=diagnostics,
        )
        session.add(turn)
        return
    expected = {
        "branch_id": binding.product_branch_id,
        "parent_turn_id": parent_turn_id,
        "source_checkpoint_ref": binding.source_checkpoint_ref,
        "source_checkpoint_digest": binding.source_checkpoint_digest,
        "instruction_ref": instruction_ref,
        "instruction_digest": instruction_digest,
        "idempotency_key": binding.idempotency_key,
        "workspace_policy": str(binding.workspace_policy),
        "git_work_branch": binding.work_branch,
        "step_execution_manifest_ref": step_execution_manifest_ref,
        "created_step_execution_id": created_step_execution_id,
    }
    for field_name, expected_value in expected.items():
        if getattr(turn, field_name) != expected_value:
            raise CheckpointBranchGitBindingError(
                "invalid_binding",
                f"existing branch turn {binding.branch_turn_id!r} has "
                f"different {field_name}",
            )
    turn.workspace_restore_ref = workspace_restore_ref
    turn.git_binding_ref = git_binding_ref
    turn.status = "preparing"
    turn.diagnostics = {**(turn.diagnostics or {}), **diagnostics}


async def _upsert_artifact_ref(
    *,
    session: AsyncSession,
    branch_id: str,
    branch_turn_id: str | None,
    artifact_kind: str,
    artifact_ref: str,
    content_type: str,
    digest: str | None,
) -> None:
    result = await session.execute(
        select(WorkflowCheckpointBranchArtifact).where(
            WorkflowCheckpointBranchArtifact.branch_id == branch_id,
            WorkflowCheckpointBranchArtifact.branch_turn_id == branch_turn_id,
            WorkflowCheckpointBranchArtifact.artifact_kind == artifact_kind,
        )
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        session.add(
            WorkflowCheckpointBranchArtifact(
                branch_id=branch_id,
                branch_turn_id=branch_turn_id,
                artifact_kind=artifact_kind,
                artifact_ref=artifact_ref,
                content_type=content_type,
                digest=digest,
            )
        )
        return
    artifact.artifact_ref = artifact_ref
    artifact.content_type = content_type
    artifact.digest = digest
