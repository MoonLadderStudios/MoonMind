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
    OmnigentBridgeError,
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


@pytest.mark.parametrize("route,resource,args", [
    ("workspace_files", "workspace_files", ()),
    ("workspace_file", "workspace_file", ("src/app.py",)),
])
async def test_close_during_json_or_binary_body_is_a_bounded_transport_failure(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
    route: str,
    resource: str,
    args: tuple[str, ...],
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(close_during_body_routes=(route,))
    )
    created = await harness.proxy.create_session(
        request=_request(), binding=_binding(f"body-close-{route}")
    )

    with pytest.raises(OmnigentBridgeError) as excinfo:
        await harness.proxy.get_resource(resource, created["id"], *args)

    assert excinfo.value.failure_class
    assert len(str(excinfo.value)) < 2048
    assert "partial-secret-shaped-token" not in str(excinfo.value)


async def test_stream_frame_variants_disconnect_and_terminal_omission_are_explicit(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    frames = (
        '{"id":"provider-2","type":"assistant.delta","delta":"same","cursor":2}',
        '{"id":"provider-1","type":"assistant.delta","delta":"same","cursor":1}',
        "{malformed",
    )
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(
            stream_frames=frames,
            stream_disconnect_after_frames=3,
            omit_terminal_event=True,
        )
    )
    created = await _create_and_post(harness, key="frame-variants")

    with pytest.raises(OmnigentBridgeError) as excinfo:
        _ = [event async for event in harness.proxy.stream_events(created["id"])]

    assert excinfo.value.failure_class
    assert len(str(excinfo.value)) < 2048


async def test_missing_terminal_and_active_snapshot_remain_nonterminal(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(
            omit_terminal_event=True, snapshot_stays_active=True
        )
    )
    created = await _create_and_post(harness, key="active-end")
    events = [event async for event in harness.proxy.stream_events(created["id"])]
    snapshot = await harness.proxy.get_session(created["id"])

    assert events == [{"session": {"status": "running"}}]
    assert snapshot["status"] == "running"


