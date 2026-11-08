"""Deployment stops actual journal consumers before changing retained data."""

import json

import pytest

from moonmind.workflows.skills import deployment_release as release


def consumer(identifier, fleet, *, running=True, oneoff=False, image="sha256:source"):
    return {
        "Id": identifier,
        "Image": image,
        "Config": {
            "Env": [f"TEMPORAL_WORKER_FLEET={fleet}"],
            "Labels": {
                "com.docker.compose.project": "moonmind-test",
                "com.docker.compose.service": f"custom-{fleet}",
                "com.docker.compose.oneoff": str(oneoff),
            },
        },
        "State": {"Running": running, "Status": "running" if running else "exited"},
    }


@pytest.mark.asyncio
async def test_journal_transition_observes_writers_and_sweepers_stopped(monkeypatch):
    rows = [consumer("writer", "agent_runtime"), consumer("sweeper", "artifacts"), consumer("api", "workflow")]
    actions = []

    async def docker(*args, **kwargs):
        actions.append(args)
        if args[0] == "ps":
            return "\n".join(row["Id"] for row in rows)
        if args[0] == "inspect":
            return json.dumps(rows)
        assert args[0] == "stop"
        assert set(args[1:]) == {"writer", "sweeper"}
        assert kwargs["timeout_seconds"] == 900
        for row in rows[:2]:
            row["State"] = {"Running": False, "Status": "exited"}
        return ""

    async def compact():
        assert all(not row["State"]["Running"] for row in rows[:2])
        assert actions[-1][0] == "inspect"
        actions.append(("compact",))
        return {"scanned": 2, "compacted": 1, "events": 8}

    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "compact_active_journals", compact, raising=False)
    receipt = await release.prepare_journal_transition("moonmind-test")
    assert receipt["compacted"] == 1
    assert receipt["consumers"] == ["writer", "sweeper"]
    assert rows[-1]["State"]["Running"] is True
    assert actions[-1] == ("compact",)


@pytest.mark.asyncio
@pytest.mark.parametrize("new_consumer", [False, True])
@pytest.mark.parametrize("labeled", [False, True])
async def test_journal_transition_refuses_unproven_quiescence(
    monkeypatch, new_consumer, labeled
):
    rows = [consumer("writer", "core" if labeled else "agent_runtime")]
    if labeled:
        rows[0]["Config"]["Labels"]["moonmind.journal-consumer"] = "true"
    stopped = False

    async def docker(*args, **kwargs):
        nonlocal stopped
        if args[0] == "stop":
            stopped = True
            if new_consumer:
                rows[0]["State"] = {"Running": False, "Status": "exited"}
                unexpected = consumer(
                    "unexpected", "core" if labeled else "artifacts", oneoff=True
                )
                if labeled:
                    unexpected["Config"]["Labels"]["moonmind.journal-consumer"] = "true"
                rows.append(unexpected)
            return ""
        if args[0] == "ps":
            return "\n".join(row["Id"] for row in rows)
        assert args[0] == "inspect"
        return json.dumps(rows)

    async def compact():
        pytest.fail("compaction must not run with a live consumer")

    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "compact_active_journals", compact, raising=False)
    with pytest.raises(RuntimeError, match="Journal consumers.*running"):
        await release.prepare_journal_transition("moonmind-test")
    assert stopped


@pytest.mark.asyncio
async def test_retry_recompacts_after_partial_recreation(monkeypatch):
    rows = [consumer("writer", "agent_runtime")]
    compactions = []

    async def docker(*args, **kwargs):
        if args[0] == "ps":
            return "writer"
        if args[0] == "inspect":
            return json.dumps(rows)
        rows[0]["State"] = {"Running": False, "Status": "exited"}
        return ""

    async def compact():
        compactions.append(True)
        return {"scanned": 1, "compacted": 1, "events": len(compactions)}

    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "compact_active_journals", compact, raising=False)
    await release.prepare_journal_transition("moonmind-test")
    # Compose starts a writer then fails. A retry cannot trust the old receipt:
    # that writer may have committed another bounded chunk before interruption.
    rows[0]["State"] = {"Running": True, "Status": "running"}
    await release.prepare_journal_transition("moonmind-test")
    assert compactions == [True, True]


