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


def test_agent_execution_request_accepts_codex_managed_session_binding() -> None:
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        managedSession={
            "workflowId": "wf-run-1:session:codex_cli",
            "taskRunId": "wf-run-1",
            "sessionId": "sess:wf-run-1:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
        },
    )

    assert request.managed_session is not None
    assert request.managed_session.runtime_id == "codex_cli"
    assert request.managed_session.session_id == "sess:wf-run-1:codex_cli"


def test_agent_execution_request_rejects_managed_session_for_non_codex_runtime() -> None:
    with pytest.raises(
        ValidationError,
        match="managedSession is only supported for managed Codex runtimes",
    ):
        AgentExecutionRequest(
            agentKind="managed",
            agentId="claude_code",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            managedSession={
                "workflowId": "wf-run-1:session:codex_cli",
                "taskRunId": "wf-run-1",
                "sessionId": "sess:wf-run-1:codex_cli",
                "sessionEpoch": 1,
                "runtimeId": "codex_cli",
            },
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
            credentialSource="oauth_volume",
            runtimeMaterializationMode="oauth_home",
            maxParallelRuns=1,
            enabled=True,
            rateLimitPolicy={"secret_token": "sensitive"},
        )


def test_managed_agent_provider_profile_accepts_valid_per_profile_limits() -> None:
    profile = ManagedAgentProviderProfile(
        profileId="claude_team_profile",
        runtimeId="claude_code",
        credentialSource="oauth_volume",
        runtimeMaterializationMode="oauth_home",
        maxParallelRuns=2,
        cooldownAfter429Seconds=120,
        enabled=True,
        rateLimitPolicy={"strategy": "provider_backoff"},
    )
    assert profile.max_parallel_runs == 2
    assert profile.cooldown_after_429_seconds == 120


def test_managed_runtime_profile_rejects_github_tokens_in_env_overrides() -> None:
    with pytest.raises(
        ValidationError, match="envOverrides must not contain raw credential keys"
    ):
        ManagedRuntimeProfile(
            profileId="gemini_provider_profile",
            runtimeId="gemini_cli",
            commandTemplate=["gemini"],
            envOverrides={"GITHUB_TOKEN": "ghp-2"},
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
        passthroughEnvKeys=["github_token", "GITHUB_TOKEN"],
    )
    assert profile.passthrough_env_keys == ["GITHUB_TOKEN"]


def test_managed_runtime_profile_coerces_legacy_file_templates_mapping() -> None:
    profile = ManagedRuntimeProfile(
        profileId="codex-openrouter",
        runtimeId="codex_cli",
        commandTemplate=["codex", "exec"],
        fileTemplates={
            "/tmp/codex.toml": 'model = "qwen/qwen3.6-plus:free"\n',
        },
    )

    assert len(profile.file_templates) == 1
    assert profile.file_templates[0].path == "/tmp/codex.toml"
    assert profile.file_templates[0].format == "text"
    assert profile.file_templates[0].merge_strategy == "replace"
    assert profile.file_templates[0].content_template == 'model = "qwen/qwen3.6-plus:free"\n'


def test_managed_runtime_profile_coerces_empty_legacy_file_templates_mapping() -> None:
    profile = ManagedRuntimeProfile(
        profileId="codex-openrouter",
        runtimeId="codex_cli",
        commandTemplate=["codex", "exec"],
        fileTemplates={},
    )

    assert profile.file_templates == []


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


def test_build_canonical_result_maps_provider_fields_into_metadata() -> None:
    from moonmind.schemas.agent_runtime_models import build_canonical_result

    raw_payload = {
        "outputRefs": ["ref1"],
        "summary": "Job completed",
        "tracking_ref": "track-123",
        "providerStatus": "done",
        "external_url": "https://dashboard.example.com/runs/1",
        "url": "https://fallback.example.com/runs/1",
    }

    result = build_canonical_result(raw_payload)

    assert result.metadata == {
        "trackingRef": "track-123",
        "providerStatus": "done",
        "externalUrl": "https://dashboard.example.com/runs/1",
    }

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


# ---------------------------------------------------------------------------
# New validation tests for the full provider-profile contract (T004)
# ---------------------------------------------------------------------------


