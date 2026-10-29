"""Rolled-back artifact uploads remain recoverable by the lifecycle owner."""

from datetime import UTC, datetime, timedelta

import pytest

from api_service.db.models import TemporalArtifact, TemporalArtifactRetentionClass
from moonmind.workflows.temporal.artifacts import (
    ArtifactTransactionOwnership,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = pytest.mark.asyncio


def _service(session, store, *, caller=False):
    return TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=store,
        transaction_ownership=(
            ArtifactTransactionOwnership.CALLER
            if caller
            else ArtifactTransactionOwnership.STANDALONE
        ),
    )


async def _write(service, *, shared=False):
    if shared:
        artifact, _ = await service.put_content_addressed_payload_complete(
            principal="service:test",
            payload=b"synthetic admission bytes",
            content_type="text/plain",
            scope="caller-upload-recovery",
            retention_class=TemporalArtifactRetentionClass.PINNED,
        )
    else:
        artifact, _ = await service.create(
            principal="service:test",
            content_type="text/plain",
            retention_class=TemporalArtifactRetentionClass.PINNED,
        )
        await service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="service:test",
            payload=b"synthetic admission bytes",
        )
    return artifact.artifact_id, artifact.storage_key


async def _sweep(maker, store):
    async with maker() as session:
        return await _service(session, store).sweep_lifecycle(
            principal="service:lifecycle",
            now=datetime.now(UTC) + timedelta(days=2),
        )


@pytest.mark.parametrize("shared", [False, True])
async def test_rolled_back_upload_is_reclaimed_by_existing_lifecycle(tmp_path, shared):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            artifact_id, key = await _write(
                _service(session, store, caller=True), shared=shared
            )
            assert store.object_exists(key)
            await session.rollback()
        async with maker() as observer:
            assert await observer.get(TemporalArtifact, artifact_id) is None
        await _sweep(maker, store)
        assert not store.object_exists(key)
        await _sweep(maker, store)
        assert not store.object_exists(key)


async def test_committed_caller_upload_survives_pending_write_reconciliation(tmp_path):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            artifact_id, key = await _write(_service(session, store, caller=True))
            await session.commit()
        await _sweep(maker, store)
        assert store.read_bytes(key) == b"synthetic admission bytes"
        async with maker() as observer:
            assert await observer.get(TemporalArtifact, artifact_id) is not None


async def test_failed_shared_upload_never_deletes_another_logical_owner(tmp_path):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            retained_id, retained_key = await _write(
                _service(session, store), shared=True
            )
        async with maker() as session:
            rejected_id, key = await _write(
                _service(session, store, caller=True), shared=True
            )
            assert rejected_id != retained_id and key == retained_key
            await session.rollback()
        await _sweep(maker, store)
        assert store.read_bytes(key) == b"synthetic admission bytes"
        async with maker() as observer:
            assert await observer.get(TemporalArtifact, retained_id) is not None
            assert await observer.get(TemporalArtifact, rejected_id) is None


