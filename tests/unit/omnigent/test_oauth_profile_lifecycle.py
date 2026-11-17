import asyncio
import base64
import hashlib
import json
import os
import runpy
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.checkpoints import (
    OmnigentCheckpointIdentity,
    SessionResumeDecision,
    materialize_cold_restore_inputs,
    recovery_mode,
    validate_branch_identity,
    validate_cold_restore_target,
    validate_restore_material,
)
from moonmind.omnigent.codex_login import CODEX_LOGIN_SESSION_PROBE
from moonmind.omnigent.effective_capabilities import (
    CAPABILITY_NAMES,
    adapt_provider_capabilities,
    resolve_bridge_row_capabilities,
)
from moonmind.omnigent.execution_profiles import (
    compile_persisted_effective_launch,
    validate_effective_launch_snapshot,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.host_services.legacy_host_containers import (
    LegacyOmnigentHostContainerService,
)
from moonmind.omnigent.host_services.oauth_credential_validation import (
    OmnigentOAuthCredentialValidator,
    _prepend_tools_path,
    run_host_command,
)
from moonmind.omnigent.oauth_hosts import (
    HOST_CLEANUP_CLAIMED_ERROR_CODE,
    HOST_PROFILE_BUSY_ERROR_CODE,
    HostPreflightFailure,
    OmnigentOAuthHostRepository,
    on_demand_binding_policy_ref,
    validate_preflight_result,
)
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.omnigent.repository_sources import normalize_repository_source
from moonmind.omnigent.workspace_intent import (
    authored_github_mutation_required,
    authored_repository_mutation_required,
)
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.provider_profiles.lease_client import (
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.schemas.temporal_models import WorkspaceCheckpointEvidenceModel
from moonmind.security.egress import (
    EGRESS_CONFIG_DIGEST,
    ENFORCER_IMPLEMENTATION,
    OMNIGENT_EGRESS_PROFILE,
    EgressAttestation,
)
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
from tests.unit.omnigent.test_policy_authority import policy_document


def _egress_attestation() -> EgressAttestation:
    return EgressAttestation(
        profileRef=OMNIGENT_EGRESS_PROFILE.ref,
        profileDigest=OMNIGENT_EGRESS_PROFILE.digest,
        enforcerImplementation=ENFORCER_IMPLEMENTATION,
        backendRef="test",
        networkRef=OMNIGENT_EGRESS_PROFILE.network_ref,
        gatewayRef=OMNIGENT_EGRESS_PROFILE.gateway_ref,
        appliedRuleDigest="sha256:" + "a" * 64,
        configDigest=EGRESS_CONFIG_DIGEST,
        gatewayImageDigest="sha256:" + "b" * 64,
        healthResult="healthy",
        validatedAt=datetime(2026, 8, 12, tzinfo=UTC),
        validationResult="passed",
    )


def test_persisted_policy_snapshot_is_complete_launch_authority():
    snapshot = compile_policy_snapshot(
        policy_id="codex-static",
        version=1,
        document=policy_document(),
        validation={"valid": True, "diagnostics": []},
    )

    realized = compile_persisted_effective_launch(
        snapshot, provider_profile_id="profile-1"
    )

    assert realized["hostMode"] == snapshot["boundaries"]["host"]["mode"]
    assert realized["serverImageRef"] == snapshot["boundaries"]["host"]["serverImageRef"]
    assert realized["limits"]["memoryMiB"] == snapshot["boundaries"]["resources"]["memoryMiB"]
    assert realized["networkRef"] == snapshot["boundaries"]["network"]["attachmentRef"]
    assert realized["mountClasses"] == snapshot["boundaries"]["workspace"]["mountClasses"]
    assert realized["boundaries"] == snapshot["boundaries"]
    assert realized["policyAuthority"]["policyDigest"] == snapshot["policyDigest"]
    assert realized["executionProfileDigest"].startswith("sha256:")
    assert realized["agentProfileCapabilities"]["sendMessage"] is True
    assert realized["capabilities"]["sendMessage"] is True
    assert realized["sessionStateCapabilities"]["sendMessage"] is True
    assert realized["snapshotRef"].startswith("omnigent-launch:sha256:")
    validate_effective_launch_snapshot(realized)


def test_compiled_launch_drives_a_live_canonical_capability_decision():
    snapshot = compile_policy_snapshot(
        policy_id="codex-static",
        version=1,
        document=policy_document(),
        validation={"valid": True, "diagnostics": []},
    )
    launch = compile_persisted_effective_launch(
        snapshot, provider_profile_id="profile-1"
    )
    upstream = adapt_provider_capabilities({"sendFollowUp": True})
    row = SimpleNamespace(
        status="active",
        provider_profile_id="profile-1",
        credential_generation=3,
        effective_launch_snapshot_json=launch,
        metadata_={
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 3,
                "upstream": upstream,
                "agentProfile": launch["agentProfileCapabilities"],
                "launchPolicy": launch["capabilities"],
                "state": {
                    "sessionEpoch": 1,
                    "capabilities": launch["sessionStateCapabilities"],
                },
            }
        },
    )
    result = resolve_bridge_row_capabilities(
        row, caller_capabilities=dict.fromkeys(CAPABILITY_NAMES, True)
    )
    assert result.capabilities["sendMessage"] is True
    assert result.disabled_reasons["queueMessage"] == "upstream_unsupported"


@pytest.mark.asyncio
async def test_oauth_runtime_command_failure_keeps_bounded_redacted_diagnostics():
    """A real process failure must retain its reason without leaking a token."""
    with pytest.raises(OmnigentOAuthHostError) as failure:
        await run_host_command(
            sys.executable,
            "-c",
            "import sys; print('initializer missing; token=private-test-token', file=sys.stderr); sys.exit(7)",
        )
    message = str(failure.value)
    assert "exit 7" in message
    assert "initializer missing" in message
    assert "private-test-token" not in message
    assert len(message) <= 512


def test_repository_mutation_requirement_is_explicit_or_implied_by_publish() -> None:
    explicit = AgentExecutionRequest(
        agentKind="external", agentId="omnigent", correlationId="corr-explicit",
        idempotencyKey="explicit",
        parameters={"repositoryMutationRequired": True},
    )
    read_only = AgentExecutionRequest(
        agentKind="external", agentId="omnigent", correlationId="corr-read-only",
        idempotencyKey="read-only",
        parameters={},
    )
    branch_publish_without_gh = AgentExecutionRequest(
        agentKind="external", agentId="omnigent", correlationId="corr-branch",
        idempotencyKey="branch", parameters={"publishMode": "branch"},
    )

    assert authored_repository_mutation_required(explicit)
    assert authored_repository_mutation_required(branch_publish_without_gh)
    assert not authored_repository_mutation_required(read_only)


def _binding() -> OmnigentOAuthHostBinding:
    return OmnigentOAuthHostBinding(
        bindingRef="omnigent-oauth:codex",
        providerProfileId="codex",
        endpointRef="default",
        harness="codex-native",
        credentialMountRef=CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId="codex",
                runtimeId="codex_cli",
                providerId="openai",
                volumeRef="codex_auth_volume",
                credentialGeneration=3,
                ownerUserId="user-1",
            ),
            targetPath="/home/app/.codex",
            runtimeUid=1000,
            runtimeGid=1000,
        ),
        staticHostId="host-1",
    )


