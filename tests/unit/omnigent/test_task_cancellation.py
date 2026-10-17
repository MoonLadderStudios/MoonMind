"""Helper tasks stop even when one cancellation is absorbed."""

from __future__ import annotations

import asyncio

import pytest

from moonmind.omnigent import task_cancellation
from moonmind.omnigent.task_cancellation import cancel_and_wait


def _absorbing(times: int, absorbed: list[int]):
    async def helper() -> None:
        while True:
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                if len(absorbed) >= times:
                    raise
                absorbed.append(1)

    return helper


@pytest.mark.asyncio
async def test_cancel_is_repeated_until_the_helper_stops(monkeypatch) -> None:
    monkeypatch.setattr(task_cancellation, "CANCEL_REPEAT_SECONDS", 0.05)
    absorbed: list[int] = []
    tasks = [asyncio.create_task(_absorbing(2, absorbed)()) for _ in range(2)]
    await asyncio.sleep(0.02)

    await asyncio.wait_for(cancel_and_wait(*tasks), 5)

    assert all(task.cancelled() for task in tasks)
    assert len(absorbed) == 2


@pytest.mark.asyncio
async def test_a_helper_failure_surfaces_after_every_helper_stops() -> None:
    async def failing() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise RuntimeError("cleanup failed") from None

    failing_task = asyncio.create_task(failing())
    other = asyncio.create_task(asyncio.sleep(10))
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await cancel_and_wait(failing_task, None, other)
    assert other.cancelled()


@pytest.mark.asyncio
async def test_the_callers_own_cancellation_is_not_swallowed(monkeypatch) -> None:
    monkeypatch.setattr(task_cancellation, "CANCEL_REPEAT_SECONDS", 0.05)
    absorbed: list[int] = []
    helper = asyncio.create_task(_absorbing(3, absorbed)())
    await asyncio.sleep(0.02)
    caller = asyncio.create_task(cancel_and_wait(helper))
    await asyncio.sleep(0.02)
    caller.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(caller, 5)
    # The helper is still stopped before the caller's cancellation surfaces.
    assert helper.done()
