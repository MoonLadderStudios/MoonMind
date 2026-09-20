import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from moonmind.workflows.skills import deployment_release as release


@pytest.mark.asyncio
@pytest.mark.parametrize("has_previous_report", [False, True])
async def test_supervisor_publishes_complete_observation_after_inventory(
    tmp_path, monkeypatch, has_previous_report
):
    from unittest.mock import AsyncMock

    from moonmind.workflows.skills import deployment_availability as availability
    from moonmind.workflows.skills import deployment_maintenance as maintenance
    from moonmind.workflows.skills.deployment_execution import (
        DeploymentUpdateLockManager,
        HostDockerComposeRunner,
    )
    from moonmind.workflows.temporal import worker_runtime

    executor = SimpleNamespace(
        runner=HostDockerComposeRunner(project_dir=str(tmp_path)),
        lock_manager=DeploymentUpdateLockManager(),
    )
    monkeypatch.setattr(worker_runtime, "_build_deployment_update_executor", lambda: executor)
    monkeypatch.setattr(availability, "state_root", lambda: tmp_path)
    monkeypatch.setattr(
        availability, "reconcile_availability",
        AsyncMock(return_value={"current": "test.current", "versions": []}),
    )
    inventory_started, finish_inventory, stop = (
        asyncio.Event(), asyncio.Event(), asyncio.Event()
    )

    async def inventory(_runner):
        inventory_started.set()
        await finish_inventory.wait()
        return {"distinctBuildIds": ["current"], "coherent": True}

    async def maintained():
        stop.set()
        return {}

    monkeypatch.setattr(availability, "installed_fleet_inventory", inventory)
    monkeypatch.setattr(maintenance, "reconcile_releases", maintained)
    prior = {"current": "test.previous", "routing": {"status": "current"}}
    metadata = {"releaseAvailability": prior} if has_previous_report else {}
    report_file = tmp_path / "availability.json"
    if has_previous_report:
        release.write_record(report_file, prior)
    spec = SimpleNamespace(deployment_id="test", build_id="current")
    task = asyncio.create_task(
        availability.supervise_availability(None, spec, metadata, stop=stop)
    )
    try:
        await asyncio.wait_for(inventory_started.wait(), 5)
        if has_previous_report:
            assert metadata["releaseAvailability"] == prior
            assert json.loads(report_file.read_text()) == prior
        else:
            assert "releaseAvailability" not in metadata
            assert not report_file.exists()
        finish_inventory.set()
        await asyncio.wait_for(task, 5)
        published = metadata["releaseAvailability"]
        assert published["current"] == "test.current"
        assert published["routing"] == metadata["releaseRouting"]
        assert published["routing"]["status"] == "current"
        assert published == json.loads(report_file.read_text())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "children,expected",
    [
        (
            [
                {"ready": True, "buildId": "candidate"},
                {"ready": True, "buildId": "candidate"},
            ],
            True,
        ),
        (
            [
                {"ready": True, "buildId": "candidate"},
                {"ready": True, "buildId": "old"},
            ],
            False,
        ),
        (
            [
                {"ready": True, "buildId": "candidate"},
                {"ready": False, "buildId": "candidate"},
            ],
            False,
        ),
        ([{"ready": True}], False),
        ([], False),
    ],
)
def test_release_qualification_validates_every_supervised_child(children, expected):
    assert (
        release.readiness_matches({"ready": True, "children": children}, "candidate")
        is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("module_entrypoint", [False, True])
@pytest.mark.parametrize("operator_urls", [None, ["http://installed.example:7000"]])
async def test_detached_update_reconciles_lost_launch_ack_and_reuses_terminal_receipt(
    tmp_path, monkeypatch, module_entrypoint, operator_urls
):
    execute = release.execute_detached
    if module_entrypoint:
        import runpy
        import sys

        # Run the actual __main__ branch without dispatching a deployment. Its
        # functions retain __name__ == '__main__', the escaped launcher case.
        with monkeypatch.context() as launch_context:
            launch_context.setattr(
                sys, "argv", [release.__file__, str(tmp_path / "request.json")]
            )
            launch_context.setattr(
                release.asyncio, "run", lambda pending: pending.close()
            )
            namespace = runpy.run_path(release.__file__, run_name="__main__")
        execute = namespace["execute_detached"]
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    owner = "workflow:deployment:step:1"
    digest = "sha256:" + "a" * 64
    inputs = {
        "stack": "moonmind",
        "image": {"repository": "example/moonmind", "reference": "candidate"},
    }
    context = {"idempotency_key": owner, "principal": "system:deployment"}
    if operator_urls is not None:
        context["deployment_operator_urls"] = operator_urls
    container = None
    launches = []

    class Runner:
        async def pull(self, **kwargs):
            return {"exitCode": 0}

        async def inspect_image(self, requested):
            return {"Id": "image-id", "RepoDigests": [f"example/moonmind@{digest}"]}

        async def _run_compose_command(self, command, **kwargs):
            nonlocal container
            launches.append((command, kwargs))
            assert command[-2] == "moonmind.workflows.skills.deployment_release"
            request = release.Path(command[-1])
            record = json.loads(request.read_text())
            assert record["image"] == f"example/moonmind@{digest}"
            assert record["authored"]["context"].get("deployment_operator_urls") == operator_urls
            release.write_record(
                request.parent / "result.json",
                {
                    "owner": owner,
                    "result": {
                        "status": "COMPLETED",
                        "outputs": {"verified": True},
                        "progress": {},
                    },
                },
            )
            container = {"Image": "image-id", "State": {"Running": False}}
            return {"exitCode": 1, "stderr": "client connection lost after launch"}

    async def inspect(*args):
        return container

    async def docker(*args):
        nonlocal container
        assert args[0] in {"rm", "update"}
        if args[0] == "rm":
            container = None
        return ""

    monkeypatch.setattr(release, "inspect_owned", inspect)
    monkeypatch.setattr(release, "docker", docker)

    async def coherent(*args):
        return {}

    monkeypatch.setattr(release, "require_coherent_images", coherent)
    if module_entrypoint:
        for name, value in (
            ("inspect_owned", inspect),
            ("docker", docker),
            ("require_coherent_images", coherent),
        ):
            monkeypatch.setitem(execute.__globals__, name, value)
    executor = SimpleNamespace(runner=Runner())
    first = await execute(executor, inputs, context)
    second = await execute(executor, inputs, context)
    assert first == second
    assert len(launches) == 1
    changed = {**inputs, "reason": "different authorized operation"}
    with pytest.raises(ValueError, match="different inputs"):
        await execute(executor, changed, context)
    with pytest.raises(ValueError, match="different inputs"):
        await execute(executor, inputs, {**context, "deployment_operator_urls": ["http://different.example:7000"]})


@pytest.mark.asyncio
async def test_unreadable_daemon_is_not_an_absent_owner(monkeypatch):
    async def unavailable(*args):
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(release, "docker", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        await release.inspect_owned("owned-container", "execution")


@pytest.mark.asyncio
async def test_foreign_container_is_never_adopted(monkeypatch):
    async def daemon(*args):
        if args[0] == "ps":
            return "container-id"
        return json.dumps(
            [{"Config": {"Labels": {"moonmind.release.owner": "another-owner"}}}]
        )

    monkeypatch.setattr(release, "docker", daemon)
    with pytest.raises(ValueError, match="ownership differs"):
        await release.inspect_owned("owned-container", "execution")


@pytest.mark.asyncio
async def test_typed_ramping_route_remains_in_recovery_set(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from temporalio.api.deployment.v1 import (
        RoutingConfig,
        WorkerDeploymentInfo,
        WorkerDeploymentVersion,
    )
    from temporalio.api.workflowservice.v1 import DescribeWorkerDeploymentResponse
    from moonmind.workflows.skills import deployment_availability as availability

    snapshot = DescribeWorkerDeploymentResponse(
        worker_deployment_info=WorkerDeploymentInfo(
            routing_config=RoutingConfig(
                current_deployment_version=WorkerDeploymentVersion(
                    deployment_name="test", build_id="current"
                ),
                ramping_deployment_version=WorkerDeploymentVersion(
                    deployment_name="test", build_id="ramping"
                ),
                ramping_version_percentage=10,
            )
        )
    )
    monkeypatch.setattr(
        availability, "routing_snapshot", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(availability, "docker", AsyncMock(return_value=""))
    broken = tmp_path / "broken-unrelated"
    broken.mkdir()
    (broken / "routing.json").write_text("{broken")
    (broken / "deployment-result.json").write_text("{broken")
    (broken / "request.json").write_text("{broken")
    (broken / "retained.json").write_text("{broken")
    missing = tmp_path / "missing-authority"
    missing.mkdir()
    release.write_record(missing / "routing.json", {"deployment": "test"})
    release.write_record(missing / "request.json", {"authored": {}})
    release.write_record(missing / "retained.json", {})
    observed = []

    async def observe(_client, version, **_kwargs):
        observed.append(version)
        return {"version": version, "available": True, "queues": []}

    monkeypatch.setattr(availability, "version_availability", observe)
    result = await availability.reconcile_availability(
        None, deployment="test", runner=None, root=tmp_path
    )
    assert observed == ["test.current", "test.ramping"]
    assert len(result["versions"]) == 2
    assert len(result["discoveryErrors"]) == 3
    assert {"record": "missing-authority", "errorCode": "ValueError"} in result[
        "discoveryErrors"
    ]


@pytest.mark.asyncio
async def test_retained_only_receipt_requires_exact_owner_and_manifest(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    deployment = "existing-install"
    digest = "sha256:" + "a" * 64
    image = "sha256:" + "b" * 64
    version = deployment + "." + digest
    directory = tmp_path / "upgrade"
    directory.mkdir()
    release.write_record(directory / "request.json", {"authored": {"owner": "upgrade"}})
    release.write_record(
        directory / "routing.json",
        {"deployment": deployment, "candidate": "new", "previous": version},
    )
    release.write_record(
        directory / "retained.json",
        {"owner": "upgrade", "version": version, "image": image},
    )
    docker = AsyncMock(
        side_effect=[
            json.dumps([{"Id": image}]),
            json.dumps({"digest": "wrong", "sourceRevision": "source"}),
        ]
    )
    monkeypatch.setattr(release, "docker", docker)
    with pytest.raises(ValueError, match="manifest differs"):
        await release.successful_release_image(tmp_path, version)
    release.write_record(
        directory / "retained.json",
        {"owner": "foreign", "version": version, "image": image},
    )
    errors = []
    assert (
        list(release.retained_release_records(tmp_path, deployment, errors=errors))
        == []
    )
    assert errors == [{"record": "upgrade", "errorCode": "ValueError"}]
    assert await release.successful_release_image(tmp_path, version) is None
    release.write_record(
        directory / "retained.json",
        {"owner": "upgrade", "version": "foreign", "image": image},
    )
    assert list(release.retained_release_records(tmp_path, deployment)) == []
    assert docker.await_count == 2


@pytest.mark.asyncio
async def test_serving_receipt_survives_observation_loss_without_an_update_job(
    tmp_path, monkeypatch
):
    import hashlib
    from unittest.mock import AsyncMock

    deployment = "compose-install"
    digest = "sha256:" + "a" * 64
    image = "sha256:" + "b" * 64
    version = deployment + "." + digest
    key = hashlib.sha256(version.encode()).hexdigest()[:32]
    directory = tmp_path / key
    directory.mkdir()
    receipt = {
        "owner": f"release-availability:{version}",
        "version": version,
        "image": image,
    }
    release.write_record(directory / "retained.json", receipt)
    # Observation is mutable and may be lost or exhausted. It cannot revoke
    # the image receipt needed to recover the same current or pinned version.
    release.write_record(directory / "availability.json", {"phase": "exhausted"})
    docker = AsyncMock(side_effect=[
        json.dumps([{"Id": image}]),
        json.dumps({"digest": digest, "sourceRevision": "source"}),
    ])
    monkeypatch.setattr(release, "docker", docker)
    assert list(release.retained_release_records(tmp_path, deployment)) == [
        (directory, receipt)
    ]
    assert await release.successful_release_image(tmp_path, version) == {
        "image": image, "sourceReceipt": key, "sourceRevision": "source",
    }
    # A copied receipt cannot claim a different availability owner's directory.
    directory.rename(tmp_path / "foreign")
    assert list(release.retained_release_records(tmp_path, deployment)) == []
    assert await release.successful_release_image(tmp_path, version) is None
    assert docker.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("coherent", [False, True])
async def test_recording_serving_image_requires_coherent_installed_fleets(
    tmp_path, monkeypatch, coherent
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from moonmind.workflows.temporal import workers

    monkeypatch.setattr(workers, "_FLEET_SERVICE_NAMES", {"workflow": "workflow", "llm": "llm"})
    runner = SimpleNamespace(_run_compose_command=AsyncMock(side_effect=[
        {"exitCode": 0, "stdout": "workflow-id"},
        {"exitCode": 0, "stdout": "llm-id"},
        {"exitCode": 0, "stdout": "workflow-id"},
        {"exitCode": 0, "stdout": "llm-id"},
    ]))
    monkeypatch.setattr(release, "worker_readiness", AsyncMock(return_value={"ready": True, "buildId": "a"}))
    docker = AsyncMock(side_effect=[
        json.dumps([{"Image": "sha256:a"}]),
        json.dumps([{"Image": "sha256:a" if coherent else "sha256:b"}]),
        json.dumps([{"Image": "sha256:a"}]),
        json.dumps([{"Image": "sha256:a" if coherent else "sha256:b"}]),
    ])
    monkeypatch.setattr(release, "docker", docker)
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    if coherent:
        result = await cohort.record_serving_image("fleet.a", "fleet")
        assert result["image"] == "sha256:a"
        assert json.loads((tmp_path / "retained.json").read_text()) == result
        # Restart uses its receipt without looking for now-missing containers.
        assert await cohort.record_serving_image("fleet.a", "fleet") == result
    else:
        with pytest.raises(ValueError, match="different images"):
            await cohort.record_serving_image("fleet.a", "fleet")
        assert not (tmp_path / "retained.json").exists()
    assert not cohort.names
    assert all(call.args[0] == "inspect" for call in docker.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_source", ["rev-1", "rev-2", None])
async def test_terminal_receipt_source_revision_binds_image_authority(
    tmp_path, monkeypatch, receipt_source
):
    from unittest.mock import AsyncMock

    deployment = "release-test"
    digest = "sha256:" + "c" * 64
    image_id = "sha256:" + "d" * 64
    version = deployment + "." + digest
    directory = tmp_path / "job1"
    directory.mkdir()
    release.write_record(
        directory / "routing.json",
        {"deployment": deployment, "candidate": digest},
    )
    release.write_record(
        directory / "request.json",
        {
            "authored": {
                "owner": "owner",
                "inputs": {"sourceRevision": "rev-1"},
            },
            "image": "example/image@" + digest,
            "imageId": image_id,
        },
    )
    outputs = {
        "resolvedDigest": digest,
        "releaseReadinessArtifactRef": "artifact://readiness",
    }
    if receipt_source is not None:
        outputs["sourceRevision"] = receipt_source
    release.write_record(
        directory / "deployment-result.json",
        {"owner": "owner", "result": {"status": "COMPLETED", "outputs": outputs}},
    )
    docker = AsyncMock(
        side_effect=[
            image_id,
            json.dumps([{"Id": image_id}]),
            json.dumps({"digest": digest, "sourceRevision": "rev-1"}),
        ]
    )
    monkeypatch.setattr(release, "docker", docker)
    if receipt_source == "rev-2":
        # A mismatched source stamp denies authority from that receipt but is
        # skipped, never fatal to the scan.
        assert await release.successful_release_image(tmp_path, version) is None
        docker.assert_not_awaited()
        return
    # A matching stamp grants authority; a pre-stamp legacy receipt without
    # the key still recovers through the live manifest source binding.
    assert await release.successful_release_image(tmp_path, version) == {
        "image": image_id,
        "sourceReceipt": "job1",
        "sourceRevision": "rev-1",
    }
    assert docker.await_count == 3


@pytest.mark.asyncio
async def test_legacy_receipt_without_source_revision_cannot_block_promotion_scan(
    tmp_path, monkeypatch
):
    """A pre-sourceRevision receipt is skipped, never fatal to the scan.

    Regression: one legacy-format COMPLETED receipt (authored before
    sourceRevision existed) crashed successful_release_image with
    KeyError, blocking every promotion including outage recovery.
    """
    from unittest.mock import AsyncMock

    deployment = "legacy-install"
    digest = "sha256:" + "c" * 64
    version = deployment + "." + digest

    legacy = tmp_path / "legacy-job"
    legacy.mkdir()
    release.write_record(
        legacy / "routing.json",
        {"deployment": deployment, "candidate": digest, "previous": "other"},
    )
    release.write_record(
        legacy / "request.json",
        {
            "authored": {
                "owner": "legacy-owner",
                "inputs": {
                    "stack": "moonmind",
                    "image": {"repository": "example/moonmind", "reference": "latest"},
                },
            },
            "image": f"example/moonmind@{digest}",
            "imageId": "sha256:" + "d" * 64,
        },
    )
    release.write_record(
        legacy / "deployment-result.json",
        {
            "owner": "legacy-owner",
            "result": {"status": "COMPLETED", "outputs": {"resolvedDigest": digest}},
        },
    )

    valid = tmp_path / "valid-job"
    valid.mkdir()
    release.write_record(
        valid / "routing.json",
        {"deployment": deployment, "candidate": digest, "previous": "other"},
    )
    release.write_record(
        valid / "request.json",
        {
            "authored": {
                "owner": "valid-owner",
                "inputs": {
                    "stack": "moonmind",
                    "image": {"repository": "example/moonmind", "reference": "candidate"},
                    "sourceRevision": "rev",
                },
            },
            "image": f"example/moonmind@{digest}",
            "imageId": "sha256:" + "e" * 64,
        },
    )
    release.write_record(
        valid / "deployment-result.json",
        {
            "owner": "valid-owner",
            "result": {"status": "COMPLETED", "outputs": {"resolvedDigest": digest}},
        },
    )

    docker = AsyncMock(
        side_effect=[
            "sha256:" + "d" * 64,
            json.dumps([{"Id": "sha256:" + "d" * 64}]),
            json.dumps({"digest": digest, "sourceRevision": "rev"}),
            "sha256:" + "e" * 64,
            json.dumps([{"Id": "sha256:" + "e" * 64}]),
            json.dumps({"digest": digest, "sourceRevision": "rev"}),
        ]
    )
    monkeypatch.setattr(release, "docker", docker)
    # "legacy-job" sorts first: it must be skipped, and the valid receipt
    # for the same version must still be returned.
    assert await release.successful_release_image(tmp_path, version) == {
        "image": "sha256:" + "e" * 64,
        "sourceReceipt": "valid-job",
        "sourceRevision": "rev",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("log_failure", [None, TimeoutError("timed out")])
async def test_exhausted_deliveries_surface_updater_evidence_and_recovery_hint(
    tmp_path, monkeypatch, log_failure
):
    """Exhaustion must name the recovery owner and preserve inner evidence.

    Regression: WSL /mnt/<drive> checkouts launched updater containers with
    empty state volumes. The updater exited with FileNotFoundError three
    times without attempts.json/result.json, and the outer error hid that
    cause behind a generic message with no recovery path. A failing log
    collection must not replace the established exhaustion either.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    owner = "host-update:dc9f2a60-0e8e-464e-bc97-6657fb608d76"
    digest = "sha256:" + "a" * 64
    inputs = {
        "stack": "moonmind",
        "image": {"repository": "example/moonmind", "reference": "candidate"},
    }
    context = {"idempotency_key": owner}

    class Runner:
        async def pull(self, **kwargs):
            return {"exitCode": 0}

        async def inspect_image(self, requested):
            return {"Id": "image-id", "RepoDigests": [f"example/moonmind@{digest}"]}

        async def _run_compose_command(self, command, **kwargs):
            return {"exitCode": 0, "stdout": json.dumps({"services": {}})}

    async def inspect_owned(name, owner):
        assert name.startswith("moonmind-release-update-")
        return {"Image": "image-id", "State": {"Running": False, "ExitCode": 1}}

    async def docker(*args):
        assert args[0] in {"update", "start"}
        return ""

    async def logs_tail(name, tail_lines=30, timeout_seconds=60):
        assert name.startswith("moonmind-release-update-")
        if log_failure is not None:
            raise log_failure
        return "Traceback (most recent call last):\nFileNotFoundError"

    async def coherent(*args):
        return {}

    monkeypatch.setattr(release, "inspect_owned", inspect_owned)
    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "docker_logs_tail", logs_tail)
    monkeypatch.setattr(release, "require_coherent_images", coherent)

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(release.asyncio, "sleep", no_sleep)
    executor = SimpleNamespace(runner=Runner())
    with pytest.raises(RuntimeError, match="exhausted three deliveries") as exc_info:
        await release.execute_detached(executor, inputs, context)
    message = str(exc_info.value)
    assert "release job" in message
    assert "do not --resume this submission" in message
    assert "--resume dc9f2a60" not in message
    assert "attempts=none" in message
    assert "updater-exit=1" in message
    if log_failure is None:
        assert "FileNotFoundError" in message
    else:
        assert "updater-logs=unavailable" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("bounded", [False, True])
async def test_docker_failure_preserves_redacted_multiline_diagnostic(
    monkeypatch, bounded
):
    reason = "Error response from daemon: port is already allocated"
    diagnostic = f"{reason}\npassword=example-secret\n"
    expected = f"{reason}\npassword=[REDACTED]\n"
    if bounded:
        padding = "x" * (990 - len(diagnostic)) + " "
        diagnostic += padding + "ghp_" + "A" * 36 + "\n" + "y" * 1200 + "\n"
        expected += padding + "[REDACTED]\n" + "y" * 1200 + "\n"
    summary = "Error: failed to start containers: release-test"
    diagnostic += summary
    expected += summary

    class FakeProcess:
        returncode = 1

        async def communicate(self, input_bytes):
            return b"", diagnostic.encode()

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(release.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError) as exc_info:
        await release.docker("start", "release-test")
    message = str(exc_info.value)
    assert reason in message
    assert "example-secret" not in message
    assert "ghp_" not in message
    assert message == f"Docker start failed: {expected[:1000]}"
    assert len(message) <= len("Docker start failed: ") + 1000


@pytest.mark.asyncio
async def test_docker_logs_tail_merges_stdout_and_stderr(tmp_path, monkeypatch):
    """Tracebacks reach the logs command over stderr; stdout alone hides them."""

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return (b"container stdout line\n", b"Traceback: FileNotFoundError\n")

        def kill(self):  # pragma: no cover - success path never kills
            raise AssertionError("kill must not run on success")

        async def wait(self):
            return self.returncode

    async def fake_exec(*args, **kwargs):
        assert args[:3] == ("docker", "logs", "--tail")
        assert kwargs.get("stdout") is release.asyncio.subprocess.PIPE
        assert kwargs.get("stderr") is release.asyncio.subprocess.PIPE
        return FakeProcess()

    monkeypatch.setattr(release.asyncio, "create_subprocess_exec", fake_exec)
    merged = await release.docker_logs_tail("moonmind-release-update-test")
    assert "container stdout line" in merged
    assert "Traceback: FileNotFoundError" in merged


@pytest.mark.asyncio
@pytest.mark.parametrize("repaired", [True, False, "starting", "absent"])
async def test_unhealthy_gateway_is_repaired_before_previous_pollers_are_required(
    tmp_path, monkeypatch, repaired
):
    """A broken egress gateway must not make a deployment un-updatable.

    Regression: workers attest the singular restricted-egress gateway before
    they report ready, so an out-of-band change that left it unhealthy stopped
    the previous release from retaining pollers. Every later release then
    failed in ``preserve_previous`` — including the one that installs the
    gateway's replacement. The cohort repairs it from the definition its own
    image owns, and reports the fleet and gateway when it still cannot.
    """
    from unittest.mock import AsyncMock

    from moonmind.security.egress import EGRESS_GATEWAY_REF, EGRESS_GATEWAY_SERVICE
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner
    from moonmind.workflows.temporal import workers

    monkeypatch.setattr(
        workers,
        "_FLEET_SERVICE_NAMES",
        {"agent_runtime": "temporal-worker-agent-runtime"},
    )
    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    digest = "sha256:" + "a" * 64
    (tmp_path / "retained.json").write_text(
        json.dumps(
            {
                "owner": "owner",
                "version": f"fleet.{digest}",
                "image": "sha256:previous",
                "retired": [],
            }
        )
    )
    health = {
        "status": None
        if repaired == "absent"
        else ("starting" if repaired == "starting" else "unhealthy")
    }
    polls = {"count": 0}
    embedded_compose = "services:\n  sandbox-egress-proxy:\n    image: ubuntu/squid\n"

    async def docker(*args, **kwargs):
        if args[0] == "run":
            assert args[-2] == "sha256:previous"
            assert args[-1] == "/app/release/docker-compose.yaml"
            return embedded_compose
        assert args[0] == "inspect" and args[1] == EGRESS_GATEWAY_REF
        polls["count"] += 1
        if repaired == "starting" and polls["count"] > 2:
            # A gateway that converges on its own is never recreated.
            health["status"] = "healthy"
        if health["status"] is None:
            raise RuntimeError("Docker inspect failed: No such object")
        return json.dumps([{"State": {"Health": {"Status": health["status"]}}}])

    compose_calls = []

    async def fake_compose(self, command, **kwargs):
        compose_calls.append((command, kwargs))
        assert command[:4] == ("docker", "compose", "up", "-d")
        assert command[-1] == EGRESS_GATEWAY_SERVICE
        assert "--force-recreate" in command
        assert kwargs["requested_image"] == "sha256:previous"
        if repaired:
            health["status"] = "healthy"
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", fake_compose)

    async def readiness(name):
        if repaired == "absent":
            # A deployment that runs no gateway has nothing to attest here.
            return {"ready": True, "buildId": digest}
        if health["status"] != "healthy":
            raise RuntimeError("restricted-egress gateway is not healthy")
        return {"ready": True, "buildId": digest}

    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))
    monkeypatch.setattr(release, "worker_readiness", readiness)
    monkeypatch.setattr(
        release,
        "inspect_owned",
        AsyncMock(return_value={"Image": "sha256:previous", "State": {"Running": True}}),
    )
    monkeypatch.setattr(
        release,
        "docker_logs_tail",
        AsyncMock(
            return_value="RuntimeError: restricted-egress gateway is not healthy\n"
            + "x" * 50
            + "password=supersecret-value"
            + "y" * 1489
        ),
    )
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file="/deployment/docker-compose.yaml",
        project_name="moonmind",
    )
    captured = {}
    original_restore = release.ReleaseCohort.restore_gateway

    async def spy_restore(self, gateway_runner, image, **kwargs):
        captured["compose_file"] = gateway_runner.compose_file
        captured["project_name"] = gateway_runner.project_name
        captured["project_dir"] = gateway_runner.project_dir
        captured["image"] = image
        return await original_restore(self, gateway_runner, image, **kwargs)

    monkeypatch.setattr(release.ReleaseCohort, "restore_gateway", spy_restore)
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    if repaired:
        # ``starting`` converges without a repair; ``True`` converges with one.
        await cohort.preserve_previous(f"fleet.{digest}", "fleet", "sha256:candidate")
    else:
        with pytest.raises(RuntimeError, match="retain compatible pollers") as failure:
            await cohort.preserve_previous(
                f"fleet.{digest}", "fleet", "sha256:candidate"
            )
        message = str(failure.value)
        assert "fleet=agent_runtime" in message
        assert "gateway=unhealthy" in message
        # Redaction runs over the whole text before the bound, so a tail that
        # would split the marker from its value cannot publish the value.
        assert "supersecret-value" not in message
    # The repair renders the definition the retained image owns — never the
    # deployment checkout's newer gateway definition — while the
    # deployment-owned runner identity accompanies the command.
    expected_compose = (
        tmp_path
        / f"retained-compose-{hashlib.sha256(b'sha256:previous').hexdigest()[:16]}.yaml"
    )
    if repaired == "absent":
        # The retained definition is still rendered for the pollers, but no
        # gateway is recreated or waited on when the deployment runs none.
        assert not compose_calls
        return
    assert captured["compose_file"] == str(expected_compose)
    assert captured["project_name"] == "moonmind"
    assert captured["project_dir"] == str(tmp_path)
    assert captured["image"] == "sha256:previous"
    assert expected_compose.read_text() == embedded_compose
    # A repair that converges is never repeated, one that is still starting on
    # its own is never attempted, and one that does not take is retried on the
    # cooldown - never per readiness poll.
    if repaired == "starting":
        assert not compose_calls
    elif repaired:
        assert len(compose_calls) == 1
    else:
        assert len(compose_calls) == -(-60 // release._GATEWAY_REPAIR_COOLDOWN_POLLS)


@pytest.mark.asyncio
async def test_preserve_previous_repairs_gateway_before_serving_image_readiness_proof(
    tmp_path, monkeypatch
):
    """The serving-image proof must not precede gateway recovery.

    Regression (Codex P1): on an initial plain-Compose installation with no
    release receipt and no availability-owned ``retained.json``,
    ``record_serving_image`` probed every installed worker's ``/readyz``
    before the new recovery path ran. Worker readiness attests the gateway,
    so a broken gateway raised ``no coherent live worker owner`` and the
    repair was never reached. The image must be discovered and validated
    without gateway-dependent readiness, the gateway repaired, and only then
    may coherent worker readiness be required.
    """
    from unittest.mock import AsyncMock

    from moonmind.security.egress import EGRESS_GATEWAY_REF, EGRESS_GATEWAY_SERVICE
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner
    from moonmind.workflows.temporal import workers

    monkeypatch.setattr(
        workers,
        "_FLEET_SERVICE_NAMES",
        {"agent_runtime": "temporal-worker-agent-runtime"},
    )
    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        release, "successful_release_image", AsyncMock(return_value=None)
    )
    digest = "sha256:" + "a" * 64
    health = {"status": "unhealthy"}
    embedded_compose = "services:\n  sandbox-egress-proxy:\n    image: ubuntu/squid\n"

    async def docker(*args, **kwargs):
        if args[0] == "run":
            assert args[-2] == "sha256:previous"
            assert args[-1] == "/app/release/docker-compose.yaml"
            return embedded_compose
        assert args[0] == "inspect"
        if args[1] == EGRESS_GATEWAY_REF:
            return json.dumps([{"State": {"Health": {"Status": health["status"]}}}])
        assert args[1] == "installed-id"
        return json.dumps([{"Image": "sha256:previous"}])

    compose_calls = []

    async def fake_compose(self, command, **kwargs):
        compose_calls.append((command, kwargs))
        if tuple(command[:3]) == ("docker", "compose", "ps"):
            return {"exitCode": 0, "stdout": "installed-id"}
        assert command[:4] == ("docker", "compose", "up", "-d")
        assert command[-1] == EGRESS_GATEWAY_SERVICE
        assert kwargs["requested_image"] == "sha256:previous"
        health["status"] = "healthy"
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", fake_compose)

    async def readiness(name):
        if health["status"] != "healthy":
            raise RuntimeError("restricted-egress gateway is not healthy")
        return {"ready": True, "buildId": digest}

    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))
    monkeypatch.setattr(release, "worker_readiness", readiness)
    monkeypatch.setattr(
        release,
        "inspect_owned",
        AsyncMock(return_value={"Image": "sha256:previous", "State": {"Running": True}}),
    )
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file="/deployment/docker-compose.yaml",
        project_name="moonmind",
    )
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    await cohort.preserve_previous(f"fleet.{digest}", "fleet", "sha256:candidate")

    retained = json.loads((tmp_path / "retained.json").read_text())
    assert retained["image"] == "sha256:previous"
    assert retained["version"] == f"fleet.{digest}"
    # Discovery (ps) precedes the gateway repair (up), and the repair runs once.
    kinds = [
        "ps" if tuple(command[:3]) == ("docker", "compose", "ps") else command[2]
        for command, _ in compose_calls
    ]
    assert kinds[0] == "ps"
    assert kinds.count("up") == 1
    assert kinds.index("up") > kinds.index("ps")


@pytest.mark.asyncio
async def test_retained_worker_diagnostic_redacts_before_truncating(
    tmp_path, monkeypatch
):
    """A long retained log must not leak a credential cut off by truncation.

    Regression (Codex P2): slicing the 1,500-character tail before redacting
    removes the ``TOKEN=`` context ``redact_sensitive_text`` needs while
    leaving the complete credential in the failure message. Redact the full
    text first and only then take the bounded tail.
    """
    from unittest.mock import AsyncMock

    from moonmind.security.egress import EGRESS_GATEWAY_REF
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner
    from moonmind.workflows.temporal import workers

    monkeypatch.setattr(
        workers,
        "_FLEET_SERVICE_NAMES",
        {"agent_runtime": "temporal-worker-agent-runtime"},
    )
    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    digest = "sha256:" + "a" * 64
    (tmp_path / "retained.json").write_text(
        json.dumps(
            {
                "owner": "owner",
                "version": f"fleet.{digest}",
                "image": "sha256:previous",
                "retired": [],
            }
        )
    )
    embedded_compose = "services:\n  sandbox-egress-proxy:\n    image: ubuntu/squid\n"

    async def docker(*args, **kwargs):
        if args[0] == "run":
            return embedded_compose
        assert args[0] == "inspect" and args[1] == EGRESS_GATEWAY_REF
        return json.dumps([{"State": {"Health": {"Status": "unhealthy"}}}])

    async def fake_compose(self, command, **kwargs):
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", fake_compose)
    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))

    async def readiness(name):
        raise RuntimeError("restricted-egress gateway is not healthy")

    monkeypatch.setattr(release, "worker_readiness", readiness)
    monkeypatch.setattr(
        release,
        "inspect_owned",
        AsyncMock(return_value={"Image": "sha256:previous", "State": {"Running": True}}),
    )
    secret = "S" * 3000
    monkeypatch.setattr(
        release,
        "docker_logs_tail",
        AsyncMock(return_value="event happened\n" * 200 + "MY_TOKEN=" + secret),
    )
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file="/deployment/docker-compose.yaml",
        project_name="moonmind",
    )
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    with pytest.raises(RuntimeError, match="retain compatible pollers") as failure:
        await cohort.preserve_previous(f"fleet.{digest}", "fleet", "sha256:candidate")
    message = str(failure.value)
    assert "gateway=unhealthy" in message
    assert "[REDACTED]" in message
    assert "S" * 20 not in message


@pytest.mark.asyncio
async def test_gateway_repair_is_retried_inside_one_release_attempt(
    tmp_path, monkeypatch
):
    """A recreate that does not take must be retried, not waited out.

    Regression: ``restore_gateway`` recreated the gateway exactly once and
    then polled out its window. A gateway that needed a second recreate left
    retention to fail, failing the whole release attempt; the deployment only
    converged because the job retried the entire update. Two doomed attempts
    cost nine minutes of wall clock for a repair that belongs inside one.
    """
    from unittest.mock import AsyncMock

    from moonmind.security.egress import EGRESS_GATEWAY_REF, EGRESS_GATEWAY_SERVICE
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner

    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    health = {"status": "unhealthy"}
    recreates = {"count": 0}

    async def docker(*args, **kwargs):
        assert args[0] == "inspect" and args[1] == EGRESS_GATEWAY_REF
        return json.dumps([{"State": {"Health": {"Status": health["status"]}}}])

    async def fake_compose(self, command, **kwargs):
        assert command[:4] == ("docker", "compose", "up", "-d")
        assert command[-1] == EGRESS_GATEWAY_SERVICE
        recreates["count"] += 1
        # The first recreate does not take - the observed failure mode.
        if recreates["count"] >= 2:
            health["status"] = "healthy"
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))
    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", fake_compose)
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file=str(tmp_path / "retained.yaml"),
        project_name="moonmind",
    )
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")

    observed = await cohort.restore_gateway(runner, "sha256:previous")

    # The repair converges within its own window instead of handing an
    # unhealthy gateway to a readiness loop that cannot succeed.
    assert observed == "healthy"
    assert recreates["count"] == 2


@pytest.mark.asyncio
async def test_durable_deadline_is_established_before_the_updater_pull(
    tmp_path, monkeypatch
):
    """Pre-launch work consumes the budget instead of preceding it.

    Regression (Codex P1 on #4422): the job deadline was computed only after
    the updater image was pulled and inspected. The runner allows that pull
    900 seconds, so a slow first pull shifted the detached deadline past the
    supervising Activity's schedule-to-close, and a near-budget release could
    still be running after its workflow had already timed out.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    started = 1_000_000.0
    now = {"value": started}
    monkeypatch.setattr(release.time, "time", lambda: now["value"])
    owner = "workflow:deployment:step:1"
    digest = "sha256:" + "a" * 64
    inputs = {
        "stack": "moonmind",
        "image": {"repository": "example/moonmind", "reference": "candidate"},
    }
    container = None

    class Runner:
        async def pull(self, **kwargs):
            # The observed failure mode: a cold pull that burns the runner's
            # whole command budget before the deadline would have existed.
            now["value"] += 900
            return {"exitCode": 0}

        async def inspect_image(self, requested):
            return {"Id": "image-id", "RepoDigests": [f"example/moonmind@{digest}"]}

        async def _run_compose_command(self, command, **kwargs):
            nonlocal container
            request = release.Path(command[-1])
            release.write_record(
                request.parent / "result.json",
                {
                    "owner": owner,
                    "result": {
                        "status": "COMPLETED",
                        "outputs": {"verified": True},
                        "progress": {},
                    },
                },
            )
            container = {"Image": "image-id", "State": {"Running": False}}
            return {"exitCode": 0, "stdout": "", "stderr": ""}

    async def inspect(*args):
        return container

    async def docker(*args):
        nonlocal container
        if args[0] == "rm":
            container = None
        return ""

    async def coherent(*args):
        return {}

    monkeypatch.setattr(release, "inspect_owned", inspect)
    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "require_coherent_images", coherent)

    await release.execute_detached(
        SimpleNamespace(runner=Runner()),
        inputs,
        {"idempotency_key": owner, "principal": "system:deployment"},
    )

    key = hashlib.sha256(owner.encode()).hexdigest()[:32]
    record = json.loads((release.state_root() / key / "request.json").read_text())
    # The deadline is anchored where the Activity began, not after the pull,
    # so the supervisor's schedule still covers the job's own deadline.
    assert record["deadline"] == started + release.RELEASE_JOB_BUDGET_SECONDS


@pytest.mark.asyncio
async def test_durable_deadline_is_anchored_to_the_activity_schedule(
    tmp_path, monkeypatch
):
    """Queue delay must not push the job past its supervisor.

    Regression (Codex P1 round 2 on #4422): the deadline was anchored where
    ``execute_detached`` began running, but Temporal's schedule-to-close clock
    starts when the Activity is scheduled. The deployment fleet runs one
    Activity at a time, so a second release can wait in the queue longer than
    the schedule's margin; its budget then ran past the supervisor again.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    scheduled = 1_000_000.0
    # The Activity sat behind another deployment for well over the schedule's
    # margin before this attempt got to run.
    monkeypatch.setattr(release.time, "time", lambda: scheduled + 5_000)
    monkeypatch.setattr(release, "_activity_schedule_anchor", lambda: scheduled)
    owner = "workflow:deployment:step:1"
    digest = "sha256:" + "a" * 64
    container = None

    class Runner:
        async def pull(self, **kwargs):
            return {"exitCode": 0}

        async def inspect_image(self, requested):
            return {"Id": "image-id", "RepoDigests": [f"example/moonmind@{digest}"]}

        async def _run_compose_command(self, command, **kwargs):
            nonlocal container
            request = release.Path(command[-1])
            release.write_record(
                request.parent / "result.json",
                {
                    "owner": owner,
                    "result": {"status": "COMPLETED", "outputs": {}, "progress": {}},
                },
            )
            container = {"Image": "image-id", "State": {"Running": False}}
            return {"exitCode": 0, "stdout": "", "stderr": ""}

    async def inspect(*args):
        return container

    async def docker(*args):
        nonlocal container
        if args[0] == "rm":
            container = None
        return ""

    async def coherent(*args):
        return {}

    monkeypatch.setattr(release, "inspect_owned", inspect)
    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "require_coherent_images", coherent)

    await release.execute_detached(
        SimpleNamespace(runner=Runner()),
        {
            "stack": "moonmind",
            "image": {"repository": "example/moonmind", "reference": "candidate"},
        },
        {"idempotency_key": owner, "principal": "system:deployment"},
    )

    key = hashlib.sha256(owner.encode()).hexdigest()[:32]
    record = json.loads((release.state_root() / key / "request.json").read_text())
    # Both clocks start at the same instant, so the schedule always covers it.
    assert record["deadline"] == scheduled + release.RELEASE_JOB_BUDGET_SECONDS


@pytest.mark.asyncio
async def test_recorded_release_failure_keeps_the_line_that_names_it(
    tmp_path, monkeypatch
):
    """A bounded diagnosis must not discard the exception it ends with.

    ``preserve_previous`` reports the retained worker's log tail, and a Python
    traceback names its cause on its last line. Keeping only the head of a long
    failure published frame stacks and dropped ``RuntimeError:
    restricted-egress gateway is not healthy``, so every operator surface fed
    from this record - the Temporal failure, the run summary, the memo and
    ``last-error.json`` - reported a release that could not retain pollers
    without saying why.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    directory = release.state_root() / "job"
    directory.mkdir(parents=True)
    request_file = directory / "request.json"
    release.write_record(
        request_file,
        {"authored": {"owner": "owner"}, "deadline": release.time.time() + 300},
    )
    cause = "RuntimeError: restricted-egress gateway is not healthy"
    diagnosis = (
        "Previous release could not retain compatible pollers"
        " (fleet=agent_runtime; gateway=unhealthy; retained-logs="
        + "  File \"/app/moonmind/workflows/temporal/worker_runtime.py\"\n" * 40
        + cause
        + ")"
    )

    async def body(path):
        raise RuntimeError(diagnosis)

    async def no_wait(*args):
        pass

    monkeypatch.setattr(release, "_run_job_body", body)
    monkeypatch.setattr(release.asyncio, "sleep", no_wait)
    await release.run_job(request_file)

    recorded = json.loads((directory / "last-error.json").read_text())["error"]
    assert "fleet=agent_runtime" in recorded
    assert "gateway=unhealthy" in recorded
    assert cause in recorded
    assert len(recorded) <= 1000
    assert json.loads((directory / "result.json").read_text())["error"] == recorded


@pytest.mark.asyncio
async def test_candidate_gateway_upgrade_serves_new_definition_before_qualify(
    tmp_path, monkeypatch
):
    """An egress-policy change must not deadlock canary qualification.

    Regression: retention repairs the gateway from the previous definition
    only, so candidates carrying new egress files failed closed against the
    old gateway (``restricted-egress live config cannot be observed``) while
    retained workers would fail against a prematurely upgraded one. The
    cohort now serves the candidate definition after retention converges -
    running workers attest only at startup - before canary workers prove it.
    The probe runs through the same Compose service candidates launch with,
    so deployment-owned environment and policy mounts apply identically.
    """
    from unittest.mock import AsyncMock

    from moonmind.security import egress as egress_module
    from moonmind.security.egress import EGRESS_GATEWAY_REF, EGRESS_GATEWAY_SERVICE
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner
    from moonmind.workflows.temporal.workers import (
        AGENT_RUNTIME_FLEET,
        _FLEET_SERVICE_NAMES,
    )

    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    new_files = {
        "squid.conf": "sha256:" + "a" * 64,
        "omnigent-provider-domains.txt": "sha256:" + "b" * 64,
        "package-registry-domains.txt": "sha256:" + "c" * 64,
    }
    probe_definition = {
        "liveDirectory": "/run/moonmind-egress",
        "files": new_files,
        "enforcer": egress_module.ENFORCER_IMPLEMENTATION,
        "networks": sorted(egress_module._EXPECTED_GATEWAY_NETWORKS),
    }
    state = {"gateway": "old"}

    def live_lines(files):
        return "".join(
            f"{digest.removeprefix('sha256:')}  {directory}/{name}\n"
            for directory in ("/etc/squid", "/run/moonmind-egress")
            for name, digest in sorted(files.items())
        )

    def inspect_body():
        return json.dumps(
            [
                {
                    "Config": {
                        "Labels": {
                            "moonmind.egress.enforcer": (
                                egress_module.ENFORCER_IMPLEMENTATION
                            )
                        }
                    },
                    "NetworkSettings": {
                        "Networks": {
                            name: {} for name in probe_definition["networks"]
                        }
                    },
                    "State": {"Health": {"Status": "healthy"}},
                }
            ]
        )

    async def docker(*args, **kwargs):
        assert args[0] in {"inspect", "exec"} and args[1] == EGRESS_GATEWAY_REF
        if args[0] == "inspect":
            return inspect_body()
        assert args[2] == "sha256sum"
        if state["gateway"] == "old":
            raise RuntimeError("sha256sum: package-registry-domains.txt: No such file")
        return live_lines(new_files)

    compose_calls = []

    async def fake_compose(self, command, **kwargs):
        compose_calls.append((command, kwargs))
        if command[2] == "run":
            # The probe inherits the candidate workers' Compose environment
            # and mounts rather than evaluating bare image defaults.
            assert command[3:6] == ("--rm", "--no-deps", "-T")
            assert command[6:8] == ("--entrypoint", "python")
            assert command[8] == _FLEET_SERVICE_NAMES[AGENT_RUNTIME_FLEET]
            assert kwargs["requested_image"] == "sha256:candidate"
            return {
                "exitCode": 0,
                "stdout": json.dumps(probe_definition),
                "stderr": "",
            }
        assert command[:4] == ("docker", "compose", "up", "-d")
        assert command[-1] == EGRESS_GATEWAY_SERVICE
        assert "--force-recreate" in command
        assert kwargs["requested_image"] == "sha256:candidate"
        state["gateway"] = "new"
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", fake_compose)
    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file="/deployment/docker-compose.yaml",
        project_name="moonmind",
    )
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    assert await cohort.align_gateway_to_candidate("sha256:candidate") == "upgraded"
    assert [call[0][2] for call in compose_calls].count("up") == 1


@pytest.mark.asyncio
async def test_candidate_gateway_alignment_skips_recreate_when_already_current(
    tmp_path, monkeypatch
):
    """A current gateway must not be bounced before every qualification."""
    from unittest.mock import AsyncMock

    from moonmind.security import egress as egress_module
    from moonmind.security.egress import EGRESS_GATEWAY_REF
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner
    from moonmind.workflows.temporal.workers import (
        AGENT_RUNTIME_FLEET,
        _FLEET_SERVICE_NAMES,
    )

    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    files = {
        "squid.conf": "sha256:" + "a" * 64,
        "omnigent-provider-domains.txt": "sha256:" + "b" * 64,
    }
    probe_definition = {
        "liveDirectory": "/run/moonmind-egress",
        "files": files,
        "enforcer": egress_module.ENFORCER_IMPLEMENTATION,
        "networks": sorted(egress_module._EXPECTED_GATEWAY_NETWORKS),
    }
    body = "".join(
        f"{digest.removeprefix('sha256:')}  {directory}/{name}\n"
        for directory in ("/etc/squid", "/run/moonmind-egress")
        for name, digest in sorted(files.items())
    )

    async def docker(*args, **kwargs):
        if args[0] == "inspect":
            return json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "moonmind.egress.enforcer": (
                                    egress_module.ENFORCER_IMPLEMENTATION
                                )
                            }
                        },
                        "NetworkSettings": {
                            "Networks": {
                                name: {}
                                for name in probe_definition["networks"]
                            }
                        },
                        "State": {"Health": {"Status": "healthy"}},
                    }
                ]
            )
        assert args[0] == "exec"
        return body

    async def fake_compose(self, command, **kwargs):
        if command[2] == "run":
            assert command[8] == _FLEET_SERVICE_NAMES[AGENT_RUNTIME_FLEET]
            return {
                "exitCode": 0,
                "stdout": json.dumps(probe_definition),
                "stderr": "",
            }
        raise AssertionError("gateway must not be recreated when already current")

    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", fake_compose)
    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file="/deployment/docker-compose.yaml",
        project_name="moonmind",
    )
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    assert await cohort.align_gateway_to_candidate("sha256:candidate") == "aligned"


@pytest.mark.asyncio
async def test_candidate_gateway_alignment_requires_health_before_noop(
    tmp_path, monkeypatch
):
    """Matching files on an unhealthy gateway must not report alignment.

    Otherwise candidates fail their mandatory gateway-health attestation
    until the qualification timeout in a state the update could have
    repaired.
    """
    from unittest.mock import AsyncMock

    from moonmind.security import egress as egress_module
    from moonmind.security.egress import EGRESS_GATEWAY_REF
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner

    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    files = {"squid.conf": "sha256:" + "a" * 64}
    probe_definition = {
        "liveDirectory": "/run/moonmind-egress",
        "files": files,
        "enforcer": egress_module.ENFORCER_IMPLEMENTATION,
        "networks": sorted(egress_module._EXPECTED_GATEWAY_NETWORKS),
    }
    body = "".join(
        f"{digest.removeprefix('sha256:')}  {directory}/{name}\n"
        for directory in ("/etc/squid", "/run/moonmind-egress")
        for name, digest in sorted(files.items())
    )
    health = {"status": "starting"}
    polls = {"count": 0}

    async def docker(*args, **kwargs):
        if args[0] == "inspect":
            polls["count"] += 1
            if polls["count"] > 2:
                health["status"] = "healthy"
            return json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "moonmind.egress.enforcer": (
                                    egress_module.ENFORCER_IMPLEMENTATION
                                )
                            }
                        },
                        "NetworkSettings": {
                            "Networks": {
                                name: {}
                                for name in probe_definition["networks"]
                            }
                        },
                        "State": {"Health": {"Status": health["status"]}},
                    }
                ]
            )
        return body

    async def no_recreate(self, command, **kwargs):
        if command[2] == "run":
            return {
                "exitCode": 0,
                "stdout": json.dumps(probe_definition),
                "stderr": "",
            }
        raise AssertionError("a converging gateway must not be bounced")

    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", no_recreate)
    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file="/deployment/docker-compose.yaml",
        project_name="moonmind",
    )
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    assert await cohort.align_gateway_to_candidate("sha256:candidate") == "aligned"


