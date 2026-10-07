"""Canonical session creation consumes bridge-owned launch authority."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentBridgeSession
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities import omnigent_session_activities


class _WorkerStopped(BaseException):
    """Stop just after the provider accepted creation, before attachment."""


@pytest.mark.asyncio
@pytest.mark.parametrize("permission_mode", ["bypassPermissions", "plan"])
@pytest.mark.parametrize("already_attached", [False, True])
async def test_canonical_create_replays_frozen_launch_defaults(
    monkeypatch, tmp_path, permission_mode, already_attached
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("api_service.db.base.async_session_maker", sessions)
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="mm:wf",
        idempotencyKey="mm:wf:step-1",
        parameters={
            "omnigent": {
                "agent": {"agentId": "agent-1", "harnessOverride": "claude-native"},
                "session": {
                    "hostType": "external",
                    "hostId": "host-1",
                    "workspace": "/workspaces/run",
                    "terminalLaunchArgs": ["--verbose"],
                },
            }
        },
    )
    store = OmnigentBridgeSessionStore(sessions)
    bridge = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name=None,
        target_metadata={"workspace": "/workspaces/run"},
    )
    async with sessions() as db:
        row = await db.get(OmnigentBridgeSession, bridge.bridge_session_id)
        row.metadata_ = {
            **row.metadata_,
            "workflowLaunchDefaults": {
                "claude-native": ["--permission-mode", permission_mode]
            },
        }
        await db.commit()
    if already_attached:
        await store.attach_session(request.idempotency_key, "existing-session")
    canonical = SimpleNamespace(
        session_id="oms_123",
        provider_session_ref="existing-session" if already_attached else None,
        revision=1,
        execution_plan_ref=None,
        metadata={
            "omnigentHostRef": "host-1",
            "bridgeSessionRef": bridge.bridge_session_id,
        },
    )

    class ControlStore:
        def __init__(self, _sessions):
            pass

        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(
                sessions=SimpleNamespace(get=AsyncMock(return_value=canonical))
            )

    monkeypatch.setattr(
        "moonmind.omnigent.control_plane.OmnigentControlPlaneStore", ControlStore
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_claim_command",
        AsyncMock(return_value=(SimpleNamespace(status="claimed"), True)),
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_load_intent_request",
        AsyncMock(return_value=request),
    )
    settle = AsyncMock(return_value={"outcome": "completed"})
    monkeypatch.setattr(omnigent_session_activities, "_settle_command", settle)
    payloads = []

    class Client:
        async def list_agents(self):
            return [{"id": "agent-1", "name": "Claude"}]

        async def create_session(self, payload):
            settle.assert_not_awaited()
            assert payload["terminal_launch_args"] == [
                "--verbose",
                "--permission-mode",
                permission_mode,
            ]
            assert payload["idempotency_key"] == "oms_123"
            payloads.append(payload)
            raise _WorkerStopped()

    http_client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        omnigent_session_activities,
        "_omnigent_client_context",
        AsyncMock(return_value=(http_client, Client())),
    )
    activity_input = {
        "sessionId": "oms_123",
        "compiledExecutionIntentRef": "art_intent_123",
        "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
        "expectedRevision": 1,
        "fencingGeneration": 1,
    }
    try:
        if already_attached:
            result = await omnigent_session_activities.omnigent_ensure_provider_session_activity(
                activity_input
            )
            assert result == {"outcome": "completed", "revision": 1}
            assert payloads == []
            settle.assert_awaited_once()
            http_client.aclose.assert_not_awaited()
            return
        for _ in range(2):
            with pytest.raises(_WorkerStopped):
                await omnigent_session_activities.omnigent_ensure_provider_session_activity(
                    activity_input
                )
        assert payloads[0] == payloads[1]
        assert (
            await store.get_existing(request.idempotency_key)
        ).omnigent_session_id is None
        settle.assert_not_awaited()
        assert http_client.aclose.await_count == 2
    finally:
        await engine.dispose()
