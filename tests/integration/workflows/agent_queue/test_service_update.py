"""Unit tests for queued job update behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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
    AgentQueueValidationError,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def queue_db(tmp_path: Path) -> AsyncIterator[sessionmaker[AsyncSession]]:
    """Provide isolated async sqlite storage for update tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_queue_update.db"
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


async def test_update_queued_job_success_updates_and_appends_event(
    tmp_path: Path,
) -> None:
    """Queued never-started jobs should update fields and append audit event."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)
            prior_updated_at = job.updated_at

            updated = await service.update_queued_job(
                job_id=job.id,
                actor_user_id=owner_id,
                job_type="task",
                payload={
                    "repository": "Moon/Test",
                    "task": {"instructions": "Updated objective"},
                },
                priority=7,
                affinity_key="repo/Moon/Test",
                max_attempts=5,
                expected_updated_at=prior_updated_at,
                note="tuned objective",
            )
            events = await service.list_events(job_id=job.id, limit=50)

    assert updated.priority == 7
    assert updated.max_attempts == 5
    assert updated.affinity_key == "repo/Moon/Test"
    prior_ts = (
        prior_updated_at.replace(tzinfo=UTC)
        if prior_updated_at.tzinfo is None
        else prior_updated_at.astimezone(UTC)
    )
    updated_ts = (
        updated.updated_at.replace(tzinfo=UTC)
        if updated.updated_at.tzinfo is None
        else updated.updated_at.astimezone(UTC)
    )
    assert updated_ts >= prior_ts
    assert any(event.message == "Job updated" for event in events)
    update_events = [event for event in events if event.message == "Job updated"]
    assert update_events
    payload = update_events[-1].payload or {}
    assert payload["actorUserId"] == str(owner_id)
    assert "payload" in payload["changedFields"]
    assert payload["note"] == "tuned objective"


async def test_update_queued_job_allows_stub_actor_for_unowned_job(
    tmp_path: Path,
) -> None:
    """Stub-context users should be able to edit unowned queued jobs."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await _create_task_job(service)
            prior_updated_at = job.updated_at

            updated = await service.update_queued_job(
                job_id=job.id,
                actor_user_id=None,
                job_type="task",
                payload={
                    "repository": "Moon/Test",
                    "task": {"instructions": "Edited objective"},
                },
                expected_updated_at=prior_updated_at,
            )
            events = await service.list_events(job_id=job.id, limit=50)

    assert updated.payload["task"]["instructions"] == "Edited objective"
    update_events = [event for event in events if event.message == "Job updated"]
    assert update_events
    payload = update_events[-1].payload or {}
    assert payload["actorUserId"] is None


async def test_update_queued_job_rejects_stub_actor_for_owned_job(
    tmp_path: Path,
) -> None:
    """Stub-context actor IDs should remain unauthorized when ownership is set."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)

            with pytest.raises(AgentQueueJobAuthorizationError):
                await service.update_queued_job(
                    job_id=job.id,
                    actor_user_id=None,
                    job_type="task",
                    payload={
                        "repository": "Moon/Test",
                        "task": {"instructions": "Updated objective"},
                    },
                )


async def test_update_queued_job_rejects_non_queued_status(tmp_path: Path) -> None:
    """Running jobs should reject queued updates."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)
            await service.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["codex", "git", "gh"],
            )
            with pytest.raises(AgentJobStateError):
                await service.update_queued_job(
                    job_id=job.id,
                    actor_user_id=owner_id,
                    job_type="task",
                    payload={
                        "repository": "Moon/Test",
                        "task": {"instructions": "Updated objective"},
                    },
                )


async def test_update_queued_job_rejects_started_at_not_null(tmp_path: Path) -> None:
    """Queued jobs that already started should reject updates."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)
            job.started_at = datetime.now(UTC)
            await repo.commit()

            with pytest.raises(AgentJobStateError):
                await service.update_queued_job(
                    job_id=job.id,
                    actor_user_id=owner_id,
                    job_type="task",
                    payload={
                        "repository": "Moon/Test",
                        "task": {"instructions": "Updated objective"},
                    },
                )


async def test_update_queued_job_rejects_expected_updated_at_mismatch(
    tmp_path: Path,
) -> None:
    """Optimistic concurrency mismatch should reject updates."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)

            with pytest.raises(AgentJobStateError):
                await service.update_queued_job(
                    job_id=job.id,
                    actor_user_id=owner_id,
                    job_type="task",
                    payload={
                        "repository": "Moon/Test",
                        "task": {"instructions": "Updated objective"},
                    },
                    expected_updated_at=(job.updated_at - timedelta(seconds=1)),
                )


async def test_update_queued_job_rejects_non_owner(tmp_path: Path) -> None:
    """Only the owning user should update queued jobs."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)

            with pytest.raises(AgentQueueJobAuthorizationError):
                await service.update_queued_job(
                    job_id=job.id,
                    actor_user_id=uuid4(),
                    job_type="task",
                    payload={
                        "repository": "Moon/Test",
                        "task": {"instructions": "Updated objective"},
                    },
                )


async def test_update_queued_job_allows_superuser_non_owner(tmp_path: Path) -> None:
    """Superusers should be able to edit queued jobs they do not own."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)
            superuser_id = uuid4()

            updated = await service.update_queued_job(
                job_id=job.id,
                actor_user_id=superuser_id,
                actor_is_superuser=True,
                job_type="task",
                payload={
                    "repository": "Moon/Test",
                    "task": {"instructions": "Updated by operator"},
                },
            )
            events = await service.list_events(job_id=job.id, limit=50)

    assert updated.payload["task"]["instructions"] == "Updated by operator"
    update_events = [event for event in events if event.message == "Job updated"]
    assert update_events
    payload = update_events[-1].payload or {}
    assert payload["actorUserId"] == str(superuser_id)


async def test_update_queued_job_rejects_attachment_mutation_payload(
    tmp_path: Path,
) -> None:
    """Queued updates must reject attachment mutation fields in v1."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            owner_id = uuid4()
            job = await _create_task_job(service, owner_id=owner_id)

            with pytest.raises(AgentQueueValidationError):
                await service.update_queued_job(
                    job_id=job.id,
                    actor_user_id=owner_id,
                    job_type="task",
                    payload={
                        "repository": "Moon/Test",
                        "task": {
                            "instructions": "Updated objective",
                            "attachments": [{"name": "image.png"}],
                        },
                    },
                )