def _host_lease() -> OmnigentHostLease:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    return OmnigentHostLease(
        leaseId="host-lease-1",
        providerProfileId="codex",
        providerLeaseId="provider-lease-1",
        bindingRef="omnigent-oauth:codex",
        credentialGeneration=3,
        omnigentHostId="host-1",
        status="ready",
        acquiredAt=now,
        lastHeartbeatAt=now,
        expiresAt=now + timedelta(hours=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("login_session", ["ended", "active", "unknown"])
async def test_credential_mount_preflight_runs_provider_login_without_execution_launch(
    monkeypatch: pytest.MonkeyPatch,
    login_session: str,
) -> None:
    launch = {
        "hostImageRef": "example.invalid/omnigent-host@sha256:" + "a" * 64,
        "providerRuntime": "codex_cli",
        "harness": "codex-native",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
        "limits": {
            "cpuMillis": 1000,
            "memoryMiB": 512,
            "processes": 128,
            "temporaryStorageMiB": 64,
        },
    }
    binding = _binding().model_copy(
        update={
            "static_host_id": None,
            "host_launch_profile_ref": "codex-on-demand@1",
            "effective_launch_snapshot": launch,
        }
    )
    lease = _host_lease().model_copy(
        update={
            "omnigent_host_id": None,
            "status": "starting",
            "effective_launch_snapshot": launch,
        }
    )
    monkeypatch.setattr(
        "moonmind.omnigent.host_services.oauth_credential_validation.validate_effective_launch",
        MagicMock(return_value=launch),
    )
    run = AsyncMock(return_value=(0, "Logged in", ""))
    validator = OmnigentOAuthCredentialValidator(run_command=run)
    validator._containers.container_present = AsyncMock(return_value=False)
    validator._discover_upstream_path = AsyncMock(
        return_value="/usr/local/bin:/usr/bin"
    )
    validator.probe_login_session = AsyncMock(return_value=login_session)

    if login_session == "ended":
        with pytest.raises(OmnigentOAuthHostError) as failure:
            await validator.validate_credential_mount(
                binding=binding, host_lease=lease, effective_launch=launch
            )
        assert failure.value.code == HostPreflightFailure.LOGIN_STATUS_FAILED.value
        assert "ended" in str(failure.value)
        return

    result = await validator.validate_credential_mount(
        binding=binding,
        host_lease=lease,
        effective_launch=launch,
    )

    assert result == {
        "status": "ready",
        "providerProfileId": "codex",
        "runtimeId": "codex_cli",
        "credentialGeneration": 3,
        "loginStatus": "authenticated",
        "validationMode": "credential_only",
    }
    validator.probe_login_session.assert_awaited_once_with(
        binding=binding, effective_launch=launch
    )
    command = run.await_args.args
    assert command[:2] == ("docker", "run")
    assert "none" in command
    assert "type=volume,src=codex_auth_volume,dst=/home/app/.codex,readonly" in command
    assert "/workspaces/run" not in command
    image_index = command.index(launch["hostImageRef"])
    assert command[image_index - 2 : image_index] == ("--entrypoint", "/usr/bin/env")
    assert command[-3:] == ("codex", "login", "status")
    assert command[image_index + 1 : image_index + 3] == ("-u", "OPENAI_API_KEY")
    assert all(index > image_index for index, item in enumerate(command) if item == "-u")


def _codex_auth_json(*, issued_at: float, expires_at: float, secret: str) -> dict:
    def jwt(claims: dict) -> str:
        def segment(value: dict) -> str:
            raw = json.dumps(value).encode()
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        return f"{segment({'alg': 'RS256'})}.{segment(claims)}.{secret}"

    claims = {"iat": int(issued_at), "exp": int(expires_at)}
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": jwt({**claims, "email": "operator@example.com"}),
            "access_token": jwt(claims),
            "refresh_token": f"rt.{secret}",
        },
    }


