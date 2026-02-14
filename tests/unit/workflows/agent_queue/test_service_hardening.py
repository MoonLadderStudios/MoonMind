"""Unit tests for queue hardening service behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import (
    AgentQueueAuthenticationError,
    AgentQueueService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def queue_db(tmp_path: Path):
    """Provide isolated async sqlite storage for service tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_queue_service_hardening.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield async_session_maker
    finally:
        await engine.dispose()


async def test_issue_and_resolve_worker_token_policy(tmp_path: Path) -> None:
    """Issued worker tokens should resolve to stored policy metadata."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)

            issued = await service.issue_worker_token(
                worker_id="executor-01",
                description="primary",
                allowed_repositories=["Moon/Mind"],
                allowed_job_types=["codex_exec"],
                capabilities=["codex", "git"],
            )
            policy = await service.resolve_worker_token(issued.raw_token)

    assert policy.worker_id == "executor-01"
    assert policy.auth_source == "worker_token"
    assert policy.allowed_repositories == ("Moon/Mind",)
    assert policy.allowed_job_types == ("codex_exec",)
    assert policy.capabilities == ("codex", "git")


async def test_resolve_worker_token_rejects_inactive_token(tmp_path: Path) -> None:
    """Inactive tokens should not authenticate worker actions."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)

            issued = await service.issue_worker_token(worker_id="executor-01")
            await service.revoke_worker_token(token_id=issued.token_record.id)

            with pytest.raises(AgentQueueAuthenticationError):
                await service.resolve_worker_token(issued.raw_token)


async def test_fail_job_retry_backoff_and_dead_letter(tmp_path: Path) -> None:
    """Retryable failures should back off then dead-letter after exhaustion."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(
                repo,
                retry_backoff_base_seconds=5,
                retry_backoff_max_seconds=60,
            )
            job = await service.create_job(
                job_type="codex_exec",
                payload={"instruction": "run"},
                max_attempts=2,
            )
            await service.claim_job(
                worker_id="executor-01",
                lease_seconds=30,
            )

            first = await service.fail_job(
                job_id=job.id,
                worker_id="executor-01",
                error_message="transient",
                retryable=True,
            )
            assert first.status is models.AgentJobStatus.QUEUED
            assert first.next_attempt_at is not None
            assert first.next_attempt_at > datetime.now(UTC)

            first.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            await repo.commit()

            await service.claim_job(worker_id="executor-01", lease_seconds=30)
            second = await service.fail_job(
                job_id=job.id,
                worker_id="executor-01",
                error_message="still broken",
                retryable=True,
            )

    assert second.status is models.AgentJobStatus.DEAD_LETTER
    assert second.next_attempt_at is None


async def test_append_and_list_events_with_after_cursor(tmp_path: Path) -> None:
    """Event reads should support incremental polling with `after` cursor."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="codex_exec",
                payload={"instruction": "run"},
            )
            first = await service.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.INFO,
                message="first",
            )
            second = await service.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.WARN,
                message="second",
            )

            events = await service.list_events(job_id=job.id, after=first.created_at)

    assert len(events) == 1
    assert events[0].id == second.id
