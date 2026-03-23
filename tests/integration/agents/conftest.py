"""Shared fixtures for agent integration tests."""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # dotenv not installed — rely on shell env


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------


def build_fake_profile(
    runtime_id: str,
    *,
    profile_id: str | None = None,
    auth_mode: str = "api_key",
) -> dict[str, Any]:
    """Return a realistic auth profile dict matching DB schema shape."""
    return {
        "profile_id": profile_id or f"profile-{runtime_id}-{uuid.uuid4().hex[:6]}",
        "runtime_id": runtime_id,
        "auth_mode": auth_mode,
        "max_parallel_runs": 2,
        "cooldown_after_429_seconds": 300,
        "rate_limit_policy": "backoff",
        "enabled": True,
        "api_key_ref": f"ref:{runtime_id}-key",
        "account_label": f"test-{runtime_id}",
    }


def build_fake_profile_fetcher(
    runtime_id: str,
    *,
    profiles: list[dict[str, Any]] | None = None,
) -> AsyncMock:
    """Build an async mock for ManagedAgentAdapter's profile_fetcher."""
    if profiles is None:
        profiles = [build_fake_profile(runtime_id)]
    mock = AsyncMock(return_value={"profiles": profiles})
    return mock


def build_stub_callables() -> dict[str, AsyncMock]:
    """Build a full set of async stub callables for ManagedAgentAdapter."""
    return {
        "slot_requester": AsyncMock(),
        "slot_releaser": AsyncMock(),
        "cooldown_reporter": AsyncMock(),
        "run_launcher": AsyncMock(
            return_value={"run_id": f"run-{uuid.uuid4().hex[:8]}", "status": "launching"}
        ),
    }


# ---------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------


def build_execution_request(
    *,
    agent_kind: str = "managed",
    agent_id: str = "gemini_cli",
    profile_ref: str = "auto",
    instruction: str | None = "Add a comment to the top of README.md",
    parameters: dict[str, Any] | None = None,
) -> Any:
    """Build a minimal AgentExecutionRequest."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    correlation_id = f"integ-test-{uuid.uuid4().hex[:8]}"
    return AgentExecutionRequest(
        agentKind=agent_kind,
        agentId=agent_id,
        executionProfileRef=profile_ref,
        correlationId=correlation_id,
        idempotencyKey=f"idem-{correlation_id}",
        instructionRef=instruction,
        parameters=parameters or {},
    )


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def env_vars_present(*keys: str) -> bool:
    """Return True if all named env vars have non-blank values."""
    return all(os.environ.get(k, "").strip() for k in keys)


# ---------------------------------------------------------------------------
# Reusable pytest marks
# ---------------------------------------------------------------------------

requires_jules = pytest.mark.skipif(
    not env_vars_present("JULES_API_KEY"),
    reason="JULES_API_KEY not set",
)

requires_codex_cloud = pytest.mark.skipif(
    not env_vars_present(
        "CODEX_CLOUD_API_KEY", "CODEX_CLOUD_API_URL", "CODEX_CLOUD_ENABLED"
    ),
    reason="Codex Cloud env vars not set",
)

requires_openclaw = pytest.mark.skipif(
    not env_vars_present("OPENCLAW_ENABLED", "OPENCLAW_GATEWAY_TOKEN"),
    reason="OpenClaw env vars not set",
)
