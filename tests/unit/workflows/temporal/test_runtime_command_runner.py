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


@pytest.mark.asyncio
async def test_binary_stdout_is_bounded_and_preserved():
    import sys

    code, out, err = await run_runtime_command(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write(b'\\xffsession.example.com:587');sys.stderr.write('password=private-value')",
        ],
        binary_stdout_limit_bytes=1024,
        output_limit_bytes=1024,
    )
    assert code == 0
    assert out == b"\xffsession.example.com:587"
    assert b"private-value" not in err
    with pytest.raises(RuntimeError, match="binary stdout exceeds"):
        await run_runtime_command(
            [
                sys.executable,
                "-c",
                "import sys,time;sys.stdout.buffer.write(b'x'*10000);sys.stdout.flush();time.sleep(60)",
            ],
            binary_stdout_limit_bytes=100,
            timeout_seconds=3,
        )


@pytest.mark.asyncio
async def test_binary_stdout_flood_drains_after_kill_without_hanging():
    with pytest.raises(RuntimeError, match="binary stdout exceeds"):
        await asyncio.wait_for(
            run_runtime_command(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stdout.buffer.write(b'x'*(10*1024*1024));sys.stdout.flush()",
                ],
                binary_stdout_limit_bytes=1048576,
                timeout_seconds=1,
            ),
            timeout=3,
        )


@pytest.mark.asyncio
async def test_binary_overflow_drains_paused_transport_before_wait(monkeypatch):
    drained = asyncio.Event()

    class Reader:
        def __init__(self, chunks):
            self.chunks = chunks

        async def read(self, count):
            if self.chunks:
                return self.chunks.pop(0)
            drained.set()
            return b""

    class EmptyReader:
        async def read(self, count):
            return b""

    class Process:
        stdin = None
        returncode = None
        stdout = Reader([b"x" * 8192 for _ in range(200)])
        stderr = EmptyReader()

        def kill(self):
            self.returncode = -9

        async def wait(self):
            # Model asyncio's paused stdout transport: even a killed process
            # cannot complete wait until its full pipe has been drained.
            await drained.wait()
            return self.returncode

    async def create(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(RuntimeError, match="binary stdout exceeds"):
        await asyncio.wait_for(
            run_runtime_command(["fixture"], binary_stdout_limit_bytes=1048576), 1
        )
    assert drained.is_set()
