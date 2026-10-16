"""Real Temporal: an older versioned installation crosses to ordinary workers.

Each journey recreates the server state an installation left behind when its
workers ran with Worker Versioning (AUTO_UPGRADE, a current Worker Deployment
version), then exercises the one-time cutover the deployment controller runs.
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from google.protobuf.field_mask_pb2 import FieldMask
from temporalio import activity, workflow
from temporalio.api.workflow.v1 import VersioningOverride, WorkflowExecutionOptions
from temporalio.api.workflowservice.v1 import (
    DescribeWorkerDeploymentVersionRequest,
    SetWorkerDeploymentCurrentVersionRequest,
    SetWorkerDeploymentRampingVersionRequest,
    UpdateWorkflowExecutionOptionsRequest,
)
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
    WorkflowExecutionStatus,
)
from temporalio.common import (
    PinnedVersioningOverride,
    VersioningBehavior,
    WorkerDeploymentVersion,
)
from temporalio.worker import (
    Replayer,
    UnsandboxedWorkflowRunner,
    Worker,
    WorkerDeploymentConfig,
)

from moonmind.workflows.temporal.unversioned_cutover import (
    current_version,
    describe_deployment,
    ramping_version,
    run_cutover,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class CutoverProbe:
    """Activity, then signal wait, then a durable timer and another Activity."""

    def __init__(self):
        self.proceed = False

    @workflow.signal
    def resume(self):
        self.proceed = True

    @workflow.run
    async def run(self, activity_queue: str) -> list[str]:
        first = await workflow.execute_activity(
            "cutover.probe",
            task_queue=activity_queue,
            start_to_close_timeout=timedelta(seconds=20),
        )
        await workflow.wait_condition(lambda: self.proceed)
        await workflow.sleep(timedelta(seconds=1))
        second = await workflow.execute_activity(
            "cutover.probe",
            task_queue=activity_queue,
            start_to_close_timeout=timedelta(seconds=20),
        )
        return [first, second]


@workflow.defn(name="MoonMind.ReleaseCanary")
class RetiredReleaseCanary:
    """Stands in for the removed release canary's open executions."""

    @workflow.run
    async def run(self, payload: dict) -> dict:
        await workflow.wait_condition(lambda: False)
        return payload


async def connect():
    address = os.getenv("MOONMIND_TEST_TEMPORAL_ADDRESS")
    assert (
        address
    ), "Set MOONMIND_TEST_TEMPORAL_ADDRESS to the isolated reliability Compose service"
    for _ in range(59):
        try:
            return await Client.connect(address)
        except RuntimeError:
            await asyncio.sleep(1)
    return await Client.connect(address)


async def _await_temporal_condition(
    description, fetch, *, attempts, interval_seconds, is_ready=None
):
    """Bounded real-dependency polling helper (MoonLadderStudios/MoonMind#4374).

    Deduplicates repeated ``for _ in range(N): ... await asyncio.sleep()``
    polling loops against the real isolated Temporal service. A fetch that
    keeps raising re-raises its last error; a condition that never becomes
    ready fails loudly instead of passing silently.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            value = await fetch()
        except Exception as exc:  # noqa: BLE001 - retried as not-ready
            last_error = exc
        else:
            last_error = None
            if is_ready(value) if is_ready is not None else bool(value):
                return value
        if attempt == attempts - 1:
            break
        await asyncio.sleep(interval_seconds)
    if last_error is not None:
        raise last_error
    pytest.fail(f"Timed out waiting for real Temporal condition: {description}")
    raise AssertionError(f"unreachable: {description}")  # pragma: no cover


def _probe(name, seen=None):
    @activity.defn(name="cutover.probe")
    async def implementation():
        if seen is not None:
            seen.set()
        return name

    return implementation


def _versioned(deployment, build):
    """The worker configuration older installations ran."""
    return WorkerDeploymentConfig(
        version=WorkerDeploymentVersion(deployment, build),
        use_worker_versioning=True,
        default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
    )


async def _await_registered(client, version, queues):
    async def registered():
        response = await client.workflow_service.describe_worker_deployment_version(
            DescribeWorkerDeploymentVersionRequest(
                namespace=client.namespace, version=version
            )
        )
        names = {
            item.name
            for item in response.worker_deployment_version_info.task_queue_infos
        }
        return set(queues) <= names

    await _await_temporal_condition(
        "versioned workers to register their queues",
        registered,
        attempts=60,
        interval_seconds=0.5,
    )


async def _make_current(client, deployment, build, queues):
    """Recreate the routing an older installation's promotion left behind."""
    version = f"{deployment}.{build}"
    await _await_registered(client, version, queues)
    snapshot = await describe_deployment(client, deployment)
    await client.workflow_service.set_worker_deployment_current_version(
        SetWorkerDeploymentCurrentVersionRequest(
            namespace=client.namespace,
            deployment_name=deployment,
            version=version,
            conflict_token=snapshot.conflict_token,
            identity="older-installation",
        )
    )
    assert current_version(await describe_deployment(client, deployment)) == version


