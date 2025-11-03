"""Workflow orchestration helpers wiring Celery chains."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID

from celery import chain

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery import models
from moonmind.workflows.speckit_celery.repositories import SpecWorkflowRepository
from moonmind.workflows.speckit_celery.tasks import (
    apply_and_publish,
    discover_next_phase,
    submit_codex_job,
)


@dataclass(slots=True)
class TriggeredWorkflow:
    """Represents a workflow run triggered via the orchestrator."""

    run_id: UUID
    celery_chain_id: str


class WorkflowConflictError(RuntimeError):
    """Raised when attempting to start a workflow already in progress."""

    def __init__(self, feature_key: str, run_id: UUID) -> None:
        super().__init__(
            f"Workflow already active for feature '{feature_key}' (run_id={run_id})"
        )
        self.feature_key = feature_key
        self.run_id = run_id


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
        await session.commit()
        return run


async def _store_chain_identifier(run_id: UUID, chain_id: str) -> None:
    async with get_async_session_context() as session:
        repo = SpecWorkflowRepository(session)
        await repo.update_run(run_id, celery_chain_id=chain_id)
        await session.commit()


def trigger_spec_workflow_run(
    *,
    feature_key: Optional[str] = None,
    created_by: Optional[UUID | str] = None,
    force_phase: Optional[str] = None,
) -> TriggeredWorkflow:
    """Create a workflow run and dispatch the Celery chain."""

    effective_feature = feature_key or settings.spec_workflow.default_feature_key
    created_by_uuid: Optional[UUID]
    if isinstance(created_by, UUID) or created_by is None:
        created_by_uuid = created_by
    else:
        created_by_uuid = UUID(str(created_by))

    run = asyncio.run(
        _create_workflow_run(effective_feature, created_by=created_by_uuid)
    )

    task_chain = chain(
        discover_next_phase.s(
            str(run.id), feature_key=effective_feature, force_phase=force_phase
        ),
        submit_codex_job.s(),
        apply_and_publish.s(),
    )
    result = task_chain.apply_async()
    asyncio.run(_store_chain_identifier(run.id, result.id))

    return TriggeredWorkflow(run_id=run.id, celery_chain_id=result.id)


__all__ = [
    "trigger_spec_workflow_run",
    "WorkflowConflictError",
    "TriggeredWorkflow",
]
