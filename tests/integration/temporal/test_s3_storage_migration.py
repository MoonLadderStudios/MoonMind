"""Real disposable S3 transfer, cutover and artifact recovery journeys."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import os
import threading
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4
from urllib.parse import urlsplit

from botocore.exceptions import ClientError
import httpx
import pytest

from api_service.db.models import TemporalArtifactRedactionLevel
from moonmind.workflows.skills.storage_migration import qualify_store, s3_client, transfer_buckets
from moonmind.workflows.temporal.artifacts import (
    S3TemporalArtifactStore, TemporalArtifactAuthorizationError,
    TemporalArtifactRepository, TemporalArtifactService,
)
from tests.integration.temporal.test_temporal_artifact_lifecycle import _db

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture
def stores():
    source_endpoint = os.environ.get("MOONMIND_TEST_SOURCE_S3_ENDPOINT", "http://artifact-source:9000")
    destination_endpoint = os.environ.get("MOONMIND_TEST_S3_ENDPOINT", "http://moonmind-test-temporal-artifacts-s3:9000")
    public_endpoint = os.environ.get("MOONMIND_TEST_S3_PUBLIC_ENDPOINT")
    assert public_endpoint, "Use the hermetic integration runner's disposable published S3 port"
    assert urlsplit(public_endpoint).netloc != urlsplit(destination_endpoint).netloc
    access = os.environ.get("TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID", "minioadmin")
    secret = os.environ.get("TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY", "minioadmin")
    bucket = "moonmind-test-" + uuid4().hex
    clients = [s3_client(endpoint, access, secret) for endpoint in (source_endpoint, destination_endpoint)]
    for client in clients:
        client.create_bucket(Bucket=bucket)

    def adapter(endpoint, public=None):
        return S3TemporalArtifactStore(
            endpoint_url=endpoint, public_endpoint_url=public, bucket=bucket,
            access_key_id=access, secret_access_key=secret, region_name="us-east-1", use_ssl=False,
        )

    yield clients, bucket, adapter(source_endpoint), adapter(destination_endpoint, public_endpoint)
    for client in clients:
        for upload in client.list_multipart_uploads(Bucket=bucket).get("Uploads", []):
            client.abort_multipart_upload(Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"])
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=obj["Key"])
        client.delete_bucket(Bucket=bucket)


@pytest.mark.parametrize("index", [0, 1])
def test_real_store_passes_deployment_read_write_and_presign_qualification(stores, index):
    clients, bucket, _, _ = stores
    qualify_store(clients[index], bucket)
    assert not clients[index].list_multipart_uploads(Bucket=bucket).get("Uploads")
    assert not clients[index].list_objects_v2(Bucket=bucket).get("Contents")


@pytest.mark.parametrize("multipart", [False, True])
def test_adapter_transfer_range_checksum_and_public_download(stores, tmp_path, multipart):
    (source, destination), bucket, old, new = stores
    payload = b"saved step input" if not multipart else os.urandom(9 * 1024 * 1024)
    key = "immutable/" + uuid4().hex
    digest = hashlib.sha256(payload).digest()
    if multipart:
        upload = old.create_multipart_upload(storage_key=key, content_type="application/octet-stream")
        parts = []
        for number, part in enumerate((payload[:5 * 1024 * 1024], payload[5 * 1024 * 1024:]), 1):
            url, headers = old.presign_upload_part(storage_key=key, upload_id=upload, part_number=number, expires_in_seconds=60)
            # Match the artifact clients' raw HTTP body; urllib adds an
            # unsigned form Content-Type to bytes, invalidating S3 v2 signing.
            with httpx.Client(trust_env=False) as client:
                response = client.put(url, content=part, headers=headers, timeout=30)
                assert response.status_code == 200, response.text[:200]
                parts.append({"part_number": number, "etag": response.headers["ETag"]})
        old.complete_multipart_upload(storage_key=key, upload_id=upload, parts=parts)
    else:
        source.put_object(Bucket=bucket, Key=key, Body=payload, Metadata={"sha256": digest.hex()},
                          ContentType="application/json", CacheControl="private", ChecksumSHA256=base64.b64encode(digest).decode())
        assert source.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")["ChecksumSHA256"] == base64.b64encode(digest).decode()
    before = source.head_object(Bucket=bucket, Key=key)
    receipt = transfer_buckets(source, destination, tmp_path / "copy.sqlite", buckets=[bucket])
    assert receipt["verifiedObjects"] == 1
    assert destination.head_object(Bucket=bucket, Key=key)["Metadata"] == before["Metadata"]
    assert destination.head_object(Bucket=bucket, Key=key)["ContentType"] == before["ContentType"]
    assert new.object_matches(key, sha256=digest.hex(), size_bytes=len(payload))
    response = destination.get_object(Bucket=bucket, Key=key, Range="bytes=3-17")
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 206
    assert response["Body"].read() == payload[3:18]
    url = new.presign_download(storage_key=key, expires_in_seconds=60, download_filename="saved.bin")
    public_origin = urlsplit(os.environ["MOONMIND_TEST_S3_PUBLIC_ENDPOINT"])
    assert (urlsplit(url).scheme, urlsplit(url).netloc) == (public_origin.scheme, public_origin.netloc)
    assert urlsplit(url).netloc != urlsplit(destination.meta.endpoint_url).netloc
    with build_opener(ProxyHandler({})).open(url, timeout=30) as response:
        assert response.read() == payload
        assert "saved.bin" in response.headers["Content-Disposition"]
    denied = s3_client(os.environ["MOONMIND_TEST_S3_PUBLIC_ENDPOINT"], "denied-credentials", "invalid-secret")
    with pytest.raises(ClientError) as error:
        denied.get_object(Bucket=bucket, Key=key)
    assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    assert old.read_bytes(key) == payload


def test_transfer_resumes_uncertain_upload_and_refuses_corrupt_copy(stores, tmp_path):
    (source, destination), bucket, old, new = stores
    old.write_bytes("history", b"historical bytes", content_type="text/plain")
    original = destination.upload_fileobj

    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("lost copy acknowledgement")

    destination.upload_fileobj = lose_ack
    state = tmp_path / "resume.sqlite"
    with pytest.raises(OSError, match="acknowledgement"):
        transfer_buckets(source, destination, state, buckets=[bucket])
    destination.upload_fileobj = lambda *a, **k: pytest.fail("confirmed bytes were copied twice")
    assert transfer_buckets(source, destination, state, buckets=[bucket])["verifiedObjects"] == 1
    new.write_bytes("history", b"corrupted bytes!", content_type="text/plain")
    with pytest.raises(ValueError, match="mismatch"):
        transfer_buckets(source, destination, state, buckets=[bucket])
    assert old.read_bytes("history") == b"historical bytes"


def test_unfinished_multipart_is_preserved_until_completed(stores, tmp_path):
    (source, destination), bucket, old, _ = stores
    upload = old.create_multipart_upload(storage_key="active", content_type="text/plain")
    part = old.upload_multipart_part(storage_key="active", upload_id=upload, part_number=1, payload=b"active step")
    with pytest.raises(ValueError, match="multipart"):
        transfer_buckets(source, destination, tmp_path / "multipart.sqlite", buckets=[bucket])
    assert source.list_parts(Bucket=bucket, Key="active", UploadId=upload)["Parts"][0]["ETag"] == part
    old.complete_multipart_upload(storage_key="active", upload_id=upload, parts=[{"part_number": 1, "etag": part}])
    assert transfer_buckets(source, destination, tmp_path / "multipart.sqlite", buckets=[bucket])["verifiedObjects"] == 1


def test_active_writes_cutover_and_rollback_preserve_logical_keys(stores, tmp_path):
    (source, destination), bucket, old, new = stores
    stopped, first_write = threading.Event(), threading.Event()
    confirmed = {}

    def write():
        for number in range(20):
            if stopped.is_set():
                return
            key, value = f"workflow/logical-step/{number}", str(number).encode()
            old.write_bytes(key, value, content_type="text/plain")
            confirmed[key] = value
            first_write.set()

    writer = threading.Thread(target=write)
    writer.start()
    assert first_write.wait(30)
    stopped.set()
    writer.join(30)
    assert not writer.is_alive()
    transfer_buckets(source, destination, tmp_path / "forward.sqlite", buckets=[bucket])
    new.write_bytes("workflow/next-step", b"one confirmed effect", content_type="text/plain")
    confirmed["workflow/next-step"] = b"one confirmed effect"
    transfer_buckets(destination, source, tmp_path / "reverse.sqlite", buckets=[bucket])
    for key, value in confirmed.items():
        assert old.read_bytes(key) == new.read_bytes(key) == value
    assert len(list(source.list_objects_v2(Bucket=bucket)["Contents"])) == len(confirmed)


@pytest.mark.asyncio
async def test_migration_preserves_identity_authorization_pins_and_shared_bytes(stores, tmp_path, monkeypatch):
    (source, destination), bucket, old, new = stores
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with _db(tmp_path) as sessions:
        async with sessions() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session), store=old, lifecycle_hard_delete_after_seconds=1)
            artifact, _ = await service.create(principal="owner", content_type="text/plain", redaction_level=TemporalArtifactRedactionLevel.RESTRICTED)
            await service.write_complete(artifact_id=artifact.artifact_id, principal="owner", payload=b"saved input", content_type="text/plain")
            first, _ = await service.put_content_addressed_payload_complete(principal="owner", payload=b"shared", content_type="text/plain", scope="checkpoint_archive")
            second, reused = await service.put_content_addressed_payload_complete(principal="owner", payload=b"shared", content_type="text/plain", scope="checkpoint_archive")
            assert reused and first.storage_key == second.storage_key
            artifact_id, storage_key = artifact.artifact_id, artifact.storage_key
            await service.pin(artifact_id=artifact_id, principal="owner", reason="immutable input")
            artifact.expires_at = datetime.now(UTC) - timedelta(days=1)
            first.expires_at = datetime.now(UTC) - timedelta(days=1)
            await service._repository.commit()
        transfer_buckets(source, destination, tmp_path / "artifact-copy.sqlite", buckets=[bucket])
        async with sessions() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session), store=new, lifecycle_hard_delete_after_seconds=1)
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(artifact_id=artifact_id, principal="unauthorized")
            row, payload = await service.read(artifact_id=artifact_id, principal="owner")
            assert row.artifact_id == artifact_id and row.storage_key == storage_key
            assert payload == b"saved input"
            now = datetime.now(UTC)
            await service.sweep_lifecycle(principal="service:lifecycle", now=now)
            await service.sweep_lifecycle(principal="service:lifecycle", now=now + timedelta(seconds=3))
            assert new.read_bytes(storage_key) == b"saved input"
            assert new.read_bytes(second.storage_key) == b"shared"
