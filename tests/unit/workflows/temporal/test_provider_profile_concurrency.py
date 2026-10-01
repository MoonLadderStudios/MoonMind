"""Independent Provider Profiles share a manager, not an execution budget."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowUpdateStage
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    ACTIVITY_TASK_QUEUE,
    MoonMindProviderProfileManagerWorkflow,
)


@workflow.defn(name="Test.IndependentProfileLeaseOwner")
class _LeaseOwner:
    def __init__(self) -> None:
        self.assignment: dict[str, Any] | None = None
        self.finished = False

    @workflow.signal(name="slot_assigned")
    def slot_assigned(self, assignment: dict[str, Any]) -> None:
        self.assignment = assignment

    @workflow.signal
    def finish(self) -> None:
        self.finished = True

    @workflow.query
    def assigned_profile(self) -> str | None:
        return self.assignment["profile_id"] if self.assignment else None

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: self.finished)


class _ProfileActivities:
    """Keep lease writes across workflow tasks; verify actual Temporal owners."""

    def __init__(self, runtime_id: str, client: Any) -> None:
        self.runtime_id = runtime_id
        self.client = client
        self.rows: dict[str, dict[str, Any]] = {}

    @activity.defn(name="provider_profile.list")
    async def list_profiles(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request == {"runtime_id": self.runtime_id}
        oauth = self.runtime_id in {"codex_cli", "claude_code"}
        return {
            "profiles": [
                {
                    "profile_id": profile_id,
                    "runtime_id": self.runtime_id,
                    "provider_id": "test-provider",
                    "credential_source": "oauth_volume" if oauth else "none",
                    "runtime_materialization_mode": "oauth_home" if oauth else "none",
                    "max_parallel_runs": 1,
                    "enabled": True,
                    "launch_ready": True,
                    "is_default": profile_id == "first",
                    # Exercise both the explicit default and the omitted default.
                    **(
                        {"capacity_scope_ref": "provider-profile:first"}
                        if profile_id == "first"
                        else {}
                    ),
                }
                for profile_id in ("first", "second")
            ]
        }

    @activity.defn(name="provider_profile.sync_slot_leases")
    async def sync_leases(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request["runtime_id"] == self.runtime_id
        action = request["action"]
        if action == "load":
            return {"leases": list(self.rows.values())}
        if action == "grant":
            row = request["leases"][0]
            lease_id = row["lease_id"]
            assert lease_id not in self.rows
            assert not any(
                held["profile_id"] == row["profile_id"] for held in self.rows.values()
            ), "a profile's capacity-one budget must remain enforced"
            self.rows[lease_id] = dict(row)
            return {"outcome": "granted", "synced": 1}
        if action == "release_one":
            row = request["leases"][0]
            held = self.rows.pop(row["lease_id"])
            assert held["profile_id"] == row["profile_id"]
            assert held["fencing_generation"] == row["fencing_generation"]
            return {"outcome": "released", "released": True}
        assert action == "purge_released"
        return {"purged": 0}

    @activity.defn(name="provider_profile.pending_request_order")
    async def pending_order(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"orders": {owner: {} for owner in request["workflow_ids"]}}

    @activity.defn(name="provider_profile.verify_lease_holders")
    async def verify(self, request: dict[str, Any]) -> dict[str, Any]:
        statuses = {}
        for owner in request["workflow_ids"]:
            description = await self.client.get_workflow_handle(owner).describe()
            statuses[owner] = {
                "running": description.status.name == "RUNNING",
                "status": description.status.name,
            }
        return statuses


async def _wait_for_state(manager: Any, predicate: Any) -> dict[str, Any]:
    async with asyncio.timeout(15):
        while True:
            state = await manager.query("get_state")
            if predicate(state):
                return state
            await asyncio.sleep(0.01)


@asynccontextmanager
async def _running_manager(runtime_id: str):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        activities = _ProfileActivities(runtime_id, env.client)
        async with Worker(
            env.client,
            task_queue="independent-profiles",
            workflows=[MoonMindProviderProfileManagerWorkflow, _LeaseOwner],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                activities.list_profiles,
                activities.sync_leases,
                activities.pending_order,
                activities.verify,
            ],
        ):
            manager = await env.client.start_workflow(
                MoonMindProviderProfileManagerWorkflow.run,
                {"runtime_id": runtime_id},
                id=f"provider-profile-manager:{runtime_id}",
                task_queue="independent-profiles",
            )
            owners = {
                owner: await env.client.start_workflow(
                    _LeaseOwner.run,
                    id=owner,
                    task_queue="independent-profiles",
                )
                for owner in ("first-run", "first-waiter", "second-run", "second-next")
            }
            # Result waits must not fast-forward other live owners or their cooldowns.
            with env.auto_time_skipping_disabled():
                try:
                    yield manager, activities, owners
                finally:
                    await manager.signal("shutdown")
                    await asyncio.wait_for(manager.result(), timeout=15)
                    for owner in owners.values():
                        if (
                            await owner.describe()
                        ).status == WorkflowExecutionStatus.RUNNING:
                            await owner.signal("finish")
                        await owner.result()
                history = await manager.fetch_history()

    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_id", ["codex_cli", "claude_code", "opencode"])
@pytest.mark.parametrize("admission", ["signal", "update"])
async def test_full_or_cooling_profile_does_not_block_another_profile(
    runtime_id: str, admission: str
) -> None:
    """Two live owners overlap despite a full profile and its older waiter."""
    async with _running_manager(runtime_id) as (manager, activities, owners):

        def request(owner: str, profile: str) -> dict[str, Any]:
            return {
                "requester_workflow_id": owner,
                "runtime_id": runtime_id,
                "execution_profile_ref": profile,
            }

        async def acquire(owner: str, profile: str) -> None:
            if admission == "update":
                grant = await asyncio.wait_for(
                    manager.execute_update("AcquireSlotV2", request(owner, profile)),
                    timeout=15,
                )
                assert grant["profile_id"] == profile
            else:
                await manager.signal("request_slot", request(owner, profile))
                async with asyncio.timeout(15):
                    while await owners[owner].query("assigned_profile") != profile:
                        await asyncio.sleep(0.01)
            assert activities.rows[owner]["profile_id"] == profile

        async def release(owner: str, profile: str) -> None:
            row = activities.rows[owner]
            await manager.signal(
                "release_slot",
                {
                    **request(owner, profile),
                    "profile_id": profile,
                    "fencing_generation": row["fencing_generation"],
                },
            )
            await _wait_for_state(
                manager,
                lambda state: owner not in state["profiles"][profile]["current_leases"],
            )

        await acquire("first-run", "first")
        if admission == "update":
            waiter = await manager.start_update(
                "AcquireSlotV2",
                request("first-waiter", "first"),
                wait_for_stage=WorkflowUpdateStage.ACCEPTED,
            )
        else:
            await manager.signal("request_slot", request("first-waiter", "first"))
            await _wait_for_state(
                manager,
                lambda state: any(
                    entry["requester_workflow_id"] == "first-waiter"
                    for entry in state["pending_requests"]
                ),
            )

        await acquire("second-run", "second")
        assert set(activities.rows) == {"first-run", "second-run"}
        state = await manager.query("get_state")
        assert state["profiles"]["first"]["current_leases"] == ["first-run"]
        assert state["profiles"]["second"]["current_leases"] == ["second-run"]

        await release("first-run", "first")
        if admission == "update":
            assert (await asyncio.wait_for(waiter.result(), timeout=15))[
                "profile_id"
            ] == "first"
        else:
            await _wait_for_state(
                manager,
                lambda state: state["profiles"]["first"]["current_leases"]
                == ["first-waiter"]
                and "first-waiter" in activities.rows,
            )
        assert set(activities.rows) == {"first-waiter", "second-run"}

        await manager.signal(
            "report_cooldown",
            {"profile_id": "first", "cooldown_seconds": 600, "report_id": "first-429"},
        )
        await _wait_for_state(
            manager,
            lambda state: any(
                scope["scope_ref"] == "provider-profile:first"
                and scope["cooldown_until"] is not None
                for scope in state["scopes"]
            ),
        )
        await release("second-run", "second")
        await acquire("second-next", "second")
        assert set(activities.rows) == {"first-waiter", "second-next"}
        state = await manager.query("get_state")
        scopes = {scope["scope_ref"]: scope for scope in state["scopes"]}
        assert scopes["provider-profile:first"]["cooldown_until"] is not None
        assert scopes["provider-profile:second"]["cooldown_until"] is None
        await release("first-waiter", "first")
        await release("second-next", "second")
        assert activities.rows == {}


@pytest.mark.asyncio
async def test_completed_lease_owner_does_not_break_manager_cleanup() -> None:
    """Cleanup reconciles an owner that completed before the manager stopped."""
    async with _running_manager("codex_cli") as (_, _, owners):
        await owners["first-run"].signal("finish")
        await owners["first-run"].result()
        for owner_id, owner in owners.items():
            if owner_id != "first-run":
                assert (
                    await owner.describe()
                ).status == WorkflowExecutionStatus.RUNNING
