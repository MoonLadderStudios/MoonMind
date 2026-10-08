"""Journal rotation preserves the durable replacement before reclaiming copies."""

import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentBridgeSession,
    OmnigentObservation,
    OmnigentSession,
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

    async def publish(count, *, complete=True, embedded=False, chunk=None, events=None):
        refs = []
        async with sessions() as session:
            artifacts = service(session)
            for kind in ("raw", "normalized"):
                if embedded:
                    name = f"runtime.omnigent.embedded.sse.{kind}/{count}.jsonl"
                elif chunk is None:
                    name = f"runtime.omnigent.sse.{kind}.{count:08d}.jsonl"
                else:
                    name = f"runtime.omnigent.sse.{kind}.{chunk:08d}.{count:08d}.jsonl"
                artifact, _ = await artifacts.create(
                    principal="service:omnigent-generic-host",
                    link={
                        "namespace": "default",
                        "workflow_id": "workflow-test",
                        "run_id": "run-test",
                        "link_type": f"runtime.omnigent.sse.{kind}",
                    },
                    metadata_json={
                        "name": name,
                        "correlation_id": "journal-test",
                    },
                )
                if complete:
                    await artifacts.write_complete(
                        artifact_id=artifact.artifact_id,
                        principal="service:omnigent-generic-host",
                        payload=(
                            "".join(
                                json.dumps(event) + "\n" for event in events
                            ).encode()
                            if events is not None
                            else b"event\n" * count
                        ),
                        content_type="application/x-ndjson",
                    )
                refs.append(f"artifact:{artifact.artifact_id}")
        return tuple(refs)

    try:
        yield store, row.bridge_session_id, sessions, service, blobs, publish
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("embedded", [False, True])
async def test_committed_journal_replacement_reclaims_superseded_prefix(
    journals, embedded
):
    store, session_id, sessions, service, blobs, publish = journals
    previous = await publish(1, embedded=embedded)
    replacement = await publish(2, embedded=embedded)
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
@pytest.mark.parametrize("embedded", [False, True])
async def test_event_payload_refs_follow_complete_journal_replacement(
    journals, embedded
):
    store, session_id, sessions, service, blobs, publish = journals
    previous = await publish(1, embedded=embedded)
    replacement = await publish(2, embedded=embedded)
    await store.attach_active_journal_refs(
        session_id, raw_ref=previous[0], normalized_ref=previous[1]
    )
    await store.append_events(
        session_id,
        [
            {
                "eventType": "response.delta",
                "artifactRef": previous[1],
                "textPreview": "first",
                "deduplicationKey": "first-event",
            }
        ],
    )
    await store.attach_active_journal_refs(
        session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
    )
    event = (await store.list_event_page(session_id)).rows[0]
    assert event.artifact_ref == replacement[1]
    assert event.text_preview == "first"
    async with sessions() as session:
        artifacts = service(session)
        payload = await artifacts._repository.get_artifact(
            event.artifact_ref.removeprefix("artifact:")
        )
        assert blobs.read_bytes(payload.storage_key) == b"event\nevent\n"
        old = await artifacts._repository.get_artifact(
            previous[1].removeprefix("artifact:")
        )
        assert old.hard_deleted_at is not None


@pytest.mark.asyncio
async def test_delayed_event_append_uses_current_complete_journal(journals):
    store, session_id, _sessions, _service, _blobs, publish = journals
    previous = await publish(1)
    replacement = await publish(2)
    await store.attach_active_journal_refs(
        session_id, raw_ref=previous[0], normalized_ref=previous[1]
    )
    await store.attach_active_journal_refs(
        session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
    )
    appended = await store.append_events(
        session_id,
        [
            {
                "eventType": "response.delta",
                "artifactRef": previous[1],
                "deduplicationKey": "delayed-event",
            }
        ],
    )
    assert appended[0].artifact_ref == replacement[1]


