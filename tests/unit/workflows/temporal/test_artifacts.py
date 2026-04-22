"""Unit tests for local-dev Temporal artifact service behavior."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
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
    S3TemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
    TemporalArtifactStore,
    TemporalArtifactValidationError,
    build_artifact_ref,
    generate_artifact_id,
)
from moonmind.workflows.temporal import artifacts as artifact_module
from moonmind.workflows.temporal.report_artifacts import (
    REPORT_ARTIFACT_LINK_TYPES,
    build_report_bundle_result,
    validate_report_artifact_contract,
    validate_report_bundle_result,
)

pytestmark = [pytest.mark.asyncio]


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

    def presign_download(
        self,
        *,
        storage_key: str,
        expires_in_seconds: int,
        download_filename: str | None = None,
    ):
        _ = expires_in_seconds, download_filename
        return f"https://example.test/download/{storage_key}"

    def put_part(self, upload_id: str, part_number: int, payload: bytes) -> str:
        self._uploads[upload_id][part_number] = payload
        return str(part_number)


class _EventuallyVisibleMemoryStore(_MultipartMemoryStore):
    """Store that hides uploaded bytes for a bounded number of initial reads."""

    def __init__(self, missing_read_count: int) -> None:
        super().__init__()
        self._missing_read_count = missing_read_count
        self.read_attempts = 0

    def read_bytes(self, storage_key: str) -> bytes:
        self.read_attempts += 1
        if self.read_attempts <= self._missing_read_count:
            raise KeyError(storage_key)
        return super().read_bytes(storage_key)


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


async def test_s3_store_uses_thread_local_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3 store should allocate one boto3 client per thread."""

    created_clients: list[object] = []

    class _DummyS3Client:
        def __init__(self, index: int) -> None:
            self.index = index

        def head_bucket(self, *, Bucket: str) -> None:
            _ = Bucket

    def _fake_boto3_client(service_name: str, **kwargs: object) -> _DummyS3Client:
        assert service_name == "s3"
        assert kwargs["endpoint_url"] == "http://example.test:9000"
        client = _DummyS3Client(len(created_clients))
        created_clients.append(client)
        return client

    monkeypatch.setattr(
        "moonmind.workflows.temporal.artifacts.boto3.client",
        _fake_boto3_client,
    )

    store = S3TemporalArtifactStore(
        endpoint_url="http://example.test:9000",
        bucket="bucket-1",
        access_key_id="access",
        secret_access_key="secret",
        region_name="us-east-1",
        use_ssl=False,
    )

    main_thread_client = store._client
    assert store._client is main_thread_client

    other_thread_client = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: store._client,
    )

    assert other_thread_client is not main_thread_client
    assert len(created_clients) == 2


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


async def test_report_artifact_contract_accepts_supported_link_types() -> None:
    """MM-460: Report artifact link types should be explicit and stable."""

    assert REPORT_ARTIFACT_LINK_TYPES == frozenset(
        {
            "report.primary",
            "report.summary",
            "report.structured",
            "report.evidence",
            "report.appendix",
            "report.findings_index",
            "report.export",
        }
    )

    for link_type in REPORT_ARTIFACT_LINK_TYPES:
        validate_report_artifact_contract(
            link_type=link_type,
            metadata={
                "artifact_type": "unit_test_report",
                "report_type": "unit_test",
                "report_scope": "final",
                "title": "Unit test report",
                "producer": "pytest",
                "subject": "tests/unit",
                "render_hint": "json",
                "counts": {"total": 3, "failed": 0},
                "step_id": "test",
                "attempt": 1,
            },
        )


