"""Orchestration helpers for Spec Kit workflow (compatibility shim - Celery removed)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from api_service.db.base import get_async_session_context
from moonmind.schemas.workflow_models import RetryWorkflowMode
from moonmind.workflows.speckit_celery import models
from moonmind.workflows.speckit_celery.repositories import SpecWorkflowRepository

logger = logging.getLogger(__name__)


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


async def trigger_spec_workflow_run(
    *,
    feature_key: Optional[str] = None,
    created_by: Optional[UUID] = None,
    requested_by_user_id: Optional[UUID] = None,
    force_phase: Optional[str] = None,
    repository: Optional[str] = None,
) -> TriggeredWorkflow:
    """Create a workflow run record.

    NOTE: Celery dispatch has been removed. This creates the DB record only.
    """

    from uuid import uuid4
    from moonmind.config.settings import settings

    async with get_async_session_context() as session:
        repo = SpecWorkflowRepository(session)
        # Check for any conflicting active run
        if feature_key:
            existing = await repo.find_active_run(feature_key)
            if existing is not None:
                raise WorkflowConflictError(feature_key, existing.id)

        git_repo = repository or settings.spec_workflow.github_repository or ""
        run = await repo.create_run(
            feature_key=feature_key or "",
            repository=git_repo,
            created_by=created_by,
            requested_by_user_id=requested_by_user_id,
        )
        await session.commit()

    logger.info(
        "Workflow run created (Celery dispatch removed): run_id=%s, feature_key=%s",
        run.id,
        feature_key,
    )
    return TriggeredWorkflow(run=run, celery_chain_id="")


async def retry_spec_workflow_run(
    run_id: UUID,
    *,
    notes: Optional[str] = None,
    mode: Optional[RetryWorkflowMode] = None,
) -> TriggeredWorkflow:
    """Retry a failed workflow run.

    NOTE: Celery dispatch has been removed. This resets the DB record only.
    """

    async with get_async_session_context() as session:
        repo = SpecWorkflowRepository(session)
        run = await repo.get_run(run_id, with_relations=True)
        if run is None:
            raise WorkflowRetryError(run_id, f"Run {run_id} not found", code="workflow_not_found")

        if run.status not in (
            models.SpecWorkflowRunStatus.FAILED,
            models.SpecWorkflowRunStatus.SUCCEEDED,
        ):
            raise WorkflowRetryError(
                run_id,
                f"Run {run_id} is not in a retryable state (status={run.status})",
                code="run_not_retryable",
            )

        await repo.update_run(
            run_id,
            status=models.SpecWorkflowRunStatus.QUEUED,
            notes=notes,
        )
        await session.commit()

    logger.info(
        "Workflow run reset for retry (Celery dispatch removed): run_id=%s",
        run_id,
    )
    return TriggeredWorkflow(run=run, celery_chain_id="")


__all__ = [
    "TriggeredWorkflow",
    "WorkflowConflictError",
    "WorkflowRetryError",
    "retry_spec_workflow_run",
    "trigger_spec_workflow_run",
]