@pytest.mark.asyncio
async def test_backfilled_observation_keeps_original_payload_artifact(journals):
    store, session_id, sessions, service, blobs, publish = journals
    previous = await publish(1)
    replacement = await publish(2)
    await store.attach_active_journal_refs(
        session_id, raw_ref=previous[0], normalized_ref=previous[1]
    )
    async with sessions() as session:
        session.add(
            OmnigentSession(
                session_id="canonical-session",
                moonmind_workflow_id="workflow-test",
                provider="omnigent",
            )
        )
        await session.flush()
        session.add(
            OmnigentObservation(
                observation_id="backfilled-event",
                session_id="canonical-session",
                observation_type="bridge_event",
                source="legacy-backfill",
                observed_at=datetime.now(UTC),
                deduplication_key="backfilled-event",
                payload_ref=previous[1],
                source_digest="original-event-digest",
                bounded_index_={"artifact_ref": previous[1]},
            )
        )
        await session.commit()
    await store.attach_active_journal_refs(
        session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
    )
    async with sessions() as session:
        observation = await session.get(OmnigentObservation, "backfilled-event")
        assert observation.payload_ref == previous[1]
        assert observation.source_digest == "original-event-digest"
        artifact = await service(session)._repository.get_artifact(
            observation.payload_ref.removeprefix("artifact:")
        )
        assert artifact.hard_deleted_at is None
        assert blobs.read_bytes(artifact.storage_key) == b"event\n"


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
    await store.append_events(
        session_id,
        [
            {
                "eventType": "response.delta",
                "artifactRef": previous[1],
                "deduplicationKey": "protected-event",
            }
        ],
    )
    await store.attach_active_journal_refs(
        session_id, raw_ref=replacement[0], normalized_ref=replacement[1]
    )
    assert (await store.list_events(session_id))[0].artifact_ref == replacement[1]
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


async def _artifact(sessions, service, ref):
    async with sessions() as session:
        return await service(session)._repository.get_artifact(
            ref.removeprefix("artifact:")
        )


@pytest.mark.asyncio
async def test_next_chunk_retains_completed_chunk_in_journal_history(journals):
    store, session_id, sessions, service, blobs, publish = journals
    first = await publish(1, chunk=0)
    completed = await publish(2, chunk=0)
    following = await publish(1, chunk=2)
    await store.attach_active_journal_refs(
        session_id, raw_ref=first[0], normalized_ref=first[1], new_chunk=True
    )
    await store.append_events(
        session_id,
        [
            {
                "eventType": "response.delta",
                "artifactRef": first[1],
                "deduplicationKey": "chunk-zero-event",
            }
        ],
    )
    await store.attach_active_journal_refs(
        session_id, raw_ref=completed[0], normalized_ref=completed[1]
    )
    await store.attach_active_journal_refs(
        session_id,
        raw_ref=following[0],
        normalized_ref=following[1],
        new_chunk=True,
    )

    row = await store.get_bridge_session(session_id)
    assert (row.raw_events_ref, row.normalized_events_ref) == following
    assert row.metadata_[bridge_store.SEALED_JOURNAL_CHUNKS_KEY] == [
        {"raw": completed[0], "normalized": completed[1]}
    ]
    # Within a chunk the longer version supersedes the shorter one; starting
    # the next chunk neither re-points nor reclaims the completed chunk.
    assert (await store.list_events(session_id))[0].artifact_ref == completed[1]
    for ref in first:
        assert (await _artifact(sessions, service, ref)).hard_deleted_at is not None
    for ref in completed:
        artifact = await _artifact(sessions, service, ref)
        assert artifact.status is TemporalArtifactStatus.COMPLETE
        assert blobs.read_bytes(artifact.storage_key) == b"event\nevent\n"


@pytest.mark.asyncio
async def test_replacing_a_different_chunk_requires_retaining_it(journals):
    store, session_id, _sessions, _service, _blobs, publish = journals
    current = await publish(4, chunk=0)
    unrelated = await publish(5, chunk=4)
    await store.attach_active_journal_refs(
        session_id, raw_ref=current[0], normalized_ref=current[1], new_chunk=True
    )
    with pytest.raises(RuntimeError, match="progress"):
        await store.attach_active_journal_refs(
            session_id, raw_ref=unrelated[0], normalized_ref=unrelated[1]
        )
    row = await store.get_bridge_session(session_id)
    assert (row.raw_events_ref, row.normalized_events_ref) == current
    assert bridge_store.SEALED_JOURNAL_CHUNKS_KEY not in (row.metadata_ or {})


@pytest.mark.asyncio
async def test_completed_chunks_of_live_journal_survive_troubleshooting_expiry(
    journals,
):
    store, session_id, sessions, service, blobs, publish = journals
    completed = await publish(2, chunk=0)
    current = await publish(1, chunk=2)
    for refs in (completed, current):
        await store.attach_active_journal_refs(
            session_id, raw_ref=refs[0], normalized_ref=refs[1], new_chunk=True
        )
    async with sessions() as session:
        artifacts = service(session)
        for ref in (*completed, *current):
            artifact = await artifacts._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            artifact.expires_at = datetime.now(UTC) - timedelta(days=1)
        await artifacts._repository.commit()
        result = await artifacts.sweep_lifecycle(
            principal="service:storage-maintenance"
        )
        assert result.soft_deleted_count == 0
        with pytest.raises(TemporalArtifactStateError, match="active recovery journal"):
            await artifacts.soft_delete(
                artifact_id=completed[0].removeprefix("artifact:"),
                principal="service:storage-maintenance",
            )

        row = await session.get(OmnigentBridgeSession, session_id)
        row.status = "completed"
        await session.commit()
        result = await artifacts.sweep_lifecycle(
            principal="service:storage-maintenance"
        )
        assert result.soft_deleted_count == 4


