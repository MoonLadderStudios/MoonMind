"""Unit tests for recurring task scheduling service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManifestRecord,
    RecurringTaskRun,
    RecurringTaskRunOutcome,
)
from api_service.services.manifests_service import ManifestsService
from api_service.services.recurring_tasks_service import (
    RecurringTasksService,
    RecurringTaskValidationError,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def recurring_db(tmp_path: Path):
    db_path = tmp_path / "recurring_tasks.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async_session_maker = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield async_session_maker
    finally:
        await engine.dispose()


def _fixture_manifest_content() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "manifests"
        / "phase0"
        / "registry.yaml"
    )
    return path.read_text(encoding="utf-8")


async def test_create_definition_computes_next_run(tmp_path: Path) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringTasksService(session)
            definition = await service.create_definition(
                name="Daily Demo",
                description="Nightly schedule",
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=uuid4(),
                target={
                    "kind": "queue_task",
                    "job": {
                        "type": "task",
                        "priority": 0,
                        "maxAttempts": 3,
                        "payload": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "targetRuntime": "codex",
                            "task": {
                                "instructions": "Queue job",
                                "publish": {"mode": "none"},
                                "skill": {"id": "auto", "args": {}},
                            },
                        },
                    },
                },
                policy={"misfireGraceSeconds": 300},
            )

            assert definition.schedule_type.value == "cron"
            assert definition.next_run_at is not None
            assert definition.next_run_at.hour == 6
            assert definition.next_run_at.minute == 0
            assert definition.version == 1


async def test_scheduler_tick_creates_and_dispatches_runs(tmp_path: Path) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringTasksService(session)
            manifest_service = ManifestsService(session, service._queue_service)

            await manifest_service.upsert_manifest(
                name="demo",
                content=_fixture_manifest_content(),
            )

            owner_id = uuid4()
            task_definition = await service.create_definition(
                name="Queue Task Schedule",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="* * * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=owner_id,
                target={
                    "kind": "queue_task",
                    "job": {
                        "type": "task",
                        "priority": 0,
                        "maxAttempts": 3,
                        "payload": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "targetRuntime": "codex",
                            "task": {
                                "instructions": "Nightly queue task",
                                "publish": {"mode": "none"},
                                "skill": {"id": "auto", "args": {}},
                            },
                        },
                    },
                },
                policy={"catchup": {"mode": "last", "maxBackfill": 2}},
            )
            manifest_definition = await service.create_definition(
                name="Manifest Schedule",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="* * * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=owner_id,
                target={
                    "kind": "manifest_run",
                    "name": "demo",
                    "action": "run",
                    "options": {"dryRun": True},
                },
                policy={},
            )

            now = datetime.now(UTC).replace(second=0, microsecond=0)
            task_definition.next_run_at = now - timedelta(minutes=1)
            manifest_definition.next_run_at = now - timedelta(minutes=1)
            await session.commit()

            scheduled = await service.schedule_due_definitions(
                now=now,
                batch_size=10,
                max_backfill=3,
            )
            assert scheduled >= 2

            dispatched = await service.dispatch_pending_runs(now=now, batch_size=10)
            assert dispatched >= 2

            runs = (
                (
                    await session.execute(
                        select(RecurringTaskRun).order_by(
                            RecurringTaskRun.created_at.asc()
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(runs) >= 2
            outcomes = {run.outcome for run in runs}
            assert RecurringTaskRunOutcome.ENQUEUED in outcomes
            assert any(run.queue_job_type == "task" for run in runs)
            assert any(run.queue_job_type == "manifest" for run in runs)

            manifest = (await session.execute(select(ManifestRecord))).scalars().first()
            assert manifest is not None
            assert manifest.last_run_job_id is not None
            assert manifest.last_run_status == "queued"


async def test_create_definition_rejects_invalid_policy(tmp_path: Path) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringTasksService(session)
            with pytest.raises(RecurringTaskValidationError):
                await service.create_definition(
                    name="Invalid Policy",
                    description=None,
                    enabled=True,
                    schedule_type="cron",
                    cron="0 6 * * *",
                    timezone="UTC",
                    scope_type="personal",
                    scope_ref=None,
                    owner_user_id=uuid4(),
                    target={
                        "kind": "queue_task",
                        "job": {
                            "type": "task",
                            "payload": {
                                "repository": "MoonLadderStudios/MoonMind",
                                "targetRuntime": "codex",
                                "task": {
                                    "instructions": "Queue job",
                                    "publish": {"mode": "none"},
                                    "skill": {"id": "auto", "args": {}},
                                },
                            },
                        },
                    },
                    policy={"catchup": {"mode": "invalid"}},
                )


async def test_compute_due_occurrences_none_catchup_skips_backfill(
    tmp_path: Path,
) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringTasksService(session)
            definition = await service.create_definition(
                name="No Catchup",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="* * * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=uuid4(),
                target={
                    "kind": "queue_task",
                    "job": {
                        "type": "task",
                        "payload": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "targetRuntime": "codex",
                            "task": {
                                "instructions": "Queue job",
                                "publish": {"mode": "none"},
                                "skill": {"id": "auto", "args": {}},
                            },
                        },
                    },
                },
                policy={"catchup": {"mode": "none"}},
            )

            now = datetime.now(UTC).replace(second=0, microsecond=0)
            definition.next_run_at = now - timedelta(minutes=3)
            await session.commit()

            scheduled = await service.schedule_due_definitions(now=now, batch_size=10)
            assert scheduled == 0


async def test_dispatch_pending_runs_applies_zero_misfire_grace(tmp_path: Path) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringTasksService(session)
            definition = await service.create_definition(
                name="Zero Grace",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="* * * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=uuid4(),
                target={
                    "kind": "queue_task",
                    "job": {
                        "type": "task",
                        "payload": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "targetRuntime": "codex",
                            "task": {
                                "instructions": "Queue job",
                                "publish": {"mode": "none"},
                                "skill": {"id": "auto", "args": {}},
                            },
                        },
                    },
                },
                policy={"misfireGraceSeconds": 0},
            )
            now = datetime.now(UTC).replace(second=0, microsecond=0)
            definition.next_run_at = now - timedelta(minutes=2)
            await session.commit()
            await service.schedule_due_definitions(now=now, batch_size=10)
            await service.dispatch_pending_runs(now=now, batch_size=10)

            run = (
                (
                    await session.execute(
                        select(RecurringTaskRun).where(
                            RecurringTaskRun.definition_id == definition.id
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert run is not None
            assert run.outcome is RecurringTaskRunOutcome.SKIPPED
            assert run.message == "Skipped due to misfire grace threshold"


async def test_target_kind_housekeeping_is_rejected(tmp_path: Path) -> None:
    async with recurring_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = RecurringTasksService(session)
            with pytest.raises(RecurringTaskValidationError):
                await service.create_definition(
                    name="Housekeeping",
                    description=None,
                    enabled=True,
                    schedule_type="cron",
                    cron="* * * * *",
                    timezone="UTC",
                    scope_type="personal",
                    scope_ref=None,
                    owner_user_id=uuid4(),
                    target={"kind": "housekeeping", "action": "prune_artifacts"},
                    policy={},
                )
