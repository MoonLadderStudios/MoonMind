"""Persistence service for checkpoint branch graph records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
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
    CheckpointBranchStateUpdateModel,
    CheckpointBranchTurnCreateModel,
    StepExecutionBranchMetadataModel,
)
from moonmind.workflows.temporal.remediation_workspace_head import (
    REMEDIATION_HEAD_MISMATCH,
    REMEDIATION_HEAD_STALE_VERSION,
    RemediationAttemptInput,
    RemediationAttemptOutput,
    RemediationHeadError,
    RemediationHeadStatus,
    RemediationWorkspaceHead,
    VerificationEvidence,
    WorkspaceMaterializationEvidence,
    advance_head,
    apply_verification,
    authorize_materialization,
    mark_terminal,
    rollback_head,
)
from moonmind.statuses.checkpoint_branch import (
    CheckpointBranchState,
    CheckpointBranchTurnState,
)

SOURCE_TRACEABILITY_ISSUES = ("MM-1087", "MM-1088")
CHECKPOINT_BRANCH_GRAPH_TRACEABILITY_ISSUES = ("MM-1087", "MM-1099")
_PROTECTED_GIT_WORK_BRANCHES = {"", "main", "master", "HEAD"}


def build_branch_turn_launch_idempotency_key(
    *,
    workflow_id: str,
    branch_id: str,
    branch_turn_id: str,
) -> str:
    """Construct the runtime launch key from workflow, branch, and turn identity."""

    parts = [workflow_id.strip(), branch_id.strip(), branch_turn_id.strip(), "launch"]
    if any(not part for part in parts):
        raise ValueError("branch turn launch idempotency key requires identities")
    return ":".join(parts)


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

    @staticmethod
    def _remediation_head(branch: WorkflowCheckpointBranch) -> RemediationWorkspaceHead:
        if not all(
            (
                branch.remediation_loop_id,
                branch.source_checkpoint_ref,
                branch.source_checkpoint_digest,
                branch.current_head_checkpoint_ref,
                branch.current_head_checkpoint_digest,
                branch.current_head_version,
                branch.remediation_head_status,
            )
        ):
            raise RemediationHeadError(
                REMEDIATION_HEAD_MISMATCH,
                "checkpoint branch has no complete remediation head authority",
            )
        return RemediationWorkspaceHead(
            loopId=branch.remediation_loop_id,
            branchRef=f"checkpoint-branch:{branch.branch_id}",
            rootCheckpointRef=branch.source_checkpoint_ref,
            rootWorkspaceDigest=branch.source_checkpoint_digest,
            headCheckpointRef=branch.current_head_checkpoint_ref,
            headWorkspaceDigest=branch.current_head_checkpoint_digest,
            headStepExecutionId=branch.current_head_step_execution_id,
            headAttemptOrdinal=branch.current_head_attempt_ordinal or 0,
            headVersion=branch.current_head_version,
            latestVerificationRef=branch.latest_verification_ref,
            latestVerificationVerdict=branch.latest_verification_verdict,
            status=branch.remediation_head_status,
            remainingWorkRef=(branch.artifact_refs or {}).get(
                "remediationRemainingWork"
            ),
        )

    async def authorize_remediation_materialization(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        attempt: RemediationAttemptInput,
        evidence: WorkspaceMaterializationEvidence,
        expected_owner_step_execution_id: str,
    ) -> RemediationWorkspaceHead:
        """Authorize one live reuse or cold restore from persisted head authority.

        The destination path deliberately does not enter this boundary.  Runtime
        code must first produce checkpoint, digest, version, loop, and owner
        evidence; only an exact match may be handed to the AgentRun.
        """

        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        head = self._remediation_head(branch)
        authorize_materialization(
            head,
            attempt,
            evidence,
            expected_owner_step_execution_id=expected_owner_step_execution_id,
        )
        return head

    async def initialize_remediation_head(
        self, *, workflow_id: str, branch_id: str, loop_id: str
    ) -> RemediationWorkspaceHead:
        """Initialize root authority once from persisted checkpoint evidence."""

        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        if branch.remediation_loop_id:
            if branch.remediation_loop_id != loop_id:
                raise RemediationHeadError(
                    REMEDIATION_HEAD_MISMATCH, "branch is owned by another remediation loop"
                )
            return self._remediation_head(branch)
        if not branch.source_checkpoint_digest:
            raise RemediationHeadError(
                REMEDIATION_HEAD_MISMATCH,
                "root checkpoint digest is required for remediation authority",
            )
        branch.remediation_loop_id = loop_id
        branch.current_head_checkpoint_ref = branch.source_checkpoint_ref
        branch.current_head_checkpoint_digest = branch.source_checkpoint_digest
        branch.current_head_version = 1
        branch.current_head_attempt_ordinal = 0
        branch.remediation_head_status = RemediationHeadStatus.CANDIDATE.value
        await self._session.flush()
        return self._remediation_head(branch)

    async def advance_remediation_head(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        attempt: RemediationAttemptInput,
        output: RemediationAttemptOutput,
        step_execution_id: str,
        transition_id: str,
    ) -> RemediationWorkspaceHead:
        """Atomically CAS a captured candidate and persist idempotency evidence."""

        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        head = self._remediation_head(branch)
        artifact_kind = f"remediation_transition_{transition_id}"
        existing = (
            await self._session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_id == branch_id,
                    WorkflowCheckpointBranchArtifact.artifact_kind == artifact_kind,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.artifact_ref != output.attempt_evidence_ref:
                raise RemediationHeadError(
                    REMEDIATION_HEAD_STALE_VERSION,
                    "transition identity was reused with different evidence",
                )
            return head

        updated, _transition = advance_head(
            head,
            attempt,
            output,
            step_execution_id=step_execution_id,
            transition_id=transition_id,
        )
        if updated.head_version != head.head_version:
            result = await self._session.execute(
                update(WorkflowCheckpointBranch)
                .where(
                    WorkflowCheckpointBranch.workflow_id == workflow_id,
                    WorkflowCheckpointBranch.branch_id == branch_id,
                    WorkflowCheckpointBranch.current_head_version
                    == attempt.expected_head_version,
                    WorkflowCheckpointBranch.current_head_checkpoint_ref
                    == attempt.base_checkpoint_ref,
                    WorkflowCheckpointBranch.current_head_checkpoint_digest
                    == attempt.expected_base_digest,
                )
                .values(
                    current_head_checkpoint_ref=updated.head_checkpoint_ref,
                    current_head_checkpoint_digest=updated.head_workspace_digest,
                    current_head_step_execution_id=updated.head_step_execution_id,
                    current_head_attempt_ordinal=updated.head_attempt_ordinal,
                    current_head_version=updated.head_version,
                    remediation_head_status=updated.status.value,
                    latest_verification_ref=None,
                    latest_verification_verdict=None,
                )
            )
            if result.rowcount != 1:
                raise RemediationHeadError(
                    REMEDIATION_HEAD_STALE_VERSION,
                    "remediation head was concurrently advanced",
                )
            await self._session.refresh(branch)
        await self.record_artifact(
            branch_id=branch_id,
            artifact_ref=output.attempt_evidence_ref,
            artifact_kind=artifact_kind,
        )
        return self._remediation_head(branch)

    async def record_remediation_verification(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        evidence: VerificationEvidence,
    ) -> RemediationWorkspaceHead:
        """Persist the authoritative verifier result for the exact current head."""

        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        head = self._remediation_head(branch)
        updated = apply_verification(head, evidence)
        result = await self._session.execute(
            update(WorkflowCheckpointBranch)
            .where(
                WorkflowCheckpointBranch.workflow_id == workflow_id,
                WorkflowCheckpointBranch.branch_id == branch_id,
                WorkflowCheckpointBranch.current_head_version
                == evidence.input_head_version,
                WorkflowCheckpointBranch.current_head_checkpoint_ref
                == evidence.input_head_ref,
                WorkflowCheckpointBranch.current_head_checkpoint_digest
                == evidence.input_head_digest,
            )
            .values(
                remediation_head_status=updated.status.value,
                latest_verification_ref=updated.latest_verification_ref,
                latest_verification_verdict=updated.latest_verification_verdict,
            )
        )
        if result.rowcount != 1:
            raise RemediationHeadError(
                REMEDIATION_HEAD_STALE_VERSION,
                "remediation head advanced while verification was running",
            )
        await self.record_artifact(
            branch_id=branch_id,
            artifact_ref=evidence.verifier_artifact_ref,
            artifact_kind=f"remediation_verification_v{evidence.input_head_version}",
        )
        await self._session.refresh(branch)
        return self._remediation_head(branch)

    async def mark_remediation_terminal(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        remaining_work_ref: str,
    ) -> RemediationWorkspaceHead:
        """Preserve the last valid candidate while recording terminal remaining work."""

        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        updated = mark_terminal(self._remediation_head(branch), remaining_work_ref)
        branch.remediation_head_status = updated.status.value
        branch.artifact_refs = {
            **(branch.artifact_refs or {}),
            "remediationRemainingWork": updated.remaining_work_ref,
        }
        await self.record_artifact(
            branch_id=branch_id,
            artifact_ref=remaining_work_ref,
            artifact_kind=f"remediation_terminal_v{updated.head_version}",
        )
        await self._session.flush()
        return updated

    async def rollback_remediation_head(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        expected_head_version: int,
        checkpoint_ref: str,
        workspace_digest: str,
        evidence_ref: str,
        transition_id: str,
    ) -> RemediationWorkspaceHead:
        """Atomically roll back a head and retain append-only supersession evidence."""

        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        head = self._remediation_head(branch)
        if head.head_version != expected_head_version:
            raise RemediationHeadError(
                REMEDIATION_HEAD_STALE_VERSION, "remediation head was concurrently advanced"
            )
        updated, _transition = rollback_head(
            head,
            checkpoint_ref=checkpoint_ref,
            workspace_digest=workspace_digest,
            evidence_ref=evidence_ref,
            transition_id=transition_id,
        )
        result = await self._session.execute(
            update(WorkflowCheckpointBranch)
            .where(
                WorkflowCheckpointBranch.workflow_id == workflow_id,
                WorkflowCheckpointBranch.branch_id == branch_id,
                WorkflowCheckpointBranch.current_head_version == expected_head_version,
                WorkflowCheckpointBranch.current_head_checkpoint_ref
                == head.head_checkpoint_ref,
                WorkflowCheckpointBranch.current_head_checkpoint_digest
                == head.head_workspace_digest,
            )
            .values(
                current_head_checkpoint_ref=updated.head_checkpoint_ref,
                current_head_checkpoint_digest=updated.head_workspace_digest,
                current_head_version=updated.head_version,
                remediation_head_status=updated.status.value,
                latest_verification_ref=None,
                latest_verification_verdict=None,
            )
        )
        if result.rowcount != 1:
            raise RemediationHeadError(
                REMEDIATION_HEAD_STALE_VERSION, "remediation head was concurrently advanced"
            )
        await self.record_artifact(
            branch_id=branch_id,
            artifact_ref=evidence_ref,
            artifact_kind=f"remediation_rollback_{transition_id}",
        )
        await self._session.refresh(branch)
        return self._remediation_head(branch)

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
        # branch_turn_id breaks created_at ties so same-transaction turns
        # resolve to a deterministic parent.
        result = await self._session.execute(
            select(WorkflowCheckpointBranchTurn)
            .where(WorkflowCheckpointBranchTurn.branch_id == branch_id)
            .order_by(
                WorkflowCheckpointBranchTurn.created_at.desc(),
                WorkflowCheckpointBranchTurn.branch_turn_id.desc(),
            )
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

    @staticmethod
    def _require_replay_matches_turn(
        existing_turn: WorkflowCheckpointBranchTurn,
        *,
        branch_id: str,
        instruction_digest: str,
    ) -> None:
        if existing_turn.branch_id != branch_id:
            raise ValueError(
                "idempotencyKey is already bound to another checkpoint branch"
            )
        if existing_turn.instruction_digest != instruction_digest:
            raise ValueError(
                "idempotencyKey is already bound to a turn with different instructions"
            )

    async def _operation_artifact_by_key(
        self,
        *,
        branch_id: str,
        idempotency_key: str,
    ) -> WorkflowCheckpointBranchArtifact | None:
        result = await self._session.execute(
            select(WorkflowCheckpointBranchArtifact).where(
                WorkflowCheckpointBranchArtifact.branch_id == branch_id,
                WorkflowCheckpointBranchArtifact.artifact_kind.like("operation_%"),
                WorkflowCheckpointBranchArtifact.artifact_ref
                == self._operation_artifact_ref(idempotency_key),
            )
        )
        return result.scalar_one_or_none()

    def _operation_artifact_ref(self, idempotency_key: str) -> str:
        return f"idempotency://{idempotency_key.strip()}"

    async def _replayed_operation(
        self,
        *,
        branch: WorkflowCheckpointBranch,
        idempotency_key: str,
        artifact_kind: str,
    ) -> CheckpointBranchStateUpdateModel | None:
        existing = await self._operation_artifact_by_key(
            branch_id=branch.branch_id,
            idempotency_key=idempotency_key,
        )
        if existing is None:
            return None
        if existing.artifact_kind != artifact_kind:
            raise ValueError(
                "idempotencyKey is already bound to a different branch operation"
            )
        return CheckpointBranchStateUpdateModel.model_validate(branch)

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
            state=CheckpointBranchState(model.state).value,
            branch_kind=CheckpointBranchKind(model.branch_kind).value,
            workspace_policy=CheckpointBranchWorkspacePolicy(
                model.workspace_policy
            ).value,
            runtime_context_policy=CheckpointBranchRuntimeContextPolicy(
                model.runtime_context_policy
            ).value,
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
            self._require_replay_matches_turn(
                existing_turn,
                branch_id=model.branch_id,
                instruction_digest=model.instruction_digest,
            )
            existing_branch = await self._get_branch(
                workflow_id=model.source.workflow_id,
                branch_id=existing_turn.branch_id,
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
        await self._session.refresh(branch)
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
            self._require_replay_matches_turn(
                existing_turn,
                branch_id=branch.branch_id,
                instruction_digest=model.instruction_digest,
            )
            return existing_turn
        if branch.state in {
            CheckpointBranchState.ARCHIVED.value,
            CheckpointBranchState.PROMOTED.value,
            CheckpointBranchState.SUPERSEDED.value,
        }:
            raise ValueError(
                f"cannot continue checkpoint branch in state '{branch.state}'"
            )
        latest_turn = await self._latest_turn(branch_id)
        source_checkpoint_ref = (
            branch.current_head_checkpoint_ref or branch.source_checkpoint_ref
        )
        source_checkpoint_digest = (
            branch.source_checkpoint_digest
            if source_checkpoint_ref == branch.source_checkpoint_ref
            else None
        )
        workspace_policy = model.workspace_policy or branch.workspace_policy
        runtime_context_policy = (
            model.runtime_context_policy or branch.runtime_context_policy
        )
        turn = await self.create_turn(
            {
                "branchTurnId": model.branch_turn_id
                or self._next_turn_id(branch_id, await self._turn_count(branch_id)),
                "branchId": branch.branch_id,
                "parentTurnId": latest_turn.branch_turn_id if latest_turn else None,
                "sourceCheckpointRef": source_checkpoint_ref,
                "sourceCheckpointDigest": source_checkpoint_digest,
                "sourceStateKind": branch.source_state_kind,
                "sourceStateRef": branch.source_state_ref,
                "sourceStateDigest": branch.source_state_digest,
                "workspacePolicy": workspace_policy,
                "runtimeContextPolicy": runtime_context_policy,
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
        branch.state = CheckpointBranchState.ACTIVE.value
        branch.workspace_policy = workspace_policy
        branch.runtime_context_policy = runtime_context_policy
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
            self._require_replay_matches_turn(
                existing_turn,
                branch_id=model.branch_id,
                instruction_digest=model.instruction_digest,
            )
            existing_child = await self._get_branch(
                workflow_id=workflow_id,
                branch_id=existing_turn.branch_id,
            )
            return await self._branch_graph(existing_child)
        if parent.state in {
            CheckpointBranchState.ARCHIVED.value,
            CheckpointBranchState.SUPERSEDED.value,
        }:
            raise ValueError(
                f"cannot fork checkpoint branch in state '{parent.state}'"
            )
        parent_turn = await self._require_turn_on_branch(
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
                    "checkpointRef": parent_turn.source_checkpoint_ref,
                    "checkpointDigest": parent_turn.source_checkpoint_digest,
                    "sourceStateKind": parent_turn.source_state_kind,
                    "sourceStateRef": parent_turn.source_state_ref,
                    "sourceStateDigest": parent_turn.source_state_digest,
                },
                "label": model.label,
                "branchKind": "child_fork",
                "workspacePolicy": model.workspace_policy,
                "runtimeContextPolicy": model.runtime_context_policy,
                "parentBranchId": parent.branch_id,
                "parentTurnId": model.parent_turn_id,
            }
        )
        turn = await self.create_turn(
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
                "runtimeAgentRunId": model.runtime_agent_run_id,
                "providerSessionId": model.provider_session_id,
                "idempotencyKey": model.idempotency_key,
                "status": "created",
            }
        )
        child.current_head_step_execution_id = turn.created_step_execution_id
        child.current_head_checkpoint_ref = turn.source_checkpoint_ref
        await self._session.flush()
        await self._session.refresh(child)
        return await self._branch_graph(child)

    async def archive_branch(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        idempotency_key: str | None = None,
    ) -> CheckpointBranchStateUpdateModel:
        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        if idempotency_key:
            replayed = await self._replayed_operation(
                branch=branch,
                idempotency_key=idempotency_key,
                artifact_kind="operation_archive",
            )
            if replayed is not None:
                return replayed
        branch.state = CheckpointBranchState.ARCHIVED.value
        branch.archived_at = branch.archived_at or datetime.now(UTC)
        if idempotency_key:
            await self.record_artifact(
                branch_id=branch.branch_id,
                artifact_ref=self._operation_artifact_ref(idempotency_key),
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
        if idempotency_key:
            replayed = await self._replayed_operation(
                branch=branch,
                idempotency_key=idempotency_key,
                artifact_kind=artifact_kind,
            )
            if replayed is not None:
                return replayed
        branch.state = state.value
        if idempotency_key:
            await self.record_artifact(
                branch_id=branch.branch_id,
                artifact_ref=self._operation_artifact_ref(idempotency_key),
                artifact_kind=artifact_kind,
            )
        await self._session.flush()
        return CheckpointBranchStateUpdateModel.model_validate(branch)

    async def mark_promotable(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        idempotency_key: str,
        candidate_artifact_ref: str | None = None,
    ) -> CheckpointBranchStateUpdateModel:
        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        replayed = await self._replayed_operation(
            branch=branch,
            idempotency_key=idempotency_key,
            artifact_kind="operation_promotable",
        )
        if replayed is not None:
            return replayed
        branch.state = CheckpointBranchState.PROMOTABLE.value
        if candidate_artifact_ref:
            existing_candidate = (
                await self._session.execute(
                    select(WorkflowCheckpointBranchArtifact).where(
                        WorkflowCheckpointBranchArtifact.branch_id == branch.branch_id,
                        WorkflowCheckpointBranchArtifact.artifact_kind
                        == "candidate_result",
                        WorkflowCheckpointBranchArtifact.artifact_ref
                        == candidate_artifact_ref,
                    )
                )
            ).scalar_one_or_none()
            if existing_candidate is None:
                await self.record_artifact(
                    branch_id=branch.branch_id,
                    artifact_ref=candidate_artifact_ref,
                    artifact_kind="candidate_result",
                )
        await self.record_artifact(
            branch_id=branch.branch_id,
            artifact_ref=self._operation_artifact_ref(idempotency_key),
            artifact_kind="operation_promotable",
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
                        CheckpointBranchState.ARCHIVED.value,
                        CheckpointBranchState.SUPERSEDED.value,
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

    async def launch_turn(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        branch_turn_id: str,
        context_bundle_ref: str,
        step_execution_manifest_ref: str,
        checkpoint_ref: str | None,
        diagnostics_ref: str,
        idempotency_key: str,
        created_step_execution_id: str | None = None,
        runtime_agent_run_id: str | None = None,
        provider_session_id: str | None = None,
        agent_request_ref: str | None = None,
        agent_result_ref: str | None = None,
    ) -> WorkflowCheckpointBranchTurn:
        """Launch one persisted branch turn as semantic runtime evidence."""

        branch = await self._get_branch(workflow_id=workflow_id, branch_id=branch_id)
        if branch.state in {
            CheckpointBranchState.ARCHIVED.value,
            CheckpointBranchState.PROMOTED.value,
            CheckpointBranchState.SUPERSEDED.value,
        }:
            raise ValueError(
                f"cannot launch checkpoint branch turn in state '{branch.state}'"
            )
        turn = await self._require_turn_on_branch(
            branch_id=branch.branch_id,
            branch_turn_id=branch_turn_id,
            relation="branchTurnId",
        )
        expected_key = build_branch_turn_launch_idempotency_key(
            workflow_id=workflow_id,
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
        )
        if idempotency_key.strip() != expected_key:
            raise ValueError(
                "branch turn launch idempotency key must include workflow, "
                "branch, and branch turn identity"
            )
        if not created_step_execution_id:
            raise ValueError("branch turn launch requires Step Execution evidence")
        launch_values = {
            "context_bundle_ref": context_bundle_ref,
            "step_execution_manifest_ref": step_execution_manifest_ref,
            "created_step_execution_id": created_step_execution_id,
            "runtime_agent_run_id": runtime_agent_run_id,
            "provider_session_id": provider_session_id,
        }
        self._require_launch_replay_matches(turn, launch_values)

        now = datetime.now(UTC)
        turn.context_bundle_ref = context_bundle_ref.strip()
        turn.step_execution_manifest_ref = step_execution_manifest_ref.strip()
        turn.created_step_execution_id = (
            created_step_execution_id.strip() if created_step_execution_id else None
        )
        turn.runtime_agent_run_id = (
            runtime_agent_run_id.strip() if runtime_agent_run_id else None
        )
        turn.provider_session_id = (
            provider_session_id.strip() if provider_session_id else None
        )
        turn.status = CheckpointBranchTurnState.RUNNING.value
        turn.started_at = turn.started_at or now
        turn.diagnostics = {
            **(turn.diagnostics or {}),
            "launchIdempotencyKey": expected_key,
            "stepExecutionManifestBranch": StepExecutionBranchMetadataModel(
                branchId=branch.branch_id,
                branchTurnId=turn.branch_turn_id,
                rootCheckpointRef=turn.source_checkpoint_ref,
                sourceStateKind=turn.source_state_kind,
                sourceStateRef=turn.source_state_ref,
                sourceStateDigest=turn.source_state_digest,
                parentBranchId=branch.parent_branch_id,
                parentTurnId=turn.parent_turn_id or branch.parent_turn_id,
                gitWorkBranch=branch.git_work_branch or turn.git_work_branch,
            ).model_dump(by_alias=True, exclude_none=True),
            "branchTurnArtifacts": {
                "contextBundleRef": context_bundle_ref,
                "stepExecutionManifestRef": step_execution_manifest_ref,
                "checkpointRef": checkpoint_ref,
                "diagnosticsRef": diagnostics_ref,
                "agentRequestRef": agent_request_ref,
                "agentResultRef": agent_result_ref,
            },
        }
        branch.state = CheckpointBranchState.ACTIVE.value
        branch.current_head_step_execution_id = turn.created_step_execution_id
        if checkpoint_ref:
            branch.current_head_checkpoint_ref = checkpoint_ref.strip()
        branch.artifact_refs = {
            **(branch.artifact_refs or {}),
            "latestBranchTurnContextBundle": context_bundle_ref,
            "latestBranchTurnManifest": step_execution_manifest_ref,
            "latestBranchTurnDiagnostics": diagnostics_ref,
        }
        if checkpoint_ref:
            branch.artifact_refs["latestBranchTurnCheckpoint"] = checkpoint_ref
        if turn.instruction_ref.startswith("artifact://"):
            await self._upsert_turn_artifact(
                branch_id=branch.branch_id,
                branch_turn_id=turn.branch_turn_id,
                artifact_kind="input.branch_turn.instructions.md",
                artifact_ref=turn.instruction_ref,
            )
        await self._upsert_turn_artifact(
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
            artifact_kind="runtime.branch_turn.context_bundle.json",
            artifact_ref=context_bundle_ref,
        )
        if agent_request_ref:
            await self._upsert_turn_artifact(
                branch_id=branch.branch_id,
                branch_turn_id=turn.branch_turn_id,
                artifact_kind="runtime.branch_turn.agent_request.json",
                artifact_ref=agent_request_ref,
            )
        if agent_result_ref:
            await self._upsert_turn_artifact(
                branch_id=branch.branch_id,
                branch_turn_id=turn.branch_turn_id,
                artifact_kind="runtime.branch_turn.agent_result.json",
                artifact_ref=agent_result_ref,
            )
        await self._upsert_turn_artifact(
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
            artifact_kind="output.branch_turn.step_execution_manifest.json",
            artifact_ref=step_execution_manifest_ref,
        )
        if checkpoint_ref:
            await self._upsert_turn_artifact(
                branch_id=branch.branch_id,
                branch_turn_id=turn.branch_turn_id,
                artifact_kind="output.branch_turn.checkpoint.json",
                artifact_ref=checkpoint_ref,
            )
        await self._upsert_turn_artifact(
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
            artifact_kind="output.branch_turn.diagnostics.json",
            artifact_ref=diagnostics_ref,
        )
        await self._session.flush()
        await self._session.refresh(turn)
        return turn

    @staticmethod
    def _require_launch_replay_matches(
        turn: WorkflowCheckpointBranchTurn,
        values: dict[str, str | None],
    ) -> None:
        for field_name, requested in values.items():
            requested_value = requested.strip() if requested else None
            existing_value = getattr(turn, field_name)
            if existing_value is not None and (
                requested_value is None or existing_value != requested_value
            ):
                raise ValueError(
                    f"immutable launch field {field_name} cannot be changed"
                )

    async def _upsert_turn_artifact(
        self,
        *,
        branch_id: str,
        branch_turn_id: str,
        artifact_kind: str,
        artifact_ref: str,
    ) -> WorkflowCheckpointBranchArtifact:
        artifact_ref = artifact_ref.strip()
        result = await self._session.execute(
            select(WorkflowCheckpointBranchArtifact).where(
                WorkflowCheckpointBranchArtifact.branch_id == branch_id,
                WorkflowCheckpointBranchArtifact.branch_turn_id == branch_turn_id,
                WorkflowCheckpointBranchArtifact.artifact_kind == artifact_kind,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            if existing.artifact_ref != artifact_ref:
                raise ValueError(
                    f"immutable launch artifact {artifact_kind} cannot be changed"
                )
            return existing
        artifact = WorkflowCheckpointBranchArtifact(
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            artifact_kind=artifact_kind,
            artifact_ref=artifact_ref,
        )
        self._session.add(artifact)
        await self._session.flush()
        return artifact

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
            workspace_policy=CheckpointBranchWorkspacePolicy(
                model.workspace_policy
            ).value,
            runtime_context_policy=CheckpointBranchRuntimeContextPolicy(
                model.runtime_context_policy
            ).value,
            instruction_ref=model.instruction_ref,
            instruction_digest=model.instruction_digest,
            context_bundle_ref=model.context_bundle_ref,
            created_step_execution_id=model.created_step_execution_id,
            runtime_agent_run_id=model.runtime_agent_run_id,
            provider_session_id=model.provider_session_id,
            idempotency_key=model.idempotency_key,
            status=CheckpointBranchTurnState(model.status).value,
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
            workspace_policy=CheckpointBranchWorkspacePolicy(
                model.workspace_policy
            ).value,
            creation_mode=model.creation_mode,
            publish_status=CheckpointBranchPublishStatus(model.publish_status).value,
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
