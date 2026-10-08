"""Real Compose/PostgreSQL qualification of the active-journal deployment phase.

CI runs this explicitly beside its already-built candidate image. An actual
controller process exits after partial recreation; another process reuses the
operation and source helper before a rollback to retained legacy consumers.
The image acquisition and HTTP submission boundaries have separate coverage.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
ACTOR = Path(__file__).with_name("journal_transition_actor.py")
WRITER = "temporal-worker-agent-runtime"
SWEEPER = "temporal-worker-artifacts"
CONTROL = "temporal-worker-deployment-control"
pytestmark = [pytest.mark.integration]

CONTROLLER_PHASE = """
import json, os, sys
sys.path.insert(0, sys.argv[1] + '/deploy/controller')
import engine, lock, record, server
store = record.OperationStore(sys.argv[2])
operation = store.load(sys.argv[3])
runner = engine.subprocess_runner()
def apply(operation):
    target = operation['target']
    overlay = server.write_image_overlay(str(store.state_dir), operation['operationId'], operation['desired']['image'])
    with lock.StackLock(store.state_dir, operation['stack']).acquire():
        receipt = server.prepare_controller_journals(store, operation, runner=runner, overlay=overlay)
        print(json.dumps(receipt), flush=True)
        base = engine.compose_base(project=target['project'], project_dir=target['projectDir'],
            compose_files=target['composeFiles'], env_files=[overlay])
        if sys.argv[4] == 'crash':
            engine.apply_services(runner, base, ['temporal-worker-agent-runtime'])
            os._exit(71)
        engine.apply_services(runner, base, target['services'])
        observed = engine.observe_services(runner, base, target['services'])
        assert all(observed['services'].values()), observed
        store.confirm_installed(operation['operationId'], image=operation['desired']['image'])
