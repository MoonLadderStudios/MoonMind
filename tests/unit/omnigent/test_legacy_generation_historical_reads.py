"""Historical reads and truthful provenance for every retained generation.

Source issue: MoonLadderStudios/MoonMind#3835 (required work section 5).

``tests/unit/omnigent/test_direct_compat_historical_reads.py`` (#3518) already
proves persisted **direct Codex** sessions stay readable through the real
Workflow Detail read path. This module covers the generations #3835 adds to that
obligation:

* direct **Claude** sessions,
* ``codex-profile-bound@1`` executions, and
* the invariant that no generation is relabeled as generic on read.

Each test exercises the production read path (durable journal query followed by
the deterministic row -> chat-event projection) with no adapter, worker,
activity, or live session, and additionally with the legacy launch modules made
unimportable — which is the state the repository is in once launch code is
disabled or removed.
"""

from __future__ import annotations

import builtins

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

CLAUDE_COMPAT_PROFILE = "moonmind.claude_direct_compat.v1"
PROFILE_BOUND_REALIZER = "codex-profile-bound@1"
GENERIC_REALIZER = "generic-omnigent-host@1"

# The launch modules a removal stage eventually deletes. Historical reads must
# not depend on any of them.
LEGACY_LAUNCH_MODULES = (
    "moonmind.omnigent.oauth_host_runtime",
    "moonmind.omnigent.profile_bound_execution",
    "moonmind.workflows.temporal.runtime.codex_session_runtime",
    "moonmind.workflows.temporal.runtime.strategies.claude_code",
)


@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentBridgeSessionStore(session_maker)
    await engine.dispose()