@pytest.mark.parametrize(
    ("auth", "expected"),
    [
        # OpenAI refreshed an ended session: the token has no lifetime.
        ("zero-lifetime", "ended"),
        # A token that merely aged out may still refresh once Codex starts.
        ("aged-out", "active"),
        ("current", "active"),
        ("api-key", "unknown"),
        ("missing", "unknown"),
        ("malformed", "unknown"),
    ],
)
def test_codex_login_session_probe_prints_only_a_verdict(
    tmp_path, auth: str, expected: str
) -> None:
    secret = "never-print-this-token-material"
    now = time.time()
    documents = {
        "zero-lifetime": _codex_auth_json(
            issued_at=now - 60, expires_at=now - 60, secret=secret
        ),
        "aged-out": _codex_auth_json(
            issued_at=now - 864000, expires_at=now - 60, secret=secret
        ),
        "current": _codex_auth_json(
            issued_at=now - 60, expires_at=now + 864000, secret=secret
        ),
        "api-key": {"auth_mode": "apikey", "OPENAI_API_KEY": secret, "tokens": None},
    }
    if auth == "malformed":
        (tmp_path / "auth.json").write_text("{not json")
    elif auth != "missing":
        (tmp_path / "auth.json").write_text(json.dumps(documents[auth]))

    completed = subprocess.run(
        [sys.executable, "-c", CODEX_LOGIN_SESSION_PROBE, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert completed.stdout == f"{expected}\n"
    assert secret not in completed.stdout + completed.stderr


@pytest.mark.asyncio
async def test_login_session_probe_reads_the_home_without_network_or_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch = {
        "hostImageRef": "example.invalid/omnigent-host@sha256:" + "a" * 64,
        "runtimeUid": 1000,
        "runtimeGid": 1000,
    }
    run = AsyncMock(return_value=(0, "ended\n", ""))
    monkeypatch.setattr(
        "moonmind.omnigent.host_services.oauth_credential_validation.validate_effective_launch",
        MagicMock(return_value=launch),
    )

    verdict = await OmnigentOAuthCredentialValidator(
        run_command=run
    ).probe_login_session(binding=_binding(), effective_launch=launch)

    assert verdict == "ended"
    command = run.await_args.args
    assert command[:3] == ("docker", "run", "--rm")
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert "type=volume,src=codex_auth_volume,dst=/home/app/.codex,readonly" in command
    image_index = command.index(launch["hostImageRef"])
    assert command[image_index - 2 : image_index] == ("--entrypoint", "python3")
    assert command[image_index + 1 :] == (
        "-c",
        CODEX_LOGIN_SESSION_PROBE,
        "/home/app/.codex",
    )
    assert run.await_args.kwargs == {"check": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["exit-nonzero", "unexpected-output", "docker-error", "claude"]
)
async def test_login_session_probe_reports_unknown_without_proof(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    launch = {
        "hostImageRef": "example.invalid/omnigent-host@sha256:" + "a" * 64,
        "runtimeUid": 1000,
        "runtimeGid": 1000,
    }
    run = AsyncMock(
        return_value={
            "exit-nonzero": (125, "", "Unable to find image"),
            "unexpected-output": (0, "ended soon\n", ""),
            "claude": (0, "ended\n", ""),
        }.get(outcome),
        side_effect=(
            OSError("docker unavailable") if outcome == "docker-error" else None
        ),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.host_services.oauth_credential_validation.validate_effective_launch",
        MagicMock(return_value=launch),
    )
    binding = _binding()
    if outcome == "claude":
        mount = binding.credential_mount_ref
        volume = mount.auth_volume_ref.model_copy(update={"runtime_id": "claude_code"})
        binding = binding.model_copy(
            update={
                "credential_mount_ref": mount.model_copy(
                    update={"auth_volume_ref": volume}
                )
            }
        )

    verdict = await OmnigentOAuthCredentialValidator(
        run_command=run
    ).probe_login_session(binding=binding, effective_launch=launch)

    assert verdict == "unknown"
    if outcome == "claude":
        run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exit_code", "expected_code"),
    [
        (1, HostPreflightFailure.LOGIN_STATUS_FAILED),
        (125, HostPreflightFailure.VALIDATION_UNAVAILABLE),
        (127, HostPreflightFailure.VALIDATION_UNAVAILABLE),
        (None, HostPreflightFailure.VALIDATION_UNAVAILABLE),
    ],
)
async def test_credential_mount_preflight_separates_login_failure_from_docker_failure(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int | None,
    expected_code: HostPreflightFailure,
) -> None:
    launch = {
        "hostImageRef": "example.invalid/omnigent-host@sha256:" + "a" * 64,
        "providerRuntime": "codex_cli",
        "harness": "codex-native",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
        "limits": {
            "cpuMillis": 1000,
            "memoryMiB": 512,
            "processes": 128,
            "temporaryStorageMiB": 64,
        },
    }
    binding = _binding().model_copy(update={
        "static_host_id": None,
        "host_launch_profile_ref": "codex-on-demand@1",
        "effective_launch_snapshot": launch,
    })
    lease = _host_lease().model_copy(update={
        "omnigent_host_id": None,
        "status": "starting",
        "effective_launch_snapshot": launch,
    })
    monkeypatch.setattr(
        "moonmind.omnigent.host_services.oauth_credential_validation.validate_effective_launch",
        MagicMock(return_value=launch),
    )
    validator = OmnigentOAuthCredentialValidator(
        run_command=(
            AsyncMock(side_effect=TimeoutError())
            if exit_code is None
            else AsyncMock(return_value=(exit_code, "", ""))
        )
    )
    validator._containers.container_present = AsyncMock(return_value=False)
    validator._discover_upstream_path = AsyncMock(
        return_value="/usr/local/bin:/usr/bin"
    )

    with pytest.raises(OmnigentOAuthHostError) as failure:
        await validator.validate_credential_mount(
            binding=binding,
            host_lease=lease,
            effective_launch=launch,
        )

    assert failure.value.code == expected_code.value


@pytest.mark.asyncio
async def test_retained_static_lease_cleanup_removes_only_lease_owned_resources(
    tmp_path,
) -> None:
    # A lease recorded under a retired static binding is released through the
    # one lease-owned cleanup path; no Compose service is stopped by name.
    present = {"mm-omnigent-host-host-lease-1"}

    async def container_present(name: str) -> bool:
        return name in present

    async def run(*args, **_kwargs):
        if args[:3] == ("docker", "rm", "-f"):
            present.discard(args[-1])
        if args[:3] == ("docker", "volume", "inspect"):
            return 1, "", "absent"
        return 0, "", ""

    docker = AsyncMock(side_effect=run)
    containers = LegacyOmnigentHostContainerService(
        run_command=docker,
        runtime_scripts_root=tmp_path / "workspaces" / ".omnigent-runtime-scripts",
    )
    containers.container_present = container_present
    containers.assert_container_owned = AsyncMock()
    containers.remove_initializer = AsyncMock()

    lease = _host_lease().model_copy(
        update={"container_name": "mm-omnigent-host-host-lease-1"}
    )
    result = await containers.stop_host(binding=_binding(), host_lease=lease)

    assert result["cleanupResult"] == "succeeded"
    assert result["resourceCleanup"]["mode"] == "on_demand_remove"
    containers.assert_container_owned.assert_awaited_once_with(
        container_name="mm-omnigent-host-host-lease-1", lease_id="host-lease-1"
    )
    commands = [call.args for call in docker.await_args_list]
    assert ("docker", "rm", "-f", "mm-omnigent-host-host-lease-1") in commands
    assert not any("compose" in command for command in commands)


def test_claude_profile_materializes_exact_oauth_home_without_secret_data() -> None:
    profile = SimpleNamespace(
        profile_id="claude-oauth",
        runtime_id="claude_code",
        provider_id="anthropic",
        credential_source="oauth_volume",
        runtime_materialization_mode="oauth_home",
        volume_ref="claude_auth_volume",
        volume_mount_path="/home/app/.claude",
        credential_generation=4,
        owner_user_id="user-1",
    )

    mount = OmnigentOAuthHostRepository._mount_from_profile(profile)

    assert mount.target_path == "/home/app/.claude"
    assert mount.auth_volume_ref.runtime_id == "claude_code"
    assert mount.auth_volume_ref.provider_id == "anthropic"
    assert (
        OmnigentOAuthHostRepository._harness_for_mount(mount)
        == "claude-native"
    )
    assert "token" not in str(mount.model_dump()).lower()


def test_claude_preflight_requires_exact_profile_generation_and_harness() -> None:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    binding = OmnigentOAuthHostBinding(
        bindingRef="omnigent-oauth:claude",
        providerProfileId="claude",
        endpointRef="default",
        harness="claude-native",
        credentialMountRef=CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId="claude",
                runtimeId="claude_code",
                providerId="anthropic",
                volumeRef="claude_auth_volume",
                credentialGeneration=4,
                ownerUserId="user-1",
            ),
            targetPath="/home/app/.claude",
            runtimeUid=1000,
            runtimeGid=1000,
        ),
        staticHostId="claude-host-1",
    )
    lease = OmnigentHostLease(
        leaseId="host-lease-claude",
        providerProfileId="claude",
        providerLeaseId="provider-lease-claude",
        bindingRef=binding.binding_ref,
        credentialGeneration=4,
        omnigentHostId="claude-host-1",
        status="ready",
        acquiredAt=now,
        lastHeartbeatAt=now,
        expiresAt=now + timedelta(hours=1),
    )
    result = {
        "providerProfileId": "claude",
        "runtimeId": "claude_code",
        "providerId": "anthropic",
        "credentialGeneration": 4,
        "mountPath": "/home/app/.claude",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
        "harness": "claude-native",
        "competingCredentialsPresent": False,
        "loginStatus": "authenticated",
        "hostId": "claude-host-1",
    }

    assert validate_preflight_result(
        result=result, binding=binding, host_lease=lease
    )["status"] == "ready"
    with pytest.raises(OmnigentOAuthHostError):
        validate_preflight_result(
            result={**result, "harness": "codex-native"},
            binding=binding,
            host_lease=lease,
        )


def _checkpoint() -> OmnigentCheckpointIdentity:
    return OmnigentCheckpointIdentity(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="step-1",
        stepExecutionId="step-execution-1",
        attemptOrdinal=1,
        boundary="after_execution",
        providerProfileId="codex",
        credentialRef="credential://codex",
        credentialGeneration=3,
        providerLeaseRef="provider-lease-1",
        hostBindingRef="omnigent-oauth:codex",
        hostLeaseRef="host-lease-1",
        endpointRef="default",
        omnigentHostId="host-1",
        omnigentSessionId="session-1",
        bridgeSessionId="bridge-1",
        externalStateRef="artifact://external-state",
        externalStateDigest="sha256:" + "0" * 64,
        idempotencyKey="idem-1",
        effectiveLaunchRef="omnigent-launch:sha256:" + "0" * 64,
        executionProfileRef="profile://codex",
        launchPolicyRef="policy://default",
        lastBridgeEventCursor="event-4",
        firstMessageId="message-1",
        firstMessageDigest="sha256:" + "1" * 64,
        workspaceLocator={
            "kind": "sandbox",
            "workspaceId": "workspace-1",
            "relativePath": "repo",
        },
        baselineCommit="abc123",
        headCommit="def456",
        headRef="artifact://head",
        headDigest="sha256:" + "2" * 64,
        workspaceCheckpointRef="artifact://workspace-checkpoint",
        workspaceCheckpointDigest="sha256:" + "3" * 64,
        instructionRefs=["artifact://instructions"],
        contextRefs=["artifact://context"],
        sourceBranch="main",
        publicationState="unpublished",
        capturedAt=datetime(2026, 7, 12, tzinfo=UTC),
        producerVersion="moonmind-test",
        validation={
            "valid": True,
            "liveReattachAvailable": True,
            "workspaceColdRestoreAvailable": True,
            "branchCreationAvailable": True,
        },
    )


