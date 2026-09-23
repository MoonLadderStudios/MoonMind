"""Actual API/Temporal repair journey (#3946 REQ-03/REQ-05).

Drives one real workflow through the time-skipping Temporal test server and
reconciles its real execution descriptions through the shared mutator owner
(``api_service.core.sync.sync_execution_projection``): a RUNNING observation
repairs the missing projection, terminal close evidence converges the same
row without duplicating the execution, and a late same-run RUNNING snapshot
can never reopen the closed run — the last valid state is retained with
honest limited freshness instead of a restart.

NOTE: marked ``integration`` only, not ``integration_ci`` — Temporal
workflow tests with time-skipping consistently exceed CI timeout thresholds.
Kept for local dev verification alongside the SQLite ordering unit coverage.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import workflow
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.core.sync import sync_execution_projection
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
)
from tests.helpers.temporal_visibility import register_deployment_search_attributes

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@workflow.defn(name="MoonMind.UserWorkflow")
class _RepairJourneyWorkflow:
    """Minimal product-scope workflow: runs until an explicit release signal."""

    def __init__(self) -> None:
        self._released = False

    @workflow.signal
    def release(self) -> None:
        self._released = True

    @workflow.run
    async def run(self) -> dict:
        await workflow.wait_condition(lambda: self._released)
        return {"status": "success"}


async def _wait_for_running(handle) -> None:
    from temporalio.client import WorkflowExecutionStatus

    async with asyncio.timeout(60):
        while True:
            desc = await handle.describe()
            if desc.status == WorkflowExecutionStatus.RUNNING:
                return
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_real_temporal_repair_journey_converges_without_reexecution(tmp_path):
    queue = f"repair-journey-3946-{uuid4()}"
    workflow_id = f"repair-journey-3946-{uuid4()}"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/journey.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await register_deployment_search_attributes(env)
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[_RepairJourneyWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                stamped = datetime.now(UTC)
                handle = await env.client.start_workflow(
                    _RepairJourneyWorkflow.run,
                    id=workflow_id,
                    task_queue=queue,
                    memo={
                        "entry": "run",
                        "owner_id": "owner-1",
                        "owner_type": "user",
                        "parameters": {"targetRuntime": "codex_cli"},
                    },
                    search_attributes=TypedSearchAttributes([
                        SearchAttributePair(
                            SearchAttributeKey.for_keyword("mm_owner_id"), "owner-1"
                        ),
                        SearchAttributePair(
                            SearchAttributeKey.for_keyword("mm_owner_type"), "user"
                        ),
                        SearchAttributePair(
                            SearchAttributeKey.for_keyword("mm_state"), "executing"
                        ),
                        SearchAttributePair(
                            SearchAttributeKey.for_datetime("mm_updated_at"), stamped
                        ),
                    ]),
                )
                await _wait_for_running(handle)

                async with sessions() as session:
                    # First observation repairs the missing projection from real
                    # Temporal truth — read model only, no local execution.
                    running_desc = await handle.describe()
                    first = await sync_execution_projection(session, running_desc)
                    await session.commit()
                    await session.refresh(first)

                    assert first.workflow_id == workflow_id
                    assert first.run_id == running_desc.run_id
                    assert first.owner_id == "owner-1"
                    assert first.parameters == {"targetRuntime": "codex_cli"}
                    assert first.sync_state is TemporalExecutionProjectionSyncState.FRESH
                    assert first.projection_version == 1

                    # Terminal close evidence converges the same row.
                    await handle.signal("release")
                    async with asyncio.timeout(120):
                        result = await handle.result()
                    assert result["status"] == "success"
                    closed_desc = await handle.describe()
                    assert closed_desc.run_id == running_desc.run_id

                    second = await sync_execution_projection(session, closed_desc)
                    await session.commit()
                    await session.refresh(second)

                    assert second.workflow_id == workflow_id
                    assert second.run_id == running_desc.run_id
                    assert second.state is MoonMindWorkflowState.COMPLETED
                    assert second.close_status is TemporalExecutionCloseStatus.COMPLETED
                    assert second.sync_state is TemporalExecutionProjectionSyncState.FRESH
                    assert second.projection_version == 2
                    assert second.owner_id == "owner-1"
                    assert second.parameters == {"targetRuntime": "codex_cli"}

                    # A late same-run RUNNING snapshot cannot reopen the closed
                    # run: the last valid state is retained with honest limited
                    # freshness, and no new revision is produced.
                    reopened = await sync_execution_projection(session, running_desc)
                    await session.commit()
                    await session.refresh(reopened)

                    assert reopened.run_id == running_desc.run_id
                    assert reopened.state is MoonMindWorkflowState.COMPLETED
                    assert reopened.close_status is TemporalExecutionCloseStatus.COMPLETED
                    assert reopened.projection_version == 2
                    assert reopened.sync_state is TemporalExecutionProjectionSyncState.STALE

                    # No duplicated executions behind the journey.
                    count = (
                        await session.execute(
                            select(func.count())
                            .select_from(TemporalExecutionRecord)
                            .where(TemporalExecutionRecord.workflow_id == workflow_id)
                        )
                    ).scalar_one()
                    assert count == 1
    finally:
        await engine.dispose()
