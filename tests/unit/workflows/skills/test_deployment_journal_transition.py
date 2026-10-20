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
async def test_journal_transition_refuses_unproven_quiescence(monkeypatch, new_consumer):
    rows = [consumer("writer", "agent_runtime")]
    stopped = False

    async def docker(*args, **kwargs):
        nonlocal stopped
        if args[0] == "stop":
            stopped = True
            if new_consumer:
                rows[0]["State"] = {"Running": False, "Status": "exited"}
                rows.append(consumer("unexpected", "artifacts", oneoff=True))
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
    assert await release.controller_journal_cli("journal-prepare", {
        "operationId": "op-1", "target": {"project": "moonmind-test"}
    }) == 0
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
