"""Unit tests for local-dev Temporal artifact service behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TemporalArtifactRedactionLevel,
    TemporalArtifactStatus,
    TemporalArtifactStorageBackend,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStore,
    TemporalArtifactValidationError,
    build_artifact_ref,
    generate_artifact_id,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def temporal_db(tmp_path: Path):
    """Provide isolated async sqlite DB for Temporal artifact tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_artifacts.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield session_maker
    finally:
        await engine.dispose()


class _MultipartMemoryStore(TemporalArtifactStore):
    """In-memory multipart-capable store used for service unit tests."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._uploads: dict[str, dict[int, bytes]] = {}

    @property
    def backend(self):
        return TemporalArtifactStorageBackend.S3

    @property
    def supports_multipart(self) -> bool:
        return True

    def build_storage_key(self, *, namespace: str, artifact_id: str, now):
        _ = now
        return f"{namespace}/artifacts/{artifact_id}"

    def write_bytes(
        self, storage_key: str, payload: bytes, *, content_type=None
    ) -> None:
        _ = content_type
        self._objects[storage_key] = payload

    def read_bytes(self, storage_key: str) -> bytes:
        return self._objects[storage_key]

    def read_chunks(self, storage_key: str, *, chunk_size: int = 64 * 1024):
        data = self._objects[storage_key]
        for idx in range(0, len(data), chunk_size):
            yield data[idx : idx + chunk_size]

    def read_path(self, storage_key: str):
        raise TemporalArtifactValidationError("memory store has no path reads")

    def delete(self, storage_key: str) -> None:
        self._objects.pop(storage_key, None)

    def presign_single_upload(
        self, *, storage_key: str, content_type, expires_in_seconds: int
    ):
        _ = content_type, expires_in_seconds
        return f"https://example.test/upload/{storage_key}", {}

    def create_multipart_upload(self, *, storage_key: str, content_type=None) -> str:
        _ = content_type
        upload_id = f"upload-{storage_key}"
        self._uploads[upload_id] = {}
        return upload_id

    def presign_upload_part(
        self,
        *,
        storage_key: str,
        upload_id: str,
        part_number: int,
        expires_in_seconds: int,
    ):
        _ = storage_key, expires_in_seconds
        return f"https://example.test/upload-part/{upload_id}/{part_number}", {}

    def complete_multipart_upload(
        self, *, storage_key: str, upload_id: str, parts: list[dict]
    ):
        assembled = b""
        for part in sorted(parts, key=lambda row: row["part_number"]):
            etag = part["etag"]
            assembled += self._uploads[upload_id][int(etag)]
        self._objects[storage_key] = assembled

    def abort_multipart_upload(self, *, storage_key: str, upload_id: str):
        _ = storage_key
        self._uploads.pop(upload_id, None)

    def presign_download(self, *, storage_key: str, expires_in_seconds: int):
        _ = expires_in_seconds
        return f"https://example.test/download/{storage_key}"

    def put_part(self, upload_id: str, part_number: int, payload: bytes) -> str:
        self._uploads[upload_id][part_number] = payload
        return str(part_number)


async def test_generate_artifact_id_uses_art_prefix_and_ulid_shape() -> None:
    """Generated artifact IDs should follow ``art_<ULID>`` shape."""

    artifact_id = generate_artifact_id()
    assert artifact_id.startswith("art_")
    suffix = artifact_id[len("art_") :]
    assert len(suffix) == 26
    allowed = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert set(suffix).issubset(allowed)


async def test_local_store_rejects_traversal_storage_key(tmp_path: Path) -> None:
    """Storage key traversal attempts should be rejected."""

    store = LocalTemporalArtifactStore(tmp_path)
    with pytest.raises(TemporalArtifactValidationError):
        store.resolve_storage_key("../escape.txt")


async def test_create_write_read_and_list_for_execution(tmp_path: Path) -> None:
    """Service should create, upload, read, and list artifacts by execution linkage."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            artifact, upload = await service.create(
                principal="user-1",
                content_type="text/plain",
                link={
                    "namespace": "moonmind",
                    "workflow_id": "wf-1",
                    "run_id": "run-1",
                    "link_type": "output.primary",
                    "label": "Final output",
                },
            )
            assert upload.upload_url.endswith(f"/{artifact.artifact_id}/content")

            completed = await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"artifact-bytes",
                content_type="text/plain",
            )
            assert completed.status is TemporalArtifactStatus.COMPLETE
            ref = build_artifact_ref(completed)
            assert ref.artifact_id == artifact.artifact_id
            assert ref.size_bytes == len(b"artifact-bytes")

            _artifact, payload = await service.read(
                artifact_id=artifact.artifact_id,
                principal="user-1",
            )
            assert payload == b"artifact-bytes"

            listed = await service.list_for_execution(
                namespace="moonmind",
                workflow_id="wf-1",
                run_id="run-1",
                principal="user-1",
            )
            assert [item.artifact_id for item in listed] == [artifact.artifact_id]


