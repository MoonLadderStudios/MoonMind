"""Hermetic fake-server coverage for the Omnigent bridge proxy (OB-§20.2).

MM-1155 (source: MM-1140): drive the proxy-mode Host Protocol Facade against a
fake stock Omnigent Server using the real HTTP client and the real STORY-002
store (SQLite). Verifies create/reuse/get, provider-session persistence before
first message, and durable ``session.created`` emission.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_service.db.models import OmnigentBridgeSession
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    OmnigentBridgeSessionProxy,
)
from moonmind.omnigent.bridge_store import (
    BRIDGE_EVENT_JOURNAL_KEY,
    SESSION_CREATED_EVENT_TYPE,
    OmnigentBridgeSessionStore,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


class FakeOmnigentServer:
    def __init__(self) -> None:
        self.session_ids: list[str] = []
        self.create_payloads: list[dict[str, Any]] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/agents", self.list_agents)
        app.router.add_post("/v1/sessions", self.create_session)
        app.router.add_get("/v1/sessions/{session_id}", self.get_session)
        return app

    async def list_agents(self, _request: web.Request) -> web.Response:
        return web.json_response({"agents": [{"id": "agent-1", "name": "codex"}]})

    async def create_session(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.create_payloads.append(payload)
        session_id = f"session-{len(self.session_ids) + 1}"
        self.session_ids.append(session_id)
        return web.json_response({"id": session_id})

    async def get_session(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": request.match_info["session_id"],
                "status": "running",
                "summary": "fake Omnigent snapshot",
            }
        )


@pytest_asyncio.fixture
async def fake_server():
    server = FakeOmnigentServer()
    runner = web.AppRunner(server.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets if site._server is not None else []
    assert sockets
    host, port = sockets[0].getsockname()[:2]
    try:
        yield server, f"http://{host}:{port}"
    finally:
        await runner.cleanup()


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
    assert server.create_payloads[0]["labels"]["moonmind.issue"] == "MM-1155"

    # Provider session id persisted + session.created recorded on the row.
    row = await _row(session_maker, "idem-1")
    assert row.omnigent_session_id == "session-1"
    journal = row.metadata_[BRIDGE_EVENT_JOURNAL_KEY]
    assert [entry["type"] for entry in journal] == [SESSION_CREATED_EVENT_TYPE]

    # GET returns an Omnigent-shaped snapshot.
    snapshot = await proxy.get_session("session-1")
    assert snapshot["id"] == "session-1"
    assert snapshot["summary"] == "fake Omnigent snapshot"

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
