"""Real Temporal routing, pinned candidate qualification and in-flight upgrade."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleIntervalSpec, ScheduleSpec
from temporalio.common import (
    PinnedVersioningOverride,
    VersioningBehavior,
    WorkerDeploymentVersion,
)
from temporalio.worker import UnsandboxedWorkflowRunner, Worker, WorkerDeploymentConfig

from moonmind import release_identity
from moonmind.workflows.temporal.release_routing import (
    bootstrap_version_routing,
    current_version,
    promote_version,
    routing_snapshot,
    version_drained,
)
from moonmind.workflows.temporal.workflows.release_canary import (
    ReleaseCanaryWorkflow,
    inspect_release_activity,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class ReleaseUpgradeProbe:
    def __init__(self):
        self.proceed = False

    @workflow.signal
    def resume(self):
        self.proceed = True

    @workflow.run
    async def run(self, activity_queue: str) -> list[str]:
        first = await workflow.execute_activity(
            "release.probe",
            task_queue=activity_queue,
            start_to_close_timeout=timedelta(seconds=20),
        )
        await workflow.wait_condition(lambda: self.proceed)
        second = await workflow.execute_activity(
            "release.probe",
            task_queue=activity_queue,
            start_to_close_timeout=timedelta(seconds=20),
        )
        return [first, second]


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


async def test_schedule_recovers_when_installed_release_replaces_absent_current_worker(
    tmp_path, monkeypatch,
):
    """Replay definition 68d074f1: healthy replacement, unroutable schedule."""
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    client = await connect()
    definition_id = uuid4()
    deployment = f"schedule-recovery-{definition_id.hex}"
    queue = deployment + "-workflow"
    root = tmp_path / "image"
    (root / "moonmind").mkdir(parents=True)
    source = root / "moonmind" / "entry.py"
    monkeypatch.setattr(release_identity, "__file__", str(source))

    def install(value):
        source.write_text(value)
        release = release_identity.build_release(root)
        (root / release_identity.RELEASE_FILE).write_text(json.dumps(release))
        return release["digest"]

    def worker_for(build):
        return Worker(
            client, task_queue=queue, workflows=[ReleaseCanaryWorkflow],
            activities=[inspect_release_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
            deployment_config=WorkerDeploymentConfig(
                version=WorkerDeploymentVersion(deployment, build),
                use_worker_versioning=True,
                default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
            ),
        )

    def spec(build):
        return SimpleNamespace(
            versioning_enabled=True, workflows=(ReleaseCanaryWorkflow,),
            deployment_id=deployment, build_id=build, task_queues=(queue,),
        )

    old = install("old")
    async with worker_for(old):
        await bootstrap_version_routing(client, spec(old))
    current = install("current")
    async with worker_for(current):
        assert (await bootstrap_version_routing(client, spec(current)))["status"] == "awaiting_promotion"
        schedule = await client.create_schedule(
            f"mm-schedule:{definition_id}",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "MoonMind.ReleaseCanary", {"digest": current, "taskQueues": [queue]},
                    id=f"mm:{definition_id}", task_queue=queue,
                    execution_timeout=timedelta(seconds=120),
                ),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]),
            ),
        )
        adapter = TemporalClientAdapter(client=client)
        try:
            await adapter.trigger_schedule(definition_id=definition_id)
            for _ in range(50):
                actions = (await schedule.describe()).info.recent_actions
                if actions:
                    break
                await asyncio.sleep(0.1)
            assert actions
            blocked = client.get_workflow_handle(actions[-1].action.workflow_id)
            assert (await blocked.describe()).history_length == 2
            await promote_version(
                client, deployment=deployment, build_id=current,
                expected_current=f"{deployment}.{old}", task_queue=queue,
                task_queues=(queue,), canary_id=deployment + "-canary",
            )
            assert await blocked.result() == {"digest": current, "status": "verified"}
            await adapter.trigger_schedule(definition_id=definition_id)
            for _ in range(50):
                actions = (await schedule.describe()).info.recent_actions
                if len(actions) == 2:
                    break
                await asyncio.sleep(0.1)
            assert len(actions) == 2
            fresh = client.get_workflow_handle(actions[-1].action.workflow_id)
            assert fresh.id != blocked.id
            assert await fresh.result() == {"digest": current, "status": "verified"}
        finally:
            await schedule.delete()


@pytest.mark.parametrize("pinned", [False, True])
async def test_candidate_canary_compare_and_set_and_inflight_upgrade(
    tmp_path, monkeypatch, pinned
):
    client = await connect()
    deployment = f"release-test-{uuid4().hex}"
    queue, activity_queue = f"{deployment}-workflow", f"{deployment}-activity"
    calls = []
    started = asyncio.Event()
    # The Activity reads a real image manifest outside source overlays. Only
    # its installation location differs from the installed /app layout.
    root = tmp_path / "image"
    (root / "moonmind").mkdir(parents=True)
    source = root / "moonmind" / "entry.py"
    source.write_text("release_a = True\n")
    manifest_a = release_identity.build_release(root)
    (root / release_identity.RELEASE_FILE).write_text(json.dumps(manifest_a))
    monkeypatch.setattr(
        release_identity, "__file__", str(root / "moonmind" / "release_identity.py")
    )

    def config(build):
        return WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(deployment, build),
            use_worker_versioning=True,
            default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
        )

    def probe(build):
        @activity.defn(name="release.probe")
        async def implementation():
            calls.append(build)
            started.set()
            return build

        return implementation

    def spec(build):
        return SimpleNamespace(
            versioning_enabled=True,
            workflows=(ReleaseCanaryWorkflow,),
            deployment_id=deployment,
            build_id=build,
            task_queues=(queue,),
        )

    a = manifest_a["digest"]
    async with (
        Worker(
            client,
            task_queue=activity_queue,
            activities=[probe(a), inspect_release_activity],
            deployment_config=config(a),
        ),
        Worker(
            client,
            task_queue=queue,
            workflows=[ReleaseCanaryWorkflow, ReleaseUpgradeProbe],
            activities=[inspect_release_activity],
            deployment_config=config(a),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        initialized = await bootstrap_version_routing(client, spec(a))
        assert initialized["currentVersion"] == f"{deployment}.{a}"
        handle = await client.start_workflow(
            ReleaseUpgradeProbe.run,
            activity_queue,
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(seconds=120),
            versioning_override=(
                PinnedVersioningOverride(WorkerDeploymentVersion(deployment, a))
                if pinned
                else None
            ),
        )
        await asyncio.wait_for(started.wait(), timeout=30)
        source.write_text("release_b = True\n")
        manifest_b = release_identity.build_release(root)
        (root / release_identity.RELEASE_FILE).write_text(json.dumps(manifest_b))
        b = manifest_b["digest"]
        async with Worker(
            client,
            task_queue=activity_queue,
            activities=[probe(b), inspect_release_activity],
            deployment_config=config(b),
        ), Worker(
            client,
            task_queue=queue,
            workflows=[ReleaseCanaryWorkflow, ReleaseUpgradeProbe],
            activities=[inspect_release_activity],
            deployment_config=config(b),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            candidate = await bootstrap_version_routing(client, spec(b))
            assert candidate["status"] == "awaiting_promotion"
            assert (
                current_version(await routing_snapshot(client, deployment))
                == f"{deployment}.{a}"
            )
            canary_id = uuid4().hex
            evidence = await promote_version(
                client,
                deployment=deployment,
                build_id=b,
                expected_current=f"{deployment}.{a}",
                task_queue=queue,
                task_queues=(queue, activity_queue),
                canary_id=canary_id,
            )
            assert evidence["verified"]
            first_canary = await client.get_workflow_handle(canary_id).describe()
            # Lose the caller's response and reconstruct it from server-owned
            # evidence. Neither the canary run nor its execution budget resets.
            resumed = await promote_version(
                client,
                deployment=deployment,
                build_id=b,
                expected_current=f"{deployment}.{a}",
                task_queue=queue,
                task_queues=(queue, activity_queue),
                canary_id=canary_id,
            )
            assert resumed == evidence
            assert (
                await client.get_workflow_handle(canary_id).describe()
            ).run_id == first_canary.run_id
            with pytest.raises(ValueError, match="routing changed"):
                await promote_version(
                    client,
                    deployment=deployment,
                    build_id=a,
                    expected_current=f"{deployment}.{a}",
                    task_queue=queue,
                )
            if pinned:
                assert not await version_drained(client, f"{deployment}.{a}")
            await handle.signal(ReleaseUpgradeProbe.resume)
            expected = [a, a] if pinned else [a, b]
            assert await handle.result() == expected
            assert calls == expected
            if pinned:
                for _ in range(50):
                    if await version_drained(client, f"{deployment}.{a}"):
                        break
                    await asyncio.sleep(1)
                else:
                    pytest.fail(
                        "Temporal did not confirm old pinned work drained after completion"
                    )


async def test_unversioned_inflight_workflow_moves_to_qualified_release(
    tmp_path, monkeypatch
):
    client = await connect()
    deployment = f"migration-{uuid4().hex}"
    queue = deployment + "-workflow"
    activity_queue = deployment + "-activity"
    first_seen = asyncio.Event()
    root = tmp_path / "image"
    (root / "moonmind").mkdir(parents=True)
    (root / "moonmind" / "entry.py").write_text("qualified = True\n")
    release = release_identity.build_release(root)
    (root / release_identity.RELEASE_FILE).write_text(json.dumps(release))
    monkeypatch.setattr(
        release_identity, "__file__", str(root / "moonmind" / "release_identity.py")
    )

    @activity.defn(name="release.probe")
    async def old_probe():
        first_seen.set()
        return "unversioned"

    @activity.defn(name="release.probe")
    async def candidate_probe():
        return release["digest"]

    config = WorkerDeploymentConfig(
        version=WorkerDeploymentVersion(deployment, release["digest"]),
        use_worker_versioning=True,
        default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
    )
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ReleaseUpgradeProbe],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ), Worker(
        client,
        task_queue=activity_queue,
        activities=[old_probe],
    ):
        handle = await client.start_workflow(
            ReleaseUpgradeProbe.run,
            activity_queue,
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(seconds=120),
        )
        await asyncio.wait_for(first_seen.wait(), 30)
        async with Worker(
            client,
            task_queue=queue,
            workflows=[ReleaseUpgradeProbe, ReleaseCanaryWorkflow],
            activities=[inspect_release_activity],
            deployment_config=config,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(
            client,
            task_queue=activity_queue,
            activities=[candidate_probe, inspect_release_activity],
            deployment_config=config,
        ):
            for attempt in range(60):
                try:
                    previous = current_version(
                        await routing_snapshot(client, deployment)
                    )
                    break
                except Exception:
                    if attempt == 59:
                        raise
                    await asyncio.sleep(1)
            assert previous in {"", "__unversioned__"}
            await promote_version(
                client,
                deployment=deployment,
                build_id=release["digest"],
                expected_current=previous,
                task_queue=queue,
                task_queues=(queue, activity_queue),
            )
            await handle.signal(ReleaseUpgradeProbe.resume)
            assert await handle.result() == ["unversioned", release["digest"]]


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
        workflows=[ReleaseUpgradeProbe],
        activities=[],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        schedule = await client.create_schedule(
            f"mm-schedule:{definition}",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    ReleaseUpgradeProbe.run,
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
            result = await adapter.trigger_schedule(
                definition_id=definition, request_id=str(uuid4()), scheduled_at=marker
            )
            for _ in range(50):
                result = await adapter.observe_schedule_trigger(
                    definition_id=definition, scheduled_at=marker
                )
                if result.disposition == "started":
                    break
                await asyncio.sleep(0.1)
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
