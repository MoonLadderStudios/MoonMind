"""Authorization and presign policy tests for Temporal artifact service."""

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
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.agentkit]


@asynccontextmanager
async def temporal_db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_artifacts_auth.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield session_maker
    finally:
        await engine.dispose()


async def test_authenticated_mode_denies_cross_principal_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticated mode should deny raw reads to non-owning principals."""

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "local")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1", content_type="text/plain"
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"secret",
                content_type="text/plain",
            )

            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="user-2",
                )


async def test_disabled_mode_allows_local_default_principal_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled auth mode should permit read operations without user identity checks."""

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="default-user", content_type="text/plain"
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="default-user",
                payload=b"local",
                content_type="text/plain",
            )

            _meta, payload = await service.read(
                artifact_id=artifact.artifact_id,
                principal="another-local-user",
            )
            assert payload == b"local"


async def test_restricted_raw_presign_denied_for_non_owner_in_auth_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticated mode should block restricted raw presign requests from non-owners."""

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "local")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"token=supersecret",
                content_type="text/plain",
            )

            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.presign_download(
                    artifact_id=artifact.artifact_id,
                    principal="user-2",
                )
