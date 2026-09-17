import asyncio
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
@pytest.mark.parametrize("repaired", [True, False, "starting"])
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
    health = {"status": "starting" if repaired == "starting" else "unhealthy"}
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
        AsyncMock(return_value="RuntimeError: restricted-egress gateway is not healthy"),
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
        assert "restricted-egress gateway is not healthy" in message
    # The repair renders the definition the retained image owns — never the
    # deployment checkout's newer gateway definition — while the
    # deployment-owned runner identity accompanies the command.
    expected_compose = (
        tmp_path
        / f"retained-compose-{hashlib.sha256(b'sha256:previous').hexdigest()[:16]}.yaml"
    )
    assert captured["compose_file"] == str(expected_compose)
    assert captured["project_name"] == "moonmind"
    assert captured["project_dir"] == str(tmp_path)
    assert captured["image"] == "sha256:previous"
    assert expected_compose.read_text() == embedded_compose
    # The repair is attempted once, never per readiness poll, and never at all
    # while the gateway is still starting on its own.
    assert len(compose_calls) == (0 if repaired == "starting" else 1)


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

    import hashlib

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
