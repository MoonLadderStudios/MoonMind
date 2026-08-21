"""Server-owned Checkpoint Branch turn execution boundary.

The owner validates every persisted authority input, allocates the semantic
runtime identities, persists them, and only then starts the durable Temporal
workflow that dispatches the canonical profile-bound Omnigent AgentRun.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.common import WorkflowIDReusePolicy

from api_service.db.base import async_session_maker
from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfileUsage,
    ProviderProfileAuthState,
    TemporalArtifact,
    TemporalArtifactRetentionClass,
    TemporalExecutionCanonicalRecord,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from api_service.services.omnigent_agent_profile_selection import (
    compile_agent_profile_snapshot_parameters,
)
from api_service.services.omnigent_policies import OmnigentPolicyService
from moonmind.config.settings import settings
from moonmind.omnigent.checkpoints import validate_restore_material
from moonmind.omnigent.profile_bound_execution import (
    compile_follow_up_retrieval_policy,
    enforce_required_follow_up_retrieval,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.checkpoint_branch_models import CheckpointBranchTurnLaunchRequest
from moonmind.schemas.temporal_models import StepExecutionCheckpointModel
from moonmind.workflows import (
    get_temporal_artifact_repository,
    get_temporal_artifact_service,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter

WORKFLOW_TYPE = "MoonMind.CheckpointBranchTurn"
_STEP_EXECUTION_ID_MAX_LENGTH = 255


def get_checkpoint_branch_artifact_service(
    session: AsyncSession,
) -> TemporalArtifactService:
    """Use hermetic local storage in the repository's explicit test mode."""

    if settings.workflow.test_mode:
        return TemporalArtifactService(
            get_temporal_artifact_repository(session),
            store=LocalTemporalArtifactStore(settings.workflow.temporal_artifact_root),
        )
    return get_temporal_artifact_service(session)


