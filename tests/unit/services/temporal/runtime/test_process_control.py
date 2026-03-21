"""Unit tests for managed process lifecycle control utilities."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from moonmind.workflows.temporal.runtime.process_control import (
    cancel_managed_process,
)


def _make_mock_process(*, pid: int = 42, returncode: int | None = None):
    """Create a mock asyncio subprocess Process."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.send_signal = MagicMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_cancel_already_terminated():
    """Process already exited — no signals sent."""
    proc = _make_mock_process(returncode=0)
    result = await cancel_managed_process(proc)
    assert result == 0
    proc.send_signal.assert_not_called()
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_graceful_exit():
    """Process exits during grace period — no SIGKILL."""
    proc = _make_mock_process(returncode=None)

    async def _simulate_exit():
        proc.returncode = 0

    proc.wait = AsyncMock(side_effect=_simulate_exit)

    result = await cancel_managed_process(proc, grace_seconds=1.0)
    assert result == 0
    proc.send_signal.assert_called_once()
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_requires_sigkill():
    """Process ignores SIGTERM — SIGKILL sent after grace period."""
    proc = _make_mock_process(returncode=None)

    call_count = 0

    async def _wait_behavior():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First wait (SIGTERM grace) — times out
            raise asyncio.TimeoutError()
        else:
            # Second wait (after SIGKILL) — exit
            proc.returncode = -9

    proc.wait = AsyncMock(side_effect=_wait_behavior)

    result = await cancel_managed_process(proc, grace_seconds=0.1)
    assert result == -9
    proc.send_signal.assert_called_once()
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_process_already_gone():
    """ProcessLookupError on SIGTERM — process already gone."""
    proc = _make_mock_process(returncode=None)
    proc.send_signal = MagicMock(side_effect=ProcessLookupError)

    result = await cancel_managed_process(proc)
    assert result is None  # returncode was still None
    proc.kill.assert_not_called()