@pytest.mark.asyncio
async def test_compaction_failure_leaves_recoverable_consumers_stopped(monkeypatch):
    rows = [consumer("writer", "agent_runtime")]

    async def docker(*args, **kwargs):
        if args[0] == "ps":
            return "writer"
        if args[0] == "inspect":
            return json.dumps(rows)
        assert args[0] == "stop"
        rows[0]["State"] = {"Running": False, "Status": "exited"}
        return ""

    async def compact():
        raise RuntimeError("artifact unavailable")

    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "compact_active_journals", compact, raising=False)
    with pytest.raises(RuntimeError, match="artifact unavailable"):
        await release.prepare_journal_transition("moonmind-test")
    assert not rows[0]["State"]["Running"]


@pytest.mark.asyncio
async def test_controller_helper_holds_existing_deployment_lock(tmp_path, monkeypatch, capsys):
    from moonmind.workflows.skills.deployment_execution import (
        FileDeploymentUpdateLockManager,
    )
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_LOCK_DIR", str(lock_dir))

    async def prepare(project, *, compact=True):
        assert (project, compact) == ("moonmind-test", True)
        with pytest.raises(ToolFailure):
            await FileDeploymentUpdateLockManager(lock_dir=str(lock_dir)).acquire(
                "moonmind", wait_seconds=0
            )
        return {"status": "prepared", "scanned": 2, "compacted": 1, "events": 9}

    monkeypatch.setattr(release, "prepare_journal_transition", prepare)
    assert (
        await release.controller_journal_cli(
            "journal-prepare",
            {
                "operationId": "op-1",
                "target": {"project": "moonmind-test"},
                "controllerCapabilities": ["journal-consumer-topology"],
            },
        )
        == 0
    )
    receipt = capsys.readouterr().err
    assert '"compacted": 1' in receipt
    # The operation-scoped helper releases the ordinary deployment lease.
    lease = await FileDeploymentUpdateLockManager(lock_dir=str(lock_dir)).acquire(
        "moonmind", wait_seconds=0
    )
    await lease.release()


@pytest.mark.asyncio
async def test_portable_storage_prepass_uses_rendered_storage_without_recreation():
    calls = []

    class Runner:
        async def _run_compose_services(self):
            return ("api", "postgres", "minio", "temporal-worker-agent-runtime")

        async def _run_compose_command(self, args, **kwargs):
            calls.append(args)
            assert args[-2:] == ("postgres", "minio")
            assert "--no-recreate" in args and "--no-deps" in args
            return {"exitCode": 0}

    await release.prepare_journal_storage(Runner())
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service,fleet,label,selected",
    [
        ("temporal-worker-core", "core", "true", True),
        ("renamed-sweeper", "workflow", "true", True),
        ("temporal-worker-artifacts", "artifacts", "false", False),
        ("temporal-worker-agent-runtime", "agent_runtime", "false", False),
        ("temporal-worker-artifacts", "workflow", None, True),
        ("renamed-legacy", "artifacts", None, True),
        ("unrelated", "workflow", None, False),
    ],
)
async def test_journal_consumer_label_precedes_legacy_fallback(
    monkeypatch, service, fleet, label, selected
):
    row = consumer("candidate", fleet)
    labels = row["Config"]["Labels"]
    labels["com.docker.compose.service"] = service
    if label is not None:
        labels["moonmind.journal-consumer"] = label

    async def docker(*args, **kwargs):
        if args[0] == "ps":
            assert args == (
                "ps",
                "--all",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=moonmind-test",
            )
            return "candidate"
        assert args == ("inspect", "candidate")
        return json.dumps([row])

    monkeypatch.setattr(release, "docker", docker)
    assert await release._journal_consumers("moonmind-test") == (
        [row] if selected else []
    )


