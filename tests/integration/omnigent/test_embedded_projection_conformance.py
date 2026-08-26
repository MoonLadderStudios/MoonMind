"""Proxy/embedded projection conformance for MoonLadderStudios/MoonMind#3370.

The two transports are intentionally different, but their durable Workflow
Detail evidence is one contract.  These fixtures exercise the production
normalizer/store boundary for proxy observations and the embedded facade for
unchanged-host observations, then compare only MoonMind-facing projections.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base, OmnigentBridgeSession
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    parse_bridge_config,
)
from moonmind.omnigent.bridge_embedded import (
    EmbeddedHostAuthContext,
    EmbeddedHostSessionEventRequest,
    OmnigentEmbeddedHostProtocolFacade,
)
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from tests.helpers.omnigent_conformance import (
    FakeOmnigentServer,
    start_fake_omnigent_server,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/conformance.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
def store(session_factory):
    return OmnigentBridgeSessionStore(session_factory)


def _request(key: str) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="mm:wf-projection-conformance",
        idempotencyKey=key,
    )


def _config():
    return parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy",
                    "liveSmokeEvidenceRef": "artifact://omnigent/live",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/auth",
                }
            },
        }
    )


def _event_projection(event: Any) -> dict[str, Any]:
    """Select the fields exposed by the common event-page/SSE contract."""

    return {
        "sequence": event.sequence,
        "eventType": event.event_type,
        "normalizedStatus": event.normalized_status,
        "direction": event.direction,
        "textPreview": event.text_preview,
        "moonmind": event.metadata_["moonmind"],
    }


async def _session(store: OmnigentBridgeSessionStore, key: str, session_id: str):
    row = await store.get_or_create(
        request=_request(key),
        endpoint_ref="embedded" if key == "embedded" else "proxy",
        agent_id="agent-1",
        agent_name="Codex",
        target_metadata={"workspace": "/workspace/repo"},
        workflow_id="mm:wf-projection-conformance",
        agent_run_id="run-conformance",
    )
    await store.attach_session(key, session_id)
    return row


async def test_proxy_and_embedded_events_have_equivalent_public_projections(
    store, session_factory,
) -> None:
    proxy = await _session(store, "proxy", "proxy-session")
    embedded = await _session(store, "embedded", "embedded-session")
    async with session_factory() as session:
        persisted = await session.get(OmnigentBridgeSession, embedded.bridge_session_id)
        persisted.omnigent_host_id = "host-1"
        await session.commit()

    observations = (
        {"type": "session.started", "data": {}},
        {"type": "response.delta", "data": {"text": "same output"}},
        {
            "type": "response.completed",
            "data": {"outputRefs": ["artifact://omnigent/result"]},
        },
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="upstream_runner_tunnel",
        protocol_profile="omnigent.runner_tunnel.7da32637",
        runner_id="host-1",
        credential_generation=1,
    )
    facade = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_config())

    for sequence, payload in enumerate(observations, start=1):
        normalized = build_omnigent_bridge_event(
            payload={**payload, "direction": "host_to_moonmind"},
            sequence=sequence,
            request=_request("proxy"),
            omnigent_session_id="proxy-session",
            bridge_session_id=proxy.bridge_session_id,
        ).event
        await store.append_events(proxy.bridge_session_id, [normalized])
        await facade.ingest_session_event(
            host_id="host-1",
            session_id="embedded-session",
            request=EmbeddedHostSessionEventRequest(**payload),
            auth=auth,
        )

    proxy_events = await store.list_events(proxy.bridge_session_id)
    embedded_events = await store.list_events(embedded.bridge_session_id)
    assert [_event_projection(event) for event in proxy_events] == [
        _event_projection(event) for event in embedded_events
    ]

    proxy_terminal = await store.get_existing("proxy")
    embedded_terminal = await store.get_existing("embedded")
    assert proxy_terminal.status == embedded_terminal.status == "completed"
    assert (
        proxy_terminal.first_message_state == embedded_terminal.first_message_state
    )

    snapshot = await facade.get_session("embedded-session")
    replay = [event async for event in facade.stream_events("embedded-session", after=1)]
    assert snapshot["hostId"] == "host-1"
    assert snapshot["terminal"] is True
    assert [event["sequence"] for event in replay] == [2, 3, 3]
    assert replay[-1]["type"] == "terminal"


class _ResourceChannel:
    def __init__(self) -> None:
        self.responses = {
            "/v1/sessions/embedded-session/resources/environments/default/changes": {
                "items": [{"path": "src/app.py"}]
            },
            "/v1/sessions/embedded-session/resources/environments/default/filesystem": {
                "items": [
                    {"path": "README.md", "type": "file"},
                    {"path": "src/app.py", "type": "file"},
                    {"path": "src", "type": "directory"},
                ]
            },
            "/v1/sessions/embedded-session/resources/environments/default/filesystem/src/app.py": b"print('fake')\n",
            "/v1/sessions/embedded-session/resources/environments/default/diff/src/app.py": b"diff --git a/src/app.py b/src/app.py\n",
            "/v1/sessions/embedded-session/resources/files": {
                "items": [{"id": "file-1", "filename": "session.log"}]
            },
            "/v1/sessions/embedded-session/resources/files/file-1/content": b"session file evidence\n",
        }

    async def request_runner(
        self, *, runner_id: str, method: str, path: str, payload=None, expect_json=True
    ):
        assert runner_id == "runner-1"
        assert method == "GET"
        return self.responses[path]


async def test_embedded_resources_share_the_canonical_proxy_shapes(
    store, session_factory,
) -> None:
    row = await _session(store, "embedded", "embedded-session")
    async with session_factory() as session:
        persisted = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        persisted.omnigent_host_id = "host-1"
        persisted.omnigent_runner_id = "runner-1"
        await session.commit()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_config(), host_channels=_ResourceChannel()
    )
    running = await start_fake_omnigent_server(FakeOmnigentServer())
    proxy = OmnigentHttpClient(base_url=running.base_url)
    try:
        assert await facade.get_resource(
            "changed_files", "embedded-session"
        ) == await proxy.list_changed_files("proxy-session")
        assert await facade.get_resource(
            "workspace_files", "embedded-session"
        ) == await proxy.list_workspace_files("proxy-session")
        assert await facade.get_resource(
            "workspace_file", "embedded-session", "src/app.py"
        ) == await proxy.get_workspace_file("proxy-session", "src/app.py")
        assert await facade.get_resource(
            "workspace_diff", "embedded-session", "src/app.py"
        ) == await proxy.get_workspace_diff("proxy-session", "src/app.py")
        assert await facade.get_resource(
            "session_files", "embedded-session"
        ) == await proxy.list_session_files("proxy-session")
        assert await facade.get_resource(
            "session_file", "embedded-session", "file-1"
        ) == await proxy.get_session_file_content("proxy-session", "file-1")
    finally:
        await running.runner.cleanup()


@pytest.mark.parametrize(
    ("terminal_status", "terminal_state"),
    [("completed", "stopped"), ("failed", "failed"), ("canceled", "stopped")],
)
async def test_workflow_detail_terminal_envelope_projects_embedded_lifecycle_outcomes(
    store, terminal_status, terminal_state,
) -> None:
    """Workflow Detail receives bounded lifecycle rows plus terminal refs."""

    row = await _session(store, "embedded", "embedded-session")
    await store.bind_profile_authorization(
        request=_request("embedded"), endpoint_ref="embedded",
        provider_profile_id="profile-1", provider_lease_id="provider-lease-1",
        credential_generation=1, host_binding_ref="binding-1",
        host_lease_ref="host-lease-1", omnigent_host_id="host-1",
    )
    await store.begin_embedded_runner_launch(
        "embedded", host_id="host-1", runner_id="runner-1", generation=1000001,
        credential_generation=1, launch_generation=1,
    )
    await store.mark_embedded_runner_state(
        "embedded", state="launch_sent", code="host_launch_command_sending"
    )
    await store.mark_embedded_runner_state(
        "embedded", state="launch_acknowledged", code="host_launch_acknowledged"
    )
    await store.bind_embedded_runner(
        "embedded", host_id="host-1", runner_id="runner-1"
    )
    await store.mark_embedded_runner_state(
        "embedded", state="runner_tunnel_ready", code="bounded_runner_reconnect_verified"
    )
    await store.mark_prepared("embedded", digest="digest-1", marker="marker-1")
    await store.mark_posting("embedded")
    await store.mark_posted(
        "embedded", response={"pending_id": "pending-1", "item_id": "item-1"}
    )
    terminal_refs = {
        "outputRefs": ["artifact://omnigent/output"],
        "diagnosticsRef": "artifact://omnigent/diagnostics",
        "cleanupState": "completed",
        "hostLeaseOutcome": "released_after_host_stop",
    }
    await store.record_lifecycle_event(
        "embedded", event_type="cleanup", status="running",
        event_identity=f"cleanup:{terminal_status}", code="runner_cleanup_started",
        metadata={"hostStopped": True, "providerLeaseReleased": True},
    )
    await store.mark_terminal(
        "embedded", status=terminal_status, terminal_refs=terminal_refs
    )

    projected = await store.get_existing("embedded")
    lifecycle = projected.metadata_["embedded_runner_lifecycle"]
    events = await store.list_events(row.bridge_session_id)
    envelope = {
        "status": projected.status,
        "terminalRefs": projected.terminal_refs,
        "lifecycle": lifecycle,
        "events": [_event_projection(event) for event in events],
    }
    encoded = json.dumps(envelope)

    assert lifecycle["state"] == terminal_state
    states = [item["state"] for item in lifecycle["timeline"]]
    assert "runner_tunnel_waiting" in states
    assert "runner_tunnel_ready" in states
    assert "first_message_posting" in states
    assert "first_message_posted" in states
    assert envelope["terminalRefs"]["cleanupState"] == "completed"
    assert envelope["terminalRefs"]["hostLeaseOutcome"] == "released_after_host_stop"
    assert [event["eventType"] for event in envelope["events"]] == [
        "lifecycle.cleanup"
    ]
    assert "binding_token" not in encoded.lower()
    assert "root-secret" not in encoded
