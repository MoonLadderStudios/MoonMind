"""Retained safe diagnostics repair through the operator's existing HTTP path."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    router,
)
from api_service.auth_providers import get_instance_access_optional
from api_service.db.models import TemporalArtifact, TemporalArtifactRedactionLevel
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = pytest.mark.asyncio
RAW = b"contact alice.fixture@example.test\nUseful reproduction: src/example.py:42"


@pytest_asyncio.fixture
async def preview_sessions(tmp_path):
    async with temporal_db(tmp_path) as sessions:
        yield sessions


@pytest_asyncio.fixture
async def preview_api(tmp_path, preview_sessions):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    sessions = preview_sessions
    async with sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session), store=store
        )
        source, _ = await service.create(
            principal="service:producer",
            content_type="text/plain",
            redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
        )
        await service.write_complete(
            artifact_id=source.artifact_id, principal="service:producer", payload=RAW
        )
        source_id = source.artifact_id
        legacy_id = source.metadata_json["preview_artifact_id"]
        legacy = await session.get(TemporalArtifact, legacy_id)
        legacy.metadata_json = {
            key: value
            for key, value in legacy.metadata_json.items()
            if key != "transformation"
        }
        # Model retained bytes created before the current personal-data transform.
        store.write_bytes(legacy.storage_key, RAW, content_type="text/plain")
        legacy.sha256 = hashlib.sha256(RAW).hexdigest()
        legacy.size_bytes = len(RAW)
        await session.commit()

    async def service_dependency():
        async with sessions() as session:
            yield TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_temporal_artifact_service] = service_dependency
    app.dependency_overrides[get_instance_access_optional] = lambda: OperatorAdmission(
        via="loopback"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, sessions, store, source_id, legacy_id


@pytest.mark.parametrize("quarantined", [False, True])
async def test_operator_repairs_retained_legacy_preview_without_raw_rights(
    preview_api, quarantined
):
    client, sessions, store, source_id, legacy_id = preview_api
    async with sessions() as session:
        source = await session.get(TemporalArtifact, source_id)
        source.metadata_json = {**source.metadata_json, "quarantine": quarantined}
        source.expires_at = datetime.now(UTC) - timedelta(days=1)
        service = TemporalArtifactService(
            TemporalArtifactRepository(session), store=store
        )
        await service.pin(
            artifact_id=source_id, principal="operator", reason="Retained diagnostics"
        )
        original_key = source.storage_key
        original_digest = source.sha256
        original_owner = source.created_by_principal

    assert (await client.get(f"/api/artifacts/{legacy_id}/download")).status_code == 409
    response = await client.get(f"/api/artifacts/{source_id}")
    assert response.status_code == 200, response.text
    metadata = response.json()
    assert metadata["raw_access_allowed"] is False
    assert metadata["preview_artifact_ref"] is not None
    preview_id = metadata["preview_artifact_ref"]["artifact_id"]
    assert preview_id != legacy_id
    assert metadata["default_read_ref"]["artifact_id"] == preview_id
    downloaded = await client.get(f"/api/artifacts/{preview_id}/download?inline=true")
    assert downloaded.status_code == 200, downloaded.text
    assert b"Useful reproduction: src/example.py:42" in downloaded.content
    assert b"alice.fixture@example.test" not in downloaded.content
    assert (await client.get(f"/api/artifacts/{source_id}/download")).status_code == (
        409 if quarantined else 403
    )
    assert (await client.get(f"/api/artifacts/{legacy_id}/download")).status_code == 409
    # A repeated request and a new request session reuse the completed representation.
    again = await client.get(f"/api/artifacts/{source_id}")
    assert again.json()["preview_artifact_ref"]["artifact_id"] == preview_id
    async with sessions() as session:
        source = await session.get(TemporalArtifact, source_id)
        assert (source.storage_key, source.sha256, source.created_by_principal) == (
            original_key,
            original_digest,
            original_owner,
        )
        assert store.read_bytes(source.storage_key) == RAW
        assert (
            store.read_bytes(
                (await session.get(TemporalArtifact, legacy_id)).storage_key
            )
            == RAW
        )
        assert len(list((await session.scalars(select(TemporalArtifact))).all())) == 3


async def test_concurrent_operator_requests_publish_one_preview(
    preview_api, monkeypatch
):
    import time

    client, sessions, store, source_id, _ = preview_api
    async with sessions() as session:
        source_key = (await session.get(TemporalArtifact, source_id)).storage_key
    reads = []
    original = store.read_chunks

    def slow_read(key, **kwargs):
        if key == source_key:
            reads.append(key)
            time.sleep(0.1)
        yield from original(key, **kwargs)

    monkeypatch.setattr(store, "read_chunks", slow_read)
    responses = await asyncio.gather(
        *[client.get(f"/api/artifacts/{source_id}") for _ in range(3)]
    )
    assert [response.status_code for response in responses] == [200] * 3
    refs = {
        response.json()["preview_artifact_ref"]["artifact_id"] for response in responses
    }
    assert len(refs) == 1
    assert len(reads) == 1
    async with sessions() as session:
        assert len(list((await session.scalars(select(TemporalArtifact))).all())) == 3


@pytest.mark.parametrize("failure", ["read", "write", "missing", "integrity"])
async def test_failed_repair_withholds_stale_bytes_then_retries(
    preview_api, monkeypatch, failure
):
    client, sessions, store, source_id, legacy_id = preview_api
    async with sessions() as session:
        source_key = (await session.get(TemporalArtifact, source_id)).storage_key
    reads = []
    read = store.read_chunks
    write = store.write_bytes

    def fail_read(key, **kwargs):
        if key == source_key:
            reads.append(key)
            if failure == "read":
                raise TimeoutError("synthetic storage outage")
            if failure == "missing":
                raise FileNotFoundError("synthetic missing source")
            if failure == "integrity":
                yield b"x" * len(RAW)
                return
        yield from read(key, **kwargs)

    def fail_write(key, body, **kwargs):
        # Model storage accepting bytes but its response being lost. The current
        # pointer must remain unchanged, with ordinary pending-write recovery.
        write(key, body, **kwargs)
        raise TimeoutError("synthetic write response loss")

    monkeypatch.setattr(store, "read_chunks", fail_read)
    if failure == "write":
        monkeypatch.setattr(store, "write_bytes", fail_write)
    response = await client.get(f"/api/artifacts/{source_id}")
    assert response.status_code == (503 if failure in {"read", "write"} else 409), (
        response.text
    )
    assert "alice.fixture@example.test" not in response.text
    assert len(reads) == 1
    async with sessions() as session:
        source = await session.get(TemporalArtifact, source_id)
        assert source.metadata_json["preview_artifact_id"] == legacy_id
        assert store.read_bytes(source.storage_key) == RAW
        assert len(list((await session.scalars(select(TemporalArtifact))).all())) == 2
    assert (await client.get(f"/api/artifacts/{legacy_id}/download")).status_code == 409
    monkeypatch.setattr(store, "read_chunks", read)
    monkeypatch.setattr(store, "write_bytes", write)
    retry = await client.get(f"/api/artifacts/{source_id}")
    assert retry.status_code == 200, retry.text
    ref = retry.json()["preview_artifact_ref"]["artifact_id"]
    assert ref != legacy_id
    assert (
        b"alice.fixture@example.test"
        not in (await client.get(f"/api/artifacts/{ref}/download")).content
    )


async def test_lost_publication_response_reuses_committed_preview(
    preview_api, monkeypatch
):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactUnavailableError

    client, sessions, _, source_id, legacy_id = preview_api
    commit = TemporalArtifactRepository.commit
    lost = False

    async def lose_response(repository):
        nonlocal lost
        await commit(repository)
        if not lost:
            lost = True
            raise TemporalArtifactUnavailableError(
                source_id, dependency="authority", operation="commit"
            )

    monkeypatch.setattr(TemporalArtifactRepository, "commit", lose_response)
    first = await client.get(f"/api/artifacts/{source_id}")
    assert first.status_code == 503, first.text
    async with sessions() as session:
        committed = (await session.get(TemporalArtifact, source_id)).metadata_json[
            "preview_artifact_id"
        ]
        assert committed != legacy_id
    retry = await client.get(f"/api/artifacts/{source_id}")
    assert retry.status_code == 200, retry.text
    assert retry.json()["preview_artifact_ref"]["artifact_id"] == committed
    async with sessions() as session:
        assert len(list((await session.scalars(select(TemporalArtifact))).all())) == 3


@pytest.mark.parametrize("unavailable", ["expired", "deleted"])
async def test_unavailable_source_is_not_repaired(
    preview_api, monkeypatch, unavailable
):
    client, sessions, store, source_id, legacy_id = preview_api
    async with sessions() as session:
        source = await session.get(TemporalArtifact, source_id)
        if unavailable == "expired":
            source.expires_at = datetime.now(UTC) - timedelta(days=1)
        else:
            source.deleted_at = datetime.now(UTC)
        await session.commit()

    def unexpected_read(*args, **kwargs):
        raise AssertionError("Unavailable source bytes were read")

    monkeypatch.setattr(store, "read_chunks", unexpected_read)
    response = await client.get(f"/api/artifacts/{source_id}")
    assert response.status_code == 200, response.text
    assert response.json()["preview_artifact_ref"] is None
    assert response.json()["raw_access_allowed"] is False
    assert (await client.get(f"/api/artifacts/{legacy_id}/download")).status_code == 409


@pytest.mark.parametrize("lying_size", [False, True])
async def test_upgrade_read_is_bounded(preview_api, monkeypatch, lying_size):
    client, sessions, store, source_id, _ = preview_api
    limit = 4 * 1024 * 1024
    async with sessions() as session:
        source = await session.get(TemporalArtifact, source_id)
        source_key = source.storage_key
        source.size_bytes = len(RAW) if lying_size else limit + 1
        await session.commit()
    observed = []
    closed = []
    original = store.read_chunks

    def source_chunks(key, **kwargs):
        if key != source_key:
            yield from original(key, **kwargs)
            return
        assert lying_size, "Declared oversized source must not be loaded"
        try:
            for _ in range(10000):
                observed.append(64 * 1024)
                yield b"x" * (64 * 1024)
        finally:
            closed.append(True)

    monkeypatch.setattr(store, "read_chunks", source_chunks)
    response = await client.get(f"/api/artifacts/{source_id}")
    if lying_size:
        assert response.status_code == 409, response.text
        assert sum(observed) == limit + 64 * 1024
        assert closed == [True]
    else:
        assert response.status_code == 200, response.text
        ref = response.json()["preview_artifact_ref"]["artifact_id"]
        preview = await client.get(f"/api/artifacts/{ref}/download")
        assert preview.status_code == 200
        assert b"withheld" in preview.content.lower()
        assert not observed
