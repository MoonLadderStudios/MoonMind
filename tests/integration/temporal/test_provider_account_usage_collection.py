"""Usage collection workflow ownership and cleanup (MoonLadderStudios/MoonMind#5363)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activity_catalog import WORKFLOW_TASK_QUEUE
from moonmind.workflows.temporal.workflows.provider_account_usage import (
    ACTIVITY_TASK_QUEUE,
    MoonMindProviderAccountUsageCollectionWorkflow,
    workflow_id_for_attempt,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    USAGE_OBSERVATION_PREEMPT_SIGNAL,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

EVENTS: list[Any] = []
REQUEST = {
    "profile_id": "alpha",
    "credential_generation": 1,
    "provider_id": "openai",
    "runtime_id": "codex_cli",
    "attempt_id": "attempt-1",
    "trigger": "manual",
    "deadline_at": "2026-11-18T12:02:00+00:00",
}


@workflow.defn(name="StubProviderProfileManager")
class StubManager:
    def __init__(self) -> None:
        self.done = False

    @workflow.signal(name="release_slot")
    def release_slot(self, payload: dict) -> None:
        EVENTS.append(("release", payload))
        self.done = True

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: self.done)


class Behavior:
    route = "idle_probe"
    granted = True
    block_probe = False
    cleanup_fails = False


@activity.defn(name="provider_account_usage.resolve")
async def resolve(request: dict) -> dict:
    EVENTS.append("resolve")
    return {
        "status": "ready",
        "route": Behavior.route,
        "credentialResourceKey": "credential-home:abc",
        "aliasProfileIds": ["alpha"],
        "owners": [
            {"kind": "runtime_binding", "profile_id": "alpha", "owner_ref": "b1"}
        ],
    }


@activity.defn(name="provider_account_usage.collect_from_owner")
async def collect_from_owner(request: dict) -> dict:
    EVENTS.append(("owner", request["owners"][0]["owner_ref"]))
    return {"status": "observed"}


@activity.defn(name="provider_account_usage.try_acquire")
async def try_acquire(request: dict) -> dict:
    EVENTS.append(("acquire", request["owner_id"], request["aliasProfileIds"]))
    if not Behavior.granted:
        return {"granted": False, "status": "deferred"}
    return {"granted": True, "leaseId": request["owner_id"], "fencingGeneration": 7}


@activity.defn(name="provider_account_usage.probe")
async def probe(request: dict) -> dict:
    EVENTS.append("probe")
    if Behavior.block_probe:
        try:
            while True:
                activity.heartbeat()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            EVENTS.append("probe_cancelled")
            raise
    return {"status": "observed"}


@activity.defn(name="provider_account_usage.cleanup")
async def cleanup(request: dict) -> dict:
    EVENTS.append(("cleanup", request["leaseId"], request["deferAttempt"]))
    if Behavior.cleanup_fails:
        raise ApplicationError("docker unavailable", non_retryable=True)
    return {"verified": True}


@pytest.fixture(autouse=True)
def reset():
    EVENTS.clear()
    Behavior.route = "idle_probe"
    Behavior.granted = True
    Behavior.block_probe = False
    Behavior.cleanup_fails = False


@asynccontextmanager
async def _workers(env):
    async with Worker(
        env.client,
        task_queue=WORKFLOW_TASK_QUEUE,
        workflows=[MoonMindProviderAccountUsageCollectionWorkflow, StubManager],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ), Worker(
        env.client,
        task_queue=ACTIVITY_TASK_QUEUE,
        activities=[resolve, collect_from_owner, try_acquire, probe, cleanup],
    ):
        await env.client.start_workflow(
            StubManager.run,
            id="provider-profile-manager:codex_cli",
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        yield


async def _run(env, *, preempt: bool = False) -> dict:
    workflow_id = workflow_id_for_attempt("alpha", "attempt-1")
    handle = await env.client.start_workflow(
        MoonMindProviderAccountUsageCollectionWorkflow.run,
        REQUEST,
        id=workflow_id,
        task_queue=WORKFLOW_TASK_QUEUE,
    )
    if preempt:
        for _ in range(100):
            if "probe" in EVENTS:
                break
            await asyncio.sleep(0.1)
        await handle.signal(
            USAGE_OBSERVATION_PREEMPT_SIGNAL,
            {"lease_id": workflow_id, "reason": "execution_pending"},
        )
    return await handle.result()


async def test_idle_probe_releases_its_grant_only_after_verified_cleanup() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env, _workers(env):
        result = await _run(env)

    owner = workflow_id_for_attempt("alpha", "attempt-1")
    assert result == {"status": "observed", "preempted": False}
    assert EVENTS[:3] == ["resolve", ("acquire", owner, ["alpha"]), "probe"]
    assert EVENTS[3] == ("cleanup", owner, False)
    assert EVENTS[4][0] == "release"
    assert EVENTS[4][1]["fencing_generation"] == 7
    assert EVENTS[4][1]["purpose"] == "usage_observation"


async def test_arriving_work_cancels_the_probe_then_cleans_up_before_release() -> None:
    Behavior.block_probe = True
    async with await WorkflowEnvironment.start_time_skipping() as env, _workers(env):
        result = await _run(env, preempt=True)

    owner = workflow_id_for_attempt("alpha", "attempt-1")
    assert result == {"status": "deferred", "preempted": True}
    cancelled = EVENTS.index("probe_cancelled")
    cleaned = EVENTS.index(("cleanup", owner, True))
    released = next(i for i, event in enumerate(EVENTS) if event[0] == "release")
    assert cancelled < cleaned < released


async def test_unverified_cleanup_keeps_the_grant() -> None:
    Behavior.cleanup_fails = True
    async with await WorkflowEnvironment.start_time_skipping() as env, _workers(env):
        result = await _run(env)

    assert result["status"] == "failed"
    assert not any(isinstance(e, tuple) and e[0] == "release" for e in EVENTS)


async def test_busy_home_defers_without_probing_or_releasing() -> None:
    Behavior.granted = False
    async with await WorkflowEnvironment.start_time_skipping() as env, _workers(env):
        result = await _run(env)

    assert result == {"status": "deferred"}
    assert "probe" not in EVENTS
    assert not any(
        isinstance(e, tuple) and e[0] in {"cleanup", "release"} for e in EVENTS
    )


async def test_active_owner_route_never_takes_an_idle_grant() -> None:
    Behavior.route = "active_owner"
    async with await WorkflowEnvironment.start_time_skipping() as env, _workers(env):
        result = await _run(env)

    assert result == {"status": "observed"}
    assert EVENTS == ["resolve", ("owner", "b1")]