@pytest.fixture()
def launch_code_unavailable(monkeypatch: pytest.MonkeyPatch):
    """Make every legacy launch module unimportable for the duration of a test."""

    real_import = builtins.__import__

    def _blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name in LEGACY_LAUNCH_MODULES:
            raise ModuleNotFoundError(f"legacy launch module {name} has been removed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    yield


def _request(
    idempotency_key: str, *, agent_id: str, correlation_id: str
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId=agent_id,
        correlationId=correlation_id,
        idempotencyKey=idempotency_key,
    )


def _event(
    payload: dict[str, object],
    *,
    request: AgentExecutionRequest,
    session_id: str,
    bridge_session_id: str,
    sequence: int,
    provenance: dict[str, object],
) -> dict[str, object]:
    """Build a normalized v1 event and stamp truthful runtime provenance.

    This mirrors what the compatibility producers do on the write path: the
    canonical bridge event is built first, then ``metadata.moonmind`` records
    which generation actually produced it.
    """

    event = build_omnigent_bridge_event(
        payload=payload,
        sequence=sequence,
        request=request,
        omnigent_session_id=session_id,
        bridge_session_id=bridge_session_id,
    ).event
    event["metadata"]["moonmind"].update(provenance)
    return event


async def _seed_session(
    store: OmnigentBridgeSessionStore,
    *,
    idempotency_key: str,
    agent_id: str,
    agent_name: str,
    correlation_id: str,
    endpoint_ref: str,
    session_id: str,
    target_metadata: dict[str, object],
    provenance: dict[str, object],
    summary: str,
) -> tuple[str, AgentExecutionRequest]:
    request = _request(
        idempotency_key, agent_id=agent_id, correlation_id=correlation_id
    )
    row = await store.get_or_create(
        request=request,
        endpoint_ref=endpoint_ref,
        agent_id=agent_id,
        agent_name=agent_name,
        target_metadata=target_metadata,
    )
    bridge_session_id = row.bridge_session_id
    await store.append_events(
        bridge_session_id,
        [
            _event(
                {
                    "type": "session.started",
                    "status": "running",
                    "data": {"managedSessionWorkflowId": correlation_id},
                },
                request=request,
                session_id=session_id,
                bridge_session_id=bridge_session_id,
                sequence=1,
                provenance=provenance,
            ),
            _event(
                {
                    "type": "response.output",
                    "status": "running",
                    "text": "Reviewed the repository and drafted a fix.",
                },
                request=request,
                session_id=session_id,
                bridge_session_id=bridge_session_id,
                sequence=2,
                provenance=provenance,
            ),
            _event(
                {"type": "response.completed", "status": "completed"},
                request=request,
                session_id=session_id,
                bridge_session_id=bridge_session_id,
                sequence=3,
                provenance=provenance,
            ),
        ],
    )
    await store.mark_terminal(
        request.idempotency_key,
        status="completed",
        terminal_refs={"summary": summary},
    )
    return bridge_session_id, request


async def _seed_direct_claude(store: OmnigentBridgeSessionStore) -> str:
    bridge_session_id, _ = await _seed_session(
        store,
        idempotency_key="claude-direct-1",
        agent_id="claude_code",
        agent_name="Claude Code",
        correlation_id="mm:wf-claude-direct",
        endpoint_ref="direct-claude-compat",
        session_id="claude-session-11",
        target_metadata={
            "hostType": "managed",
            "workspace": "MoonLadderStudios/MoonMind",
            "compatibilityProfile": CLAUDE_COMPAT_PROFILE,
            "producer": "direct_claude_managed_session",
            "temporaryMigrationPath": True,
        },
        provenance={
            "source": "claude_direct_compat",
            "compatibilityProfile": CLAUDE_COMPAT_PROFILE,
            "directManagedSessionId": "claude-session-11",
        },
        summary="Direct Claude compatibility run completed.",
    )
    return bridge_session_id


async def _seed_profile_bound(store: OmnigentBridgeSessionStore) -> str:
    bridge_session_id, _ = await _seed_session(
        store,
        idempotency_key="profile-bound-1",
        agent_id="codex_cli",
        agent_name="Codex CLI",
        correlation_id="mm:wf-profile-bound",
        endpoint_ref="omnigent-codex",
        session_id="codex-session-99",
        target_metadata={
            "hostType": "omnigent",
            "workspace": "MoonLadderStudios/MoonMind",
            "executionRealizerRef": PROFILE_BOUND_REALIZER,
        },
        provenance={
            "source": "omnigent",
            "executionRealizerRef": PROFILE_BOUND_REALIZER,
        },
        summary="Profile-bound Codex execution completed.",
    )
    return bridge_session_id


@pytest.mark.asyncio
async def test_direct_claude_session_reads_without_live_runtime(
    store, launch_code_unavailable
) -> None:
    bridge_session_id = await _seed_direct_claude(store)

    page = await store.list_event_page(bridge_session_id, after=0, limit=100)
    projected = [_bridge_event_payload(item) for item in page.rows]

    assert [event["kind"] for event in projected] == [
        "session_started",
        "assistant_message",
        "response_completed",
    ]
    for event in projected:
        moonmind = event["metadata"]["moonmind"]
        # Historical direct Claude work stays labeled direct. It is never
        # rewritten to look like an Omnigent or generic execution.
        assert moonmind["source"] == "claude_direct_compat"
        assert moonmind["source"] != "omnigent"
        assert moonmind.get("executionRealizerRef") != GENERIC_REALIZER
        assert "compatibilityProfile" in moonmind

    session_row = await store.get_bridge_session(bridge_session_id)
    envelope = _terminal_envelope(session_row)
    assert envelope is not None
    assert envelope.status == "completed"
    assert envelope.summary == "Direct Claude compatibility run completed."


@pytest.mark.asyncio
async def test_profile_bound_execution_keeps_its_realizer_label(
    store, launch_code_unavailable
) -> None:
    bridge_session_id = await _seed_profile_bound(store)

    page = await store.list_event_page(bridge_session_id, after=0, limit=100)
    projected = [_bridge_event_payload(item) for item in page.rows]
    assert projected

    for event in projected:
        moonmind = event["metadata"]["moonmind"]
        # Historical ``codex-profile-bound@1`` work stays labeled with that
        # realizer; the past is never made to look generic.
        assert moonmind["executionRealizerRef"] == PROFILE_BOUND_REALIZER
        assert moonmind["executionRealizerRef"] != GENERIC_REALIZER

    session_row = await store.get_bridge_session(bridge_session_id)
    envelope = _terminal_envelope(session_row)
    assert envelope is not None
    assert envelope.status == "completed"


@pytest.mark.asyncio
async def test_no_generation_is_relabeled_as_generic_on_read(store) -> None:
    """Every retained generation keeps its own provenance in one read model."""

    claude_id = await _seed_direct_claude(store)
    profile_bound_id = await _seed_profile_bound(store)

    observed: dict[str, set[str]] = {}
    for bridge_session_id in (claude_id, profile_bound_id):
        page = await store.list_event_page(bridge_session_id, after=0, limit=100)
        for row in page.rows:
            payload = _bridge_event_payload(row)
            moonmind = payload["metadata"]["moonmind"]
            observed.setdefault(bridge_session_id, set()).add(
                str(moonmind.get("executionRealizerRef") or moonmind.get("source"))
            )
            # The wire transport tag is the shared bridge journal; the runtime
            # provenance underneath stays distinct per generation.
            assert payload["metadata"]["source"] == "omnigent_bridge"

    assert observed[claude_id] == {"claude_direct_compat"}
    assert observed[profile_bound_id] == {PROFILE_BOUND_REALIZER}


@pytest.mark.asyncio
async def test_historical_projection_protects_provider_and_credential_authority(
    store,
) -> None:
    """Provider-session, host, credential, and endpoint authority stays protected."""

    request = _request(
        "claude-direct-secret", agent_id="claude_code", correlation_id="mm:wf-secret"
    )
    row = await store.get_or_create(
        request=request,
        endpoint_ref="direct-claude-compat",
        agent_id="claude_code",
        agent_name="Claude Code",
        target_metadata={
            "hostType": "managed",
            "compatibilityProfile": CLAUDE_COMPAT_PROFILE,
        },
    )
    bridge_session_id = row.bridge_session_id
    await store.append_events(
        bridge_session_id,
        [
            _event(
                {
                    "type": "response.output",
                    "status": "running",
                    "text": "authorization: Bearer sk-ant-api03-not-a-real-secret",
                    "data": {
                        "apiKey": "sk-ant-api03-not-a-real-secret",
                        "hostEndpoint": "http://omnigent-host-claude:8080/internal",
                    },
                },
                request=request,
                session_id="claude-session-secret",
                bridge_session_id=bridge_session_id,
                sequence=1,
                provenance={
                    "source": "claude_direct_compat",
                    "compatibilityProfile": CLAUDE_COMPAT_PROFILE,
                },
            )
        ],
    )

    page = await store.list_event_page(bridge_session_id, after=0, limit=100)
    payload = _bridge_event_payload(page.rows[0])
    serialized = repr(payload)
    assert "sk-ant-api03-not-a-real-secret" not in serialized
    # Provenance is still truthful even though the secret material is scrubbed.
    assert payload["metadata"]["moonmind"]["source"] == "claude_direct_compat"


@pytest.mark.asyncio
async def test_reads_do_not_import_legacy_launch_modules(
    store, launch_code_unavailable
) -> None:
    """The read path must not pull in launch code that a removal stage deletes."""

    bridge_session_id = await _seed_profile_bound(store)
    page = await store.list_event_page(bridge_session_id, after=0, limit=100)
    assert [_bridge_event_payload(item)["kind"] for item in page.rows] == [
        "session_started",
        "assistant_message",
        "response_completed",
    ]
    session_row = await store.get_bridge_session(bridge_session_id)
    assert _terminal_envelope(session_row) is not None