@pytest.mark.asyncio
async def test_candidate_gateway_alignment_rejects_stale_contract_fields(
    tmp_path, monkeypatch
):
    """File equality alone must not declare an old gateway aligned.

    A candidate that changes only the enforcer, labels, or network
    attachments would otherwise qualify against stale enforcement and
    promote before the new gateway is ever exercised.
    """
    from unittest.mock import AsyncMock

    from moonmind.security import egress as egress_module
    from moonmind.security.egress import EGRESS_GATEWAY_REF, EGRESS_GATEWAY_SERVICE
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner

    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    files = {"squid.conf": "sha256:" + "a" * 64}
    probe_definition = {
        "liveDirectory": "/run/moonmind-egress",
        "files": files,
        "enforcer": egress_module.ENFORCER_IMPLEMENTATION,
        "networks": sorted(egress_module._EXPECTED_GATEWAY_NETWORKS),
    }
    body = "".join(
        f"{digest.removeprefix('sha256:')}  {directory}/{name}\n"
        for directory in ("/etc/squid", "/run/moonmind-egress")
        for name, digest in sorted(files.items())
    )
    state = {"upgraded": False}

    async def docker(*args, **kwargs):
        if args[0] == "inspect":
            networks = (
                probe_definition["networks"]
                if state["upgraded"]
                else probe_definition["networks"][:-1]
            )
            return json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "moonmind.egress.enforcer": (
                                    egress_module.ENFORCER_IMPLEMENTATION
                                )
                            }
                        },
                        "NetworkSettings": {
                            "Networks": {name: {} for name in networks}
                        },
                        "State": {"Health": {"Status": "healthy"}},
                    }
                ]
            )
        return body

    async def fake_compose(self, command, **kwargs):
        if command[2] == "run":
            return {
                "exitCode": 0,
                "stdout": json.dumps(probe_definition),
                "stderr": "",
            }
        assert command[-1] == EGRESS_GATEWAY_SERVICE
        state["upgraded"] = True
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", fake_compose)
    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file="/deployment/docker-compose.yaml",
        project_name="moonmind",
    )
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    assert await cohort.align_gateway_to_candidate("sha256:candidate") == "upgraded"


