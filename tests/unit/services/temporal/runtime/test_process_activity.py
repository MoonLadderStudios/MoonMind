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


def _session_pid_count(session_pid: int) -> int:
    """How many live processes currently carry this session id."""

    probe = ProcessActivityProbe(session_pid=session_pid)
    probe._total_cpu_seconds()
    return len(probe._live_ticks)


@pytest.mark.asyncio
async def test_cpu_of_exited_descendants_stays_in_the_session_total():
    """A short-lived worker must not take its CPU with it when it exits.

    ``/proc`` only lists live processes, so summing it alone makes the session
    total *fall* when a descendant exits. A managed agent running a sequence of
    short-lived compilers or test processes would then read as stalled while
    working continuously: each replacement has to re-earn all the CPU its
    predecessor took with it before the delta turns positive again.
    """

    process = await _spawn(
        "sh",
        "-c",
        # A burst of work, then the worker exits while the session lives on.
        f"{sys.executable} -c 'x = 0\nfor _ in range(8_000_000): x += 1'; sleep 30",
    )
    try:
        probe = ProcessActivityProbe(session_pid=process.pid)
        probe.sample(datetime.now(tz=UTC))  # baseline, before the burst lands

        # Observe the descendant working. sh + python + (later) sleep all carry
        # the session id, so the worker is present while this is true.
        for _ in range(120):
            await asyncio.sleep(0.25)
            if probe.sample(datetime.now(tz=UTC)) is not None:
                break
        assert (
            probe.sample(datetime.now(tz=UTC)) is not None
        ), "the descendant's work was never observed"
        total_with_worker = probe._last_total_seconds
        assert total_with_worker is not None

        # Wait for the worker to actually exit: the session drops back to
        # sh + sleep.
        for _ in range(120):
            await asyncio.sleep(0.25)
            if _session_pid_count(process.pid) <= 2:
                break
        assert _session_pid_count(process.pid) <= 2, "worker never exited"

        # The exited worker's CPU stays in the session total, so the next worker
        # starts from here rather than having to climb back over it.
        probe.sample(datetime.now(tz=UTC))
        assert probe._last_total_seconds is not None
        assert probe._last_total_seconds >= total_with_worker
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
