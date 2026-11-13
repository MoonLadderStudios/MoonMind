from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from api_service import main as api_main
from moonmind.workflows.temporal.client import TemporalClientAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError])
async def test_janitor_registration_failure_is_visible_and_retryable(
    monkeypatch, caplog, error_type
):
    register = AsyncMock(
        side_effect=[
            error_type("private-provider-detail"),
            "omnigent-oauth-host-janitor",
        ]
    )
    monkeypatch.setattr(
        TemporalClientAdapter, "ensure_omnigent_oauth_host_janitor_schedule", register
    )
    monkeypatch.setattr(
        api_main.app.state, "omnigent_host_janitor_schedule_ready", True, raising=False
    )

    await api_main.ensure_omnigent_oauth_host_janitor_schedule_started()

    assert api_main.app.state.omnigent_host_janitor_schedule_ready is False
    assert "host janitor schedule registration deferred" in caplog.text
    assert "private-provider-detail" not in caplog.text

    await api_main.ensure_omnigent_oauth_host_janitor_schedule_started()

    assert api_main.app.state.omnigent_host_janitor_schedule_ready is True
    assert register.await_count == 2
    register.assert_awaited_with(enabled=True)


@pytest.mark.asyncio
async def test_schedule_observer_retries_janitor_until_registered(monkeypatch):
    from api_service.services import recurring_workflows_service

    register = AsyncMock(side_effect=[RuntimeError("Temporal unavailable"), "janitor"])
    monkeypatch.setattr(
        TemporalClientAdapter, "ensure_omnigent_oauth_host_janitor_schedule", register
    )
    monkeypatch.setattr(
        api_main.app.state, "omnigent_host_janitor_schedule_ready", False, raising=False
    )
    monkeypatch.setattr(
        api_main.app.state, "github_claim_recovery_schedule_ready", True, raising=False
    )

    @asynccontextmanager
    async def session_context():
        yield object()

    reconcile = AsyncMock()

    class RecurringWorkflowsService:
        def __init__(self, session):
            pass

        reconcile_manual_runs = reconcile

    monkeypatch.setattr(api_main, "get_async_session_context", session_context)
    monkeypatch.setattr(
        recurring_workflows_service,
        "RecurringWorkflowsService",
        RecurringWorkflowsService,
    )
    delays = []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(api_main.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await api_main._observe_manual_schedule_requests()

    assert register.await_count == 2
    assert api_main.app.state.omnigent_host_janitor_schedule_ready is True
    assert reconcile.await_count == 3
    assert delays == [30, 30, 30]


@pytest.mark.asyncio
async def test_unresponsive_janitor_registration_is_deferred(monkeypatch):
    started = asyncio.Event()

    async def register(self, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        TemporalClientAdapter, "ensure_omnigent_oauth_host_janitor_schedule", register
    )
    monkeypatch.setattr(
        api_main.app.state, "omnigent_host_janitor_schedule_ready", True, raising=False
    )
    timeout = asyncio.timeout
    deadlines = []

    def immediate_timeout(seconds):
        deadlines.append(seconds)
        return timeout(0)

    monkeypatch.setattr(api_main.asyncio, "timeout", immediate_timeout)

    await asyncio.wait_for(
        api_main.ensure_omnigent_oauth_host_janitor_schedule_started(), timeout=1
    )

    assert started.is_set()
    assert api_main.app.state.omnigent_host_janitor_schedule_ready is False
    assert deadlines == [25]


@pytest.mark.asyncio
async def test_janitor_registration_propagates_shutdown(monkeypatch):
    monkeypatch.setattr(
        TemporalClientAdapter,
        "ensure_omnigent_oauth_host_janitor_schedule",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await api_main.ensure_omnigent_oauth_host_janitor_schedule_started()
