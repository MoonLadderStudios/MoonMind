"""Checkpoint lock retries restart publication without relaxing sealing."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api_service.db.models import TemporalArtifactLink
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactUnavailableError,
)
from tests.unit.omnigent.test_generic_saved_work_index import fixture
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import temporal_db

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("failures", [1, 4, 5])
async def test_checkpoint_contention_restarts_transaction(
    tmp_path, monkeypatch, failures
):
    _, _, _, request = fixture(tmp_path)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    original = TemporalArtifactRepository.get_artifact_for_update
    attempts = []
    sleeps = []
    active = []

    async def contested(repository, artifact_id, *, wait=True):
        # Exercise rollback after the real root lock and unpublished link exist.
        artifact = await original(repository, artifact_id, wait=wait)
        if not wait and repository._session not in attempts:
            attempts.append(repository._session)
            if len(attempts) <= failures:
                raise TemporalArtifactUnavailableError(
                    artifact_id, dependency="authority", operation="lock_dependencies"
                )
        return artifact

    async def backoff(delay):
        assert not active, "must release the entire transaction before waiting"
        sleeps.append(delay)

    monkeypatch.setattr(
        TemporalArtifactRepository, "get_artifact_for_update", contested
    )
    monkeypatch.setattr("moonmind.omnigent.bridge_artifacts.asyncio.sleep", backoff)
    async with temporal_db(tmp_path) as factory:

        @asynccontextmanager
        async def sessions():
            async with factory() as session:
                active.append(session)
                try:
                    yield session
                finally:
                    active.remove(session)

        gateway = TemporalOmnigentArtifactGateway(sessions)
        kwargs = dict(
            request=request,
            name="checkpoint_archive",
            payload=b"saved bytes",
            link_type="evidence.recovery",
        )
        if failures == 5:
            with pytest.raises(TemporalArtifactUnavailableError) as caught:
                await gateway.write_bytes(**kwargs)
            assert caught.value.operation == "lock_dependencies"
        else:
            ref = await gateway.write_bytes(**kwargs)
            assert await gateway.read_bytes(ref) == b"saved bytes"
            # A lost acknowledgement reuses the accepted identity.
            assert await gateway.write_bytes(**kwargs) == ref
        assert len(sleeps) == min(failures, 4)
        assert sleeps == [0.25, 0.5, 1.0, 2.0][: len(sleeps)]
        assert len({id(s) for s in attempts}) == min(failures + 1, 5) + (failures < 5)
        async with factory() as session:
            links = (await session.scalars(select(TemporalArtifactLink))).all()
            assert len(links) == (0 if failures == 5 else 1)


@pytest.mark.parametrize(
    "error",
    [
        TemporalArtifactUnavailableError(
            "art_input", dependency="authority", operation="get_artifact"
        ),
        TemporalArtifactUnavailableError(
            "art_input", dependency="storage", operation="read"
        ),
        ValueError("digest mismatch"),
        asyncio.CancelledError(),
    ],
)
async def test_non_contention_and_cancellation_are_not_retried(monkeypatch, error):
    gateway = TemporalOmnigentArtifactGateway(None)
    write = AsyncMock(side_effect=error)
    monkeypatch.setattr(gateway, "_write_bytes_once", write, raising=False)
    with pytest.raises(type(error)):
        await gateway.write_bytes(
            request=SimpleNamespace(),
            name="checkpoint_archive",
            payload=b"work",
            link_type="evidence.recovery",
        )
    assert write.await_count == 1


async def test_cancellation_during_backoff_stops_publication(monkeypatch):
    gateway = TemporalOmnigentArtifactGateway(None)
    write = AsyncMock(
        side_effect=TemporalArtifactUnavailableError(
            "art_input", dependency="authority", operation="lock_dependencies"
        )
    )
    monkeypatch.setattr(gateway, "_write_bytes_once", write)
    monkeypatch.setattr(
        "moonmind.omnigent.bridge_artifacts.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )
    with pytest.raises(asyncio.CancelledError):
        await gateway.write_bytes(
            request=SimpleNamespace(),
            name="checkpoint_archive",
            payload=b"work",
            link_type="evidence.recovery",
        )
    assert write.await_count == 1
