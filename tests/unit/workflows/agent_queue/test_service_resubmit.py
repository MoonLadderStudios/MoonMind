"""Unit tests for terminal task resubmit behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import (
    AgentJobStateError,
    AgentQueueRepository,
)
from moonmind.workflows.agent_queue.service import (
    AgentQueueJobAuthorizationError,
    AgentQueueService,
)

pytestmark = [pytest.mark.asyncio]


@asynccontextmanager
async def queue_db(tmp_path: Path) -> AsyncIterator[sessionmaker[AsyncSession]]:
    """Provide isolated async sqlite storage for resubmit tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_queue_resubmit.db"
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


async def _create_task_job(
    service: AgentQueueService,
    *,
    owner_id: UUID | None = None,
) -> models.AgentJob:
    return await service.create_job(
        job_type="task",
        payload={
            "repository": "Moon/Test",
            "task": {"instructions": "Initial objective"},
        },
        priority=1,
        max_attempts=3,
        created_by_user_id=owner_id,
        requested_by_user_id=owner_id,
    )


async def _set_terminal_status(
    repo: AgentQueueRepository,
    job: models.AgentJob,
    status: models.AgentJobStatus,
) -> None:
    now = datetime.now(UTC)
    job.status = status
    if status in {
        models.AgentJobStatus.FAILED,
        models.AgentJobStatus.CANCELLED,
        models.AgentJobStatus.DEAD_LETTER,
        models.AgentJobStatus.SUCCEEDED,
    }:
        job.finished_at = now
    else:
        job.finished_at = None
    if status is models.AgentJobStatus.RUNNING:
        job.started_at = now
    job.updated_at = now
    await repo.commit()


@pytest.mark.parametrize(
    "terminal_status",
    (models.AgentJobStatus.FAILED, models.AgentJobStatus.CANCELLED),
)
async def test_resubmit_job_success_creates_new_job_and_audit_events(
    tmp_path: Path,
    terminal_status: models.AgentJobStatus,
) -> None:
    """Failed/cancelled task jobs should create a new queued replacement."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            source_job = await _create_task_job(service, owner_id=owner_id)
            source_payload_before = dict(source_job.payload)
            source_priority_before = source_job.priority
            source_affinity_before = source_job.affinity_key
            source_max_attempts_before = source_job.max_attempts
            await _set_terminal_status(repo, source_job, terminal_status)

            created = await service.resubmit_job(
                job_id=source_job.id,
                actor_user_id=owner_id,
                job_type="task",
                payload={
                    "repository": "Moon/Test",
                    "task": {"instructions": "Retry objective"},
                },
                priority=7,
                affinity_key="repo/Moon/Test",
                max_attempts=5,
                note="retry with edits",
            )
            source_events = await service.list_events(job_id=source_job.id, limit=50)
            created_events = await service.list_events(job_id=created.id, limit=50)

    assert created.id != source_job.id
    assert created.status is models.AgentJobStatus.QUEUED
    assert created.type == "task"
    assert created.priority == 7
    assert created.affinity_key == "repo/Moon/Test"
    assert created.max_attempts == 5
    assert created.payload["task"]["instructions"] == "Retry objective"

    assert source_job.status is terminal_status
    assert source_job.payload == source_payload_before
    assert source_job.priority == source_priority_before
    assert source_job.affinity_key == source_affinity_before
    assert source_job.max_attempts == source_max_attempts_before

    resubmit_events = [event for event in source_events if event.message == "Job resubmitted"]
    assert resubmit_events
    source_event_payload = resubmit_events[-1].payload or {}
    assert source_event_payload["newJobId"] == str(created.id)
    assert source_event_payload["actorUserId"] == str(owner_id)
    assert "payload" in source_event_payload["changedFields"]
    assert source_event_payload["note"] == "retry with edits"

    assert any(event.message == "Job queued" for event in created_events)
    from_events = [event for event in created_events if event.message == "Job resubmitted from"]
    assert from_events
    created_event_payload = from_events[-1].payload or {}
    assert created_event_payload["sourceJobId"] == str(source_job.id)


@pytest.mark.parametrize(
    "ineligible_status",
    (
        models.AgentJobStatus.QUEUED,
        models.AgentJobStatus.RUNNING,
        models.AgentJobStatus.DEAD_LETTER,
    ),
)
async def test_resubmit_job_rejects_ineligible_status(
    tmp_path: Path,
    ineligible_status: models.AgentJobStatus,
) -> None:
    """Queued/running/dead-letter jobs should reject resubmit requests."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)
            if ineligible_status is not models.AgentJobStatus.QUEUED:
                await _set_terminal_status(repo, job, ineligible_status)

            with pytest.raises(AgentJobStateError):
                await service.resubmit_job(
                    job_id=job.id,
                    actor_user_id=owner_id,
                    job_type="task",
                    payload={
                        "repository": "Moon/Test",
                        "task": {"instructions": "Retry objective"},
                    },
                )


async def test_resubmit_job_rejects_non_task_job_type(tmp_path: Path) -> None:
    """Non-task source jobs are ineligible for resubmit in v1."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)
            job.type = "manifest"
            await _set_terminal_status(repo, job, models.AgentJobStatus.FAILED)

            with pytest.raises(AgentJobStateError):
                await service.resubmit_job(
                    job_id=job.id,
                    actor_user_id=owner_id,
                    job_type="manifest",
                    payload={},
                )


async def test_resubmit_job_rejects_non_owner(tmp_path: Path) -> None:
    """Only owners (or superusers) can resubmit terminal jobs."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)
            await _set_terminal_status(repo, job, models.AgentJobStatus.CANCELLED)

            with pytest.raises(AgentQueueJobAuthorizationError):
                await service.resubmit_job(
                    job_id=job.id,
                    actor_user_id=uuid4(),
                    job_type="task",
                    payload={
                        "repository": "Moon/Test",
                        "task": {"instructions": "Retry objective"},
                    },
                )
