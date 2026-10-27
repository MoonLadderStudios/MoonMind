"""Bounded reclamation of tombstoned artifact metadata after content deletion."""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentBridgeSession,
    TemporalArtifactLink,
    TemporalExecutionRemediationLink,
)
from moonmind.config.settings import settings
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    S3TemporalArtifactStore,
    TemporalArtifactExpiredError,
    TemporalArtifactNotFoundError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
    generate_artifact_id,
)
from tests.artifact_access import access_for_test

pytestmark = [pytest.mark.asyncio]

HORIZON = timedelta(days=7)
PRINCIPAL = "service:lifecycle"


@asynccontextmanager
async def temporal_db(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/artifact_metadata_purge.db", future=True
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_maker
    finally:
        await engine.dispose()


def _service(session: AsyncSession, store: LocalTemporalArtifactStore):
    return TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=store,
        lifecycle_hard_delete_after_seconds=0,
        lifecycle_purge_after_seconds=int(HORIZON.total_seconds()),
    )


async def _tombstones(
    service: TemporalArtifactService, count: int, *, payload_prefix: str = "payload"
) -> list[str]:
    """Linked artifacts whose content was deleted through the ordinary path."""

    artifact_ids: list[str] = []
    for index in range(count):
        artifact, _upload = await service.create(
            principal="user-1",
            content_type="text/plain",
            link=ExecutionRef(
                namespace="default",
                workflow_id="wf-journal",
                run_id="run-1",
                link_type="runtime.omnigent.sse.raw",
            ),
        )
        await service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="user-1",
            payload=f"{payload_prefix}-{index}".encode(),
            content_type="text/plain",
        )
        await service.soft_delete(artifact_id=artifact.artifact_id, principal="user-1")
        await service.hard_delete(artifact_id=artifact.artifact_id, principal="user-1")
        artifact_ids.append(artifact.artifact_id)
    return artifact_ids


async def _exists(service: TemporalArtifactService, artifact_id: str) -> bool:
    try:
        await service._repository.get_artifact(artifact_id)
    except TemporalArtifactNotFoundError:
        return False
    return True


async def _link_count(session: AsyncSession) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(TemporalArtifactLink))
    )


async def test_purge_reclaims_tombstones_and_links_in_bounded_resumable_pages(
    tmp_path: Path,
) -> None:
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            artifact_ids = await _tombstones(service, 5)
            assert await _link_count(session) == 5

            inside_horizon = await service.sweep_lifecycle(
                principal=PRINCIPAL,
                now=datetime.now(UTC) + HORIZON - timedelta(minutes=5),
            )
            assert inside_horizon.purged_count == 0
            assert all([await _exists(service, item) for item in artifact_ids])

            past_horizon = datetime.now(UTC) + HORIZON + timedelta(minutes=5)
            first = await service.sweep_lifecycle(
                principal=PRINCIPAL, now=past_horizon, limit=2
            )
            assert (first.purge_candidate_count, first.purged_count) == (2, 2)
            assert first.purged_link_count == 2
            assert first.drained is False

        # A restart resumes from durable state instead of a remembered cursor.
        async with session_maker() as session:
            service = _service(session, store)
            pages = []
            summary = await service.drain_lifecycle(
                principal=PRINCIPAL,
                limit=2,
                time_budget=timedelta(minutes=5),
                now=past_horizon,
                on_page=pages.append,
            )
            assert summary.purged_count == 3
            assert summary.purged_link_count == 3
            assert summary.drained is True
            assert len(pages) == 2
            assert not any([await _exists(service, item) for item in artifact_ids])
            assert await _link_count(session) == 0

            again = await service.sweep_lifecycle(principal=PRINCIPAL, now=past_horizon)
            assert again.purge_candidate_count == again.purged_count == 0


