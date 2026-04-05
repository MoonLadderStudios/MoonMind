"""Integration test: real Jules API one-shot bundled lifecycle.

Requires ``JULES_API_KEY`` in the environment (or ``.env``).
Skipped automatically when the key is absent.

Run manually::

    python -m pytest tests/integration/test_jules_integration.py -v -s

This test creates one Jules session with a checklist-shaped prompt and
polls until the session reaches a terminal state (``completed``). It is a
smoke test for the bundled one-shot execution path and intentionally does
not exercise normal multi-step ``sendMessage`` progression.

**WARNING**: This test creates real Jules sessions against the configured
repository and will consume Jules task quota.  Each session may create a
real PR.  Use a test/scratch repository if possible.
"""

from __future__ import annotations

import asyncio
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

# Polling constants
_POLL_INTERVAL_SECONDS = 15
_MAX_POLL_MINUTES = 20  # generous ceiling per step
_MAX_POLL_ITERATIONS = int(_MAX_POLL_MINUTES * 60 / _POLL_INTERVAL_SECONDS)

# Terminal adapter statuses
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.provider_verification,
    pytest.mark.jules,
    pytest.mark.requires_credentials,
    pytest.mark.skipif(not _JULES_API_KEY, reason="JULES_API_KEY not set"),
]


def _build_adapter() -> JulesAgentAdapter:
    """Build a real JulesAgentAdapter backed by the configured API key."""
    client = JulesClient(base_url=_JULES_API_URL, api_key=_JULES_API_KEY)
    return JulesAgentAdapter(client_factory=lambda: client)


def _bundled_request() -> AgentExecutionRequest:
    """Build one checklist-shaped request for the bundled Jules smoke test."""
    correlation_id = f"bundle-test-{uuid.uuid4().hex[:8]}"
    return AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-bundle-integ-test",
        correlationId=correlation_id,
        idempotencyKey=f"idem-{correlation_id}",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "branch": "main",
        },
        parameters={
            "title": "[Integration Test] Bundled lifecycle check",
            "description": (
                "You are implementing one cohesive bundled change.\n\n"
                "Ordered Checklist:\n"
                "1. Add a single-line comment '# MoonMind integration test' to the very top of README.md.\n"
                "2. Add a second line '# Bundled integration step 2' directly below it.\n\n"
                "Validation Checklist:\n"
                "- Do not run tests.\n"
                "- Do not change anything else."
            ),
            "metadata": {
                "origin": "bundled-integration-test",
                "repo": "MoonLadderStudios/MoonMind",
            },
        },
    )


async def _poll_until_terminal(
    adapter: JulesAgentAdapter,
    run_id: str,
    *,
    step_label: str = "step",
) -> AgentRunStatus:
    """Poll the adapter until the session reaches a terminal state.

    Returns the final ``AgentRunStatus``.  Raises ``TimeoutError`` if
    the maximum number of poll iterations is exceeded.
    """
    for i in range(1, _MAX_POLL_ITERATIONS + 1):
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        status: AgentRunStatus = await adapter.status(run_id)
        normalized = status.status
        logger.info(
            "[%s] poll %d/%d — status=%s (provider=%s)",
            step_label,
            i,
            _MAX_POLL_ITERATIONS,
            normalized,
            status.metadata.get("providerStatus", "?"),
        )
        if normalized in _TERMINAL_STATUSES:
            return status

    raise TimeoutError(
        f"[{step_label}] Jules session {run_id} did not reach a terminal "
        f"state after {_MAX_POLL_ITERATIONS} polls ({_MAX_POLL_MINUTES} min)"
    )


class TestJulesAdapterLifecycle:
    """End-to-end bundled lifecycle: start one task, poll, then fetch result."""

    async def test_one_shot_bundled_flow(self) -> None:
        """Create a bundled Jules task and wait for terminal completion."""
        adapter = _build_adapter()
        request = _bundled_request()
        created_run_id: str | None = None

        try:
            logger.info("=== BUNDLED RUN: Creating Jules session ===")
            handle: AgentRunHandle = await adapter.start(request)
            created_run_id = handle.run_id

            logger.info(
                "Jules session created: run_id=%s  status=%s",
                handle.run_id,
                handle.status,
            )
            assert handle.run_id, "run_id must be non-empty"
            assert handle.status in {
                "queued",
                "running",
                "awaiting_callback",
                "completed",
            }, f"unexpected initial status: {handle.status}"
            assert handle.poll_hint_seconds is not None, (
                "poll_hint_seconds should be auto-populated from capability descriptor"
            )

            bundled_result = await _poll_until_terminal(
                adapter, handle.run_id, step_label="bundled-run"
            )
            logger.info(
                "=== BUNDLED RUN COMPLETE: status=%s ===", bundled_result.status
            )
            assert bundled_result.status == "completed", (
                f"Bundled run ended with status={bundled_result.status}, expected completed"
            )
            final_result = await adapter.fetch_result(handle.run_id)
            assert final_result.failure_class is None

            logger.info(
                "✅ Bundled integration test PASSED — session %s completed successfully.",
                handle.run_id,
            )

        except Exception:
            if created_run_id:
                logger.warning(
                    "Test failed — Jules session %s may need manual cleanup",
                    created_run_id,
                )
                # Best-effort cleanup: try to cancel/finish the session
                try:
                    await adapter.cancel(created_run_id)
                    logger.info("Cleanup: cancelled session %s", created_run_id)
                except Exception as cleanup_err:
                    logger.warning(
                        "Cleanup cancel failed for %s: %s",
                        created_run_id,
                        cleanup_err,
                    )
            raise