async def test_optional_capability_absence_degrades_without_hiding_required_failure(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    optional = await bridge_harness(
        scenario=FakeOmnigentScenario(absent_routes=("workspace_files",))
    )
    created = await _create_and_post(optional, key="absent-optional")
    harvested = await optional.proxy.harvest_session(created["id"])
    assert harvested["status"] == "completed_with_diagnostics"

    required = await bridge_harness(
        scenario=FakeOmnigentScenario(absent_routes=("create_session",))
    )
    with pytest.raises(OmnigentBridgeError) as excinfo:
        await required.proxy.create_session(
            request=_request(), binding=_binding("absent-required")
        )
    assert excinfo.value.status_code == 404


async def test_response_before_posted_persistence_is_ambiguous_and_never_retried(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness(
        scenario=FakeOmnigentScenario(post_event_response_then_disconnect=True)
    )
    key = "ambiguous-post"
    created = await harness.proxy.create_session(request=_request(), binding=_binding(key))
    await harness.store.mark_prepared(key, digest="sha256:first", marker="m")
    await harness.store.mark_posting(key)

    with pytest.raises(OmnigentBridgeError):
        await harness.proxy.post_event(
            session_id=created["id"],
            event=BridgeSessionEventRequest(type="message", text="hello"),
        )

    row = await harness.store.get_existing(key)
    assert row is not None and row.first_message_state == FIRST_MESSAGE_POSTING
    assert len(harness.running.server.session_ids) == 1
    assert len(harness.running.server.events) == 1


async def test_incremental_restart_overlap_and_terminalization_are_append_only(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    """A recreated boundary resumes after committed events without rewriting them."""
    harness = await bridge_harness()
    created = await _create_and_post(harness, key="incremental-restart")
    durable = await harness.store.get_existing("incremental-restart")
    assert durable is not None

    committed = await harness.store.append_events(
        durable.bridge_session_id,
        [
            {"id": "provider-1", "type": "assistant.delta", "delta": "one"},
            {"id": "provider-2", "type": "assistant.delta", "delta": "two"},
        ],
    )
    original_identity = [(row.event_id, row.sequence) for row in committed]

    restarted_store = OmnigentBridgeSessionStore(harness.store._session_factory)
    appended = await restarted_store.append_events(
        durable.bridge_session_id,
        [
            {"id": "provider-2", "type": "assistant.delta", "delta": "two"},
            {
                "id": "provider-3",
                "type": "response.completed",
                "normalizedStatus": "completed",
            },
        ],
    )
    terminal_page = await restarted_store.list_event_page(
        durable.bridge_session_id, after=0, limit=100
    )

    assert [(row.event_id, row.sequence) for row in terminal_page.rows[:2]] == original_identity
    assert len(appended) == 1
    assert [row.sequence for row in terminal_page.rows] == [1, 2, 3]
    assert terminal_page.rows[-1].normalized_status == "completed"


async def test_pathological_resource_paths_fail_before_upstream_access(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    harness = await bridge_harness()
    created = await harness.proxy.create_session(
        request=_request(), binding=_binding("unsafe-resource-path")
    )
    calls_before = len(harness.running.server.route_calls)

    for path in ("../secret", "/absolute", "src/%2e%2e/secret", "src\\..\\secret"):
        with pytest.raises(OmnigentBridgeError):
            await harness.proxy.get_resource(
                "workspace_file", created["id"], path
            )

    assert len(harness.running.server.route_calls) == calls_before


async def test_deleted_provider_binding_cannot_be_reused_or_reach_upstream(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    """Deletion clears durable ownership before later ID-bearing requests."""
    harness = await bridge_harness()
    created = await _create_and_post(harness, key="deleted-binding")

    await harness.proxy.delete_session(created["id"])
    deleted = await harness.store.get_existing("deleted-binding")
    calls_after_delete = len(harness.running.server.route_calls)

    assert deleted is not None
    assert deleted.omnigent_session_id is None
    assert await harness.proxy.get_session_owner(created["id"]) is None
    # The owner lookup is entirely durable and must not enumerate the deleted
    # provider ID by consulting the fake upstream.
    assert len(harness.running.server.route_calls) == calls_after_delete


async def test_failure_evidence_surfaces_are_collectively_secret_free(
    bridge_harness: Callable[..., Awaitable[BridgeHarness]],
) -> None:
    """Scan durable/public-shaped evidence, not only the raised exception text."""
    harness = await bridge_harness()
    created = await harness.proxy.create_session(
        request=_request(), binding=_binding("secret-scan")
    )
    durable = await harness.store.get_existing("secret-scan")
    assert durable is not None
    await harness.store.append_events(
        durable.bridge_session_id,
        [
            {
                "id": "secret-shaped-provider-frame",
                "type": "diagnostic",
                "metadata": {
                    "authorization": "Bearer ghp_not-a-real-token",
                    "cookie": "session=fake",
                    "nested": {"password": "fake-password"},
                },
                "text": "token=fake-token",
            }
        ],
    )
    page = await harness.store.list_event_page(
        durable.bridge_session_id, after=0, limit=100
    )
    refreshed = await harness.store.get_existing("secret-scan")

    surfaces = {
        "databaseEventMetadata": [row.metadata_ for row in page.rows],
        "normalizedJournal": [
            {
                "eventId": row.event_id,
                "textPreview": row.text_preview,
                "artifactRef": row.artifact_ref,
            }
            for row in page.rows
        ],
        "diagnostics": refreshed.diagnostics_ref if refreshed else None,
        "apiProjection": [
            {"sequence": row.sequence, "status": row.normalized_status}
            for row in page.rows
        ],
        "sseProjection": [row.event_type for row in page.rows],
    }
    serialized = str(surfaces)
    for prohibited in ("ghp_", "Bearer ", "fake-password", "fake-token", "session=fake"):
        assert prohibited not in serialized
