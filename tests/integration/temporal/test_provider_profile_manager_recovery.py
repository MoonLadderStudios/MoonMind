"""Temporal boundary regression tests for provider-profile lease recovery."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    ACTIVITY_TASK_QUEUE,
    MoonMindProviderProfileManagerWorkflow,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.temporal_boundary,
]


class _ProviderProfileActivities:
    def __init__(self) -> None:
        self.list_started = asyncio.Event()
        self.release_list = asyncio.Event()

    @activity.defn(name="provider_profile.list")
    async def list_profiles(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request == {"runtime_id": "codex_cli"}
        self.list_started.set()
        await self.release_list.wait()
        return {
            "profiles": [
                {
                    "profile_id": "codex-oauth",
                    "runtime_id": "codex_cli",
                    "credential_source": "oauth_volume",
                    "runtime_materialization_mode": "oauth_home",
                    "max_parallel_runs": 1,
                    "enabled": True,
                    "is_default": True,
                }
            ]
        }

    @activity.defn(name="provider_profile.sync_slot_leases")
    async def sync_slot_leases(self, request: dict[str, Any]) -> dict[str, Any]:
        if request["action"] == "load":
            return {
                "leases": [
                    {
                        "workflow_id": "active-agent-run",
                        "profile_id": "codex-oauth",
                    }
                ]
            }
        return {"synced": len(request.get("leases", []))}

    @activity.defn(name="provider_profile.pending_request_order")
    async def pending_request_order(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"orders": {workflow_id: {} for workflow_id in request["workflow_ids"]}}

    @activity.defn(name="provider_profile.verify_lease_holders")
    async def verify_lease_holders(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            workflow_id: {"running": True, "status": "RUNNING"}
            for workflow_id in request["workflow_ids"]
        }


@workflow.defn(name="Test.ProviderProfileLeaseHolder")
class _LeaseHolderWorkflow:
    def __init__(self) -> None:
        self._assigned_profiles: list[str] = []
        self._shutdown = False

    @workflow.signal(name="slot_assigned")
    def slot_assigned(self, payload: dict[str, Any]) -> None:
        self._assigned_profiles.append(payload["profile_id"])

    @workflow.query(name="assignment_count")
    def assignment_count(self) -> int:
        return len(self._assigned_profiles)

    @workflow.signal(name="shutdown")
    def shutdown(self) -> None:
        self._shutdown = True

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: self._shutdown)


async def _query_until_recovered(manager_handle: Any) -> dict[str, Any]:
    for _ in range(100):
        state = await manager_handle.query("get_state")
        profile = state.get("profiles", {}).get("codex-oauth", {})
        pending = {
            request["requester_workflow_id"]
            for request in state.get("pending_requests", [])
        }
        if profile.get("current_leases") == ["active-agent-run"] and pending == {
            "waiting-agent-run"
        }:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(
        "manager did not recover the durable lease and pending request"
    )


async def test_request_during_startup_cannot_bypass_recovered_oauth_lease() -> None:
    """A request signaled during profile loading must remain queued at capacity."""
    activities = _ProviderProfileActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        task_queue = f"provider-profile-recovery-{uuid.uuid4()}"
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[
                MoonMindProviderProfileManagerWorkflow,
                _LeaseHolderWorkflow,
            ],
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
            holder = await env.client.start_workflow(
                _LeaseHolderWorkflow.run,
                id="active-agent-run",
                task_queue=task_queue,
            )
            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": "codex_cli"},
                id=f"provider-profile-manager-test-{uuid.uuid4()}",
                task_queue=task_queue,
            )

            await asyncio.wait_for(activities.list_started.wait(), timeout=10)
            await manager.signal(
                "request_slot",
                {
                    "requester_workflow_id": "waiting-agent-run",
                    "runtime_id": "codex_cli",
                },
            )
            activities.release_list.set()

            await asyncio.wait_for(_query_until_recovered(manager), timeout=15)
            assert await holder.query("assignment_count") == 1

            await manager.signal("shutdown")
            await holder.signal("shutdown")
            manager_result = await asyncio.wait_for(manager.result(), timeout=15)
            await asyncio.wait_for(holder.result(), timeout=15)

    assert manager_result["status"] == "shutdown"