async def test_purge_waits_for_a_pending_deletion_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tombstone commits before object deletion; it alone never authorizes purge."""

    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    real_delete = store.delete

    def unavailable(_storage_key: str) -> None:
        raise OSError("object store unavailable")

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            artifact, _upload = await service.create(
                principal="user-1", content_type="text/plain"
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"bytes",
                content_type="text/plain",
            )
            await service.soft_delete(
                artifact_id=artifact.artifact_id, principal="user-1"
            )
            monkeypatch.setattr(store, "delete", unavailable)
            with pytest.raises(TemporalArtifactStateError, match="DELETE_RETRY"):
                await service.hard_delete(
                    artifact_id=artifact.artifact_id, principal="user-1"
                )
            row = await service._repository.get_artifact(artifact.artifact_id)
            assert row.hard_deleted_at is not None

            past_horizon = datetime.now(UTC) + HORIZON + timedelta(minutes=5)
            blocked = await service.sweep_lifecycle(
                principal=PRINCIPAL, now=past_horizon
            )
            assert blocked.purged_count == 0
            assert blocked.drained is False
            assert await _exists(service, artifact.artifact_id)
            assert store.resolve_storage_key(row.storage_key).exists()

            monkeypatch.setattr(store, "delete", real_delete)
            converged = await service.drain_lifecycle(
                principal=PRINCIPAL, time_budget=timedelta(minutes=5), now=past_horizon
            )
            assert converged.reconciled_deletion_count == 1
            assert converged.purged_count == 1
            assert not await _exists(service, artifact.artifact_id)


@pytest.mark.parametrize("store_state", ["present", "unavailable"])
async def test_purge_returns_unconfirmed_bytes_to_deletion_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store_state: str
) -> None:
    """Tombstones without intents (older code paths) still prove deletion first."""

    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            blobs = {}
            for index in range(3):
                artifact, _upload = await service.create(
                    principal="user-1", content_type="text/plain"
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="user-1",
                    payload=f"orphaned bytes {index}".encode(),
                    content_type="text/plain",
                )
                row = await service._repository.get_artifact(artifact.artifact_id)
                # A legacy tombstone whose object delete never happened.
                row.hard_deleted_at = row.tombstoned_at = datetime.now(UTC)
                blobs[artifact.artifact_id] = store.resolve_storage_key(row.storage_key)
            await service._repository.commit()
            past_horizon = datetime.now(UTC) + HORIZON + timedelta(minutes=5)

            if store_state == "unavailable":

                def unavailable(_storage_key: str) -> bool:
                    raise OSError("object store timed out")

                monkeypatch.setattr(store, "object_exists", unavailable)
                outage = await service.drain_lifecycle(
                    principal=PRINCIPAL,
                    limit=1,
                    time_budget=timedelta(minutes=5),
                    now=past_horizon,
                )
                # An outage stops the pass instead of walking the backlog.
                assert (outage.pages, outage.purge_error_count) == (1, 1)
                assert outage.purged_count == 0
                assert outage.drained is False
                intents = await service._repository.list_pending_deletion_intents()
                assert len(intents) == 1
                assert "object store timed out" in (intents[0].last_error or "")
                assert all(blob.exists() for blob in blobs.values())
                monkeypatch.undo()

            summary = await service.drain_lifecycle(
                principal=PRINCIPAL,
                limit=1,
                time_budget=timedelta(minutes=5),
                now=past_horizon,
            )
            assert summary.drained is True
            assert summary.purged_count == 3
            if store_state == "present":
                assert summary.purge_unconfirmed_count == 3
            assert summary.reconciled_deletion_count == 3
            assert not any(blob.exists() for blob in blobs.values())
            assert not any([await _exists(service, item) for item in blobs])


class _MemoryS3Client:
    """In-memory boto client; ``head_error`` simulates an unknown object state."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.head_error: Exception | None = None

    def head_bucket(self, *, Bucket: str) -> None:
        _ = Bucket

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_kwargs) -> None:
        self.objects[Key] = bytes(Body)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, int]:
        if self.head_error is not None:
            raise self.head_error
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop(Key, None)


