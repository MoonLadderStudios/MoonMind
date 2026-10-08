"""Exercise Temporal binaries materialized in the hermetic test image."""

from __future__ import annotations

import pytest
from temporalio.api.workflowservice.v1 import GetSystemInfoRequest
from temporalio.testing import WorkflowEnvironment

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]


@pytest.mark.parametrize("server_kind", ["time_skipping", "local"])
async def test_cached_temporal_servers_start_without_download_egress(
    monkeypatch: pytest.MonkeyPatch, server_kind: str
) -> None:
    """The built image starts both cached servers without download access."""

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1,localhost,::1")
    start = getattr(WorkflowEnvironment, f"start_{server_kind}")
    async with await start() as environment:
        await environment.client.workflow_service.get_system_info(
            GetSystemInfoRequest()
        )
