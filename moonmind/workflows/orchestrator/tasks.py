"""Orchestrator plan-step execution logic.

This module provides the framework-agnostic core that the DB queue worker
(``queue_worker.py``) calls to execute individual plan steps.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from uuid import UUID

from api_service.db import models as db_models
from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings

from .command_runner import AllowListViolation, CommandRunner, CommandRunnerError
from .metrics import (
    apply_run_snapshot,
    apply_step_snapshot,
    record_run_completed,
    record_run_transition,
    record_step_result,
    record_step_started,
)
from .repositories import OrchestratorRepository
from .service_profiles import get_service_profile
from .storage import (
    ArtifactPathError,
    ArtifactStorage,
    ArtifactStorageError,
    ArtifactWriteResult,
    resolve_artifact_root,
)

logger = logging.getLogger(__name__)


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


def _duration_ms(started_at: datetime | None, finished_at: datetime) -> float | None:
    if started_at is None:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    elapsed = (finished_at - started_at).total_seconds() * 1000
    return max(elapsed, 0.0)


def _resolve_current_task_id() -> str | None:
    """Return the current execution context task id, or ``None``."""
    return None


def _artifact_root() -> Path:
    default_root = Path(settings.workflow.artifacts_root)
    configured = os.getenv("ORCHESTRATOR_ARTIFACT_ROOT")
    return resolve_artifact_root(default_root, configured)


def _build_storage_for_run(run: db_models.OrchestratorRun) -> ArtifactStorage:
    """Return an ``ArtifactStorage`` aligned with the run's configured root."""

    stored_root = run.artifact_root
    configured_root = _artifact_root()
    if stored_root:
        run_path = Path(stored_root)
        if not run_path.is_absolute():
            run_path = run_path.resolve()

        # ``artifact_root`` may point at the specific run directory or the base root.
        base_path = run_path.parent if run_path.name == str(run.id) else run_path
        try:
            storage = ArtifactStorage(base_path)
            resolved = storage.ensure_run_directory(run.id)
        except (ArtifactPathError, PermissionError, OSError):
            storage = ArtifactStorage(configured_root)
            resolved = storage.ensure_run_directory(run.id)
    else:
        storage = ArtifactStorage(configured_root)
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