async def test_purge_through_s3_store_waits_for_a_definite_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default backend's head_object errors keep the tombstone, not purge it."""

    client = _MemoryS3Client()
    monkeypatch.setattr(
        "moonmind.workflows.temporal.artifacts.boto3.client",
        lambda service_name, **kwargs: client,
    )
    store = S3TemporalArtifactStore(
        endpoint_url="http://minio.test:9000",
        bucket="artifacts",
        access_key_id="access",
        secret_access_key="secret",
        region_name="us-east-1",
        use_ssl=False,
    )
    past_horizon = datetime.now(UTC) + HORIZON + timedelta(minutes=5)
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            (artifact_id,) = await _tombstones(service, 1)
            assert client.objects == {}

            client.head_error = ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "HeadObject"
            )
            denied = await service.sweep_lifecycle(
                principal=PRINCIPAL, now=past_horizon
            )

            assert (denied.purge_error_count, denied.purged_count) == (1, 0)
            assert await _exists(service, artifact_id)
            (intent,) = await service._repository.list_pending_deletion_intents()
            assert intent.artifact_id == artifact_id
            assert "AccessDenied" in (intent.last_error or "")

            client.head_error = None
            converged = await service.drain_lifecycle(
                principal=PRINCIPAL, time_budget=timedelta(minutes=5), now=past_horizon
            )

            assert converged.drained is True
            assert converged.purged_count == 1
            assert not await _exists(service, artifact_id)
            assert await service._repository.list_pending_deletion_intents() == []


async def test_purge_releases_a_shared_blob_reference_without_touching_bytes(
    tmp_path: Path,
) -> None:
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            payload = b"shared checkpoint"
            first, _ = await service.put_content_addressed_payload_complete(
                principal="system",
                payload=payload,
                content_type="application/octet-stream",
                scope="checkpoint_archive",
            )
            second, reused = await service.put_content_addressed_payload_complete(
                principal="system",
                payload=payload,
                content_type="application/octet-stream",
                scope="checkpoint_archive",
            )
            assert reused and first.storage_key == second.storage_key
            await service.soft_delete(artifact_id=first.artifact_id, principal="system")
            await service.hard_delete(artifact_id=first.artifact_id, principal="system")

            summary = await service.sweep_lifecycle(
                principal=PRINCIPAL,
                now=datetime.now(UTC) + HORIZON + timedelta(minutes=5),
            )

            assert summary.purged_count == 1
            assert not await _exists(service, first.artifact_id)
            assert store.read_bytes(second.storage_key) == payload
            assert await _exists(service, second.artifact_id)


@pytest.mark.parametrize(
    "protection", ["pin", "use_claim", "active_journal", "remediation_context"]
)
async def test_purge_preserves_protected_tombstones(
    tmp_path: Path, protection: str
) -> None:
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    past_horizon = datetime.now(UTC) + HORIZON + timedelta(minutes=5)
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            protected, unprotected = await _tombstones(service, 2)
            repository = service._repository
            if protection == "pin":
                await repository.pin_artifact(
                    artifact_id=protected, principal="operator", reason="audit"
                )
            elif protection == "use_claim":
                await repository.acquire_use_claim(
                    artifact_id=protected,
                    owner_principal="user-1",
                    request_id="restore-1",
                    operation_kind="restore",
                    expires_at=past_horizon + timedelta(hours=1),
                )
            elif protection == "active_journal":
                session.add(
                    OmnigentBridgeSession(
                        bridge_session_id="bridge-active",
                        provider="codex",
                        compatibility_profile="default",
                        moonmind_workflow_id="wf-journal",
                        moonmind_agent_run_id="agent-run-1",
                        idempotency_key="idem-active",
                        omnigent_endpoint_ref="endpoint",
                        host_type="proxy",
                        status="active",
                        raw_events_ref=f"artifact:{protected}",
                    )
                )
            else:
                session.add(
                    TemporalExecutionRemediationLink(
                        remediation_workflow_id="wf-remediation",
                        remediation_run_id="run-remediation",
                        target_workflow_id="wf-journal",
                        target_run_id="run-1",
                        mode="snapshot_then_follow",
                        authority_mode="observe_only",
                        context_artifact_ref=protected,
                    )
                )
            await repository.commit()

            summary = await service.drain_lifecycle(
                principal=PRINCIPAL,
                limit=1,
                time_budget=timedelta(minutes=5),
                now=past_horizon,
            )

            assert summary.purged_count == 1
            # Withheld tombstones are visible, and a multi-page pass reports
            # the current number rather than one count per page.
            assert summary.pages == 2
            assert summary.purge_protected_count == 1
            assert await _exists(service, protected)
            assert not await _exists(service, unprotected)