async def test_report_artifact_contract_rejects_unsupported_report_link_type() -> None:
    """MM-460: Unknown report link types must not create implicit semantics."""

    with pytest.raises(
        TemporalArtifactValidationError,
        match="unsupported report artifact link_type",
    ):
        validate_report_artifact_contract(
            link_type="report.raw_dump",
            metadata={"title": "Raw dump"},
        )


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"title": "Report", "unexpected": "value"}, "unsupported report metadata key"),
        ({"title": "x" * 2049}, "report metadata value is too large"),
        ({"token": "abc123"}, "unsafe report metadata key"),
        ({"title": "token=abc123"}, "unsafe report metadata value"),
        ({"counts": {"raw_payload": "x" * 2049}}, "report metadata value is too large"),
    ],
)
async def test_report_artifact_contract_rejects_unsafe_metadata(
    metadata: dict[str, object],
    message: str,
) -> None:
    """MM-460: Report metadata should stay bounded and safe for display."""

    with pytest.raises(TemporalArtifactValidationError, match=message):
        validate_report_artifact_contract(
            link_type="report.primary",
            metadata=metadata,
        )


async def test_create_accepts_report_primary_with_bounded_metadata(
    tmp_path: Path,
) -> None:
    """MM-460: Report artifacts should use the existing artifact store."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            artifact, _upload = await service.create(
                principal="workflow-producer",
                content_type="text/markdown",
                link={
                    "namespace": "moonmind",
                    "workflow_id": "wf-report",
                    "run_id": "run-report",
                    "link_type": "report.primary",
                    "label": "Final report",
                },
                metadata_json={
                    "artifact_type": "unit_test_report",
                    "report_type": "unit_test",
                    "report_scope": "final",
                    "title": "Final unit test report",
                    "producer": "pytest",
                    "subject": "tests/unit",
                    "render_hint": "text",
                    "is_final_report": True,
                },
            )

            links = await repo.list_links(artifact.artifact_id)
            assert artifact.metadata_json["report_type"] == "unit_test"
            assert links[0].link_type == "report.primary"


async def test_create_rejects_bad_report_link_and_metadata(tmp_path: Path) -> None:
    """MM-460: Report publication should fail before unsafe data is stored."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            with pytest.raises(
                TemporalArtifactValidationError,
                match="unsupported report artifact link_type",
            ):
                await service.create(
                    principal="workflow-producer",
                    content_type="application/json",
                    link={
                        "namespace": "moonmind",
                        "workflow_id": "wf-report",
                        "run_id": "run-report",
                        "link_type": "report.raw_dump",
                    },
                    metadata_json={"title": "Raw dump"},
                )

            with pytest.raises(
                TemporalArtifactValidationError,
                match="unsafe report metadata",
            ):
                await service.create(
                    principal="workflow-producer",
                    content_type="application/json",
                    link={
                        "namespace": "moonmind",
                        "workflow_id": "wf-report",
                        "run_id": "run-report",
                        "link_type": "report.primary",
                    },
                    metadata_json={"title": "token=abc123"},
                )


async def test_link_artifact_rejects_unsafe_report_metadata(tmp_path: Path) -> None:
    """MM-460: Existing artifacts must satisfy report metadata before report linking."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="workflow-producer",
                content_type="application/json",
                metadata_json={"title": "token=abc123"},
            )

            with pytest.raises(
                TemporalArtifactValidationError,
                match="unsafe report metadata",
            ):
                await service.link_artifact(
                    artifact_id=artifact.artifact_id,
                    principal="workflow-producer",
                    execution_ref={
                        "namespace": "moonmind",
                        "workflow_id": "wf-report",
                        "run_id": "run-report",
                        "link_type": "report.primary",
                    },
                )


async def test_link_artifact_allows_internal_preview_metadata_for_reports(
    tmp_path: Path,
) -> None:
    """MM-460: System-added preview metadata must not block report linking."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="workflow-producer",
                content_type="text/markdown",
                metadata_json={"title": "Restricted report"},
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            completed = await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="workflow-producer",
                payload=b"# Report\nsafe display content",
                content_type="text/markdown",
            )

            assert completed.metadata_json["preview_artifact_id"].startswith("art_")

            link = await service.link_artifact(
                artifact_id=artifact.artifact_id,
                principal="workflow-producer",
                execution_ref={
                    "namespace": "moonmind",
                    "workflow_id": "wf-report",
                    "run_id": "run-report",
                    "link_type": "report.primary",
                },
            )

            assert link.link_type == "report.primary"


