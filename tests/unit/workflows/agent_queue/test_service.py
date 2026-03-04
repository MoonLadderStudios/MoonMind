"""Focused unit tests for AgentQueueService action validation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from moonmind.workflows.agent_queue.service import (
    AgentQueueService,
    AgentQueueValidationError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    ["retry_step", "hard_reset_step", "resume_from_step"],
)
async def test_apply_control_action_rejects_deferred_recovery_actions(
    action: str,
) -> None:
    repository = AsyncMock()
    service = AgentQueueService(repository=repository)

    with pytest.raises(
        AgentQueueValidationError,
        match="action must be one of: pause, resume, takeover",
    ):
        await service.apply_control_action(
            task_run_id=uuid4(),
            actor_user_id=uuid4(),
            action=action,
        )

    repository.set_job_live_control.assert_not_awaited()
    repository.append_control_event.assert_not_awaited()
    repository.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_runtime_state_persists_checkpoint_for_owned_job() -> None:
    """Runtime checkpoint updates should validate ownership and commit payload state."""

    repository = AsyncMock()
    service = AgentQueueService(repository=repository)
    job_id = uuid4()
    repository.set_job_runtime_state.return_value = SimpleNamespace(id=job_id)
    service._assert_job_worker_ownership = AsyncMock()  # type: ignore[method-assign]

    runtime_state = {
        "runtime": "jules",
        "externalTaskId": "task-123",
        "status": "running",
    }
    job = await service.update_runtime_state(
        job_id=job_id,
        worker_id="worker-1",
        runtime_state=runtime_state,
    )

    assert job.id == job_id
    service._assert_job_worker_ownership.assert_awaited_once_with(  # type: ignore[attr-defined]
        job_id=job_id,
        worker_id="worker-1",
    )
    repository.set_job_runtime_state.assert_awaited_once_with(
        job_id=job_id,
        runtime_state=runtime_state,
    )
    repository.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_runtime_state_rejects_non_serializable_payload() -> None:
    """Runtime checkpoint payloads must be JSON-serializable."""

    repository = AsyncMock()
    service = AgentQueueService(repository=repository)

    with pytest.raises(
        AgentQueueValidationError,
        match="runtimeState must be JSON-serializable",
    ):
        await service.update_runtime_state(
            job_id=uuid4(),
            worker_id="worker-1",
            runtime_state={"bad": object()},
        )

    repository.set_job_runtime_state.assert_not_awaited()
    repository.commit.assert_not_awaited()
