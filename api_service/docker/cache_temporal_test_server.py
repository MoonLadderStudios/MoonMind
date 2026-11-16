"""Materialize SDK-compatible Temporal test servers for hermetic tests."""

from __future__ import annotations

import asyncio
import re
import sys

from temporalio.testing import WorkflowEnvironment

# The SDK's "default" time-skipping server floats with Temporal's Java releases.
# Test server 1.41.0 accepts workflow updates but never delivers them to the
# worker, so every update hangs; 1.40.0 is published for every platform the
# test images build on. Return to the SDK default once a newer release
# completes an update again. The local dev server keeps the SDK's selection.
TIME_SKIPPING_TEST_SERVER_VERSION = "v1.40.0"


async def _cache_test_server() -> None:
    for start_environment, options in (
        (
            WorkflowEnvironment.start_time_skipping,
            {"test_server_download_version": TIME_SKIPPING_TEST_SERVER_VERSION},
        ),
        (WorkflowEnvironment.start_local, {}),
    ):
        for retry_delay in (30, 60, None):
            try:
                environment = await start_environment(**options)
                break
            except RuntimeError as exc:
                # The SDK removes partial downloads before reporting archive
                # I/O failures, so retrying never accepts an incomplete binary.
                if retry_delay is None or not re.search(
                    r"failed to download ephemeral server executable: .*"
                    r"HTTP status (?:client|server) error \((?:429|5\d\d)\b"
                    r"|invalid ephemeral server download: could not (?:read|extract) "
                    r"(?:tar|ZIP) archive\b",
                    str(exc),
                ):
                    raise
                print(f"{exc}; retrying in {retry_delay}s", file=sys.stderr, flush=True)
                await asyncio.sleep(retry_delay)
        await environment.shutdown()


if __name__ == "__main__":
    asyncio.run(_cache_test_server())
