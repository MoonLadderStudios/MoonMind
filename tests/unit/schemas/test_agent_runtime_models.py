"""Unit tests for canonical agent runtime contract models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentRunStatus,
    ManagedAgentProviderProfile,
    ManagedRuntimeProfile,
    LiveLogChunk,
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


def test_managed_agent_provider_profile_rejects_sensitive_policy_keys() -> None:
    with pytest.raises(
        ValidationError, match="rateLimitPolicy must not contain raw credential keys"
    ):
        ManagedAgentProviderProfile(
            profileId="gemini_oauth_user_a",
            runtimeId="gemini_cli",
            authMode="oauth",
            maxParallelRuns=1,
            enabled=True,
            rateLimitPolicy={"secret_token": "sensitive"},
        )


def test_managed_agent_provider_profile_accepts_valid_per_profile_limits() -> None:
    profile = ManagedAgentProviderProfile(
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


def test_managed_runtime_profile_rejects_github_tokens_in_env_overrides() -> None:
    with pytest.raises(
        ValidationError, match="envOverrides must not contain raw credential keys"
    ):
        ManagedRuntimeProfile(
            profileId="gemini_provider_profile",
            runtimeId="gemini_cli",
            commandTemplate=["gemini"],
            envOverrides={"GH_TOKEN": "ghp-1", "GITHUB_TOKEN": "ghp-2"},
        )


def test_managed_runtime_profile_rejects_other_sensitive_env_override_keys() -> None:
    with pytest.raises(
        ValidationError, match="envOverrides must not contain raw credential keys"
    ):
        ManagedRuntimeProfile(
            profileId="gemini_provider_profile",
            runtimeId="gemini_cli",
            commandTemplate=["gemini"],
            envOverrides={"OPENAI_API_KEY": "secret"},
        )


def test_managed_runtime_profile_allows_managed_launch_metadata_keys() -> None:
    profile = ManagedRuntimeProfile(
        profileId="claude_mm",
        runtimeId="claude_code",
        commandTemplate=["claude"],
        envOverrides={
            "MANAGED_API_KEY_REF": "MINIMAX_API_KEY",
            "MANAGED_API_KEY_TARGET_ENV": "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
        },
    )
    assert profile.env_overrides["MANAGED_API_KEY_TARGET_ENV"] == "ANTHROPIC_AUTH_TOKEN"


def test_managed_runtime_profile_allows_secret_passthrough_key_names() -> None:
    profile = ManagedRuntimeProfile(
        profileId="gemini_provider_profile",
        runtimeId="gemini_cli",
        commandTemplate=["gemini"],
        passthroughEnvKeys=["gh_token", "GITHUB_TOKEN", "GH_TOKEN"],
    )
    assert profile.passthrough_env_keys == ["GH_TOKEN", "GITHUB_TOKEN"]


def test_managed_runtime_profile_rejects_unsupported_secret_passthrough_keys() -> None:
    with pytest.raises(
        ValidationError,
        match="passthroughEnvKeys contains unsupported key",
    ):
        ManagedRuntimeProfile(
            profileId="gemini_provider_profile",
            runtimeId="gemini_cli",
            commandTemplate=["gemini"],
            passthroughEnvKeys=["OPENAI_API_KEY"],
        )


def test_raise_unsupported_status_throws_exception() -> None:
    from moonmind.schemas.agent_runtime_models import raise_unsupported_status, UnsupportedStatusError
    with pytest.raises(UnsupportedStatusError, match="Unsupported status: 'borked'"):
        raise_unsupported_status("borked", context="test-agent")


def test_build_canonical_start_handle_safely_maps_provider_fields() -> None:
    from moonmind.schemas.agent_runtime_models import build_canonical_start_handle
    raw_payload = {
        "external_id": "ext-12345",
        "agentKind": "external",
        "agentId": "jules",
        "status": "running",
        "startedAt": "2026-03-31T00:00:00Z",
        "tracking_ref": "track-999",
        "arbitrary_field": "somevalue",
        "metadata": {"existing": True}
    }
    handle = build_canonical_start_handle(raw_payload)
    assert handle.run_id == "ext-12345"
    assert handle.status == "running"
    # Ensure external_id doesn't poison workflow, but tracking_ref is moved to metadata.
    assert handle.metadata == {"existing": True, "trackingRef": "track-999"}
    assert not hasattr(handle, "arbitrary_field")


def test_build_canonical_status_safely_filters_metadata() -> None:
    from moonmind.schemas.agent_runtime_models import build_canonical_status
    raw_payload = {
        "runId": "run-001",
        "agent_kind": "external",
        "agent_id": "codex_cloud",
        "status": "completed",
        "providerStatus": "succeeded",
        "normalizedStatus": "completed",
        "externalUrl": "https://dashboard.example.com",
        "url": "https://fallback.example.com",
    }
    status = build_canonical_status(raw_payload)
    assert status.run_id == "run-001"
    assert status.agent_id == "codex_cloud"
    assert status.status == "completed"
    assert status.metadata == {
        "providerStatus": "succeeded",
        "normalizedStatus": "completed",
        "externalUrl": "https://dashboard.example.com",
    }


def test_build_canonical_result_enforces_correct_schema() -> None:
    from moonmind.schemas.agent_runtime_models import build_canonical_result
    raw_payload = {
        "outputRefs": ["ref1", "ref2"],
        "summary": "Job completed",
        "unknown_field_should_be_ignored": "true",
        "metrics": {"duration": 150}
    }
    result = build_canonical_result(raw_payload)
    assert result.output_refs == ["ref1", "ref2"]
    assert result.summary == "Job completed"
    assert result.metrics == {"duration": 150}
    assert not hasattr(result, "unknown_field_should_be_ignored")

def test_live_log_chunk_requires_valid_stream() -> None:
    with pytest.raises(ValidationError, match="Input should be 'stdout', 'stderr' or 'system'"):
        LiveLogChunk(sequence=1, stream="invalid_stream", text="test\n", timestamp="2026-03-31T00:00:00Z")

def test_live_log_chunk_accepts_valid_data() -> None:
    chunk = LiveLogChunk(
        sequence=42,
        stream="stdout",
        text="Hello world\n",
        timestamp="2026-03-31T00:00:00Z",
        offset=1024,
    )
    assert chunk.sequence == 42
    assert chunk.stream == "stdout"
    assert chunk.offset == 1024
