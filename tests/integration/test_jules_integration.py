"""Integration test: real Jules API lifecycle via the universal adapter pattern.

Requires ``JULES_API_KEY`` in the environment (or ``.env``).
Skipped automatically when the key is absent.

Run manually::

    python -m pytest tests/integration/test_jules_integration.py -v -s
"""

from __future__ import annotations

import logging
import os
import uuid

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # dotenv not installed — rely on shell env

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunStatus,
)
from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter
from moonmind.workflows.adapters.jules_client import JulesClient

logger = logging.getLogger(__name__)

_JULES_API_KEY = os.environ.get("JULES_API_KEY", "").strip()
_JULES_API_URL = (
    os.environ.get("JULES_API_URL", "").strip()
    or "https://jules.googleapis.com/v1alpha"
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(not _JULES_API_KEY, reason="JULES_API_KEY not set"),
]


def _build_adapter() -> JulesAgentAdapter:
    """Build a real JulesAgentAdapter backed by the configured API key."""
    client = JulesClient(base_url=_JULES_API_URL, api_key=_JULES_API_KEY)
    return JulesAgentAdapter(client_factory=lambda: client)


def _integration_request() -> AgentExecutionRequest:
    """Build a minimal AgentExecutionRequest for a Jules integration test."""
    correlation_id = f"integ-test-{uuid.uuid4().hex[:8]}"
    return AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-integration-test",
        correlationId=correlation_id,
        idempotencyKey=f"idem-{correlation_id}",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "branch": "main",
        },
        parameters={
            "title": "[Integration Test] MoonMind adapter lifecycle check",
            "description": (
                "This is an automated MoonMind integration test for the "
                "MoonLadderStudios/MoonMind repository. No action is needed — "
                "this task will be cancelled automatically by the test harness."
            ),
            "metadata": {
                "origin": "integration-test",
                "repo": "MoonLadderStudios/MoonMind",
            },
        },
    )


class TestJulesAdapterLifecycle:
    """End-to-end adapter lifecycle: start → status → cancel."""

    async def test_create_poll_and_cancel(self) -> None:
        """Create a real Jules task, poll its status, and cancel it."""
        adapter = _build_adapter()
        request = _integration_request()
        created_run_id: str | None = None

        try:
            # ---- START ----
            handle: AgentRunHandle = await adapter.start(request)
            created_run_id = handle.run_id

            logger.info("Jules task created: run_id=%s", handle.run_id)
            logger.info("  status=%s", handle.status)
            logger.info("  poll_hint_seconds=%s", handle.poll_hint_seconds)

            assert handle.run_id, "run_id must be non-empty"
            assert handle.status in {
                "queued",
                "running",
                "awaiting_callback",
                "completed",
            }, f"unexpected status: {handle.status}"
            assert handle.poll_hint_seconds is not None, (
                "poll_hint_seconds should be auto-populated from capability descriptor"
            )

            # ---- STATUS ----
            status: AgentRunStatus = await adapter.status(handle.run_id)

            logger.info("Jules task status: %s", status.status)

            assert status.run_id == handle.run_id
            assert status.status in {
                "queued",
                "running",
                "awaiting_callback",
                "completed",
                "failed",
                "cancelled",
            }, f"unexpected status: {status.status}"

            # ---- CANCEL ----
            cancel_result: AgentRunStatus = await adapter.cancel(handle.run_id)

            logger.info(
                "Jules task cancel result: status=%s metadata=%s",
                cancel_result.status,
                cancel_result.metadata,
            )

            # Cancel may or may not be accepted depending on task state
            assert cancel_result.status in {
                "cancelled",
                "completed",
                "failed",
                "intervention_requested",
            }, f"unexpected cancel status: {cancel_result.status}"

        except Exception:
            if created_run_id:
                logger.warning(
                    "Test failed — Jules task %s may need manual cleanup",
                    created_run_id,
                )
            raise
