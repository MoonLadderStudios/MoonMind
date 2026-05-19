"""Unit tests for canonical agent runtime contract models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentRunStatus,
    ManagedAgentRuntimeProfile,
    ManagedAgentProviderProfile,
    ManagedRuntimeProfile,
    MoonMindOpsRuntime,
    LiveLogChunk,
    RuntimeCommandInvocation,
    RuntimeCommandRenderResult,
    build_docker_sidecar_launch_plan,
    extract_durable_retrieval_metadata,
    is_terminal_agent_run_state,
    resolve_managed_runtime_workload_mode,
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

def test_agent_execution_request_accepts_runtime_command_metadata() -> None:
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="/review\nCheck this.",
        runtimeCommand={
            "kind": "slash_command",
            "source": "leading_slash",
            "sourcePath": "objective.instructions",
            "command": "review",
            "rawCommand": "/review",
            "args": "",
            "instructionBody": "Check this.",
            "targetRuntime": "codex_cli",
            "detectionStatus": "detected",
            "hintStatus": "hinted",
            "recognitionMode": "hinted_runtime_passthrough",
            "renderMode": "materialized_command",
            "materializedCommand": {
                "path": ".claude/commands/review.md",
                "invocation": "/project:review",
            },
            "requiresRuntimeRecognition": True,
            "runtimeCapabilityVersion": "2026-05-13",
            "hintCatalogVersion": "2026-05-13",
            "detectionPhase": "submit",
        },
    )

    assert request.runtime_command is not None
    assert request.runtime_command.command == "review"
    assert request.runtime_command.render_mode == "materialized_command"
    assert request.runtime_command.materialized_command == {
        "path": ".claude/commands/review.md",
        "invocation": "/project:review",
    }
    assert request.model_dump(by_alias=True)["runtimeCommand"]["rawCommand"] == "/review"

def test_runtime_command_render_result_supports_failure_and_prompt_prefix() -> None:
    invocation = RuntimeCommandInvocation(
        kind="slash_command",
        source="leading_slash",
        sourcePath="objective.instructions",
        command="review",
        rawCommand="/review",
        args="",
        instructionBody="Check this.",
        targetRuntime="codex_cli",
        detectionStatus="detected",
        hintStatus="hinted",
        recognitionMode="hinted_runtime_passthrough",
        requiresRuntimeRecognition=True,
        runtimeCapabilityVersion="2026-05-13",
        hintCatalogVersion="2026-05-13",
        detectionPhase="submit",
    )

    ok = RuntimeCommandRenderResult(
        status="ok",
        renderMode="prompt_prefix",
        renderedInstruction="/review\n\nCheck this.",
        invocation=invocation,
    )
    failed = RuntimeCommandRenderResult(
        status="failed",
        failureReason="runtime_command_render_failed",
        diagnostics={"message": "redacted"},
    )

    assert ok.render_mode == "prompt_prefix"
    assert ok.rendered_instruction.startswith("/review")
    assert failed.failure_reason == "runtime_command_render_failed"

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

def test_codex_oauth_provider_profile_requires_auth_volume_refs() -> None:
    with pytest.raises(ValidationError, match="volumeRef is required"):
        ManagedAgentProviderProfile(
            profileId="codex_oauth_missing_volume",
            runtimeId="codex_cli",
            providerId="openai",
            credentialSource="oauth_volume",
            runtimeMaterializationMode="oauth_home",
        )

    with pytest.raises(ValidationError, match="volumeMountPath is required"):
        ManagedAgentProviderProfile(
            profileId="codex_oauth_missing_mount",
            runtimeId="codex_cli",
            providerId="openai",
            credentialSource="oauth_volume",
            runtimeMaterializationMode="oauth_home",
            volumeRef="codex_auth_volume",
        )

def test_codex_oauth_provider_profile_preserves_secret_free_refs_and_policy() -> None:
    profile = ManagedAgentProviderProfile(
        profileId="codex_oauth_profile",
        runtimeId="codex_cli",
        providerId="openai",
        credentialSource="oauth_volume",
        runtimeMaterializationMode="oauth_home",
        volumeRef="codex_auth_volume",
        volumeMountPath="/home/app/.codex",
        maxParallelRuns=3,
        cooldownAfter429Seconds=120,
        maxLeaseDurationSeconds=900,
        rateLimitPolicy={"strategy": "queue"},
    )

    assert profile.provider_id == "openai"
    assert profile.volume_ref == "codex_auth_volume"
    assert profile.volume_mount_path == "/home/app/.codex"
    assert profile.max_parallel_runs == 3
    assert profile.cooldown_after_429_seconds == 120
    assert profile.max_lease_duration_seconds == 900
    assert profile.rate_limit_policy == {"strategy": "queue"}

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
            "/tmp/codex.toml": 'model = "qwen/qwen3.6-plus"\n',
        },
    )

    assert len(profile.file_templates) == 1
    assert profile.file_templates[0].path == "/tmp/codex.toml"
    assert profile.file_templates[0].format == "text"
    assert profile.file_templates[0].merge_strategy == "replace"
    assert profile.file_templates[0].content_template == 'model = "qwen/qwen3.6-plus"\n'

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
    with pytest.raises(
        ValidationError,
        match="Input should be 'stdout', 'stderr', 'system' or 'session'",
    ):
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
        defaultModel="qwen/qwen3.6-plus",
        modelOverrides={"small_fast": "qwen/qwen3.6-plus"},
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
                    "model": "qwen/qwen3.6-plus",
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
        "model": "qwen/qwen3.6-plus",
    }


def test_extract_durable_retrieval_metadata_only_allows_boolean_contract_fields() -> None:
    metadata = extract_durable_retrieval_metadata(
        {
            "metadata": {
                "moonmind": {
                    "retrievedContextItemCount": True,
                    "retrievalContextTruncated": True,
                    "retrievalMode": "semantic",
                }
            }
        }
    )

    assert metadata == {
        "retrievalContextTruncated": True,
        "retrievalMode": "semantic",
    }


def _valid_docker_sidecar_profile() -> dict:
    return {
        "workloadMode": "docker-sidecar",
        "workspace": {
            "volume": "agent_workspaces",
            "mountPath": "/work/agent_jobs",
            "repoEnv": "MOONMIND_REPO_DIR",
            "lifecycle": "session",
        },
        "agent": {
            "image": "moonmind/managed-agent:2026-05-16",
            "workspace": {"mountPath": "/work/agent_jobs"},
            "dockerClient": {
                "enabled": True,
                "composePlugin": True,
                "daemonInAgent": False,
            },
            "env": {
                "DOCKER_HOST": "unix:///var/run/moonmind-docker/docker.sock",
            },
            "mounts": [
                {"name": "workspace", "mountPath": "/work/agent_jobs"},
                {"name": "docker-socket", "mountPath": "/var/run/moonmind-docker"},
            ],
        },
        "dockerSidecar": {
            "enabled": True,
            "mode": "dind",
            "image": "docker:27-dind",
            "socket": {
                "path": "/var/run/moonmind-docker/docker.sock",
                "volumeName": "docker-socket",
            },
            "storage": {
                "volumeName": "docker-graph",
                "mountPath": "/var/lib/docker",
                "lifecycle": "session",
                "daemonScope": "session",
            },
            "workspace": {"mountPath": "/work/agent_jobs"},
            "security": {
                "privileged": True,
                "hostDockerSocket": "forbidden",
                "moonmindDeploymentSecrets": "forbidden",
            },
            "mounts": [
                {"name": "workspace", "mountPath": "/work/agent_jobs"},
                {"name": "docker-socket", "mountPath": "/var/run/moonmind-docker"},
                {"name": "docker-graph", "mountPath": "/var/lib/docker"},
            ],
            "optionalCaches": [
                {
                    "name": "pip-cache",
                    "volumeName": "mm-cache-pip",
                    "mountPath": "/cache/pip",
                    "approvalRef": "deployment-approved-cache-pip",
                }
            ],
        },
        "resources": {
            "session": {"maxRuntimeSeconds": 14400},
            "agent": {"cpu": "2", "memory": "4Gi"},
            "dockerSidecar": {
                "cpu": "4",
                "memory": "8Gi",
                "ephemeralStorage": "40Gi",
            },
            "nestedContainers": {
                "defaultCpu": "2",
                "defaultMemory": "4Gi",
                "maxContainers": 16,
            },
        },
        "cleanup": {
            "idempotent": True,
            "onSessionEnd": {
                "stopSidecar": True,
                "stopNestedContainers": True,
                "removeDockerGraph": True,
                "removeDockerSocket": True,
                "preserveWorkspace": "retention_policy",
            },
            "onSidecarFailure": {
                "markDockerCapabilityUnavailable": True,
                "preserveAgentSession": True,
            },
            "onAgentFailure": {
                "stopSidecar": True,
                "preserveWorkspace": "retention_policy",
            },
        },
        "readiness": {
            "docker": {
                "required": True,
                "timeoutSeconds": 60,
                "intervalSeconds": 2,
            },
        },
        "labels": {
            "moonmind.kind": "managed-session",
            "moonmind.workload_mode": "docker-sidecar",
        },
        "policy": {
            "hostDockerSocket": "forbidden",
            "sharedDaemonAcrossUsers": "forbidden",
            "moonmindDeploymentSecretsInSession": "forbidden",
            "appContainerControlFromSession": "forbidden",
            "apiContainerWorkloadDockerSocketAccess": False,
        },
    }


def test_managed_agent_runtime_profile_accepts_valid_docker_sidecar_contract() -> None:
    profile = ManagedAgentRuntimeProfile.model_validate(_valid_docker_sidecar_profile())

    assert profile.workload_mode == "docker-sidecar"
    assert profile.agent.docker_client.enabled is True
    assert (
        profile.agent.env["DOCKER_HOST"]
        == "unix:///var/run/moonmind-docker/docker.sock"
    )
    assert profile.docker_sidecar is not None
    assert profile.docker_sidecar.image == "docker:27-dind"
    assert profile.resources.docker_sidecar is not None
    assert profile.resources.docker_sidecar.ephemeral_storage == "40Gi"
    assert profile.labels == {
        "moonmind.kind": "managed-session",
        "moonmind.workload_mode": "docker-sidecar",
    }
    assert profile.cleanup.on_session_end.remove_docker_graph is True
    assert profile.policy.host_docker_socket == "forbidden"
    assert profile.policy.shared_daemon_across_users == "forbidden"
    assert profile.policy.moonmind_deployment_secrets_in_session == "forbidden"


def test_managed_agent_runtime_profile_rejects_sensitive_label_keys() -> None:
    payload = _valid_docker_sidecar_profile()
    payload["labels"]["moonmind.session_token"] = "should-not-leak"

    with pytest.raises(ValidationError, match="labels must not receive deployment"):
        ManagedAgentRuntimeProfile.model_validate(payload)


def test_managed_agent_runtime_profile_builds_mm695_sidecar_launch_plan() -> None:
    profile = ManagedAgentRuntimeProfile.model_validate(_valid_docker_sidecar_profile())

    plan = build_docker_sidecar_launch_plan(profile)

    assert plan is not None
    dumped = plan.model_dump(mode="json", by_alias=True)
    assert dumped["issueKey"] == "MM-695"
    assert dumped["applyLimitsOutsideNestedDaemon"] is True
    assert dumped["resources"]["session"]["maxRuntimeSeconds"] == 14400
    assert dumped["labels"] == {
        "moonmind.kind": "managed-session",
        "moonmind.workload_mode": "docker-sidecar",
    }
    assert dumped["resources"]["dockerSidecar"]["ephemeralStorage"] == "40Gi"
    assert dumped["resources"]["nestedContainers"]["maxContainers"] == 16
    assert dumped["cleanup"]["onSessionEnd"] == {
        "stopSidecar": True,
        "stopNestedContainers": True,
        "removeDockerGraph": True,
        "removeDockerSocket": True,
        "preserveWorkspace": "retention_policy",
    }
    assert dumped["cleanup"]["onSidecarFailure"] == {
        "markDockerCapabilityUnavailable": True,
        "preserveAgentSession": True,
    }
    assert dumped["cleanup"]["onAgentFailure"] == {
        "stopSidecar": True,
        "preserveWorkspace": "retention_policy",
    }
    assert dumped["optionalCaches"] == [
        {
            "name": "pip-cache",
            "volumeName": "mm-cache-pip",
            "mountPath": "/cache/pip",
            "approvalRef": "deployment-approved-cache-pip",
            "readOnly": False,
        }
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda p: p["agent"]["dockerClient"].update({"enabled": False}),
            "agent.dockerClient.enabled must be true",
        ),
        (
            lambda p: p["agent"]["dockerClient"].update({"daemonInAgent": True}),
            "daemonInAgent must be false",
        ),
        (
            lambda p: p["agent"]["env"].update(
                {"DOCKER_HOST": "unix:///tmp/docker.sock"}
            ),
            "DOCKER_HOST must point at dockerSidecar.socket.path",
        ),
        (
            lambda p: p["dockerSidecar"]["workspace"].update(
                {"mountPath": "/mnt/workspace"}
            ),
            "workspace mount paths must match",
        ),
        (
            lambda p: p["agent"]["mounts"].append(
                {"source": "/var/run/docker.sock", "mountPath": "/var/run/docker.sock"}
            ),
            "host Docker socket must not be mounted",
        ),
        (
            lambda p: p["agent"]["env"].update({"OPENAI_API_KEY": "secret"}),
            "must not receive deployment credentials",
        ),
        (
            lambda p: p["policy"].update(
                {"apiContainerWorkloadDockerSocketAccess": True}
            ),
            "API container must not have normal workload Docker socket access",
        ),
        (
            lambda p: p["dockerSidecar"].update({"image": "docker:latest"}),
            "sidecar image must be pinned",
        ),
        (
            lambda p: p["dockerSidecar"]["storage"].update(
                {"daemonScope": "shared"}
            ),
            "Docker daemon scope must be per session",
        ),
        (
            lambda p: p["resources"]["dockerSidecar"].pop("ephemeralStorage"),
            "resources.dockerSidecar.ephemeralStorage",
        ),
        (
            lambda p: p["resources"].pop("nestedContainers"),
            "resources.nestedContainers",
        ),
        (
            lambda p: p["resources"]["nestedContainers"].update({"defaultCpu": "8"}),
            "defaultCpu must not exceed",
        ),
        (
            lambda p: p["cleanup"]["onSessionEnd"].update(
                {"removeDockerGraph": False}
            ),
            "removeDockerGraph must be true",
        ),
        (
            lambda p: p["cleanup"]["onSessionEnd"].update({"stopSidecar": False}),
            "onSessionEnd.stopSidecar must be true",
        ),
        (
            lambda p: p["cleanup"]["onSidecarFailure"].update(
                {"preserveAgentSession": False}
            ),
            "preserveAgentSession must be true",
        ),
        (
            lambda p: p["agent"]["mounts"].append(
                {"source": "/tmp/cache", "mountPath": "/cache"}
            ),
            "arbitrary host path mounts are not allowed",
        ),
        (
            lambda p: p["agent"]["mounts"].append(
                {"source": "./cache", "mountPath": "/cache"}
            ),
            "arbitrary host path mounts are not allowed",
        ),
        (
            lambda p: p["dockerSidecar"]["mounts"].append(
                {"source": "../tmp", "mountPath": "/cache"}
            ),
            "arbitrary host path mounts are not allowed",
        ),
    ],
)
def test_managed_agent_runtime_profile_rejects_unsafe_sidecar_invariants(
    mutate, message
) -> None:
    payload = _valid_docker_sidecar_profile()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        ManagedAgentRuntimeProfile.model_validate(payload)


@pytest.mark.parametrize(
    ("policy_key", "message"),
    [
        ("hostDockerSocket", "hostDockerSocket"),
        ("sharedDaemonAcrossUsers", "sharedDaemonAcrossUsers"),
        (
            "moonmindDeploymentSecretsInSession",
            "moonmindDeploymentSecretsInSession",
        ),
        ("appContainerControlFromSession", "appContainerControlFromSession"),
    ],
)
def test_managed_agent_runtime_profile_forbids_mm694_policy_relaxation(
    policy_key, message
) -> None:
    payload = _valid_docker_sidecar_profile()
    payload["policy"][policy_key] = "allowed"

    with pytest.raises(ValidationError, match=message):
        ManagedAgentRuntimeProfile.model_validate(payload)


def test_moonmind_ops_runtime_contract_is_hidden_from_managed_agents() -> None:
    runtime = MoonMindOpsRuntime.model_validate(
        {
            "kind": "MoonMindOpsRuntime",
            "name": "docker-admin-runtime",
            "purpose": "moonmind-application-operations",
            "backend": "docker",
            "exposedToManagedAgents": False,
            "allowedOperations": [
                "status",
                "deploy",
                "restart",
                "rollback",
                "imageRefresh",
                "logs",
            ],
            "dockerBackend": {
                "hostDockerAccess": True,
                "component": "moonmind-ops-runner",
            },
        }
    )

    assert runtime.exposed_to_managed_agents is False
    assert runtime.allowed_operations == (
        "status",
        "deploy",
        "restart",
        "rollback",
        "imageRefresh",
        "logs",
    )


def test_moonmind_ops_runtime_rejects_managed_agent_exposure() -> None:
    with pytest.raises(ValidationError, match="exposedToManagedAgents"):
        MoonMindOpsRuntime.model_validate({"exposedToManagedAgents": True})


def test_moonmind_ops_runtime_rejects_duplicate_allowed_operations() -> None:
    with pytest.raises(ValidationError, match="duplicate operations"):
        MoonMindOpsRuntime.model_validate(
            {"allowedOperations": ["status", "deploy", "status"]}
        )


def _valid_no_docker_profile() -> dict:
    return {
        "workloadMode": "no-docker",
        "workspace": {
            "volume": "agent_workspaces",
            "mountPath": "/work/agent_jobs",
            "lifecycle": "session",
        },
        "agent": {
            "workspace": {"mountPath": "/work/agent_jobs"},
            "dockerClient": {"enabled": False, "daemonInAgent": False},
            "env": {},
            "mounts": [{"name": "workspace", "mountPath": "/work/agent_jobs"}],
        },
        "policy": {
            "hostDockerSocket": "forbidden",
            "sharedDaemonAcrossUsers": "forbidden",
            "moonmindDeploymentSecretsInSession": "forbidden",
            "appContainerControlFromSession": "forbidden",
        },
    }


def test_no_docker_profile_rejects_enabled_sidecar() -> None:
    payload = _valid_no_docker_profile()
    payload["dockerSidecar"] = {
        "enabled": True,
        "image": "docker:27-dind",
        "socket": {
            "path": "/var/run/moonmind-docker/docker.sock",
            "volumeName": "docker-socket",
        },
        "storage": {
            "volumeName": "docker-graph",
            "mountPath": "/var/lib/docker",
            "lifecycle": "session",
            "daemonScope": "session",
        },
        "workspace": {"mountPath": "/work/agent_jobs"},
    }
    with pytest.raises(
        ValidationError,
        match="dockerSidecar.enabled must be false for no-docker profiles",
    ):
        ManagedAgentRuntimeProfile.model_validate(payload)


def test_no_docker_profile_rejects_docker_host_env() -> None:
    payload = _valid_no_docker_profile()
    payload["agent"]["env"]["DOCKER_HOST"] = "unix:///var/run/moonmind-docker/docker.sock"
    with pytest.raises(
        ValidationError,
        match="agent.env.DOCKER_HOST must not be set for no-docker profiles",
    ):
        ManagedAgentRuntimeProfile.model_validate(payload)


def test_no_docker_profile_allows_disabled_sidecar() -> None:
    payload = _valid_no_docker_profile()
    payload["dockerSidecar"] = {"enabled": False}
    profile = ManagedAgentRuntimeProfile.model_validate(payload)
    assert profile.workload_mode == "no-docker"
    assert profile.docker_sidecar is not None
    assert profile.docker_sidecar.enabled is False


def test_host_docker_socket_check_normalizes_paths() -> None:
    payload = _valid_docker_sidecar_profile()
    payload["agent"]["mounts"].append(
        {"source": "//var/run/docker.sock/", "mountPath": "/host/docker.sock"}
    )
    with pytest.raises(
        ValidationError, match="host Docker socket must not be mounted"
    ):
        ManagedAgentRuntimeProfile.model_validate(payload)


def test_no_docker_profile_cannot_be_raised_by_task_requested_mode() -> None:
    profile = ManagedAgentRuntimeProfile.model_validate(
        {
            "workloadMode": "no-docker",
            "workspace": {
                "volume": "agent_workspaces",
                "mountPath": "/work/agent_jobs",
                "lifecycle": "session",
            },
            "agent": {
                "workspace": {"mountPath": "/work/agent_jobs"},
                "dockerClient": {"enabled": False, "daemonInAgent": False},
                "env": {},
                "mounts": [{"name": "workspace", "mountPath": "/work/agent_jobs"}],
            },
            "policy": {
                "hostDockerSocket": "forbidden",
                "sharedDaemonAcrossUsers": "forbidden",
                "moonmindDeploymentSecretsInSession": "forbidden",
                "appContainerControlFromSession": "forbidden",
            },
        }
    )

    with pytest.raises(ValueError, match="task instructions cannot raise Docker capability"):
        resolve_managed_runtime_workload_mode(
            profile,
            task_requested_workload_mode="docker-sidecar",
        )


def _valid_kubernetes_job_profile(*, supported: bool = True) -> dict:
    return {
        "workloadMode": "kubernetes-job",
        "workspace": {
            "volume": "agent_workspaces",
            "mountPath": "/work/agent_jobs",
            "repoEnv": "MOONMIND_REPO_DIR",
            "lifecycle": "session",
        },
        "agent": {
            "image": "moonmind/managed-agent:2026-05-16",
            "workspace": {"mountPath": "/work/agent_jobs"},
            "dockerClient": {
                "enabled": False,
                "composePlugin": False,
                "daemonInAgent": False,
            },
            "env": {},
            "mounts": [
                {"name": "workspace", "mountPath": "/work/agent_jobs"},
            ],
        },
        "dockerSidecar": {"enabled": False},
        "resources": {
            "session": {"maxRuntimeSeconds": 14400},
            "agent": {"cpu": "2", "memory": "4Gi"},
        },
        "labels": {
            "moonmind.kind": "managed-session",
            "moonmind.workload_mode": "kubernetes-job",
        },
        "policy": {
            "hostDockerSocket": "forbidden",
            "sharedDaemonAcrossUsers": "forbidden",
            "moonmindDeploymentSecretsInSession": "forbidden",
            "appContainerControlFromSession": "forbidden",
            "apiContainerWorkloadDockerSocketAccess": False,
            "kubernetesJobRuntimeSupported": supported,
        },
    }


def test_mm698_kubernetes_job_profile_requires_explicit_deployment_support() -> None:
    with pytest.raises(
        ValidationError,
        match="kubernetes-job requires explicit deployment support",
    ):
        ManagedAgentRuntimeProfile.model_validate(
            _valid_kubernetes_job_profile(supported=False)
        )


def test_mm698_kubernetes_job_profile_is_backend_portable_when_supported() -> None:
    profile = ManagedAgentRuntimeProfile.model_validate(
        _valid_kubernetes_job_profile()
    )

    assert profile.workload_mode == "kubernetes-job"
    assert profile.agent.docker_client.enabled is False
    assert profile.docker_sidecar is not None
    assert profile.docker_sidecar.enabled is False
    assert profile.resources.docker_sidecar is None
    assert profile.resources.nested_containers is None
    assert profile.labels["moonmind.workload_mode"] == "kubernetes-job"


def test_mm698_kubernetes_job_profile_fails_fast_without_runtime_renderer() -> None:
    profile = ManagedAgentRuntimeProfile.model_validate(
        _valid_kubernetes_job_profile()
    )

    with pytest.raises(ValueError, match="cannot be launched until"):
        build_docker_sidecar_launch_plan(profile)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda p: p["agent"]["dockerClient"].update({"enabled": True}),
            "agent.dockerClient.enabled must be false for kubernetes-job",
        ),
        (
            lambda p: p["agent"]["env"].update(
                {"DOCKER_HOST": "unix:///var/run/moonmind-docker/docker.sock"}
            ),
            "DOCKER_HOST must not be set for kubernetes-job",
        ),
        (
            lambda p: p["dockerSidecar"].update({"enabled": True}),
            "dockerSidecar.enabled must be false for kubernetes-job",
        ),
        (
            lambda p: p["resources"].update(
                {
                    "dockerSidecar": {
                        "cpu": "4",
                        "memory": "8Gi",
                        "ephemeralStorage": "40Gi",
                    }
                }
            ),
            "resources.dockerSidecar must be omitted for kubernetes-job",
        ),
        (
            lambda p: p["resources"].update(
                {
                    "nestedContainers": {
                        "defaultCpu": "2",
                        "defaultMemory": "4Gi",
                        "maxContainers": 16,
                    }
                }
            ),
            "resources.nestedContainers must be omitted for kubernetes-job",
        ),
    ],
)
def test_mm698_kubernetes_job_profile_rejects_docker_sidecar_assumptions(
    mutate, message
) -> None:
    payload = _valid_kubernetes_job_profile()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        ManagedAgentRuntimeProfile.model_validate(payload)
