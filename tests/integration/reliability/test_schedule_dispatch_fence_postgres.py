"""Committed desired schedule versions fence concurrent Temporal dispatch."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import RecurringWorkflowDefinition
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from tests.integration import test_execution_input_admission_postgres as admission

sessions = admission.sessions
conversion_postgres_url = admission.conversion_postgres_url
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def _definition(sessions):
    definition_id = uuid4()
    async with sessions() as session:
        session.add(
            RecurringWorkflowDefinition(
                id=definition_id,
                name="Dispatch ordering",
                enabled=False,
                cron="0 * * * *",
                timezone="UTC",
                target={},
                policy={},
                version=1,
            )
        )
        await session.commit()
    return definition_id


def _adapter(update):
    return SimpleNamespace(
        update_schedule=AsyncMock(side_effect=update),
        pause_schedule=AsyncMock(),
        unpause_schedule=AsyncMock(),
    )


async def _edit(sessions, definition_id, adapter, enabled, *, delayed=False):
    async with sessions() as session:
        session.info["delayed_dispatch"] = delayed
        definition = await session.get(RecurringWorkflowDefinition, definition_id)
        return await RecurringWorkflowsService(
            session, temporal_client_adapter=adapter
        ).update_definition(definition, enabled=enabled)


async def test_older_dispatch_holds_committed_version_fence_through_rpc(sessions):
    definition_id = await _definition(sessions)
    entered, release = asyncio.Event(), asyncio.Event()
    remote = []

    async def update(**kwargs):
        if kwargs["enabled"]:
            # Database admission is already visible even while the RPC blocks.
            async with sessions() as observer:
                current = await observer.get(RecurringWorkflowDefinition, definition_id)
                assert current.enabled and current.version == 2
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        remote.append(kwargs["enabled"])

    adapter = _adapter(update)
    older = asyncio.create_task(_edit(sessions, definition_id, adapter, True))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with sessions() as newer:
            await newer.execute(text("SET LOCAL lock_timeout = '150ms'"))
            current = await newer.get(RecurringWorkflowDefinition, definition_id)
            with pytest.raises(DBAPIError) as blocked:
                await RecurringWorkflowsService(
                    newer, temporal_client_adapter=adapter
                ).update_definition(current, enabled=False)
            assert blocked.value.orig.sqlstate == "55P03"
            await newer.rollback()
    finally:
        release.set()
        await older
    await _edit(sessions, definition_id, adapter, False)
    assert remote == [True, False]
    async with sessions() as observer:
        current = await observer.get(RecurringWorkflowDefinition, definition_id)
        assert current.enabled is False and current.version == 3


async def test_superseded_dispatch_does_not_reenable_newer_disabled_schedule(
    sessions, monkeypatch
):
    definition_id = await _definition(sessions)
    committed, resume = asyncio.Event(), asyncio.Event()
    original_commit = AsyncSession.commit

    async def commit(session):
        await original_commit(session)
        if session.info.pop("delayed_dispatch", False):
            committed.set()
            await asyncio.wait_for(resume.wait(), 10)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    remote = []

    async def update(**kwargs):
        remote.append(kwargs["enabled"])

    adapter = _adapter(update)
    older = asyncio.create_task(
        _edit(sessions, definition_id, adapter, True, delayed=True)
    )
    try:
        await asyncio.wait_for(committed.wait(), 10)
        await _edit(sessions, definition_id, adapter, False)
    finally:
        resume.set()
        await older
    assert remote == [False]
    adapter.unpause_schedule.assert_not_awaited()
    async with sessions() as observer:
        current = await observer.get(RecurringWorkflowDefinition, definition_id)
        assert current.enabled is False and current.version == 3
