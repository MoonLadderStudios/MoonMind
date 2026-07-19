"""Unit tests for the canonical Omnigent bridge session store (MM-1152).

Covers the OmnigentBridge design §7.1/§7.2 and §17 requirements:
- lifecycle-to-normalized status coalescence,
- terminal-status failure classification (``timed_out`` kept distinct),
- the non-lossy per-event normalized status stream in the event index,
- unique idempotency_key session identity.
Source design traceability: OmnigentBridge.md (MM-1152, source issue MM-1140).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentBridgeSession
from moonmind.omnigent.bridge_store import (
    BRIDGE_EVENT_JOURNAL_KEY,
    FIRST_MESSAGE_TERMINAL,
    SESSION_CREATED_EVENT_TYPE,
    STATUS_ACTIVE,
    STATUS_CREATING,
    STATUS_DECLARED,
    BridgeProjectionAmbiguousError,
    OmnigentBridgeSessionStore,
    OmnigentDigestMismatchError,
    OmnigentIdempotencyError,
    bridge_failure_class,
    coalesce_bridge_status,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRuntimeStepExecutionLaunch,
)


@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentBridgeSessionStore(session_maker)
    await engine.dispose()


@pytest.mark.asyncio
async def test_active_host_protocol_modes_reports_ownership_and_unknown(store) -> None:
    proxy = await store.get_or_create(
        request=_request("proxy"), endpoint_ref="endpoint", agent_id=None,
        agent_name=None, target_metadata={"hostProtocolMode": "proxy"},
    )
    await store.get_or_create(
        request=_request("legacy"), endpoint_ref="endpoint", agent_id=None,
        agent_name=None, target_metadata={},
    )
    await store.get_or_create(
        request=_request("terminal"), endpoint_ref="endpoint", agent_id=None,
        agent_name=None, target_metadata={"hostProtocolMode": "embedded"},
    )
    await store.record_lifecycle_event(
        "terminal", event_type="terminal", status="completed",
    )

    assert await store.active_host_protocol_modes() == {"proxy": 1, "unknown": 1}


def _request(idempotency_key: str = "idem-1", *, with_step: bool = False):
    step = None
    if with_step:
        step = AgentRuntimeStepExecutionLaunch(
            workflowId="mm:wf-1",
            runId="run-7",
            logicalStepId="implement",
            executionOrdinal=1,
            stepExecutionId="mm:wf-1:run-7:implement:execution:1",
            runtimeContextPolicy="fresh_agent_run",
        )
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey=idempotency_key,
        stepExecution=step,
    )


# --- coalescence (§7.1) -----------------------------------------------------


@pytest.mark.parametrize(
    "normalized",
    [
        "created",
        "launching",
        "provisioning",
        "running",
        "waiting",
        "idle",
        "awaiting_approval",
        "intervention_requested",
    ],
)
def test_non_terminal_statuses_coalesce_to_active(normalized):
    assert coalesce_bridge_status(normalized) == STATUS_ACTIVE


@pytest.mark.parametrize(
    "terminal",
    ["completed", "failed", "canceled", "timed_out"],
)
def test_terminal_statuses_pass_through(terminal):
    assert coalesce_bridge_status(terminal) == terminal


def test_provider_aliases_normalized():
    assert coalesce_bridge_status("cancelled") == "canceled"
    assert coalesce_bridge_status("timeout") == "timed_out"


def test_lifecycle_statuses_pass_through():
    assert coalesce_bridge_status(STATUS_DECLARED) == STATUS_DECLARED
    assert coalesce_bridge_status(STATUS_CREATING) == STATUS_CREATING
    assert coalesce_bridge_status(STATUS_ACTIVE) == STATUS_ACTIVE


def test_unknown_status_fails_fast():
    with pytest.raises(ValueError):
        coalesce_bridge_status("banana")


def test_timed_out_is_distinct_system_error():
    # timed_out is never collapsed into failed and maps to system_error (§17).
    assert coalesce_bridge_status("timed_out") == "timed_out"
    assert bridge_failure_class("timed_out") == "system_error"
    assert bridge_failure_class("canceled") == "system_error"
    assert bridge_failure_class("failed") == "execution_error"
    assert bridge_failure_class("completed") is None


# --- store lifecycle --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_and_declared(store):
    request = _request(with_step=True)
    row = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    assert row.status == STATUS_DECLARED
    assert row.provider == "omnigent"
    assert row.compatibility_profile == "omnigent.server.v1"
    assert row.moonmind_workflow_id == "mm:wf-1"
    assert row.moonmind_run_id == "run-7"
    assert row.moonmind_agent_run_id == "run-7"
    assert row.step_execution_id == "mm:wf-1:run-7:implement:execution:1"
    assert row.host_type == "managed"
    assert row.workspace == "https://x/y#main"
    assert row.bridge_session_id.startswith("brs_")

    again = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    assert again.bridge_session_id == row.bridge_session_id


@pytest.mark.asyncio
async def test_get_or_create_persists_binding_identity_override(store):
    # The Session API Facade holds a verified workflow id out-of-band and
    # synthesizes a request with no step_execution (correlation id != workflow
    # id). The explicit override must be persisted, not the correlation id.
    request = _request()  # correlationId="corr-1", no step_execution
    row = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-verified",
        agent_run_id="ar-verified",
    )
    assert row.moonmind_workflow_id == "mm:wf-verified"
    assert row.moonmind_agent_run_id == "ar-verified"


@pytest.mark.asyncio
async def test_get_or_create_without_override_derives_from_request(store):
    # Managed-execution path behavior is preserved when no override is given.
    request = _request()  # no step_execution -> falls back to correlation id
    row = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
    )
    assert row.moonmind_workflow_id == "corr-1"
    assert row.moonmind_agent_run_id == "corr-1"


@pytest.mark.asyncio
async def test_get_existing_returns_none_then_row(store):
    request = _request()
    assert await store.get_existing(request.idempotency_key) is None
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-1",
    )
    row = await store.get_existing(request.idempotency_key)
    assert row is not None
    assert row.moonmind_workflow_id == "mm:wf-1"
    assert row.omnigent_agent_id == "ag_1"


@pytest.mark.asyncio
async def test_get_session_owner_resolves_by_session_id(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-owner",
    )
    await store.attach_session(request.idempotency_key, "sess-abc")

    owner = await store.get_session_owner("sess-abc")
    assert owner is not None
    assert owner.workflow_id == "mm:wf-owner"
    assert owner.agent_run_id == "ar-owner"

    assert await store.get_session_owner("sess-missing") is None
    assert await store.get_session_owner("") is None


@pytest.mark.asyncio
async def test_resolve_projection_session_falls_back_after_explicit_key_miss(store):
    first = await store.get_or_create(
        request=_request("idem-first"),
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-first",
    )
    second = await store.get_or_create(
        request=_request("idem-second"),
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-second",
    )
    await store.mark_posting("idem-second")

    by_key = await store.resolve_projection_session(idempotency_key="idem-first")
    assert by_key is not None
    assert by_key.bridge_session_id == first.bridge_session_id

    missed_key = await store.resolve_projection_session(
        workflow_id="mm:wf-owner",
        idempotency_key="stale-or-execution-key",
    )
    assert missed_key is not None
    assert missed_key.bridge_session_id == second.bridge_session_id

    latest = await store.resolve_projection_session(workflow_id="mm:wf-owner")
    assert latest is not None
    assert latest.bridge_session_id == second.bridge_session_id

    scoped = await store.resolve_projection_session(
        workflow_id="mm:wf-owner",
        agent_run_id="ar-first",
    )
    assert scoped is not None
    assert scoped.bridge_session_id == first.bridge_session_id


@pytest.mark.asyncio
async def test_resolve_projection_session_binding_precedes_idempotency(store):
    first = await store.get_or_create(
        request=_request("idem-first"),
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-first",
    )
    second = await store.get_or_create(
        request=_request("idem-second"),
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent Two",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-second",
    )
    resolved = await store.resolve_projection_session(
        workflow_id="mm:wf-owner",
        agent_run_id="ar-first",
        idempotency_key="idem-second",
    )
    assert resolved is not None
    assert resolved.bridge_session_id == first.bridge_session_id
    assert resolved.bridge_session_id != second.bridge_session_id


@pytest.mark.asyncio
async def test_resolve_projection_session_rejects_ambiguous_explicit_binding(store):
    for key in ("idem-first", "idem-second"):
        await store.get_or_create(
            request=_request(key),
            endpoint_ref="default",
            agent_id="ag_1",
            agent_name="Agent One",
            target_metadata={"hostType": "managed"},
            workflow_id="mm:wf-owner",
            agent_run_id="ar-shared",
        )
    with pytest.raises(BridgeProjectionAmbiguousError):
        await store.resolve_projection_session(
            workflow_id="mm:wf-owner", agent_run_id="ar-shared"
        )


@pytest.mark.asyncio
async def test_attach_and_first_message_transitions(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    attached = await store.attach_session("idem-1", "sess-1")
    assert attached.omnigent_session_id == "sess-1"
    assert attached.status == STATUS_CREATING

    await store.mark_prepared("idem-1", digest="sha256:abc", marker="marker")
    posting = await store.mark_posting("idem-1")
    assert posting.status == STATUS_ACTIVE
    assert posting.first_message_state == "posting"

    posted = await store.mark_posted(
        "idem-1", response={"pending_id": "pnd-1", "item_id": "itm-1"}
    )
    assert posted.first_message_state == "posted"
    assert posted.first_message_pending_id == "pnd-1"
    assert posted.first_message_item_id == "itm-1"


@pytest.mark.asyncio
async def test_digest_mismatch_fails_fast(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.mark_prepared("idem-1", digest="sha256:first", marker="m")
    with pytest.raises(OmnigentDigestMismatchError):
        await store.mark_prepared("idem-1", digest="sha256:second", marker="m")


@pytest.mark.asyncio
async def test_attach_conflicting_session_fails(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.attach_session("idem-1", "sess-1")
    with pytest.raises(OmnigentIdempotencyError):
        await store.attach_session("idem-1", "sess-2")


@pytest.mark.asyncio
async def test_missing_row_requires_get_or_create(store):
    with pytest.raises(OmnigentIdempotencyError):
        await store.mark_posting("never-created")


# --- terminal coalescence + event index (§7.1/§7.2) -------------------------


@pytest.mark.asyncio
async def test_terminal_lifecycle_event_projects_session_terminal_state(store):
    request = _request()
    row = await store.get_or_create(
        request=request,
        endpoint_ref="pending",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.record_lifecycle_event(
        request.idempotency_key,
        event_type="terminal",
        status="failed",
        event_identity="idem-1:attempt:1:terminal:failed",
    )

    projected = await store.get_bridge_session(row.bridge_session_id)
    assert projected is not None
    assert projected.status == "failed"
    assert projected.first_message_state == FIRST_MESSAGE_TERMINAL


@pytest.mark.asyncio
async def test_mark_terminal_coalesces_and_preserves_event_stream(store):
    request = _request()
    created = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.record_lifecycle_event(
        request.idempotency_key,
        event_type="profile_resolution",
        status="completed",
        event_identity="idem-1:attempt:1:profile_resolution:completed",
    )

    # Full non-lossy normalized status stream, including a terminal timeout.
    stream = [
        {"eventType": "session.created", "normalizedStatus": "created", "sequence": 1},
        {"eventType": "response.delta", "normalizedStatus": "running", "sequence": 2},
        {
            "eventType": "response.elicitation_request",
            "normalizedStatus": "awaiting_approval",
            "sequence": 3,
        },
        {"eventType": "response.delta", "normalizedStatus": "running", "sequence": 4},
        {
            "eventType": "response.failed",
            "normalizedStatus": "timed_out",
            "sequence": 5,
        },
    ]

    terminal = await store.mark_terminal(
        "idem-1",
        status="timed_out",
        terminal_refs={"outputRefs": ["art_final"]},
        events=stream,
    )
    # Session status keeps the terminal value distinct (not collapsed to failed).
    assert terminal.status == "timed_out"
    assert terminal.first_message_state == FIRST_MESSAGE_TERMINAL
    assert terminal.terminal_refs == {"outputRefs": ["art_final"]}

    events = await store.list_events(created.bridge_session_id)
    # The event index preserves the full, non-lossy per-event normalized stream,
    # including the non-terminal statuses coalesced away at the session level.
    assert [e.sequence for e in events] == [1, 2, 3, 4, 5, 6]
    assert events[0].event_type == "lifecycle.profile_resolution"
    assert [e.normalized_status for e in events[1:]] == [
        "created",
        "running",
        "awaiting_approval",
        "running",
        "timed_out",
    ]
    assert all(e.direction == "host_to_moonmind" for e in events[1:])
    assert events[1].event_type == "session.created"


@pytest.mark.asyncio
async def test_mark_terminal_event_indexing_is_idempotent_on_retry(store):
    # A Temporal activity retry can reattach to the durable session and call
    # mark_terminal again with the same idempotency key. The event index must not
    # accumulate duplicate sequences (§7.2).
    request = _request()
    created = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    stream = [
        {"eventType": "session.created", "normalizedStatus": "created", "sequence": 1},
        {
            "eventType": "response.completed",
            "normalizedStatus": "completed",
            "sequence": 2,
        },
    ]

    await store.mark_terminal(
        "idem-1",
        status="completed",
        terminal_refs={"outputRefs": ["art"]},
        events=stream,
    )
    # Retry: same key, same events.
    await store.mark_terminal(
        "idem-1",
        status="completed",
        terminal_refs={"outputRefs": ["art"]},
        events=stream,
    )

    events = await store.list_events(created.bridge_session_id)
    assert [e.sequence for e in events] == [1, 2]
    assert [e.normalized_status for e in events] == ["created", "completed"]


@pytest.mark.asyncio
async def test_mark_terminal_populates_canonical_ref_columns(store):
    # The dedicated first-class evidence ref columns must be populated from the
    # capture bundle refs (§7.1) instead of remaining NULL for new runs.
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    terminal_refs = {
        "outputRefs": ["art_final", "art_norm", "art_manifest"],
        "diagnosticsRef": "art_diag",
        "metadataRefs": {
            "rawSseStreamRef": "art_raw",
            "normalizedEventStreamRef": "art_norm",
            "initialSnapshotRef": "art_initial",
            "finalSnapshotRef": "art_final",
            "captureManifestRef": "art_manifest",
            "externalStateRef": "art_external",
        },
    }

    terminal = await store.mark_terminal(
        "idem-1", status="completed", terminal_refs=terminal_refs
    )

    assert terminal.raw_events_ref == "art_raw"
    assert terminal.normalized_events_ref == "art_norm"
    assert terminal.initial_snapshot_ref == "art_initial"
    assert terminal.final_snapshot_ref == "art_final"
    assert terminal.capture_manifest_ref == "art_manifest"
    assert terminal.external_state_ref == "art_external"
    assert terminal.diagnostics_ref == "art_diag"
    # The JSON terminal_refs blob is preserved unchanged alongside the columns.
    assert terminal.terminal_refs == terminal_refs


@pytest.mark.asyncio
async def test_unique_idempotency_key_enforced(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    # A second row with the same idempotency_key but a different primary key must
    # be rejected by the unique constraint.
    async with store._session_factory() as session:  # noqa: SLF001 - constraint test
        session.add(
            OmnigentBridgeSession(
                bridge_session_id="brs_dupe",
                provider="omnigent",
                compatibility_profile="omnigent.server.v1",
                moonmind_workflow_id="corr-1",
                moonmind_agent_run_id="corr-1",
                idempotency_key="idem-1",
                omnigent_endpoint_ref="default",
                host_type="managed",
                status=STATUS_DECLARED,
                first_message_state="not_prepared",
                terminal_refs={},
                metadata_={},
            )
        )
        with pytest.raises(Exception):
            await session.commit()


@pytest.mark.asyncio
async def test_append_events_allocates_monotonic_sequences_and_keeps_terminal_status(
    store,
):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )

    await store.append_events(
        row.bridge_session_id,
        [
            {"eventType": "response.delta", "normalizedStatus": "running"},
            {"eventType": "response.completed", "normalizedStatus": "completed"},
        ],
    )
    after_terminal = await store.get_bridge_session(row.bridge_session_id)
    assert after_terminal is not None
    assert after_terminal.status == "completed"

    await store.append_events(
        row.bridge_session_id,
        [{"eventType": "stream.done", "normalizedStatus": "running"}],
    )

    events = await store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2, 3]
    final = await store.get_bridge_session(row.bridge_session_id)
    assert final is not None
    assert final.status == "completed"


@pytest.mark.asyncio
async def test_append_events_deduplicates_replay_but_preserves_identical_distinct_deltas(store):
    row = await store.get_or_create(
        request=_request(), endpoint_ref="default", agent_id=None,
        agent_name=None, target_metadata={},
    )
    first = {
        "eventType": "response.delta", "normalizedStatus": "running",
        "textPreview": "same", "deduplicationKey": "cursor:7:abc",
    }
    second = {**first, "deduplicationKey": "cursor:8:abc"}

    assert len(await store.append_events(row.bridge_session_id, [first])) == 1
    assert await store.append_events(row.bridge_session_id, [first]) == []
    assert len(await store.append_events(row.bridge_session_id, [second])) == 1

    events = await store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.text_preview for event in events] == ["same", "same"]


@pytest.mark.asyncio
async def test_terminal_reconciliation_never_deletes_live_rows(store):
    row = await store.get_or_create(
        request=_request(), endpoint_ref="default", agent_id=None,
        agent_name=None, target_metadata={},
    )
    live = {
        "eventType": "response.delta", "normalizedStatus": "running",
        "deduplicationKey": "provider:event-1",
    }
    terminal = {
        "eventType": "response.completed", "normalizedStatus": "completed",
        "deduplicationKey": "provider:event-2",
    }
    await store.append_events(row.bridge_session_id, [live])
    await store.mark_terminal("idem-1", status="completed", events=[live, terminal])
    await store.mark_terminal("idem-1", status="completed", events=[live, terminal])

    events = await store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == [
        "response.delta", "response.completed",
    ]


@pytest.mark.asyncio
async def test_lifecycle_events_share_the_ordered_projection(store):
    row = await store.get_or_create(
        request=_request(), endpoint_ref="default", agent_id=None,
        agent_name=None, target_metadata={},
    )
    await store.record_lifecycle_event(
        "idem-1", event_type="profile.resolved", code="ready",
        summary="Profile authorized",
    )
    await store.append_events(
        row.bridge_session_id,
        [{"eventType": "response.delta", "normalizedStatus": "running",
          "deduplicationKey": "provider:event-1"}],
    )

    events = await store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == [
        "profile.resolved", "response.delta",
    ]


# --- session.created journal (MM-1155, §8.2 step 6) -------------------------


async def _seed_created_session(store) -> None:
    await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    await store.attach_session("idem-1", "sess-1")


@pytest.mark.asyncio
async def test_record_session_created_appends_event(store):
    await _seed_created_session(store)

    row = await store.record_session_created(
        "idem-1", session_id="sess-1", agent_id="agent-1", endpoint_ref="default"
    )

    journal = row.metadata_[BRIDGE_EVENT_JOURNAL_KEY]
    assert len(journal) == 1
    event = journal[0]
    assert event["type"] == SESSION_CREATED_EVENT_TYPE
    assert event["omnigentSessionId"] == "sess-1"
    assert event["omnigentAgentId"] == "agent-1"
    assert event["endpointRef"] == "default"
    assert event["sequence"] == 1
    assert event["timestamp"]
    # Existing session metadata is preserved alongside the journal.
    assert row.metadata_["hostType"] == "managed"


@pytest.mark.asyncio
async def test_record_session_created_is_idempotent(store):
    await _seed_created_session(store)

    await store.record_session_created("idem-1", session_id="sess-1")
    row = await store.record_session_created("idem-1", session_id="sess-1")

    assert len(row.metadata_[BRIDGE_EVENT_JOURNAL_KEY]) == 1


@pytest.mark.asyncio
async def test_record_session_created_persists_across_get_or_create(store):
    await _seed_created_session(store)
    await store.record_session_created("idem-1", session_id="sess-1")

    # A subsequent get_or_create (retry) must not clobber the journal.
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    assert BRIDGE_EVENT_JOURNAL_KEY in row.metadata_
    assert len(row.metadata_[BRIDGE_EVENT_JOURNAL_KEY]) == 1
