"""Historical-read compatibility for direct Codex sessions.

Source issue: MoonLadderStudios/MoonMind#3518 (required work #6, AC7).

The Codex-through-Omnigent cutover retires the *direct* launch runtime in
controlled stages while promising that persisted ``codex_direct_compat``
sessions stay readable in Workflow Detail without an active direct runtime and
without ever being relabeled as Omnigent sessions.  These tests exercise the
exact server read path used by the Workflow Detail projection
(:func:`OmnigentBridgeSessionStore.list_event_page` ->
:func:`_bridge_event_payload` / :func:`_terminal_envelope`) against events that
were persisted by the direct compatibility producer, with no adapter, worker,
activity, or live session involved.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.omnigent_bridge import (
    _bridge_event_payload,
    _terminal_envelope,
)
from api_service.db.models import Base
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

COMPAT_PROFILE = "moonmind.codex_direct_compat.v1"


@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentBridgeSessionStore(session_maker)
    await engine.dispose()


def _request(idempotency_key: str = "direct-compat-1") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="mm:wf-direct",
        idempotencyKey=idempotency_key,
    )


def _direct_event(
    payload: dict[str, object],
    *,
    request: AgentExecutionRequest,
    session_id: str,
    bridge_session_id: str,
) -> dict[str, object]:
    """Mirror the direct compatibility producer's provenance stamping.

    See ``TemporalAgentRuntimeActivities._append_direct_codex_bridge_events``:
    the normalized v1 event is built and then ``metadata.moonmind`` is updated
    with the truthful ``codex_direct_compat`` provenance.
    """

    event = build_omnigent_bridge_event(
        payload=payload,
        sequence=1,
        request=request,
        omnigent_session_id=session_id,
        bridge_session_id=bridge_session_id,
    ).event
    event["metadata"]["moonmind"].update(
        {
            "source": "codex_direct_compat",
            "compatibilityProfile": COMPAT_PROFILE,
            "directManagedSessionId": session_id,
        }
    )
    return event


@pytest.mark.asyncio
async def test_persisted_direct_compat_session_reads_without_live_runtime(store) -> None:
    request = _request()
    session_id = "codex-session-42"
    row = await store.get_or_create(
        request=request,
        endpoint_ref="direct-codex-compat",
        agent_id="codex_cli",
        agent_name="Codex CLI",
        target_metadata={
            "hostType": "managed",
            "workspace": "MoonLadderStudios/MoonMind",
            "compatibilityProfile": COMPAT_PROFILE,
            "producer": "direct_codex_managed_session",
            "temporaryMigrationPath": True,
        },
    )
    bridge_session_id = row.bridge_session_id

    events = [
        _direct_event(
            {
                "type": "session.started",
                "status": "running",
                "data": {"managedSessionWorkflowId": "mm:wf-direct"},
            },
            request=request,
            session_id=session_id,
            bridge_session_id=bridge_session_id,
        ),
        _direct_event(
            {
                "type": "response.output",
                "status": "running",
                "text": "Analyzed the repository and drafted a fix.",
            },
            request=request,
            session_id=session_id,
            bridge_session_id=bridge_session_id,
        ),
        _direct_event(
            {"type": "response.completed", "status": "completed"},
            request=request,
            session_id=session_id,
            bridge_session_id=bridge_session_id,
        ),
    ]
    await store.append_events(bridge_session_id, events)
    await store.mark_terminal(
        request.idempotency_key,
        status="completed",
        terminal_refs={"summary": "Direct compatibility run completed."},
    )

    # Exact Workflow Detail read path: a pure durable-journal query, then the
    # deterministic row -> chat-event projection.  No adapter/worker/session.
    page = await store.list_event_page(bridge_session_id, after=0, limit=100)
    projected = [_bridge_event_payload(item) for item in page.rows]
    kinds = [event["kind"] for event in projected]

    assert kinds == ["session_started", "assistant_message", "response_completed"]
    assert [event["sequence"] for event in projected] == sorted(
        event["sequence"] for event in projected
    )

    # Truthful provenance survives the read model: the runtime producer stays
    # ``codex_direct_compat`` and is never relabeled as an Omnigent session.
    # (The durable store applies the raw-event redactor on write, so the compat
    # marker key is carried but its value may be scrubbed; the authoritative
    # runtime provenance is ``moonmind.source``.)
    for event in projected:
        moonmind = event["metadata"]["moonmind"]
        assert moonmind["source"] == "codex_direct_compat"
        assert moonmind["source"] != "omnigent"
        assert "compatibilityProfile" in moonmind

    # The persisted terminal envelope resolves from durable columns alone.
    session_row = await store.get_bridge_session(bridge_session_id)
    envelope = _terminal_envelope(session_row)
    assert envelope is not None
    assert envelope.status == "completed"
    assert envelope.summary == "Direct compatibility run completed."
    assert page.latest_sequence == projected[-1]["sequence"]
    assert page.has_more is False


@pytest.mark.asyncio
async def test_direct_compat_read_model_matches_omnigent_transport_shape(store) -> None:
    """Direct compat rows project through the same chat schema as Omnigent.

    Historical reads must render identically to native Omnigent bridge events
    (same journal transport) while keeping distinct runtime provenance, so the
    Workflow Detail client needs no direct-runtime-specific decoder.
    """

    request = _request("direct-compat-2")
    session_id = "codex-session-7"
    row = await store.get_or_create(
        request=request,
        endpoint_ref="direct-codex-compat",
        agent_id="codex_cli",
        agent_name="Codex CLI",
        target_metadata={"hostType": "managed", "compatibilityProfile": COMPAT_PROFILE},
    )
    event = _direct_event(
        {"type": "response.output", "status": "running", "text": "hello"},
        request=request,
        session_id=session_id,
        bridge_session_id=row.bridge_session_id,
    )
    await store.append_events(row.bridge_session_id, [event])

    page = await store.list_event_page(row.bridge_session_id, after=0, limit=100)
    payload = _bridge_event_payload(page.rows[0])

    # The wire event exposes the canonical bridge transport tag at top level
    # (the event lives in the Omnigent bridge journal) while the truthful
    # runtime provenance stays nested and unmodified.
    assert payload["metadata"]["source"] == "omnigent_bridge"
    assert payload["metadata"]["moonmind"]["source"] == "codex_direct_compat"
    assert payload["kind"] == "assistant_message"
    assert payload["text"] == "hello"