def _policy_bound_checkpoint() -> tuple[OmnigentCheckpointIdentity, dict]:
    snapshot = compile_policy_snapshot(
        policy_id="codex-static",
        version=1,
        document=policy_document(),
        validation={"valid": True, "diagnostics": []},
    )
    return _checkpoint().model_copy(
        update={
            "policy_id": snapshot["policyId"],
            "policy_version": snapshot["policyVersion"],
            "policy_ref": snapshot["policyRef"],
            "policy_digest": snapshot["policyDigest"],
            "policy_snapshot_ref": snapshot["snapshotRef"],
            "policy_validation": snapshot["validation"],
        }
    ), snapshot


def test_legacy_checkpoint_without_effective_launch_ref_remains_loadable() -> None:
    payload = _checkpoint().model_dump(by_alias=True, mode="json")
    payload.pop("effectiveLaunchRef")

    checkpoint = OmnigentCheckpointIdentity.model_validate(payload)

    assert checkpoint.effective_launch_ref is None


def test_checkpoint_rejects_partial_policy_authority_evidence() -> None:
    payload = _checkpoint().model_dump(by_alias=True, mode="json")
    payload["policyId"] = "restricted"

    with pytest.raises(ValidationError, match="policy authority evidence is incomplete"):
        OmnigentCheckpointIdentity.model_validate(payload)


def test_checkpoint_rejects_partial_plan_and_runtime_binding_authority() -> None:
    payload = _checkpoint().model_dump(by_alias=True, mode="json")
    payload["executionPlanRef"] = "omnigent-execution-plan:sha256:" + "a" * 64

    with pytest.raises(
        ValidationError,
        match="execution-plan/runtime-binding authority is incomplete",
    ):
        OmnigentCheckpointIdentity.model_validate(payload)


def test_restore_rejects_checkpoint_bound_to_a_different_policy_snapshot() -> None:
    snapshot = compile_policy_snapshot(
        policy_id="restricted",
        version=3,
        document={
            "schemaVersion": 1,
            "endpoint": {"ref": "default", "bridgeModes": ["embedded"]},
            "execution": {"profileRef": "omnigent-codex@1", "harness": "codex-native", "agentIdentities": ["codex-native-ui"]},
            "host": {"mode": "static_compose", "backendRef": "compose", "architectures": ["amd64"], "serverImageRef": "image@sha256:" + "1" * 64, "hostImageRef": "host@sha256:" + "2" * 64},
            "resources": {"cpuMillis": 1000, "memoryMiB": 1024, "processes": 64, "timeoutSeconds": 60, "temporaryStorageMiB": 64, "concurrency": 1},
            "network": {"attachmentRef": "network", "egressProfileRef": "egress"},
            "workspace": {"allowedClasses": ["workflow"], "repositoryMutation": True, "mountClasses": ["workspace"], "runtimeUid": 1000, "runtimeGid": 1000},
            "providerProfile": {"compatibleProviders": ["codex"], "queueWhenBusy": True},
            "session": {"create": True, "firstMessage": "required", "continuation": True, "interruption": True, "cancellation": True, "cleanup": "drain"},
            "capture": {"required": True, "artifactClasses": ["events"], "maxLogBytes": 1000, "redaction": "required"},
            "checkpoint": {"capture": True, "resume": True, "branch": True, "publication": "approval", "promotion": "verified"},
            "remediation": {"actions": ["retry"], "riskTiers": {"retry": "low"}, "locks": True, "maxActions": 1, "autonomous": False},
            "rag": {"initialScope": "workflow", "followupScope": "session", "collectionRefs": ["default"], "tokenBudget": 100, "fallback": "deny", "credentialRef": "retrieval"},
            "approvals": {"actions": {}},
            "retention": {"days": 1, "deletion": "after-expiry"},
            "rollout": {"cohort": "default", "gate": "ready", "diagnostics": True},
        },
        validation={"valid": True},
    )
    payload = _checkpoint().model_dump(by_alias=True, mode="json")
    payload.update(
        policyId="restricted", policyVersion=3, policyRef="restricted@3",
        policyDigest="sha256:" + "0" * 64, policySnapshotRef=snapshot["snapshotRef"],
        policyValidation=snapshot["validation"],
    )
    checkpoint = OmnigentCheckpointIdentity.model_validate(payload)

    validation = validate_restore_material(
        checkpoint,
        workflow_id="workflow-1", run_id="run-1", logical_step_id="step-1",
        step_execution_id="step-execution-1", attempt_ordinal=1,
        boundary="after_execution", provider_profile_id="codex",
        credential_generation=3, repository_baseline="abc123",
        repository_head="def456", artifact_reader=lambda _ref: b"missing",
        policy_snapshot=snapshot,
    )
    assert "policy_authority_mismatch" in validation.reasons


def test_restore_rejects_missing_run_bound_policy_snapshot() -> None:
    checkpoint = _checkpoint()

    validation = validate_restore_material(
        checkpoint,
        workflow_id="workflow-1", run_id="run-1", logical_step_id="step-1",
        step_execution_id="step-execution-1", attempt_ordinal=1,
        boundary="after_execution", provider_profile_id="codex",
        credential_generation=3, repository_baseline="abc123",
        repository_head="def456", artifact_reader=lambda _ref: b"missing",
        policy_snapshot=None,
    )

    assert validation.valid is False
    assert "policy_authority_missing" in validation.reasons


def test_complete_checkpoint_validates_and_compiles_cold_restore_material() -> None:
    payloads = {
        "artifact://external-state": b"external",
        "artifact://head": b"head",
        "artifact://workspace-checkpoint": b"workspace",
        "artifact://instructions": b"instructions",
        "artifact://context": b"context",
    }
    checkpoint, policy_snapshot = _policy_bound_checkpoint()
    checkpoint = checkpoint.model_copy(
        update={
            "external_state_digest": "sha256:" + hashlib.sha256(b"external").hexdigest(),
            "head_digest": "sha256:" + hashlib.sha256(b"head").hexdigest(),
            "workspace_checkpoint_digest": (
                "sha256:" + hashlib.sha256(b"workspace").hexdigest()
            ),
        }
    )

    validation = validate_restore_material(
        checkpoint,
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="step-1",
        step_execution_id="step-execution-1",
        attempt_ordinal=1,
        boundary="after_execution",
        provider_profile_id="codex",
        credential_generation=3,
        repository_baseline="abc123",
        repository_head="def456",
        artifact_reader=payloads.__getitem__,
        policy_snapshot=policy_snapshot,
    )
    material = materialize_cold_restore_inputs(checkpoint, validation)

    assert validation.workspace_cold_restore_available is True
    assert validation.live_reattach_available is True
    assert material.external_state_ref == "artifact://external-state"
    assert material.external_state_digest == checkpoint.external_state_digest
    assert material.head_digest == checkpoint.head_digest
    assert material.immutable_input_refs == [
        "artifact://instructions",
        "artifact://context",
    ]


def test_restore_validation_reports_bounded_independent_denial_reasons() -> None:
    checkpoint = _checkpoint()

    validation = validate_restore_material(
        checkpoint,
        workflow_id="other-workflow",
        run_id="run-1",
        logical_step_id="step-1",
        step_execution_id="other-step-execution",
        attempt_ordinal=2,
        boundary="before_execution",
        provider_profile_id="other-profile",
        credential_generation=4,
        repository_baseline="different",
        repository_head="different",
        artifact_reader=lambda _ref: b"wrong",
        policy_snapshot=None,
    )

    assert validation.valid is False
    assert validation.live_reattach_available is False
    assert validation.workspace_cold_restore_available is False
    assert validation.branch_creation_available is False
    assert {
        "lineage_mismatch",
        "step_execution_lineage_mismatch",
        "repository_baseline_mismatch",
        "repository_head_mismatch",
        "provider_profile_mismatch",
        "credential_generation_mismatch",
        "artifact_digest_mismatch",
    }.issubset(validation.reasons)


