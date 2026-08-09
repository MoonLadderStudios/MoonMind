"""Materialize the Temporal test server used by the hermetic test runner."""

from __future__ import annotations

import asyncio

from temporalio.testing import WorkflowEnvironment


TEMPORAL_TEST_SERVER_VERSION = "v1.29.0"


async def _cache_test_server() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        test_server_download_version=TEMPORAL_TEST_SERVER_VERSION
    )
    await environment.shutdown()


if __name__ == "__main__":
    asyncio.run(_cache_test_server())
