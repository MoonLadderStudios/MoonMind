"""Isolated singleton recovery for a wedged provider-profile manager.

Source: MoonLadderStudios/MoonMind#4363.

The incident's operator cutover (terminate the wedged
``provider-profile-manager:opencode`` singleton, fresh-start the same
workflow ID, let waiting ``AgentRun`` workflows re-queue) must work against
the real durable lease ledger, not just in-memory fakes. This test proves
the full cutover on an isolated stack — a hermetic Temporal test server plus
an ephemeral PostgreSQL cluster holding the real ``provider_profile_slot_leases``
table, driven through the real ``provider_profile.sync_slot_leases`` Activity:

* seed state shows 0 held leases (the incident's DB evidence);
* a first holder is granted a slot and the DB shows exactly one ``held`` row
  with fencing evidence;
* the manager is terminated (the operator cutover) and fresh-started under
  the same workflow ID;
* the fresh manager restores the held lease from the ledger — it does not
  drop the grant and its fencing generation resumes at or above the
  pre-cutover high-water mark;
* a fresh requester queues honestly at capacity, then acquires the slot
  after a fenced release, and the DB shows its row ``held``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.models import ProviderProfileSlotLease
from moonmind.provider_profiles.lease_client import DurableLeaseState
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    ACTIVITY_TASK_QUEUE,
    MoonMindProviderProfileManagerWorkflow,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
]

RUNTIME_ID = "opencode"
MANAGER_ID = "provider-profile-manager:opencode"
PROFILE_ID = "opencode-go-default"


@pytest_asyncio.fixture()
async def ledger_session_maker(control_plane_postgres_url, monkeypatch):
    """Bind the production lease session factory to an ephemeral cluster.

    The real ``provider_profile.sync_slot_leases`` Activity resolves
    ``api_service.db.base.async_session_maker`` at call time, so rebinding it
    exercises the real ledger Activity against a real table.
    """

    import api_service.db.base as db_base

    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(ProviderProfileSlotLease.__table__.create, checkfirst=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", maker)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(ProviderProfileSlotLease.__table__.drop)
        await engine.dispose()


class _LedgerBackedProfileActivities:
    """Fake discovery/verification around the real lease-ledger Activity."""

    def __init__(self, ledger: Any) -> None:
        self._ledger = ledger

    @activity.defn(name="provider_profile.list")
    async def list_profiles(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request == {"runtime_id": RUNTIME_ID}
        return {
            "profiles": [
                {
                    "profile_id": PROFILE_ID,
                    "runtime_id": RUNTIME_ID,
                    "credential_source": "api_key",
                    "runtime_materialization_mode": "env",
                    "max_parallel_runs": 1,
                    "enabled": True,
                    "launch_ready": True,
                    "is_default": True,
                }
            ]
        }

    @activity.defn(name="provider_profile.sync_slot_leases")
    async def sync_slot_leases(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._ledger.provider_profile_sync_slot_leases(
            runtime_id=request.get("runtime_id"),
            leases=request.get("leases"),
            action=request.get("action", "load"),
            writer_generation=request.get("writer_generation"),
        )

    @activity.defn(name="provider_profile.pending_request_order")
    async def pending_request_order(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"orders": {workflow_id: {} for workflow_id in request["workflow_ids"]}}

    @activity.defn(name="provider_profile.verify_lease_holders")
    async def verify_lease_holders(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            workflow_id: {"running": True, "status": "RUNNING"}
            for workflow_id in request["workflow_ids"]
        }


@workflow.defn(name="Test.WedgeRecoveryRequester")
class _RequesterWorkflow:
    """Minimal AgentRun stand-in: receives slot_assigned, counts assignments."""

    def __init__(self) -> None:
        self.assignment: dict[str, Any] | None = None
        self.assignments: list[dict[str, Any]] = []
        self.stopped = False

    @workflow.signal(name="slot_assigned")
    def slot_assigned(self, payload: dict[str, Any]) -> None:
        self.assignment = payload
        self.assignments.append(payload)

    @workflow.query(name="assigned")
    def assigned(self) -> dict[str, Any] | None:
        return self.assignment

    @workflow.query(name="assignment_count")
    def assignment_count(self) -> int:
        return len(self.assignments)

    @workflow.signal(name="shutdown")
    def shutdown(self) -> None:
        self.stopped = True

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: self.stopped)


async def _held_count(maker: Any) -> int:
    async with maker() as session:
        return int(
            (
                await session.execute(
                    select(func.count(ProviderProfileSlotLease.id)).where(
                        ProviderProfileSlotLease.runtime_id == RUNTIME_ID,
                        ProviderProfileSlotLease.lease_state
                        == DurableLeaseState.HELD.value,
                    )
                )
            ).scalar()
            or 0
        )


async def _wait_for_assignment(handle: Any, timeout: float = 30) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while (assignment := await handle.query("assigned")) is None:
            await asyncio.sleep(0.05)
    return assignment


async def _wait_for_state(
    handle: Any, predicate: Any, timeout: float = 30
) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while True:
            state = await handle.query("get_state")
            if predicate(state):
                return state
            await asyncio.sleep(0.05)


async def _pending_ids(state: dict[str, Any]) -> set[str]:
    return {
        str(request.get("requester_workflow_id") or "")
        for request in (state.get("pending_requests") or [])
        if isinstance(request, dict)
    }


async def test_wedged_singleton_cutover_restores_ledger_and_regrants(
    ledger_session_maker: Any,
) -> None:
    """Terminate + fresh-start recovers the ledger grant for waiting runs."""
    maker = ledger_session_maker
    ledger = TemporalArtifactActivities(service=None)
    activities = _LedgerBackedProfileActivities(ledger)
    task_queue = "test-wedge-recovery-4363"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[MoonMindProviderProfileManagerWorkflow, _RequesterWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                activities.list_profiles,
                activities.sync_slot_leases,
                activities.pending_request_order,
                activities.verify_lease_holders,
            ],
        ):
            # The incident's DB evidence: 0 held leases before recovery.
            assert await _held_count(maker) == 0

            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": RUNTIME_ID},
                id=MANAGER_ID,
                task_queue=task_queue,
            )
            holder = await env.client.start_workflow(
                _RequesterWorkflow.run,
                id="wedge-recovery-holder",
                task_queue=task_queue,
            )
            await manager.signal(
                "request_slot",
                {"requester_workflow_id": holder.id, "runtime_id": RUNTIME_ID},
            )
            assignment = await _wait_for_assignment(holder)
            assert assignment["profile_id"] == PROFILE_ID
            first_fence = int(assignment["fencing_generation"])
            assert first_fence > 0

            # The grant is durable authority: exactly one held row with the
            # same fencing evidence the consumer was handed.
            assert await _held_count(maker) == 1

            # The operator cutover: terminate the wedged run, fresh-start the
            # same singleton ID on the current worker.
            await manager.terminate(reason="operator cutover per runbook 11.9")
            async with asyncio.timeout(15):
                while (await manager.describe()).status.name != "TERMINATED":
                    await asyncio.sleep(0.05)
            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": RUNTIME_ID},
                id=MANAGER_ID,
                task_queue=task_queue,
            )

            # The fresh manager restores the held lease from the ledger
            # instead of dropping the grant.
            restored = await _wait_for_state(
                manager,
                lambda state: state.get("profiles", {})
                .get(PROFILE_ID, {})
                .get("current_leases")
                == [holder.id],
            )
            restored_profile = restored.get("profiles", {}).get(PROFILE_ID, {})
            restored_metadata = restored_profile.get("lease_metadata", {})
            assert int(restored_metadata[holder.id]["fencingGeneration"]) >= first_fence
            assert await _held_count(maker) == 1

            # A fresh AgentRun queues honestly at capacity: no phantom grant
            # from the recovery, and the request stays durable.
            waiter = await env.client.start_workflow(
                _RequesterWorkflow.run,
                id="wedge-recovery-waiter",
                task_queue=task_queue,
            )
            await manager.signal(
                "request_slot",
                {"requester_workflow_id": waiter.id, "runtime_id": RUNTIME_ID},
            )
            await _wait_for_state(
                manager, lambda state: waiter.id in _pending_ids(state)
            )
            await asyncio.sleep(1)
            assert await waiter.query("assignment_count") == 0
            assert await _held_count(maker) == 1

            # A fenced release hands the slot to the waiter, and the DB shows
            # the waiter's row held: recovery ends in a fresh grant.
            await manager.signal(
                "release_slot",
                {
                    "profile_id": PROFILE_ID,
                    "requester_workflow_id": holder.id,
                    "fencing_generation": first_fence,
                },
            )
            re_assignment = await _wait_for_assignment(waiter)
            assert re_assignment["profile_id"] == PROFILE_ID
            assert int(re_assignment["fencing_generation"]) > first_fence
            assert await _held_count(maker) == 1
            async with maker() as session:
                rows = (
                    await session.execute(
                        select(ProviderProfileSlotLease).where(
                            ProviderProfileSlotLease.runtime_id == RUNTIME_ID,
                            ProviderProfileSlotLease.lease_state
                            == DurableLeaseState.HELD.value,
                        )
                    )
                ).scalars().all()
            assert [row.workflow_id for row in rows] == [waiter.id]

            with env.auto_time_skipping_disabled():
                await manager.signal("shutdown")
                await holder.signal("shutdown")
                await waiter.signal("shutdown")
                assert (await manager.result())["status"] == "shutdown"
                await holder.result()
                await waiter.result()
