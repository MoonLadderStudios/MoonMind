"""Unit tests for the embedded Omnigent-compatible host facade (MM-1164)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    parse_bridge_config,
)
from moonmind.omnigent.bridge_embedded import (
    EmbeddedHostAuthContext,
    EmbeddedHostHeartbeatRequest,
    EmbeddedHostRegisterRequest,
    EmbeddedHostSessionEventRequest,
    OmnigentEmbeddedHostProtocolFacade,
    verify_embedded_host_auth,
)
from moonmind.omnigent.bridge_proxy import OmnigentBridgeError
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/embedded.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentBridgeSessionStore(session_maker)
    await engine.dispose()


def _embedded_config():
    return parse_bridge_config(
        {
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://omnigent/proxy-conformance",
                    "liveSmokeEvidenceRef": "artifact://omnigent/live-smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://omnigent/host-auth",
                }
            },
        }
    )


def _request():
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="mm:wf-embedded",
        idempotencyKey="idem-embedded",
    )


def test_embedded_host_auth_accepts_bearer_token() -> None:
    context = verify_embedded_host_auth(
        headers={"Authorization": "Bearer runner-token"},
        config=_embedded_config(),
        configured_token="runner-token",
    )

    assert context.auth_mode == "header_or_token"
    assert context.protocol_profile == "omnigent.host_runner.v1"


def test_embedded_host_auth_rejects_missing_or_wrong_token() -> None:
    with pytest.raises(OmnigentBridgeError) as excinfo:
        verify_embedded_host_auth(
            headers={"X-Omnigent-Host-Token": "wrong"},
            config=_embedded_config(),
            configured_token="runner-token",
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.failure_class == "user_error"


@pytest.mark.asyncio
async def test_register_and_heartbeat_return_embedded_bridge_shape(store) -> None:
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )
    auth = EmbeddedHostAuthContext(
        auth_mode="header_or_token",
        protocol_profile="omnigent.host_runner.v1",
    )

    registered = await facade.register_host(
        request=EmbeddedHostRegisterRequest(hostId="host-1", runnerId="runner-1"),
        auth=auth,
    )
    heartbeat = await facade.heartbeat(
        host_id="host-1",
        request=EmbeddedHostHeartbeatRequest(status="running"),
        auth=auth,
    )

    assert registered["status"] == "registered"
    assert heartbeat["moonmind"]["hostProtocolMode"] == HOST_PROTOCOL_MODE_EMBEDDED


@pytest.mark.asyncio
async def test_embedded_session_events_append_to_same_bridge_event_model(store) -> None:
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="embedded",
        agent_id="agent-1",
        agent_name="Codex",
        target_metadata={"hostType": "external", "workspace": "/workspace/repo"},
        workflow_id="mm:wf-embedded",
        agent_run_id="run-embedded",
    )
    await store.attach_session("idem-embedded", "sess-embedded")
    facade = OmnigentEmbeddedHostProtocolFacade(
        run_store=store,
        config=_embedded_config(),
    )

    response = await facade.ingest_session_event(
        host_id="host-1",
        session_id="sess-embedded",
        request=EmbeddedHostSessionEventRequest(
            type="response.delta",
            data={"text": "hello"},
        ),
        auth=EmbeddedHostAuthContext(
            auth_mode="header_or_token",
            protocol_profile="omnigent.host_runner.v1",
        ),
    )
    events = await store.list_events(row.bridge_session_id)

    assert response["accepted"] == 1
    assert response["bridgeSessionId"] == row.bridge_session_id
    assert len(events) == 1
    assert events[0].event_type == "response.delta"
    assert events[0].normalized_status == "running"
    assert events[0].metadata_["moonmind"]["source"] == "omnigent_stream"
