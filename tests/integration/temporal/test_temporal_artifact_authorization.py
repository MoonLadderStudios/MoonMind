"""Integration checks for artifact authorization boundaries.

Phase 6: verify that the artifact service enforces ownership-based access control
at the workflow boundary, including the restricted-artifact raw-content boundary.
"""

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

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@asynccontextmanager
async def _db(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path}/temporal_artifact_authz.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


class TestArtifactAuthorizationBoundaries:
    """Phase 6: authorization boundary tests for the artifact service."""

    async def test_non_owner_cannot_read_restricted_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When OIDC is enabled, a non-owner principal must be denied read access."""
        monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                artifact, _upload = await service.create(
                    principal="owner@example.com",
                    content_type="text/plain",
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                    payload=b"secret-content",
                    content_type="text/plain",
                )

                # Non-owner read must fail
                with pytest.raises(TemporalArtifactAuthorizationError, match="cannot read"):
                    await service.read(
                        artifact_id=artifact.artifact_id,
                        principal="attacker@example.com",
                    )

                # Owner read must succeed
                payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                )
                assert payload == b"secret-content"

    async def test_service_principal_can_read_any_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Service principals (service:*) bypass ownership checks for reads."""
        monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                artifact, _upload = await service.create(
                    principal="owner@example.com",
                    content_type="text/plain",
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                    payload=b"owner-data",
                    content_type="text/plain",
                )

                payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="service:lifecycle",
                )
                assert payload == b"owner-data"

    async def test_non_owner_cannot_mutate_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When OIDC is enabled, a non-owner principal must be denied mutation access."""
        monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                artifact, _upload = await service.create(
                    principal="owner@example.com",
                    content_type="text/plain",
                )

                # Non-owner write_complete must fail
                with pytest.raises(
                    TemporalArtifactAuthorizationError, match="cannot mutate"
                ):
                    await service.write_complete(
                        artifact_id=artifact.artifact_id,
                        principal="attacker@example.com",
                        payload=b"tampered",
                        content_type="text/plain",
                    )

                # Owner write must succeed
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                    payload=b"owner-content",
                    content_type="text/plain",
                )
                payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                )
                assert payload == b"owner-content"

    async def test_auth_disabled_bypasses_all_checks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When AUTH_PROVIDER=disabled, any principal can read/mutate."""
        monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                artifact, _upload = await service.create(
                    principal="owner",
                    content_type="text/plain",
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="owner",
                    payload=b"content",
                    content_type="text/plain",
                )

                # Any principal can read
                payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="anyone",
                )
                assert payload == b"content"

                # Any principal can mutate
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="anyone",
                    payload=b"updated",
                    content_type="text/plain",
                )
                payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="anyone",
                )
                assert payload == b"updated"
