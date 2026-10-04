"""Journal rotation preserves the durable replacement before reclaiming copies."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentBridgeSession,
    TemporalArtifactRetentionClass,
    TemporalArtifactStatus,
)
from moonmind.omnigent import bridge_store
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)


@pytest_asyncio.fixture
async def journals(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/journal.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    blobs = LocalTemporalArtifactStore(tmp_path / "blobs")

    def service(session):
        return TemporalArtifactService(TemporalArtifactRepository(session), store=blobs)

    monkeypatch.setattr(
        bridge_store, "_journal_artifact_service", service, raising=False
    )
    store = bridge_store.OmnigentBridgeSessionStore(sessions)
    row = await store.get_or_create(
        request=AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="journal-test",
            idempotencyKey="journal-test",
        ),
        endpoint_ref="test-endpoint",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )

    async def publish(count, *, complete=True):
        refs = []
        async with sessions() as session:
            artifacts = service(session)
            for kind in ("raw", "normalized"):
                artifact, _ = await artifacts.create(
                    principal="service:omnigent-generic-host",
                    link={
                        "namespace": "default",
                        "workflow_id": "workflow-test",
                        "run_id": "run-test",
                        "link_type": f"runtime.omnigent.sse.{kind}",
                    },
                    metadata_json={
                        "name": f"runtime.omnigent.sse.{kind}.{count:08d}.jsonl",
                        "correlation_id": "journal-test",
                    },
                )
                if complete:
                    await artifacts.write_complete(
                        artifact_id=artifact.artifact_id,
                        principal="service:omnigent-generic-host",
                        payload=b"event\n" * count,
                        content_type="application/x-ndjson",
                    )
                refs.append(f"artifact:{artifact.artifact_id}")
        return tuple(refs)

    try:
        yield store, row.bridge_session_id, sessions, service, blobs, publish
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_committed_journal_replacement_reclaims_superseded_prefix(journals):
    store, session_id, sessions, service, blobs, publish = journals
    previous = await publish(1)
    replacement = await publish(2)
    await store.attach_active_journal_refs(
        session_id, raw_ref=previous[0], normalized_ref=previous[1]
    )
    await store.attach_active_journal_refs(
        session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
    )
    row = await store.get_bridge_session(session_id)
    assert (row.raw_events_ref, row.normalized_events_ref) == replacement
    async with sessions() as session:
        artifacts = service(session)
        for ref in previous:
            artifact = await artifacts._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            assert artifact.hard_deleted_at is not None
            assert blobs.resolve_storage_key(artifact.storage_key).exists() is False
        for ref in replacement:
            artifact = await artifacts._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            assert artifact.status is TemporalArtifactStatus.COMPLETE
            assert blobs.read_bytes(artifact.storage_key) == b"event\nevent\n"


@pytest.mark.asyncio
async def test_pending_replacement_preserves_committed_journal(journals):
    store, session_id, sessions, service, blobs, publish = journals
    previous = await publish(1)
    replacement = await publish(2, complete=False)
    await store.attach_active_journal_refs(
        session_id, raw_ref=previous[0], normalized_ref=previous[1]
    )
    with pytest.raises(RuntimeError, match="complete"):
        await store.attach_active_journal_refs(
            session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
        )
    row = await store.get_bridge_session(session_id)
    assert (row.raw_events_ref, row.normalized_events_ref) == previous
    async with sessions() as session:
        for ref in previous:
            artifact = await service(session)._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            assert blobs.resolve_storage_key(artifact.storage_key).exists()


@pytest.mark.asyncio
async def test_shorter_journal_cannot_replace_verified_progress(journals):
    store, session_id, _sessions, _service, _blobs, publish = journals
    previous = await publish(2)
    replacement = await publish(1)
    await store.attach_active_journal_refs(
        session_id, raw_ref=previous[0], normalized_ref=previous[1]
    )
    with pytest.raises(RuntimeError, match="progress"):
        await store.attach_active_journal_refs(
            session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
        )
    row = await store.get_bridge_session(session_id)
    assert (row.raw_events_ref, row.normalized_events_ref) == previous


@pytest.mark.asyncio
async def test_pinned_and_in_use_prefixes_remain_protected(journals):
    store, session_id, sessions, service, blobs, publish = journals
    previous = await publish(1)
    replacement = await publish(2)
    async with sessions() as session:
        artifacts = service(session)
        await artifacts.pin(
            artifact_id=previous[0].removeprefix("artifact:"),
            principal="service:operator",
            reason="keep",
        )
        await artifacts._repository.acquire_use_claim(
            artifact_id=previous[1].removeprefix("artifact:"),
            owner_principal="service:restore",
            request_id="restore-test",
            operation_kind="restore",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await artifacts._repository.commit()
    await store.attach_active_journal_refs(
        session_id, raw_ref=previous[0], normalized_ref=previous[1]
    )
    await store.attach_active_journal_refs(
        session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
    )
    async with sessions() as session:
        for ref in previous:
            artifact = await service(session)._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            assert artifact.hard_deleted_at is None
            assert blobs.resolve_storage_key(artifact.storage_key).exists()


@pytest.mark.asyncio
async def test_reclamation_failure_keeps_new_journal_and_recoverable_intent(
    journals, monkeypatch
):
    store, session_id, sessions, service, blobs, publish = journals
    previous = await publish(1)
    replacement = await publish(2)
    await store.attach_active_journal_refs(
        session_id, raw_ref=previous[0], normalized_ref=previous[1]
    )
    delete = blobs.delete

    def unavailable(_key):
        raise OSError("object store unavailable")

    monkeypatch.setattr(blobs, "delete", unavailable)
    await store.attach_active_journal_refs(
        session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
    )
    row = await store.get_bridge_session(session_id)
    assert (row.raw_events_ref, row.normalized_events_ref) == replacement
    async with sessions() as session:
        assert (
            len(
                await service(session)._repository.list_pending_deletion_intents(
                    limit=10
                )
            )
            == 2
        )
    monkeypatch.setattr(blobs, "delete", delete)
    async with sessions() as session:
        assert (
            await service(session).reconcile_deletion_intents(
                principal="service:storage-maintenance"
            )
            == 2
        )


@pytest.mark.asyncio
async def test_event_journals_use_seven_day_troubleshooting_retention(journals):
    _store, _session_id, sessions, service, _blobs, publish = journals
    refs = await publish(1)
    async with sessions() as session:
        for ref in refs:
            artifact = await service(session)._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            assert artifact.retention_class is TemporalArtifactRetentionClass.EPHEMERAL
            expiry = artifact.expires_at.replace(tzinfo=UTC)
            created = artifact.created_at.replace(tzinfo=UTC)
            assert timedelta(days=6) < expiry - created < timedelta(days=8)


@pytest.mark.asyncio
async def test_active_recovery_journals_survive_troubleshooting_expiry(journals):
    store, session_id, sessions, service, blobs, publish = journals
    refs = await publish(1)
    await store.attach_active_journal_refs(
        session_id, raw_ref=refs[0], normalized_ref=refs[1]
    )
    async with sessions() as session:
        artifacts = service(session)
        for ref in refs:
            artifact = await artifacts._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            artifact.expires_at = datetime.now(UTC) - timedelta(days=1)
        await artifacts._repository.commit()
        result = await artifacts.sweep_lifecycle(
            principal="service:storage-maintenance"
        )
        assert result.soft_deleted_count == 0
        for ref in refs:
            artifact = await artifacts._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            assert artifact.status is TemporalArtifactStatus.COMPLETE
            assert blobs.resolve_storage_key(artifact.storage_key).exists()

        row = await session.get(OmnigentBridgeSession, session_id)
        row.status = "completed"
        await session.commit()
        result = await artifacts.sweep_lifecycle(
            principal="service:storage-maintenance"
        )
        assert result.soft_deleted_count == 2


@pytest.mark.asyncio
async def test_ordinary_deletion_cannot_remove_current_recovery_journal(journals):
    store, session_id, sessions, service, blobs, publish = journals
    refs = await publish(1)
    await store.attach_active_journal_refs(
        session_id, raw_ref=refs[0], normalized_ref=refs[1]
    )
    async with sessions() as session:
        artifacts = service(session)
        with pytest.raises(TemporalArtifactStateError, match="active recovery journal"):
            await artifacts.soft_delete(
                artifact_id=refs[0].removeprefix("artifact:"),
                principal="service:storage-maintenance",
            )
        artifact = await artifacts._repository.get_artifact(
            refs[0].removeprefix("artifact:")
        )
        assert artifact.status is TemporalArtifactStatus.COMPLETE
        assert blobs.resolve_storage_key(artifact.storage_key).exists()
