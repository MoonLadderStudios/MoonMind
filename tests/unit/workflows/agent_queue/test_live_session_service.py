"""Unit tests for task-run live-session service behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import AgentQueueService

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def queue_db(tmp_path: Path):
    """Provide isolated async sqlite storage for service tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_queue_live_session_service.db"
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


async def _create_task_job(service: AgentQueueService) -> models.AgentJob:
    """Create one minimal canonical task job for live-session tests."""

    return await service.create_job(
        job_type="task",
        payload={
            "repository": "Moon/Mind",
            "task": {
                "instructions": "Run task",
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
            },
        },
    )


async def test_create_live_session_is_idempotent_when_already_ready(
    tmp_path: Path,
) -> None:
    """Repeated create calls should not downgrade an already READY session."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await _create_task_job(service)

            ready = await service.report_live_session(
                task_run_id=job.id,
                worker_id="worker-1",
                worker_hostname="host-1",
                status=models.AgentJobLiveSessionStatus.READY,
                provider=models.AgentJobLiveSessionProvider.TMATE,
                attach_ro="ssh ro",
                attach_rw="ssh rw",
                web_ro="https://web-ro.example",
                web_rw="https://web-rw.example",
                tmate_session_name="mm-test",
                tmate_socket_path="/tmp/moonmind/tmate/test.sock",
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
            before = await service.get_live_session(task_run_id=job.id)
            repeat = await service.create_live_session(
                task_run_id=job.id,
                actor_user_id=uuid4(),
            )

    assert before is not None
    assert repeat.id == ready.id
    assert repeat.status is models.AgentJobLiveSessionStatus.READY
    assert repeat.attach_ro == "ssh ro"
    assert repeat.expires_at == before.expires_at


async def test_report_live_session_clears_web_links_when_allow_web_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report updates should clear stored web links when allow_web is disabled."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await _create_task_job(service)

            monkeypatch.setattr(settings.spec_workflow, "live_session_allow_web", True)
            initial = await service.report_live_session(
                task_run_id=job.id,
                worker_id="worker-1",
                worker_hostname="host-1",
                status=models.AgentJobLiveSessionStatus.READY,
                provider=models.AgentJobLiveSessionProvider.TMATE,
                attach_ro="ssh ro",
                attach_rw="ssh rw",
                web_ro="https://web-ro.example",
                web_rw="https://web-rw.example",
                tmate_session_name="mm-test",
                tmate_socket_path="/tmp/moonmind/tmate/test.sock",
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
            assert initial.web_ro == "https://web-ro.example"
            assert initial.web_rw_encrypted == "https://web-rw.example"

            monkeypatch.setattr(settings.spec_workflow, "live_session_allow_web", False)
            updated = await service.report_live_session(
                task_run_id=job.id,
                worker_id="worker-1",
                worker_hostname="host-1",
                status=models.AgentJobLiveSessionStatus.READY,
                provider=models.AgentJobLiveSessionProvider.TMATE,
            )

    assert updated.web_ro is None
    assert updated.web_rw_encrypted is None


async def test_grant_live_session_write_hides_web_link_when_allow_web_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write grant should never expose web RW links when allow_web is disabled."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await _create_task_job(service)

            monkeypatch.setattr(settings.spec_workflow, "live_session_allow_web", True)
            await service.report_live_session(
                task_run_id=job.id,
                worker_id="worker-1",
                worker_hostname="host-1",
                status=models.AgentJobLiveSessionStatus.READY,
                provider=models.AgentJobLiveSessionProvider.TMATE,
                attach_ro="ssh ro",
                attach_rw="ssh rw",
                web_ro="https://web-ro.example",
                web_rw="https://web-rw.example",
                tmate_session_name="mm-test",
                tmate_socket_path="/tmp/moonmind/tmate/test.sock",
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )

            monkeypatch.setattr(settings.spec_workflow, "live_session_allow_web", False)
            grant = await service.grant_live_session_write(
                task_run_id=job.id,
                actor_user_id=uuid4(),
                ttl_minutes=15,
            )

    assert grant.attach_rw == "ssh rw"
    assert grant.web_rw is None
