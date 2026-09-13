import json
from types import SimpleNamespace

import pytest

from moonmind.workflows.skills import deployment_release as release


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
    ]))
    monkeypatch.setattr(release, "worker_readiness", AsyncMock(return_value={"ready": True, "buildId": "a"}))
    docker = AsyncMock(side_effect=[
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
