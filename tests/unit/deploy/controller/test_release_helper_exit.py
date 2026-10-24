"""A completed release one-off must exit after awaited coroutine cleanup."""

import json
import subprocess
import sys

import pytest
from conftest import load


@pytest.mark.parametrize("failure", [False, True])
def test_release_helper_exit_keeps_receipt_cleanup_and_original_failure(
    controller_path, tmp_path, failure
):
    server = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    operation = store.begin(
        stack="moonmind-test-helper-exit",
        desired_image="example/moonmind:latest",
        source_revision="",
        target={"project": "moonmind-test-helper-exit", "projectDir": str(tmp_path)},
    )
    commands = []

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            commands.append(args)
            return {
                "exit": 0,
                "output": 'MOONMIND_OMNIGENT_RESULT={"status":"completed"}',
            }

    server.run_omnigent_step(
        store, operation, runner=Runner(), overlay="", phase="routing-cutover-apply"
    )
    command = commands[0]
    source_index = command.index("-c") + 1
    cleanup = tmp_path / "async-cleanup.txt"
    prelude = f"""
import asyncio, atexit, json, sys, time, types
from pathlib import Path

async def pending():
    try:
        await asyncio.Event().wait()
    finally:
        Path({str(cleanup)!r}).write_text('awaited')

async def finish(phase, payload):
    asyncio.create_task(pending())
    await asyncio.sleep(0)
    if {failure!r}:
        raise RuntimeError('original routing failure')
    print('buffered output')
    print('MOONMIND_OMNIGENT_RESULT=' + json.dumps({{'status': 'completed'}}),
          file=sys.stderr, flush=True)
    return 0

module = types.ModuleType('moonmind.workflows.skills.deployment_release')
module.controller_routing_cutover_cli = finish
sys.modules[module.__name__] = module
# Reproduce a shutdown stall after the coroutine and its cleanup completed.
# The real upgrade helper printed its receipt and exited asyncio.run, but
# interpreter finalization left its operation-owned container running.
atexit.register(lambda: time.sleep(30))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            prelude + "\n" + command[source_index],
            *command[source_index + 1 :],
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert cleanup.read_text() == "awaited"
    assert result.returncode == (1 if failure else 0)
    if failure:
        assert "RuntimeError: original routing failure" in result.stderr
        assert "MOONMIND_OMNIGENT_RESULT=" not in result.stderr
    else:
        assert result.stdout.strip() == "buffered output"
        assert json.loads(result.stderr.split("MOONMIND_OMNIGENT_RESULT=")[1]) == {
            "status": "completed"
        }