async def test_generic_output_links_remain_accepted_with_generic_metadata(
    tmp_path: Path,
) -> None:
    """MM-460: Generic output flows must not require report metadata."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            for link_type in ("output.primary", "output.summary", "output.agent_result"):
                artifact, _upload = await service.create(
                    principal="workflow-producer",
                    content_type="application/json",
                    link={
                        "namespace": "moonmind",
                        "workflow_id": "wf-output",
                        "run_id": "run-output",
                        "link_type": link_type,
                    },
                    metadata_json={
                        "integration_name": "jules",
                        "raw_result_shape": {"status": "completed"},
                    },
                )
                links = await repo.list_links(artifact.artifact_id)
                assert links[0].link_type == link_type


async def test_latest_report_primary_uses_existing_execution_linkage(
    tmp_path: Path,
) -> None:
    """MM-460: Latest report lookup should use existing execution link filters."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            first, _upload = await service.create(
                principal="workflow-producer",
                content_type="text/markdown",
                link={
                    "namespace": "moonmind",
                    "workflow_id": "wf-report",
                    "run_id": "run-report",
                    "link_type": "report.primary",
                },
                metadata_json={"title": "First report"},
            )
            second, _upload = await service.create(
                principal="workflow-producer",
                content_type="text/markdown",
                link={
                    "namespace": "moonmind",
                    "workflow_id": "wf-report",
                    "run_id": "run-report",
                    "link_type": "report.primary",
                },
                metadata_json={"title": "Second report"},
            )

            latest = await service.list_for_execution(
                namespace="moonmind",
                workflow_id="wf-report",
                run_id="run-report",
                principal="workflow-producer",
                link_type="report.primary",
                latest_only=True,
            )

            assert [artifact.artifact_id for artifact in latest] == [
                second.artifact_id
            ]
            assert first.artifact_id != second.artifact_id


async def test_report_bundle_result_is_compact_and_rejects_inline_payloads() -> None:
    """MM-461: Report bundle results must be refs and bounded metadata only."""

    bundle = build_report_bundle_result(
        primary_report_ref={"artifact_ref_v": 1, "artifact_id": "art_primary"},
        summary_ref={"artifact_ref_v": 1, "artifact_id": "art_summary"},
        structured_ref={"artifact_ref_v": 1, "artifact_id": "art_structured"},
        evidence_refs=({"artifact_ref_v": 1, "artifact_id": "art_evidence"},),
        report_type="unit_test_report",
        report_scope="final",
        sensitivity="restricted",
        counts={"total": 3},
    )

    assert bundle == {
        "report_bundle_v": 1,
        "primary_report_ref": {"artifact_ref_v": 1, "artifact_id": "art_primary"},
        "summary_ref": {"artifact_ref_v": 1, "artifact_id": "art_summary"},
        "structured_ref": {"artifact_ref_v": 1, "artifact_id": "art_structured"},
        "evidence_refs": [{"artifact_ref_v": 1, "artifact_id": "art_evidence"}],
        "report_type": "unit_test_report",
        "report_scope": "final",
        "sensitivity": "restricted",
        "counts": {"total": 3},
    }

    with pytest.raises(TemporalArtifactValidationError, match="unsafe report bundle"):
        validate_report_bundle_result(
            {
                "report_bundle_v": 1,
                "primary_report_ref": {
                    "artifact_ref_v": 1,
                    "artifact_id": "art_primary",
                },
                "report_body": "# inline body",
            }
        )

    with pytest.raises(TemporalArtifactValidationError, match="unsafe report bundle"):
        validate_report_bundle_result(
            {
                "report_bundle_v": 1,
                "primary_report_ref": {
                    "artifact_ref_v": 1,
                    "artifact_id": "art_primary",
                },
                "raw_download_url": "https://example.invalid/report",
            }
        )