def test_managed_agent_provider_profile_accepts_full_provider_contract() -> None:
    """Instantiate with all provider-profile fields and verify round-trip."""
    profile = ManagedAgentProviderProfile(
        profileId="codex-openrouter",
        runtimeId="codex_cli",
        providerId="openrouter",
        providerLabel="OpenRouter",
        defaultModel="qwen/qwen3.6-plus:free",
        modelOverrides={"small_fast": "qwen/qwen3.6-plus:free"},
        credentialSource="secret_ref",
        runtimeMaterializationMode="composite",
        tags=["openrouter", "codex"],
        priority=150,
        clearEnvKeys=["OPENAI_API_KEY", "OPENROUTER_API_KEY"],
        envTemplate={"OPENROUTER_API_KEY": {"from_secret_ref": "provider_api_key"}},
        fileTemplates=[
            {
                "path": "{{runtime_support_dir}}/codex-home/config.toml",
                "format": "toml",
                "contentTemplate": {"model_provider": "openrouter"},
            }
        ],
        homePathOverrides={"CODEX_HOME": "{{runtime_support_dir}}/codex-home"},
        commandBehavior={"suppress_default_model_flag": True},
        secretRefs={"provider_api_key": "env://OPENROUTER_API_KEY"},
        volumeMountPath="/mnt/data",
        maxLeaseDurationSeconds=3600,
        ownerUserId="user-123",
        maxParallelRuns=4,
    )
    # No ValidationError — assert all fields round-trip.
    dump = profile.model_dump(by_alias=True)
    assert dump["profileId"] == "codex-openrouter"
    assert dump["credentialSource"] == "secret_ref"
    assert dump["runtimeMaterializationMode"] == "composite"
    assert dump["tags"] == ["openrouter", "codex"]
    assert dump["priority"] == 150
    assert dump["clearEnvKeys"] == ["OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    assert dump["envTemplate"] == {
        "OPENROUTER_API_KEY": {"from_secret_ref": "provider_api_key"}
    }
    assert len(dump["fileTemplates"]) == 1
    assert dump["fileTemplates"][0]["format"] == "toml"
    assert dump["homePathOverrides"] == {
        "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
    }
    assert dump["commandBehavior"] == {"suppress_default_model_flag": True}
    assert dump["secretRefs"] == {"provider_api_key": "env://OPENROUTER_API_KEY"}
    assert dump["volumeMountPath"] == "/mnt/data"
    assert dump["maxLeaseDurationSeconds"] == 3600
    assert dump["ownerUserId"] == "user-123"
    assert dump["maxParallelRuns"] == 4


def test_managed_agent_provider_profile_rejects_invalid_credential_source() -> None:
    """Invalid credentialSource raises ValidationError with allowed values."""
    with pytest.raises(ValidationError, match="credentialSource must be one of"):
        ManagedAgentProviderProfile(
            profileId="test",
            runtimeId="codex_cli",
            credentialSource="invalid_value",
            runtimeMaterializationMode="composite",
        )


def test_managed_agent_provider_profile_rejects_invalid_materialization_mode() -> None:
    """Invalid runtimeMaterializationMode raises ValidationError with allowed values."""
    with pytest.raises(ValidationError, match="runtimeMaterializationMode must be one of"):
        ManagedAgentProviderProfile(
            profileId="test",
            runtimeId="codex_cli",
            credentialSource="secret_ref",
            runtimeMaterializationMode="invalid_mode",
        )


def test_managed_agent_provider_profile_rejects_legacy_auth_mode() -> None:
    """Legacy authMode is rejected by extra='forbid'."""
    with pytest.raises(ValidationError):
        ManagedAgentProviderProfile(
            profileId="test",
            runtimeId="codex_cli",
            authMode="oauth",
            credentialSource="oauth_volume",
            runtimeMaterializationMode="oauth_home",
        )


def test_managed_agent_provider_profile_forbids_unknown_fields() -> None:
    """Arbitrary unknown fields are rejected by extra='forbid'."""
    with pytest.raises(ValidationError):
        ManagedAgentProviderProfile(
            profileId="test",
            runtimeId="codex_cli",
            credentialSource="secret_ref",
            runtimeMaterializationMode="composite",
            someFutureField="value",
        )


def test_managed_agent_provider_profile_accepts_credential_source_none() -> None:
    """credential_source='none' with config_bundle materialization is valid (EC-007)."""
    profile = ManagedAgentProviderProfile(
        profileId="config-only",
        runtimeId="codex_cli",
        credentialSource="none",
        runtimeMaterializationMode="config_bundle",
        defaultModel="local/mock",
    )
    assert profile.credential_source == "none"
    assert profile.runtime_materialization_mode == "config_bundle"


def test_managed_runtime_profile_roundtrips_file_templates() -> None:
    """file_templates with TOML entries round-trip through model_dump and model_validate."""
    profile = ManagedRuntimeProfile(
        profileId="codex-openrouter",
        runtimeId="codex_cli",
        commandTemplate=["codex", "exec"],
        fileTemplates=[
            {
                "path": "{{runtime_support_dir}}/codex-home/config.toml",
                "format": "toml",
                "contentTemplate": {
                    "model_provider": "openrouter",
                    "model": "qwen/qwen3.6-plus:free",
                },
            }
        ],
    )
    dumped = profile.model_dump(by_alias=True)
    reparsed = ManagedRuntimeProfile.model_validate(dumped)
    assert len(reparsed.file_templates) == 1
    ft = reparsed.file_templates[0]
    assert ft.path == "{{runtime_support_dir}}/codex-home/config.toml"
    assert ft.format == "toml"
    assert ft.content_template == {
        "model_provider": "openrouter",
        "model": "qwen/qwen3.6-plus:free",
    }
