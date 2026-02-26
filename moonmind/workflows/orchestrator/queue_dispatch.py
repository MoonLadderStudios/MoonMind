"""Helpers for dispatching orchestrator runs onto the agent DB queue."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from api_service.db import models as db_models
from moonmind.workflows.agent_queue.job_types import (
    ORCHESTRATOR_RUN_JOB_TYPE,
    ORCHESTRATOR_TASK_JOB_TYPE,
)
from moonmind.workflows.agent_queue.service import AgentQueueService


def _normalize_step_sequence(
    steps: Sequence[str | db_models.OrchestratorPlanStep],
    *,
    include_rollback: bool,
) -> tuple[list[str], bool]:
    executable_steps: list[str] = []
    rollback_present = False

    for raw_step in steps:
        step = (
            raw_step
            if isinstance(raw_step, db_models.OrchestratorPlanStep)
            else db_models.OrchestratorPlanStep(str(raw_step))
        )
        if step is db_models.OrchestratorPlanStep.ROLLBACK:
            rollback_present = True
            continue
        executable_steps.append(step.value)

    if not executable_steps:
        raise ValueError("Action plan does not contain executable steps")

    run_rollback = include_rollback and rollback_present
    return executable_steps, run_rollback


def build_orchestrator_queue_payload(
    *,
    run_id: UUID,
    steps: Sequence[str | db_models.OrchestratorPlanStep],
    include_rollback: bool,
) -> dict[str, object]:
    """Build one queue payload for a full orchestrator run execution."""

    executable_steps, run_rollback = _normalize_step_sequence(
        steps,
        include_rollback=include_rollback,
    )
    return {
        "runId": str(run_id),
        "taskId": str(run_id),
        "steps": executable_steps,
        "includeRollback": run_rollback,
        # Required so only orchestrator-capable DB workers claim these jobs.
        "requiredCapabilities": ["orchestrator"],
    }


def build_orchestrator_task_queue_payload(
    *,
    task_id: UUID,
    steps: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Build queue payload for explicit orchestrator task runtime steps."""

    normalized_steps: list[dict[str, object]] = []
    for entry in steps:
        normalized_steps.append(dict(entry))
    if not normalized_steps:
        raise ValueError("Orchestrator task payload must include at least one step")
    return {
        "taskId": str(task_id),
        "runId": str(task_id),
        "steps": normalized_steps,
        "requiredCapabilities": ["orchestrator"],
    }


async def enqueue_orchestrator_run_job(
    *,
    queue_service: AgentQueueService,
    run_id: UUID,
    steps: Sequence[str | db_models.OrchestratorPlanStep],
    include_rollback: bool,
    priority: int = 0,
) -> UUID:
    """Create one orchestrator queue job and return its queue id."""

    payload = build_orchestrator_queue_payload(
        run_id=run_id,
        steps=steps,
        include_rollback=include_rollback,
    )
    job = await queue_service.create_job(
        job_type=ORCHESTRATOR_RUN_JOB_TYPE,
        payload=payload,
        priority=priority,
        max_attempts=1,
    )
    return job.id


async def enqueue_orchestrator_task_job(
    *,
    queue_service: AgentQueueService,
    task_id: UUID,
    steps: Sequence[dict[str, object]],
    priority: int = 0,
) -> UUID:
    """Create one orchestrator task queue job and return its queue id."""

    payload = build_orchestrator_task_queue_payload(task_id=task_id, steps=steps)
    job = await queue_service.create_job(
        job_type=ORCHESTRATOR_TASK_JOB_TYPE,
        payload=payload,
        priority=priority,
        max_attempts=1,
    )
    return job.id


__all__ = [
    "build_orchestrator_queue_payload",
    "build_orchestrator_task_queue_payload",
    "enqueue_orchestrator_run_job",
    "enqueue_orchestrator_task_job",
]