async def test_publish_report_bundle_writes_links_final_marker_and_step_metadata(
    tmp_path: Path,
) -> None:
    """MM-461: Activities should publish artifact-backed report bundles."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            bundle = await service.publish_report_bundle(
                principal="workflow-producer",
                namespace="moonmind",
                workflow_id="wf-report",
                run_id="run-report",
                report_type="unit_test_report",
                report_scope="final",
                sensitivity="restricted",
                counts={"total": 3},
                step_id="step-1",
                attempt=2,
                scope="step",
                primary={
                    "payload": "# Final report",
                    "content_type": "text/markdown",
                    "label": "Final report",
                    "metadata": {"title": "Final report"},
                },
                summary={
                    "payload": "Summary",
                    "content_type": "text/plain",
                    "label": "Summary",
                },
                structured={
                    "payload": {"findings": [], "status": "complete"},
                    "content_type": "application/json",
                    "label": "Findings JSON",
                },
                evidence=[
                    {
                        "payload": "command output",
                        "content_type": "text/plain",
                        "label": "Command output",
                    }
                ],
            )

            assert bundle["report_bundle_v"] == 1
            assert bundle["report_scope"] == "final"
            assert bundle["primary_report_ref"]["artifact_ref_v"] == 1
            assert bundle["structured_ref"]["artifact_ref_v"] == 1
            assert len(bundle["evidence_refs"]) == 1

            primary = await repo.get_artifact(bundle["primary_report_ref"]["artifact_id"])
            primary_links = await repo.list_links(primary.artifact_id)
            assert primary_links[0].namespace == "moonmind"
            assert primary_links[0].workflow_id == "wf-report"
            assert primary_links[0].run_id == "run-report"
            assert primary_links[0].link_type == "report.primary"
            assert primary_links[0].label == "Final report"
            assert primary.metadata_json["is_final_report"] is True
            assert primary.metadata_json["report_scope"] == "final"
            assert primary.metadata_json["step_id"] == "step-1"
            assert primary.metadata_json["attempt"] == 2
            assert primary.metadata_json["scope"] == "step"

            evidence = await repo.get_artifact(bundle["evidence_refs"][0]["artifact_id"])
            evidence_links = await repo.list_links(evidence.artifact_id)
            assert evidence_links[0].link_type == "report.evidence"

            _structured, structured_payload = await service.read(
                artifact_id=bundle["structured_ref"]["artifact_id"],
                principal="workflow-producer",
                allow_restricted_raw=True,
            )
            assert structured_payload == b'{"findings": [], "status": "complete"}'


async def test_publish_report_bundle_rejects_missing_and_duplicate_final_marker(
    tmp_path: Path,
) -> None:
    """MM-461: Final bundles must have exactly one canonical final report."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            with pytest.raises(TemporalArtifactValidationError, match="primary report"):
                await service.publish_report_bundle(
                    principal="workflow-producer",
                    namespace="moonmind",
                    workflow_id="wf-report",
                    run_id="run-report",
                    report_type="unit_test_report",
                    report_scope="Final",
                    primary=None,
                    evidence=[
                        {
                            "payload": "evidence",
                            "metadata": {"is_final_report": True},
                        }
                    ],
                )

            with pytest.raises(TemporalArtifactValidationError, match="exactly one"):
                await service.publish_report_bundle(
                    principal="workflow-producer",
                    namespace="moonmind",
                    workflow_id="wf-report",
                    run_id="run-report",
                    report_type="unit_test_report",
                    report_scope="final",
                    primary={
                        "payload": "# Final report",
                        "metadata": {"is_final_report": True},
                    },
                    evidence=[
                        {
                            "payload": "evidence",
                            "metadata": {"is_final_report": True},
                        }
                    ],
                )


async def test_write_complete_rejects_invalid_task_image_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task image attachments should be sniffed server-side at completion."""

    monkeypatch.setattr(
        artifact_module.settings.workflow,
        "agent_job_attachment_allowed_content_types",
        ("image/png", "image/jpeg", "image/webp"),
    )

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="image/png",
                metadata_json={"source": "task-dashboard-step-attachment"},
            )

            with pytest.raises(
                TemporalArtifactValidationError,
                match="image/png signature",
            ):
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="user-1",
                    payload=b"not-a-png",
                    content_type="image/png",
                )


async def test_write_complete_rejects_invalid_image_signature_without_task_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All image uploads should be sniffed, even when metadata is only diagnostic."""

    monkeypatch.setattr(
        artifact_module.settings.workflow,
        "agent_job_attachment_allowed_content_types",
        ("image/png", "image/jpeg", "image/webp"),
    )

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="image/png",
            )

            with pytest.raises(
                TemporalArtifactValidationError,
                match="image/png signature",
            ):
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="user-1",
                    payload=b"not-a-png",
                    content_type="image/png",
                )