def test_deterministic_owner_reuses_activity_retry_identity() -> None:
    kwargs = {
        "profile_id": "codex",
        "purpose": CredentialLeasePurpose.EXECUTION_OMNIGENT,
        "workflow_id": "wf-1",
        "step_execution_id": "step-1",
        "idempotency_key": "idem-1",
    }
    owner_id = deterministic_lease_owner_id(**kwargs)
    assert owner_id == deterministic_lease_owner_id(**kwargs)
    assert owner_id.startswith("profile-lease:execution_omnigent:")
    assert all(value not in owner_id for value in ("codex", "wf-1", "idem-1"))
    assert CredentialLeasePurpose.OAUTH_RECONNECT.is_maintenance is True
    assert CredentialLeasePurpose.EXECUTION_DIRECT.is_maintenance is False


@pytest.mark.asyncio
async def test_activity_lease_client_marks_deterministic_owner_as_non_workflow() -> (
    None
):
    class Adapter:
        def __init__(self) -> None:
            self.payload = None
            self.update_name = None

        async def get_client(self):
            return self

        async def start_workflow(self, *_args, **_kwargs):
            return None

        async def update_workflow(self, _workflow_id, update_name, payload):
            self.update_name = update_name
            self.payload = payload
            return {
                "profile_id": "codex",
                "lease_id": payload["requester_workflow_id"],
            }

    adapter = Adapter()
    lease = await ProviderProfileLeaseClient(adapter).acquire_execution_lease(
        runtime_id="codex_cli",
        profile_id="codex",
        owner_id="profile-lease:execution_omnigent:retry",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        metadata={"ownerIsWorkflow": True, "workflowId": "workflow-1"},
    )
    assert lease.profile_id == "codex"
    assert adapter.update_name == "AcquireSlotV2"
    assert adapter.payload["metadata"]["ownerIsWorkflow"] is False
    assert adapter.payload["metadata"]["workflowId"] == "workflow-1"


@pytest.mark.asyncio
async def test_activity_lease_client_reopens_manager_after_completed_update() -> None:
    from temporalio.client import WorkflowUpdateFailedError
    from temporalio.exceptions import ApplicationError

    class Adapter:
        def __init__(self) -> None:
            self.start_count = 0
            self.update_count = 0

        async def get_client(self):
            return self

        async def start_workflow(self, *_args, **_kwargs):
            self.start_count += 1

        async def update_workflow(self, _workflow_id, _update_name, payload):
            self.update_count += 1
            if self.update_count == 1:
                raise WorkflowUpdateFailedError(
                    ApplicationError(
                        "Workflow completed before the Update completed.",
                        type="AcceptedUpdateCompletedWorkflow",
                        non_retryable=True,
                    )
                )
            return {
                "profile_id": "codex",
                "lease_id": payload["requester_workflow_id"],
            }

    adapter = Adapter()
    lease = await ProviderProfileLeaseClient(adapter).acquire_execution_lease(
        runtime_id="codex_cli",
        profile_id="codex",
        owner_id="profile-lease:execution_omnigent:manager-race",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )

    assert lease.lease_id == "profile-lease:execution_omnigent:manager-race"
    assert adapter.start_count == 2
    assert adapter.update_count == 2


@pytest.mark.asyncio
async def test_activity_lease_client_preserves_delegating_workflow_owner() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.payload = None

        async def get_client(self):
            return self

        async def start_workflow(self, *_args, **_kwargs):
            return None

        async def update_workflow(self, _workflow_id, _update_name, payload):
            self.payload = payload
            return {
                "profile_id": "codex",
                "lease_id": payload["requester_workflow_id"],
            }

    adapter = Adapter()
    await ProviderProfileLeaseClient(adapter).acquire_maintenance_lease(
        runtime_id="codex_cli",
        profile_id="codex",
        owner_id="oauth-session:oas-1",
        purpose=CredentialLeasePurpose.OAUTH_RECONNECT,
        metadata={"workflowId": "oauth-session:oas-1"},
        owner_is_workflow=True,
    )

    assert adapter.payload["metadata"] == {
        "workflowId": "oauth-session:oas-1",
        "ownerIsWorkflow": True,
    }


def _lease_runtime_scripts(root: Path, lease_id: str) -> Path:
    # The per-lease runtime scripts directory that host reclamation removes.
    digest = hashlib.sha256(lease_id.encode("utf-8")).hexdigest()[:24]
    return root / ".omnigent-runtime-scripts" / digest


@pytest.mark.asyncio
@pytest.mark.parametrize("present", [True, False])
async def test_host_inventory_distinguishes_stopped_container_from_absence(
    present,
) -> None:
    async def run(*argv, **_kwargs):
        assert argv[:3] == ("docker", "inspect", "--format")
        if argv[3] == "{{.State.Running}}":
            return (0, "false\n", "") if present else (1, "", "not found")
        assert argv[3] == "{{.Id}}"
        return (0, "stopped-container-id\n", "") if present else (1, "", "not found")

    containers = LegacyOmnigentHostContainerService(run_command=run)
    assert await containers.container_exists("mm-host-lease-1") is False
    assert await containers.container_present("mm-host-lease-1") is present


@pytest.mark.asyncio
@pytest.mark.parametrize("container_owner", ["host-lease-1", "foreign-lease"])
async def test_stop_host_checks_stopped_container_ownership_before_removal(
    tmp_path,
    container_owner,
) -> None:
    binding = _binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    lease = _host_lease().model_copy(update={"container_name": "mm-host-lease-1"})
    runtime_scripts = _lease_runtime_scripts(tmp_path, lease.lease_id)
    runtime_scripts.mkdir(parents=True)
    saved_control = runtime_scripts / "execution-fanout"
    saved_control.write_text("expired-test-capability\n", encoding="utf-8")
    commands = []
    container_present = True

    async def run(*argv, **_kwargs):
        nonlocal container_present
        commands.append(argv)
        if argv[:2] == ("docker", "inspect"):
            if not container_present:
                return (1, "", "not found")
            if argv[3] == "{{.State.Running}}":
                return (0, "false\n", "")
            if argv[3] == "{{.Id}}":
                return (0, "stopped-container-id\n", "")
            assert argv[3] == '{{index .Config.Labels "moonmind.host_lease_id"}}'
            return (0, container_owner + "\n", "")
        if argv[:3] == ("docker", "rm", "-f"):
            container_present = False
        if argv[:3] == ("docker", "volume", "inspect"):
            return (1, "", "not found")
        return (0, "", "")

    containers = LegacyOmnigentHostContainerService(
        run_command=run, runtime_scripts_root=runtime_scripts.parent
    )
    if container_owner != lease.lease_id:
        with pytest.raises(OmnigentOAuthHostError) as failure:
            await containers.stop_host(binding=binding, host_lease=lease)
        assert failure.value.code == "OMNIGENT_HOST_OWNERSHIP_MISMATCH"
        assert container_present is True
        assert saved_control.is_file()
        assert all(command[:2] != ("docker", "stop") for command in commands)
        assert all(command[:2] != ("docker", "rm") for command in commands)
        assert all(command[:3] != ("docker", "volume", "rm") for command in commands)
    else:
        result = await containers.stop_host(binding=binding, host_lease=lease)
        assert result["cleanupResult"] == "succeeded"
        assert container_present is False
        assert not runtime_scripts.exists()
        ownership_check = (
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "moonmind.host_lease_id"}}',
            lease.container_name,
        )
        assert commands.index(ownership_check) < commands.index(
            ("docker", "rm", "-f", lease.container_name)
        )
        assert all(
            binding.credential_mount_ref.auth_volume_ref.volume_ref not in command
            for command in commands
        )


