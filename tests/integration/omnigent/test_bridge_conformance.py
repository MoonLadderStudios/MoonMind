"""Named Omnigent bridge conformance suite for OB-§20.2.

MM-1163: the reusable fake stock host is driven through the bridge facade and
real Omnigent HTTP client for the nine documented fake-server scenarios.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_service.api.routers import omnigent_bridge as bridge_router
from api_service.db.models import OmnigentBridgeSession, OmnigentBridgeSessionEvent
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    BridgeSessionEventRequest,
    OmnigentBridgeError,
    OmnigentBridgeSessionProxy,
)
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_POSTED,
    FIRST_MESSAGE_PREPARED,
    FIRST_MESSAGE_POSTING,
    FIRST_MESSAGE_TERMINAL,
    OmnigentBridgeSessionStore,
    OmnigentDigestMismatchError,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)
from tests.helpers.omnigent_conformance import (
    BRIDGE_CONFORMANCE_SCENARIOS,
    CONFORMANCE_PROFILE_VERSION,
    FakeOmnigentScenario,
    FakeOmnigentServer,
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
    session_maker: Any


@pytest_asyncio.fixture
async def bridge_harness():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentBridgeSession.__table__.create)
        await conn.run_sync(OmnigentBridgeSessionEvent.__table__.create)
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
            session_maker=session_maker,
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


async def test_bridge_conformance_suite_declares_versioned_scenarios() -> None:
    assert CONFORMANCE_PROFILE_VERSION == "moonmind.omnigent.conformance/v4"
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
        "transport_status_timeout_and_malformed_responses",
        "stream_replay_overlap_and_schema_drift",
        "oversized_resources_and_secret_redaction",
        "ambiguous_first_message_response",
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
async def test_scenario_10_upstream_statuses_keep_stable_failure_evidence(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]], status: int
) -> None:
    scenario = FakeOmnigentScenario(statuses={"sessions.create": status})
    harness = await bridge_harness(scenario=scenario)

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await harness.proxy.create_session(
            request=_request(), binding=_binding(f"status-{status}")
        )

    error = excinfo.value
    assert error.status_code == status
    assert error.code.startswith("omnigent_bridge_")
    assert error.failure_class
    assert "fake-upstream-secret" not in str(error)
    assert harness.running.server.route_calls == ["sessions.create"]


async def test_scenario_10_timeout_and_malformed_json_are_deterministic(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    timeout_harness = await bridge_harness(
        scenario=FakeOmnigentScenario(delays={"hosts": 0.1})
    )
    timeout_client = OmnigentHttpClient(
        base_url=timeout_harness.running.base_url, timeout_seconds=0.01
    )
    with pytest.raises(OmnigentClientError) as timeout:
        await timeout_client.list_hosts()
    assert timeout.value.failure_class == "integration_error"

    malformed = await bridge_harness(
        scenario=FakeOmnigentScenario(malformed_json={"hosts"})
    )
    with pytest.raises(OmnigentClientError) as payload:
        await malformed.client.list_hosts()
    assert payload.value.failure_class == "integration_error"


@pytest.mark.parametrize(
    ("route", "invoke"),
    [
        ("agents", lambda client: client.list_agents()),
        ("hosts", lambda client: client.list_hosts()),
        ("sessions.create", lambda client: client.create_session({"agentId": "agent-1"})),
        ("sessions.get", lambda client: client.get_session("session-1")),
        ("resources.changes", lambda client: client.list_changed_files("session-1")),
        ("resources.workspace-list", lambda client: client.list_workspace_files("session-1")),
        ("resources.session-list", lambda client: client.list_session_files("session-1")),
    ],
)
async def test_malformed_route_schema_matrix_fails_closed(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
    route: str,
    invoke: Callable[[OmnigentHttpClient], Awaitable[Any]],
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(malformed_payloads={route: {"unexpected": [{"nested": None}]}})
    )
    try:
        result = await invoke(harness.client)
    except (OmnigentClientError, OmnigentBridgeError, KeyError, TypeError, ValueError) as exc:
        assert len(str(exc)) < 512
        assert "authorization" not in str(exc).lower()
    else:
        # Catalog discovery may safely degrade to an empty list; other raw
        # client calls preserve the malformed shape for facade validation.
        assert result == [] or isinstance(result, dict)
    assert harness.running.server.route_calls == [route]


async def test_transport_close_phases_are_bounded_integration_failures(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    before = await bridge_harness(
        scenario=FakeOmnigentScenario(close_before_headers={"hosts"})
    )
    with pytest.raises(OmnigentClientError) as closed:
        await before.client.list_hosts()
    assert closed.value.failure_class == "integration_error"

    during = await bridge_harness(
        scenario=FakeOmnigentScenario(close_during_body={"resources.workspace-file"})
    )
    with pytest.raises(OmnigentClientError) as partial:
        await during.client.get_workspace_file("session-1", "README.md")
    assert partial.value.failure_class == "integration_error"


async def test_close_during_sse_and_missing_terminal_active_snapshot_are_explicit(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    closed = await bridge_harness(
        scenario=FakeOmnigentScenario(
            stream_frames=[
                b'data: {"event_id":"provider-1","type":"response.delta"}\n\n',
                b'data: {"event_id":"provider-2","type":"response.completed"}\n\n',
            ],
            close_during_sse_after=1,
        )
    )
    created = await _create_and_post(closed)
    received: list[dict[str, Any]] = []
    with pytest.raises(OmnigentClientError):
        async for event in closed.client.stream_events(created["id"]):
            received.append(event)
    assert [event["event_id"] for event in received] == ["provider-1"]

    active = await bridge_harness(
        terminal_status="completed",
        scenario=FakeOmnigentScenario(
            missing_terminal_stream=True,
            active_snapshot_after_stream_end=True,
        ),
    )
    created = await _create_and_post(active, key="missing-terminal")
    events = [event async for event in active.client.stream_events(created["id"])]
    snapshot = await active.proxy.get_session(created["id"])
    assert [event["type"] for event in events] == ["response.delta"]
    assert snapshot["status"] == "running"


async def test_out_of_order_optional_route_and_elicitation_schema_controls(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    ordered = await bridge_harness(
        scenario=FakeOmnigentScenario(out_of_order_stream=True)
    )
    created = await _create_and_post(ordered)
    frames = [event async for event in ordered.client.stream_events(created["id"])]
    assert [event["sequence"] for event in frames] == [2, 1]

    optional = await bridge_harness(
        scenario=FakeOmnigentScenario(unavailable_routes={"resources.diff"})
    )
    created = await _create_and_post(optional, key="optional-absence")
    with pytest.raises(OmnigentBridgeError) as unavailable:
        await optional.proxy.get_resource(
            "workspace_diff", created["id"], "README.md"
        )
    assert unavailable.value.code == "omnigent_bridge_capability_unavailable"

    malformed = await bridge_harness(
        scenario=FakeOmnigentScenario(
            malformed_payloads={"elicitations.resolve": {"unexpected": [{"token": "secret"}]}}
        )
    )
    created = await _create_and_post(malformed, key="elicitation-drift")
    result = await malformed.proxy.resolve_elicitation(
        session_id=created["id"], elicitation_id="el-1", payload={"answer": "yes"}
    )
    assert result == {"unexpected": [{"token": "[REDACTED]"}]}


async def test_scenario_11_stream_replay_identity_and_malformed_frame(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    frames = [
        b'data: {"id":"provider-1","delta":"same"}\n\n',
        b'data: {"id":"provider-1","delta":"same"}\n\n',
        b'data: {"id":"provider-2","delta":"same"}\n\n',
        b'data: {broken}\n\n',
    ]
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(stream_frames=frames)
    )
    created = await _create_and_post(harness)
    received: list[dict[str, Any]] = []
    with pytest.raises(OmnigentClientError, match="Malformed Omnigent SSE frame"):
        async for event in harness.client.stream_events(created["id"]):
            received.append(event)

    assert [event["id"] for event in received] == [
        "provider-1",
        "provider-1",
        "provider-2",
    ]


async def test_scenario_11_reconnect_persists_overlap_once_and_terminalizes_append_only(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    """Exercise the real HTTP stream, normalizer, and durable store together."""

    first_stream = [
        b'data: {"event_id":"provider-1","type":"response.delta","data":{"text":"one"}}\n\n',
        b'data: {"event_id":"provider-2","type":"response.delta","data":{"text":"same"}}\n\n',
    ]
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(
            stream_frames=first_stream,
            stream_disconnect_after=2,
        )
    )
    key = "idem-durable-reconnect"
    created = await _create_and_post(harness, key=key)
    row = await harness.store.get_existing(key)
    assert row is not None
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey=key,
    )

    async def persist(frames: list[dict[str, Any]], start: int) -> None:
        for cursor, payload in enumerate(frames, start=start):
            normalized = build_omnigent_bridge_event(
                payload=payload,
                sequence=cursor,
                request=request,
                omnigent_session_id=created["id"],
                bridge_session_id=row.bridge_session_id,
            ).event
            await harness.store.append_events(row.bridge_session_id, [normalized])

    received: list[dict[str, Any]] = []
    with pytest.raises(OmnigentClientError):
        async for event in harness.client.stream_events(created["id"]):
            received.append(event)
    await persist(received, 1)

    # Recreate the HTTP client boundary and reconnect with one overlapping event.
    harness.running.server.scenario.stream_disconnect_after = None
    harness.running.server.scenario.stream_frames = [
        first_stream[1],
        b'data: {"event_id":"provider-3","type":"response.delta","data":{"text":"same"}}\n\n',
        b'data: {"event_id":"provider-4","type":"response.completed"}\n\n',
    ]
    restarted_client = OmnigentHttpClient(base_url=harness.running.base_url)
    replayed = [event async for event in restarted_client.stream_events(created["id"])]
    # Use the original cursor for the overlap; provider event identity is stable
    # and the distinct identical delta keeps its own identity.
    await persist([replayed[0]], 2)
    await persist(replayed[1:], 3)
    await harness.store.mark_terminal(key, status="completed")

    events = await harness.store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        "response.delta",
        "response.delta",
        "response.delta",
        "response.completed",
    ]
    assert [event.text_preview for event in events[:3]] == ["one", "same", "same"]
    terminal = await harness.store.get_existing(key)
    assert terminal is not None
    assert terminal.status == "completed"


async def test_scenario_12_oversized_real_resources_fail_bounded(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(
            oversized_json_items=20_000,
            oversized_binary_bytes=4 * 1024 * 1024 + 1,
        )
    )
    created = await _create_and_post(harness)

    bounded = await harness.proxy.get_resource("workspace_files", created["id"])
    assert len(bounded["items"]) == 250
    with pytest.raises(OmnigentBridgeError) as excinfo:
        await harness.proxy.get_resource(
            "workspace_file", created["id"], "README.md"
        )
    assert excinfo.value.code == "omnigent_bridge_response_too_large"
    assert len(str(excinfo.value)) < 256


async def test_scenario_13_error_diagnostics_redact_credentials(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(statuses={"sessions.get": 500})
    )
    client = OmnigentHttpClient(
        base_url=harness.running.base_url,
        api_token="fake-upstream-secret",
        forward_headers={
            "cookie": "user-cookie",
            "authorization": "Bearer user-jwt",
        },
    )
    with pytest.raises(OmnigentClientError) as excinfo:
        await client.get_session("session-unknown")
    rendered = repr(excinfo.value.diagnostics())
    assert "fake-upstream-secret" not in rendered
    assert "user-cookie" not in rendered
    assert "user-jwt" not in rendered
    assert "[REDACTED_AUTHORIZATION]" in rendered


async def test_first_message_crash_matrix_reconciles_response_before_persist_once(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    """Prove every durable state and the ambiguous response crash boundary."""

    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(event_response_before_close=True)
    )
    key = "idem-first-message-matrix"
    created = await harness.proxy.create_session(
        request=_request(), binding=_binding(key)
    )
    initial = await harness.store.get_existing(key)
    assert initial is not None and initial.first_message_state == "not_prepared"

    prepared = await harness.store.mark_prepared(
        key, digest="sha256:first", marker="message:first"
    )
    assert prepared.first_message_state == FIRST_MESSAGE_PREPARED
    posting = await harness.store.mark_posting(key)
    assert posting.first_message_state == FIRST_MESSAGE_POSTING

    # The provider accepted the message, then the process crashes before the
    # returned identifiers can be persisted. Reconciliation uses provider
    # evidence and never invokes POST a second time.
    response = await harness.proxy.post_event(
        session_id=created["id"],
        event=BridgeSessionEventRequest(type="message", text="hello"),
    )
    ambiguous = await harness.store.get_existing(key)
    assert ambiguous is not None
    assert ambiguous.first_message_state == FIRST_MESSAGE_POSTING
    assert len(harness.running.server.events) == 1
    snapshot = await harness.proxy.get_session(created["id"])
    assert snapshot["status"] == "completed"
    posted = await harness.store.mark_posted(key, response=response)
    assert posted.first_message_state == FIRST_MESSAGE_POSTED
    assert len(harness.running.server.events) == 1

    terminal = await harness.store.mark_terminal(
        key,
        status="completed",
        terminal_refs={"summary": "reconciled from provider snapshot"},
    )
    assert terminal.first_message_state == FIRST_MESSAGE_TERMINAL


async def test_partial_post_before_ack_is_ambiguous_and_never_reposted(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(event_accept_before_ack_close=True)
    )
    key = "idem-partial-before-ack"
    created = await harness.proxy.create_session(request=_request(), binding=_binding(key))
    await harness.store.mark_prepared(key, digest="sha256:first", marker="message:first")
    await harness.store.mark_posting(key)

    with pytest.raises(OmnigentBridgeError) as ambiguous:
        await harness.proxy.post_event(
            session_id=created["id"],
            event=BridgeSessionEventRequest(type="message", text="hello"),
        )
    assert ambiguous.value.failure_class == "integration_error"
    row = await harness.store.get_existing(key)
    assert row is not None and row.first_message_state == FIRST_MESSAGE_POSTING
    assert len(harness.running.server.events) == 1

    snapshot = await harness.proxy.get_session(created["id"])
    assert snapshot["status"] == "completed"
    assert len(harness.running.server.events) == 1
    with pytest.raises(OmnigentDigestMismatchError):
        await harness.store.mark_prepared(
            key, digest="sha256:different", marker="message:different"
        )


@pytest.mark.parametrize(
    ("boundary", "expected_posts"),
    [
        ("not_prepared", 1),
        (FIRST_MESSAGE_PREPARED, 1),
        (FIRST_MESSAGE_POSTING, 0),
        (FIRST_MESSAGE_POSTED, 0),
        (FIRST_MESSAGE_TERMINAL, 0),
    ],
)
async def test_each_first_message_restart_boundary_is_exactly_once(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
    boundary: str,
    expected_posts: int,
) -> None:
    """Independently recreate the facade at each durable retry boundary."""

    harness = await bridge_harness()
    key = f"idem-restart-{boundary}"
    created = await harness.proxy.create_session(request=_request(), binding=_binding(key))
    if boundary != "not_prepared":
        await harness.store.mark_prepared(
            key, digest="sha256:first", marker="message:first"
        )
    if boundary in {FIRST_MESSAGE_POSTING, FIRST_MESSAGE_POSTED, FIRST_MESSAGE_TERMINAL}:
        await harness.store.mark_posting(key)
    if boundary in {FIRST_MESSAGE_POSTED, FIRST_MESSAGE_TERMINAL}:
        await harness.store.mark_posted(
            key, response={"pending_id": "pending-1", "item_id": "item-1"}
        )
    if boundary == FIRST_MESSAGE_TERMINAL:
        await harness.store.mark_terminal(
            key, status="completed", terminal_refs={"summary": "complete"}
        )

    restarted = OmnigentBridgeSessionProxy(
        run_store=harness.store,
        client=harness.client,
        default_agent_name="codex",
    )
    reused = await restarted.create_session(request=_request(), binding=_binding(key))
    assert reused["id"] == created["id"]
    assert len(harness.running.server.session_ids) == 1

    if boundary in {"not_prepared", FIRST_MESSAGE_PREPARED}:
        await restarted.post_event(
            session_id=created["id"],
            event=BridgeSessionEventRequest(type="message", text="hello"),
        )
    else:
        # Posting is ambiguous; posted/terminal are authoritative. None may be
        # retried by silently issuing another provider prompt.
        row = await harness.store.get_existing(key)
        assert row is not None and row.first_message_state == boundary
    assert len(harness.running.server.events) == expected_posts

    if boundary == "not_prepared":
        await harness.store.mark_prepared(
            key, digest="sha256:first", marker="message:first"
        )
    with pytest.raises(OmnigentDigestMismatchError):
        await harness.store.mark_prepared(
            key, digest="sha256:different", marker="message:different"
        )


async def test_real_store_api_page_and_sse_project_gap_cursor_terminal_and_redaction(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]], monkeypatch, tmp_path
) -> None:
    """Drive the durable store through the API page/SSE projection functions."""

    harness = await bridge_harness()
    key = "idem-api-projection"
    created = await _create_and_post(harness, key=key)
    row = await harness.store.get_existing(key)
    assert row is not None
    secret = "Bearer github_pat_secret-do-not-persist"
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey=key,
    )
    for sequence, payload in (
        (
            2,
            {
                "event_id": "provider-2",
                "type": "response.delta",
                "data": {"text": secret},
            },
        ),
        (
            3,
            {
                "event_id": "provider-3",
                "type": "response.completed",
                "authorization": secret,
            },
        ),
    ):
        normalized = build_omnigent_bridge_event(
            payload=payload,
            sequence=sequence,
            request=request,
            omnigent_session_id=created["id"],
            bridge_session_id=row.bridge_session_id,
        ).event
        await harness.store.append_events(row.bridge_session_id, [normalized])
    # Simulate retention pruning the first durable event so the API must
    # report the gap before the remaining sequence range.
    async with harness.session_maker() as session:
        await session.execute(
            delete(OmnigentBridgeSessionEvent).where(
                OmnigentBridgeSessionEvent.bridge_session_id == row.bridge_session_id,
                OmnigentBridgeSessionEvent.sequence == 1,
            )
        )
        await session.commit()
    await harness.store.mark_terminal(
        key,
        status="completed",
        terminal_refs={
            "summary": f"completed {secret}",
            "diagnosticsRef": "artifact://omnigent/diagnostics",
            "normalizedEventsRef": "artifact://omnigent/normalized",
        },
    )

    async def owner_principal(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(workflow_id="mm:w1")

    monkeypatch.setattr(bridge_router, "resolve_execution_principal", owner_principal)
    user = SimpleNamespace(id="owner")
    service = SimpleNamespace()
    page = await bridge_router.list_omnigent_bridge_session_events(
        row.bridge_session_id,
        after=0,
        cursor=None,
        limit=100,
        _enabled=SimpleNamespace(),
        user=user,
        service=service,
        store=harness.store,
    )
    page_json = page.model_dump_json(by_alias=True)
    assert page.retention_gap is not None
    assert page.retention_gap.earliest_available == 2
    assert page.terminal is True
    assert page.terminal_envelope is not None
    assert page.terminal_envelope.diagnostics_ref == "artifact://omnigent/diagnostics"

    fake_request = SimpleNamespace(is_disconnected=_always_connected)
    response = await bridge_router.stream_omnigent_bridge_session_events(
        row.bridge_session_id,
        request=fake_request,
        since=None,
        cursor=2,
        last_event_id="1",
        _enabled=SimpleNamespace(),
        user=user,
        service=service,
        store=harness.store,
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    sse = "".join(chunks)
    assert "id: 3" in sse and "event: terminal" in sse

    durable_events = await harness.store.list_events(row.bridge_session_id)
    durable_rendered = json.dumps(
        [event.metadata_ for event in durable_events], default=str
    )
    combined = "\n".join((page_json, sse, durable_rendered))
    assert "github_pat_secret" not in combined
    assert "[REDACTED]" in combined

    # Materialize and scan the same durable evidence classes used by the
    # terminal contract, rather than checking refs or DB projections alone.
    artifact_bodies = {}
    for name, body in {
        "raw.sse": sse,
        "normalized.json": durable_rendered,
        "diagnostics.json": json.dumps({"summary": page.terminal_envelope.summary}),
    }.items():
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        artifact_bodies[name] = path.read_text(encoding="utf-8")
    materialized = json.dumps(artifact_bodies)
    assert "github_pat_secret" not in materialized
    assert "user-jwt" not in materialized
    assert "fake-upstream-secret" not in materialized
    assert "[REDACTED]" in materialized

    calls_before_denial = list(harness.running.server.route_calls)

    async def denied_principal(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(workflow_id=None)

    monkeypatch.setattr(bridge_router, "resolve_execution_principal", denied_principal)
    with pytest.raises(HTTPException) as denied:
        await bridge_router.list_omnigent_bridge_session_events(
            row.bridge_session_id,
            after=0,
            cursor=None,
            limit=100,
            _enabled=SimpleNamespace(),
            user=SimpleNamespace(id="foreign"),
            service=service,
            store=harness.store,
        )
    assert denied.value.status_code == 404
    assert denied.value.detail == {"code": "omnigent_bridge_session_unknown"}
    assert harness.running.server.route_calls == calls_before_denial


async def _always_connected() -> bool:
    return False
