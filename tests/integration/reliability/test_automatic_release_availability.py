"""Real Docker removal, immutable receipt restoration, and Temporal traffic."""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.common import PinnedVersioningOverride, WorkerDeploymentVersion

from moonmind.release_identity import build_release
from moonmind.workflows.skills import deployment_availability as availability
from moonmind.workflows.skills import deployment_release as release
from moonmind.workflows.skills.deployment_execution import (
    DeploymentUpdateLockManager,
    HostDockerComposeRunner,
)
from moonmind.workflows.temporal import worker_runtime, workers
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.release_routing import (
    bootstrap_version_routing,
    promote_version,
)
from tests.integration.reliability.test_release_routing_journey import connect

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("retained_only", [False, True])
async def test_background_owner_restores_authoritative_missing_image(
    tmp_path, monkeypatch, retained_only
):
    client = await connect()
    key = uuid4().hex
    deployment = f"availability-{key}"
    queue = deployment + "-user"
    merge_queue = deployment + "-merge"
    network = os.environ.get(
        "MOONMIND_TEST_DOCKER_NETWORK", "moonmind-reliability-qualification_default"
    )
    root = Path(__file__).resolve().parents[3]
    image_root = tmp_path / "image"
    image_root.mkdir()
    for relative in (
        "moonmind/release_identity.py",
        "moonmind/workflows/temporal/worker_lifecycle.py",
        "moonmind/workflows/temporal/workflows/release_canary.py",
    ):
        target = image_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
    (image_root / "worker.py").write_bytes(
        (root / "tests/fixtures/reliability/release_worker.py").read_bytes()
    )
    (image_root / "Dockerfile").write_text(
        f"FROM python:3.12-slim\nRUN pip install temporalio=={version('temporalio')}\n"
        'WORKDIR /app\nCOPY . /app\nCMD ["python","worker.py"]\n'
    )
    manifest = build_release(image_root, revision=key)
    (image_root / ".moonmind-release.json").write_text(json.dumps(manifest))
    digest = manifest["digest"]
    record_root = tmp_path / "release-jobs"
    prior = record_root / key
    prior.mkdir(parents=True)
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    monkeypatch.setattr(workers, "_FLEET_SERVICE_NAMES", {"workflow": "worker"})
    project = f"moonmind-test-availability-{key[:12]}"
    compose = tmp_path / "compose.yaml"
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path), compose_file=str(compose), project_name=project
    )
    executor = SimpleNamespace(
        runner=runner, lock_manager=DeploymentUpdateLockManager()
    )
    monkeypatch.setattr(
        worker_runtime, "_build_deployment_update_executor", lambda: executor
    )
    from moonmind.workflows.temporal import client as client_module

    monkeypatch.setattr(
        client_module,
        "get_temporal_client",
        lambda *_args: asyncio.sleep(0, result=client),
    )
    image = None
    replacement_image = None
    background = None
    schedule = None
    try:
        image = f"moonmind-test-availability:{key}"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            for path in image_root.rglob("*"):
                if path.is_file():
                    tar.add(path, arcname=path.relative_to(image_root))
        await release.docker("build", "-t", image, "-", input_bytes=archive.getvalue())
        image_info = json.loads(await release.docker("image", "inspect", image))[0]
        immutable = image_info["Id"]
        compose.write_text(
            json.dumps(
                {
                    "services": {
                        "worker": {
                            "image": "${MOONMIND_IMAGE}",
                            "environment": {
                                "TEMPORAL_ADDRESS": "temporal:7233",
                                "DEPLOYMENT": deployment,
                                "QUEUES": json.dumps([queue, merge_queue]),
                                "TEMPORAL_WORKER_VERSIONING_ENABLED": "auto",
                            },
                            "networks": ["test"],
                        }
                    },
                    "networks": {"test": {"external": True, "name": network}},
                }
            )
        )
        # Use the production Compose runner, including its immutable-image env.
        launched = await runner._run_compose_command(
            ("docker", "compose", "up", "-d"), requested_image=immutable
        )
        release._ensure_command_succeeded("start fixture release", launched)
        spec = SimpleNamespace(
            versioning_enabled=True,
            workflows=("canary",),
            deployment_id=deployment,
            build_id=digest,
            task_queues=(queue,),
        )
        await bootstrap_version_routing(client, spec)
        release.write_record(
            prior / "request.json",
            {
                "authored": {"owner": key, "inputs": {"sourceRevision": key}},
                "image": immutable,
                "imageId": image_info["Id"],
                "deadline": 1,
            },
        )
        release.write_record(
            prior / "routing.json",
            {
                "deployment": deployment,
                "candidate": digest,
                "previous": "__unversioned__",
            },
        )
        receipt = {
            "owner": key,
            "result": {
                "status": "COMPLETED",
                "outputs": {
                    "resolvedDigest": immutable,
                },
            },
        }
        release.write_record(prior / "deployment-result.json", receipt)
        release.write_record(prior / "result.json", receipt)
        removed = await runner._run_compose_command(
            ("docker", "compose", "down"), requested_image=immutable
        )
        release._ensure_command_succeeded("remove prior workers", removed)
        # Install a newer, unqualified image. It has no promotion authority.
        newer = build_release(image_root, revision=key + "-newer")
        (image_root / ".moonmind-release.json").write_text(json.dumps(newer))
        replacement_image = image + "-newer"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            for path in image_root.rglob("*"):
                if path.is_file():
                    tar.add(path, arcname=path.relative_to(image_root))
        await release.docker(
            "build", "-t", replacement_image, "-", input_bytes=archive.getvalue()
        )
        replacement_id = json.loads(
            await release.docker("image", "inspect", replacement_image)
        )[0]["Id"]
        compose.write_text(
            compose.read_text().replace(
                "${MOONMIND_IMAGE}", "${MOONMIND_IMAGE:-" + replacement_id + "}"
            )
        )
        installed = await runner._run_compose_command(
            ("docker", "compose", "up", "-d"), requested_image=replacement_id
        )
        release._ensure_command_succeeded("install unqualified replacement", installed)
        spec.build_id = newer["digest"]
        assert (await bootstrap_version_routing(client, spec))[
            "status"
        ] == "awaiting_promotion"
        if retained_only:
            # First controlled upgrade: A has only the new job's previous-image
            # receipt, while B is the authorized current version.
            request = json.loads((prior / "request.json").read_text())
            request.update(image=replacement_id, imageId=replacement_id)
            request["authored"]["inputs"]["sourceRevision"] = key + "-newer"
            release.write_record(prior / "request.json", request)
            release.write_record(
                prior / "routing.json",
                {
                    "deployment": deployment,
                    "candidate": newer["digest"],
                    "previous": f"{deployment}.{digest}",
                },
            )
            release.write_record(
                prior / "retained.json",
                {
                    "owner": key,
                    "version": f"{deployment}.{digest}",
                    "image": immutable,
                    "retired": [],
                },
            )
            receipt["result"]["outputs"]["resolvedDigest"] = replacement_id
            release.write_record(prior / "deployment-result.json", receipt)
            release.write_record(prior / "result.json", receipt)
            await promote_version(
                client,
                deployment=deployment,
                build_id=newer["digest"],
                expected_current=f"{deployment}.{digest}",
                task_queue=queue,
                task_queues=(queue, merge_queue),
                canary_id=key + "-promote",
            )
        original_command = HostDockerComposeRunner._run_compose_command
        lost_ack = False

        async def command_with_lost_ack(self, command, **kwargs):
            nonlocal lost_ack
            result = await original_command(self, command, **kwargs)
            if (
                "run" in command
                and any(str(item).startswith("mm-retained-") for item in command)
                and not lost_ack
            ):
                lost_ack = True
                return {"exitCode": 1, "stderr": "launch acknowledgement lost"}
            return result

        monkeypatch.setattr(
            HostDockerComposeRunner, "_run_compose_command", command_with_lost_ack
        )
        definition = uuid4()
        schedule = await client.create_schedule(
            f"mm-schedule:{definition}",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "MoonMind.ReleaseCanary",
                    {"digest": digest, "taskQueues": [queue, merge_queue]},
                    id=f"mm:{definition}",
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=180),
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]
                ),
            ),
        )
        if retained_only:
            accepted = await client.start_workflow(
                "MoonMind.ReleaseCanary",
                {"digest": digest, "taskQueues": [queue, merge_queue]},
                id=key + "-pinned",
                task_queue=queue,
                versioning_override=PinnedVersioningOverride(
                    WorkerDeploymentVersion(deployment, digest)
                ),
                execution_timeout=timedelta(seconds=180),
            )
        else:
            adapter = TemporalClientAdapter(client=client)
            await adapter.trigger_schedule(definition_id=definition)
            for _ in range(50):
                actions = (await schedule.describe()).info.recent_actions
                if actions:
                    break
                await asyncio.sleep(0.1)
            accepted = client.get_workflow_handle(actions[-1].action.workflow_id)
        assert (await accepted.describe()).history_length == 2
        metadata = {}
        # Enter the actual startup/periodic owner. The test never invokes a
        # restoration or promotion helper to make the stranded occurrence run.
        background = asyncio.create_task(
            availability.supervise_availability(client, spec, metadata)
        )
        assert await asyncio.wait_for(accepted.result(), 150) == {
            "digest": digest,
            "status": "verified",
        }
        assert lost_ack
        assert any(
            json.loads(path.read_text()).get("sourceReceipt") == key
            for path in record_root.glob("*/retained.json")
        )
        for _ in range(100):
            if metadata.get("releaseAvailability", {}).get("versions"):
                break
            await asyncio.sleep(0.1)
        recovered = next(
            item
            for item in metadata["releaseAvailability"]["versions"]
            if item["version"] == f"{deployment}.{digest}"
        )
        assert recovered["phase"] == "available"
        if not retained_only:
            assert recovered["ordinaryTraffic"]["status"] == "verified"
        # Process replacement resumes the durable owner and original cohort.
        background.cancel()
        await asyncio.gather(background, return_exceptions=True)
        background = asyncio.create_task(
            availability.supervise_availability(client, spec, metadata)
        )
        # Ordinary unpinned traffic on the separate merge route must work too.
        current_digest = newer["digest"] if retained_only else digest
        assert await client.execute_workflow(
            "MoonMind.ReleaseCanary",
            {"digest": current_digest, "taskQueues": [merge_queue, queue]},
            id=key + "-ordinary",
            task_queue=merge_queue,
            execution_timeout=timedelta(seconds=30),
        ) == {"digest": current_digest, "status": "verified"}
        assert (
            (
                await availability.routing_snapshot(client, deployment)
            ).worker_deployment_info.routing_config.current_version
            == f"{deployment}.{current_digest}"
        )
    finally:
        if background is not None:
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)
        if schedule is not None:
            await schedule.delete()
        for path in record_root.glob("*/retained.json"):
            value = json.loads(path.read_text())
            cohort = release.ReleaseCohort(runner, path.parent, value["owner"])
            cohort.names = [f"mm-retained-{path.parent.name[:16]}-workflow"]
            await cohort.cleanup()
        if compose.exists():
            await runner._run_compose_command(
                ("docker", "compose", "down"), requested_image=image
            )
        if image is not None:
            await release.docker("image", "rm", "-f", image)
        if replacement_image is not None:
            await release.docker("image", "rm", "-f", replacement_image)