async def test_compute_preview_redacts_token_like_pairs(tmp_path: Path) -> None:
    """Preview activity should redact token/password assignments."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
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
                payload=b"token=abc123\npassword: letmein\nok=true",
                content_type="text/plain",
            )

            preview_ref = await service.compute_preview(
                artifact_id=artifact.artifact_id,
                principal="user-1",
            )
            _preview, preview_bytes = await service.read(
                artifact_id=preview_ref.artifact_id,
                principal="user-1",
            )
            preview_text = preview_bytes.decode("utf-8")
            assert "abc123" not in preview_text
            assert "letmein" not in preview_text
            assert "[REDACTED]" in preview_text


async def test_create_rejects_declared_size_over_local_limit(tmp_path: Path) -> None:
    """Service should fail fast when declared bytes exceed direct-upload max."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                direct_upload_max_bytes=4,
            )
            with pytest.raises(TemporalArtifactValidationError, match="max bytes"):
                await service.create(
                    principal="user-1",
                    content_type="text/plain",
                    size_bytes=5,
                )


async def test_create_rejects_negative_declared_size(tmp_path: Path) -> None:
    """Service should reject invalid negative declared sizes."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            with pytest.raises(
                TemporalArtifactValidationError,
                match="size_bytes must be non-negative",
            ):
                await service.create(
                    principal="user-1",
                    content_type="text/plain",
                    size_bytes=-1,
                )


async def test_create_switches_to_multipart_for_large_declared_size(
    tmp_path: Path,
) -> None:
    """Large declared sizes should return multipart upload instructions."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            store = _MultipartMemoryStore()
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=store,
                direct_upload_max_bytes=4,
            )
            artifact, upload = await service.create(
                principal="user-1",
                content_type="application/octet-stream",
                size_bytes=8,
            )

            assert artifact.upload_mode.value == "multipart"
            assert upload.mode == "multipart"
            assert upload.upload_id is not None


async def test_complete_rejects_undeclared_single_put_over_size_limit(
    tmp_path: Path,
) -> None:
    """Completion must reject oversized single-put uploads without declared size."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            store = _MultipartMemoryStore()
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=store,
                direct_upload_max_bytes=4,
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/octet-stream",
            )

            store.write_bytes(
                artifact.storage_key,
                b"oversized",
                content_type="application/octet-stream",
            )

            with pytest.raises(TemporalArtifactValidationError, match="max bytes"):
                await service.complete(
                    artifact_id=artifact.artifact_id,
                    principal="user-1",
                )


async def test_complete_multipart_upload_sets_integrity_metadata(
    tmp_path: Path,
) -> None:
    """Multipart completion should persist digest and size metadata deterministically."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            store = _MultipartMemoryStore()
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=store,
                direct_upload_max_bytes=4,
            )
            artifact, upload = await service.create(
                principal="user-1",
                content_type="application/octet-stream",
                size_bytes=8,
            )
            assert upload.upload_id is not None

            etag_1 = store.put_part(upload.upload_id, 1, b"abcd")
            etag_2 = store.put_part(upload.upload_id, 2, b"efgh")
            completed = await service.complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                parts=[
                    {"part_number": 1, "etag": etag_1},
                    {"part_number": 2, "etag": etag_2},
                ],
            )

            assert completed.size_bytes == 8
            assert completed.sha256 is not None


async def test_write_integration_event_artifact_creates_restricted_preview(
    tmp_path: Path,
) -> None:
    """Integration callback helper should persist restricted trace artifacts."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            artifact_ref = await service.write_integration_event_artifact(
                principal="service:integration-callback",
                execution=ExecutionRef(
                    namespace="moonmind",
                    workflow_id="wf-1",
                    run_id="run-1",
                    link_type="debug.trace",
                ),
                integration_name="jules",
                correlation_id="corr-1",
                payload=b'{"token":"secret"}',
                event_type="completed",
            )

            artifact, _links, _pinned, policy = await service.get_metadata(
                artifact_id=artifact_ref.artifact_id,
                principal="service:integration-callback",
            )
            assert artifact.metadata_json["artifact_kind"] == "integration_event"
            assert artifact.retention_class.value == "ephemeral"
            assert policy.preview_artifact_ref is not None


async def test_write_integration_result_and_failure_artifacts_assign_link_retention(
    tmp_path: Path,
) -> None:
    """Result/failure helpers should keep payloads compact and link-type scoped."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            execution = ExecutionRef(
                namespace="moonmind",
                workflow_id="wf-2",
                run_id="run-2",
                link_type="output.primary",
            )

            result_ref = await service.write_integration_result_artifact(
                principal="service:integration-result",
                execution=execution,
                integration_name="jules",
                correlation_id="corr-2",
                payload={"status": "completed", "url": "https://example.test/task/2"},
            )
            failure_ref = await service.write_integration_failure_artifact(
                principal="service:integration-result",
                execution=execution,
                integration_name="jules",
                correlation_id="corr-2",
                external_operation_id="task-2",
                normalized_status="failed",
                provider_status="errored",
                summary="Provider returned 500.",
                diagnostics={"httpStatus": 500},
            )

            result_artifact = await service._repository.get_artifact(
                result_ref.artifact_id
            )
            failure_artifact = await service._repository.get_artifact(
                failure_ref.artifact_id
            )

            assert result_artifact.retention_class.value == "standard"
            assert (
                result_artifact.metadata_json["artifact_kind"] == "integration_result"
            )
            assert (
                failure_artifact.metadata_json["artifact_kind"] == "integration_failure"
            )
            assert failure_artifact.retention_class.value == "standard"
