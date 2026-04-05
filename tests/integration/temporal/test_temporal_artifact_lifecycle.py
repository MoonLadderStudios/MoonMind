"""Integration checks for lifecycle cleanup flow."""

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

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@asynccontextmanager
async def _db(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path}/temporal_lifecycle_integration.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_lifecycle_soft_then_hard_delete(tmp_path: Path) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=1,
            )
            artifact, _upload = await service.create(
                principal="owner", content_type="text/plain"
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="owner",
                payload=b"payload",
                content_type="text/plain",
            )
            row = await service._repository.get_artifact(artifact.artifact_id)
            row.expires_at = datetime.now(UTC) - timedelta(days=1)
            await service._repository.commit()

            await service.sweep_lifecycle(
                principal="service:lifecycle", now=datetime.now(UTC)
            )
            refreshed = await service._repository.get_artifact(artifact.artifact_id)
            assert refreshed.status is TemporalArtifactStatus.DELETED

            await service.sweep_lifecycle(
                principal="service:lifecycle",
                now=datetime.now(UTC) + timedelta(seconds=3),
            )
            refreshed = await service._repository.get_artifact(artifact.artifact_id)
            assert refreshed.hard_deleted_at is not None
