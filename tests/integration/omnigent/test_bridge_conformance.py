"""Named Omnigent bridge conformance suite for OB-§20.2.

MM-1163: the reusable fake stock host is driven through the bridge facade and
real Omnigent HTTP client for the nine documented fake-server scenarios.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_service.db.models import OmnigentBridgeSession
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    BridgeSessionEventRequest,
    OmnigentBridgeSessionProxy,
)
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_NOT_PREPARED,
    FIRST_MESSAGE_PREPARED,
    FIRST_MESSAGE_POSTED,
    FIRST_MESSAGE_POSTING,
    FIRST_MESSAGE_TERMINAL,
    OmnigentBridgeSessionStore,
    OmnigentDigestMismatchError,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from tests.helpers.omnigent_conformance import (
    BRIDGE_CONFORMANCE_SCENARIOS,
    FakeOmnigentServer,
    FakeOmnigentScenario,
    RunningFakeOmnigentServer,
    start_fake_omnigent_server,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@dataclass(frozen=True, slots=True)
class BridgeHarness:
    running: RunningFakeOmnigentServer
    store: OmnigentBridgeSessionStore
    proxy: OmnigentBridgeSessionProxy
    client: OmnigentHttpClient


@pytest_asyncio.fixture
async def bridge_harness():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentBridgeSession.__table__.create)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(session_maker)
    running_servers: list[RunningFakeOmnigentServer] = []

    async def start(**server_kwargs: Any) -> BridgeHarness:
        running = await start_fake_omnigent_server(FakeOmnigentServer(**server_kwargs))
        running_servers.append(running)
        client = OmnigentHttpClient(base_url=running.base_url)
        proxy = OmnigentBridgeSessionProxy(
            run_store=store,
            client=client,
            default_agent_name="codex",
        )
        return BridgeHarness(
            running=running,
            store=store,
            proxy=proxy,
            client=client,
        )

    try:
        yield start
    finally:
        for running in running_servers:
            await running.runner.cleanup()
        await engine.dispose()


def _binding(key: str = "idem-1") -> BridgePrincipalBinding:
    return BridgePrincipalBinding(
        workflow_id="mm:w1",
        correlation_id="corr-1",
        idempotency_key=key,
        agent_run_id="ar-1",
    )


def _request() -> BridgeSessionCreateRequest:
    return BridgeSessionCreateRequest(
        agent_id="agent-1",
        host_type="managed",
        workspace="https://github.com/org/repo#main",
    )


async def _create_and_post(
    harness: BridgeHarness,
    *,
    key: str = "idem-1",
) -> dict[str, Any]:
    created = await harness.proxy.create_session(
        request=_request(),
        binding=_binding(key),
    )
    await harness.proxy.post_event(
        session_id=created["id"],
        event=BridgeSessionEventRequest(type="message", text="hello"),
    )
    return created


async def test_bridge_conformance_suite_declares_all_nine_scenarios() -> None:
    assert BRIDGE_CONFORMANCE_SCENARIOS == (
        "successful_session_with_streamed_assistant_output",
        "failed_session_with_diagnostics",
        "stream_disconnect_and_snapshot_reconciliation",
        "retry_after_session_create_before_first_message",
        "retry_after_posting_state",
        "digest_mismatch_under_same_idempotency_key",
        "optional_diff_unavailable",
        "child_session_event_capture",
        "cancellation_via_interrupt_and_stop_session",
    )


async def test_scenario_01_successful_session_with_streamed_assistant_output(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness()
    created = await _create_and_post(harness)

    events = [event async for event in harness.client.stream_events(created["id"])]
    snapshot = await harness.proxy.get_session(created["id"])

    assert events[-1]["type"] == "response.completed"
    assert snapshot["status"] == "completed"
    assert snapshot["summary"] == "fake Omnigent completed"


async def test_scenario_02_failed_session_with_diagnostics(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(terminal_status="failed")
    created = await _create_and_post(harness)

    snapshot = await harness.proxy.get_session(created["id"])

    assert snapshot["status"] == "failed"
    assert snapshot["diagnostics"][0]["code"] == "fake_failure"


async def test_scenario_03_stream_disconnect_and_snapshot_reconciliation(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(stream_disconnect=True)
    created = await _create_and_post(harness)

    events = [event async for event in harness.client.stream_events(created["id"])]
    snapshot = await harness.proxy.get_session(created["id"])

    assert events == [{"session": {"status": "running"}}]
    assert snapshot["status"] == "completed"


async def test_scenario_04_retry_after_session_create_before_first_message(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness()
    binding = _binding("idem-create-retry")

    first = await harness.proxy.create_session(request=_request(), binding=binding)
    second = await harness.proxy.create_session(request=_request(), binding=binding)

    assert first["id"] == second["id"] == "session-1"
    assert second["moonmind"]["reused"] is True
    assert len(harness.running.server.session_ids) == 1
    assert harness.running.server.events == []


async def test_scenario_05_retry_after_posting_state(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness()
    key = "idem-posting-retry"
    created = await harness.proxy.create_session(
        request=_request(),
        binding=_binding(key),
    )
    await harness.store.mark_prepared(key, digest="sha256:first", marker="marker")
    posting = await harness.store.mark_posting(key)

    reused = await harness.proxy.create_session(
        request=_request(),
        binding=_binding(key),
    )
    response = await harness.proxy.post_event(
        session_id=reused["id"],
        event=BridgeSessionEventRequest(type="message", text="hello"),
    )
    posted = await harness.store.mark_posted(key, response=response)

    assert created["id"] == reused["id"]
    assert reused["moonmind"]["reused"] is True
    assert posting.first_message_state == FIRST_MESSAGE_POSTING
    assert posted.first_message_state == FIRST_MESSAGE_POSTED
    assert len(harness.running.server.session_ids) == 1
    assert len(harness.running.server.events) == 1


async def test_scenario_06_digest_mismatch_under_same_idempotency_key(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness()
    key = "idem-digest-mismatch"
    await harness.proxy.create_session(request=_request(), binding=_binding(key))
    await harness.store.mark_prepared(key, digest="sha256:first", marker="marker")

    with pytest.raises(OmnigentDigestMismatchError):
        await harness.store.mark_prepared(
            key,
            digest="sha256:second",
            marker="marker",
        )


async def test_scenario_07_optional_diff_unavailable(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(supports_diff=False)
    created = await _create_and_post(harness)

    harvested = await harness.proxy.harvest_session(created["id"])

    assert harvested["status"] == "completed_with_diagnostics"
    assert harvested["moonmind"]["diagnostics"][0]["code"] == (
        "workspaceDiffs_unavailable"
    )
    assert harvested["resources"]["workspaceDiffs"][0]["unavailable"][
        "statusCode"
    ] == 404


async def test_scenario_08_child_session_event_capture(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(include_child_session_event=True)
    created = await _create_and_post(harness)

    events = [event async for event in harness.client.stream_events(created["id"])]

    assert any(event.get("type") == "session.child.created" for event in events)
    assert any(event.get("session", {}).get("id") == "child-1" for event in events)


async def test_scenario_09_cancellation_via_interrupt_and_stop_session(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness()
    created = await harness.proxy.create_session(
        request=_request(),
        binding=_binding("idem-cancel"),
    )

    interrupt = await harness.proxy.post_event(
        session_id=created["id"],
        event=BridgeSessionEventRequest(type="interrupt"),
    )
    stop = await harness.proxy.post_event(
        session_id=created["id"],
        event=BridgeSessionEventRequest(type="stop_session"),
    )

    assert interrupt["pending_id"] == "pending-1"
    assert stop["pending_id"] == "pending-1"
    assert [event["type"] for event in harness.running.server.events] == [
        "interrupt",
        "stop_session",
    ]


@pytest.mark.parametrize("status", [401, 403, 404, 409, 429, 500, 502, 504])
async def test_failure_matrix_preserves_status_classification_and_redacts_secrets(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]], status: int
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(route_statuses=(("create_session", status),))
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await harness.proxy.create_session(request=_request(), binding=_binding(f"status-{status}"))

    assert excinfo.value.status_code == status
    assert excinfo.value.failure_class
    assert "fake-secret" not in str(excinfo.value)


async def test_malformed_timeout_and_close_are_deterministic_real_transport_failures(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    malformed = await bridge_harness(
        scenario=FakeOmnigentScenario(malformed_routes=("hosts",))
    )
    with pytest.raises(OmnigentBridgeError, match="unsupported response shape"):
        await malformed.proxy.list_hosts()

    delayed = await bridge_harness(
        scenario=FakeOmnigentScenario(
            delayed_routes=("hosts",), delay_seconds=0.05
        )
    )
    delayed.proxy._client = OmnigentHttpClient(  # exercise the configured real timeout
        base_url=delayed.running.base_url, timeout_seconds=0.01
    )
    with pytest.raises(OmnigentBridgeError, match="transport error"):
        await delayed.proxy.list_hosts()

    closed = await bridge_harness(
        scenario=FakeOmnigentScenario(close_routes=("hosts",))
    )
    with pytest.raises(OmnigentBridgeError, match="transport error"):
        await closed.proxy.list_hosts()


async def test_stream_reconnect_replays_frames_and_store_deduplicates_after_restart(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    frame = '{"id":"provider-1","type":"assistant.delta","delta":"same"}'
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(
            stream_frames=(frame, frame), stream_disconnect_attempts=1
        )
    )
    created = await _create_and_post(harness, key="idem-replay")
    streamed = [event async for event in harness.proxy.stream_events(created["id"])]
    row = await harness.store.get_existing("idem-replay")
    assert row is not None

    first = await harness.store.append_events(row.bridge_session_id, streamed)
    recreated_store = OmnigentBridgeSessionStore(harness.store._session_factory)
    second = await recreated_store.append_events(row.bridge_session_id, streamed)
    page = await recreated_store.list_event_page(row.bridge_session_id, after=0, limit=100)

    assert harness.running.server.stream_attempts == 2
    assert len(first) == 2  # one provider delta plus the terminal frame
    assert second == []
    assert [event.sequence for event in page.rows] == sorted(event.sequence for event in page.rows)


async def test_first_message_state_machine_is_durable_and_never_reposts(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness()
    key = "idem-state-matrix"
    created = await harness.proxy.create_session(request=_request(), binding=_binding(key))
    row = await harness.store.get_existing(key)
    assert row is not None and row.first_message_state == FIRST_MESSAGE_NOT_PREPARED
    assert (await harness.store.mark_prepared(key, digest="sha256:first", marker="m")).first_message_state == FIRST_MESSAGE_PREPARED
    assert (await harness.store.mark_posting(key)).first_message_state == FIRST_MESSAGE_POSTING
    response = await harness.proxy.post_event(
        session_id=created["id"], event=BridgeSessionEventRequest(type="message", text="hello")
    )
    assert (await harness.store.mark_posted(key, response=response)).first_message_state == FIRST_MESSAGE_POSTED
    assert (await harness.store.mark_terminal(key, status="completed")).first_message_state == FIRST_MESSAGE_TERMINAL
    assert len(harness.running.server.session_ids) == 1
    assert len(harness.running.server.events) == 1


async def test_oversized_json_and_binary_are_bounded_through_facade(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(
            oversized_json_items=300, oversized_binary_bytes=4 * 1024 * 1024 + 1
        )
    )
    created = await harness.proxy.create_session(request=_request(), binding=_binding("bounds"))
    index = await harness.proxy.get_resource("workspace_files", created["id"])
    assert len(index["items"]) == 250
    with pytest.raises(OmnigentBridgeError, match="exceeds the bridge response limit"):
        await harness.proxy.get_resource("workspace_file", created["id"], "src/app.py")
