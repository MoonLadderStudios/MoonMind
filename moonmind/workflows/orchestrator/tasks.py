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
from .storage import (
    ArtifactStorage,
    ArtifactStorageError,
    ArtifactWriteResult,
    resolve_artifact_root,
)

logger = logging.getLogger(__name__)

app = Celery("moonmind.workflows.orchestrator")
app.config_from_object("moonmind.workflows.orchestrator.celeryconfig")

_ENV_QUEUE = os.getenv("ORCHESTRATOR_CELERY_QUEUE")
_DEFAULT_QUEUE: Final[str] = (
    _ENV_QUEUE if _ENV_QUEUE else app.conf.get("task_default_queue", "orchestrator.run")
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
    default_root = Path(settings.spec_workflow.artifacts_root)
    configured = os.getenv("ORCHESTRATOR_ARTIFACT_ROOT")
    return resolve_artifact_root(default_root, configured)


def _build_storage_for_run(run: db_models.OrchestratorRun) -> ArtifactStorage:
    """Return an ``ArtifactStorage`` aligned with the run's configured root."""

    stored_root = run.artifact_root
    if stored_root:
        run_path = Path(stored_root)
        if not run_path.is_absolute():
            run_path = run_path.resolve()

        # ``artifact_root`` may point at the specific run directory or the base root.
        base_path = run_path.parent if run_path.name == str(run.id) else run_path
        storage = ArtifactStorage(base_path)
    else:
        storage = ArtifactStorage(_artifact_root())

    resolved = storage.ensure_run_directory(run.id)
    # Persist the fully qualified run directory for future task executions.
    run.artifact_root = str(resolved)
    return storage


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


_FALLBACK_LOG_NAMES: dict[db_models.OrchestratorPlanStep, str] = {
    db_models.OrchestratorPlanStep.ANALYZE: "analyze.log",
    db_models.OrchestratorPlanStep.PATCH: "patch.log",
    db_models.OrchestratorPlanStep.BUILD: "build.log",
    db_models.OrchestratorPlanStep.RESTART: "restart.log",
    db_models.OrchestratorPlanStep.VERIFY: "verify.log",
    db_models.OrchestratorPlanStep.ROLLBACK: "rollback.log",
}


def _default_failure_log(step: db_models.OrchestratorPlanStep) -> str:
    return _FALLBACK_LOG_NAMES.get(step, f"{step.value}.log")


def _ensure_failure_artifact(
    storage: ArtifactStorage,
    run: db_models.OrchestratorRun,
    step: db_models.OrchestratorPlanStep,
    exc: "CommandRunnerError",
) -> ArtifactWriteResult:
    log_name = _default_failure_log(step)
    lines = [f"{step.value} step failed while executing orchestrator command."]
    output = getattr(exc, "output", None)
    if output:
        lines.append(str(output))
    message = str(exc)
    if message:
        lines.append(message)
    artifact = storage.write_text(run.id, log_name, "\n".join(lines))
    metadata = getattr(exc, "metadata", None)
    if metadata is None:
        metadata = {}
        exc.metadata = metadata
    metadata.setdefault("log", artifact.path)
    exc.artifacts.append(artifact)
    return artifact


async def _record_plan_failure(
    repo: OrchestratorRepository,
    run: db_models.OrchestratorRun,
    step: db_models.OrchestratorPlanStep,
    exc: Exception,
    *,
    storage: ArtifactStorage | None = None,
) -> None:
    finished = _utcnow()
    artifact_ids: list[UUID] = []
    payload: dict[str, object] | None = None
    attachments: Sequence[ArtifactWriteResult] = []
    if isinstance(exc, CommandRunnerError):
        attachments = list(exc.artifacts)
        if not attachments and storage is not None:
            attachments = [_ensure_failure_artifact(storage, run, step, exc)]
        for artifact in attachments:
            record = await repo.add_artifact(
                run_id=run.id,
                artifact_type=_classify_artifact(step, artifact.path),
                path=artifact.path,
                checksum=artifact.checksum,
                size_bytes=artifact.size_bytes,
            )
            artifact_ids.append(record.id)
        metadata = getattr(exc, "metadata", None)
        if isinstance(metadata, dict):
            payload = metadata
    await repo.upsert_plan_step_state(
        run_id=run.id,
        plan_step=step,
        plan_step_status=db_models.OrchestratorPlanStepStatus.FAILED,
        celery_state=db_models.OrchestratorTaskState.FAILURE,
        message=str(exc),
        artifact_refs=artifact_ids,
        payload=payload,
        finished_at=finished,
    )
    await repo.update_run(
        run,
        status=db_models.OrchestratorRunStatus.FAILED,
        completed_at=finished,
    )
    await repo.commit()
    logger.error("Plan step %s failed for run %s: %s", step.value, run.id, exc)


async def _execute_plan_step_async(run_id: UUID, step_name: str) -> dict[str, object]:
    step = db_models.OrchestratorPlanStep(step_name)
    async with get_async_session_context() as session:
        repo = OrchestratorRepository(session)
        run = await repo.get_run(run_id, with_relations=True)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        if run.action_plan is None:
            raise ValueError(f"Run {run_id} missing action plan")

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

        storage: ArtifactStorage | None = None
        try:
            plan_steps = run.action_plan.steps or []
            step_def = _locate_step_definition(plan_steps, step)
            parameters = (
                step_def.get("parameters") if isinstance(step_def, dict) else None
            )
            if parameters is None:
                parameters = {}
            elif not isinstance(parameters, dict):
                raise TypeError("Plan step parameters must be a mapping")

            storage = _build_storage_for_run(run)
            profile = get_service_profile(run.target_service)
            runner = CommandRunner(
                run_id=run.id, profile=profile, artifact_storage=storage
            )
            handler_name = _STEP_HANDLER[step]
            handler = getattr(runner, handler_name)
        except (ArtifactStorageError, KeyError, ValueError, TypeError) as exc:
            await _record_plan_failure(repo, run, step, exc, storage=storage)
            raise

        try:
            result = handler(parameters)
        except (AllowListViolation, CommandRunnerError) as exc:
            await _record_plan_failure(repo, run, step, exc, storage=storage)
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
                if any(
                    entry.get("name") == db_models.OrchestratorPlanStep.ROLLBACK.value
                    for entry in (run.action_plan.steps or [])
                    if isinstance(entry, dict)
                ):
                    await repo.upsert_plan_step_state(
                        run_id=run.id,
                        plan_step=db_models.OrchestratorPlanStep.ROLLBACK,
                        plan_step_status=db_models.OrchestratorPlanStepStatus.SKIPPED,
                        celery_state=db_models.OrchestratorTaskState.SUCCESS,
                        message="Rollback not required",
                        finished_at=finished,
                    )
            elif step == db_models.OrchestratorPlanStep.ROLLBACK:
                await repo.update_run(
                    run,
                    status=db_models.OrchestratorRunStatus.ROLLED_BACK,
                    completed_at=finished,
                )

            await repo.commit()
            logger.info("Plan step %s completed for run %s", step.value, run.id)
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
    logger.info("Completed plan step %s for run %s", step_name, run_id)
    return result


def enqueue_action_plan(
    run_id: UUID,
    steps: Sequence[str | db_models.OrchestratorPlanStep],
    *,
    include_rollback: bool = False,
):
    """Queue the orchestrator plan steps for execution."""

    normalized: list[str] = []
    rollback_present = False
    for raw in steps:
        step = (
            raw
            if isinstance(raw, db_models.OrchestratorPlanStep)
            else db_models.OrchestratorPlanStep(str(raw))
        )
        if step == db_models.OrchestratorPlanStep.ROLLBACK:
            rollback_present = True
            continue
        normalized.append(step.value)

    if not normalized:
        raise ValueError("Action plan does not contain executable steps")

    step_signatures = [
        execute_plan_step.si(str(run_id), step_name) for step_name in normalized
    ]

    rollback_sig = None
    if include_rollback and rollback_present:
        rollback_sig = execute_plan_step.si(
            str(run_id), db_models.OrchestratorPlanStep.ROLLBACK.value
        ).set(queue=_DEFAULT_QUEUE)
        for signature in step_signatures:
            signature.link_error(rollback_sig.clone())

    workflow = chain(*step_signatures)
    logger.info("Queueing orchestrator run %s with steps=%s", run_id, normalized)

    return workflow.apply_async(queue=_DEFAULT_QUEUE)


@app.task(name="moonmind.workflows.orchestrator.tasks.health_check")
def health_check() -> str:
    """Basic health-check task to verify the worker boots successfully."""

    return "ok"


__all__ = ["app", "enqueue_action_plan", "execute_plan_step", "health_check"]
