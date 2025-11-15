"""Application services orchestrating run creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from api_service.db import models as db_models

from .action_plan import ActionPlan
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
    ) -> db_models.OrchestratorRun:
        action_plan = await self.repository.create_action_plan(
            steps=plan.steps_as_dict(),
            service_context=plan.service_context,
            generated_by=plan.generated_by,
        )
        run = await self.repository.create_run(
            instruction=plan.instruction,
            target_service=plan.service.compose_service,
            action_plan=action_plan,
            approval_token=approval_token,
            priority=priority or db_models.OrchestratorRunPriority.NORMAL,
        )
        artifact_directory = self.artifact_storage.ensure_run_directory(run.id)
        run.artifact_root = str(artifact_directory)
        await self.repository.initialize_plan_states(run, plan.steps_as_dict())
        await self.repository.commit()
        return run


__all__ = ["OrchestratorService"]