@pytest.mark.asyncio
async def test_stop_host_cleans_volumes_when_container_is_absent(tmp_path) -> None:
    run = AsyncMock(
        side_effect=lambda *args, **_kwargs: (
            (0, "", "") if args[:2] == ("docker", "ps") else (1, "", "not found")
        )
    )
    binding = _binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    lease = _host_lease().model_copy(update={"container_name": "mm-host-lease-1"})
    runtime_scripts = _lease_runtime_scripts(tmp_path, lease.lease_id)
    containers = LegacyOmnigentHostContainerService(
        run_command=run, runtime_scripts_root=runtime_scripts.parent
    )
    capability_dir = runtime_scripts / "capabilities"
    capability_dir.mkdir(parents=True)
    (capability_dir / "execution-fanout").write_text(
        "expired-test-capability\n",
        encoding="utf-8",
    )

    result = await containers.stop_host(binding=binding, host_lease=lease)

    commands = [call.args for call in run.await_args_list]
    assert commands[0][:3] == ("docker", "inspect", "--format")
    assert ("docker", "volume", "rm", "-f", "mm-host-lease-1-artifacts") in commands
    assert ("docker", "volume", "rm", "-f", "mm-host-lease-1-cache") in commands
    assert result["resourceCleanup"]["runtimeCapabilityFilesRemoved"] is True
    assert not runtime_scripts.exists()


def test_tools_path_prepend_is_idempotent_and_preserves_upstream_path() -> None:
    upstream = "/custom/bin:/usr/local/bin:/usr/bin:/bin"
    expected = "/opt/moonmind-tools/bin:/custom/bin:/usr/local/bin:/usr/bin:/bin"

    assert _prepend_tools_path(upstream) == expected
    assert _prepend_tools_path(expected) == expected


def test_github_write_probe_uses_publish_or_skill_side_effect() -> None:
    base = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        idempotencyKey="idem",
        correlationId="corr",
        parameters={"requiredCapabilities": ["gh"], "publishMode": "none"},
    )

    assert not authored_github_mutation_required(base)
    assert authored_github_mutation_required(
        base.model_copy(update={"parameters": {**base.parameters, "publishMode": "pr"}})
    )
    assert authored_github_mutation_required(
        base.model_copy(
            update={
                "parameters": {
                    **base.parameters,
                    "skill": {"sideEffect": {"kind": "merge_pull_request"}},
                }
            }
        )
    )


@pytest.mark.asyncio
async def test_tools_path_discovery_falls_back_when_image_is_not_local() -> None:
    run = AsyncMock(return_value=(1, "", "No such image"))
    validator = OmnigentOAuthCredentialValidator(run_command=run)

    image_ref = "registry.example/snapshot-host@sha256:" + "2" * 64
    assert await validator._discover_upstream_path(image_ref) == (
        "/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
    )
    assert run.await_args.args[-1] == image_ref
    assert run.await_args.kwargs["check"] is False


def test_exact_host_preflight_rejects_generation_mismatch() -> None:
    result = {
        "providerProfileId": "codex",
        "runtimeId": "codex_cli",
        "providerId": "openai",
        "credentialGeneration": 4,
        "mountPath": "/home/app/.codex",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
        "loginStatus": "authenticated",
        "hostId": "host-1",
        "harness": "codex-native",
        "competingCredentialsPresent": False,
    }
    with pytest.raises(OmnigentOAuthHostError) as exc_info:
        validate_preflight_result(
            result=result, binding=_binding(), host_lease=_host_lease()
        )
    assert exc_info.value.code == "CODEX_OAUTH_GENERATION_STALE"


