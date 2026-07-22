from __future__ import annotations

import sys

import pytest

from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command


@pytest.mark.asyncio
async def test_runtime_command_runner_bounds_and_redacts_retained_output() -> None:
    code, stdout, stderr = await run_runtime_command(
        (
            sys.executable,
            "-c",
            "import sys; print('token=raw-secret-' + 'x' * 200); "
            "print('password=other-secret', file=sys.stderr)",
        ),
        timeout_seconds=10,
        output_limit_bytes=64,
    )

    assert code == 0
    assert len(stdout) <= 64
    assert b"raw-secret" not in stdout
    assert stderr == b"password=[REDACTED]\n"


@pytest.mark.asyncio
async def test_runtime_command_runner_kills_timed_out_child() -> None:
    with pytest.raises(TimeoutError):
        await run_runtime_command(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_seconds=0.01,
            output_limit_bytes=64,
        )
