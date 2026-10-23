"""Authorization and presign policy tests for Temporal artifact service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, TemporalArtifactRedactionLevel
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio]

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

    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
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

@pytest.mark.parametrize("surface", ["read", "metadata", "list", "latest"])
@pytest.mark.parametrize(
    "principal", ["another-local-user", "service:legacy-reader", ""]
)
async def test_retired_selector_cannot_bypass_artifact_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    principal: str,
) -> None:
    """Old configuration grants neither browser nor machine artifact authority."""
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="default-user", content_type="text/plain",
                link=ExecutionRef(
                    namespace="default", workflow_id="local-run",
                    run_id="local-run-1", link_type="input.test",
                ),
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="default-user",
                payload=b"local",
                content_type="text/plain",
            )

            if surface in {"read", "metadata"}:
                with pytest.raises(TemporalArtifactAuthorizationError):
                    if surface == "read":
                        await service.read(artifact_id=artifact.artifact_id, principal=principal)
                    else:
                        await service.get_metadata(artifact_id=artifact.artifact_id, principal=principal)
            else:
                async def listed():
                    return await service.list_for_execution(
                        namespace="default", workflow_id="local-run", run_id="local-run-1",
                        link_type="input.test", principal=principal,
                        latest_only=surface == "latest",
                    )
                if principal:
                    assert await listed() == []
                else:
                    with pytest.raises(TemporalArtifactAuthorizationError):
                        await listed()


async def test_restricted_raw_presign_denied_for_non_owner_in_auth_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticated mode should block restricted raw presign requests from non-owners."""

    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
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

async def test_restricted_report_metadata_uses_preview_default_read_without_raw_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-463: Sensitive reports should expose preview/default-read without raw access."""

    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="owner",
                content_type="text/markdown",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                link={
                    "namespace": "moonmind",
                    "workflow_id": "wf-report",
                    "run_id": "run-report",
                    "link_type": "report.primary",
                },
                metadata_json={"title": "Restricted report"},
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="owner",
                payload=b"# Report\ntoken=secret\nsafe summary",
                content_type="text/markdown",
            )

            _artifact, _links, _pinned, policy = await service.get_metadata(
                artifact_id=artifact.artifact_id,
                principal="operator",
            )

            assert policy.raw_access_allowed is False
            assert policy.preview_artifact_ref is not None
            assert (
                policy.default_read_ref.artifact_id
                == policy.preview_artifact_ref.artifact_id
            )
            assert policy.default_read_ref.artifact_id != artifact.artifact_id
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.presign_download(
                    artifact_id=artifact.artifact_id,
                    principal="operator",
                )


@pytest.mark.parametrize("auth_mode", ["accounts", "oidc", "header"])
@pytest.mark.parametrize("latest_only", [False, True])
async def test_execution_list_requires_owner_or_admitted_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auth_mode: str,
    latest_only: bool,
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", str(auth_mode or ""))
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="service:producer", content_type="text/plain",
                link=ExecutionRef(namespace="default", workflow_id="run-a",
                                  run_id="run-a-1", link_type="input.test"),
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id, principal="service:producer",
                payload=b"input", content_type="text/plain",
            )
            query = dict(namespace="default", workflow_id="run-a", run_id="run-a-1",
                         link_type="input.test", latest_only=latest_only)
            for principal in ("service:consumer", "system", "workflow:run-b"):
                assert await service.list_for_execution(principal=principal, **query) == []
                assert await service.list_for_execution(
                    principal=principal, admitted_principal="workflow:run-b", **query
                ) == []
            with pytest.raises(TemporalArtifactAuthorizationError, match="principal required"):
                await service.list_for_execution(principal="", **query)
            for principal, admitted in (
                ("service:producer", None), ("workflow:run-a", None),
                ("service:consumer", "workflow:run-a"), ("operator", None),
                ("00000000-0000-0000-0000-000000000000", None),
            ):
                listed = await service.list_for_execution(
                    principal=principal, admitted_principal=admitted, **query
                )
                assert [row.artifact_id for row in listed] == [artifact.artifact_id]
