"""Exercise the Compose MinIO image through the production artifact adapter.

Regression coverage for PR #4263's image-pull failure: the integration runner
must start the real MinIO service, and the image must support artifact I/O.
"""

from hashlib import sha256
import os
from uuid import uuid4

import pytest

from moonmind.workflows.temporal.artifacts import S3TemporalArtifactStore

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize("multipart", [False, True])
def test_compose_minio_artifact_roundtrip(multipart: bool) -> None:
    # This hostname exists only on docker-compose.test.yaml's isolated network.
    # Never consume an operator's production endpoint or bucket for this test.
    bucket = f"moonmind-test-{uuid4().hex}"
    store = S3TemporalArtifactStore(
        endpoint_url="http://moonmind-test-temporal-artifacts-s3:9000",
        bucket=bucket,
        access_key_id=os.environ.get("TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID", "minioadmin"),
        secret_access_key=os.environ.get(
            "TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY", "minioadmin"
        ),
        region_name="us-east-1",
        use_ssl=False,
    )
    key = "regression/artifact.txt"
    payload = b"MoonMind artifact storage regression"
    digest = sha256(payload).hexdigest()
    upload_id = None
    try:
        if multipart:
            upload_id = store.create_multipart_upload(
                storage_key=key, content_type="text/plain"
            )
            etag = store.upload_multipart_part(
                storage_key=key, upload_id=upload_id, part_number=1, payload=payload
            )
            store.complete_multipart_upload(
                storage_key=key,
                upload_id=upload_id,
                parts=[{"etag": etag, "part_number": 1}],
            )
            upload_id = None
        else:
            store.write_bytes(key, payload, content_type="text/plain")
        assert store.read_bytes(key) == payload
        assert store.object_matches(key, sha256=digest, size_bytes=len(payload))
        store.delete(key)
        assert not store.object_matches(key, sha256=digest, size_bytes=len(payload))
    finally:
        if upload_id is not None:
            store.abort_multipart_upload(storage_key=key, upload_id=upload_id)
        store.delete(key)
        store._client.delete_bucket(Bucket=bucket)