@pytest.mark.asyncio
async def test_journal_transition_fences_mixed_consumers_after_partial_recreation(
    monkeypatch,
):
    legacy = consumer("legacy", "artifacts")
    renamed = consumer("core", "core")
    renamed["Config"]["Labels"]["moonmind.journal-consumer"] = "true"
    oneoff = consumer("oneoff", "core", oneoff=True, running=False)
    oneoff["Config"]["Labels"]["moonmind.journal-consumer"] = "true"
    rows = [legacy, renamed, oneoff]
    stops = []

    async def docker(*args, **kwargs):
        if args[0] == "ps":
            return "\n".join(row["Id"] for row in rows)
        if args[0] == "inspect":
            return json.dumps(rows)
        assert args[0] == "stop"
        stops.append(set(args[1:]))
        for row in rows:
            if row["Id"] in args[1:]:
                row["State"] = {"Running": False, "Status": "exited"}
        return ""

    monkeypatch.setattr(release, "docker", docker)
    receipt = await release.prepare_journal_transition("moonmind-test", compact=False)
    assert receipt["consumers"] == ["legacy", "core", "oneoff"]
    assert stops == [{"legacy", "core"}]
    # A partial target recreation needs fresh fencing on the next attempt.
    rows.remove(legacy)
    renamed["State"] = {"Running": True, "Status": "running"}
    await release.prepare_journal_transition("moonmind-test", compact=False)
    assert stops == [{"legacy", "core"}, {"core"}]
    assert not oneoff["State"]["Running"]


@pytest.mark.asyncio
async def test_labeled_consumer_cannot_escape_selected_project(monkeypatch):
    row = consumer("foreign", "core")
    row["Config"]["Labels"].update(
        {
            "moonmind.journal-consumer": "true",
            "com.docker.compose.project": "another-project",
        }
    )

    async def docker(*args, **kwargs):
        return "foreign" if args[0] == "ps" else json.dumps([row])

    monkeypatch.setattr(release, "docker", docker)
    with pytest.raises(RuntimeError, match="escaped the selected project"):
        await release.prepare_journal_transition("moonmind-test", compact=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fleet,service,capabilities,allowed",
    [
        ("core", "temporal-worker-core", [], False),
        ("artifacts", "temporal-worker-core", [], True),
        ("core", "temporal-worker-artifacts", [], True),
        ("core", "temporal-worker-core", ["journal-consumer-topology"], True),
    ],
)
@pytest.mark.parametrize("phase", ["journal-prepare", "journal-quiesce"])
async def test_controller_helper_rejects_unsupported_label_only_consumers_before_fencing(
    monkeypatch, tmp_path, fleet, service, capabilities, allowed, phase
):
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_LOCK_DIR", str(tmp_path / "locks"))
    row = consumer("installed", fleet)
    row["Config"]["Labels"].update(
        {
            "com.docker.compose.service": service,
            "moonmind.journal-consumer": "true",
        }
    )
    prepared = []

    async def docker(*args, **kwargs):
        assert args[0] in ("ps", "inspect"), "compatibility probing must be read-only"
        return "installed" if args[0] == "ps" else json.dumps([row])

    async def prepare(project, *, compact=True):
        prepared.append((project, compact))
        return {"status": "prepared"}

    monkeypatch.setattr(release, "docker", docker)
    monkeypatch.setattr(release, "prepare_journal_transition", prepare)
    payload = {
        "target": {"project": "moonmind-test"},
        "controllerCapabilities": capabilities,
    }
    if allowed:
        assert await release.controller_journal_cli(phase, payload) == 0
        assert prepared == [("moonmind-test", phase == "journal-prepare")]
    else:
        with pytest.raises(RuntimeError, match="journal-consumer-topology"):
            await release.controller_journal_cli(phase, payload)
        assert prepared == []
