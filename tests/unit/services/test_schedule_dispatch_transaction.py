"""Schedule projection cleanup preserves the dispatch owner's original failure."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services.recurring_workflows_service import RecurringWorkflowsService

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("phase", ["lock", "dispatch"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_dispatch_failure_survives_transaction_cleanup_failure(
    monkeypatch, phase, cancel
):
    primary = (
        asyncio.CancelledError("dispatch cancelled")
        if cancel
        else RuntimeError("Temporal acknowledgement lost")
    )
    cleanup = ConnectionError("transaction release failed")
    session = SimpleNamespace(commit=AsyncMock(side_effect=cleanup))
    service = RecurringWorkflowsService(session, temporal_client_adapter=object())
    definition = SimpleNamespace(id="schedule", version=3)
    lock = (
        AsyncMock(side_effect=primary)
        if phase == "lock"
        else AsyncMock(return_value=definition)
    )
    monkeypatch.setattr(service, "_lock_definition_for_update", lock)

    with pytest.raises(type(primary)) as caught:
        async with service._dispatch_committed_definition("schedule", 3) as current:
            assert current is definition
            raise primary

    assert caught.value is primary
    assert any("ConnectionError" in note for note in primary.__notes__)
    session.commit.assert_awaited_once()
    lock.assert_awaited_once_with("schedule")


async def test_successful_dispatch_does_not_hide_transaction_release_failure(
    monkeypatch,
):
    cleanup = ConnectionError("transaction release failed")
    session = SimpleNamespace(commit=AsyncMock(side_effect=cleanup))
    service = RecurringWorkflowsService(session, temporal_client_adapter=object())
    definition = SimpleNamespace(id="schedule", version=3)
    monkeypatch.setattr(
        service, "_lock_definition_for_update", AsyncMock(return_value=definition)
    )
    with pytest.raises(ConnectionError) as caught:
        async with service._dispatch_committed_definition("schedule", 3) as current:
            assert current is definition
    assert caught.value is cleanup
    session.commit.assert_awaited_once()


async def test_superseded_dispatch_releases_fence_without_projecting_stale_state(
    monkeypatch,
):
    session = SimpleNamespace(commit=AsyncMock())
    service = RecurringWorkflowsService(session, temporal_client_adapter=object())
    definition = SimpleNamespace(id="schedule", version=4)
    monkeypatch.setattr(
        service, "_lock_definition_for_update", AsyncMock(return_value=definition)
    )
    async with service._dispatch_committed_definition("schedule", 3) as current:
        assert current is None
    session.commit.assert_awaited_once()
