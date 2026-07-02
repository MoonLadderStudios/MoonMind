"""Persistence service for checkpoint branch graph records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    CheckpointBranchKind,
    CheckpointBranchPublishStatus,
    CheckpointBranchRuntimeContextPolicy,
    CheckpointBranchWorkspacePolicy,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)
from moonmind.schemas.checkpoint_branch_models import (
    CheckpointBranchCreateModel,
    CheckpointBranchTurnCreateModel,
)
from moonmind.statuses.checkpoint_branch import (
    CheckpointBranchState,
    CheckpointBranchTurnState,
)

SOURCE_TRACEABILITY_ISSUES = ("MM-1087", "MM-1088")
_PROTECTED_GIT_WORK_BRANCHES = {"", "main", "master", "HEAD"}


@dataclass(frozen=True)
class CheckpointBranchGitBindingInput:
    """Input for persisting a git binding for a product checkpoint branch."""

    branch_id: str
    repository: str
    base_branch: str
    base_commit: str
    work_branch: str
    worktree_ref: str | None = None
    head_commit: str | None = None
    patch_ref: str | None = None
    pull_request_url: str | None = None
    workspace_policy: str = "apply_previous_execution_diff_to_clean_baseline"
    creation_mode: str = "manual"
    publish_status: str = "unpublished"


class CheckpointBranchService:
    """Create append-only checkpoint branch graph records."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def _require_turn_on_branch(
        self,
        *,
        branch_id: str,
        branch_turn_id: str,
        relation: str,
    ) -> WorkflowCheckpointBranchTurn:
        result = await self._session.execute(
            select(WorkflowCheckpointBranchTurn).where(
                WorkflowCheckpointBranchTurn.branch_turn_id == branch_turn_id
            )
        )
        turn = result.scalar_one_or_none()
        if turn is None:
            raise ValueError(f"{relation} must reference an existing branch turn")
        if turn.branch_id != branch_id:
            raise ValueError(f"{relation} must belong to branch {branch_id}")
        return turn

    async def create_branch(
        self,
        payload: CheckpointBranchCreateModel | dict[str, Any],
    ) -> WorkflowCheckpointBranch:
        model = (
            payload
            if isinstance(payload, CheckpointBranchCreateModel)
            else CheckpointBranchCreateModel.model_validate(payload)
        )
        source = model.source
        if (
            model.branch_kind == "child_fork"
            and model.parent_branch_id
            and model.parent_turn_id
        ):
            await self._require_turn_on_branch(
                branch_id=model.parent_branch_id,
                branch_turn_id=model.parent_turn_id,
                relation="parentTurnId",
            )
        record = WorkflowCheckpointBranch(
            branch_id=model.branch_id,
            workflow_id=source.workflow_id,
            root_workflow_id=source.root_workflow_id or source.workflow_id,
            source_run_id=source.run_id,
            logical_step_id=source.logical_step_id,
            source_execution_ordinal=source.source_execution_ordinal,
            source_checkpoint_boundary=source.checkpoint_boundary,
            source_checkpoint_ref=source.checkpoint_ref,
            source_checkpoint_digest=source.checkpoint_digest,
            source_state_kind=source.source_state_kind,
            source_state_ref=source.source_state_ref,
            source_state_digest=source.source_state_digest,
            parent_branch_id=model.parent_branch_id,
            parent_turn_id=model.parent_turn_id,
            label=model.label,
            state=CheckpointBranchState(model.state),
            branch_kind=CheckpointBranchKind(model.branch_kind),
            workspace_policy=CheckpointBranchWorkspacePolicy(model.workspace_policy),
            runtime_context_policy=CheckpointBranchRuntimeContextPolicy(
                model.runtime_context_policy
            ),
            git_repository=model.git_repository,
            git_base_branch=model.git_base_branch,
            git_base_commit=model.git_base_commit,
            git_work_branch=model.git_work_branch,
            created_by=model.created_by,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def create_turn(
        self,
        payload: CheckpointBranchTurnCreateModel | dict[str, Any],
    ) -> WorkflowCheckpointBranchTurn:
        model = (
            payload
            if isinstance(payload, CheckpointBranchTurnCreateModel)
            else CheckpointBranchTurnCreateModel.model_validate(payload)
        )
        if model.created_step_execution_id in {model.branch_id, model.branch_turn_id}:
            raise ValueError(
                "branch turn Step Execution id must differ from branch and turn ids"
            )
        if model.parent_turn_id:
            await self._require_turn_on_branch(
                branch_id=model.branch_id,
                branch_turn_id=model.parent_turn_id,
                relation="parentTurnId",
            )
        record = WorkflowCheckpointBranchTurn(
            branch_turn_id=model.branch_turn_id,
            branch_id=model.branch_id,
            parent_turn_id=model.parent_turn_id,
            source_checkpoint_ref=model.source_checkpoint_ref,
            source_checkpoint_digest=model.source_checkpoint_digest,
            source_state_kind=model.source_state_kind,
            source_state_ref=model.source_state_ref,
            source_state_digest=model.source_state_digest,
            workspace_policy=CheckpointBranchWorkspacePolicy(model.workspace_policy),
            runtime_context_policy=CheckpointBranchRuntimeContextPolicy(
                model.runtime_context_policy
            ),
            instruction_ref=model.instruction_ref,
            instruction_digest=model.instruction_digest,
            context_bundle_ref=model.context_bundle_ref,
            created_step_execution_id=model.created_step_execution_id,
            runtime_agent_run_id=model.runtime_agent_run_id,
            provider_session_id=model.provider_session_id,
            idempotency_key=model.idempotency_key,
            status=CheckpointBranchTurnState(model.status),
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def record_git_binding(
        self,
        payload: CheckpointBranchGitBindingInput | dict[str, Any],
    ) -> WorkflowCheckpointBranchGitBinding:
        model = (
            payload
            if isinstance(payload, CheckpointBranchGitBindingInput)
            else CheckpointBranchGitBindingInput(**payload)
        )
        repository = model.repository.strip()
        base_branch = model.base_branch.strip()
        base_commit = model.base_commit.strip()
        work_branch = model.work_branch.strip()
        if work_branch in _PROTECTED_GIT_WORK_BRANCHES:
            raise ValueError(
                "checkpoint branch git binding requires an isolated work branch"
            )
        existing = await self._session.execute(
            select(WorkflowCheckpointBranchGitBinding.branch_id).where(
                WorkflowCheckpointBranchGitBinding.repository == repository,
                WorkflowCheckpointBranchGitBinding.work_branch == work_branch,
            )
        )
        existing_branch_id = existing.scalar_one_or_none()
        if existing_branch_id is not None and existing_branch_id != model.branch_id:
            raise ValueError(
                "checkpoint branch git work branch is already bound to another branch"
            )
        record = WorkflowCheckpointBranchGitBinding(
            branch_id=model.branch_id,
            repository=repository,
            base_branch=base_branch,
            base_commit=base_commit,
            work_branch=work_branch,
            worktree_ref=model.worktree_ref,
            head_commit=model.head_commit,
            patch_ref=model.patch_ref,
            pull_request_url=model.pull_request_url,
            workspace_policy=CheckpointBranchWorkspacePolicy(
                model.workspace_policy
            ).value,
            creation_mode=model.creation_mode,
            publish_status=CheckpointBranchPublishStatus(model.publish_status),
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def record_artifact(
        self,
        *,
        branch_id: str,
        artifact_ref: str,
        artifact_kind: str,
        branch_turn_id: str | None = None,
    ) -> WorkflowCheckpointBranchArtifact:
        branch_id = branch_id.strip()
        artifact_ref = artifact_ref.strip()
        artifact_kind = artifact_kind.strip()
        branch_turn_id = branch_turn_id.strip() if branch_turn_id else None
        if not branch_id or not artifact_ref or not artifact_kind:
            raise ValueError("branch artifact requires branch id, ref, and kind")
        if branch_turn_id:
            await self._require_turn_on_branch(
                branch_id=branch_id,
                branch_turn_id=branch_turn_id,
                relation="branchTurnId",
            )
        record = WorkflowCheckpointBranchArtifact(
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            artifact_ref=artifact_ref,
            artifact_kind=artifact_kind,
        )
        self._session.add(record)
        await self._session.flush()
        return record
