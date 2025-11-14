"""Celery tasks powering the MoonMind orchestrator service."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Sequence
from uuid import UUID

from celery import Celery, chain, current_task

from api_service.db import models as db_models
from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings

from .command_runner import AllowListViolation, CommandRunner, CommandRunnerError
from .repositories import OrchestratorRepository
from .service_profiles import get_service_profile
from .storage import ArtifactStorage

logger = logging.getLogger(__name__)

app = Celery("moonmind.workflows.orchestrator")
app.config_from_object("moonmind.workflows.orchestrator.celeryconfig")

_DEFAULT_QUEUE: Final[str] = app.conf.get(
    "task_default_queue", os.getenv("ORCHESTRATOR_CELERY_QUEUE", "orchestrator.run")
)
app.conf.update(
    task_default_queue=_DEFAULT_QUEUE,
    task_default_routing_key=app.conf.get("task_default_routing_key", _DEFAULT_QUEUE),
)


_STEP_HANDLER = {
    db_models.OrchestratorPlanStep.ANALYZE: "analyze",
    db_models.OrchestratorPlanStep.PATCH: "patch",
    db_models.OrchestratorPlanStep.BUILD: "build",
    db_models.OrchestratorPlanStep.RESTART: "restart",
    db_models.OrchestratorPlanStep.VERIFY: "verify",
    db_models.OrchestratorPlanStep.ROLLBACK: "rollback",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _artifact_root() -> Path:
    configured = os.getenv("ORCHESTRATOR_ARTIFACT_ROOT")
    if configured:
        return Path(configured)
    return Path(settings.spec_workflow.artifacts_root)


def _classify_artifact(
    step: db_models.OrchestratorPlanStep, artifact_path: str
) -> db_models.OrchestratorRunArtifactType:
    lowered = artifact_path.lower()
    if lowered.endswith(".diff"):
        return db_models.OrchestratorRunArtifactType.PATCH_DIFF
    if step == db_models.OrchestratorPlanStep.BUILD:
        return db_models.OrchestratorRunArtifactType.BUILD_LOG
    if step == db_models.OrchestratorPlanStep.VERIFY:
        return db_models.OrchestratorRunArtifactType.VERIFY_LOG
    if step == db_models.OrchestratorPlanStep.ROLLBACK:
        return db_models.OrchestratorRunArtifactType.ROLLBACK_LOG
    return db_models.OrchestratorRunArtifactType.METRICS


def _locate_step_definition(
    plan_steps: Sequence[dict[str, object]], step: db_models.OrchestratorPlanStep
) -> dict[str, object]:
    for entry in plan_steps:
        name = entry.get("name") if isinstance(entry, dict) else None
        if name == step.value:
            return entry  # type: ignore[return-value]
    raise ValueError(f"Action plan missing step '{step.value}'")


async def _execute_plan_step_async(
    run_id: UUID, step_name: str
) -> dict[str, object]:
    step = db_models.OrchestratorPlanStep(step_name)
    async with get_async_session_context() as session:
        repo = OrchestratorRepository(session)
        run = await repo.get_run(run_id, with_relations=True)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        if run.action_plan is None:
            raise ValueError(f"Run {run_id} missing action plan")

        plan_steps = run.action_plan.steps or []
        step_def = _locate_step_definition(plan_steps, step)
        parameters = step_def.get("parameters") if isinstance(step_def, dict) else None
        if parameters is None:
            parameters = {}
        elif not isinstance(parameters, dict):
            raise TypeError("Plan step parameters must be a mapping")

        task_id = getattr(getattr(current_task, "request", None), "id", None)
        started_at = _utcnow()
        await repo.upsert_plan_step_state(
            run_id=run.id,
            plan_step=step,
            plan_step_status=db_models.OrchestratorPlanStepStatus.IN_PROGRESS,
            celery_state=db_models.OrchestratorTaskState.STARTED,
            celery_task_id=task_id,
            started_at=started_at,
        )
        if run.status == db_models.OrchestratorRunStatus.PENDING:
            await repo.update_run(
                run,
                status=db_models.OrchestratorRunStatus.RUNNING,
                started_at=started_at,
            )

        storage = ArtifactStorage(_artifact_root())
        storage.ensure_run_directory(run.id)

        profile = get_service_profile(run.target_service)
        runner = CommandRunner(
            run_id=run.id, profile=profile, artifact_storage=storage
        )
        handler_name = _STEP_HANDLER[step]
        handler = getattr(runner, handler_name)

        try:
            result = handler(parameters)
        except (AllowListViolation, CommandRunnerError) as exc:
            finished = _utcnow()
            await repo.upsert_plan_step_state(
                run_id=run.id,
                plan_step=step,
                plan_step_status=db_models.OrchestratorPlanStepStatus.FAILED,
                celery_state=db_models.OrchestratorTaskState.FAILURE,
                message=str(exc),
                finished_at=finished,
            )
            await repo.update_run(
                run,
                status=db_models.OrchestratorRunStatus.FAILED,
                completed_at=finished,
            )
            await repo.commit()
            logger.error(
                "Plan step %s failed for run %s: %s", step.value, run.id, exc
            )
            raise
        else:
            finished = _utcnow()
            artifact_ids = []
            for artifact in result.artifacts:
                record = await repo.add_artifact(
                    run_id=run.id,
                    artifact_type=_classify_artifact(step, artifact.path),
                    path=artifact.path,
                    checksum=artifact.checksum,
                    size_bytes=artifact.size_bytes,
                )
                artifact_ids.append(record.id)

            await repo.upsert_plan_step_state(
                run_id=run.id,
                plan_step=step,
                plan_step_status=db_models.OrchestratorPlanStepStatus.SUCCEEDED,
                celery_state=db_models.OrchestratorTaskState.SUCCESS,
                message=result.message,
                artifact_refs=artifact_ids,
                payload=result.metadata,
                finished_at=finished,
            )

            if step == db_models.OrchestratorPlanStep.VERIFY:
                await repo.update_run(
                    run,
                    status=db_models.OrchestratorRunStatus.SUCCEEDED,
                    completed_at=finished,
                )
            elif step == db_models.OrchestratorPlanStep.ROLLBACK:
                await repo.update_run(
                    run,
                    status=db_models.OrchestratorRunStatus.ROLLED_BACK,
                    completed_at=finished,
                )

            await repo.commit()
            logger.info(
                "Plan step %s completed for run %s", step.value, run.id
            )
            return {
                "message": result.message,
                "artifacts": [artifact.path for artifact in result.artifacts],
                "metadata": result.metadata or {},
            }


@app.task(bind=True, name="moonmind.workflows.orchestrator.tasks.execute_plan_step")
def execute_plan_step(self, run_id: str, step_name: str) -> dict[str, object]:
    """Celery task entry point executing a single plan step."""

    logger.info(
        "Starting plan step %s for run %s (task_id=%s)",
        step_name,
        run_id,
        getattr(self.request, "id", None),
    )
    result = asyncio.run(_execute_plan_step_async(UUID(run_id), step_name))
    logger.info(
        "Completed plan step %s for run %s", step_name, run_id
    )
    return result


def enqueue_action_plan(
    run_id: UUID,
    steps: Sequence[str | db_models.OrchestratorPlanStep],
    *,
    include_rollback: bool = False,
):
    """Queue the orchestrator plan steps for execution."""

    normalized: list[str] = []
    for raw in steps:
        step = (
            raw
            if isinstance(raw, db_models.OrchestratorPlanStep)
            else db_models.OrchestratorPlanStep(str(raw))
        )
        if not include_rollback and step == db_models.OrchestratorPlanStep.ROLLBACK:
            continue
        normalized.append(step.value)

    if not normalized:
        raise ValueError("Action plan does not contain executable steps")

    workflow = chain(
        *(execute_plan_step.si(str(run_id), step_name) for step_name in normalized)
    )
    logger.info("Queueing orchestrator run %s with steps=%s", run_id, normalized)
    return workflow.apply_async(queue=_DEFAULT_QUEUE)


@app.task(name="moonmind.workflows.orchestrator.tasks.health_check")
def health_check() -> str:
    """Basic health-check task to verify the worker boots successfully."""

    return "ok"


__all__ = ["app", "enqueue_action_plan", "execute_plan_step", "health_check"]
