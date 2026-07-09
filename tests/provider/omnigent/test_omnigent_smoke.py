"""MM-995 live Omnigent smoke checks.

These tests are provider verification only. They require a real disposable
Omnigent server and are intentionally excluded from credential-free CI.
Source issue traceability: MM-981 -> MM-995.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import OmnigentBridgeSession
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    BridgeSessionEventRequest,
    OmnigentBridgeSessionProxy,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.provider_verification,
    pytest.mark.requires_credentials,
]


def _live_env() -> dict[str, str]:
    required = {
        "OMNIGENT_ENABLED": os.environ.get("OMNIGENT_ENABLED", ""),
        "OMNIGENT_SERVER_URL": os.environ.get("OMNIGENT_SERVER_URL", ""),
        "OMNIGENT_API_TOKEN": os.environ.get("OMNIGENT_API_TOKEN", ""),
        "OMNIGENT_DEFAULT_AGENT_NAME": os.environ.get(
            "OMNIGENT_DEFAULT_AGENT_NAME",
            "",
        ),
    }
    missing = [key for key, value in required.items() if not value.strip()]
    if missing:
        pytest.skip(
            "live Omnigent smoke requires provider credentials: "
            + ", ".join(missing)
        )
    return required


@pytest_asyncio.fixture
async def bridge_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge-smoke.db")
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentBridgeSession.__table__.create)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentBridgeSessionStore(session_maker)
    finally:
        await engine.dispose()


async def test_live_omnigent_bridge_smoke_disposable_managed_session(
    bridge_store: OmnigentBridgeSessionStore,
) -> None:
    env = _live_env()
    client = OmnigentHttpClient(
        base_url=env["OMNIGENT_SERVER_URL"],
        api_token=env["OMNIGENT_API_TOKEN"],
    )
    proxy = OmnigentBridgeSessionProxy(
        run_store=bridge_store,
        client=client,
        default_agent_name=env["OMNIGENT_DEFAULT_AGENT_NAME"],
    )
    binding = BridgePrincipalBinding(
        workflow_id="mm-995-live-smoke",
        correlation_id="mm-995-live-smoke",
        idempotency_key="mm-995-live-smoke",
        agent_run_id="ar-mm-995-live-smoke",
    )

    agents = await proxy.list_agents()
    created = await proxy.create_session(
        request=BridgeSessionCreateRequest(
            title="MM-995 live bridge smoke",
            host_type="managed",
        ),
        binding=binding,
    )
    posted = await proxy.post_event(
        session_id=created["id"],
        event=BridgeSessionEventRequest(
            type="message",
            text="Reply with: MM-995 live bridge smoke complete",
        ),
    )
    stream_events = [event async for event in client.stream_events(created["id"])]
    final_snapshot = await proxy.get_session(created["id"])
    harvested = await proxy.harvest_session(created["id"])
    reused = await proxy.create_session(
        request=BridgeSessionCreateRequest(
            title="MM-995 live bridge smoke",
            host_type="managed",
        ),
        binding=binding,
    )

    assert agents
    assert created["id"]
    assert posted
    assert stream_events
    assert final_snapshot["id"] == created["id"]
    assert "resources" in harvested
    assert reused["id"] == created["id"]
    assert reused["moonmind"]["reused"] is True
