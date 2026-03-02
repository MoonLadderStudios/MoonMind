"""Focused unit tests for AgentQueueService action validation."""

from __future__ import annotations

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
