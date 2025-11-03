"""Workflow orchestration helpers wiring Celery chains."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from uuid import UUID

from celery import chain

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery import models
from moonmind.workflows.speckit_celery.repositories import SpecWorkflowRepository
from moonmind.workflows.speckit_celery.tasks import (
    TASK_DISCOVER,
    TASK_PUBLISH,
    TASK_SEQUENCE,
    TASK_SUBMIT,
    apply_and_publish,
    discover_next_phase,
    submit_codex_job,
    _base_context,
)


@dataclass(slots=True)
class TriggeredWorkflow:
    """Represents a workflow run triggered via the orchestrator."""

    run: models.SpecWorkflowRun
    celery_chain_id: str

    @property
    def run_id(self) -> UUID:
        """Expose the workflow run identifier for backwards compatibility."""

        return self.run.id


class WorkflowConflictError(RuntimeError):
    """Raised when attempting to start a workflow already in progress."""

    def __init__(self, feature_key: str, run_id: UUID) -> None:
        super().__init__(
            f"Workflow already active for feature '{feature_key}' (run_id={run_id})"
        )
        self.feature_key = feature_key
        self.run_id = run_id


class WorkflowRetryError(RuntimeError):
    """Raised when attempting to retry a workflow that cannot be resumed."""

    def __init__(self, run_id: UUID, message: str, *, code: str = "retry_not_allowed"):
        super().__init__(message)
        self.run_id = run_id
        self.code = code


_TASK_TO_PHASE: dict[str, models.SpecWorkflowRunPhase] = {
    TASK_DISCOVER: models.SpecWorkflowRunPhase.DISCOVER,
    TASK_SUBMIT: models.SpecWorkflowRunPhase.SUBMIT,
    TASK_PUBLISH: models.SpecWorkflowRunPhase.APPLY,
}


def _latest_task_state(
    states: Iterable[models.SpecWorkflowTaskState], task_name: str
) -> Optional[models.SpecWorkflowTaskState]:
    """Return the most recent attempt for the given task name."""

    latest: Optional[models.SpecWorkflowTaskState] = None
    for state in states:
        if state.task_name != task_name:
            continue
        if latest is None:
            latest = state
            continue
        if state.attempt > latest.attempt:
            latest = state
            continue
        if state.attempt == latest.attempt:
            current_ts = state.updated_at or state.finished_at or state.created_at
            latest_ts = latest.updated_at or latest.finished_at or latest.created_at
            if latest_ts is None or (current_ts is not None and current_ts > latest_ts):
                latest = state
    return latest


async def _create_workflow_run(
    feature_key: str,
    *,
    created_by: Optional[UUID] = None,
) -> models.SpecWorkflowRun:
    async with get_async_session_context() as session:
        repo = SpecWorkflowRepository(session)
        existing = await repo.find_active_run_for_feature(feature_key)
        if existing is not None:
            raise WorkflowConflictError(feature_key, existing.id)

        run = await repo.create_run(
            feature_key=feature_key,
            created_by=created_by,
        )
        artifacts_root = Path(settings.spec_workflow.artifacts_root)
        artifacts_dir = artifacts_root / str(run.id)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        await repo.update_run(run.id, artifacts_path=str(artifacts_dir))
        run.artifacts_path = str(artifacts_dir)
        await session.commit()
        return run


async def _store_chain_identifier(run_id: UUID, chain_id: str) -> None:
    async with get_async_session_context() as session:
        repo = SpecWorkflowRepository(session)
        await repo.update_run(run_id, celery_chain_id=chain_id)
        await session.commit()


async def trigger_spec_workflow_run(
    *,
    feature_key: Optional[str] = None,
    created_by: Optional[UUID] = None,
    force_phase: Optional[str] = None,
) -> TriggeredWorkflow:
    """Create a workflow run and dispatch the Celery chain."""

    effective_feature = feature_key or settings.spec_workflow.default_feature_key

    run = await _create_workflow_run(effective_feature, created_by=created_by)

    task_chain = chain(
        discover_next_phase.s(
            str(run.id),
            feature_key=effective_feature,
            force_phase=force_phase,
            attempt=1,
        ),
        submit_codex_job.s(),
        apply_and_publish.s(),
    )
    result = task_chain.apply_async()
    await _store_chain_identifier(run.id, result.id)
    run.celery_chain_id = result.id

    return TriggeredWorkflow(run=run, celery_chain_id=result.id)


async def retry_spec_workflow_run(
    run_id: UUID, *, notes: Optional[str] = None
) -> TriggeredWorkflow:
    """Resume a previously failed workflow run starting at the failing task."""

    async with get_async_session_context() as session:
        repo = SpecWorkflowRepository(session)
        run = await repo.get_run(run_id, with_relations=True)
        if run is None:
            raise WorkflowRetryError(
                run_id,
                f"Workflow run {run_id} was not found",
                code="workflow_not_found",
            )

        if run.status is not models.SpecWorkflowRunStatus.FAILED:
            raise WorkflowRetryError(
                run_id,
                f"Workflow run {run_id} is not in a failed state",
                code="retry_not_allowed",
            )

        task_states = list(run.task_states)
        if not task_states:
            raise WorkflowRetryError(
                run_id,
                "Workflow run has no recorded task states to determine retry point",
            )

        start_index: Optional[int] = None
        latest_by_task: dict[str, models.SpecWorkflowTaskState] = {}
        max_attempt_by_task: dict[str, int] = {}
        for task_name in TASK_SEQUENCE:
            latest_state = _latest_task_state(task_states, task_name)
            if latest_state is not None:
                latest_by_task[task_name] = latest_state
                max_attempt_by_task[task_name] = max(
                    max_attempt_by_task.get(task_name, 0), latest_state.attempt
                )
            if start_index is not None:
                continue
            if latest_state is None or (
                latest_state.status is not models.SpecWorkflowTaskStatus.SUCCEEDED
            ):
                start_index = TASK_SEQUENCE.index(task_name)

        if start_index is None:
            raise WorkflowRetryError(
                run_id,
                "Workflow run has already completed successfully and cannot be retried.",
                code="retry_not_applicable",
            )

        tasks_to_run = TASK_SEQUENCE[start_index:]
        first_task = tasks_to_run[0]
        next_attempt = max_attempt_by_task.get(first_task, 0) + 1

        await repo.ensure_task_state_placeholders(
            workflow_run_id=run_id, task_names=tasks_to_run, attempt=next_attempt
        )

        phase = _TASK_TO_PHASE.get(first_task, run.phase)
        await repo.update_run(
            run_id,
            status=models.SpecWorkflowRunStatus.RUNNING,
            phase=phase,
            finished_at=None,
        )

        await session.commit()

    base_context = _base_context(run)
    base_context["attempt"] = next_attempt
    base_context["retry"] = True
    if notes:
        base_context["retry_notes"] = notes

    discover_state = latest_by_task.get(TASK_DISCOVER)
    if discover_state and discover_state.payload:
        payload = dict(discover_state.payload)
        task_payload = {
            "taskId": payload.get("taskId"),
            "title": payload.get("title"),
            "phase": payload.get("phase"),
            "lineNumber": payload.get("lineNumber"),
        }
        base_context["task"] = task_payload

    for attr in ("codex_task_id", "codex_logs_path", "codex_patch_path", "branch_name"):
        value = getattr(run, attr, None)
        if value:
            base_context[attr] = value

    task_map = {
        TASK_DISCOVER: discover_next_phase,
        TASK_SUBMIT: submit_codex_job,
        TASK_PUBLISH: apply_and_publish,
    }

    first_task_name = tasks_to_run[0]
    if first_task_name == TASK_DISCOVER:
        first_signature = task_map[first_task_name].s(
            str(run.id),
            feature_key=run.feature_key,
            force_phase=None,
            attempt=next_attempt,
            retry_notes=notes,
        )
    else:
        first_signature = task_map[first_task_name].s(dict(base_context))

    signatures = [first_signature]
    for task_name in tasks_to_run[1:]:
        signatures.append(task_map[task_name].s())

    task_chain = chain(*signatures)
    result = task_chain.apply_async()
    await _store_chain_identifier(run.id, result.id)
    run.celery_chain_id = result.id

    return TriggeredWorkflow(run=run, celery_chain_id=result.id)


__all__ = [
    "trigger_spec_workflow_run",
    "WorkflowConflictError",
    "TriggeredWorkflow",
    "retry_spec_workflow_run",
    "WorkflowRetryError",
]
