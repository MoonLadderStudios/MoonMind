"""Integration test: real Jules API multi-step lifecycle via sendMessage.

Requires ``JULES_API_KEY`` in the environment (or ``.env``).
Skipped automatically when the key is absent.

Run manually::

    python -m pytest tests/integration/test_jules_integration.py -v -s

This test creates a Jules session with a trivial prompt, polls until the
session reaches a terminal state (``completed``), then sends a second
prompt via ``sendMessage`` and polls again.  Both steps must reach a
terminal state for the test to pass.

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
    pytest.mark.integration,
    pytest.mark.skipif(not _JULES_API_KEY, reason="JULES_API_KEY not set"),
]


def _build_adapter() -> JulesAgentAdapter:
    """Build a real JulesAgentAdapter backed by the configured API key."""
    client = JulesClient(base_url=_JULES_API_URL, api_key=_JULES_API_KEY)
    return JulesAgentAdapter(client_factory=lambda: client)


def _step1_request() -> AgentExecutionRequest:
    """Build a minimal request that should provoke the fastest Jules completion.

    The prompt is intentionally trivial — we just want Jules to finish ASAP
    so we can exercise the sendMessage → poll cycle.
    """
    correlation_id = f"multi-step-test-{uuid.uuid4().hex[:8]}"
    return AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-multistep-integ-test",
        correlationId=correlation_id,
        idempotencyKey=f"idem-{correlation_id}",
        workspaceSpec={
            "repository": "MoonLadderStudios/MoonMind",
            "branch": "main",
        },
        parameters={
            "title": "[Integration Test] Multi-step lifecycle check",
            "description": (
                "Add a single-line comment '# MoonMind integration test' "
                "to the very top of README.md. Do not change anything else. "
                "Do not run any tests."
            ),
            "metadata": {
                "origin": "multi-step-integration-test",
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
    """End-to-end multi-step lifecycle: start → poll → sendMessage → poll.

    A single test covers the full adapter surface (start, status,
    sendMessage, cancel) using one real Jules session to minimise quota
    usage.
    """

    async def test_two_step_send_message_flow(self) -> None:
        """Create a Jules task, wait for completion, send step 2, wait again.

        This validates that:
        1. A session can be created and polled to completion.
        2. ``sendMessage`` can resume the session with a new prompt.
        3. The resumed session can be polled to completion.
        4. The session ID remains stable across both steps.
        """
        adapter = _build_adapter()
        request = _step1_request()
        created_run_id: str | None = None

        try:
            # ============================================================
            # STEP 1: Create session and poll until done
            # ============================================================
            logger.info("=== STEP 1: Creating Jules session ===")
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

            step1_result = await _poll_until_terminal(
                adapter, handle.run_id, step_label="step-1"
            )
            logger.info(
                "=== STEP 1 COMPLETE: status=%s ===", step1_result.status
            )
            assert step1_result.status == "completed", (
                f"Step 1 ended with status={step1_result.status}, expected completed"
            )

            # ============================================================
            # STEP 2: Send follow-up message and poll until done
            # ============================================================
            logger.info("=== STEP 2: Sending follow-up via sendMessage ===")

            step2_prompt = (
                "Now add a second comment line '# Multi-step test step 2' "
                "directly below the line you just added. "
                "Do not change anything else. Do not run any tests."
            )

            await adapter.send_message(
                run_id=handle.run_id, prompt=step2_prompt
            )
            logger.info("sendMessage accepted — polling for step 2 completion")

            step2_result = await _poll_until_terminal(
                adapter, handle.run_id, step_label="step-2"
            )
            logger.info(
                "=== STEP 2 COMPLETE: status=%s ===", step2_result.status
            )
            assert step2_result.status == "completed", (
                f"Step 2 ended with status={step2_result.status}, expected completed"
            )

            # ---- Verify session ID stability ----
            assert step2_result.run_id == handle.run_id, (
                "Session ID must remain stable across steps"
            )

            logger.info(
                "✅ Multi-step integration test PASSED — session %s "
                "completed both steps successfully.",
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
