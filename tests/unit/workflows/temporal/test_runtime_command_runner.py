from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_runtime_command_streams_redacted_progress_before_exit_and_cancellation() -> (
    None
):
    observed = asyncio.Event()
    lines: list[bytes] = []

    async def on_output(_stream: str, line: bytes) -> None:
        lines.append(line)
        observed.set()

    task = asyncio.create_task(
        run_runtime_command(
            (
                sys.executable,
                "-c",
                "import time; print('password=private-example', flush=True); time.sleep(30)",
            ),
            on_output=on_output,
            output_limit_bytes=64,
        )
    )
    try:
        await asyncio.wait_for(observed.wait(), timeout=2)
        assert not task.done()
        assert lines == [b"password=[REDACTED]\n"]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_runtime_command_diagnostic_failure_does_not_mask_command_result() -> (
    None
):
    async def on_output(_stream: str, _line: bytes) -> None:
        raise RuntimeError("evidence store unavailable")

    code, stdout, stderr = await run_runtime_command(
        (sys.executable, "-c", "import sys; print('progress'); sys.exit(3)"),
        on_output=on_output,
    )
    assert (code, stdout, stderr) == (3, b"progress\n", b"")


@pytest.mark.asyncio
async def test_runtime_command_streamed_output_retains_terminal_error_tail() -> None:
    async def on_output(_stream: str, _line: bytes) -> None:
        return None

    code, _stdout, stderr = await run_runtime_command(
        (
            sys.executable,
            "-c",
            "import sys\n"
            "for i in range(200): print('progress line', i, file=sys.stderr)\n"
            "print('Error: pull access denied for example', file=sys.stderr)\n"
            "sys.exit(1)",
        ),
        on_output=on_output,
        output_limit_bytes=256,
    )

    assert code == 1
    assert len(stderr) <= 256
    assert stderr.endswith(b"Error: pull access denied for example\n")
