"""Durable input reads retain the admitted workflow's artifact authority."""

import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, TemporalArtifactRedactionLevel
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.workspace_artifacts import (
    WorkspaceArtifactProjectionError,
    WorkspaceArtifactProjector,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactUnavailableError,
)
from tests.artifact_access import access_for_test


@pytest.mark.asyncio
@pytest.mark.parametrize("use_gateway", [False, True])
@pytest.mark.parametrize("boundary", ["metadata", "stream"])
async def test_attachment_outage_keeps_failure_meaning_and_original_grant(artifacts, tmp_path, monkeypatch, use_gateway, boundary):
    from sqlalchemy.exc import OperationalError
    artifact_id, payload = await _publish(artifacts)
    original = OperationalError("SELECT links", {}, RuntimeError("synthetic DB outage")) if boundary == "metadata" else TimeoutError("synthetic storage outage")
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        projector = WorkspaceArtifactProjector(TemporalOmnigentArtifactGateway(artifacts) if use_gateway else service)
        with monkeypatch.context() as injected:
            if boundary == "metadata":
                async def unavailable(*args, **kwargs):
                    raise original
                injected.setattr(AsyncSession, "execute", unavailable)
            else:
                def unavailable(*args, **kwargs):
                    raise original
                    yield
                injected.setattr(LocalTemporalArtifactStore, "read_chunks", unavailable)
            with pytest.raises(TemporalArtifactUnavailableError) as raised:
                await _project(projector, tmp_path / "fresh", artifact_id)
            assert raised.value.__cause__ is original
            assert raised.value.code == f"artifact_{'authority' if boundary == 'metadata' else 'storage'}_unavailable"
        evidence = await _project(projector, tmp_path / "fresh", artifact_id)
        assert (tmp_path / "fresh" / evidence[0]["path"]).read_bytes() == payload


@pytest_asyncio.fixture
async def artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/attachments.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _publish(sessions, *, workflow_id="parent", protection=None):
    payload = b'{"remainingWork":[{"requirementId":"AC-last","gap":"retry"}]}\n'
    async with sessions() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        artifact, _ = await service.create(
            principal="system:agent_runtime",
            content_type="application/json",
            link=ExecutionRef(
                namespace="default",
                workflow_id=workflow_id,
                run_id="parent-run",
                link_type="input.moonspec_verify_handoff",
            ),
        )
        artifact = await service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="system:agent_runtime",
            payload=payload,
        )
        if protection == "restricted":
            artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        elif protection == "quarantined":
            artifact.metadata_json = {"quarantine": True}
        await session.commit()
        return artifact.artifact_id, payload


async def _project(projector, workspace, artifact_id, *, workflow_id="parent"):
    workspace.mkdir(exist_ok=True)
    return await projector.project_attachments(
        workspace,
        refs=(f"artifact://{artifact_id}",),
        workflow_id=workflow_id,
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_gateway", [False, True], ids=["service", "production-gateway"]
)
async def test_linked_report_reaches_fresh_workspace_without_service_grant(
    artifacts,
    tmp_path,
    use_gateway,
):
    artifact_id, payload = await _publish(artifacts)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=artifact_id,
                access=access_for_test("service:omnigent_workspace_attachment"),
            )
        projector = WorkspaceArtifactProjector(
            TemporalOmnigentArtifactGateway(artifacts) if use_gateway else service
        )
        workspace = tmp_path / "fresh"
        for _ in range(2):
            evidence = await _project(projector, workspace, artifact_id)
            assert len(evidence) == 1
            assert (workspace / Path(evidence[0]["path"])).read_bytes() == payload


@pytest.mark.asyncio
async def test_other_workflow_report_is_denied_under_production_gateway(
    artifacts, tmp_path
):
    artifact_id, _ = await _publish(artifacts, workflow_id="unrelated")
    projector = WorkspaceArtifactProjector(TemporalOmnigentArtifactGateway(artifacts))
    with pytest.raises(WorkspaceArtifactProjectionError) as failure:
        await _project(projector, tmp_path / "fresh", artifact_id)
    assert failure.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
    assert not list((tmp_path / "fresh/.moonmind/attachments").iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("protection", ["restricted", "quarantined"])
async def test_linked_protected_report_still_cannot_enter_workspace(
    artifacts,
    tmp_path,
    protection,
):
    artifact_id, _ = await _publish(artifacts, protection=protection)
    projector = WorkspaceArtifactProjector(TemporalOmnigentArtifactGateway(artifacts))
    with pytest.raises(WorkspaceArtifactProjectionError) as failure:
        await _project(projector, tmp_path / "fresh", artifact_id)
    assert failure.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
    assert protection in str(failure.value)
    assert not list((tmp_path / "fresh/.moonmind/attachments").iterdir())
