"""Artifact work participates in its caller's transaction without publishing it."""

import asyncio

import pytest
from sqlalchemy import select

from api_service.db.models import TemporalArtifact, TemporalArtifactLink
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactTransactionOwnership,
    TemporalArtifactValidationError,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactUnavailableError,
)
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = pytest.mark.asyncio


async def _seed(maker, store):
    async with maker() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session), store=store
        )
        artifact, _ = await service.create(
            principal="service:owner", content_type="text/plain"
        )
        await service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="service:owner",
            payload=b"retained",
        )
        return artifact.artifact_id


def _caller_service(session, store):
    return TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=store,
        transaction_ownership=ArtifactTransactionOwnership.CALLER,
    )


@pytest.mark.parametrize("operation", ["read", "link"])
async def test_outer_rollback_includes_artifact_work_and_unrelated_writes(
    tmp_path, operation
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        artifact_id = await _seed(maker, store)
        async with maker() as session:
            artifact = await session.get(TemporalArtifact, artifact_id)
            artifact.metadata_json = {"caller_write": "uncommitted"}
            await session.flush()
            service = _caller_service(session, store)
            if operation == "read":
                _, body = await service.read(
                    artifact_id=artifact_id,
                    access=ArtifactAccessContext.for_service("service:owner"),
                )
                assert body == b"retained"
            else:
                await service.link_artifact(
                    artifact_id=artifact_id,
                    principal="service:owner",
                    execution_ref={
                        "namespace": "default",
                        "workflow_id": "caller",
                        "run_id": "run",
                        "link_type": "input.test",
                    },
                )
            async with maker() as observer:
                observed = await observer.get(TemporalArtifact, artifact_id)
                assert observed.metadata_json != {"caller_write": "uncommitted"}
                assert (
                    list((await observer.scalars(select(TemporalArtifactLink))).all())
                    == []
                )
            assert artifact.metadata_json == {"caller_write": "uncommitted"}
            await session.rollback()
        async with maker() as observer:
            assert (
                await observer.get(TemporalArtifact, artifact_id)
            ).metadata_json != {"caller_write": "uncommitted"}
            assert (
                list((await observer.scalars(select(TemporalArtifactLink))).all()) == []
            )


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_read_preserves_caller_write_and_original_failure(
    tmp_path, monkeypatch, cancel
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        artifact_id = await _seed(maker, store)
        async with maker() as session:
            artifact = await session.get(TemporalArtifact, artifact_id)
            artifact.metadata_json = {"caller_write": "must survive"}
            await session.flush()
            service = _caller_service(session, store)
            primary = (
                asyncio.CancelledError("consumer cancelled")
                if cancel
                else TimeoutError("storage failed")
            )

            def fail(*args, **kwargs):
                raise primary

            monkeypatch.setattr(store, "read_bytes", fail)
            expected = (
                asyncio.CancelledError if cancel else TemporalArtifactUnavailableError
            )
            with pytest.raises(expected) as caught:
                await service.read(
                    artifact_id=artifact_id,
                    access=ArtifactAccessContext.for_service("service:owner"),
                )
            if cancel:
                assert caught.value is primary
            else:
                assert type(caught.value.__cause__) is type(primary)
                assert caught.value.__cause__.args == primary.args
            assert session.is_active
            assert artifact.metadata_json == {"caller_write": "must survive"}
            async with maker() as observer:
                assert (
                    await observer.get(TemporalArtifact, artifact_id)
                ).metadata_json != {"caller_write": "must survive"}
            await session.commit()
        async with maker() as observer:
            assert (
                await observer.get(TemporalArtifact, artifact_id)
            ).metadata_json == {"caller_write": "must survive"}


@pytest.mark.parametrize("commit", [False, True])
async def test_create_and_complete_share_outer_transaction(tmp_path, commit):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            service = _caller_service(session, store)
            artifact, _ = await service.create(
                principal="service:owner", content_type="text/plain"
            )
            artifact_id = artifact.artifact_id
            await service.write_complete(
                artifact_id=artifact_id, principal="service:owner", payload=b"new bytes"
            )
            async with maker() as observer:
                assert await observer.get(TemporalArtifact, artifact_id) is None
            if commit:
                await session.commit()
            else:
                await session.rollback()
        async with maker() as observer:
            artifact = await observer.get(TemporalArtifact, artifact_id)
            if commit:
                assert artifact is not None
                _, body = await TemporalArtifactService(
                    TemporalArtifactRepository(observer), store=store
                ).read(
                    artifact_id=artifact_id,
                    access=ArtifactAccessContext.for_service("service:owner"),
                )
                assert body == b"new bytes"
            else:
                assert artifact is None


@pytest.mark.parametrize("commit", [False, True])
async def test_preview_mutations_survive_read_cleanup_but_follow_outer_transaction(
    tmp_path, commit
):
    from api_service.db.models import TemporalArtifactRedactionLevel

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        artifact_id = await _seed(maker, store)
        async with maker() as session:
            artifact = await session.get(TemporalArtifact, artifact_id)
            artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
            await session.commit()
        async with maker() as session:
            service = _caller_service(session, store)
            preview = await service.compute_preview(
                artifact_id=artifact_id,
                access=ArtifactAccessContext.for_service("service:owner"),
            )
            preview_id = preview.artifact_id
            assert (await session.get(TemporalArtifact, artifact_id)).metadata_json[
                "preview_artifact_id"
            ] == preview_id
            assert await session.get(TemporalArtifact, preview_id) is not None
            async with maker() as observer:
                assert await observer.get(TemporalArtifact, preview_id) is None
            if commit:
                await session.commit()
            else:
                await session.rollback()
        async with maker() as observer:
            metadata = (
                await observer.get(TemporalArtifact, artifact_id)
            ).metadata_json or {}
            assert (
                await observer.get(TemporalArtifact, preview_id) is not None
            ) is commit
            assert (metadata.get("preview_artifact_id") == preview_id) is commit


@pytest.mark.parametrize("failure", [None, "storage", "cancel"])
async def test_cleanup_control_failure_does_not_replace_primary_or_rollback_caller(
    tmp_path, monkeypatch, failure
):
    from types import SimpleNamespace

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        artifact_id = await _seed(maker, store)
        async with maker() as session:
            artifact = await session.get(TemporalArtifact, artifact_id)
            artifact.metadata_json = {"caller_write": "survives cleanup"}
            await session.flush()
            service = _caller_service(session, store)
            begin = service._repository.begin_use_claim_savepoint
            cleanup = ConnectionError("savepoint cleanup response lost")

            async def begin_with_cleanup_failure():
                outer, savepoint = await begin()

                async def rollback():
                    await savepoint.rollback()
                    raise cleanup

                return outer, SimpleNamespace(rollback=rollback)

            monkeypatch.setattr(
                service._repository,
                "begin_use_claim_savepoint",
                begin_with_cleanup_failure,
            )
            primary = (
                asyncio.CancelledError("consumer cancelled")
                if failure == "cancel"
                else TimeoutError("storage unavailable")
            )
            if failure:

                def fail(*args, **kwargs):
                    raise primary

                monkeypatch.setattr(store, "read_bytes", fail)
            expected = {
                None: ConnectionError,
                "storage": TemporalArtifactUnavailableError,
                "cancel": asyncio.CancelledError,
            }[failure]
            with pytest.raises(expected) as caught:
                await service.read(
                    artifact_id=artifact_id,
                    access=ArtifactAccessContext.for_service("service:owner"),
                )
            if failure:
                if failure == "cancel":
                    assert caught.value is primary
                else:
                    assert type(caught.value.__cause__) is type(primary)
                    assert caught.value.__cause__.args == primary.args
                assert any("ConnectionError" in note for note in caught.value.__notes__)
            else:
                assert caught.value is cleanup
            assert session.is_active
            assert artifact.metadata_json == {"caller_write": "survives cleanup"}
            await session.commit()
        async with maker() as observer:
            assert (
                await observer.get(TemporalArtifact, artifact_id)
            ).metadata_json == {"caller_write": "survives cleanup"}


@pytest.mark.parametrize("surface", ["read_chunks", "presign_download"])
async def test_durable_handle_requires_standalone_owner_without_publishing_caller(
    tmp_path, surface
):
    from api_service.db.models import TemporalArtifactUseClaim

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        artifact_id = await _seed(maker, store)
        async with maker() as session:
            artifact = await session.get(TemporalArtifact, artifact_id)
            artifact.metadata_json = {"caller_write": "not published"}
            service = _caller_service(session, store)
            with pytest.raises(
                TemporalArtifactValidationError, match="standalone transaction owner"
            ):
                await getattr(service, surface)(
                    artifact_id=artifact_id,
                    access=ArtifactAccessContext.for_service("service:owner"),
                )
            assert artifact.metadata_json == {"caller_write": "not published"}
            assert (
                list((await session.scalars(select(TemporalArtifactUseClaim))).all())
                == []
            )
            async with maker() as observer:
                assert (
                    await observer.get(TemporalArtifact, artifact_id)
                ).metadata_json != {"caller_write": "not published"}
            await session.rollback()


async def test_cancelled_claim_acquisition_preserves_cancellation_when_cleanup_fails(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        artifact_id = await _seed(maker, store)
        async with maker() as session:
            artifact = await session.get(TemporalArtifact, artifact_id)
            artifact.metadata_json = {"caller_write": "retained"}
            await session.flush()
            service = _caller_service(session, store)
            begin = service._repository.begin_use_claim_savepoint
            primary = asyncio.CancelledError("claim cancelled")

            async def begin_with_cleanup_failure():
                outer, savepoint = await begin()

                async def rollback():
                    await savepoint.rollback()
                    raise ConnectionError("claim cleanup response lost")

                return outer, SimpleNamespace(rollback=rollback)

            async def acquire(**kwargs):
                raise primary

            monkeypatch.setattr(
                service._repository,
                "begin_use_claim_savepoint",
                begin_with_cleanup_failure,
            )
            monkeypatch.setattr(service._repository, "acquire_use_claim", acquire)
            with pytest.raises(asyncio.CancelledError) as caught:
                await service.read(
                    artifact_id=artifact_id,
                    access=ArtifactAccessContext.for_service("service:owner"),
                )
            assert caught.value is primary
            assert any("ConnectionError" in note for note in caught.value.__notes__)
            assert session.is_active
            assert artifact.metadata_json == {"caller_write": "retained"}
            await session.commit()
        async with maker() as observer:
            assert (
                await observer.get(TemporalArtifact, artifact_id)
            ).metadata_json == {"caller_write": "retained"}


@pytest.mark.parametrize("cancel", [False, True])
async def test_savepoint_start_failure_survives_cleanup_failure(
    tmp_path, monkeypatch, cancel
):
    from sqlalchemy.ext.asyncio import AsyncSessionTransaction

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        artifact_id = await _seed(maker, store)
        async with maker() as session:
            artifact = await session.get(TemporalArtifact, artifact_id)
            artifact.metadata_json = {"caller_write": "before savepoint"}
            await session.flush()
            service = _caller_service(session, store)
            primary = (
                asyncio.CancelledError("start cancelled")
                if cancel
                else RuntimeError("savepoint unavailable")
            )
            rollback = AsyncSessionTransaction.rollback

            async def fail_connection(*args, **kwargs):
                raise primary

            async def fail_rollback(transaction):
                await rollback(transaction)
                raise ConnectionError("start cleanup response lost")

            with monkeypatch.context() as fault:
                fault.setattr(session, "connection", fail_connection)
                fault.setattr(AsyncSessionTransaction, "rollback", fail_rollback)
                with pytest.raises(type(primary)) as caught:
                    await service.read(
                        artifact_id=artifact_id,
                        access=ArtifactAccessContext.for_service("service:owner"),
                    )
            assert caught.value is primary
            assert any("ConnectionError" in note for note in caught.value.__notes__)
            assert session.is_active
            assert artifact.metadata_json == {"caller_write": "before savepoint"}
            await session.commit()
        async with maker() as observer:
            assert (
                await observer.get(TemporalArtifact, artifact_id)
            ).metadata_json == {"caller_write": "before savepoint"}


@pytest.mark.parametrize("cancel", [False, True])
async def test_standalone_grant_owner_preserves_primary_when_rollback_fails(
    monkeypatch, cancel
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from moonmind.omnigent import bridge_artifacts

    primary = (
        asyncio.CancelledError("admission cancelled")
        if cancel
        else ValueError("invalid input authority")
    )
    rollback_calls = []

    async def rollback():
        rollback_calls.append(True)
        raise ConnectionError("rollback unavailable")

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(rollback=rollback)

    async def reject(**kwargs):
        raise primary

    monkeypatch.setattr(
        bridge_artifacts, "link_verified_execution_plan_inputs_in_session", reject
    )
    with pytest.raises(type(primary)) as caught:
        await bridge_artifacts.link_verified_execution_plan_inputs(
            session_factory=factory,
            plan=object(),
            binding=None,
            workflow_id="admitted",
            run_id="run",
            access=ArtifactAccessContext.for_execution("admitted"),
        )
    assert caught.value is primary
    assert rollback_calls == [True]
    assert any("ConnectionError" in note for note in caught.value.__notes__)


async def test_caller_cannot_delete_bytes_outside_rollback_boundary(tmp_path):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        artifact_id = await _seed(maker, store)
        async with maker() as session:
            artifact = await session.get(TemporalArtifact, artifact_id)
            storage_key = artifact.storage_key
            artifact.metadata_json = {"caller_write": "must not publish"}
            service = _caller_service(session, store)
            with pytest.raises(
                TemporalArtifactValidationError, match="standalone transaction owner"
            ):
                await service.hard_delete(
                    artifact_id=artifact_id, principal="service:owner"
                )
            assert store.read_bytes(storage_key) == b"retained"
            assert artifact.hard_deleted_at is None
            await session.rollback()
        async with maker() as observer:
            artifact = await observer.get(TemporalArtifact, artifact_id)
            assert artifact.hard_deleted_at is None
            assert artifact.metadata_json != {"caller_write": "must not publish"}
            assert store.read_bytes(storage_key) == b"retained"
