"""Real Temporal routing, pinned candidate qualification and in-flight upgrade."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.common import (
    PinnedVersioningOverride,
    VersioningBehavior,
    WorkerDeploymentVersion,
)
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import UnsandboxedWorkflowRunner, Worker, WorkerDeploymentConfig

from moonmind import release_identity
from moonmind.workflows.temporal import release_routing
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


async def _await_temporal_condition(
    description, fetch, *, attempts, interval_seconds, is_ready=None
):
    """Bounded real-dependency polling helper (MoonLadderStudios/MoonMind#4374).

    Deduplicates the repeated ``for _ in range(N): ... await asyncio.sleep()``
    polling loops against the real isolated Temporal service. Every call keeps
    real production recovery coverage: real server observations with real
    short waits; only the duplicated loop scaffolding is shared. A fetch that
    keeps raising re-raises its last error (matching the previous inline
    ``if attempt == N-1: raise`` shape); a condition that never becomes ready
    fails loudly instead of passing silently. Production
    poller-freshness/recovery windows are untouched.
    """
    last_error = None
    last_value = None
    for attempt in range(attempts):
        try:
            last_value = await fetch()
        except Exception as exc:  # noqa: BLE001 - retried as not-ready
            last_error = exc
            last_value = None
            ready = False
        else:
            last_error = None
            ready = is_ready(last_value) if is_ready is not None else bool(last_value)
            if ready:
                return last_value
        if attempt == attempts - 1:
            break
        await asyncio.sleep(interval_seconds)
    if last_error is not None:
        raise last_error
    pytest.fail(f"Timed out waiting for real Temporal condition: {description}")
    raise AssertionError(f"unreachable: {description}")  # pragma: no cover


async def test_schedule_recovers_when_installed_release_replaces_absent_current_worker(
    tmp_path,
    monkeypatch,
):
    """Replay definition 68d074f1: healthy replacement, unroutable schedule.

    The replacement fleet converges routing itself: bootstrap promotes the
    installed release after its pinned canary verifies, so the scheduled run
    is routable without waiting for a separate promotion owner.
    """
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
            client,
            task_queue=queue,
            workflows=[ReleaseCanaryWorkflow],
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
            versioning_enabled=True,
            workflows=(ReleaseCanaryWorkflow,),
            deployment_id=deployment,
            build_id=build,
            task_queues=(queue,),
        )

    old = install("old")
    async with worker_for(old):
        await bootstrap_version_routing(client, spec(old))
    current = install("current")
    # Reproduce the updater incident: the server applies the handoff, then
    # its confirmation query temporarily exhausts the consistent-query
    # buffer. Keep all routing, canaries, and subsequent schedules real.
    snapshot = release_routing.routing_snapshot
    injected = False

    async def overloaded_confirmation(client, deployment):
        nonlocal injected
        result = await snapshot(client, deployment)
        if not injected and current_version(result) == f"{deployment}.{current}":
            injected = True
            raise RPCError(
                "consistent query buffer is full",
                RPCStatusCode.RESOURCE_EXHAUSTED,
                None,
            )
        return result

    monkeypatch.setattr(release_routing, "routing_snapshot", overloaded_confirmation)
    async with worker_for(current):
        converged = await bootstrap_version_routing(client, spec(current))
        assert injected
        assert converged["status"] == "current"
        assert converged["currentVersion"] == f"{deployment}.{current}"
        schedule = await client.create_schedule(
            f"mm-schedule:{definition_id}",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "MoonMind.ReleaseCanary",
                    {"digest": current, "taskQueues": [queue]},
                    id=f"mm:{definition_id}",
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=120),
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]
                ),
            ),
        )
        adapter = TemporalClientAdapter(client=client)
        try:
            # Pin both manual triggers to one scheduled instant so Temporal
            # derives the same occurrence ID deterministically. Wall-clock
            # triggers straddle a second boundary and flake the same-ID
            # assertion below with IDs like ...42Z vs ...43Z.
            scheduled_at = datetime.now(timezone.utc)
            await adapter.trigger_schedule(
                definition_id=definition_id, scheduled_at=scheduled_at
            )

            async def _recent_actions():
                return (await schedule.describe()).info.recent_actions

            actions = await _await_temporal_condition(
                "scheduled run to appear in recent actions",
                _recent_actions,
                attempts=50,
                interval_seconds=0.1,
            )
            assert actions
            started = client.get_workflow_handle(actions[-1].action.workflow_id)
            assert await started.result() == {"digest": current, "status": "verified"}
            first_run_id = (await started.describe()).run_id
            await adapter.trigger_schedule(
                definition_id=definition_id, scheduled_at=scheduled_at
            )
            actions = await _await_temporal_condition(
                "second scheduled run to appear in recent actions",
                _recent_actions,
                attempts=50,
                interval_seconds=0.1,
                is_ready=lambda recent: len(recent) == 2,
            )
            assert len(actions) == 2
            fresh = client.get_workflow_handle(actions[-1].action.workflow_id)
            assert fresh.id == started.id

            async def _fresh_run_id():
                return (await fresh.describe()).run_id

            assert (
                await _await_temporal_condition(
                    "rescheduled run to start a new run id",
                    _fresh_run_id,
                    attempts=100,
                    interval_seconds=0.1,
                    is_ready=lambda run_id: run_id != first_run_id,
                )
                != first_run_id
            )
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
            # Startup stewardship may re-verify a seemingly live route past
            # the poller-freshness window before converging it.
            execution_timeout=timedelta(seconds=600),
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
        async with (
            Worker(
                client,
                task_queue=activity_queue,
                activities=[probe(b), inspect_release_activity],
                deployment_config=config(b),
            ),
            Worker(
                client,
                task_queue=queue,
                workflows=[ReleaseCanaryWorkflow, ReleaseUpgradeProbe],
                activities=[inspect_release_activity],
                deployment_config=config(b),
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
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
                assert await _await_temporal_condition(
                    "Temporal to confirm old pinned work drained after completion",
                    lambda: version_drained(client, f"{deployment}.{a}"),
                    attempts=50,
                    interval_seconds=1,
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
    async with (
        Worker(
            client,
            task_queue=queue,
            workflows=[ReleaseUpgradeProbe],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            client,
            task_queue=activity_queue,
            activities=[old_probe],
        ),
    ):
        handle = await client.start_workflow(
            ReleaseUpgradeProbe.run,
            activity_queue,
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(seconds=120),
        )
        await asyncio.wait_for(first_seen.wait(), 30)
        async with (
            Worker(
                client,
                task_queue=queue,
                workflows=[ReleaseUpgradeProbe, ReleaseCanaryWorkflow],
                activities=[inspect_release_activity],
                deployment_config=config,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                client,
                task_queue=activity_queue,
                activities=[candidate_probe, inspect_release_activity],
                deployment_config=config,
            ),
        ):

            async def _previous_version():
                return current_version(await routing_snapshot(client, deployment))

            # Any successful snapshot (including "" / "__unversioned__")
            # counts as ready; persistent errors re-raise.
            previous = await _await_temporal_condition(
                "routing snapshot to become readable",
                _previous_version,
                attempts=60,
                interval_seconds=1,
                is_ready=lambda _version: True,
            )
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

            async def _observe_trigger():
                return await adapter.observe_schedule_trigger(
                    definition_id=definition, scheduled_at=marker
                )

            # The canary/upgrade journey above remains the real production
            # recovery coverage; this observation poll keeps its real waits
            # behind the shared helper (MoonLadderStudios/MoonMind#4374).
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
