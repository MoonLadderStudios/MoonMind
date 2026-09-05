"""Production replay and worker-boundary coverage for slot-manager cleanup."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity, exceptions, workflow
from temporalio.client import WorkflowHistory
from temporalio.converter import DataConverter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    ACTIVITY_TASK_QUEUE,
    MoonMindProviderProfileManagerWorkflow,
)


@pytest.mark.asyncio
async def test_pre_tombstone_purge_manager_history_replays() -> None:
    """A continued manager must verify its held lease before its recorded timer.

    This sanitized 52-event production prefix predates maintenance durability.
    Inserting purge_released before lease verification used to fail replay at
    auth-profile-manager-verify-leases-v1, wedging all slot requests.
    """
    fixture = (
        Path(__file__).with_name("fixtures")
        / "provider_profile_manager_pre_tombstone_purge.json"
    )
    history = WorkflowHistory.from_json(
        "provider-profile-manager:opencode", fixture.read_text()
    )
    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


class _ProfileActivities:
    def __init__(self, runtime_id: str, fail_cleanup: bool) -> None:
        self.runtime_id = runtime_id
        self.fail_cleanup = fail_cleanup
        self.actions: list[str] = []
        self.cleaned = asyncio.Event()
        self.verified = asyncio.Event()

    @activity.defn(name="provider_profile.list")
    async def list_profiles(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request == {"runtime_id": self.runtime_id}
        return {
            "profiles": [
                {
                    "profile_id": "test-default",
                    "runtime_id": self.runtime_id,
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
    async def sync_leases(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request["runtime_id"] == self.runtime_id
        action = request["action"]
        self.actions.append(action)
        if action == "purge_released":
            assert request["leases"] == [{"older_than_seconds": 30 * 24 * 3600}]
            self.cleaned.set()
            if self.fail_cleanup:
                raise exceptions.ApplicationError(
                    "cleanup unavailable", non_retryable=True
                )
        return {"leases": [], "synced": len(request.get("leases", []))}

    @activity.defn(name="provider_profile.pending_request_order")
    async def pending_order(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"orders": {owner: {} for owner in request["workflow_ids"]}}

    @activity.defn(name="provider_profile.verify_lease_holders")
    async def verify(self, request: dict[str, Any]) -> dict[str, Any]:
        self.verified.set()
        # A novel status label must not discard the positively verified owner.
        return {
            owner: {"running": True, "status": "NEW_STATUS"}
            for owner in request["workflow_ids"]
        }


@workflow.defn(name="Test.CleanupSlotRequester")
class _SlotRequester:
    def __init__(self) -> None:
        self.assignment: dict[str, Any] | None = None
        self.stopped = False

    @workflow.signal
    def slot_assigned(self, payload: dict[str, Any]) -> None:
        self.assignment = payload

    @workflow.signal
    def shutdown(self) -> None:
        self.stopped = True

    @workflow.query
    def assigned(self) -> dict[str, Any] | None:
        return self.assignment

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: self.stopped)


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_id", ["opencode", "codex_cli", "claude_code"])
@pytest.mark.parametrize(
    "fail_cleanup", [False, True], ids=["cleanup-ok", "cleanup-failed"]
)
async def test_current_manager_cleans_then_grants_and_replays(
    runtime_id: str, fail_cleanup: bool
) -> None:
    """Default cleanup, including failure, preserves durable grant authority."""
    activities = _ProfileActivities(runtime_id, fail_cleanup)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-profile-cleanup",
            workflows=[MoonMindProviderProfileManagerWorkflow, _SlotRequester],
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
                task_queue="test-profile-cleanup",
            )
            await asyncio.wait_for(activities.cleaned.wait(), timeout=15)
            requester = await env.client.start_workflow(
                _SlotRequester.run,
                id="test-slot-requester",
                task_queue="test-profile-cleanup",
            )
            # The omitted profile selector must choose the default profile.
            await manager.signal(
                "request_slot",
                {
                    "requester_workflow_id": requester.id,
                    "runtime_id": runtime_id,
                },
            )
            await asyncio.wait_for(activities.verified.wait(), timeout=15)
            assignment = await requester.query(_SlotRequester.assigned)
            assert assignment["profile_id"] == "test-default"
            assert assignment["fencing_generation"] > 0
            state = await manager.query("get_state")
            assert state["profiles"]["test-default"]["current_leases"] == [requester.id]
            assert "purge_released" in activities.actions
            await manager.signal("shutdown")
            assert (await manager.result())["status"] == "shutdown"
            await requester.signal(_SlotRequester.shutdown)
            await requester.result()
            history = await manager.fetch_history()

    # Assert the production command handoff, then replay the new history too.
    commands = []
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            if attrs.activity_type.name == "provider_profile.sync_slot_leases":
                payload = (await DataConverter.default.decode(attrs.input.payloads))[0]
                commands.append(payload["action"])
        elif event.HasField(
            "signal_external_workflow_execution_initiated_event_attributes"
        ):
            attrs = event.signal_external_workflow_execution_initiated_event_attributes
            if attrs.signal_name == "slot_assigned":
                commands.append("slot_assigned")
    assert (
        commands.index("purge_released")
        < commands.index("grant")
        < commands.index("slot_assigned")
    )
    await Replayer(
        workflows=[MoonMindProviderProfileManagerWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