async def test_auto_upgrade_and_queued_work_cross_the_unversioned_cutover():
    client = await connect()
    deployment = f"cutover-{uuid4().hex}"
    queue, activity_queue = f"{deployment}-workflow", f"{deployment}-activity"
    first_seen = asyncio.Event()
    in_flight_id, queued_id = f"{deployment}-in-flight", f"{deployment}-queued"

    async with (
        Worker(
            client,
            task_queue=queue,
            workflows=[CutoverProbe],
            deployment_config=_versioned(deployment, "old"),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            client,
            task_queue=activity_queue,
            activities=[_probe("old", first_seen)],
            deployment_config=_versioned(deployment, "old"),
        ),
    ):
        await _make_current(client, deployment, "old", (queue, activity_queue))
        in_flight = await client.start_workflow(
            CutoverProbe.run,
            activity_queue,
            id=in_flight_id,
            task_queue=queue,
            execution_timeout=timedelta(seconds=300),
        )
        await asyncio.wait_for(first_seen.wait(), timeout=30)
    run_id = (await in_flight.describe()).run_id
    # The old fleet is gone; new work queues behind the old current version.
    queued = await client.start_workflow(
        CutoverProbe.run,
        activity_queue,
        id=queued_id,
        task_queue=queue,
        execution_timeout=timedelta(seconds=300),
    )

    # Missing-queue protection: nothing ordinary polls yet, so routing stays.
    refused = await run_cutover(client, deployment=deployment)
    assert refused["status"] == "pending"
    assert refused["outcome"] == "missing_queue"
    assert queue in refused["detail"] and activity_queue in refused["detail"]
    assert current_version(await describe_deployment(client, deployment)) == (
        f"{deployment}.old"
    )

    async with (
        Worker(
            client,
            task_queue=queue,
            workflows=[CutoverProbe],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(client, task_queue=activity_queue, activities=[_probe("ordinary")]),
    ):

        async def cutover():
            return await run_cutover(client, deployment=deployment)

        # Ordinary pollers register on their first poll.
        receipt = await _await_temporal_condition(
            "ordinary workers to poll every queue",
            cutover,
            attempts=30,
            interval_seconds=1,
            is_ready=lambda result: result["outcome"] != "missing_queue",
        )
        assert receipt["status"] == "completed", receipt
        assert receipt["outcome"] == "transitioned"
        assert receipt["previousRouting"]["currentVersion"] == f"{deployment}.old"
        assert receipt["autoUpgradeExecutions"] >= 1
        assert receipt["preservedPinned"] == []
        snapshot = await describe_deployment(client, deployment)
        assert current_version(snapshot) == ""
        assert ramping_version(snapshot) == ""

        # Old AUTO_UPGRADE work continues across a timer and an Activity
        # with its stable identity; queued and new work use ordinary workers.
        await in_flight.signal(CutoverProbe.resume)
        assert await asyncio.wait_for(in_flight.result(), 60) == ["old", "ordinary"]
        assert (await in_flight.describe()).run_id == run_id
        await queued.signal(CutoverProbe.resume)
        assert await asyncio.wait_for(queued.result(), 60) == [
            "ordinary",
            "ordinary",
        ]
        fresh = await client.start_workflow(
            CutoverProbe.run,
            activity_queue,
            id=f"{deployment}-new",
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
        await fresh.signal(CutoverProbe.resume)
        assert await asyncio.wait_for(fresh.result(), 60) == ["ordinary", "ordinary"]

        # A lost acknowledgment reruns the same transition: it rereads actual
        # routing and changes nothing.
        again = await run_cutover(client, deployment=deployment)
        assert again["status"] == "completed"
        assert again["outcome"] == "already_unversioned"

    # The crossing history replays deterministically on ordinary code.
    await Replayer(
        workflows=[CutoverProbe], workflow_runner=UnsandboxedWorkflowRunner()
    ).replay_workflow(await in_flight.fetch_history())


async def _make_ramping(client, deployment, build, queues, percentage):
    """Recreate an older installation's canary ramp toward another version."""
    version = f"{deployment}.{build}"
    await _await_registered(client, version, queues)
    snapshot = await describe_deployment(client, deployment)
    await client.workflow_service.set_worker_deployment_ramping_version(
        SetWorkerDeploymentRampingVersionRequest(
            namespace=client.namespace,
            deployment_name=deployment,
            version=version,
            percentage=percentage,
            conflict_token=snapshot.conflict_token,
            identity="older-installation",
        )
    )
    assert ramping_version(await describe_deployment(client, deployment)) == version


async def test_cutover_clears_a_ramp_and_queued_work_reaches_ordinary_workers():
    client = await connect()
    deployment = f"cutover-ramp-{uuid4().hex}"
    queue, activity_queue = f"{deployment}-workflow", f"{deployment}-activity"
    queues = (queue, activity_queue)

    async with (
        Worker(
            client,
            task_queue=queue,
            workflows=[CutoverProbe],
            deployment_config=_versioned(deployment, "old"),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            client,
            task_queue=activity_queue,
            activities=[_probe("old")],
            deployment_config=_versioned(deployment, "old"),
        ),
        Worker(
            client,
            task_queue=queue,
            workflows=[CutoverProbe],
            deployment_config=_versioned(deployment, "new"),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            client,
            task_queue=activity_queue,
            activities=[_probe("new")],
            deployment_config=_versioned(deployment, "new"),
        ),
    ):
        await _make_current(client, deployment, "old", queues)
        await _make_ramping(client, deployment, "new", queues, 50)
    # Both versioned fleets are gone; new work queues behind the ramp.
    queued = await client.start_workflow(
        CutoverProbe.run,
        activity_queue,
        id=f"{deployment}-queued",
        task_queue=queue,
        execution_timeout=timedelta(seconds=300),
    )

    async with (
        Worker(
            client,
            task_queue=queue,
            workflows=[CutoverProbe],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(client, task_queue=activity_queue, activities=[_probe("ordinary")]),
    ):
        receipt = await _await_temporal_condition(
            "ordinary workers to poll every queue",
            lambda: run_cutover(client, deployment=deployment),
            attempts=30,
            interval_seconds=1,
            is_ready=lambda result: result["outcome"] != "missing_queue",
        )
        assert receipt["status"] == "completed", receipt
        assert receipt["outcome"] == "transitioned"
        assert receipt["previousRouting"] == {
            "currentVersion": f"{deployment}.old",
            "rampingVersion": f"{deployment}.new",
        }
        snapshot = await describe_deployment(client, deployment)
        assert current_version(snapshot) == ""
        assert ramping_version(snapshot) == ""
        routing = snapshot.worker_deployment_info.routing_config
        assert routing.ramping_version_percentage == 0

        await queued.signal(CutoverProbe.resume)
        assert await asyncio.wait_for(queued.result(), 60) == [
            "ordinary",
            "ordinary",
        ]
        fresh = await client.start_workflow(
            CutoverProbe.run,
            activity_queue,
            id=f"{deployment}-new",
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
        await fresh.signal(CutoverProbe.resume)
        assert await asyncio.wait_for(fresh.result(), 60) == ["ordinary", "ordinary"]

        again = await run_cutover(client, deployment=deployment)
        assert again["status"] == "completed"
        assert again["outcome"] == "already_unversioned"


async def test_cutover_retires_release_canaries_and_preserves_unexpected_pins():
    client = await connect()
    deployment = f"cutover-pins-{uuid4().hex}"
    queue, activity_queue = f"{deployment}-workflow", f"{deployment}-activity"
    pinned_seen = asyncio.Event()
    pinned_override = PinnedVersioningOverride(
        WorkerDeploymentVersion(deployment, "old")
    )

    async with (
        Worker(
            client,
            task_queue=queue,
            workflows=[CutoverProbe, RetiredReleaseCanary],
            deployment_config=_versioned(deployment, "old"),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            client,
            task_queue=activity_queue,
            activities=[_probe("old", pinned_seen)],
            deployment_config=_versioned(deployment, "old"),
        ),
    ):
        await _make_current(client, deployment, "old", (queue, activity_queue))
        canary = await client.start_workflow(
            RetiredReleaseCanary.run,
            {"digest": "old"},
            id=f"mm-release-canary-{uuid4().hex}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=300),
            versioning_override=pinned_override,
        )
        # User work that is not a release canary keeps its pin.
        pinned = await client.start_workflow(
            CutoverProbe.run,
            activity_queue,
            id=f"{deployment}-pinned",
            task_queue=queue,
            execution_timeout=timedelta(seconds=300),
            versioning_override=pinned_override,
        )
        await asyncio.wait_for(pinned_seen.wait(), timeout=30)

    async with (
        Worker(
            client,
            task_queue=queue,
            workflows=[CutoverProbe],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(client, task_queue=activity_queue, activities=[_probe("ordinary")]),
    ):
        receipt = await _await_temporal_condition(
            "ordinary workers to poll every queue",
            lambda: run_cutover(client, deployment=deployment),
            attempts=30,
            interval_seconds=1,
            is_ready=lambda result: result["outcome"] != "missing_queue",
        )
        # Routing moved, but the pinned execution keeps the cutover open.
        assert receipt["status"] == "attention", receipt
        assert receipt["outcome"] == "transitioned"
        assert [item["workflowId"] for item in receipt["retiredCanaries"]] == [
            canary.id
        ]
        assert [item["workflowId"] for item in receipt["preservedPinned"]] == [
            pinned.id
        ]
        assert (await canary.describe()).status is WorkflowExecutionStatus.TERMINATED
        # Never terminated or reset: the pin is preserved for the operator.
        preserved = await pinned.describe()
        assert preserved.status is WorkflowExecutionStatus.RUNNING
        assert preserved.run_id == pinned.result_run_id

        # The supported explicit disposition moves it to ordinary workers.
        await client.workflow_service.update_workflow_execution_options(
            UpdateWorkflowExecutionOptionsRequest(
                namespace=client.namespace,
                workflow_execution={"workflow_id": pinned.id},
                workflow_execution_options=WorkflowExecutionOptions(
                    versioning_override=VersioningOverride(auto_upgrade=True)
                ),
                update_mask=FieldMask(paths=["versioning_override"]),
            )
        )
        await pinned.signal(CutoverProbe.resume)
        assert await asyncio.wait_for(pinned.result(), 60) == ["old", "ordinary"]
        settled = await run_cutover(client, deployment=deployment)
        assert settled["status"] == "completed", settled
        assert settled["outcome"] == "already_unversioned"


async def test_fresh_installation_cutover_is_a_proven_no_op():
    client = await connect()
    deployment = f"never-versioned-{uuid4().hex}"
    receipt = await run_cutover(client, deployment=deployment)
    assert receipt == {
        "deployment": deployment,
        "namespace": client.namespace,
        "status": "completed",
        "outcome": "fresh",
    }
    assert await describe_deployment(client, deployment) is None


async def test_manual_trigger_correlates_authored_identity_and_reports_overlap():
    from datetime import datetime, timezone

    from moonmind.workflows.temporal.client import TemporalClientAdapter

    client = await connect()
    definition = uuid4()
    queue = f"manual-trigger-{definition.hex}"
    adapter = TemporalClientAdapter(client=client)
    async with Worker(
        client,
        task_queue=queue,
        workflows=[CutoverProbe],
        activities=[],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        schedule = await client.create_schedule(
            f"mm-schedule:{definition}",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    CutoverProbe.run,
                    queue,
                    id=f"manual:{definition}",
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=30),
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]
                ),
            ),
        )
        marker = datetime.now(timezone.utc)
        try:
            await adapter.trigger_schedule(
                definition_id=definition, request_id=str(uuid4()), scheduled_at=marker
            )

            async def _observe_trigger():
                return await adapter.observe_schedule_trigger(
                    definition_id=definition, scheduled_at=marker
                )

            result = await _await_temporal_condition(
                "manual schedule trigger to report started",
                _observe_trigger,
                attempts=50,
                interval_seconds=0.1,
                is_ready=lambda outcome: outcome.disposition == "started",
            )
            assert result.disposition == "started"
            assert result.scheduled_at == marker
            assert result.workflow_id and result.run_id
            assert (
                await adapter.observe_schedule_trigger(
                    definition_id=definition,
                    scheduled_at=marker + timedelta(microseconds=1),
                )
            ).disposition == "pending"
            skipped = await adapter.trigger_schedule(definition_id=definition)
            assert skipped.disposition == "skipped"
            assert skipped.workflow_id == result.workflow_id
            assert len((await schedule.describe()).info.recent_actions) == 1
        finally:
            for action in (await schedule.describe()).info.running_actions:
                await client.get_workflow_handle(action.workflow_id).terminate()
            await schedule.delete()
