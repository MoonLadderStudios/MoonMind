"""Application services orchestrating run creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from api_service.db import models as db_models

from .action_plan import ActionPlan
from .policies import (
    ApprovalPolicy,
    apply_approval_snapshot,
    resolve_policy,
    validate_approval_token,
)
from .repositories import OrchestratorRepository
from .storage import ArtifactStorage


@dataclass(slots=True)
class OrchestratorService:
    """Facade encapsulating orchestrator run creation workflows."""

    repository: OrchestratorRepository
    artifact_storage: ArtifactStorage

    async def create_run(
        self,
        plan: ActionPlan,
        *,
        approval_token: Optional[str],
        priority: db_models.OrchestratorRunPriority | None,
        policy: ApprovalPolicy | None = None,
    ) -> db_models.OrchestratorRun:
        policy = policy or await resolve_policy(
            self.repository, plan.service.compose_service
        )
        granted_at = datetime.now(timezone.utc)
        approved, _ = validate_approval_token(
            policy, approval_token, granted_at=granted_at
        )
        run_status = (
            db_models.OrchestratorRunStatus.PENDING
            if approved or not policy.requires_approval
            else db_models.OrchestratorRunStatus.AWAITING_APPROVAL
        )
        action_plan = await self.repository.create_action_plan(
            steps=plan.steps_as_dict(),
            service_context=plan.service_context,
            generated_by=plan.generated_by,
        )
        run = await self.repository.create_run(
            instruction=plan.instruction,
            target_service=plan.service.compose_service,
            action_plan=action_plan,
            status=run_status,
            approval_gate_id=(policy.gate.id if policy.gate else None),
            approval_token=approval_token if approved else None,
            priority=priority or db_models.OrchestratorRunPriority.NORMAL,
            metrics_snapshot=apply_approval_snapshot(
                None,
                policy=policy,
                token=approval_token if approved else None,
                granted_at=granted_at if approved else None,
            ),
        )
        artifact_directory = self.artifact_storage.ensure_run_directory(run.id)
        run.artifact_root = str(artifact_directory)
        await self.repository.initialize_plan_states(run, plan.steps_as_dict())
        await self.repository.commit()
        return run


__all__ = ["OrchestratorService"]
