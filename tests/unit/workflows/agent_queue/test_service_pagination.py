"""Unit tests for AgentQueueService cursor pagination helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from moonmind.workflows.agent_queue.service import (
    AgentQueueService,
    AgentQueueValidationError,
)

pytestmark = [pytest.mark.speckit]


def test_job_cursor_round_trip() -> None:
    """Cursor encoder/decoder should round-trip the created_at/id boundary."""

    created_at = datetime(2026, 3, 1, 5, 20, 31, 123000, tzinfo=UTC)
    job_id = uuid4()
    token = AgentQueueService._encode_job_cursor(created_at=created_at, job_id=job_id)

    decoded_created_at, decoded_id = AgentQueueService._decode_job_cursor(token)

    assert decoded_created_at == created_at
    assert decoded_id == job_id


def test_job_cursor_decode_rejects_invalid_payload() -> None:
    """Malformed cursors should fail with validation error."""

    with pytest.raises(AgentQueueValidationError, match="cursor is invalid"):
        AgentQueueService._decode_job_cursor("not-base64")


@pytest.mark.asyncio
async def test_list_jobs_page_clamps_limit_and_builds_next_cursor() -> None:
    """Cursor pagination should clamp limits and compute next cursor from tail item."""

    repository = AsyncMock()
    tail = SimpleNamespace(
        id=uuid4(),
        created_at=datetime(2026, 3, 1, 8, 0, 0, tzinfo=UTC),
    )
    repository.list_jobs_page.return_value = ([tail], True)
    service = AgentQueueService(repository=repository)

    page = await service.list_jobs_page(
        status=None,
        job_type=" task ",
        limit=999,
        cursor=None,
    )

    assert page.page_size == 200
    assert len(page.items) == 1
    assert page.next_cursor
    repository.list_jobs_page.assert_awaited_once_with(
        status=None,
        job_type="task",
        cursor=None,
        limit=200,
    )


@pytest.mark.asyncio
async def test_list_jobs_page_invalid_cursor_skips_repository_call() -> None:
    """Invalid cursor should fail fast before repository pagination call."""

    repository = AsyncMock()
    service = AgentQueueService(repository=repository)

    with pytest.raises(AgentQueueValidationError, match="cursor is invalid"):
        await service.list_jobs_page(limit=50, cursor="bad-cursor")

    repository.list_jobs_page.assert_not_called()
