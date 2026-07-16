"""Hermetic fake-server coverage for the Omnigent bridge proxy (OB-§20.2).

MM-1155 (source: MM-1140): drive the proxy-mode Host Protocol Facade against a
fake stock Omnigent Server using the real HTTP client and the real STORY-002
store (SQLite). Verifies create/reuse/get, provider-session persistence before
first message, and durable ``session.created`` emission.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_service.db.models import OmnigentBridgeSession
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    BridgeSessionEventRequest,
    OmnigentBridgeError,
    OmnigentBridgeSessionProxy,
)
from moonmind.omnigent.bridge_store import (
    BRIDGE_EVENT_JOURNAL_KEY,
    SESSION_CREATED_EVENT_TYPE,
    OmnigentBridgeSessionStore,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from tests.helpers.omnigent_conformance import (
    FakeOmnigentServer,
    start_fake_omnigent_server,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture
async def fake_server():
    server = FakeOmnigentServer()
    running = await start_fake_omnigent_server(server)
    try:
        yield server, running.base_url
    finally:
        await running.runner.cleanup()


@pytest_asyncio.fixture
async def store():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentBridgeSession.__table__.create)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentBridgeSessionStore(session_maker), session_maker
    finally:
        await engine.dispose()


async def _row(session_maker, idempotency_key: str) -> OmnigentBridgeSession:
    async with session_maker() as session:
        result = await session.execute(
            select(OmnigentBridgeSession).where(
                OmnigentBridgeSession.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one()


def _proxy(
    run_store: OmnigentBridgeSessionStore, base_url: str
) -> OmnigentBridgeSessionProxy:
    return OmnigentBridgeSessionProxy(
        run_store=run_store,
        client=OmnigentHttpClient(base_url=base_url),
        default_agent_name="codex",
    )


async def test_bridge_proxy_create_get_and_journal(fake_server, store) -> None:
    server, base_url = fake_server
    run_store, session_maker = store
    proxy = _proxy(run_store, base_url)
    binding = BridgePrincipalBinding(
        workflow_id="mm:w1",
        correlation_id="corr-1",
        idempotency_key="idem-1",
        agent_run_id="ar-1",
    )
    request = BridgeSessionCreateRequest(
        agent_id="agent-1",
        host_type="managed",
        workspace="https://github.com/org/repo#main",
    )

    created = await proxy.create_session(request=request, binding=binding)

    assert created["id"] == "session-1"
    assert created["status"] == "running"
    assert created["moonmind"]["reused"] is False
    assert server.create_payloads[0]["idempotency_key"] == "idem-1"
    assert (
        server.create_payloads[0]["labels"]["moonmind.issue"]
        == "MoonLadderStudios/MoonMind#3361"
    )

    # Provider session id persisted + session.created recorded on the row.
    row = await _row(session_maker, "idem-1")
    assert row.omnigent_session_id == "session-1"
    journal = row.metadata_[BRIDGE_EVENT_JOURNAL_KEY]
    assert [entry["type"] for entry in journal] == [SESSION_CREATED_EVENT_TYPE]

    # GET returns an Omnigent-shaped snapshot.
    snapshot = await proxy.get_session("session-1")
    assert snapshot["id"] == "session-1"
    assert snapshot["summary"] == "fake Omnigent running"

    # Agent catalog proxied.
    agents = await proxy.list_agents()
    assert agents == [{"id": "agent-1", "name": "codex"}]

    # Retry under the same idempotency key reuses the session (no second create).
    reused = await proxy.create_session(request=request, binding=binding)
    assert reused["id"] == "session-1"
    assert reused["moonmind"]["reused"] is True
    assert len(server.session_ids) == 1
    journal_after = (await _row(session_maker, "idem-1")).metadata_[
        BRIDGE_EVENT_JOURNAL_KEY
    ]
    assert len(journal_after) == 1


async def test_bridge_proxy_complete_control_and_resource_matrix(fake_server, store) -> None:
    server, base_url = fake_server
    run_store, _ = store
    proxy = _proxy(run_store, base_url)
    binding = BridgePrincipalBinding(
        workflow_id="mm:w2", correlation_id="corr-2",
        idempotency_key="idem-2", agent_run_id="ar-2",
    )
    await proxy.create_session(
        request=BridgeSessionCreateRequest(agent_id="agent-1", host_type="managed"),
        binding=binding,
    )

    attached = await proxy.attach_session(session_id="session-1", binding=binding)
    assert attached["id"] == "session-1"
    assert await proxy.list_hosts() == [{"id": "profile-host", "status": "ready"}]
    assert await proxy.post_event(
        session_id="session-1", event=BridgeSessionEventRequest(type="interrupt")
    ) == {"pending_id": "pending-1", "item_id": "item-1"}
    await proxy.post_event(
        session_id="session-1", event=BridgeSessionEventRequest(type="message")
    )
    assert await proxy.resolve_elicitation(
        session_id="session-1", elicitation_id="el-1", payload={"value": "yes"}
    ) == {"ok": True}

    assert (await proxy.get_resource("changed_files", "session-1"))["items"]
    assert (await proxy.get_resource("workspace_files", "session-1"))["items"]
    assert await proxy.get_resource("workspace_file", "session-1", "src/app.py") == b"print('fake')\n"
    assert (await proxy.get_resource("workspace_diff", "session-1", "src/app.py")).startswith(b"diff --git")
    assert (await proxy.get_resource("session_files", "session-1"))["items"]
    assert await proxy.get_resource("session_file", "session-1", "file-1") == b"session file evidence\n"

    streamed = [event async for event in proxy.stream_events("session-1")]
    assert streamed[-1]["type"] == "response.completed"
    with pytest.raises(OmnigentBridgeError, match="deletion is disabled") as exc:
        await proxy.delete_session("session-1")
    assert exc.value.code == "omnigent_bridge_capability_unavailable"


async def test_bridge_proxy_optional_diff_degrades_to_stable_capability_error(store) -> None:
    server = FakeOmnigentServer(supports_diff=False)
    running = await start_fake_omnigent_server(server)
    try:
        run_store, _ = store
        proxy = _proxy(run_store, running.base_url)
        with pytest.raises(OmnigentBridgeError) as exc:
            await proxy.get_resource("workspace_diff", "session-1", "src/app.py")
        assert exc.value.code == "omnigent_bridge_capability_unavailable"
        assert exc.value.failure_class == "user_error"
    finally:
        await running.runner.cleanup()
