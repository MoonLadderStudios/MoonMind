"""Two-phase journey: the runner restarts the real store between these checks."""

from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
from uuid import uuid4

import pytest

from moonmind.workflows.skills.storage_migration import transfer_buckets
from moonmind.workflows.temporal.artifacts import (
    S3TemporalArtifactStore, TemporalArtifactAuthorizationError,
    TemporalArtifactRepository, TemporalArtifactService,
)
from tests.integration.temporal.test_temporal_artifact_lifecycle import _db

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def state_dir():
    path = Path("artifacts/integration-ci/storage-restart")
    path.mkdir(parents=True, exist_ok=True)
    return path


def store(bucket, source=False):
    endpoint = os.environ.get("MOONMIND_TEST_SOURCE_S3_ENDPOINT", "http://artifact-source:9000") if source else os.environ.get("MOONMIND_TEST_S3_ENDPOINT", "http://moonmind-test-temporal-artifacts-s3:9000")
    return S3TemporalArtifactStore(
        endpoint_url=endpoint, bucket=bucket,
        access_key_id=os.environ.get("TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID", "minioadmin"),
        secret_access_key=os.environ.get("TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY", "minioadmin"),
        region_name="us-east-1", use_ssl=False,
    )


async def test_seed_migrated_saved_inputs(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    root = state_dir()
    bucket = "moonmind-restart-" + uuid4().hex
    database_root = root / bucket
    database_root.mkdir()
    old, new = store(bucket, source=True), store(bucket)
    workspace = root / "worker-workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "saved.py").write_text("confirmed work survives host cleanup\n")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(workspace / "saved.py", arcname="saved.py")
    payload = buffer.getvalue()
    async with _db(database_root) as sessions, sessions() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(repository, store=old)
        saved, _ = await service.put_content_addressed_payload_complete(principal="owner", payload=payload, content_type="application/gzip", scope="checkpoint_archive")
        shared, reused = await service.put_content_addressed_payload_complete(principal="owner", payload=payload, content_type="application/gzip", scope="checkpoint_archive")
        assert reused and shared.storage_key == saved.storage_key
        immutable, _ = await service.create(principal="owner", content_type="application/json")
        await service.write_complete(artifact_id=immutable.artifact_id, principal="owner", payload=b'{"objective":"restore saved step"}', content_type="application/json")
        await service.pin(artifact_id=immutable.artifact_id, principal="owner", reason="saved step input")
        now = datetime.now(UTC)
        saved.expires_at = shared.expires_at = immutable.expires_at = now - timedelta(days=1)
        claims = [await repository.acquire_use_claim(artifact_id=shared.artifact_id, owner_principal="owner", request_id=request, operation_kind="restore", expires_at=now + timedelta(hours=1)) for request in ("restore-a", "restore-b")]
        await repository.commit()
        manifest = {"bucket": bucket, "savedId": saved.artifact_id, "sharedId": shared.artifact_id,
                    "inputId": immutable.artifact_id, "storageKey": saved.storage_key,
                    "sha256": hashlib.sha256(payload).hexdigest(), "claimIds": sorted(str(claim.id) for claim in claims)}
    transfer_buckets(old._client, new._client, database_root / "copy.sqlite", buckets=[bucket], binding={"bucket": bucket})
    shutil.rmtree(workspace)
    (root / "manifest.json").write_text(json.dumps(manifest))


async def test_retrieve_migrated_saved_inputs_after_restart(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    root = state_dir()
    manifest = json.loads((root / "manifest.json").read_text())
    assert not (root / "worker-workspace").exists()
    old, new = store(manifest["bucket"], source=True), store(manifest["bucket"])
    async with _db(root / manifest["bucket"]) as sessions, sessions() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(repository, store=new, lifecycle_hard_delete_after_seconds=1)
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=manifest["inputId"], principal="other")
        row, payload = await service.read(artifact_id=manifest["inputId"], principal="owner")
        assert row.artifact_id == manifest["inputId"]
        assert payload == b'{"objective":"restore saved step"}'
        claims = await repository.list_use_claims(manifest["sharedId"])
        assert sorted(str(claim.id) for claim in claims) == manifest["claimIds"]
        await repository.release_use_claim(artifact_id=manifest["sharedId"], owner_principal="owner", request_id="restore-a")
        await repository.commit()
        # Sweeps run with a fresh worker identity map. SQLite otherwise keeps
        # timezone-naive claim objects beside aware sweep cutoffs in this fixture.
        session.expunge_all()
        now = datetime.now(UTC)
        await service.sweep_lifecycle(principal="service:lifecycle", now=now)
        await service.sweep_lifecycle(principal="service:lifecycle", now=now + timedelta(seconds=3))
        claims = await repository.list_use_claims(manifest["sharedId"])
        assert [claim.request_id for claim in claims] == ["restore-b"]
        payload = new.read_bytes(manifest["storageKey"])
        assert hashlib.sha256(payload).hexdigest() == manifest["sha256"]
        assert old.read_bytes(manifest["storageKey"]) == payload
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            assert archive.extractfile("saved.py").read() == b"confirmed work survives host cleanup\n"
        assert (await service.read(artifact_id=manifest["inputId"], principal="owner"))[1] == b'{"objective":"restore saved step"}'