@pytest.mark.asyncio
async def test_complete_terminal_journals_replace_chunk_history(journals):
    store, session_id, _sessions, _service, _blobs, publish = journals
    completed = await publish(2, chunk=0)
    current = await publish(1, chunk=2)
    for refs in (completed, current):
        await store.attach_active_journal_refs(
            session_id, raw_ref=refs[0], normalized_ref=refs[1], new_chunk=True
        )
    final = await publish(3)
    await store.mark_terminal(
        "journal-test",
        status="completed",
        terminal_refs={
            "metadataRefs": {
                "rawSseStreamRef": final[0],
                "normalizedEventStreamRef": final[1],
            }
        },
    )
    row = await store.get_bridge_session(session_id)
    assert (row.raw_events_ref, row.normalized_events_ref) == final
    assert bridge_store.SEALED_JOURNAL_CHUNKS_KEY not in (row.metadata_ or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_commit", [False, True])
@pytest.mark.parametrize("rebuild_events", [False, True])
async def test_terminal_event_payloads_survive_expired_chunk_cleanup(
    journals, monkeypatch, interrupt_commit, rebuild_events
):
    store, session_id, sessions, service, blobs, publish = journals
    events = [
        {
            "sequence": index + 1,
            "eventType": "response.delta",
            "textPreview": f"token-{index}",
            "deduplicationKey": f"event-{index}",
        }
        for index in range(3)
    ]
    chunks = []
    for start, chunk_events in ((0, events[:2]), (2, events[2:])):
        refs = await publish(len(chunk_events), chunk=start, events=chunk_events)
        chunks.extend(refs)
        await store.attach_active_journal_refs(
            session_id, raw_ref=refs[0], normalized_ref=refs[1], new_chunk=True
        )
        for event in chunk_events:
            event["artifactRef"] = refs[1]
        await store.append_events(session_id, chunk_events)

    # An old session can outlive the troubleshooting retention of every chunk.
    async with sessions() as session:
        artifacts = service(session)
        for ref in chunks:
            artifact = await artifacts._repository.get_artifact(
                ref.removeprefix("artifact:")
            )
            artifact.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

    final = await publish(len(events), events=events)
    terminal_refs = {
        "metadataRefs": {
            "rawSseStreamRef": final[0],
            "normalizedEventStreamRef": final[1],
        }
    }
    if interrupt_commit:

        async def fail_commit(_session):
            raise RuntimeError("worker lost before terminal commit")

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail_commit)
            with pytest.raises(RuntimeError, match="before terminal commit"):
                await store.mark_terminal(
                    "journal-test",
                    status="completed",
                    terminal_refs=terminal_refs,
                    events=events if rebuild_events else None,
                )
        # Reattachment sees the previous durable index and protected chunks.
        store = bridge_store.OmnigentBridgeSessionStore(sessions)
        row = await store.get_bridge_session(session_id)
        assert row.first_message_state != bridge_store.FIRST_MESSAGE_TERMINAL
        assert bridge_store.SEALED_JOURNAL_CHUNKS_KEY in row.metadata_
        assert [
            event.artifact_ref for event in await store.list_events(session_id)
        ] == [event["artifactRef"] for event in events]

    async with sessions() as session:
        result = await service(session).sweep_lifecycle(
            principal="service:storage-maintenance"
        )
        assert result.soft_deleted_count == 0

    await store.mark_terminal(
        "journal-test",
        status="completed",
        terminal_refs=terminal_refs,
        events=events if rebuild_events else None,
    )
    async with sessions() as session:
        result = await service(session).sweep_lifecycle(
            principal="service:storage-maintenance"
        )
        assert result.soft_deleted_count == len(chunks)
        for ref in chunks:
            await service(session).hard_delete(
                artifact_id=ref.removeprefix("artifact:"),
                principal="service:storage-maintenance",
            )

    # Reopen the store after cleanup and read each payload via its index locator.
    store = bridge_store.OmnigentBridgeSessionStore(sessions)
    indexed = (await store.list_event_page(session_id)).rows
    assert [event.sequence for event in indexed] == [1, 2, 3]
    assert [event.deduplication_key for event in indexed] == [
        event["deduplicationKey"] for event in events
    ]
    for event in indexed:
        artifact = await _artifact(sessions, service, event.artifact_ref)
        assert artifact.status is TemporalArtifactStatus.COMPLETE
        payload = [
            json.loads(line)
            for line in blobs.read_bytes(artifact.storage_key).splitlines()
        ]
        assert payload[event.sequence - 1]["textPreview"] == event.text_preview
        assert event.artifact_ref == final[1]
