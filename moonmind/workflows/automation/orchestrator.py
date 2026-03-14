"""Workflow orchestration for Spec Automation runs.

This module replaces the Celery-based orchestrator from agentkit_celery.
The trigger/retry functions previously dispatched Celery task chains;
they are now stubs that will be wired to Temporal or another executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
from uuid import UUID

from moonmind.schemas.workflow_models import RetryWorkflowMode
from moonmind.workflows.automation import models


@dataclass(slots=True)
class TriggeredWorkflow:
    """Represents a workflow run triggered via the orchestrator."""

    run: models.WorkflowRun

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


def _latest_task_state(
    states: Iterable[models.WorkflowTaskState], task_name: str
) -> Optional[models.WorkflowTaskState]:
    """Return the most recent attempt for the given task name."""

    latest: Optional[models.WorkflowTaskState] = None
    for state in states:
        if state.task_name != task_name:
            continue
        if latest is None:
            latest = state
            continue
        if state.attempt > latest.attempt:
            latest = state
            continue
    return latest


async def trigger_workflow_run(
    *,
    feature_key: Optional[str],
    created_by: Optional[UUID] = None,
    requested_by_user_id: Optional[UUID] = None,
    force_phase: Optional[str] = None,
    repository: Optional[str] = None,
) -> TriggeredWorkflow:
    """Trigger a new Spec Automation workflow run.

    Previously dispatched a Celery task chain. Now creates the DB record
    and is expected to be wired to a Temporal workflow or similar executor.
    """

    from api_service.db.base import get_async_session_context
    from moonmind.workflows.automation.repositories import WorkflowRepository

    if not feature_key:
        raise ValueError("feature_key is required to trigger a workflow run")

    async with get_async_session_context() as session:
        repo = WorkflowRepository(session)

        existing = await repo.find_active_run_for_feature(feature_key)
        if existing is not None:
            raise WorkflowConflictError(feature_key, existing.id)

        run = await repo.create_run(
            feature_key=feature_key,
            created_by=created_by,
            requested_by_user_id=requested_by_user_id,
            repository=repository,
            status=models.WorkflowRunStatus.PENDING,
        )
        await repo.commit()

    return TriggeredWorkflow(run=run)


async def retry_workflow_run(
    run_id: UUID,
    *,
    notes: Optional[str] = None,
    mode: RetryWorkflowMode = RetryWorkflowMode.RESUME_FAILED_TASK,
) -> TriggeredWorkflow:
    """Retry a failed workflow run.

    Previously dispatched a Celery task chain. Now updates the DB record
    and is expected to be wired to a Temporal workflow or similar executor.
    """

    from api_service.db.base import get_async_session_context
    from moonmind.workflows.automation.repositories import WorkflowRepository

    async with get_async_session_context() as session:
        repo = WorkflowRepository(session)
        run = await repo.get_run(run_id, with_relations=True)

        if run is None:
            raise WorkflowRetryError(
                run_id,
                f"Workflow run {run_id} was not found",
                code="workflow_not_found",
            )

        terminal_statuses = {
            models.WorkflowRunStatus.FAILED,
            models.WorkflowRunStatus.CANCELLED,
        }
        if run.status not in terminal_statuses:
            raise WorkflowRetryError(
                run_id,
                f"Workflow run {run_id} is not in a retryable state (status={run.status})",
                code="workflow_not_retryable",
            )

        await repo.update_run(
            run_id,
            status=models.WorkflowRunStatus.PENDING,
        )
        await repo.commit()

    return TriggeredWorkflow(run=run)


__all__ = [
    "TriggeredWorkflow",
    "WorkflowConflictError",
    "WorkflowRetryError",
    "retry_workflow_run",
    "trigger_workflow_run",
]
