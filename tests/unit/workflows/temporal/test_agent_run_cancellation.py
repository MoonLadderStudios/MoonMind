import asyncio
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_agent_run_cancelled_releases_slot():
    # Simulate an agent run that must release a slot even when cancelled.
    slot_release = AsyncMock()

    async def agent_run():
        try:
            # Simulate long-running work that may be cancelled.
            await asyncio.sleep(3600)
        finally:
            # This should run even if the task is cancelled.
            await slot_release()

    # Start the simulated agent run and then cancel it.
    task = asyncio.create_task(agent_run())
    # Let the event loop start the task.
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Verify that cancellation caused the slot to be released exactly once.
    slot_release.assert_awaited_once()
