"""Durable embedded bridge recovery coverage for GitHub issue #3370.

These tests deliberately recreate the embedded facade around the same database
store.  That is the API-process restart boundary: live sockets disappear while
the bridge row remains the authority for assignment, prompt state, terminal
evidence, and historical reads.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.bridge_config import parse_bridge_config
from moonmind.omnigent.bridge_embedded import (
    EmbeddedHostAuthContext,
    EmbeddedHostSessionEventRequest,
    OmnigentEmbeddedHostProtocolFacade,
)
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
)
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_POSTED,
    OmnigentBridgeSessionStore,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _config():
    return parse_bridge_config(
        {
            "enabled": True,
            "hostProtocolMode": "embedded_omnigent_compatible_server",
            "hostConnection": {
                "embedded": {
                    "enabled": True,
                    "proxyConformanceEvidenceRef": "artifact://proxy-conformance",
                    "liveSmokeEvidenceRef": "artifact://stock-host-smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://host-auth",
                }
            },
        }
    )


def _request(key: str = "issue-3370-recovery") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="mm:issue-3370",
        idempotencyKey=key,
    )


def _binding(key: str = "issue-3370-recovery") -> BridgePrincipalBinding:
    return BridgePrincipalBinding(
        workflow_id="mm:issue-3370",
        correlation_id="mm:issue-3370",
        idempotency_key=key,
        agent_run_id="run-3370",
    )


@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/embedded-recovery.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentBridgeSessionStore(factory)
    await engine.dispose()


async def _assigned_session(store: OmnigentBridgeSessionStore, *, key: str) -> None:
    await store.bind_profile_authorization(
        request=_request(key),
        endpoint_ref="embedded",
        provider_profile_id="profile-3370",
        provider_lease_id="provider-lease-3370",
        credential_generation=7,
        host_binding_ref="binding-3370",
        host_lease_ref="host-lease-3370",
        omnigent_host_id="host-3370",
    )
    await store.get_or_create(
        request=_request(key),
        endpoint_ref="embedded",
        agent_id="agent-codex",
        agent_name="Codex",
        target_metadata={
            "hostProtocolMode": "embedded_omnigent_compatible_server",
            "workspace": "/workspace/repo",
        },
    )
    await store.attach_session(key, f"session-{key}")
    await store.bind_embedded_runner(
        key, host_id="host-3370", runner_id=f"runner-{key}"
    )


async def test_restart_reuses_assignment_and_does_not_duplicate_first_message(store) -> None:
    key = "issue-3370-restart"
    await _assigned_session(store, key=key)
    await store.mark_prepared(key, digest="sha256:prompt", marker="prompt-marker")
    await store.mark_posting(key)

    class RestartedChannels:
        calls: list[dict[str, Any]] = []

        async def post_runner_event(self, **kwargs: Any) -> dict[str, str]:
            self.calls.append(kwargs)
            return {"item_id": "item-after-restart"}

    channels = RestartedChannels()
    restarted = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_config(), host_channels=channels
    )
    event = EmbeddedHostSessionEventRequest(type="message", data={"text": "hello"})
    response = await restarted.post_event(session_id=f"session-{key}", event=event)
    await store.mark_posted(key, response=response)

    # A subsequent API retry recreates the facade but observes the durable row.
    retried = OmnigentEmbeddedHostProtocolFacade(
        run_store=store, config=_config(), host_channels=channels
    )
    reused = await retried.create_session(
        request=BridgeSessionCreateRequest(
            agent_id="agent-codex",
            host_type="external",
            workspace="/workspace/repo",
        ),
        binding=_binding(key),
    )
    row = await store.get_existing(key)

    assert reused["id"] == f"session-{key}"
    assert reused["moonmind"]["reused"] is True
    assert row.first_message_state == FIRST_MESSAGE_POSTED
    assert row.omnigent_host_id == "host-3370"
    assert row.omnigent_runner_id == f"runner-{key}"
    assert len(channels.calls) == 1


async def test_runner_exit_after_disconnect_is_durable_terminal_evidence(store) -> None:
    key = "issue-3370-disconnect"
    await _assigned_session(store, key=key)

    # The runner-exit observation may arrive after the API's live channel state
    # has gone away.  It must terminalize through the durable binding alone.
    await store.record_embedded_runner_exit(
        runner_id=f"runner-{key}",
        error="runner crashed token=must-not-persist",
    )
    row = await store.get_existing(key)
    events = await store.list_events(row.bridge_session_id)

    assert row.status == "failed"
    assert row.terminal_refs["failureClass"] == "execution_error"
    assert "must-not-persist" not in repr(row.metadata_)
    assert events[-1].normalized_status == "failed"
    assert events[-1].event_type == "terminal"
    assert events[-1].metadata_["code"] == "embedded_runner_exited"


async def test_terminal_embedded_history_survives_proxy_rollback(store) -> None:
    key = "issue-3370-rollback"
    await _assigned_session(store, key=key)
    facade = OmnigentEmbeddedHostProtocolFacade(run_store=store, config=_config())
    await facade.ingest_session_event(
        host_id="host-3370",
        session_id=f"session-{key}",
        request=EmbeddedHostSessionEventRequest(
            type="response.completed", data={"response": {"status": "completed"}}
        ),
        auth=EmbeddedHostAuthContext(
            auth_mode="upstream_runner_tunnel",
            protocol_profile="omnigent.host.v0.1.0",
            runner_id="host-3370",
            credential_generation=7,
        ),
    )

    # Terminal embedded sessions no longer own the active mode, but their
    # canonical event projection remains readable after selecting proxy mode.
    assert await store.active_host_protocol_modes() == {}
    row = await store.get_existing(key)
    page = await store.list_event_page(row.bridge_session_id, after=0, limit=20)

    assert row.metadata_["hostProtocolMode"] == "embedded_omnigent_compatible_server"
    assert row.status == "completed"
    assert page.rows[-1].normalized_status == "completed"
    assert page.rows[-1].metadata_["embeddedNormalizedEvent"]["eventType"] == (
        "response.completed"
    )
