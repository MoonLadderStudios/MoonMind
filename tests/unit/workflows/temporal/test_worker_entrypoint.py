"""Compose's default worker must receive the stop signal and finish draining."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time

import pytest
import yaml


_ROOT = Path(__file__).resolve().parents[4]
_ENTRYPOINT = _ROOT / "services/temporal/scripts/start-worker.sh"
_COMPOSE = yaml.safe_load((_ROOT / "docker-compose.yaml").read_text())
_WORKERS = [
    name
    for name, service in _COMPOSE["services"].items()
    if name.startswith("temporal-worker-")
    and any(
        str(entry).startswith("TEMPORAL_WORKER_COMMAND=")
        for entry in service.get("environment", ())
    )
]
_INTERPOLATION = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")


@pytest.fixture
def worker_probe(tmp_path):
    interpreter = tmp_path / "python"
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import json, os, signal, sys, time\n"
        "from pathlib import Path\n"
        "if sys.argv[1:2] == ['-']:\n"
        "    sys.stdin.read()\n"
        "    raise SystemExit(0)\n"
        "events = Path(os.environ['WORKER_TEST_EVENTS'])\n"
        "def record(kind):\n"
        "    with events.open('a') as output:\n"
        "        output.write(json.dumps({'kind': kind, 'pid': os.getpid(), 'args': sys.argv[1:]}) + '\\n')\n"
        "def drain(*_):\n"
        "    record('stopping')\n"
        "    time.sleep(0.05)\n"
        "    record('drained')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, drain)\n"
        "record('ready')\n"
        "if '--exit' not in sys.argv:\n"
        "    while True:\n"
        "        signal.pause()\n"
    )
    interpreter.chmod(0o755)
    events = tmp_path / "events.jsonl"
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
        "TEMPORAL_WORKER_FLEET": "agent_runtime",
        "WORKER_TEST_EVENTS": str(events),
    }
    env.pop("TEMPORAL_WORKER_COMMAND", None)
    return interpreter, events, env


def _events(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def _launch_and_stop(probe, *, command=None, arguments=()):
    interpreter, events, env = probe
    if command is not None:
        env["TEMPORAL_WORKER_COMMAND"] = command
    process = subprocess.Popen(
        ["/bin/sh", str(_ENTRYPOINT), *arguments],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        observed = []
        while time.monotonic() < deadline:
            observed = _events(events)
            if observed or process.poll() is not None:
                break
            time.sleep(0.01)
        assert observed, (
            process.stderr.read().decode()
            if process.poll() is not None
            else "worker did not start"
        )
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        observed = _events(events)
        assert [event["kind"] for event in observed] == [
            "ready",
            "stopping",
            "drained",
        ], "SIGTERM missed the worker's drain handler: " + repr(observed)
        assert process.returncode == 0
        assert observed[0]["pid"] == process.pid
        return observed[0]["args"]
    finally:
        # Only the disposable launcher's own session, including a child left
        # behind by the broken shell path, belongs to this test.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            # Graceful shutdown may already have removed the whole group.
            pass
        process.wait(timeout=5)
        process.stderr.close()


@pytest.mark.parametrize("service", _WORKERS)
def test_default_compose_worker_receives_sigterm_and_drains(worker_probe, service):
    declared = next(
        str(entry).partition("=")[2]
        for entry in _COMPOSE["services"][service]["environment"]
        if str(entry).startswith("TEMPORAL_WORKER_COMMAND=")
    )
    command = _INTERPOLATION.sub(lambda match: match[1], declared)
    if command:
        # Resolve the probe by absolute path because an explicit command runs
        # in a login shell that can replace PATH. Keep its shell launch intact.
        words = shlex.split(command)
        assert words[0] == "python"
        command = shlex.join([str(worker_probe[0]), *words[1:]])
    assert _launch_and_stop(worker_probe, command=command) == [
        "-m",
        "moonmind.workflows.temporal.worker_runtime",
    ]


@pytest.mark.parametrize("command", [None, "", "sleep infinity"])
def test_canonical_default_and_migration_sentinel_drain(worker_probe, command):
    assert _launch_and_stop(worker_probe, command=command) == [
        "-m",
        "moonmind.workflows.temporal.worker_runtime",
    ]


def test_explicit_worker_arguments_remain_intact(worker_probe):
    arguments = [str(worker_probe[0]), "--custom", "argument with spaces"]
    assert _launch_and_stop(worker_probe, arguments=arguments) == arguments[1:]


def test_explicit_exec_override_owns_signal_handling(worker_probe):
    command = "exec " + shlex.join(
        [str(worker_probe[0]), "--custom", "quoted argument"]
    )
    assert _launch_and_stop(worker_probe, command=command) == [
        "--custom",
        "quoted argument",
    ]


def test_explicit_shell_override_keeps_its_shell_semantics(worker_probe, tmp_path):
    interpreter, events, env = worker_probe
    after = tmp_path / "after-command"
    env["TEMPORAL_WORKER_COMMAND"] = (
        shlex.join([str(interpreter), "--exit", "quoted argument"])
        + "; printf done > "
        + shlex.quote(str(after))
    )
    result = subprocess.run(
        ["/bin/sh", str(_ENTRYPOINT)],
        env=env,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert _events(events)[0]["args"] == ["--exit", "quoted argument"]
    assert after.read_text() == "done"


def test_plural_only_worker_receives_sigterm_and_drains(worker_probe):
    worker_probe[2].pop("TEMPORAL_WORKER_FLEET", None)
    worker_probe[2]["TEMPORAL_WORKER_FLEETS"] = "workflow,artifacts"
    assert _launch_and_stop(worker_probe) == [
        "-m",
        "moonmind.workflows.temporal.worker_runtime",
    ]
