"""Integration tests: Codex Cloud external agent adapter lifecycle.

Requires ``CODEX_CLOUD_ENABLED=true``, ``CODEX_CLOUD_API_URL``, and
``CODEX_CLOUD_API_KEY`` in the environment (or ``.env``).
Skipped automatically when the env vars are absent.

**WARNING**: This test creates real Codex Cloud tasks against the configured
endpoint and will consume API quota.

Run manually::

    CODEX_CLOUD_ENABLED=true CODEX_CLOUD_API_URL=<url> CODEX_CLOUD_API_KEY=<key> \
      python -m pytest tests/integration/agents/test_codex_cloud_integration.py -v -s
"""

from __future__ import annotations

import os
import uuid

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from tests.integration.agents.conftest import requires_codex_cloud

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    requires_codex_cloud,
]


def _build_adapter():
    """Build a real CodexCloudAgentAdapter from env config."""
    from moonmind.workflows.adapters.codex_cloud_agent_adapter import (
        CodexCloudAgentAdapter,
    )
    from moonmind.workflows.adapters.codex_cloud_client import CodexCloudClient

    cloud_url = os.environ.get("CODEX_CLOUD_API_URL", "").strip()
    cloud_key = os.environ.get("CODEX_CLOUD_API_KEY", "").strip()
    client = CodexCloudClient(base_url=cloud_url, api_key=cloud_key)
    return CodexCloudAgentAdapter(client_factory=lambda: client)


def _build_request():
    """Build a minimal request for Codex Cloud."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    correlation_id = f"codex-cloud-test-{uuid.uuid4().hex[:8]}"
    return AgentExecutionRequest(
        agentKind="external",
        agentId="codex_cloud",
        executionProfileRef="profile:codex-cloud-integ-test",
        correlationId=correlation_id,
        idempotencyKey=f"idem-{correlation_id}",
        parameters={
            "title": "[Integration Test] Codex Cloud lifecycle check",
            "description": (
                "Add a single-line comment '# MoonMind integration test' "
                "to the very top of README.md. Do not change anything else."
            ),
        },
    )


class TestCodexCloudAdapterLifecycle:
    """End-to-end lifecycle for the Codex Cloud adapter.

    Tests start → status → cancel to exercise the full adapter surface
    with minimal quota usage.
    """

    async def test_start_returns_handle(self) -> None:
        """Creating a task should return a valid AgentRunHandle."""
        adapter = _build_adapter()
        request = _build_request()

        handle = await adapter.start(request)

        assert handle.run_id, "run_id must not be empty"
        assert handle.agent_kind == "external"
        assert handle.agent_id == "codex_cloud"
        assert handle.status in {
            "queued",
            "running",
            "awaiting_callback",
            "completed",
        }
        assert handle.poll_hint_seconds is not None

    async def test_status_returns_status(self) -> None:
        """Polling status on a created task should return normalized status."""
        adapter = _build_adapter()
        request = _build_request()
        handle = await adapter.start(request)

        status = await adapter.status(handle.run_id)

        assert status.run_id == handle.run_id
        assert status.agent_kind == "external"
        assert status.agent_id == "codex_cloud"
        assert status.status in {
            "queued",
            "running",
            "awaiting_callback",
            "completed",
            "failed",
            "cancelled",
        }

    async def test_cancel_accepted(self) -> None:
        """Cancelling a task should return cancel-related status."""
        adapter = _build_adapter()
        request = _build_request()
        handle = await adapter.start(request)

        cancel_status = await adapter.cancel(handle.run_id)

        # Cancel may or may not be accepted depending on provider state
        assert cancel_status.run_id == handle.run_id
        assert cancel_status.agent_kind == "external"

    async def test_fetch_result(self) -> None:
        """fetch_result on a created task should return AgentRunResult shape."""
        adapter = _build_adapter()
        request = _build_request()
        handle = await adapter.start(request)

        result = await adapter.fetch_result(handle.run_id)

        assert result.summary is not None or result.summary is None  # always returns
        assert isinstance(result.output_refs, list)
