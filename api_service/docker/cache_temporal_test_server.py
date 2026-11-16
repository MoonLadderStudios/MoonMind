"""Materialize SDK-compatible Temporal test servers for hermetic tests."""

from __future__ import annotations

import asyncio
import re
import sys

from temporalio.testing import WorkflowEnvironment

# The SDK's "default" time-skipping server resolves to sdk-java v1.41.0, which
# never completes workflow updates: ``execute_update`` waits until cancelled.
# Select the previous default instead. temporal.download publishes it
# for Linux amd64 and arm64 (an explicit release missing on ARM64 once made the
# test image unbuildable there) and needs the "v" prefix. Remove this selection
# once the SDK default passes
# tests/unit/workflows/temporal/test_time_skipping_test_server.py.
TIME_SKIPPING_TEST_SERVER_VERSION = "v1.40.0"


def select_time_skipping_test_server() -> None:
    """Default this process's time-skipping environments to the selected server.

    The image pre-warm and the test session both call this, so tests start the
    binary the image already holds. Callers may still choose another version.
    """
    sdk_start = WorkflowEnvironment.start_time_skipping.__func__

    async def start_time_skipping(
        cls,
        *,
        test_server_download_version: str = TIME_SKIPPING_TEST_SERVER_VERSION,
        **kwargs,
    ) -> WorkflowEnvironment:
        return await sdk_start(
            cls, test_server_download_version=test_server_download_version, **kwargs
        )

    WorkflowEnvironment.start_time_skipping = classmethod(start_time_skipping)


async def _cache_test_server() -> None:
    for start_environment in (
        WorkflowEnvironment.start_time_skipping,
        WorkflowEnvironment.start_local,
    ):
        for retry_delay in (30, 60, None):
            try:
                environment = await start_environment()
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
    select_time_skipping_test_server()
    asyncio.run(_cache_test_server())
