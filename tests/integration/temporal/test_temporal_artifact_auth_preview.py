"""Integration checks for auth + preview behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, TemporalArtifactRedactionLevel
from moonmind.config.settings import settings
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@asynccontextmanager
async def _db(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path}/temporal_auth_preview_integration.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_preview_metadata_is_exposed_for_restricted_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "local")
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="owner",
                content_type="text/plain",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="owner",
                payload=b"token=hidden",
                content_type="text/plain",
            )
            _artifact, _links, _pinned, policy = await service.get_metadata(
                artifact_id=artifact.artifact_id,
                principal="owner",
            )
            assert policy.preview_artifact_ref is not None
            assert policy.default_read_ref.artifact_id == artifact.artifact_id
