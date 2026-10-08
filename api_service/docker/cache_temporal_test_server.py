"""Materialize SDK-compatible Temporal test servers for hermetic tests."""

from __future__ import annotations

import asyncio

from temporalio.testing import WorkflowEnvironment


async def _cache_test_server() -> None:
    # Let the installed SDK select its compatible binary. Temporal does not
    # publish every explicit test-server release for every architecture; pinning
    # v1.29.0 made the on-demand Python test image unbuildable on Linux ARM64.
    for start_environment in (
        WorkflowEnvironment.start_time_skipping,
        WorkflowEnvironment.start_local,
    ):
        environment = await start_environment()
        await environment.shutdown()


if __name__ == "__main__":
    asyncio.run(_cache_test_server())