@pytest.mark.asyncio
async def test_restore_gateway_force_recreates_a_healthy_gateway(
    tmp_path, monkeypatch
):
    """Qualification rollback restores the retained definition on demand."""
    from unittest.mock import AsyncMock

    from moonmind.security.egress import EGRESS_GATEWAY_SERVICE
    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner

    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())

    async def docker(*args, **kwargs):
        return json.dumps([{"State": {"Health": {"Status": "healthy"}}}])

    calls = []

    async def fake_compose(self, command, **kwargs):
        calls.append((command, kwargs))
        return {"exitCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_command", fake_compose)
    monkeypatch.setattr(release, "docker", AsyncMock(side_effect=docker))
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file="/deployment/docker-compose.yaml",
        project_name="moonmind",
    )
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    assert await cohort.restore_gateway(runner, "sha256:previous") == "healthy"
    assert calls == []
    assert (
        await cohort.restore_gateway(runner, "sha256:previous", force=True)
        == "healthy"
    )
    assert len(calls) == 1
    assert calls[0][0][-1] == EGRESS_GATEWAY_SERVICE
    assert calls[0][1]["requested_image"] == "sha256:previous"


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway_state", ["upgraded", "aligned"])
async def test_qualify_restores_previous_gateway_only_after_upgrade(
    tmp_path, monkeypatch, gateway_state
):
    """A failed candidate must not strand an incompatible gateway.

    When the candidate gateway upgrade applied and later checks fail, the
    still-routed previous workers keep the egress they attested only if
    the retained definition is restored before the error surfaces.
    """
    from unittest.mock import AsyncMock

    import moonmind.release_identity as release_identity
    import moonmind.workflows.temporal.client as temporal_client
    import moonmind.workflows.temporal.release_routing as release_routing
    from moonmind.workflows.temporal import workers

    monkeypatch.setattr(release.asyncio, "sleep", AsyncMock())
    digest = "sha256:" + "c" * 64
    previous = f"moonmind-workflow-fleet.{digest}"
    retained_runner = object()

    async def fake_preserve(self, *args):
        return retained_runner, "sha256:previous"

    restore = AsyncMock()
    monkeypatch.setattr(release.ReleaseCohort, "preserve_previous", fake_preserve)
    monkeypatch.setattr(
        release.ReleaseCohort,
        "align_gateway_to_candidate",
        AsyncMock(return_value=gateway_state),
    )
    monkeypatch.setattr(release.ReleaseCohort, "restore_gateway", restore)
    monkeypatch.setattr(
        release.ReleaseCohort, "qualify_api", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        release.ReleaseCohort,
        "qualify_provider_managers",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(release, "require_coherent_images", AsyncMock())
    monkeypatch.setattr(
        release, "inspect_owned",
        AsyncMock(return_value={"Image": "sha256:candidate", "State": {"Running": True}}),
    )
    monkeypatch.setattr(
        release,
        "worker_readiness",
        AsyncMock(return_value={"ready": True, "buildId": digest}),
    )
    monkeypatch.setattr(
        release_identity, "installed_release", lambda: {"digest": digest}
    )
    monkeypatch.setattr(
        temporal_client, "get_temporal_client", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        release_routing, "routing_snapshot", AsyncMock(return_value={"ok": True})
    )
    monkeypatch.setattr(release_routing, "current_version", lambda snapshot: previous)
    monkeypatch.setattr(
        release_routing,
        "promote_version",
        AsyncMock(side_effect=RuntimeError("canary failed")),
    )
    monkeypatch.setattr(
        workers,
        "_FLEET_SERVICE_NAMES",
        {"agent_runtime": "temporal-worker-agent-runtime"},
    )
    monkeypatch.setattr(
        workers,
        "build_all_worker_topologies",
        lambda: [SimpleNamespace(fleet="agent_runtime", task_queues=("q1",))],
    )
    runner = SimpleNamespace(_run_compose_command=AsyncMock())
    cohort = release.ReleaseCohort(runner, tmp_path, "owner")
    with pytest.raises(RuntimeError, match="canary failed"):
        await cohort.qualify("sha256:candidate")
    if gateway_state == "upgraded":
        restore.assert_awaited_once_with(
            retained_runner, "sha256:previous", force=True
        )
    else:
        restore.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_attempts_do_not_erase_the_error_that_started_the_failure(
    tmp_path, monkeypatch
):
    """The first attempt's cause must survive the attempts that follow it.

    Regression: attempt one did the real work - recording routing and
    retaining the previous cohort - and failed for its own reason. Attempts
    two and three lost a race for the stack lock within seconds and rewrote
    ``last-error.json``, so the Temporal failure, the run summary and the
    operator's incident reconstruction all reported ``DEPLOYMENT_LOCKED``
    while the cause was unrecoverable.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    directory = release.state_root() / "job"
    directory.mkdir(parents=True)
    request_file = directory / "request.json"
    release.write_record(
        request_file,
        {"authored": {"owner": "owner"}, "deadline": release.time.time() + 300},
    )
    original = "Previous release could not retain compatible pollers (fleet=llm)"
    contention = "DEPLOYMENT_LOCKED: Deployment update for stack 'moonmind'"
    errors = [original, contention, contention]

    async def body(path):
        raise RuntimeError(errors.pop(0))

    async def no_wait(*args):
        pass

    monkeypatch.setattr(release, "_run_job_body", body)
    monkeypatch.setattr(release.asyncio, "sleep", no_wait)
    await release.run_job(request_file)

    last_error = json.loads((directory / "last-error.json").read_text())
    attempts = [entry["error"] for entry in last_error["attempts"]]
    assert attempts[0] == original
    assert attempts[-1] == contention
    assert last_error["error"] == contention

    outcome = json.loads((directory / "result.json").read_text())["error"]
    assert original in outcome
    assert contention in outcome


@pytest.mark.asyncio
async def test_exhaustion_diagnosis_names_the_first_attempt_error(
    tmp_path, monkeypatch
):
    """Delivery exhaustion must surface the cause, not only the last noise."""
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    owner = "mm:5578e7af:execute"
    digest = "sha256:" + "a" * 64
    inputs = {
        "stack": "moonmind",
        "image": {"repository": "example/moonmind", "reference": "candidate"},
    }
    context = {"idempotency_key": owner}
    key = hashlib.sha256(owner.encode()).hexdigest()[:32]
    directory = release.state_root() / key
    directory.mkdir(parents=True)
    original = "Previous release could not retain compatible pollers (fleet=llm)"
    release.write_record(
        directory / "last-error.json",
        {
            "owner": owner,
            "attempt": 3,
            "error": "DEPLOYMENT_LOCKED: already running",
            "attempts": [
                {"attempt": 1, "error": original},
                {"attempt": 3, "error": "DEPLOYMENT_LOCKED: already running"},
            ],
        },
    )
    release.write_record(directory / "deliveries.json", {"count": 3})

    class Runner:
        async def pull(self, **kwargs):
            return {"exitCode": 0}

        async def inspect_image(self, requested):
            return {"Id": "image-id", "RepoDigests": [f"example/moonmind@{digest}"]}

        async def _run_compose_command(self, command, **kwargs):
            return {"exitCode": 0, "stdout": json.dumps({"services": {}})}

    async def inspect_owned(name, job_owner):
        return {"Image": "image-id", "State": {"Running": False, "ExitCode": 1}}

    async def docker(*args):
        return ""

    async def logs_tail(name, tail_lines=30, timeout_seconds=60):
        return "updater log tail"

    async def coherent(*args):
        return {}

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(release, "inspect_owned", inspect_owned)
    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "docker_logs_tail", logs_tail)
    monkeypatch.setattr(release, "require_coherent_images", coherent)
    monkeypatch.setattr(release.asyncio, "sleep", no_sleep)

    with pytest.raises(RuntimeError, match="exhausted three deliveries") as exc_info:
        await release.execute_detached(SimpleNamespace(runner=Runner()), inputs, context)

    assert original in str(exc_info.value)


@pytest.mark.asyncio
async def test_legacy_error_record_is_carried_into_the_attempt_history(
    tmp_path, monkeypatch
):
    """An in-flight release keeps its original error across this update.

    A job already running when the attempt history was introduced has a
    ``last-error.json`` carrying only the top-level ``attempt`` and ``error``.
    Seeding an empty history from that record would erase the original failure
    during the schema transition - the exact diagnostic loss the history
    exists to prevent.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    directory = release.state_root() / "job"
    directory.mkdir(parents=True)
    original = "Previous release could not retain compatible pollers (fleet=llm)"
    release.write_record(
        directory / "last-error.json",
        {"owner": "owner", "attempt": 1, "error": original},
    )

    history = release.record_attempt_error(
        directory, "owner", 2, "DEPLOYMENT_LOCKED: already running"
    )

    assert [entry["error"] for entry in history] == [
        original,
        "DEPLOYMENT_LOCKED: already running",
    ]
    assert history[0]["attempt"] == 1
    recorded = json.loads((directory / "last-error.json").read_text())
    assert recorded["attempts"] == history
    assert original in release.release_failure_summary(history)


def _wedged_manager_disposition(blocked: bool = True) -> dict:
    """A liveness disposition carrying the wedged-singleton signature."""

    return {
        "blocked": blocked,
        "reasonCode": (
            "provider_manager_liveness_blocked" if blocked
            else "provider_manager_liveness_ok"
        ),
        "reasons": ["opencode: nondeterminism_loop"] if blocked else [],
        "evidence": (
            [{"runtime_id": "opencode", "finding": "nondeterminism_loop"}]
            if blocked
            else []
        ),
        "recoveryOwner": "provider-profile-manager-recovery" if blocked else None,
        "recoveryRunbook": "docs/Security/ProviderProfiles.md" if blocked else None,
        "recoveryHint": "hint" if blocked else None,
    }


@pytest.mark.asyncio
async def test_promotion_gate_recovers_a_wedged_manager_before_failing_closed(
    tmp_path, monkeypatch
):
    """The gate must repair the wedge it exists to detect.

    MoonLadderStudios/MoonMind#4363: a wedged singleton is owned by the
    *currently routed* worker, which predates the candidate's caller-side
    recovery. Failing closed before promotion would stop that repair from ever
    becoming current, so the incident this gate exists for would deadlock on
    the old manual cutover. The gate attempts the shared, ledger-gated
    replacement first and re-observes once.
    """
    from unittest.mock import AsyncMock

    import moonmind.provider_profiles.manager_recovery as manager_recovery
    import moonmind.workflows.skills.provider_manager_liveness as liveness

    dispositions = [
        _wedged_manager_disposition(True),
        _wedged_manager_disposition(False),
    ]
    monkeypatch.setattr(
        liveness, "read_db_held_lease_counts", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        liveness, "collect_provider_manager_liveness", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        liveness,
        "evaluate_provider_manager_liveness",
        lambda _observations: dispositions.pop(0),
    )
    recovered = manager_recovery.ManagerReplayRecovery(
        runtime_id="opencode",
        workflow_id="provider-profile-manager:opencode",
        recovered=True,
        terminated_run_id="wedged-run",
        nondeterminism_failures=3,
        startup_restored=True,
    )
    recover = AsyncMock(return_value=recovered)
    monkeypatch.setattr(manager_recovery, "recover_manager_for_runtime", recover)

    cohort = release.ReleaseCohort(SimpleNamespace(), tmp_path, "owner")
    await cohort.qualify_provider_managers(object())

    recover.assert_awaited_once()
    assert recover.await_args.args[1] == "opencode"
    record = json.loads((tmp_path / "manager-liveness.json").read_text())
    assert record["blocked"] is False
    assert record["recoveries"][0]["recovered"] is True
    assert record["recoveries"][0]["terminatedRunId"] == "wedged-run"


@pytest.mark.asyncio
async def test_promotion_gate_still_blocks_when_recovery_is_refused(
    tmp_path, monkeypatch
):
    """A ledger that still spends capacity keeps promotion closed."""
    from unittest.mock import AsyncMock

    import moonmind.provider_profiles.manager_recovery as manager_recovery
    import moonmind.workflows.skills.provider_manager_liveness as liveness

    monkeypatch.setattr(
        liveness, "read_db_held_lease_counts", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        liveness, "collect_provider_manager_liveness", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        liveness,
        "evaluate_provider_manager_liveness",
        lambda _observations: _wedged_manager_disposition(True),
    )
    refused = manager_recovery.ManagerReplayRecovery(
        runtime_id="opencode",
        workflow_id="provider-profile-manager:opencode",
        recovered=False,
        refusal=manager_recovery.MANAGER_HELD_LEASE_PRESENT,
        detail="1 unreleased lease row(s)",
        nondeterminism_failures=3,
        held_leases=1,
    )
    monkeypatch.setattr(
        manager_recovery,
        "recover_manager_for_runtime",
        AsyncMock(return_value=refused),
    )

    cohort = release.ReleaseCohort(SimpleNamespace(), tmp_path, "owner")
    with pytest.raises(RuntimeError, match="not healthy"):
        await cohort.qualify_provider_managers(object())

    record = json.loads((tmp_path / "manager-liveness.json").read_text())
    assert record["blocked"] is True
    assert record["recoveries"][0]["refusal"] == (
        manager_recovery.MANAGER_HELD_LEASE_PRESENT
    )