def test_image_owned_tools_layout_contract() -> None:
    # MoonLadderStudios/MoonMind#4558: the shell initializer is retired. The
    # image build owns installation; this test pins the layout contract host
    # launch relies on (paths probed by _exec_tools_check and the login
    # shell), with build behavior covered by test_install_moonmind_tools.py.
    repo = Path(__file__).resolve().parents[3]
    dockerfile = (repo / "services/omnigent/moonmind-host/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "/opt/moonmind-tools/bin/gh" in dockerfile
    assert "/opt/moonmind-tools/bin/moonmind" in dockerfile
    assert "/etc/profile.d/moonmind-tools.sh" in dockerfile
    assert "/opt/moonmind-tools/manifest.json" in dockerfile
    assert "services/omnigent/scripts/init-mounted-tools.sh" not in dockerfile
    assert not (repo / "services/omnigent/scripts/init-mounted-tools.sh").exists()


def test_stale_host_daemon_cleanup_removes_only_runtime_markers(tmp_path) -> None:
    scripts = Path(__file__).resolve().parents[3] / "services" / "omnigent" / "scripts"
    state_root = tmp_path / ".omnigent"
    daemon_root = state_root / "daemons"
    daemon_root.mkdir(parents=True)
    stale_marker = daemon_root / "host.json"
    preserved_file = daemon_root / "README"
    config = state_root / "config.yaml"
    stale_marker.write_text('{"pid":1}\n', encoding="utf-8")
    preserved_file.write_text("operator note\n", encoding="utf-8")
    config.write_text("host_id: fixture\n", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(scripts / "clear-stale-host-daemons.sh")],
        env={**os.environ, "OMNIGENT_STATE_PATH": str(state_root)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not stale_marker.exists()
    assert preserved_file.read_text(encoding="utf-8") == "operator note\n"
    assert config.read_text(encoding="utf-8") == "host_id: fixture\n"


def test_host_launchers_wait_for_projection_and_clear_stale_state_before_starting() -> None:
    scripts = Path(__file__).resolve().parents[3] / "services" / "omnigent" / "scripts"

    # One shared entrypoint owns the host lifecycle; the launcher selects the
    # trusted runtime pack through its environment.
    source = (scripts / "start-omnigent-host.sh").read_text(encoding="utf-8")
    assert "until /opt/moonmind/check-runner-projections.sh; do" in source
    assert "waiting for a resolved Skill projection" in source
    assert source.index("/opt/moonmind/clear-stale-host-daemons.sh") < source.index(
        "exec omnigent host"
    )


def test_omnigent_projects_portable_pr_resolver_semantics_without_copying_them() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    helper = runpy.run_path(
        str(repo_root / ".agents/skills/pr-resolver/bin/pr_resolve_snapshot.py")
    )
    actionable, reason = helper["_classify_comment_actionability"](
        {"type": "review_comment", "body": "Please fix this", "user": "reviewer"}
    )
    assert (actionable, reason) == (True, "actionable")
    adapter_source = (
        repo_root / "moonmind/omnigent/host_services/runtime_scripts.py"
    ).read_text()
    assert "_classify_comment_actionability" not in adapter_source
    assert "MOONMIND_ACTIVE_SKILLS_DIR" in adapter_source


def test_checkpoint_live_reattach_requires_every_original_authority() -> None:
    checkpoint = _checkpoint()
    assert (
        recovery_mode(
            checkpoint,
            provider_lease={"active": True, "leaseId": "provider-lease-1"},
            host_lease={
                "status": "assigned",
                "leaseId": "host-lease-1",
                "credentialGeneration": 3,
            },
            host_registered=True,
            session_valid=True,
            first_message_consistent=True,
        )
        == SessionResumeDecision.LIVE_REATTACH
    )
    assert (
        recovery_mode(
            checkpoint,
            provider_lease={"active": True, "leaseId": "provider-lease-1"},
            host_lease={
                "status": "assigned",
                "leaseId": "host-lease-1",
                "credentialGeneration": 4,
            },
            host_registered=True,
            session_valid=True,
            first_message_consistent=True,
        )
        == SessionResumeDecision.COLD_RESTORE
    )


def test_cold_restore_and_branch_preserve_profile_and_exclusive_identity() -> None:
    checkpoint = _checkpoint()
    validate_cold_restore_target(
        checkpoint, provider_profile_id="codex", credential_generation=3
    )
    validate_branch_identity(
        checkpoint, new_host_lease_ref="host-lease-2", new_session_id="session-2"
    )
    with pytest.raises(ValueError, match="new host lease"):
        validate_branch_identity(
            checkpoint,
            new_host_lease_ref="host-lease-1",
            new_session_id="session-2",
        )


def test_checkpoint_rejects_raw_credentials_and_accepts_safe_identity_refs() -> None:
    evidence = WorkspaceCheckpointEvidenceModel(
        kind="external_state_ref",
        externalStateRef="artifact-external-state",
        providerProfileId="codex",
        credentialGeneration=3,
        providerLeaseRef="provider-lease-1",
        hostBindingRef="omnigent-oauth:codex",
        hostLeaseRef="host-lease-1",
        endpointRef="default",
        omnigentHostId="host-1",
        omnigentSessionId="session-1",
        bridgeSessionId="bridge-1",
        idempotencyKey="idem-1",
    )
    assert evidence.credential_generation == 3
    with pytest.raises(ValidationError, match="raw credentials"):
        WorkspaceCheckpointEvidenceModel(
            kind="external_state_ref",
            externalStateRef="bearer access-token-value",
            providerProfileId="codex",
            credentialGeneration=3,
            hostBindingRef="omnigent-oauth:codex",
            endpointRef="default",
            bridgeSessionId="bridge-1",
            idempotencyKey="idem-1",
        )


@pytest.mark.asyncio
async def test_host_repository_creates_idempotent_binding_and_lease(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hosts.db")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=3,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                )
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(factory)
        binding = await repository.create_or_update_static_binding(
            profile_id="codex",
            endpoint_ref="default",
            host_launch_profile_ref="codex-on-demand@1",
        )
        first = await repository.create_or_get_host_lease(
            binding=binding,
            provider_lease_id="provider-lease-1",
            holder_workflow_id="workflow-1",
            agent_run_id="step-1",
            idempotency_key="idem-1",
        )
        second = await repository.create_or_get_host_lease(
            binding=binding,
            provider_lease_id="provider-lease-1",
            holder_workflow_id="workflow-1",
            agent_run_id="step-1",
            idempotency_key="idem-1",
        )
        assert first.lease_id == second.lease_id
        with pytest.raises(OmnigentOAuthHostError) as busy:
            await repository.create_or_get_host_lease(
                binding=binding,
                provider_lease_id="provider-lease-rerun",
                holder_workflow_id="workflow-rerun",
                agent_run_id="step-rerun",
                idempotency_key="idem-rerun",
            )
        assert busy.value.code == HOST_PROFILE_BUSY_ERROR_CODE
        starting = await repository.transition_host_lease(
            first.lease_id,
            expected_status="allocating",
            new_status="starting",
        )
        assert starting.status == "starting"
        claimed = await repository.claim_host_lease_cleanup(
            first.lease_id,
            expected_status="starting",
            expected_last_heartbeat_at=starting.last_heartbeat_at,
        )
        assert claimed is not None
        assert claimed.status == "draining"
        assert 89 <= (
            claimed.expires_at - claimed.last_heartbeat_at
        ).total_seconds() <= 90
        assert (
            await repository.claim_host_lease_cleanup(
                first.lease_id,
                expected_status="starting",
                expected_last_heartbeat_at=starting.last_heartbeat_at,
            )
            is None
        )
        with pytest.raises(OmnigentOAuthHostError) as heartbeat_error:
            await repository.heartbeat_host_lease(first.lease_id)
        assert heartbeat_error.value.code == HOST_CLEANUP_CLAIMED_ERROR_CODE

        await repository.mark_host_lease_stopped(first.lease_id)
        restarted = await repository.restart_host_lease(first.lease_id)
        heartbeat_result, cleanup_result = await asyncio.gather(
            repository.heartbeat_host_lease(first.lease_id),
            repository.claim_host_lease_cleanup(
                first.lease_id,
                expected_status="starting",
                expected_last_heartbeat_at=restarted.last_heartbeat_at,
            ),
            return_exceptions=True,
        )
        heartbeat_won = not isinstance(heartbeat_result, BaseException)
        cleanup_won = (
            not isinstance(cleanup_result, BaseException)
            and cleanup_result is not None
        )
        assert heartbeat_won is not cleanup_won
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_idle_binding_update_refuses_a_profile_with_an_active_lease(
    tmp_path,
) -> None:
    """The idle check and launch replacement share one binding-row transaction.

    Lease creation serializes on the same binding row, so a lease acquired on
    the old launch can never be left behind by a replacement that observed the
    profile idle before that lease committed.
    """

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hosts.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=3,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                )
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(factory)

        def launch(name: str) -> dict:
            return {"snapshotRef": f"omnigent-launch:sha256:{name}"}

        await repository.create_or_update_static_binding(
            profile_id="codex",
            endpoint_ref="default",
            host_launch_profile_ref="codex-on-demand@16",
            execution_profile_ref="omnigent-codex@1",
            launch_policy_ref="codex-on-demand@16",
            effective_launch_snapshot=launch("old"),
        )
        installed = await repository.create_or_update_static_binding(
            profile_id="codex",
            endpoint_ref="default",
            host_launch_profile_ref="codex-on-demand@16",
            execution_profile_ref="omnigent-codex@1",
            launch_policy_ref="codex-on-demand@16",
            effective_launch_snapshot=launch("installed"),
            require_idle=True,
        )
        assert installed.effective_launch_snapshot == launch("installed")
        await repository.create_or_get_host_lease(
            binding=installed,
            provider_lease_id="provider-lease-1",
            holder_workflow_id="workflow-1",
            agent_run_id="step-1",
            idempotency_key="idem-1",
        )

        with pytest.raises(OmnigentOAuthHostError) as leased:
            await repository.create_or_update_static_binding(
                profile_id="codex",
                endpoint_ref="default",
                host_launch_profile_ref="codex-on-demand@16",
                execution_profile_ref="omnigent-codex@1",
                launch_policy_ref="codex-on-demand@16",
                effective_launch_snapshot=launch("newer"),
                require_idle=True,
            )
        assert leased.value.code == "OMNIGENT_HOST_BINDING_LEASED"
        kept = await repository.get_binding_for_profile("codex")
        assert kept is not None
        assert kept.effective_launch_snapshot == launch("installed")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unconfirmed_host_stop_fences_successor_until_janitor_confirms(
    tmp_path,
) -> None:
    """An unacknowledged stop resumes reconciliation, not parallel compute."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fence.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=3,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                )
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(factory)
        binding = await repository.create_or_update_static_binding(
            profile_id="codex",
            endpoint_ref="default",
            host_launch_profile_ref="codex-on-demand@1",
        )

        async def admit(attempt: int):
            return await repository.create_or_get_host_lease(
                binding=binding,
                provider_lease_id=f"provider-lease-{attempt}",
                holder_workflow_id="workflow-1",
                agent_run_id=f"step-1:execution:{attempt}",
                idempotency_key=f"idem-step-1-execution-{attempt}",
            )

        predecessor = await admit(1)
        with pytest.raises(OmnigentOAuthHostError, match="claimed host cleanup"):
            await repository.record_unconfirmed_host_cleanup(
                predecessor.lease_id, code="early", summary="not claimed"
            )
        claimed = await repository.claim_host_lease_cleanup(
            predecessor.lease_id,
            expected_status=predecessor.status,
            expected_last_heartbeat_at=predecessor.last_heartbeat_at,
        )
        assert claimed is not None
        # The Docker engine never acknowledged the stop of the lost host.
        unconfirmed = await repository.record_unconfirmed_host_cleanup(
            predecessor.lease_id,
            code="OmnigentOAuthHostError",
            summary="docker stop token=private-test-token timed out",
        )
        assert unconfirmed.status == "draining"
        async with factory() as session:
            row = await session.get(OmnigentOAuthHostLeaseRecord, predecessor.lease_id)
            assert row.error_code == "OmnigentOAuthHostError"
            assert "private-test-token" not in str(row.error_summary)

        with pytest.raises(OmnigentOAuthHostError) as busy:
            await admit(2)
        assert busy.value.code == HOST_PROFILE_BUSY_ERROR_CODE
        # The stale owner cannot revive it, and the janitor resumes the stop
        # once the cleanup claim lapses.
        with pytest.raises(OmnigentOAuthHostError) as revived:
            await repository.heartbeat_host_lease(predecessor.lease_id)
        assert revived.value.code == HOST_CLEANUP_CLAIMED_ERROR_CODE
        expired = await repository.list_expired_host_leases(
            now=unconfirmed.expires_at + timedelta(seconds=1)
        )
        assert [lease.lease_id for lease in expired] == [predecessor.lease_id]

        await repository.mark_host_lease_stopped(predecessor.lease_id)
        successor = await admit(2)
        assert successor.lease_id != predecessor.lease_id
        assert successor.status == "allocating"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_host_lease_rejects_binding_changed_before_serialized_acquisition(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/host-lock.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=3,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                )
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(factory)
        stale_binding = await repository.create_or_update_static_binding(
            profile_id="codex",
            endpoint_ref="default",
            host_launch_profile_ref="codex-on-demand@1",
            execution_profile_ref="omnigent-codex@1",
            launch_policy_ref="codex-on-demand@1",
            effective_launch_snapshot={"snapshotRef": "old"},
        )
        async with factory() as session:
            record = await session.get(
                OmnigentOAuthHostBindingRecord,
                stale_binding.binding_ref,
            )
            record.launch_policy_ref = "codex-on-demand@2"
            record.effective_launch_snapshot_json = None
            await session.commit()

        with pytest.raises(OmnigentOAuthHostError) as conflict:
            await repository.create_or_get_host_lease(
                binding=stale_binding,
                provider_lease_id="provider-lease-1",
                holder_workflow_id="workflow-1",
                agent_run_id="step-1",
                idempotency_key="idem-1",
            )

        assert conflict.value.code == "OMNIGENT_LAUNCH_SNAPSHOT_CONFLICT"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_commit_publication_requires_exact_remote_base_head() -> None:
    """No-commit evidence names only the remote base whose SHA was verified."""

    head_sha = "a" * 40
    run_command = AsyncMock(
        side_effect=[
            SimpleNamespace(stdout=f"{head_sha}\n", returncode=0),
            SimpleNamespace(
                stdout=f"{head_sha}\trefs/heads/main\n", returncode=0
            ),
        ]
    )

    evidence = (
        await OmnigentWorkspacePublicationService._verified_no_commit_publication(
            run_command=run_command,
            base_branch="main",
        )
    )

    assert evidence == {
        "push_status": "no_commits",
        "push_branch": "main",
        "push_base_branch": "main",
        "push_head_sha": head_sha,
        "push_commit_count": 0,
        "remote_verified": True,
    }


@pytest.mark.asyncio
async def test_no_commit_publication_rejects_remote_head_mismatch() -> None:
    run_command = AsyncMock(
        side_effect=[
            SimpleNamespace(stdout=f"{'a' * 40}\n", returncode=0),
            SimpleNamespace(
                stdout=f"{'b' * 40}\trefs/heads/main\n", returncode=0
            ),
        ]
    )

    with pytest.raises(HarnessPlatformError) as exc:
        await (
            OmnigentWorkspacePublicationService._verified_no_commit_publication(
                run_command=run_command,
                base_branch="main",
            )
        )

    assert exc.value.code == "OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED"


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"host_launch_profile_ref": "codex-on-demand@3"}, "codex-on-demand@3"),
        (
            {"effective_launch_snapshot": {"hostMode": "on_demand_docker"}},
            "codex-on-demand@3",
        ),
        # Bootstrap cutover clears an idle binding's snapshot; its own
        # on-demand version still wins over the provider default.
        ({"effective_launch_snapshot": None}, "codex-on-demand@3"),
        ({"effective_launch_snapshot": {"hostMode": "static_compose"}}, None),
        (
            {"launch_policy_ref": "codex-static@2", "effective_launch_snapshot": None},
            None,
        ),
        (
            {"launch_policy_ref": "omnigent-codex@1", "effective_launch_snapshot": None},
            None,
        ),
        ({"launch_policy_ref": None}, None),
    ],
)
def test_binding_keeps_only_a_policy_that_can_still_launch(update, expected) -> None:
    binding = _binding().model_copy(
        update={
            "static_host_id": None,
            "host_launch_profile_ref": None,
            "launch_policy_ref": "codex-on-demand@3",
            **update,
        }
    )

    assert on_demand_binding_policy_ref(binding) == expected


# ---------------------------------------------------------------------------
# Repository and artifact fixtures shared with other Omnigent test modules.
# ---------------------------------------------------------------------------


def _init_source_repo(path: Path) -> None:
    """Create a small git source repo with a `main` and a `feature` branch."""

    path.mkdir(parents=True, exist_ok=True)

    def _git(*args: str) -> None:
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=test@moonmind.test",
                "-c",
                "user.name=MoonMind Test",
                "-c",
                "init.defaultBranch=main",
                *args,
            ],
            cwd=path,
            check=True,
            capture_output=True,
        )

    _git("init")
    _git("checkout", "-B", "main")
    (path / "README.md").write_text("main-content\n", encoding="utf-8")
    _git("add", "README.md")
    _git("commit", "-m", "initial")
    _git("checkout", "-B", "feature")
    (path / "feature.txt").write_text("feature-content\n", encoding="utf-8")
    _git("add", "feature.txt")
    _git("commit", "-m", "feature work")
    _git("checkout", "main")


def _sandbox_id() -> str:
    return hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]


class _FakeArtifactService:
    """Durable-service fake keyed by scheme-stripped artifact id.

    Mirrors ``TemporalArtifactService``: reads address artifacts by id (never the
    ``artifact://`` scheme) and require a ``principal``; the recorded calls let
    tests assert the real read contract is honored.
    """

    def __init__(
        self, payloads: dict[str, bytes], *, workflow_id: str = "workflow-1"
    ) -> None:
        self._payloads = payloads
        self._workflow_id = workflow_id
        self.read_calls: list[dict] = []

    async def get_metadata(self, *, artifact_id: str, **_kwargs):
        return (
            SimpleNamespace(size_bytes=len(self._payloads[artifact_id])),
            [SimpleNamespace(workflow_id=self._workflow_id)],
            False,
            None,
        )

    async def read(
        self,
        *,
        artifact_id: str,
        access: ArtifactAccessContext,
    ):
        self.read_calls.append(
            {
                "artifact_id": artifact_id,
                "access": access,
            }
        )
        return {}, self._payloads[artifact_id]


def test_normalize_repository_source_rejects_spoofed_github_host() -> None:
    normalize = normalize_repository_source
    # Real GitHub host injects credentials.
    assert normalize("https://github.com/org/repo.git") == (
        "https://github.com/org/repo.git",
        "github_https",
    )
    # Hosts that merely contain the substring "github.com" must be classified as
    # untrusted remotes so the GitHub token is never injected into them.
    for spoof in (
        "https://github.com.evil.com/org/repo.git",
        "https://evil.com/github.com/org/repo.git",
        "https://evilgithub.com/org/repo.git",
    ):
        assert normalize(spoof) == (spoof, "remote")
