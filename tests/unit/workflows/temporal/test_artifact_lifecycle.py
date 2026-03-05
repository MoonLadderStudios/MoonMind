"""Lifecycle retention and cleanup tests for Temporal artifacts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, TemporalArtifactStatus
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def temporal_db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_artifacts_lifecycle.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield session_maker
    finally:
        await engine.dispose()


async def test_lifecycle_sweep_is_idempotent_across_soft_and_hard_delete(tmp_path: Path) -> None:
    """Sweep should soft-delete then hard-delete once, and become idempotent."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=3600,
            )

            artifact, _upload = await service.create(principal="user-1", content_type="text/plain")
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"payload",
                content_type="text/plain",
            )

            artifact_row = await service._repository.get_artifact(artifact.artifact_id)
            artifact_row.expires_at = datetime.now(UTC) - timedelta(days=1)
            await service._repository.commit()

            first_now = datetime.now(UTC)
            first = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="run-1",
                now=first_now,
            )
            assert first.expired_candidate_count == 1
            assert first.soft_deleted_count == 1
            assert first.hard_deleted_count == 0

            second = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="run-2",
                now=first_now + timedelta(hours=2),
            )
            assert second.hard_deleted_count == 1

            third = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="run-3",
                now=first_now + timedelta(hours=3),
            )
            assert third.hard_deleted_count == 0

            refreshed = await service._repository.get_artifact(artifact.artifact_id)
            assert refreshed.status is TemporalArtifactStatus.DELETED
            assert refreshed.hard_deleted_at is not None
            assert refreshed.tombstoned_at is not None


async def test_lifecycle_sweep_skips_pinned_artifacts(tmp_path: Path) -> None:
    """Pinned artifacts should remain undeleted during lifecycle sweeps."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            artifact, _upload = await service.create(principal="user-1", content_type="text/plain")
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"payload",
                content_type="text/plain",
            )
            await service.pin(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                reason="keep",
            )

            artifact_row = await service._repository.get_artifact(artifact.artifact_id)
            artifact_row.expires_at = datetime.now(UTC) - timedelta(days=2)
            await service._repository.commit()

            sweep = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="run-pinned",
            )
            assert sweep.soft_deleted_count == 0

            refreshed = await service._repository.get_artifact(artifact.artifact_id)
            assert refreshed.status is TemporalArtifactStatus.COMPLETE
