"""PostgreSQL evidence for bounded tombstone metadata reclamation.

SQLite cannot prove row locking, so these checks run against the supported
backend: a multi-page backlog drains through the lifecycle owner, and a
protection being written concurrently keeps its tombstone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactPin,
    TemporalArtifactStatus,
    TemporalArtifactStorageBackend,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    generate_artifact_id,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

HORIZON = timedelta(days=7)


@pytest_asyncio.fixture()
async def pg_maker(control_plane_postgres_url):
    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _service(session: AsyncSession, root: Path) -> TemporalArtifactService:
    return TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=LocalTemporalArtifactStore(root),
        lifecycle_purge_after_seconds=int(HORIZON.total_seconds()),
    )


async def _seed_journal_tombstones(maker, count: int, *, deleted_at: datetime):
    """Superseded journal pairs as they accumulate: deleted bytes, kept rows."""

    artifact_ids = [generate_artifact_id(deleted_at) for _ in range(count)]
    async with maker() as session:
        await session.execute(
            insert(TemporalArtifact),
            [
                {
                    "artifact_id": artifact_id,
                    "created_by_principal": "service:omnigent",
                    "storage_backend": TemporalArtifactStorageBackend.LOCAL_FS,
                    "storage_key": f"default/artifacts/{artifact_id}",
                    "status": TemporalArtifactStatus.DELETED,
                    "deleted_at": deleted_at,
                    "hard_deleted_at": deleted_at,
                    "tombstoned_at": deleted_at,
                    "metadata_json": {},
                }
                for artifact_id in artifact_ids
            ],
        )
        await session.execute(
            insert(TemporalArtifactLink),
            [
                {
                    "id": uuid4(),
                    "artifact_id": artifact_id,
                    "namespace": "default",
                    "workflow_id": "wf-journal",
                    "run_id": "run-1",
                    "link_type": "runtime.omnigent.sse.raw",
                }
                for artifact_id in artifact_ids
            ],
        )
        await session.commit()
    return artifact_ids


async def _count(session: AsyncSession, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)))


async def test_backlog_of_journal_tombstones_drains_in_bounded_pages(
    pg_maker, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    await _seed_journal_tombstones(pg_maker, 120, deleted_at=now - HORIZON * 2)
    recent = await _seed_journal_tombstones(pg_maker, 5, deleted_at=now)

    async with pg_maker() as session:
        pages = []
        summary = await _service(session, tmp_path / "artifacts").drain_lifecycle(
            principal="service:lifecycle",
            limit=50,
            time_budget=timedelta(minutes=5),
            on_page=pages.append,
        )

        assert [page.purged_count for page in pages] == [50, 50, 20]
        assert summary.purged_link_count == 120
        assert summary.drained is True
        assert await _count(session, TemporalArtifact) == len(recent)
        assert await _count(session, TemporalArtifactLink) == len(recent)


async def test_tombstone_with_an_in_flight_pin_is_skipped_and_retained(
    pg_maker, tmp_path: Path
) -> None:
    contested, free = await _seed_journal_tombstones(
        pg_maker, 2, deleted_at=datetime.now(UTC) - HORIZON * 2
    )

    async with pg_maker() as pinning, pg_maker() as sweeping:
        # The pin insert holds a foreign-key lock on the tombstone until commit.
        pinning.add(
            TemporalArtifactPin(
                id=uuid4(), artifact_id=contested, pinned_by_principal="operator"
            )
        )
        await pinning.flush()

        summary = await _service(sweeping, tmp_path / "artifacts").sweep_lifecycle(
            principal="service:lifecycle"
        )
        await pinning.commit()

        assert summary.purged_count == 1
        remaining = set(
            (await sweeping.scalars(select(TemporalArtifact.artifact_id))).all()
        )
        assert remaining == {contested}
        assert free not in remaining
        assert await _count(sweeping, TemporalArtifactPin) == 1