async def test_failed_orphan_delete_remains_discoverable_for_later_sweep(
    tmp_path, monkeypatch
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            _, key = await _write(_service(session, store, caller=True))
            await session.rollback()
        actual_delete = store.delete
        attempted = []

        def unavailable(storage_key):
            if storage_key == key:
                attempted.append(storage_key)
                raise OSError("synthetic storage outage")
            actual_delete(storage_key)

        monkeypatch.setattr(store, "delete", unavailable)
        await _sweep(maker, store)
        assert attempted == [key]
        assert store.object_exists(key)
        monkeypatch.setattr(store, "delete", actual_delete)
        await _sweep(maker, store)
        assert not store.object_exists(key)


async def test_cancelled_upload_drains_writer_before_releasing_caller(
    tmp_path, monkeypatch
):
    import asyncio
    import threading

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            service = _service(session, store, caller=True)
            artifact, _ = await service.create(
                principal="service:test", content_type="text/plain"
            )
            key = artifact.storage_key
            entered, release, drained = (
                threading.Event(),
                threading.Event(),
                threading.Event(),
            )
            actual_write = store.write_bytes

            def blocked_write(storage_key, payload, **kwargs):
                if storage_key == key:
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("test did not release storage writer")
                try:
                    actual_write(storage_key, payload, **kwargs)
                finally:
                    if storage_key == key:
                        drained.set()

            monkeypatch.setattr(store, "write_bytes", blocked_write)
            writing = asyncio.create_task(
                service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="service:test",
                    payload=b"synthetic admission bytes",
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                writing.cancel()
                finished, _ = await asyncio.wait({writing}, timeout=0.05)
                released_while_writer_active = bool(finished)
            finally:
                release.set()
                result = await asyncio.gather(writing, return_exceptions=True)
                assert await asyncio.to_thread(drained.wait, 5)
            assert isinstance(result[0], asyncio.CancelledError)
            assert not released_while_writer_active
            await session.rollback()
        await _sweep(maker, store)
        assert not store.object_exists(key)


@pytest.mark.parametrize("unsupported", [False, True])
async def test_untracked_caller_write_is_refused_before_payload_io(
    tmp_path, monkeypatch, unsupported
):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactValidationError

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker, maker() as session:
        service = _service(session, store, caller=True)
        artifact, _ = await service.create(
            principal="service:test", content_type="text/plain"
        )
        key = artifact.storage_key
        if unsupported:
            monkeypatch.setattr(store, "supports_pending_writes", False)
            error_type = TemporalArtifactValidationError
        else:

            def unavailable(**kwargs):
                raise OSError("synthetic marker storage failure")

            monkeypatch.setattr(store, "record_pending_write", unavailable)
            error_type = OSError
        with pytest.raises(error_type):
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="service:test",
                payload=b"never uploaded",
            )
        assert not store.object_exists(key)
        await session.rollback()


async def test_marker_removal_retry_preserves_committed_bytes_without_rewriting(
    tmp_path, monkeypatch
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            _, key = await _write(_service(session, store, caller=True))
            await session.commit()
        markers = list(store.resolve_storage_key("_moonmind/pending-writes").iterdir())
        assert len(markers) == 1
        marker = "_moonmind/pending-writes/" + markers[0].name
        actual_delete = store.delete
        failed_deletes = []

        def unavailable(storage_key):
            if storage_key == marker:
                failed_deletes.append(storage_key)
                raise OSError("synthetic marker delete outage")
            actual_delete(storage_key)

        def no_rewrite(*args, **kwargs):
            raise AssertionError("reconciliation must not repeat payload writes")

        monkeypatch.setattr(store, "delete", unavailable)
        monkeypatch.setattr(store, "write_bytes", no_rewrite)
        first = await _sweep(maker, store)
        assert not first.drained
        assert failed_deletes == [marker]
        assert store.object_exists(marker)
        assert store.read_bytes(key) == b"synthetic admission bytes"
        monkeypatch.setattr(store, "delete", actual_delete)
        await _sweep(maker, store)
        assert not store.object_exists(marker)
        assert store.read_bytes(key) == b"synthetic admission bytes"


async def test_malformed_marker_is_observable_without_starving_later_page(
    tmp_path, caplog, monkeypatch
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    malformed = "_moonmind/pending-writes/000-malformed.json"
    store.write_bytes(malformed, b"{}", content_type="application/json")
    examined = []
    actual_read = store.read_pending_write

    def read_marker(key):
        examined.append(key)
        return actual_read(key)

    monkeypatch.setattr(store, "read_pending_write", read_marker)
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            _, key = await _write(_service(session, store, caller=True))
            await session.rollback()
        async with maker() as session:
            service = _service(session, store)
            now = datetime.now(UTC) + timedelta(days=2)
            first = await service.sweep_lifecycle(
                principal="service:lifecycle", now=now, limit=1
            )
            assert not first.drained
            assert len(examined) == 1
            await service.sweep_lifecycle(
                principal="service:lifecycle", now=now, limit=1
            )
            assert len(examined) == 2
            assert any(
                record.levelno >= 30 and malformed in record.getMessage()
                for record in caplog.records
            )
            assert not store.object_exists(key)


async def test_pending_marker_parser_stops_at_size_bound(tmp_path, monkeypatch):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactValidationError

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    consumed = []

    def oversized(*args, **kwargs):
        for index in range(100):
            consumed.append(index)
            yield b"x" * 4096

    monkeypatch.setattr(store, "read_chunks", oversized)
    with pytest.raises(TemporalArtifactValidationError, match="bound"):
        store.read_pending_write("_moonmind/pending-writes/oversized.json")
    assert consumed == [0, 1]


@pytest.mark.parametrize("trusted", [False, True])
async def test_large_caller_payload_never_initializes_untracked_multipart(
    tmp_path, trusted
):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactValidationError

    class MultipartCapableStore(LocalTemporalArtifactStore):
        @property
        def supports_multipart(self):
            return True

        def create_multipart_upload(self, **kwargs):
            raise AssertionError(
                "caller admission must not open a deferred multipart upload"
            )

    store = MultipartCapableStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=store,
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
                direct_upload_max_bytes=4,
            )
            artifact, _ = await service.create(
                principal="service:test", content_type="text/plain", size_bytes=8
            )
            key = artifact.storage_key
            if trusted:
                await service.write_payload_complete(
                    artifact_id=artifact.artifact_id,
                    principal="service:test",
                    payload=b"12345678",
                )
                assert store.read_bytes(key) == b"12345678"
            else:
                with pytest.raises(TemporalArtifactValidationError, match="max bytes"):
                    await service.write_complete(
                        artifact_id=artifact.artifact_id,
                        principal="service:test",
                        payload=b"12345678",
                    )
                assert not store.object_exists(key)
            await session.rollback()
        await _sweep(maker, store)
        assert not store.object_exists(key)


async def test_lifecycle_drain_advances_past_malformed_marker_page(
    tmp_path, monkeypatch
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    malformed = "_moonmind/pending-writes/000-malformed.json"
    store.write_bytes(malformed, b"{}", content_type="application/json")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            _, key = await _write(_service(session, store, caller=True))
            await session.rollback()
        marker_dir = store.resolve_storage_key("_moonmind/pending-writes")
        valid = next(
            "_moonmind/pending-writes/" + path.name
            for path in marker_dir.iterdir()
            if path.name != "000-malformed.json"
        )
        pages = iter([[malformed], [valid], []])
        limits = []

        def bounded_page(*, limit, after=None):
            limits.append(limit)
            return next(pages, [])

        monkeypatch.setattr(store, "pending_write_keys", bounded_page)
        async with maker() as session:
            result = await _service(session, store).drain_lifecycle(
                principal="service:lifecycle",
                now=datetime.now(UTC) + timedelta(days=2),
                limit=1,
                time_budget=timedelta(seconds=5),
            )
        assert not store.object_exists(key)
        assert store.object_exists(malformed)
        assert not result.drained
        assert limits == [1, 1, 1]


async def test_s3_markers_and_payload_use_bounded_put_and_prefix_pagination(
    monkeypatch,
):
    import json
    from unittest.mock import Mock

    from moonmind.workflows.temporal import artifacts as module

    ordinary = Mock()
    writer = Mock()
    constructed = []

    def client(**kwargs):
        constructed.append(kwargs)
        return writer if "config" in kwargs else ordinary

    monkeypatch.setattr(module.boto3, "client", client)
    store = module.S3TemporalArtifactStore(
        endpoint_url="http://synthetic-object-store.invalid",
        bucket="synthetic-artifacts",
        access_key_id="synthetic-access",
        secret_access_key="synthetic-secret",
        region_name="us-east-1",
        use_ssl=False,
    )
    ordinary.head_bucket.assert_called_once_with(Bucket="synthetic-artifacts")
    target = store.build_storage_key(
        namespace="default", artifact_id="art_synthetic", now=datetime.now(UTC)
    )
    marker = store.record_pending_write(artifact_id="art_synthetic", storage_key=target)
    store.write_bytes(target, b"synthetic payload", content_type="text/plain")
    assert len(constructed) == 2
    config = constructed[1]["config"]
    assert config.connect_timeout == 10 and config.read_timeout == 60
    assert config.retries["total_max_attempts"] == 3
    assert writer.put_object.call_count == 2
    first, second = [call.kwargs for call in writer.put_object.call_args_list]
    assert first["Bucket"] == second["Bucket"] == "synthetic-artifacts"
    assert first["Key"] == marker
    assert json.loads(first["Body"])["storageKey"] == target
    assert second["Key"] == target and second["Body"] == b"synthetic payload"
    ordinary.put_object.assert_not_called()

    ordinary.list_objects_v2.return_value = {"Contents": [{"Key": marker}]}
    assert store.pending_write_keys(limit=2, after="cursor") == [marker]
    ordinary.list_objects_v2.assert_called_with(
        Bucket="synthetic-artifacts",
        Prefix="_moonmind/pending-writes/",
        MaxKeys=2,
        StartAfter="cursor",
    )
    store.pending_write_keys(limit=9999)
    ordinary.list_objects_v2.assert_called_with(
        Bucket="synthetic-artifacts",
        Prefix="_moonmind/pending-writes/",
        MaxKeys=1000,
    )


@pytest.mark.parametrize("failure", [TimeoutError, PermissionError])
async def test_pending_marker_failure_preserves_storage_error_contract(
    tmp_path, monkeypatch, failure
):
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactStorageAccessError,
        TemporalArtifactUnavailableError,
    )

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker, maker() as session:
        service = _service(session, store, caller=True)
        artifact, _ = await service.create(
            principal="service:test", content_type="text/plain"
        )

        def fail_marker(**kwargs):
            raise failure("synthetic marker failure")

        monkeypatch.setattr(store, "record_pending_write", fail_marker)
        expected = (
            TemporalArtifactUnavailableError
            if failure is TimeoutError
            else TemporalArtifactStorageAccessError
        )
        with pytest.raises(expected) as caught:
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="service:test",
                payload=b"not uploaded",
            )
        assert isinstance(caught.value.__cause__, failure)
        assert not store.object_exists(artifact.storage_key)
        await session.rollback()
