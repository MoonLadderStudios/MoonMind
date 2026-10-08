"""Real database/blob actors for the disposable journal deployment journey.

The legacy actor deliberately retains the pre-chunk serialized reader and
retention predicate. It never calls the new journal reconstruction helper.
No provider account, API server, or Temporal worker is required.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import exists, or_, select

from api_service.db.base import async_session_maker, engine
from api_service.db.models import Base, OmnigentBridgeSession, TemporalArtifact
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)

STATE = Path("/journal-state")
KEY = "deployment-journal-journey"


def record(name, value):
    path = STATE / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True))
    temporary.replace(path)


async def bridge():
    store = OmnigentBridgeSessionStore(async_session_maker)
    row = await store.get_or_create(
        request=AgentExecutionRequest(
            agentKind="external", agentId="omnigent", correlationId=KEY,
            idempotencyKey=KEY,
        ),
        endpoint_ref="journal-journey", agent_id=None, agent_name=None,
        target_metadata={},
    )
    return store, row


async def publish(store, row, start, count):
    events = [{
        "sequence": index + 1, "eventType": "response.delta",
        "textPreview": f"token-{index}", "deduplicationKey": f"event-{index}",
        "metadata": {"reconciliation": {"postDispatchRawEventIndex": index}},
        "artifactRefs": {"providerOutput": "artifact:provider-output"} if index == 1 else {},
    } for index in range(start, start + count)]
    refs = []
    async with async_session_maker() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        for kind in ("raw", "normalized"):
            artifact, _ = await service.create(
                principal="service:omnigent-generic-host",
                link={"namespace": "default", "workflow_id": KEY,
                      "run_id": KEY, "link_type": f"runtime.omnigent.sse.{kind}"},
                metadata_json={
                    "name": f"runtime.omnigent.sse.{kind}.{start:08d}.{count:08d}.jsonl",
                    "correlation_id": KEY,
                },
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="service:omnigent-generic-host",
                payload="".join(json.dumps(
                    {**event, "artifactRef": f"artifact:{artifact.artifact_id}"}
                    if kind == "normalized" else event
                ) + "\n" for event in events).encode(),
                content_type="application/x-ndjson",
            )
            refs.append(f"artifact:{artifact.artifact_id}")
    await store.attach_active_journal_refs(
        row.bridge_session_id, raw_ref=refs[0], normalized_ref=refs[1], new_chunk=True,
    )
    await store.append_events(row.bridge_session_id, [
        {**event, "artifactRef": refs[1]} for event in events
    ])


async def writer(resume):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store, row = await bridge()
    if not (STATE / "seeded.json").exists():
        await store.attach_session(KEY, "provider-session-original")
        await store.mark_prepared(KEY, digest="a" * 64, marker="original-intent")
        await store.mark_posting(KEY)
        row = await store.mark_posted(KEY, item_id="original-first-message")
        await publish(store, row, 0, 2)
        await publish(store, row, 2, 1)
        record("seeded.json", {
            "bridge": row.bridge_session_id, "status": row.status,
            "firstMessage": row.first_message_state,
            "firstItem": row.first_message_item_id,
            "firstDigest": row.first_message_digest,
            "idempotencyKey": row.idempotency_key,
        })
    if resume and not (STATE / "resumed.json").exists():
        await publish(store, row, 3, 1)
        record("resumed.json", {"events": 4})
    while True:
        await asyncio.sleep(1)


class LegacyArtifactRepository(TemporalArtifactRepository):
    @staticmethod
    def _active_journal_reference_exists(artifact_id):
        # Retained old-reader predicate: only canonical refs protect blobs;
        # sealed chunk metadata is deliberately unknown to this consumer.
        return exists().where(
            OmnigentBridgeSession.status.not_in({"completed", "failed", "canceled", "timed_out"}),
            or_(
                OmnigentBridgeSession.raw_events_ref == artifact_id,
                OmnigentBridgeSession.raw_events_ref == "artifact:" + artifact_id,
                OmnigentBridgeSession.normalized_events_ref == artifact_id,
                OmnigentBridgeSession.normalized_events_ref == "artifact:" + artifact_id,
            ),
        )


async def sweep(legacy):
    # Start after the real writer has committed its seed transaction.
    while not (STATE / "seeded.json").exists():
        await asyncio.sleep(0.2)
    repository = LegacyArtifactRepository if legacy else TemporalArtifactRepository
    while True:
        async with async_session_maker() as session:
            for artifact in await session.scalars(select(TemporalArtifact)):
                artifact.expires_at = datetime.now(UTC) - timedelta(days=1)
            await session.commit()
            receipt = await TemporalArtifactService(repository(session)).sweep_lifecycle(
                principal="service:storage-maintenance"
            )
        record("legacy-swept.json" if legacy else "swept.json", {
            "softDeleted": receipt.soft_deleted_count,
            "hardDeleted": receipt.hard_deleted_count,
        })
        await asyncio.sleep(1)


async def legacy_reader():
    initial = json.loads((STATE / "seeded.json").read_text())
    store = OmnigentBridgeSessionStore(async_session_maker)
    while True:
        row = await store.get_bridge_session(initial["bridge"])
        assert row.status == initial["status"]
        assert row.first_message_state == initial["firstMessage"]
        assert row.first_message_item_id == initial["firstItem"]
        assert row.first_message_digest == initial["firstDigest"]
        assert row.idempotency_key == initial["idempotencyKey"]
        restored = []
        async with async_session_maker() as session:
            service = TemporalArtifactService(LegacyArtifactRepository(session))
            for ref in (row.raw_events_ref, row.normalized_events_ref):
                artifact = await service._repository.get_artifact(ref.removeprefix("artifact:"))
                assert str(artifact.status.value) == "complete"
                events = [json.loads(line) for line in service._store.read_bytes(artifact.storage_key).splitlines()]
                assert [event["textPreview"] for event in events] == [f"token-{i}" for i in range(4)]
                assert [event["metadata"]["reconciliation"]["postDispatchRawEventIndex"] for event in events] == list(range(4))
                restored.append(events)
        # The old decoder leaves artifactRef untouched. A compacted JSONL
        # must not resurrect the source chunk locator the old sweeper deletes.
        assert [event.get("artifactRef") for event in restored[1]] == [
            None, "artifact:provider-output", None, None,
        ]
        assert all("artifactRef" not in event for event in restored[0])
        indexed = await store.list_events(row.bridge_session_id)
        assert [event.artifact_ref for event in indexed] == [row.normalized_events_ref] * 4
        record("legacy-read.json", {"events": 4, "status": row.status,
                                    "providerRefsPreserved": True})
        await asyncio.sleep(0.5)


async def main():
    mode = sys.argv[1]
    try:
        if mode in ("writer", "writer-resume"):
            await writer(mode == "writer-resume")
        elif mode in ("sweeper", "legacy-sweeper"):
            await sweep(mode == "legacy-sweeper")
        elif mode == "legacy-reader":
            await legacy_reader()
        else:
            raise ValueError(mode)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