class CheckpointBranchTurnLaunchError(ValueError):
    """Bounded rejection raised before any runtime mutation."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BranchTurnExecutionIdentity:
    workflow_id: str
    semantic_run_id: str
    logical_step_id: str
    execution_ordinal: int
    step_execution_id: str
    agent_run_workflow_id: str


def build_branch_turn_execution_identity(
    *, branch_id: str, branch_turn_id: str, logical_step_id: str, ordinal: int
) -> BranchTurnExecutionIdentity:
    """Allocate replay-stable server identities for one semantic turn."""

    if ordinal < 1:
        raise ValueError("branch turn execution ordinal must be positive")
    owner_workflow_id = f"checkpoint-branch-turn:{branch_turn_id}"
    semantic_run_id = f"branch-turn-{branch_turn_id}"
    step_execution_id = (
        f"{owner_workflow_id}:{semantic_run_id}:{logical_step_id}:"
        f"execution:{ordinal}"
    )
    if len(step_execution_id) > _STEP_EXECUTION_ID_MAX_LENGTH:
        # Preserve the readable identity while it fits. Oversized public
        # logical-step values use stable digest tokens so the composed identity
        # remains valid for the durable 255-character column.
        turn_token = hashlib.sha256(branch_turn_id.encode()).hexdigest()[:16]
        owner_workflow_id = f"checkpoint-branch-turn:{turn_token}"
        semantic_run_id = f"branch-turn-{turn_token}"
        fixed_length = len(
            f"{owner_workflow_id}:{semantic_run_id}::execution:{ordinal}"
        )
        logical_limit = _STEP_EXECUTION_ID_MAX_LENGTH - fixed_length
        if logical_limit < 18:
            raise ValueError("branch turn identity cannot fit durable storage")
        if len(logical_step_id) > logical_limit:
            logical_digest = hashlib.sha256(logical_step_id.encode()).hexdigest()[:16]
            logical_step_id = (
                f"{logical_step_id[: logical_limit - 17]}-{logical_digest}"
            )
        step_execution_id = (
            f"{owner_workflow_id}:{semantic_run_id}:{logical_step_id}:"
            f"execution:{ordinal}"
        )
    return BranchTurnExecutionIdentity(
        workflow_id=owner_workflow_id,
        semantic_run_id=semantic_run_id,
        logical_step_id=logical_step_id,
        execution_ordinal=ordinal,
        step_execution_id=step_execution_id,
        agent_run_workflow_id=f"checkpoint-branch-agent:{branch_id}:{branch_turn_id}",
    )


def checkpoint_branch_turn_owner_operational() -> bool:
    """Return readiness from the exact production workflow/activity registry."""

    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )
    from moonmind.workflows.temporal.workflow_registry import (
        workflow_fleet_activity_handlers,
        workflow_fleet_workflow_types,
    )

    required_activities = {
        "checkpoint_branch.turn.mark_running",
        "checkpoint_branch.turn.persist_terminal",
        "checkpoint_branch.turn.persist_terminal_rejection",
    }
    try:
        workflow_types = set(workflow_fleet_workflow_types(settings.temporal))
        catalog = build_default_activity_catalog(settings.temporal)
        for activity_type in required_activities:
            catalog.resolve_activity(activity_type)
        registered_handlers = {
            str(
                getattr(
                    getattr(handler, "__temporal_activity_definition", None),
                    "name",
                    "",
                )
            )
            for handler in workflow_fleet_activity_handlers()
        }
    except Exception:
        return False
    return (
        WORKFLOW_TYPE in workflow_types
        and required_activities <= registered_handlers
    )


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _canonical_follow_up_retrieval(value: Any) -> dict[str, Any] | None:
    """Normalize legacy budget spelling before compiling the runtime request."""

    if not isinstance(value, Mapping):
        return None
    normalized = dict(value)
    budgets = normalized.pop("budgets", None)
    if isinstance(budgets, Mapping):
        tokens = budgets.get("tokens")
        latency_ms = budgets.get("latency_ms")
        if (
            "maxContextTokens" not in normalized
            and isinstance(tokens, int)
            and not isinstance(tokens, bool)
            and tokens > 0
        ):
            normalized["maxContextTokens"] = tokens
        if (
            "latencyMs" not in normalized
            and isinstance(latency_ms, int)
            and not isinstance(latency_ms, bool)
            and latency_ms > 0
        ):
            normalized["latencyMs"] = latency_ms
    return normalized


def _artifact_id(ref: str, *, field_name: str) -> str:
    value = str(ref or "").strip()
    if not value.startswith("artifact://") or not value.removeprefix("artifact://"):
        raise CheckpointBranchTurnLaunchError(
            "authority_ref_invalid", f"{field_name} must be a durable artifact ref"
        )
    return value.removeprefix("artifact://")


class CheckpointBranchTurnExecutionOwner:
    """Validate, claim, and dispatch exactly one Checkpoint Branch turn."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        principal: str,
        client: TemporalClientAdapter | None = None,
        artifact_service: TemporalArtifactService | None = None,
    ) -> None:
        self._session = session
        self._principal = principal
        self._client = client or TemporalClientAdapter()
        # Production artifact writes use their own short transaction.  The
        # owner session can therefore retain its branch-turn row lock across
        # artifact commits and serialize simultaneous launch requests.  Tests
        # may inject the real boundary fake directly.
        self._artifacts = artifact_service
        self._artifact_link: dict[str, Any] | None = None

    @asynccontextmanager
    async def _artifact_service(
        self,
    ) -> AsyncIterator[tuple[AsyncSession | None, TemporalArtifactService]]:
        if self._artifacts is not None:
            yield None, self._artifacts
            return
        bound_factory = (
            async_sessionmaker(self._session.bind, expire_on_commit=False)
            if self._session.bind is not None
            else async_session_maker
        )
        async with bound_factory() as artifact_session:
            yield artifact_session, get_checkpoint_branch_artifact_service(
                artifact_session
            )

    async def _read_ref(self, ref: str, *, field_name: str) -> bytes:
        if str(ref).startswith("artifact://omnigent/"):
            try:
                from moonmind.omnigent.bridge_artifacts import (
                    LocalOmnigentArtifactGateway,
                )

                return await LocalOmnigentArtifactGateway().read_bytes(str(ref))
            except Exception as exc:
                raise CheckpointBranchTurnLaunchError(
                    "authority_ref_unavailable", f"{field_name} is unavailable"
                ) from exc
        try:
            async with self._artifact_service() as (_session, artifacts):
                _artifact, payload = await artifacts.read(
                    artifact_id=_artifact_id(ref, field_name=field_name),
                    principal=self._principal,
                    allow_restricted_raw=True,
                )
        except CheckpointBranchTurnLaunchError:
            raise
        except Exception as exc:
            raise CheckpointBranchTurnLaunchError(
                "authority_ref_unavailable", f"{field_name} is unavailable"
            ) from exc
        return payload

    async def _validated_remediation_context_ref(
        self, turn: WorkflowCheckpointBranchTurn
    ) -> str | None:
        """Resolve the turn-owned remediation input before launch artifacts exist."""

        ref = str((turn.diagnostics or {}).get("remediationContextRef") or "").strip()
        if not ref:
            return None
        record = (
            await self._session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_id == turn.branch_id,
                    WorkflowCheckpointBranchArtifact.branch_turn_id
                    == turn.branch_turn_id,
                    WorkflowCheckpointBranchArtifact.artifact_kind
                    == "input.branch_turn.remediation_context.json",
                )
            )
        ).scalar_one_or_none()
        if record is None or record.artifact_ref != ref:
            raise CheckpointBranchTurnLaunchError(
                "remediation_context_authority_changed",
                "stored remediation context authority changed",
            )
        await self._read_ref(ref, field_name="remediationContextRef")
        return ref

    async def _write_artifact(
        self, *, content_type: str, payload: bytes, kind: str, branch_turn_id: str
    ) -> str:
        digest = _sha256(payload).removeprefix("sha256:")
        async with self._artifact_service() as (artifact_session, artifacts):
            artifact: TemporalArtifact | None = None
            if artifact_session is not None:
                # Artifact creation commits independently from the launch
                # claim.  Resolve an earlier owned artifact first so a crash
                # after blob persistence but before claim persistence reuses
                # the exact immutable ref.
                artifact = (
                    await artifact_session.execute(
                        select(TemporalArtifact)
                        .where(
                            TemporalArtifact.created_by_principal
                            == self._principal,
                            TemporalArtifact.metadata_json["kind"].as_string()
                            == kind,
                            TemporalArtifact.metadata_json[
                                "branchTurnId"
                            ].as_string()
                            == branch_turn_id,
                        )
                        .order_by(
                            TemporalArtifact.created_at,
                            TemporalArtifact.artifact_id,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if artifact is not None:
                if (
                    artifact.sha256 not in {None, digest}
                    or artifact.size_bytes not in {None, len(payload)}
                    or artifact.content_type not in {None, content_type}
                ):
                    raise CheckpointBranchTurnLaunchError(
                        "launch_artifact_conflict",
                        f"owned branch-turn artifact {kind} changed across retry",
                    )
                if getattr(artifact.status, "value", artifact.status) == "complete":
                    _stored, existing_payload = await artifacts.read(
                        artifact_id=artifact.artifact_id,
                        principal=self._principal,
                        allow_restricted_raw=True,
                    )
                    if existing_payload != payload:
                        raise CheckpointBranchTurnLaunchError(
                            "launch_artifact_conflict",
                            f"owned branch-turn artifact {kind} changed across retry",
                        )
                    return f"artifact://{artifact.artifact_id}"
            else:
                artifact, _upload = await artifacts.create(
                    principal=self._principal,
                    content_type=content_type,
                    size_bytes=len(payload),
                    sha256=digest,
                    retention_class=TemporalArtifactRetentionClass.LONG,
                    link=self._artifact_link,
                    metadata_json={
                        "kind": kind,
                        "branchTurnId": branch_turn_id,
                        "issue": "MoonLadderStudios/MoonMind#3621",
                    },
                )
            await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal=self._principal,
                payload=payload,
                content_type=content_type,
            )
            return f"artifact://{artifact.artifact_id}"

    async def persist_instruction_text(
        self, *, text: str, branch_turn_id: str
    ) -> tuple[str, str]:
        """Persist authored text as immutable input instead of an inline URI."""

        payload = text.encode("utf-8")
        ref = await self._write_artifact(
            content_type="text/markdown",
            payload=payload,
            kind="input.branch_turn.instructions.md",
            branch_turn_id=branch_turn_id,
        )
        return ref, _sha256(payload)

    async def _load_graph_authority(
        self, *, workflow_id: str, branch_id: str, branch_turn_id: str
    ) -> tuple[
        WorkflowCheckpointBranch,
        WorkflowCheckpointBranchTurn,
        WorkflowCheckpointBranchGitBinding,
        TemporalExecutionCanonicalRecord,
    ]:
        branch = (
            await self._session.execute(
                select(WorkflowCheckpointBranch)
                .where(WorkflowCheckpointBranch.branch_id == branch_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if branch is None or branch.workflow_id != workflow_id:
            raise CheckpointBranchTurnLaunchError(
                "branch_not_found", "checkpoint branch was not found"
            )
        turn = (
            await self._session.execute(
                select(WorkflowCheckpointBranchTurn)
                .where(
                    WorkflowCheckpointBranchTurn.branch_turn_id == branch_turn_id
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if turn is None or turn.branch_id != branch_id:
            raise CheckpointBranchTurnLaunchError(
                "branch_turn_not_found", "checkpoint branch turn was not found"
            )
        binding = await self._session.get(WorkflowCheckpointBranchGitBinding, branch_id)
        if binding is None:
            raise CheckpointBranchTurnLaunchError(
                "git_binding_missing", "isolated git binding is missing"
            )
        source = await self._session.get(TemporalExecutionCanonicalRecord, workflow_id)
        if source is None:
            raise CheckpointBranchTurnLaunchError(
                "source_execution_missing", "source workflow is missing"
            )
        return branch, turn, binding, source

    async def _validate_source_authority(
        self,
        *,
        branch: WorkflowCheckpointBranch,
        turn: WorkflowCheckpointBranchTurn,
        binding: WorkflowCheckpointBranchGitBinding,
        source: TemporalExecutionCanonicalRecord,
        expected_head_version: int | None,
    ) -> tuple[
        StepExecutionCheckpointModel,
        ManagedAgentProviderProfile,
        dict[str, Any],
    ]:
        """Reject changed authority before a lease, host, session, or message."""

        parent_turn_id = turn.parent_turn_id or branch.parent_turn_id
        if parent_turn_id is None and source.run_id != branch.source_run_id:
            raise CheckpointBranchTurnLaunchError(
                "source_run_stale", "pinned source run no longer matches"
            )
        if branch.root_workflow_id != branch.workflow_id:
            raise CheckpointBranchTurnLaunchError(
                "root_workflow_mismatch", "stored root Workflow authority changed"
            )
        if expected_head_version is not None and (
            branch.current_head_version != expected_head_version
        ):
            raise CheckpointBranchTurnLaunchError(
                "branch_head_stale", "expected branch head version does not match"
            )
        if branch.current_head_checkpoint_ref != turn.source_checkpoint_ref:
            raise CheckpointBranchTurnLaunchError(
                "branch_head_checkpoint_changed",
                "turn source no longer matches the persisted branch head",
            )
        if branch.workspace_policy != turn.workspace_policy:
            raise CheckpointBranchTurnLaunchError(
                "workspace_policy_changed", "stored workspace policy changed"
            )
        if branch.runtime_context_policy != turn.runtime_context_policy:
            raise CheckpointBranchTurnLaunchError(
                "runtime_context_policy_changed",
                "stored runtime-context policy changed",
            )
        if turn.runtime_context_policy != "fresh_agent_run":
            raise CheckpointBranchTurnLaunchError(
                "runtime_context_policy_unsupported",
                "Checkpoint Branch turns require fresh_agent_run",
            )
        if binding.work_branch in {"", "HEAD", "main", "master"}:
            raise CheckpointBranchTurnLaunchError(
                "git_binding_not_isolated", "git work branch is not isolated"
            )
        if (
            branch.git_repository != binding.repository
            or branch.git_base_branch != binding.base_branch
            or branch.git_base_commit != binding.base_commit
            or branch.git_work_branch != binding.work_branch
        ):
            raise CheckpointBranchTurnLaunchError(
                "git_binding_changed", "stored git binding authority changed"
            )

        branch_state_authority = (
            branch.source_state_kind,
            branch.source_state_ref,
            branch.source_state_digest,
        )
        turn_state_authority = (
            turn.source_state_kind,
            turn.source_state_ref,
            turn.source_state_digest,
        )
        if branch_state_authority != turn_state_authority:
            raise CheckpointBranchTurnLaunchError(
                "source_state_authority_changed",
                "stored source-state authority changed",
            )
        if any(turn_state_authority):
            if not turn.source_state_kind or not turn.source_state_ref:
                raise CheckpointBranchTurnLaunchError(
                    "source_state_authority_invalid",
                    "stored source-state authority is incomplete",
                )
            source_state_bytes = await self._read_ref(
                turn.source_state_ref, field_name="sourceStateRef"
            )
            if (
                turn.source_state_digest
                and _sha256(source_state_bytes) != turn.source_state_digest
            ):
                raise CheckpointBranchTurnLaunchError(
                    "source_state_digest_mismatch",
                    "stored source-state authority changed",
                )

        checkpoint_bytes = await self._read_ref(
            turn.source_checkpoint_ref, field_name="sourceCheckpointRef"
        )
        actual_checkpoint_digest = _sha256(checkpoint_bytes)
        for expected in (
            turn.source_checkpoint_digest,
            branch.source_checkpoint_digest
            if turn.source_checkpoint_ref == branch.source_checkpoint_ref
            else None,
        ):
            if expected and expected != actual_checkpoint_digest:
                raise CheckpointBranchTurnLaunchError(
                    "checkpoint_digest_mismatch", "source checkpoint digest changed"
                )
        try:
            checkpoint = StepExecutionCheckpointModel.model_validate_json(
                checkpoint_bytes
            )
        except Exception as exc:
            raise CheckpointBranchTurnLaunchError(
                "checkpoint_schema_invalid", "source checkpoint schema is invalid"
            ) from exc
        if checkpoint.omnigent is None:
            raise CheckpointBranchTurnLaunchError(
                "checkpoint_restore_unsupported",
                "source checkpoint has no Omnigent cold-restore authority",
            )
        if checkpoint.validation is not None and not checkpoint.validation.valid:
            raise CheckpointBranchTurnLaunchError(
                "checkpoint_validation_failed", "source checkpoint is not valid"
            )
        source_identity = checkpoint.source
        if (
            checkpoint.omnigent.workflow_id,
            checkpoint.omnigent.run_id,
            checkpoint.omnigent.logical_step_id,
            checkpoint.omnigent.boundary,
        ) != (
            source_identity.workflow_id,
            source_identity.run_id,
            source_identity.logical_step_id,
            checkpoint.boundary,
        ):
            raise CheckpointBranchTurnLaunchError(
                "checkpoint_semantic_identity_mismatch",
                "source checkpoint semantic identity changed",
            )
        if checkpoint.boundary != branch.source_checkpoint_boundary:
            raise CheckpointBranchTurnLaunchError(
                "checkpoint_boundary_mismatch",
                "source checkpoint boundary changed",
            )
        if parent_turn_id:
            parent_turn = await self._session.get(
                WorkflowCheckpointBranchTurn, parent_turn_id
            )
            if parent_turn is None or not parent_turn.created_step_execution_id:
                raise CheckpointBranchTurnLaunchError(
                    "parent_turn_authority_missing",
                    "parent turn has no server-owned Step Execution authority",
                )
            expected_parent_branch_id = (
                branch.parent_branch_id
                if branch.parent_branch_id
                and branch.parent_turn_id
                and parent_turn_id == branch.parent_turn_id
                else branch.branch_id
            )
            if parent_turn.branch_id != expected_parent_branch_id:
                raise CheckpointBranchTurnLaunchError(
                    "parent_turn_branch_mismatch",
                    "parent turn does not belong to the persisted parent branch",
                )
            parent_checkpoint = (
                await self._session.execute(
                    select(WorkflowCheckpointBranchArtifact).where(
                        WorkflowCheckpointBranchArtifact.branch_id
                        == parent_turn.branch_id,
                        WorkflowCheckpointBranchArtifact.branch_turn_id
                        == parent_turn.branch_turn_id,
                        WorkflowCheckpointBranchArtifact.artifact_kind
                        == "output.branch_turn.checkpoint.json",
                    )
                )
            ).scalar_one_or_none()
            if (
                parent_checkpoint is None
                or parent_checkpoint.artifact_ref != turn.source_checkpoint_ref
            ):
                raise CheckpointBranchTurnLaunchError(
                    "parent_turn_checkpoint_mismatch",
                    "turn source is not the persisted parent output checkpoint",
                )
            if (
                parent_checkpoint.digest
                and parent_checkpoint.digest != actual_checkpoint_digest
            ):
                raise CheckpointBranchTurnLaunchError(
                    "parent_turn_checkpoint_digest_mismatch",
                    "parent output checkpoint digest changed",
                )
            if checkpoint.omnigent.step_execution_id != (
                parent_turn.created_step_execution_id
            ):
                raise CheckpointBranchTurnLaunchError(
                    "parent_turn_execution_mismatch",
                    "parent checkpoint Step Execution identity changed",
                )
        else:
            expected_identity = (
                branch.workflow_id,
                branch.source_run_id,
                branch.logical_step_id,
                branch.source_execution_ordinal,
                branch.source_checkpoint_boundary,
            )
            actual_identity = (
                source_identity.workflow_id,
                source_identity.run_id,
                source_identity.logical_step_id,
                source_identity.execution_ordinal,
                checkpoint.boundary,
            )
            if actual_identity != expected_identity:
                raise CheckpointBranchTurnLaunchError(
                    "checkpoint_lineage_mismatch", "source checkpoint lineage changed"
                )

        instruction_bytes = await self._read_ref(
            turn.instruction_ref, field_name="instructionRef"
        )
        if _sha256(instruction_bytes) != turn.instruction_digest:
            raise CheckpointBranchTurnLaunchError(
                "instruction_digest_mismatch", "branch instructions changed"
            )

        follow_up_retrieval_value = (turn.diagnostics or {}).get(
            "followUpRetrieval"
        )
        if follow_up_retrieval_value is not None and not isinstance(
            follow_up_retrieval_value, Mapping
        ):
            raise CheckpointBranchTurnLaunchError(
                "retrieval_authority_invalid",
                "stored follow-up retrieval authority is invalid",
            )
        follow_up_retrieval = _canonical_follow_up_retrieval(
            follow_up_retrieval_value
        )
        if follow_up_retrieval is not None:
            try:
                AgentExecutionRequest(
                    agentKind="external",
                    agentId="omnigent",
                    correlationId=turn.branch_turn_id,
                    idempotencyKey=f"checkpoint-branch-retrieval:{turn.branch_turn_id}",
                    parameters={"followUpRetrieval": follow_up_retrieval},
                )
            except Exception as exc:
                raise CheckpointBranchTurnLaunchError(
                    "retrieval_authority_invalid",
                    "stored follow-up retrieval authority is invalid",
                ) from exc

        omnigent = checkpoint.omnigent
        selection = dict((branch.diagnostics or {}).get("runtimeSelection") or {})
        selected_runtime_policy = str(
            selection.get("runtimeContextPolicy") or ""
        ).strip()
        if selected_runtime_policy and selected_runtime_policy != (
            turn.runtime_context_policy
        ):
            raise CheckpointBranchTurnLaunchError(
                "runtime_selection_policy_mismatch",
                "runtime selection differs from the stored turn policy",
            )
        selected_work_branch = str(selection.get("gitWorkBranch") or "").strip()
        if selected_work_branch and selected_work_branch != binding.work_branch:
            raise CheckpointBranchTurnLaunchError(
                "runtime_selection_branch_mismatch",
                "runtime selection differs from the isolated git binding",
            )
        for field_name in ("model", "effort"):
            selected_value = selection.get(field_name)
            if selected_value is not None and (
                not isinstance(selected_value, str) or not selected_value.strip()
            ):
                raise CheckpointBranchTurnLaunchError(
                    f"{field_name}_selection_invalid",
                    f"stored {field_name} selection is invalid",
                )
        publish_mode = str(selection.get("publishMode") or "none").strip().lower()
        if publish_mode not in {"none", "branch", "pull_request"}:
            raise CheckpointBranchTurnLaunchError(
                "publish_intent_unsupported", "stored publish intent is unsupported"
            )
        selected_launch_policy = str(
            selection.get("launchPolicyRef") or ""
        ).strip()
        if (
            selected_launch_policy
            and selected_launch_policy != omnigent.launch_policy_ref
        ):
            raise CheckpointBranchTurnLaunchError(
                "launch_policy_mismatch",
                "stored launch policy differs from checkpoint authority",
            )
        selected_profile = str(selection.get("providerProfileRef") or "").strip()
        if selected_profile and selected_profile != omnigent.provider_profile_id:
            raise CheckpointBranchTurnLaunchError(
                "provider_profile_mismatch",
                "stored Provider Profile differs from checkpoint authority",
            )
        selected_execution_profile = str(
            selection.get("executionProfileRef") or ""
        ).strip()
        if (
            selected_execution_profile
            and selected_execution_profile != omnigent.execution_profile_ref
        ):
            raise CheckpointBranchTurnLaunchError(
                "execution_profile_mismatch",
                "stored execution profile differs from checkpoint authority",
            )
        agent_snapshot = selection.get("agentProfileSnapshot")
        agent_identity = selection.get("agentProfile")
        if agent_snapshot is not None:
            if not isinstance(agent_snapshot, Mapping) or not isinstance(
                agent_identity, Mapping
            ):
                raise CheckpointBranchTurnLaunchError(
                    "agent_profile_snapshot_invalid",
                    "stored Agent Profile snapshot identity is incomplete",
                )
            for key in ("profileId", "version", "digest"):
                if agent_identity.get(key) != agent_snapshot.get(key):
                    raise CheckpointBranchTurnLaunchError(
                        "agent_profile_snapshot_changed",
                        "stored Agent Profile snapshot identity changed",
                    )
            snapshot_provider = str(
                agent_snapshot.get("providerProfileRef") or ""
            ).strip()
            if snapshot_provider and snapshot_provider != omnigent.provider_profile_id:
                raise CheckpointBranchTurnLaunchError(
                    "agent_profile_provider_mismatch",
                    "Agent Profile snapshot Provider Profile changed",
                )
            usage = (
                await self._session.execute(
                    select(OmnigentAgentProfileUsage).where(
                        OmnigentAgentProfileUsage.consumer_type == "checkpoint",
                        OmnigentAgentProfileUsage.consumer_id == branch.branch_id,
                    )
                )
            ).scalar_one_or_none()
            if usage is None or usage.effective_snapshot != dict(agent_snapshot):
                raise CheckpointBranchTurnLaunchError(
                    "agent_profile_snapshot_unavailable",
                    "immutable Agent Profile snapshot authority is unavailable",
                )
        profile = await self._session.get(
            ManagedAgentProviderProfile, omnigent.provider_profile_id
        )
        if profile is None:
            raise CheckpointBranchTurnLaunchError(
                "provider_profile_missing", "Provider Profile is missing"
            )
        auth_state = getattr(profile.auth_state, "value", profile.auth_state)
        if (
            not profile.enabled
            or auth_state != ProviderProfileAuthState.CONNECTED.value
        ):
            raise CheckpointBranchTurnLaunchError(
                "provider_profile_not_ready", "Provider Profile is not launch ready"
            )
        if profile.credential_generation != omnigent.credential_generation:
            raise CheckpointBranchTurnLaunchError(
                "credential_generation_changed",
                "Provider Profile credential generation changed",
            )
        selected_runtime = str(selection.get("runtimeId") or "").strip()
        current_runtime = str(
            getattr(profile.runtime_id, "value", profile.runtime_id)
        ).strip()
        if selected_runtime and selected_runtime != current_runtime:
            raise CheckpointBranchTurnLaunchError(
                "runtime_selection_mismatch",
                "stored runtime selection differs from the Provider Profile",
            )
        if binding.base_commit != omnigent.baseline_commit:
            raise CheckpointBranchTurnLaunchError(
                "repository_baseline_mismatch",
                "git base commit differs from checkpoint baseline",
            )

        try:
            policy_snapshot = await OmnigentPolicyService(
                self._session
            ).resolve_runtime_snapshot(omnigent.launch_policy_ref)
        except Exception as exc:
            raise CheckpointBranchTurnLaunchError(
                "policy_authority_unavailable", "launch policy authority is unavailable"
            ) from exc
        if follow_up_retrieval is not None:
            try:
                compiled_retrieval = compile_follow_up_retrieval_policy(
                    policy_snapshot,
                    {"followUpRetrieval": follow_up_retrieval},
                    repository=binding.repository,
                    tenant_id=str(
                        follow_up_retrieval.get("tenantId")
                        or os.getenv(
                            "MOONMIND_FOLLOWUP_RETRIEVAL_DEFAULT_TENANT",
                            "default",
                        )
                    ).strip(),
                )
                enforce_required_follow_up_retrieval(
                    follow_up_retrieval,
                    compiled_retrieval,
                )
            except Exception as exc:
                raise CheckpointBranchTurnLaunchError(
                    "retrieval_authority_incompatible",
                    "stored follow-up retrieval authority is incompatible with "
                    "the current launch policy",
                ) from exc

        referenced: dict[str, bytes] = {}
        refs = [
            omnigent.external_state_ref,
            omnigent.head_ref,
            omnigent.workspace_checkpoint_ref,
            omnigent.diff_ref,
            omnigent.resource_manifest_ref,
            omnigent.capture_manifest_ref,
            *omnigent.instruction_refs,
            *omnigent.context_refs,
        ]
        for ref in dict.fromkeys(item for item in refs if item):
            referenced[str(ref)] = await self._read_ref(
                str(ref), field_name="checkpointAuthorityRef"
            )
        validation = validate_restore_material(
            omnigent,
            workflow_id=source_identity.workflow_id,
            run_id=source_identity.run_id,
            logical_step_id=source_identity.logical_step_id,
            step_execution_id=omnigent.step_execution_id,
            attempt_ordinal=omnigent.attempt_ordinal,
            boundary=omnigent.boundary,
            provider_profile_id=profile.profile_id,
            credential_generation=profile.credential_generation,
            repository_baseline=binding.base_commit or "",
            repository_head=omnigent.head_commit,
            artifact_reader=lambda ref: referenced[ref],
            policy_snapshot=policy_snapshot,
        )
        if not validation.valid or not validation.branch_creation_available:
            reason = validation.reasons[0] if validation.reasons else "unknown"
            raise CheckpointBranchTurnLaunchError(
                "checkpoint_restore_validation_failed",
                f"checkpoint branch restore is unavailable: {reason}",
            )
        return checkpoint, profile, policy_snapshot

    async def launch(
        self,
        *,
        workflow_id: str,
        branch_id: str,
        branch_turn_id: str,
        intent: CheckpointBranchTurnLaunchRequest | Mapping[str, Any],
    ) -> WorkflowCheckpointBranchTurn:
        """Claim and start the durable owner; duplicates reuse exact identity."""

        request = (
            intent
            if isinstance(intent, CheckpointBranchTurnLaunchRequest)
            else CheckpointBranchTurnLaunchRequest.model_validate(intent)
        )
        branch, turn, binding, source = await self._load_graph_authority(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
        )
        self._artifact_link = {
            "namespace": source.namespace,
            "workflow_id": source.workflow_id,
            "run_id": source.run_id,
            "link_type": "checkpoint_branch.turn",
            "label": f"Checkpoint Branch turn {branch_turn_id}",
        }
        launch_key = build_branch_turn_launch_idempotency_key(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
        )
        if turn.created_step_execution_id:
            await self._start_claimed_turn(branch=branch, turn=turn, binding=binding)
            return turn

        checkpoint, profile, policy_snapshot = await self._validate_source_authority(
            branch=branch,
            turn=turn,
            binding=binding,
            source=source,
            expected_head_version=request.expected_branch_head_version,
        )
        count = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(WorkflowCheckpointBranchTurn)
                    .where(
                        WorkflowCheckpointBranchTurn.branch_id == branch_id,
                        WorkflowCheckpointBranchTurn.created_at <= turn.created_at,
                    )
                )
            ).scalar_one()
        )
        logical_step_id = branch.logical_step_id or "checkpoint-branch"
        identity = build_branch_turn_execution_identity(
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            logical_step_id=logical_step_id,
            ordinal=max(count, 1),
        )
        locator_id = hashlib.sha256(
            identity.step_execution_id.encode()
        ).hexdigest()[:24]
        runtime_selection = dict(
            (branch.diagnostics or {}).get("runtimeSelection") or {}
        )
        agent_snapshot = runtime_selection.get("agentProfileSnapshot")
        if agent_snapshot is not None:
            try:
                runtime_selection = compile_agent_profile_snapshot_parameters(
                    runtime_selection,
                    snapshot=agent_snapshot,
                )
            except (TypeError, ValueError) as exc:
                raise CheckpointBranchTurnLaunchError(
                    "agent_profile_snapshot_invalid",
                    "stored Agent Profile snapshot cannot be compiled",
                ) from exc
        budget_value = (turn.diagnostics or {}).get(
            "maxBudgetUsd",
            runtime_selection.get("maxBudgetUsd"),
        )
        if budget_value is not None:
            if (
                isinstance(budget_value, bool)
                or not isinstance(budget_value, (int, float))
                or not math.isfinite(float(budget_value))
                or float(budget_value) <= 0
            ):
                raise CheckpointBranchTurnLaunchError(
                    "max_budget_invalid",
                    "stored maximum budget must be a finite positive number",
                )
            runtime_selection["maxBudgetUsd"] = float(budget_value)
        follow_up_retrieval = _canonical_follow_up_retrieval(
            (turn.diagnostics or {}).get("followUpRetrieval")
        )
        model = runtime_selection.get("model") or profile.default_model
        effort = runtime_selection.get("effort") or profile.default_effort
        publish_mode = str(runtime_selection.get("publishMode") or "none")
        repository_branch = binding.work_branch
        runtime_selection = {
            **runtime_selection,
            "providerProfileRef": profile.profile_id,
            "executionProfileRef": checkpoint.omnigent.execution_profile_ref,
            "runtimeId": str(
                getattr(profile.runtime_id, "value", profile.runtime_id)
            ),
            "launchPolicyRef": checkpoint.omnigent.launch_policy_ref,
            "model": model,
            "effort": effort,
            "runtimeContextPolicy": turn.runtime_context_policy,
            "publishMode": publish_mode,
            "gitWorkBranch": binding.work_branch,
        }
        remediation_context_ref = await self._validated_remediation_context_ref(turn)
        branch.diagnostics = {
            **(branch.diagnostics or {}),
            "runtimeSelection": runtime_selection,
        }
        source_immutable = {
            "instructionDigest": f"source:{checkpoint.omnigent.idempotency_key}",
            "runtimeId": str(getattr(profile.runtime_id, "value", profile.runtime_id)),
            "model": model,
            "effort": effort,
            "providerProfileId": profile.profile_id,
            "launchPolicyRef": checkpoint.omnigent.launch_policy_ref,
            "repositoryBranch": checkpoint.omnigent.source_branch,
            "publishMode": checkpoint.omnigent.publication_state,
        }
        requested_immutable = {
            **source_immutable,
            "instructionDigest": turn.instruction_digest,
            "repositoryBranch": repository_branch,
            "publishMode": publish_mode,
            **(
                {"maxBudgetUsd": runtime_selection["maxBudgetUsd"]}
                if "maxBudgetUsd" in runtime_selection
                else {}
            ),
        }
        context = {
            "schemaVersion": "checkpoint-branch-context/v1",
            "workflowId": workflow_id,
            "branchId": branch_id,
            "branchTurnId": branch_turn_id,
            "sourceCheckpointRef": turn.source_checkpoint_ref,
            "instructionRef": turn.instruction_ref,
            "instructionDigest": turn.instruction_digest,
            "runtimeSelection": runtime_selection,
            "followUpRetrieval": follow_up_retrieval,
            **(
                {"remediationContextRef": remediation_context_ref}
                if remediation_context_ref is not None
                else {}
            ),
            "gitBinding": {
                "repository": binding.repository,
                "baseBranch": binding.base_branch,
                "baseCommit": binding.base_commit,
                "workBranch": binding.work_branch,
            },
        }
        context_bytes = json.dumps(
            context, sort_keys=True, separators=(",", ":")
        ).encode()
        context_ref = await self._write_artifact(
            content_type="application/vnd.moonmind.checkpoint-branch-context+json",
            payload=context_bytes,
            kind="runtime.branch_turn.context_bundle.json",
            branch_turn_id=branch_turn_id,
        )
        step_launch = {
            "schemaVersion": "v1",
            "workflowId": identity.workflow_id,
            "runId": identity.semantic_run_id,
            "logicalStepId": identity.logical_step_id,
            "executionOrdinal": identity.execution_ordinal,
            "stepExecutionId": identity.step_execution_id,
            "reason": "checkpoint_branch",
            "runtimeContextPolicy": "fresh_agent_run",
            "contextBundleRef": context_ref,
            "contextBundleDigest": _sha256(context_bytes),
            "preparedInputRefs": list(
                dict.fromkeys(
                    [
                        turn.instruction_ref,
                        turn.source_checkpoint_ref,
                        *(
                            [remediation_context_ref]
                            if remediation_context_ref is not None
                            else []
                        ),
                    ]
                )
            ),
            "runtimeSelection": runtime_selection,
            "runtimeSessionReset": {
                "mode": "new_agent_run",
                "sourceProviderSessionReused": False,
                "sourceOAuthLeaseReused": False,
            },
            "branch": {
                "branchId": branch_id,
                "branchTurnId": branch_turn_id,
                "parentBranchId": branch.parent_branch_id,
                "parentTurnId": turn.parent_turn_id or branch.parent_turn_id,
                "rootCheckpointRef": turn.source_checkpoint_ref,
                "gitWorkBranch": binding.work_branch,
            },
        }
        agent_request = AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            executionProfileRef=profile.profile_id,
            correlationId=branch_turn_id,
            idempotencyKey=launch_key,
            instructionRef=turn.instruction_ref,
            stepExecution=step_launch,
            checkpointRecovery={
                "recoveryAction": "branch_required",
                "omnigentCheckpoint": checkpoint.omnigent.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                ),
                "immutableSource": source_immutable,
                "immutableRequested": requested_immutable,
            },
            inputRefs=list(
                dict.fromkeys(
                    [
                        turn.instruction_ref,
                        context_ref,
                        turn.source_checkpoint_ref,
                        *(
                            [remediation_context_ref]
                            if remediation_context_ref is not None
                            else []
                        ),
                    ]
                )
            ),
            workspaceSpec={
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": locator_id,
                    "relativePath": "repo",
                },
                "repository": binding.repository,
                "startingBranch": binding.base_branch,
                "targetBranch": binding.work_branch,
                "checkoutCommit": binding.base_commit,
            },
            parameters={
                **runtime_selection,
                "repository": binding.repository,
                "startingBranch": binding.base_branch,
                "targetBranch": binding.work_branch,
                "publishMode": publish_mode,
                "model": model,
                "effort": effort,
                "omnigent": {
                    **(
                        dict(runtime_selection.get("omnigent") or {})
                        if isinstance(runtime_selection.get("omnigent"), Mapping)
                        else {}
                    ),
                    "target": {
                        "profileRef": policy_snapshot["boundaries"]["execution"][
                            "profileRef"
                        ],
                        "launchPolicyRef": checkpoint.omnigent.launch_policy_ref,
                    }
                },
                **(
                    {"followUpRetrieval": follow_up_retrieval}
                    if follow_up_retrieval is not None
                    else {}
                ),
            },
        )
        manifest = {
            "schemaVersion": "checkpoint-branch-step-execution/v1",
            "identity": step_launch,
            "agentRunWorkflowId": identity.agent_run_workflow_id,
            "executionOwnerWorkflowId": identity.workflow_id,
            "agentExecutionRequest": agent_request.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
        }
        manifest_bytes = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode()
        manifest_ref = await self._write_artifact(
            content_type="application/vnd.moonmind.step-execution-manifest+json",
            payload=manifest_bytes,
            kind="output.branch_turn.step_execution_manifest.json",
            branch_turn_id=branch_turn_id,
        )
        request_bytes = agent_request.model_dump_json(
            by_alias=True, exclude_none=True
        ).encode()
        agent_request_ref = await self._write_artifact(
            content_type="application/vnd.moonmind.agent-execution-request+json",
            payload=request_bytes,
            kind="runtime.branch_turn.agent_request.json",
            branch_turn_id=branch_turn_id,
        )
        diagnostics = {
            "schemaVersion": "checkpoint-branch-launch-diagnostics/v1",
            "validation": "passed",
            "executionOwner": WORKFLOW_TYPE,
            "sourceAuthorityValidatedBeforeMutation": True,
            "sourceSessionReused": False,
            "sourceOAuthLeaseReused": False,
        }
        diagnostics_ref = await self._write_artifact(
            content_type="application/json",
            payload=json.dumps(diagnostics, sort_keys=True).encode(),
            kind="output.branch_turn.launch_diagnostics.json",
            branch_turn_id=branch_turn_id,
        )
        turn.diagnostics = {
            **(turn.diagnostics or {}),
            "operatorLaunchIdempotencyKey": request.idempotency_key,
        }
        service = CheckpointBranchService(self._session)
        claimed = await service.claim_turn_execution(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            context_bundle_ref=context_ref,
            step_execution_manifest_ref=manifest_ref,
            diagnostics_ref=diagnostics_ref,
            launch_idempotency_key=launch_key,
            created_step_execution_id=identity.step_execution_id,
            runtime_agent_run_id=identity.agent_run_workflow_id,
            agent_request_ref=agent_request_ref,
            execution_workflow_id=identity.workflow_id,
        )
        await self._session.commit()
        await self._start_claimed_turn(branch=branch, turn=claimed, binding=binding)
        await self._session.refresh(claimed)
        return claimed

    def _control_plane_store(self) -> Any:
        """Bind the canonical control-plane store to this owner's engine."""

        from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

        bind = getattr(self._session, "bind", None)
        factory = (
            async_sessionmaker(bind, expire_on_commit=False)
            if bind is not None
            else async_session_maker
        )
        return OmnigentControlPlaneStore(factory)

    async def _admit_canonical_branch_turn(
        self,
        *,
        branch: WorkflowCheckpointBranch,
        turn: WorkflowCheckpointBranchTurn,
        binding: WorkflowCheckpointBranchGitBinding,
        agent_request: AgentExecutionRequest,
    ) -> None:
        """Submit this Checkpoint Branch turn through the canonical boundary.

        A Checkpoint Branch turn is follow-up Omnigent work, so it is not allowed
        to be an independent submission authority (#3707). It enters the one
        canonical boundary naming the artifact-backed checkpoint it branches
        from, the launcher's own idempotency key, and the immutable authority it
        intends to run under. The producer is determined by the trigger the turn
        durably carries, never by a caller-supplied label:

        * a turn with an owned remediation context is the remediation
          controller's attempt (``omnigent.remediation_controller`` /
          ``remediation``);
        * any other turn is an operator-driven linked branch
          (``omnigent.checkpoint_branch_turn`` / ``linked_branch``).

        Either way the operation *is* a branch, so the expected typed decision
        refuses same-session reuse: ``new_session_required`` for a linked branch
        (policy never reuses the source session) and ``branch_required`` for a
        remediation whose work branch differs from the recorded plan -- which is
        exactly the remediation-locked-dimension refusal. That refusal is the
        launcher's authorization to allocate new canonical authority, which is
        what it goes on to do. ``resume_unavailable`` -- a superseded fencing
        generation, a stale session revision, or missing checkpoint evidence --
        means no safe path exists, so the durable owner is never started.

        A source with no canonical session row is a pre-canonical execution: it
        has no canonical authority to bind, so it stays on the existing path
        rather than being migrated outside the #3712 handoff contract.
        """

        from moonmind.omnigent.control_plane import (
            CanonicalTurnRequest,
            TurnSubmissionRefusedError,
            compute_digest,
        )
        from moonmind.omnigent.supervisor_turn_dispatch import production_turn_service
        from moonmind.omnigent.turn_contracts import (
            ImmutableExecutionAuthority,
            OmnigentTurnSource,
        )

        recovery = agent_request.checkpoint_recovery
        checkpoint = (
            dict(recovery.get("omnigentCheckpoint") or {})
            if isinstance(recovery, Mapping)
            else {}
        )
        provider_session_ref = str(
            checkpoint.get("omnigentSessionId") or ""
        ).strip()
        source_workflow_id = str(checkpoint.get("workflowId") or "").strip()
        instruction_ref = str(turn.instruction_ref or "").strip()
        checkpoint_ref = str(turn.source_checkpoint_ref or "").strip()
        if not (provider_session_ref and source_workflow_id and instruction_ref):
            return

        store = self._control_plane_store()
        async with store.transaction() as repos:
            session = await repos.sessions.get_by_scope(
                source_workflow_id, provider_session_ref
            )
        if session is None:
            return

        remediation_context_ref = str(
            (turn.diagnostics or {}).get("remediationContextRef") or ""
        ).strip()
        if remediation_context_ref:
            producer = "omnigent.remediation_controller"
            source = OmnigentTurnSource.REMEDIATION
        else:
            producer = "omnigent.checkpoint_branch_turn"
            source = OmnigentTurnSource.LINKED_BRANCH
        parameters = dict(agent_request.parameters or {})
        requested_authority = ImmutableExecutionAuthority(
            branchRef=str(binding.work_branch or "").strip() or None,
            publicationAuthorityRef=(
                str(parameters.get("publishMode") or "").strip() or None
            ),
        )
        launch_key = build_branch_turn_launch_idempotency_key(
            workflow_id=branch.workflow_id,
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
        )
        try:
            result = await production_turn_service(store).submit_turn(
                CanonicalTurnRequest(
                    producer=producer,
                    session_id=session.session_id,
                    turn_attempt_id=f"turn-{compute_digest(launch_key)[:32]}",
                    idempotency_key=launch_key,
                    instruction_ref=instruction_ref,
                    source=source,
                    parent_turn_attempt_id=session.active_turn_attempt_id,
                    remediation_of_turn_attempt_id=(
                        session.active_turn_attempt_id
                        if remediation_context_ref
                        else None
                    ),
                    remediation_gate_ref=remediation_context_ref or None,
                    checkpoint_ref=checkpoint_ref,
                    step_execution_id=turn.created_step_execution_id,
                    requested_authority=requested_authority,
                )
            )
            result.require_new_canonical_authority()
        except TurnSubmissionRefusedError as exc:
            raise CheckpointBranchTurnLaunchError(
                "canonical_turn_not_admitted",
                "canonical Omnigent turn boundary refused this branch turn: "
                f"{exc.decision.disposition.value}/{exc.decision.reason_code}",
            ) from exc

    async def _start_claimed_turn(
        self,
        *,
        branch: WorkflowCheckpointBranch,
        turn: WorkflowCheckpointBranchTurn,
        binding: WorkflowCheckpointBranchGitBinding,
    ) -> None:
        """Start or reattach after a crash between claim and Temporal start."""

        manifest_ref = str(turn.step_execution_manifest_ref or "").strip()
        if not manifest_ref:
            raise CheckpointBranchTurnLaunchError(
                "launch_claim_incomplete", "claimed turn has no execution manifest"
            )
        manifest_bytes = await self._read_ref(
            manifest_ref, field_name="stepExecutionManifestRef"
        )
        try:
            manifest = json.loads(manifest_bytes)
            agent_request = AgentExecutionRequest.model_validate(
                manifest["agentExecutionRequest"]
            )
        except Exception as exc:
            raise CheckpointBranchTurnLaunchError(
                "launch_manifest_invalid", "claimed turn manifest is invalid"
            ) from exc
        execution_workflow_id = str(
            (turn.diagnostics or {}).get("executionWorkflowId") or ""
        ).strip()
        if not execution_workflow_id:
            raise CheckpointBranchTurnLaunchError(
                "launch_claim_incomplete", "claimed turn has no owner workflow identity"
            )
        # Canonical turn admission precedes the durable owner start on both the
        # first launch and the crash-resume path, so no Checkpoint Branch turn
        # can reach a runtime without a canonical decision recorded for it.
        await self._admit_canonical_branch_turn(
            branch=branch,
            turn=turn,
            binding=binding,
            agent_request=agent_request,
        )
        try:
            source = await self._session.get(
                TemporalExecutionCanonicalRecord, branch.workflow_id
            )
            if source is None:
                raise CheckpointBranchTurnLaunchError(
                    "source_execution_missing", "source workflow is missing"
                )
            await self._client.start_workflow(
                workflow_type=WORKFLOW_TYPE,
                workflow_id=execution_workflow_id,
                input_args={
                    "schemaVersion": "checkpoint-branch-turn-execution/v1",
                    "workflowId": branch.workflow_id,
                    "branchId": branch.branch_id,
                    "branchTurnId": turn.branch_turn_id,
                    "principal": self._principal,
                    "sourceNamespace": source.namespace,
                    "sourceRunId": source.run_id,
                    "agentRunWorkflowId": turn.runtime_agent_run_id,
                    "agentRequest": agent_request.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                    "sourceCheckpointRef": turn.source_checkpoint_ref,
                    "instructionRef": turn.instruction_ref,
                    "workspaceLocator": agent_request.workspace_spec[
                        "workspaceLocator"
                    ],
                    "baseCommit": binding.base_commit,
                },
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except Exception as exc:
            raise CheckpointBranchTurnLaunchError(
                "execution_owner_start_failed",
                "branch turn execution owner could not be started",
            ) from exc


__all__ = [
    "BranchTurnExecutionIdentity",
    "CheckpointBranchTurnExecutionOwner",
    "CheckpointBranchTurnLaunchError",
    "WORKFLOW_TYPE",
    "build_branch_turn_execution_identity",
    "checkpoint_branch_turn_owner_operational",
    "get_checkpoint_branch_artifact_service",
]
