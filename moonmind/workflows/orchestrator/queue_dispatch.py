"""Helpers for dispatching orchestrator runs onto the agent DB queue."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from api_service.db import models as db_models
from moonmind.workflows.agent_queue.job_types import ORCHESTRATOR_RUN_JOB_TYPE
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
        "steps": executable_steps,
        "includeRollback": run_rollback,
        # Required so only orchestrator-capable DB workers claim these jobs.
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


__all__ = [
    "build_orchestrator_queue_payload",
    "enqueue_orchestrator_run_job",
]
