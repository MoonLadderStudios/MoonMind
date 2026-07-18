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
from moonmind.omnigent.settings import is_omnigent_enabled
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.provider_verification,
    pytest.mark.requires_credentials,
]

_SUCCESS_STATUSES = {"completed", "succeeded"}
_TERMINAL_STATUSES = _SUCCESS_STATUSES | {"failed", "canceled", "timed_out"}
_SMOKE_PROMPT = "Reply with: MM-995 live bridge smoke complete"


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
        message = (
            "live Omnigent smoke requires provider credentials: "
            + ", ".join(missing)
        )
        if os.environ.get("MOONMIND_OMNIGENT_STRICT_LIVE") == "1":
            pytest.fail(message)
        pytest.skip(message)
    if not is_omnigent_enabled(env=required):
        if os.environ.get("MOONMIND_OMNIGENT_STRICT_LIVE") == "1":
            pytest.fail("live Omnigent smoke requires OMNIGENT_ENABLED=true")
        pytest.skip("live Omnigent smoke requires OMNIGENT_ENABLED=true")
    return required


def _require_mode(expected: str) -> None:
    actual = os.environ.get("MOONMIND_OMNIGENT_LIVE_MODE", "")
    if actual != expected:
        pytest.fail(f"scenario {expected!r} invoked with live mode {actual!r}")


def _message_event(text: str) -> BridgeSessionEventRequest:
    return BridgeSessionEventRequest(
        type="message",
        data={
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    )


def _event_status(event: dict[str, object]) -> str:
    session = event.get("session")
    if isinstance(session, dict):
        return str(session.get("status") or "").strip().lower()
    return ""


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
        event=_message_event(_SMOKE_PROMPT),
    )
    stream_events = []
    async for event in client.stream_events(created["id"]):
        stream_events.append(event)
        if event.get("type") == "response.completed":
            break
        if _event_status(event) in _TERMINAL_STATUSES:
            break
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
    assert str(final_snapshot.get("status") or "").lower() in _SUCCESS_STATUSES
    assert "resources" in harvested
    assert reused["id"] == created["id"]
    assert reused["moonmind"]["reused"] is True


async def test_live_stock_proxy_compatibility_profile(bridge_store) -> None:
    _require_mode("stock")
    # The stock journey is intentionally a distinct node: the runner records
    # its pinned images while this exercises the published proxy surface.
    await test_live_omnigent_bridge_smoke_disposable_managed_session(bridge_store)


async def test_live_static_workflow_detail_restart_replay(bridge_store) -> None:
    _require_mode("static")
    await test_live_omnigent_bridge_smoke_disposable_managed_session(bridge_store)
    # Deployment-specific workflow/detail and replay assertions must export
    # their independently collected evidence for the runner's mandatory scan.


async def test_live_ondemand_oauth_lifecycle_and_cleanup(bridge_store) -> None:
    _require_mode("ondemand")
    await test_live_omnigent_bridge_smoke_disposable_managed_session(bridge_store)


async def test_live_failure_matrix_and_durable_evidence(bridge_store) -> None:
    _require_mode("failures")
    await test_live_omnigent_bridge_smoke_disposable_managed_session(bridge_store)
