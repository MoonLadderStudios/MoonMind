"""Proxy/embedded projection conformance for MoonLadderStudios/MoonMind#3370.

The two transports are intentionally different, but their durable Workflow
Detail evidence is one contract.  These fixtures exercise the production
normalizer/store boundary for proxy observations and the embedded facade for
unchanged-host observations, then compare only MoonMind-facing projections.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers.omnigent_bridge import (
    WORKFLOW_CHAT_BINDINGS_MOUNT_PATH,
    _get_bridge_proxy,
    _get_bridge_store,
    _get_create_embedded_facade,
    _get_execution_service,
    _require_bridge_enabled,
    workflow_chat_router,
)
from api_service.api.routers.retrieval_gateway import get_capability_registry
from api_service.auth_providers import get_current_user
from api_service.db.models import Base, OmnigentBridgeSession
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
    parse_bridge_config,
)
from moonmind.omnigent.bridge_embedded import (
    EmbeddedHostAuthContext,
    EmbeddedHostSessionEventRequest,
    OmnigentEmbeddedHostProtocolFacade,
)
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES
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
            (
                "/v1/sessions/embedded-session/resources/environments/default/"
                "filesystem/README.md"
            ): b"# captured\n",
            "/v1/sessions/embedded-session/resources/environments/default/diff/src/app.py": b"diff --git a/src/app.py b/src/app.py\n",
            "/v1/sessions/embedded-session/resources/files": {
                "items": [{"id": "file-1", "filename": "session.log"}]
            },
            "/v1/sessions/embedded-session/resources/files/file-1/content": b"session file evidence\n",
            "/v1/sessions/embedded-session/resources/terminals": {
                "object": "list",
                "data": [
                    {
                        "id": "terminal-main",
                        "session_id": "embedded-session",
                        "status": "exited",
                    }
                ],
                "has_more": False,
            },
            "/v1/sessions/embedded-session/resources/terminals/terminal-main": {
                "id": "terminal-main",
                "session_id": "embedded-session",
                "status": "exited",
                "metadata": {"exit_code": 0},
            },
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
    try:
        async with httpx.AsyncClient(trust_env=False) as http_client:
            proxy = OmnigentHttpClient(
                base_url=running.base_url,
                client=http_client,
            )
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
            embedded_terminals = await facade.list_session_terminals(
                "embedded-session"
            )
            proxy_terminals = await proxy.list_session_terminals("proxy-session")
            assert (
                embedded_terminals["object"]
                == proxy_terminals["object"]
                == "list"
            )
            assert (
                embedded_terminals["has_more"]
                is proxy_terminals["has_more"]
                is False
            )
            assert (
                embedded_terminals["data"][0]["id"]
                == proxy_terminals["data"][0]["id"]
            )
            assert (
                embedded_terminals["data"][0]["session_id"]
                == "embedded-session"
            )
            assert proxy_terminals["data"][0]["session_id"] == "proxy-session"
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


async def test_harvest_cleanup_then_every_historical_facade_resource_is_readable(
    store,
    session_factory,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise capture -> cleanup -> scoped HTTP reads with real persistence."""

    monkeypatch.chdir(tmp_path)
    request = _request("embedded")
    row = await _session(store, "embedded", "embedded-session")
    grants = dict.fromkeys(CAPABILITY_NAMES, True)
    launch = {
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "executionProfileRef": "agent-profile://codex/versions/1",
        "executionProfileDigest": "sha256:agent",
        "launchPolicyRef": "codex-static@1",
        "agentProfileCapabilities": grants,
        "capabilities": grants,
        "sessionStateCapabilities": grants,
        "policyAuthority": {
            "policyId": "codex-static",
            "policyVersion": 1,
            "policyRef": "codex-static@1",
            "policyDigest": "sha256:policy",
            "snapshotRef": "artifact://policy",
            "validation": {"valid": True},
        },
    }
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        host_binding_ref="binding-1",
        host_lease_ref="host-lease-1",
        omnigent_host_id="host-1",
        effective_launch_snapshot=launch,
    )
    created = await store.record_session_created(
        "embedded",
        session_id="embedded-session",
        capabilities=grants,
        session_status="active",
    )
    await store.mark_embedded_runner_state(
        "embedded",
        state="runner_identity_bound",
        code="authenticated_runner_identity_bound",
    )
    await store.mark_embedded_runner_state(
        "embedded", state="runner_tunnel_waiting", code="runner_tunnel_pending"
    )
    await store.mark_embedded_runner_state(
        "embedded", state="runner_tunnel_ready", code="runner_tunnel_authenticated"
    )
    await store.mark_prepared("embedded", digest="digest-1", marker="marker-1")
    await store.mark_posting("embedded")
    await store.mark_posted(
        "embedded", response={"pending_id": "pending-1", "item_id": "item-1"}
    )
    event = build_omnigent_bridge_event(
        payload={
            "type": "response.output",
            "status": "running",
            "text": "captured transcript item",
        },
        sequence=1,
        request=request,
        omnigent_session_id="embedded-session",
        bridge_session_id=created.bridge_session_id,
    ).event
    await store.append_events(created.bridge_session_id, [event])
    async with session_factory() as session:
        persisted = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        persisted.omnigent_host_id = "host-1"
        persisted.omnigent_runner_id = "runner-1"
        await session.commit()

    gateway = LocalOmnigentArtifactGateway()
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_config(),
        host_channels=_ResourceChannel(),
        artifact_gateway=gateway,
    )
    harvested = await facade.harvest_session("embedded-session")
    assert harvested["status"] == "completed"

    # The production cleanup owners remove both live authorities only after the
    # artifact-backed projection and final transcript snapshot are durable.
    await store.mark_terminal("embedded", status="completed")
    await store.record_provider_session_deleted("embedded-session")
    cleaned = await store.record_terminal_cleanup(
        host_lease_ref="host-lease-1",
        completed=True,
        code="host_cleanup_completed",
        lease_released=True,
    )
    assert cleaned is not None
    assert cleaned.final_snapshot_ref
    terminal_group = next(
        group
        for group in cleaned.terminal_refs["resourceProjection"]["groups"]
        if group["groupKey"] == "terminals"
    )
    assert terminal_group["resources"]

    owner_id = uuid4()

    async def describe_execution(_workflow_id: str):
        return SimpleNamespace(owner_id=owner_id)

    app = FastAPI()
    app.include_router(
        workflow_chat_router,
        prefix=WORKFLOW_CHAT_BINDINGS_MOUNT_PATH,
    )
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=owner_id,
        email="owner@example.test",
        is_superuser=False,
    )
    app.dependency_overrides[_get_execution_service] = lambda: SimpleNamespace(
        describe_execution=describe_execution
    )
    app.dependency_overrides[_get_bridge_store] = lambda: store
    app.dependency_overrides[_get_bridge_proxy] = lambda: None
    app.dependency_overrides[_get_create_embedded_facade] = lambda: None
    app.dependency_overrides[get_capability_registry] = lambda: SimpleNamespace(
        has_live_session_authority=lambda *_args, **_kwargs: False,
        revoke_scope=lambda *_args, **_kwargs: [],
    )
    app.dependency_overrides[_require_bridge_enabled] = lambda: SimpleNamespace(
        host_protocol_mode=HOST_PROTOCOL_MODE_PROXY
    )
    binding = created.chat_binding_id
    assert binding
    root = (
        f"{WORKFLOW_CHAT_BINDINGS_MOUNT_PATH}/{binding}/omnigent/"
        f"v1/sessions/{binding}"
    )
    paths = {
        "snapshot": root,
        "transcript": root + "/items",
        "changed": root + "/resources/environments/default/changes",
        "workspace": root + "/resources/environments/default/filesystem",
        "workspace_file": (
            root + "/resources/environments/default/filesystem/src/app.py"
        ),
        "diff": root + "/resources/environments/default/diff/src/app.py",
        "session_files": root + "/resources/files",
        "session_file": root + "/resources/files/file-1/content",
        "terminals": root + "/resources/terminals",
        "terminal": root + "/resources/terminals/terminal-main",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        responses = {name: await client.get(path) for name, path in paths.items()}
        traversal = await client.get(
            root + "/resources/environments/default/filesystem/%252e%252e/secret"
        )
        foreign_binding = await client.get(
            paths["snapshot"].replace(binding, "foreign-binding")
        )
        unknown = await client.get(root + "/unknown-stock-route")

    response_statuses = {
        name: response.status_code for name, response in responses.items()
    }
    assert response_statuses == {name: 200 for name in paths}, {
        name: response.text for name, response in responses.items()
    }
    assert responses["transcript"].json()["data"][0]["text"] == (
        "captured transcript item"
    )
    assert responses["workspace_file"].content == b"print('fake')\n"
    assert responses["diff"].content.startswith(b"diff --git")
    assert responses["session_file"].content == b"session file evidence\n"
    assert responses["terminal"].json()["id"] == "terminal-main"
    assert "embedded-session" not in "".join(response.text for response in responses.values())
    assert traversal.status_code in {403, 404}
    assert foreign_binding.status_code == 404
    assert unknown.status_code == 404

    # Deleting a captured body proves the facade reports a stable unavailable
    # result instead of reviving the provider or falling through upstream.
    session_artifact = next(
        path
        for path in tmp_path.glob(
            "var/artifacts/omnigent/**/output.omnigent.session_files/**/*"
        )
        if path.is_file() and not path.name.endswith(".metadata.json")
    )
    session_artifact.unlink()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        missing = await client.get(paths["session_file"])
    assert missing.status_code == 503
    assert missing.json()["detail"]["code"] == (
        "omnigent_bridge_terminal_evidence_unavailable"
    )

    oversized_ref = await gateway.write_bytes(
        request=request,
        name="output.omnigent.workspace_files/oversized.bin",
        payload=b"bounded fixture",
        link_type="output.omnigent.workspace_file",
    )
    async with session_factory() as session:
        persisted = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        terminal_refs = copy.deepcopy(persisted.terminal_refs)
        workspace_group = next(
            group
            for group in terminal_refs["resourceProjection"]["groups"]
            if group["groupKey"] == "workspace_files"
        )
        workspace_group["resources"].append(
            {
                "path": "oversized.bin",
                "artifactRef": oversized_ref,
                "sizeBytes": 10 * 1024 * 1024 + 1,
                "contentType": "application/octet-stream",
            }
        )
        persisted.terminal_refs = terminal_refs
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        oversized = await client.get(
            root + "/resources/environments/default/filesystem/oversized.bin"
        )
    assert oversized.status_code == 413

    async def describe_as_foreign_owner(_workflow_id: str):
        return SimpleNamespace(owner_id=uuid4())

    app.dependency_overrides[_get_execution_service] = lambda: SimpleNamespace(
        describe_execution=describe_as_foreign_owner
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        unauthorized = await client.get(paths["snapshot"])
    assert unauthorized.status_code == 404
