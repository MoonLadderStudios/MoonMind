"""Process-tree activity probing.

This is the evidence that made MoonLadderStudios/MoonMind#3771 detectable. In
that run the agent spent its final 23 minutes executing a test suite: it wrote
nothing to stdout (``claude -p`` buffers until the turn ends) and touched only
``__pycache__``, which the workspace-mutation probe deliberately skips. CPU time
was the one signal that separated it from a wedged process.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime

import pytest

from moonmind.workflows.temporal.runtime.process_activity import (
    ProcessActivityProbe,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the probe reads /proc, which only exists on Linux",
)


async def _spawn(*argv: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        # Matches how the launcher spawns managed runtimes: the child is its own
        # session leader, so its session id identifies the whole tree.
        start_new_session=True,
    )


async def _terminate(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(process.pid), 9)
    except (ProcessLookupError, PermissionError):
        process.kill()
    await process.wait()


@pytest.mark.asyncio
async def test_first_sample_is_a_baseline_not_evidence():
    """CPU burned before the first observation cannot be dated, so it is not
    reported as progress at a particular time."""

    process = await _spawn("sleep", "30")
    try:
        probe = ProcessActivityProbe(session_pid=process.pid)
        assert probe.sample(datetime.now(tz=UTC)) is None
    finally:
        await _terminate(process)


@pytest.mark.asyncio
async def test_idle_process_never_reports_progress():
    """A wedged run must still be terminated at its base budget."""

    process = await _spawn("sleep", "30")
    try:
        probe = ProcessActivityProbe(session_pid=process.pid)
        probe.sample(datetime.now(tz=UTC))  # baseline
        await asyncio.sleep(1.0)
        assert probe.sample(datetime.now(tz=UTC)) is None
    finally:
        await _terminate(process)


@pytest.mark.asyncio
async def test_busy_process_reports_progress():
    process = await _spawn(sys.executable, "-c", "x = 0\nwhile True: x += 1")
    try:
        probe = ProcessActivityProbe(session_pid=process.pid)
        probe.sample(datetime.now(tz=UTC))  # baseline
        await asyncio.sleep(0.75)
        observed = probe.sample(datetime.now(tz=UTC))
        assert observed is not None
    finally:
        await _terminate(process)


@pytest.mark.asyncio
async def test_work_done_only_by_a_descendant_counts_as_progress():
    """The #3771 case: the CLI waits while a child does the work.

    An agent that shells out to a test runner or compiler is idle in its own
    right; the CPU is burned by a grandchild. Summing by session id is what makes
    that visible.
    """

    process = await _spawn(
        "sh",
        "-c",
        f"{sys.executable} -c 'x = 0\nwhile True: x += 1' & sleep 30",
    )
    try:
        probe = ProcessActivityProbe(session_pid=process.pid)
        probe.sample(datetime.now(tz=UTC))  # baseline
        await asyncio.sleep(0.75)
        assert probe.sample(datetime.now(tz=UTC)) is not None
    finally:
        await _terminate(process)


@pytest.mark.asyncio
async def test_exited_session_retains_last_observed_activity():
    """After the tree exits the probe stops advancing but does not erase what it
    already saw; the exit path owns the run's outcome from there."""

    process = await _spawn(sys.executable, "-c", "x = 0\nwhile True: x += 1")
    probe = ProcessActivityProbe(session_pid=process.pid)
    probe.sample(datetime.now(tz=UTC))
    await asyncio.sleep(0.75)
    observed = probe.sample(datetime.now(tz=UTC))
    assert observed is not None

    await _terminate(process)
    await asyncio.sleep(0.2)
    assert probe.sample(datetime.now(tz=UTC)) == observed


@pytest.mark.asyncio
async def test_unrelated_process_activity_is_not_counted():
    """Only the supervised session's CPU counts, so a busy host cannot make a
    wedged run look alive."""

    idle = await _spawn("sleep", "30")
    busy = await _spawn(sys.executable, "-c", "x = 0\nwhile True: x += 1")
    try:
        probe = ProcessActivityProbe(session_pid=idle.pid)
        probe.sample(datetime.now(tz=UTC))
        await asyncio.sleep(0.75)
        assert probe.sample(datetime.now(tz=UTC)) is None
    finally:
        await _terminate(busy)
        await _terminate(idle)