async def test_create_rejects_reserved_input_attachment_storage_key(
    tmp_path: Path,
) -> None:
    """Worker artifact uploads must not impersonate input attachment namespaces."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            with pytest.raises(
                TemporalArtifactValidationError,
                match="reserved input attachment namespace",
            ):
                await service.create(
                    principal="worker-1",
                    content_type="text/plain",
                    metadata_json={
                        "source": "agent-runtime",
                        "artifact_path": "inputs/objective/screenshot.png",
                    },
                )


@pytest.mark.parametrize(
    "reserved_path",
    [
        "./inputs/objective/screenshot.png",
        "/inputs/objective/screenshot.png",
        ".moonmind/inputs/objective/screenshot.png",
        "/.moonmind/inputs/objective/screenshot.png",
    ],
)
async def test_create_rejects_normalized_reserved_input_attachment_storage_keys(
    tmp_path: Path,
    reserved_path: str,
) -> None:
    """Reserved path checks should normalize candidates and prefixes consistently."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            with pytest.raises(
                TemporalArtifactValidationError,
                match="reserved input attachment namespace",
            ):
                await service.create(
                    principal="worker-1",
                    content_type="text/plain",
                    metadata_json={
                        "source": "agent-runtime",
                        "artifact_path": reserved_path,
                    },
                )


async def test_create_uses_same_origin_content_endpoint_for_small_s3_uploads(
    tmp_path: Path,
) -> None:
    """Small S3-backed uploads should stay on the API content route."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=_MultipartMemoryStore(),
                direct_upload_max_bytes=1024,
            )

            artifact, upload = await service.create(
                principal="user-1",
                content_type="text/plain",
                size_bytes=11,
            )

            assert upload.mode == "single_put"
            assert upload.upload_url == f"/api/artifacts/{artifact.artifact_id}/content"


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


async def test_complete_retries_single_put_reads_until_uploaded_bytes_are_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-put completion should tolerate short storage visibility delays."""

    monkeypatch.setattr(
        artifact_module,
        "_SINGLE_PUT_READ_RETRY_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            store = _EventuallyVisibleMemoryStore(missing_read_count=3)
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=store,
                direct_upload_max_bytes=1024,
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
            )

            store.write_bytes(
                artifact.storage_key,
                b"ready after retries",
                content_type="text/plain",
            )

            completed = await service.complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
            )

            assert completed.status is TemporalArtifactStatus.COMPLETE
            assert store.read_attempts == 4


async def test_complete_still_fails_when_single_put_bytes_never_appear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-put completion should still return state error after retry budget."""

    monkeypatch.setattr(
        artifact_module,
        "_SINGLE_PUT_READ_RETRY_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            store = _EventuallyVisibleMemoryStore(missing_read_count=99)
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=store,
                direct_upload_max_bytes=1024,
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
            )

            with pytest.raises(TemporalArtifactStateError, match="not complete"):
                await service.complete(
                    artifact_id=artifact.artifact_id,
                    principal="user-1",
                )


async def test_complete_single_put_raises_non_visibility_read_error_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-put completion should not mask non-visibility storage failures."""

    class _AccessDeniedReadStore(_MultipartMemoryStore):
        def read_bytes(self, storage_key: str) -> bytes:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                "GetObject",
            )

    monkeypatch.setattr(
        artifact_module,
        "_SINGLE_PUT_READ_RETRY_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            store = _AccessDeniedReadStore()
            repo = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repo,
                store=store,
                direct_upload_max_bytes=1024,
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
            )

            store.write_bytes(
                artifact.storage_key,
                b"present but unreadable",
                content_type="text/plain",
            )

            with pytest.raises(ClientError, match="AccessDenied"):
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
