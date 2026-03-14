"""Unit tests for canonical agent runtime contract models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentRunStatus,
    ManagedAgentAuthProfile,
    is_terminal_agent_run_state,
)


def test_agent_execution_request_requires_non_blank_idempotency_key() -> None:
    with pytest.raises(ValidationError, match="idempotencyKey must not be blank"):
        AgentExecutionRequest(
            agentKind="external",
            agentId="jules",
            executionProfileRef="profile:jules-default",
            correlationId="corr-1",
            idempotencyKey="   ",
        )


def test_agent_execution_request_rejects_sensitive_parameter_keys() -> None:
    with pytest.raises(
        ValidationError, match="parameters must not contain raw credential keys"
    ):
        AgentExecutionRequest(
            agentKind="external",
            agentId="jules",
            executionProfileRef="profile:jules-default",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={"api_key": "should-not-be-accepted"},
        )


def test_agent_run_status_terminal_helpers() -> None:
    status = AgentRunStatus(
        runId="run-1",
        agentKind="external",
        agentId="jules",
        status="completed",
    )
    assert status.terminal is True
    assert is_terminal_agent_run_state("timed_out") is True
    assert is_terminal_agent_run_state("running") is False


def test_agent_run_result_enforces_compact_summary_payloads() -> None:
    with pytest.raises(ValidationError, match="summary must be <="):
        AgentRunResult(
            outputRefs=["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V9"],
            summary="x" * 4097,
        )


def test_managed_agent_auth_profile_rejects_sensitive_policy_keys() -> None:
    with pytest.raises(
        ValidationError, match="rateLimitPolicy must not contain raw credential keys"
    ):
        ManagedAgentAuthProfile(
            profileId="gemini_oauth_user_a",
            runtimeId="gemini_cli",
            authMode="oauth",
            maxParallelRuns=1,
            enabled=True,
            rateLimitPolicy={"secret_token": "sensitive"},
        )


def test_managed_agent_auth_profile_accepts_valid_per_profile_limits() -> None:
    profile = ManagedAgentAuthProfile(
        profileId="claude_team_profile",
        runtimeId="claude_code",
        authMode="oauth",
        maxParallelRuns=2,
        cooldownAfter429=120,
        enabled=True,
        rateLimitPolicy={"strategy": "provider_backoff"},
    )
    assert profile.max_parallel_runs == 2
    assert profile.cooldown_after_429 == 120
