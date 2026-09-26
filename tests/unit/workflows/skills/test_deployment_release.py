import hashlib
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

    A release failure record ends in a worker log tail, and a Python
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
async def test_post_update_migration_failure_marks_release_failed_without_losing_fleet_receipt(
    tmp_path, monkeypatch,
):
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
    release.write_record(
        directory / "deployment-result.json",
        {
            "owner": "owner",
            "result": {
                "status": "COMPLETED",
                "outputs": {"fleetVerified": True},
                "progress": {"state": "SUCCEEDED", "percent": 100},
            },
        },
    )

    async def body(_path):
        raise RuntimeError("managed schedule requires an explicit revision")

    async def no_wait(*_args):
        pass

    monkeypatch.setattr(release, "_run_job_body", body)
    monkeypatch.setattr(release.asyncio, "sleep", no_wait)
    await release.run_job(request_file)

    result = json.loads((directory / "result.json").read_text())["result"]
    assert result["status"] == "FAILED"
    assert result["outputs"]["fleetVerified"] is True
    assert "managed schedule requires an explicit revision" in result["outputs"]["finalError"]
    assert result["progress"]["state"] == "FAILED"
    assert result["progress"]["events"][-1]["state"] == "FAILED"


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
