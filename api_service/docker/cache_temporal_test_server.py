"""Materialize SDK-compatible Temporal test servers for hermetic tests."""

from __future__ import annotations

import asyncio
import re
import sys

from temporalio.testing import WorkflowEnvironment


async def _cache_test_server() -> None:
    # Let the installed SDK select its compatible binary. Temporal does not
    # publish every explicit test-server release for every architecture; pinning
    # v1.29.0 made the on-demand Python test image unbuildable on Linux ARM64.
    for start_environment in (
        WorkflowEnvironment.start_time_skipping,
        WorkflowEnvironment.start_local,
    ):
        for retry_delay in (30, 60, None):
            try:
                environment = await start_environment()
                break
            except RuntimeError as exc:
                if retry_delay is None or not re.search(
                    r"failed to download ephemeral server executable: .*"
                    r"HTTP status (?:client|server) error \((?:429|5\d\d)\b",
                    str(exc),
                ):
                    raise
                print(f"{exc}; retrying in {retry_delay}s", file=sys.stderr, flush=True)
                await asyncio.sleep(retry_delay)
        await environment.shutdown()


if __name__ == "__main__":
    asyncio.run(_cache_test_server())