result = server.converge_on_restart(store, apply, legacy_writer_probe=server.default_legacy_writer_probe)
assert result['converged'] == [operation['operationId']], result
"""


def run(args, *, env, cwd, check=True, timeout=180):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=timeout, check=False)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def await_record(path, *, process_check=None):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text())
        if process_check:
            process_check()
        time.sleep(0.2)
    pytest.fail(f"Journal actor did not produce {path.name}")


def test_live_journal_survives_partial_apply_restart_and_legacy_rollback(tmp_path):
    if os.environ.get("MOONMIND_TEST_JOURNAL_TRANSITION") != "1":
        pytest.skip("Real Compose journey is explicitly enabled in controller-journey CI")
    image = os.environ["MOONMIND_TEST_JOURNAL_APP_IMAGE"]
    env = {name: os.environ[name] for name in (
        "PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
        "DOCKER_API_VERSION", "TMPDIR", "TEMP", "SYSTEMROOT",
    ) if name in os.environ}
    run(["docker", "info"], env=env, cwd=tmp_path)
    run(["docker", "compose", "version"], env=env, cwd=tmp_path)
    run(["docker", "image", "inspect", image], env=env, cwd=tmp_path)
    project = f"moonmind-test-journal-{uuid4().hex[:10]}"
    old_image = f"{project}-legacy:local"
    state = tmp_path / "journal-state"
    state.mkdir()
    controller_state = tmp_path / "controller-state"
    compose_file = tmp_path / "compose.json"
    config = {"services": {}}
    common = {
        "image": "${MOONMIND_IMAGE:-" + image + "}", "pull_policy": "never",
        "user": "0:0", "stop_grace_period": "2s", "restart": "unless-stopped",
        "environment": {
            "PYTHONPATH": "/app",
            "POSTGRES_HOST": "postgres", "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "journey-only", "POSTGRES_DB": "moonmind",
            "TEMPORAL_ARTIFACT_BACKEND": "local_fs", "TEMPORAL_ARTIFACT_ROOT": "/journal-state/blobs",
            "MOONMIND_DEPLOYMENT_LOCK_DIR": "/journal-state/locks",
        },
        "volumes": [f"{state}:/journal-state", f"{ACTOR}:/fixture/actor.py:ro"],
    }
    for service, fleet, mode in ((WRITER, "agent_runtime", "writer"),
                                 (SWEEPER, "artifacts", "sweeper"),
                                 (CONTROL, "deployment", "writer")):
        config["services"][service] = {
            **common, "environment": {**common["environment"], "TEMPORAL_WORKER_FLEET": fleet},
            "entrypoint": ["python", "/fixture/actor.py", mode],
        }
    config["services"]["postgres"] = {
        "image": "postgres:17", "environment": {
            "POSTGRES_PASSWORD": "journey-only", "POSTGRES_DB": "moonmind",
        },
        "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U postgres -d moonmind"],
                        "interval": "1s", "timeout": "3s", "retries": 30},
    }
    compose_file.write_text(json.dumps(config))
    base = ["docker", "compose", "-p", project, "-f", str(compose_file)]

    def compose(*args, check=True):
        return run([*base, *args], env=env, cwd=tmp_path, check=check)

    def actors_healthy(services=(WRITER, SWEEPER)):
        result = compose("ps", "--all", "--format", "json")
        output = result.stdout.strip()
        rows = json.loads(output) if output.startswith("[") else [json.loads(line) for line in output.splitlines()]
        broken = [row for row in rows if row.get("Service") in services
                  and row.get("State") not in ("running", "created")]
        if broken:
            pytest.fail(compose("logs", "--tail", "80", check=False).stdout + str(broken))

    # The old target has the actual app dependencies and retained reader
    # contract, but intentionally cannot run the new preparation helper.
    (tmp_path / "Dockerfile").write_text(
        f"FROM {image}\nUSER 0:0\n"
        "RUN printf '\\ndel controller_journal_cli\\n' >> /app/moonmind/workflows/skills/deployment_release.py\n"
    )
    run(["docker", "build", "--pull=false", "--network=none", "-t", old_image, str(tmp_path)],
        env=env, cwd=tmp_path)
    sys.path.insert(0, str(ROOT / "deploy" / "controller"))
    import record
    import redact
    store = record.OperationStore(controller_state)
    diagnostics = ROOT / "var" / "artifacts" / "journal-transition" / project
    diagnostics.mkdir(parents=True, exist_ok=True)
    phase_number = 0

    def phase(operation, action="finish"):
        nonlocal phase_number
        result = run([sys.executable, "-c", CONTROLLER_PHASE, str(ROOT),
                      str(controller_state), operation["operationId"], action],
                     env=env, cwd=tmp_path, check=False)
        phase_number += 1
        (diagnostics / f"phase-{phase_number}-{action}.json").write_text(json.dumps({
            "operationId": operation["operationId"], "returncode": result.returncode,
            "stdout": redact.redact_text(result.stdout),
            "stderr": redact.redact_text(result.stderr),
            "operation": redact.redact_mapping(store.load(operation["operationId"])),
        }, indent=2))
        if action != "crash":
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    target = {"project": project, "projectDir": str(tmp_path),
              "composeFiles": [str(compose_file)], "services": ["postgres", WRITER, SWEEPER]}
    try:
        compose("up", "-d", "--wait", "postgres")
        compose("up", "-d", "--no-deps", "--pull", "never", WRITER, SWEEPER)
        before = await_record(state / "seeded.json", process_check=actors_healthy)
        await_record(state / "swept.json", process_check=actors_healthy)
        config["services"][WRITER]["entrypoint"][-1] = "writer-resume"
        compose_file.write_text(json.dumps(config))
        operation = store.begin(stack="moonmind", desired_image=image,
                                source_revision="candidate", target=target)
        crashed = phase(operation, "crash")
        assert crashed.returncode == 71, crashed.stdout + crashed.stderr
        assert store.load(operation["operationId"])["installed"] is None
        await_record(state / "resumed.json", process_check=lambda: actors_healthy((WRITER,)))
        original_sources = store.load(operation["operationId"])["journalTransition"]["sourceImages"]
        resumed = phase(store.load(operation["operationId"]))
        assert '"compacted": 1' in resumed.stdout
        completed = store.load(operation["operationId"])
        assert completed["status"] == "succeeded"
        assert completed["journalTransition"]["sourceImages"] == original_sources
        config["services"][WRITER]["entrypoint"][-1] = "legacy-reader"
        config["services"][SWEEPER]["entrypoint"][-1] = "legacy-sweeper"
        compose_file.write_text(json.dumps(config))
        rollback = store.begin(stack="moonmind", desired_image=old_image,
                               source_revision="legacy-contract", target=target)
        phase(rollback)
        readback = await_record(state / "legacy-read.json", process_check=actors_healthy)
        await_record(state / "legacy-swept.json", process_check=actors_healthy)
        # Re-read after the old sweeper has actually run, not only before it.
        read_path = state / "legacy-read.json"
        read_path.unlink()
        readback = await_record(read_path, process_check=actors_healthy)
        assert readback == {"events": 4, "status": before["status"], "providerRefsPreserved": True}
        assert store.load(rollback["operationId"])["journalTransition"]["helperImage"] in original_sources
    finally:
        # Preserve this disposable project's actual evidence before cleanup;
        # the workflow's generic Compose diagnostics target another project.
        for name, args in (("ps", ("ps", "--all", "--format", "json")),
                           ("logs", ("logs", "--no-color", "--tail", "80"))):
            result = compose(*args, check=False)
            (diagnostics / f"{name}.txt").write_text(
                redact.redact_text(result.stdout + result.stderr)
            )
        compose("down", "--remove-orphans", "--volumes", check=False)
        run(["docker", "image", "rm", old_image], env=env, cwd=tmp_path, check=False)
