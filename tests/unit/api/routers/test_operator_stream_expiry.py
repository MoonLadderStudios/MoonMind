"""Live operator streams expire even while busy or waiting for upstream data."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service import auth_providers
from api_service.api.routers import agent_runs
from api_service.db.base import get_async_session
from api_service.services import single_user_conversion
from moonmind.security.operator_admission import STREAM_REVALIDATION_SECONDS
from tests.unit.api.routers.test_omnigent_bridge import (
    OMNIGENT_BRIDGE_MOUNT_PATH,
    _build,
)

HEADERS = {"X-Moonmind-User": "asserted-operator"}


@pytest.fixture
def streams(monkeypatch):
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "testclient")
    monkeypatch.delenv("MOONMIND_PROXY_IDENTITY_HEADER", raising=False)
    client, proxy, store = _build()
    client.app.dependency_overrides.pop(auth_providers.get_instance_access)
    client.app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    monkeypatch.setattr(
        single_user_conversion, "completed_guarded_upgrade",
        AsyncMock(return_value=object()),
    )
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(auth_providers, "monotonic", lambda: clock.now, raising=False)
    return SimpleNamespace(client=client, proxy=proxy, store=store, clock=clock)


def test_busy_upstream_stream_expires_and_reconnect_requires_admission(streams):
    closed = []

    async def events(*args, **kwargs):
        try:
            yield {"sequence": 1}
            streams.clock.now += STREAM_REVALIDATION_SECONDS
            yield {"sequence": 2}
        finally:
            closed.append(True)

    streams.proxy.stream_events = events
    path = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/stream"
    response = streams.client.get(path, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert "id: 1\n" in response.text
    assert "id: 2\n" not in response.text
    assert closed == [True]
    assert streams.proxy.posted_events == []
    assert streams.client.get(path).status_code == 401
    renewed = streams.client.get(path, headers=HEADERS)
    assert "id: 1\n" in renewed.text


def test_projection_stream_does_not_emit_after_admission_expires(streams):
    streams.store._session_overrides["status"] = "completed"
    original = streams.store.get_bridge_session
    calls = 0

    async def get_session(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls >= 1:
            streams.clock.now += STREAM_REVALIDATION_SECONDS
        return await original(*args, **kwargs)

    streams.store.get_bridge_session = get_session
    response = streams.client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/brs-1/stream", headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert "event: bridge_event\n" in response.text
    assert "event: terminal\n" not in response.text


def test_busy_live_logs_expire_and_stop_reader(streams, monkeypatch, tmp_path):
    streams.client.app.include_router(agent_runs.router, prefix="/api")
    record = SimpleNamespace(
        workflow_id="mm:w1", status="running", live_stream_capable=True,
        workspace_path=str(tmp_path),
    )
    monkeypatch.setattr(agent_runs, "_load_managed_run_record", AsyncMock(return_value=record))
    monkeypatch.setattr(agent_runs, "_get_agent_runtime_store_root", lambda: tmp_path)
    stopped = []

    class Reader:
        def __init__(self, **kwargs):
            pass

        async def follow(self, **kwargs):
            yield SimpleNamespace(model_dump_json=lambda **_: '{"sequence":1}')
            streams.clock.now += STREAM_REVALIDATION_SECONDS
            yield SimpleNamespace(model_dump_json=lambda **_: '{"sequence":2}')

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(agent_runs, "SpoolLogReader", Reader)
    response = streams.client.get("/api/agent-runs/run-1/logs/stream", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert '"sequence":1' in response.text
    assert '"sequence":2' not in response.text
    assert stopped == [True]
    assert record.status == "running"


def test_idle_upstream_stream_expires_and_closes_source(streams, monkeypatch):
    # A silent upstream must close too; checking only emitted chunks is insufficient.
    monkeypatch.setattr(auth_providers, "STREAM_REVALIDATION_SECONDS", 0.02, raising=False)
    closed = []

    async def events(*args, **kwargs):
        try:
            yield {"sequence": 1}
            await asyncio.sleep(0.1)
            yield {"sequence": 2}
        finally:
            closed.append(True)

    streams.proxy.stream_events = events
    response = streams.client.get(
        f"{OMNIGENT_BRIDGE_MOUNT_PATH}/v1/sessions/sess-77/stream", headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert "id: 1\n" in response.text
    assert "id: 2\n" not in response.text
    assert closed == [True]
