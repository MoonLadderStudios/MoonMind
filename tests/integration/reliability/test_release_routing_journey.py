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
from temporalio.client import Client
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
    async with Worker(
        client,
        task_queue=activity_queue,
        activities=[probe(a)],
        deployment_config=config(a),
    ), Worker(
        client,
        task_queue=queue,
        workflows=[ReleaseCanaryWorkflow, ReleaseUpgradeProbe],
        activities=[inspect_release_activity],
        deployment_config=config(a),
        workflow_runner=UnsandboxedWorkflowRunner(),
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