async def test_purge_reclaims_a_tombstone_whose_pin_ended_with_deletion(
    tmp_path: Path,
) -> None:
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            artifact, _upload = await service.create(
                principal="user-1", content_type="text/plain"
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"pinned",
                content_type="text/plain",
            )
            await service.pin(
                artifact_id=artifact.artifact_id, principal="user-1", reason="keep"
            )
            # Deletion removes the pin row; the retention class it set must
            # not withhold the tombstone forever.
            await service.soft_delete(
                artifact_id=artifact.artifact_id, principal="user-1"
            )
            await service.hard_delete(
                artifact_id=artifact.artifact_id, principal="user-1"
            )

            summary = await service.sweep_lifecycle(
                principal=PRINCIPAL,
                now=datetime.now(UTC) + HORIZON + timedelta(minutes=5),
            )

            assert summary.purged_count == 1
            assert summary.purge_protected_count == 0
            assert not await _exists(service, artifact.artifact_id)


async def test_purge_rechecks_protection_acquired_after_candidate_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            (artifact_id,) = await _tombstones(service, 1)
            repository = service._repository
            select_candidates = repository.list_purge_candidates

            async def pin_after_selection(**kwargs):
                candidates = await select_candidates(**kwargs)
                await repository.pin_artifact(
                    artifact_id=artifact_id, principal="operator", reason="race"
                )
                return candidates

            monkeypatch.setattr(
                repository, "list_purge_candidates", pin_after_selection
            )
            summary = await service.sweep_lifecycle(
                principal=PRINCIPAL,
                now=datetime.now(UTC) + HORIZON + timedelta(minutes=5),
            )

            assert summary.purge_candidate_count == 1
            assert summary.purge_protected_count == 1
            assert summary.purged_count == 0
            assert await repository.get_pin(artifact_id) is not None


async def test_purge_converges_after_a_failed_database_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    past_horizon = datetime.now(UTC) + HORIZON + timedelta(minutes=5)
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            artifact_ids = await _tombstones(service, 2)

        async with session_maker() as session:
            service = _service(session, store)
            repository = service._repository
            purge = repository.purge_artifacts
            real_commit = repository.commit
            state = {"purged": False}

            async def purge_then_fail(artifact_ids):
                state["purged"] = True
                return await purge(artifact_ids)

            async def commit() -> None:
                if state["purged"]:
                    raise ConnectionError("database connection lost during commit")
                await real_commit()

            monkeypatch.setattr(repository, "purge_artifacts", purge_then_fail)
            monkeypatch.setattr(repository, "commit", commit)
            with pytest.raises(ConnectionError):
                await service.sweep_lifecycle(principal=PRINCIPAL, now=past_horizon)
            await session.rollback()

        async with session_maker() as session:
            service = _service(session, store)
            assert all([await _exists(service, item) for item in artifact_ids])
            summary = await service.sweep_lifecycle(
                principal=PRINCIPAL, now=past_horizon
            )
            assert summary.purged_count == 2
            assert not any([await _exists(service, item) for item in artifact_ids])


async def test_missing_artifact_older_than_horizon_reports_expiration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_lifecycle_purge_after_seconds",
        int(HORIZON.total_seconds()),
    )
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, store)
            old_id = generate_artifact_id(
                datetime.now(UTC) - HORIZON - timedelta(days=1)
            )
            with pytest.raises(TemporalArtifactExpiredError) as expired:
                await service.get_metadata(
                    artifact_id=old_id, access=access_for_test("user-1")
                )
            message = str(expired.value)
            assert message.startswith(f"Artifact {old_id} was not found")
            assert "expired" in message
            assert expired.value.created_at is not None

            recent_id = generate_artifact_id()
            with pytest.raises(TemporalArtifactNotFoundError) as missing:
                await service.get_metadata(
                    artifact_id=recent_id, access=access_for_test("user-1")
                )
            assert not isinstance(missing.value, TemporalArtifactExpiredError)

            with pytest.raises(TemporalArtifactNotFoundError) as opaque:
                await service.get_metadata(
                    artifact_id="art_unknown", access=access_for_test("user-1")
                )
            assert not isinstance(opaque.value, TemporalArtifactExpiredError)
