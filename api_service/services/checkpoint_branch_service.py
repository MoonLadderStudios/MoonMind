"""Persistence service for checkpoint branch graph records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
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
    CheckpointBranchContinueModel,
    CheckpointBranchForkModel,
    CheckpointBranchGraphCreateModel,
    CheckpointBranchGraphModel,
    CheckpointBranchPublishReadyModel,
    CheckpointBranchStateUpdateModel,
    CheckpointBranchTurnCreateModel,
)
from moonmind.statuses.checkpoint_branch import (
    CheckpointBranchState,
    CheckpointBranchTurnState,
)

SOURCE_TRACEABILITY_ISSUES = ("MM-1087", "MM-1088")
CHECKPOINT_BRANCH_GRAPH_TRACEABILITY_ISSUES = ("MM-1087", "MM-1099")
CHECKPOINT_BRANCH_CONTROL_OPERATIONS = frozenset(
    {
        "checkpoint_branch_create",
        "checkpoint_branch_continue",
        "checkpoint_branch_fork",
        "checkpoint_branch_archive",
        "checkpoint_branch_publish_ready",
        "checkpoint_branch_failed",
        "checkpoint_branch_superseded",
    }
)
NON_BRANCH_CONTROL_OPERATIONS = frozenset(
    {
        "retry",
        "step_reexecution",
        "recovery",
        "promotion",
        "publication",
    }
)
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
    publish_status: str = "unpublished"


class CheckpointBranchService:
    """Create append-only checkpoint branch graph records."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_branch(
        self,
        *,
        workflow_id: str,
        branch_id: str,
    ) -> WorkflowCheckpointBranch:
        result = await self._session.execute(
            select(WorkflowCheckpointBranch).where(
                WorkflowCheckpointBranch.workflow_id == workflow_id,
                WorkflowCheckpointBranch.branch_id == branch_id,
            )
        )
        branch = result.scalar_one_or_none()
        if branch is None:
            raise ValueError("checkpoint branch not found")
        return branch

    async def _turn_count(self, branch_id: str) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(WorkflowCheckpointBranchTurn).where(
                WorkflowCheckpointBranchTurn.branch_id == branch_id
            )
        )
        return result.scalar_one()

    async def _latest_turn(
        self,
        branch_id: str,
    ) -> WorkflowCheckpointBranchTurn | None:
        result = await self._session.execute(
            select(WorkflowCheckpointBranchTurn)
            .where(WorkflowCheckpointBranchTurn.branch_id == branch_id)
            .order_by(WorkflowCheckpointBranchTurn.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _turn_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> WorkflowCheckpointBranchTurn | None:
        result = await self._session.execute(
            select(WorkflowCheckpointBranchTurn).where(
                WorkflowCheckpointBranchTurn.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def _operation_artifact(
        self,
        *,
        branch_id: str,
        idempotency_key: str,
        artifact_kind: str,
    ) -> WorkflowCheckpointBranchArtifact | None:
        result = await self._session.execute(
            select(WorkflowCheckpointBranchArtifact).where(
                WorkflowCheckpointBranchArtifact.branch_id == branch_id,
                WorkflowCheckpointBranchArtifact.artifact_kind == artifact_kind,
                WorkflowCheckpointBranchArtifact.artifact_ref
                == self._operation_artifact_ref(idempotency_key),
            )
        )
        return result.scalar_one_or_none()

    def _operation_artifact_ref(self, idempotency_key: str) -> str:
        return f"idempotency://{idempotency_key.strip()}"

    async def _record_operation_artifact_once(
        self,
        *,
        branch_id: str,
        idempotency_key: str,
        artifact_kind: str,
    ) -> WorkflowCheckpointBranchArtifact:
        existing = await self._operation_artifact(
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            artifact_kind=artifact_kind,
        )
        if existing is not None:
            return existing
        return await self.record_artifact(
            branch_id=branch_id,
            artifact_ref=self._operation_artifact_ref(idempotency_key),
            artifact_kind=artifact_kind,
        )

    async def _branch_graph(
        self,
        branch: WorkflowCheckpointBranch,
    ) -> CheckpointBranchGraphModel:
        turns = (
            await self._session.execute(
                select(WorkflowCheckpointBranchTurn)
                .where(WorkflowCheckpointBranchTurn.branch_id == branch.branch_id)
                .order_by(WorkflowCheckpointBranchTurn.created_at)
            )
        ).scalars().all()
        artifacts = (
            await self._session.execute(
                select(WorkflowCheckpointBranchArtifact)
                .where(WorkflowCheckpointBranchArtifact.branch_id == branch.branch_id)
                .order_by(WorkflowCheckpointBranchArtifact.created_at)
            )
        ).scalars().all()
        return CheckpointBranchGraphModel.model_validate(
            {"branch": branch, "turns": turns, "artifacts": artifacts}
        )

    def _next_turn_id(self, branch_id: str, count: int) -> str:
        return f"{branch_id}-turn-{count + 1}"

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
        await self._session.refresh(record)
        return record

    async def create_branch_graph(
        self,
        payload: CheckpointBranchGraphCreateModel | dict[str, Any],
    ) -> CheckpointBranchGraphModel:
        model = (
            payload
            if isinstance(payload, CheckpointBranchGraphCreateModel)
            else CheckpointBranchGraphCreateModel.model_validate(payload)
        )
        existing_turn = await self._turn_by_idempotency_key(model.idempotency_key)
        if existing_turn is not None:
            existing_branch = await self._get_branch(
                workflow_id=model.source.workflow_id,
                branch_id=existing_turn.branch_id,
            )
            if existing_branch.branch_id != model.branch_id:
                raise ValueError(
                    "idempotencyKey is already bound to another checkpoint branch"
                )
            return await self._branch_graph(existing_branch)
        branch = await self.create_branch(model)
        turn = await self.create_turn(
            {
                "branchTurnId": model.branch_turn_id
                or self._next_turn_id(model.branch_id, 0),
                "branchId": branch.branch_id,
                "sourceCheckpointRef": model.source.checkpoint_ref,
                "sourceCheckpointDigest": model.source.checkpoint_digest,
                "sourceStateKind": model.source.source_state_kind,
                "sourceStateRef": model.source.source_state_ref,
                "sourceStateDigest": model.source.source_state_digest,
                "workspacePolicy": model.workspace_policy,
                "runtimeContextPolicy": model.runtime_context_policy,
                "instructionRef": model.instruction_ref,
                "instructionDigest": model.instruction_digest,
                "contextBundleRef": model.context_bundle_ref,
                "createdStepExecutionId": model.created_step_execution_id,
                "runtimeAgentRunId": model.runtime_agent_run_id,
                "providerSessionId": model.provider_session_id,
                "idempotencyKey": model.idempotency_key,
                "status": "created",
            }
        )
        branch.current_head_step_execution_id = turn.created_step_execution_id
        branch.current_head_checkpoint_ref = turn.source_checkpoint_ref
        await self._session.flush()
        return await self._branch_graph(branch)

    async def continue_branch(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        payload: CheckpointBranchContinueModel | dict[str, Any],
    ) -> WorkflowCheckpointBranchTurn:
        model = (
            payload
            if isinstance(payload, CheckpointBranchContinueModel)
            else CheckpointBranchContinueModel.model_validate(payload)
        )
        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        existing_turn = await self._turn_by_idempotency_key(model.idempotency_key)
        if existing_turn is not None:
            if existing_turn.branch_id != branch.branch_id:
                raise ValueError(
                    "idempotencyKey is already bound to another checkpoint branch"
                )
            return existing_turn
        latest_turn = await self._latest_turn(branch_id)
        turn = await self.create_turn(
            {
                "branchTurnId": model.branch_turn_id
                or self._next_turn_id(branch_id, await self._turn_count(branch_id)),
                "branchId": branch.branch_id,
                "parentTurnId": latest_turn.branch_turn_id if latest_turn else None,
                "sourceCheckpointRef": branch.current_head_checkpoint_ref
                or branch.source_checkpoint_ref,
                "sourceCheckpointDigest": branch.source_checkpoint_digest,
                "sourceStateKind": branch.source_state_kind,
                "sourceStateRef": branch.source_state_ref,
                "sourceStateDigest": branch.source_state_digest,
                "workspacePolicy": branch.workspace_policy.value,
                "runtimeContextPolicy": branch.runtime_context_policy.value,
                "instructionRef": model.instruction_ref,
                "instructionDigest": model.instruction_digest,
                "contextBundleRef": model.context_bundle_ref,
                "createdStepExecutionId": model.created_step_execution_id,
                "runtimeAgentRunId": model.runtime_agent_run_id,
                "providerSessionId": model.provider_session_id,
                "idempotencyKey": model.idempotency_key,
                "status": "created",
            }
        )
        branch.state = CheckpointBranchState.ACTIVE
        branch.current_head_step_execution_id = turn.created_step_execution_id
        branch.current_head_checkpoint_ref = turn.source_checkpoint_ref
        await self._session.flush()
        return turn

    async def fork_branch(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        payload: CheckpointBranchForkModel | dict[str, Any],
    ) -> CheckpointBranchGraphModel:
        model = (
            payload
            if isinstance(payload, CheckpointBranchForkModel)
            else CheckpointBranchForkModel.model_validate(payload)
        )
        parent = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        existing_turn = await self._turn_by_idempotency_key(model.idempotency_key)
        if existing_turn is not None:
            if existing_turn.branch_id != model.branch_id:
                raise ValueError(
                    "idempotencyKey is already bound to another checkpoint branch"
                )
            existing_child = await self._get_branch(
                workflow_id=workflow_id,
                branch_id=existing_turn.branch_id,
            )
            return await self._branch_graph(existing_child)
        await self._require_turn_on_branch(
            branch_id=parent.branch_id,
            branch_turn_id=model.parent_turn_id,
            relation="parentTurnId",
        )
        child = await self.create_branch(
            {
                "branchId": model.branch_id,
                "source": {
                    "workflowId": parent.workflow_id,
                    "rootWorkflowId": parent.root_workflow_id,
                    "runId": parent.source_run_id,
                    "logicalStepId": parent.logical_step_id,
                    "sourceExecutionOrdinal": parent.source_execution_ordinal,
                    "checkpointBoundary": parent.source_checkpoint_boundary,
                    "checkpointRef": parent.current_head_checkpoint_ref
                    or parent.source_checkpoint_ref,
                    "checkpointDigest": parent.source_checkpoint_digest,
                    "sourceStateKind": parent.source_state_kind,
                    "sourceStateRef": parent.source_state_ref,
                    "sourceStateDigest": parent.source_state_digest,
                },
                "label": model.label,
                "branchKind": "child_fork",
                "workspacePolicy": model.workspace_policy,
                "runtimeContextPolicy": model.runtime_context_policy,
                "parentBranchId": parent.branch_id,
                "parentTurnId": model.parent_turn_id,
            }
        )
        await self.create_turn(
            {
                "branchTurnId": model.branch_turn_id
                or self._next_turn_id(model.branch_id, 0),
                "branchId": child.branch_id,
                "parentTurnId": None,
                "sourceCheckpointRef": child.source_checkpoint_ref,
                "sourceCheckpointDigest": child.source_checkpoint_digest,
                "sourceStateKind": child.source_state_kind,
                "sourceStateRef": child.source_state_ref,
                "sourceStateDigest": child.source_state_digest,
                "workspacePolicy": model.workspace_policy,
                "runtimeContextPolicy": model.runtime_context_policy,
                "instructionRef": model.instruction_ref,
                "instructionDigest": model.instruction_digest,
                "contextBundleRef": model.context_bundle_ref,
                "createdStepExecutionId": model.created_step_execution_id,
                "idempotencyKey": model.idempotency_key,
                "status": "created",
            }
        )
        await self._session.flush()
        return await self._branch_graph(child)

    async def archive_branch(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        idempotency_key: str | None = None,
    ) -> CheckpointBranchStateUpdateModel:
        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        if idempotency_key and await self._operation_artifact(
            branch_id=branch.branch_id,
            idempotency_key=idempotency_key,
            artifact_kind="operation_archive",
        ):
            return CheckpointBranchStateUpdateModel.model_validate(branch)
        branch.state = CheckpointBranchState.ARCHIVED
        branch.archived_at = branch.archived_at or datetime.now(UTC)
        if idempotency_key:
            await self._record_operation_artifact_once(
                branch_id=branch.branch_id,
                idempotency_key=idempotency_key,
                artifact_kind="operation_archive",
            )
        await self._session.flush()
        return CheckpointBranchStateUpdateModel.model_validate(branch)

    async def mark_failed(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        idempotency_key: str | None = None,
    ) -> CheckpointBranchStateUpdateModel:
        return await self._mark_terminal_state(
            workflow_id=workflow_id,
            branch_id=branch_id,
            state=CheckpointBranchState.FAILED,
            idempotency_key=idempotency_key,
            artifact_kind="operation_failed",
        )

    async def mark_superseded(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        idempotency_key: str | None = None,
    ) -> CheckpointBranchStateUpdateModel:
        return await self._mark_terminal_state(
            workflow_id=workflow_id,
            branch_id=branch_id,
            state=CheckpointBranchState.SUPERSEDED,
            idempotency_key=idempotency_key,
            artifact_kind="operation_superseded",
        )

    async def _mark_terminal_state(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        state: CheckpointBranchState,
        idempotency_key: str | None,
        artifact_kind: str,
    ) -> CheckpointBranchStateUpdateModel:
        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        if idempotency_key and await self._operation_artifact(
            branch_id=branch.branch_id,
            idempotency_key=idempotency_key,
            artifact_kind=artifact_kind,
        ):
            return CheckpointBranchStateUpdateModel.model_validate(branch)
        branch.state = state
        if idempotency_key:
            await self._record_operation_artifact_once(
                branch_id=branch.branch_id,
                idempotency_key=idempotency_key,
                artifact_kind=artifact_kind,
            )
        await self._session.flush()
        return CheckpointBranchStateUpdateModel.model_validate(branch)

    async def mark_publish_ready(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        payload: CheckpointBranchPublishReadyModel | dict[str, Any],
        artifact_ref: str | None = None,
    ) -> CheckpointBranchStateUpdateModel:
        model = (
            payload
            if isinstance(payload, CheckpointBranchPublishReadyModel)
            else CheckpointBranchPublishReadyModel.model_validate(payload)
        )
        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        if await self._operation_artifact(
            branch_id=branch.branch_id,
            idempotency_key=model.idempotency_key,
            artifact_kind="operation_publish_ready",
        ):
            return CheckpointBranchStateUpdateModel.model_validate(branch)
        branch.state = CheckpointBranchState.PROMOTABLE
        candidate_ref = artifact_ref or model.artifact_ref
        if candidate_ref:
            existing_candidate = (
                await self._session.execute(
                    select(WorkflowCheckpointBranchArtifact).where(
                        WorkflowCheckpointBranchArtifact.branch_id == branch.branch_id,
                        WorkflowCheckpointBranchArtifact.artifact_kind
                        == "publish_ready",
                        WorkflowCheckpointBranchArtifact.artifact_ref == candidate_ref,
                    )
                )
            ).scalar_one_or_none()
            if existing_candidate is None:
                await self.record_artifact(
                    branch_id=branch.branch_id,
                    artifact_ref=candidate_ref,
                    artifact_kind="publish_ready",
                )
        await self._record_operation_artifact_once(
            branch_id=branch.branch_id,
            idempotency_key=model.idempotency_key,
            artifact_kind="operation_publish_ready",
        )
        await self._session.flush()
        return CheckpointBranchStateUpdateModel.model_validate(branch)

    async def list_branch_graphs(
        self,
        *,
        workflow_id: str,
        active_only: bool = False,
    ) -> list[CheckpointBranchGraphModel]:
        statement = select(WorkflowCheckpointBranch).where(
            WorkflowCheckpointBranch.workflow_id == workflow_id
        )
        if active_only:
            statement = statement.where(
                WorkflowCheckpointBranch.state.notin_(
                    [
                        CheckpointBranchState.ARCHIVED,
                        CheckpointBranchState.FAILED,
                        CheckpointBranchState.SUPERSEDED,
                    ]
                )
            )
        statement = statement.order_by(WorkflowCheckpointBranch.created_at)
        branches = (await self._session.execute(statement)).scalars().all()
        return [await self._branch_graph(branch) for branch in branches]

    async def read_branch_graph(
        self,
        *,
        workflow_id: str,
        branch_id: str,
    ) -> CheckpointBranchGraphModel:
        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        return await self._branch_graph(branch)

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
        await self._session.refresh(record)
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
            publish_status=CheckpointBranchPublishStatus(model.publish_status),
        )
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
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
        await self._session.refresh(record)
        return record
