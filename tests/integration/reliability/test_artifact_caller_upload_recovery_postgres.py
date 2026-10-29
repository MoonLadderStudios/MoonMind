"""Caller-owned uploads remain safe until their PostgreSQL owner resolves."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api_service.db.models import Base, TemporalArtifact
from moonmind.workflows.temporal.artifacts import ArtifactTransactionOwnership
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import (
    _complete_artifact,
    _service,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def test_rolled_back_caller_upload_is_reclaimed_by_lifecycle(tmp_path):
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        async with sessions() as producer:
            service = _service(
                producer,
                tmp_path,
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
            )
            artifact = await _complete_artifact(
                service, principal="operator", payload=b"rolled-back producer bytes"
            )
            artifact_id, key = artifact.artifact_id, artifact.storage_key
            assert service._store.object_exists(key)
            await producer.rollback()
        async with sessions() as observer:
            assert (
                await observer.scalar(
                    select(TemporalArtifact).where(
                        TemporalArtifact.artifact_id == artifact_id
                    )
                )
                is None
            )
            sweeper = _service(observer, tmp_path)
            await sweeper.sweep_lifecycle(
                principal="service:lifecycle",
                now=datetime.now(UTC) + timedelta(days=30),
            )
            assert not sweeper._store.object_exists(key)


@pytest.mark.parametrize("outcome", ["commit", "rollback"])
async def test_sweep_skips_active_upload_then_observes_owner_outcome(tmp_path, outcome):
    import asyncio

    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        async with sessions() as producer:
            service = _service(
                producer,
                tmp_path,
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
            )
            artifact = await _complete_artifact(
                service, principal="operator", payload=b"active transaction bytes"
            )
            key = artifact.storage_key
            # Keep committed content live independently of the marker's age.
            artifact.expires_at = datetime.now(UTC) + timedelta(days=90)
            async with sessions() as observer:
                sweeper = _service(observer, tmp_path)
                await asyncio.wait_for(
                    sweeper.sweep_lifecycle(
                        principal="service:lifecycle",
                        now=datetime.now(UTC) + timedelta(days=2),
                    ),
                    5,
                )
                assert sweeper._store.read_bytes(key) == b"active transaction bytes"
            await getattr(producer, outcome)()
        async with sessions() as observer:
            sweeper = _service(observer, tmp_path)
            await sweeper.sweep_lifecycle(
                principal="service:lifecycle", now=datetime.now(UTC) + timedelta(days=2)
            )
            assert sweeper._store.object_exists(key) is (outcome == "commit")


async def test_rollback_does_not_remove_other_committed_reference(tmp_path):
    payload = b"shared committed content"
    content = {
        "content_addressed_scope": "saved-work",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        async with sessions() as owner:
            existing = await _complete_artifact(
                _service(owner, tmp_path),
                principal="other-owner",
                payload=payload,
                **content,
            )
            existing.expires_at = datetime.now(UTC) + timedelta(days=90)
            key = existing.storage_key
            await owner.commit()
        async with sessions() as producer:
            service = _service(
                producer,
                tmp_path,
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
            )
            duplicate = await _complete_artifact(
                service, principal="operator", payload=payload, **content
            )
            assert duplicate.storage_key == key
            await producer.rollback()
        async with sessions() as observer:
            sweeper = _service(observer, tmp_path)
            await sweeper.sweep_lifecycle(
                principal="service:lifecycle", now=datetime.now(UTC) + timedelta(days=2)
            )
            assert sweeper._store.read_bytes(key) == payload


async def test_cleanup_fence_serializes_new_reference_before_upload(
    tmp_path, monkeypatch
):
    import asyncio

    from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository

    payload = b"cleanup races a new reference"
    content = {
        "content_addressed_scope": "saved-work",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        async with sessions() as abandoned:
            artifact = await _complete_artifact(
                _service(
                    abandoned,
                    tmp_path,
                    transaction_ownership=ArtifactTransactionOwnership.CALLER,
                ),
                principal="operator",
                payload=payload,
                **content,
            )
            key = artifact.storage_key
            await abandoned.rollback()

        cleanup_locked = asyncio.Event()
        release_cleanup = asyncio.Event()
        producer_reached_fence = asyncio.Event()
        actual_fence = TemporalArtifactRepository.fence_storage_key

        async with sessions() as cleanup_session, sessions() as producer_session:

            async def fence(repository, **kwargs):
                if (
                    repository._session is producer_session
                    and kwargs["storage_key"] == key
                ):
                    producer_reached_fence.set()
                acquired = await actual_fence(repository, **kwargs)
                if (
                    repository._session is cleanup_session
                    and kwargs["storage_key"] == key
                    and acquired
                ):
                    cleanup_locked.set()
                    await release_cleanup.wait()
                return acquired

            monkeypatch.setattr(TemporalArtifactRepository, "fence_storage_key", fence)
            sweeper = _service(cleanup_session, tmp_path)
            cleanup = asyncio.create_task(
                sweeper.sweep_lifecycle(
                    principal="service:lifecycle",
                    now=datetime.now(UTC) + timedelta(days=2),
                )
            )

            async def produce():
                service = _service(
                    producer_session,
                    tmp_path,
                    transaction_ownership=ArtifactTransactionOwnership.CALLER,
                )
                fresh = await _complete_artifact(
                    service, principal="operator", payload=payload, **content
                )
                fresh.expires_at = datetime.now(UTC) + timedelta(days=90)
                await producer_session.commit()
                return fresh.artifact_id

            producer = None
            try:
                await asyncio.wait_for(cleanup_locked.wait(), 5)
                producer = asyncio.create_task(produce())
                await asyncio.wait_for(producer_reached_fence.wait(), 5)
                # The independently admitted writer cannot finish while the
                # cleanup owner's deletion fence is held.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(producer), 0.1)
                release_cleanup.set()
                await asyncio.wait_for(cleanup, 5)
                fresh_id = await asyncio.wait_for(producer, 5)
            finally:
                release_cleanup.set()
                for task in (cleanup, producer):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (cleanup, producer) if task is not None),
                    return_exceptions=True,
                )
        async with sessions() as observer:
            row = await observer.get(TemporalArtifact, fresh_id)
            assert row is not None
            assert (
                _service(observer, tmp_path)._store.read_bytes(row.storage_key)
                == payload
            )


async def test_caller_rechecks_deleted_upload_after_waiting_for_storage_fence(
    tmp_path, monkeypatch
):
    import asyncio

    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactStateError,
    )

    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        async with sessions() as creator:
            pending, _ = await _service(creator, tmp_path).create(principal="operator")
            artifact_id, key = pending.artifact_id, pending.storage_key
        reached_fence = asyncio.Event()
        release_fence = asyncio.Event()
        actual_fence = TemporalArtifactRepository.fence_storage_key
        async with sessions() as writer_session:

            async def fence(repository, **kwargs):
                if repository._session is writer_session:
                    reached_fence.set()
                    await release_fence.wait()
                return await actual_fence(repository, **kwargs)

            monkeypatch.setattr(TemporalArtifactRepository, "fence_storage_key", fence)
            writer = _service(
                writer_session,
                tmp_path,
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
            )
            upload = asyncio.create_task(
                writer.write_complete(
                    artifact_id=artifact_id,
                    principal="operator",
                    payload=b"must not resurrect",
                )
            )
            try:
                await asyncio.wait_for(reached_fence.wait(), 5)
                async with sessions() as deleter_session:
                    deleter = _service(deleter_session, tmp_path)
                    await asyncio.wait_for(
                        deleter.soft_delete(
                            artifact_id=artifact_id, principal="operator"
                        ),
                        5,
                    )
                    await asyncio.wait_for(
                        deleter.hard_delete(
                            artifact_id=artifact_id, principal="operator"
                        ),
                        5,
                    )
                release_fence.set()
                with pytest.raises(TemporalArtifactStateError, match="deleted"):
                    await asyncio.wait_for(upload, 5)
                await writer_session.rollback()
            finally:
                release_fence.set()
                if not upload.done():
                    upload.cancel()
                await asyncio.gather(upload, return_exceptions=True)
        async with sessions() as observer:
            row = await observer.get(TemporalArtifact, artifact_id)
            assert row.hard_deleted_at is not None
            assert not _service(observer, tmp_path)._store.object_exists(key)