def _plan_contains_step(
    plan: db_models.OrchestratorActionPlan | None,
    step: db_models.OrchestratorPlanStep,
) -> bool:
    if plan is None:
        return False
    for entry in plan.steps or []:
        if isinstance(entry, dict):
            name = entry.get("name")
        else:
            name = entry
        if name == step.value:
            return True
    return False


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
    started_at: datetime | None = None,
    worker_task_id: str | None = None,
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
        worker_state=db_models.OrchestratorTaskState.FAILURE,
        worker_task_id=worker_task_id,
        message=str(exc),
        artifact_refs=artifact_ids,
        payload=payload,
        finished_at=finished,
    )

    duration_ms = _duration_ms(started_at, finished)
    record_step_result(step.value, "failed", duration_ms)
    run.metrics_snapshot = apply_step_snapshot(
        getattr(run, "metrics_snapshot", None),
        step=step.value,
        status=db_models.OrchestratorPlanStepStatus.FAILED.value,
        duration_ms=duration_ms,
    )

    has_rollback = _plan_contains_step(
        getattr(run, "action_plan", None),
        db_models.OrchestratorPlanStep.ROLLBACK,
    )
    final_failure = (not has_rollback) or (
        step == db_models.OrchestratorPlanStep.ROLLBACK
    )
    run_duration_ms: float | None = None
    if final_failure:
        run_duration_ms = _duration_ms(getattr(run, "started_at", None), finished)
        run.metrics_snapshot = apply_run_snapshot(
            getattr(run, "metrics_snapshot", None),
            status=db_models.OrchestratorRunStatus.FAILED.value,
            duration_ms=run_duration_ms,
        )

    await repo.update_run(
        run,
        status=db_models.OrchestratorRunStatus.FAILED,
        completed_at=finished if final_failure else None,
        metrics_snapshot=run.metrics_snapshot,
    )

    if final_failure:
        record_run_completed(
            db_models.OrchestratorRunStatus.FAILED.value,
            run_duration_ms,
        )
    else:
        record_run_transition(db_models.OrchestratorRunStatus.FAILED.value)

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

        task_id = _resolve_current_task_id()
        started_at = _utcnow()
        await repo.upsert_plan_step_state(
            run_id=run.id,
            plan_step=step,
            plan_step_status=db_models.OrchestratorPlanStepStatus.IN_PROGRESS,
            worker_state=db_models.OrchestratorTaskState.STARTED,
            worker_task_id=task_id,
            started_at=started_at,
        )
        record_step_started(step.value)
        if run.status == db_models.OrchestratorRunStatus.PENDING:
            await repo.update_run(
                run,
                status=db_models.OrchestratorRunStatus.RUNNING,
                started_at=started_at,
            )
            record_run_transition(db_models.OrchestratorRunStatus.RUNNING.value)

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
            await _record_plan_failure(
                repo,
                run,
                step,
                exc,
                storage=storage,
                started_at=started_at,
                worker_task_id=task_id,
            )
            raise

        try:
            result = handler(parameters)
        except (AllowListViolation, CommandRunnerError) as exc:
            await _record_plan_failure(
                repo,
                run,
                step,
                exc,
                storage=storage,
                started_at=started_at,
                worker_task_id=task_id,
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
                worker_state=db_models.OrchestratorTaskState.SUCCESS,
                worker_task_id=task_id,
                message=result.message,
                artifact_refs=artifact_ids,
                payload=result.metadata,
                finished_at=finished,
            )

            duration_ms = _duration_ms(started_at, finished)
            record_step_result(step.value, "succeeded", duration_ms)
            run.metrics_snapshot = apply_step_snapshot(
                getattr(run, "metrics_snapshot", None),
                step=step.value,
                status=db_models.OrchestratorPlanStepStatus.SUCCEEDED.value,
                duration_ms=duration_ms,
            )

            if step == db_models.OrchestratorPlanStep.VERIFY:
                run_duration_ms = _duration_ms(
                    getattr(run, "started_at", None), finished
                )
                run.metrics_snapshot = apply_run_snapshot(
                    getattr(run, "metrics_snapshot", None),
                    status=db_models.OrchestratorRunStatus.SUCCEEDED.value,
                    duration_ms=run_duration_ms,
                )
                await repo.update_run(
                    run,
                    status=db_models.OrchestratorRunStatus.SUCCEEDED,
                    completed_at=finished,
                    metrics_snapshot=run.metrics_snapshot,
                )
                record_run_completed(
                    db_models.OrchestratorRunStatus.SUCCEEDED.value,
                    run_duration_ms,
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
                        worker_state=db_models.OrchestratorTaskState.SUCCESS,
                        message="Rollback not required",
                        finished_at=finished,
                    )
            elif step == db_models.OrchestratorPlanStep.ROLLBACK:
                run_duration_ms = _duration_ms(
                    getattr(run, "started_at", None), finished
                )
                run.metrics_snapshot = apply_run_snapshot(
                    getattr(run, "metrics_snapshot", None),
                    status=db_models.OrchestratorRunStatus.ROLLED_BACK.value,
                    duration_ms=run_duration_ms,
                )
                await repo.update_run(
                    run,
                    status=db_models.OrchestratorRunStatus.ROLLED_BACK,
                    completed_at=finished,
                    metrics_snapshot=run.metrics_snapshot,
                )
                record_run_completed(
                    db_models.OrchestratorRunStatus.ROLLED_BACK.value,
                    run_duration_ms,
                )

            await repo.commit()
            logger.info("Plan step %s completed for run %s", step.value, run.id)
            return {
                "message": result.message,
                "artifacts": [artifact.path for artifact in result.artifacts],
                "metadata": result.metadata or {},
            }


__all__ = ["_execute_plan_step_async"]
