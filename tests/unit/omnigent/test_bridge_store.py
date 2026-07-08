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
    FIRST_MESSAGE_TERMINAL,
    STATUS_ACTIVE,
    STATUS_CREATING,
    STATUS_DECLARED,
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
async def test_mark_terminal_coalesces_and_preserves_event_stream(store):
    request = _request()
    created = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
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
        {"eventType": "response.failed", "normalizedStatus": "timed_out", "sequence": 5},
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
    assert [e.sequence for e in events] == [1, 2, 3, 4, 5]
    assert [e.normalized_status for e in events] == [
        "created",
        "running",
        "awaiting_approval",
        "running",
        "timed_out",
    ]
    assert all(e.direction == "host_to_moonmind" for e in events)
    assert events[0].event_type == "session.created"


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
        {"eventType": "response.completed", "normalizedStatus": "completed", "sequence": 2},
    ]

    await store.mark_terminal(
        "idem-1", status="completed", terminal_refs={"outputRefs": ["art"]}, events=stream
    )
    # Retry: same key, same events.
    await store.mark_terminal(
        "idem-1", status="completed", terminal_refs={"outputRefs": ["art"]}, events=stream
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
