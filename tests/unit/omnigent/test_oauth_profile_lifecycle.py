from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
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
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)

from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointIdentity,
    OmnigentRecoveryMode,
    materialize_cold_restore_inputs,
    recovery_mode,
    validate_branch_identity,
    validate_cold_restore_target,
    validate_restore_material,
)
from moonmind.omnigent.oauth_hosts import (
    OmnigentOAuthHostError,
    OmnigentOAuthHostRepository,
    validate_preflight_result,
)
from moonmind.omnigent.execution_profiles import (
    compile_effective_launch,
    validate_effective_launch_snapshot,
)
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.mounted_tool_preflight import MountedToolPreflightError
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
    _compile_persisted_effective_launch,
    _bind_candidate_workspace,
    _failure_evidence,
)
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.omnigent.remediation_workspace import RemediationWorkspaceError
from moonmind.provider_profiles.lease_client import (
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.schemas.temporal_models import WorkspaceCheckpointEvidenceModel
from moonmind.schemas.workspace_locator_models import WorkspaceLocatorResolutionError
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from tests.unit.omnigent.test_policy_authority import policy_document


@pytest.fixture(autouse=True)
def persisted_policy_authority(monkeypatch):
    """Keep coordinator tests focused while requiring the production authority seam."""

    async def resolve(_self, policy_ref):
        document = policy_document()
        if policy_ref.startswith("codex-on-demand@"):
            document["host"]["mode"] = "on_demand_docker"
            document["host"]["backendRef"] = "container-backend"
            document["session"]["cleanup"] = "remove"
        return compile_policy_snapshot(
            policy_id=policy_ref.rsplit("@", 1)[0],
            version=int(policy_ref.rsplit("@", 1)[1]),
            document=document,
            validation={"valid": True, "diagnostics": []},
        )

    monkeypatch.setattr(
        OmnigentProfileBoundExecutionCoordinator,
        "_resolve_policy_snapshot",
        resolve,
    )


def test_persisted_policy_snapshot_is_complete_launch_authority():
    snapshot = compile_policy_snapshot(
        policy_id="codex-static",
        version=1,
        document=policy_document(),
        validation={"valid": True, "diagnostics": []},
    )

    realized = _compile_persisted_effective_launch(
        snapshot, provider_profile_id="profile-1"
    )

    assert realized["hostMode"] == snapshot["boundaries"]["host"]["mode"]
    assert realized["serverImageRef"] == snapshot["boundaries"]["host"]["serverImageRef"]
    assert realized["limits"]["memoryMiB"] == snapshot["boundaries"]["resources"]["memoryMiB"]
    assert realized["networkRef"] == snapshot["boundaries"]["network"]["attachmentRef"]
    assert realized["mountClasses"] == snapshot["boundaries"]["workspace"]["mountClasses"]
    assert realized["boundaries"] == snapshot["boundaries"]
    assert realized["policyAuthority"]["policyDigest"] == snapshot["policyDigest"]
    assert realized["snapshotRef"].startswith("omnigent-launch:sha256:")
    validate_effective_launch_snapshot(realized)


@pytest.mark.parametrize(
    ("code", "failure_class", "remediation"),
    [
        ("authorization_denied", "authorization_error", "contact_administrator"),
        (
            "profile_resolution_failed",
            "configuration_error",
            "select_execution_profile",
        ),
        (
            "profile_readiness_failed",
            "configuration_error",
            "validate_codex_oauth",
        ),
        ("credential_owner_mismatch", "configuration_error", "validate_codex_oauth"),
        ("profile_lease_conflict", "resource_unavailable", "wait_for_profile_lease"),
        ("bridge_auth_failed", "configuration_error", "repair_bridge_authentication"),
        ("host_binding_mismatch", "configuration_error", "correct_host_binding"),
        ("harness_incompatible", "configuration_error", "correct_host_binding"),
        ("container_start_failed", "configuration_error", "repair_host_image"),
        ("image_pull_failed", "configuration_error", "repair_host_image"),
        ("network_unavailable", "integration_error", "repair_server_endpoint"),
        ("server_endpoint_invalid", "integration_error", "repair_server_endpoint"),
        ("session_create_failed", "integration_error", "retry_transient_upstream"),
        ("first_message_reconcile_failed", "integration_error", "retry_transient_upstream"),
    ],
)
def test_failure_evidence_classifies_operator_action(
    code: str, failure_class: str, remediation: str
) -> None:
    exc = RuntimeError("failed")
    exc.code = code  # type: ignore[attr-defined]
    assert _failure_evidence(exc) == (code, failure_class, remediation)


def test_failure_evidence_falls_back_when_code_is_none() -> None:
    exc = RuntimeError("failed")
    exc.code = None  # type: ignore[attr-defined]
    assert _failure_evidence(exc)[0] == "RuntimeError"


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

    assert OmnigentProfileBoundExecutionCoordinator._repository_mutation_required(explicit)
    assert OmnigentProfileBoundExecutionCoordinator._repository_mutation_required(
        branch_publish_without_gh
    )
    assert not OmnigentProfileBoundExecutionCoordinator._repository_mutation_required(read_only)


@pytest.mark.asyncio
async def test_remediation_admission_precedes_lease_and_host_mutation() -> None:
    lease_client = SimpleNamespace(
        acquire_execution_lease=AsyncMock(), release_lease=AsyncMock()
    )
    hosts = SimpleNamespace(
        get_binding_for_profile=AsyncMock(
            return_value=_binding().model_copy(
                update={"static_host_id": None, "host_launch_profile_ref": "codex"}
            )
        ),
        create_or_get_host_lease=AsyncMock(),
    )
    workspace_owner = SimpleNamespace(
        admit_and_resolve=AsyncMock(
            side_effect=RemediationWorkspaceError(
                "REMEDIATION_WORKSPACE_RESTORE_MISMATCH", "stale head"
            )
        )
    )
    store = SimpleNamespace(
        get_or_create=AsyncMock(
            return_value=SimpleNamespace(bridge_session_id="bridge-1")
        ),
        record_lifecycle_event=AsyncMock(),
    )
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=lease_client,
        host_repository=hosts,
        host_runtime=SimpleNamespace(prepare_host=AsyncMock()),
        run_store=store,
        execution_runner=AsyncMock(),
        artifact_gateway=object(),
        workspace_owner=workspace_owner,
    )
    coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=_launch_ready_profile()
    )
    step_id = "workflow-1:run-1:remediate:execution:2"
    workspace_id = hashlib.sha256(f"workflow-1:{step_id}".encode()).hexdigest()[:24]
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId="workflow-1",
        idempotencyKey="attempt-2",
        stepExecution={
            "schemaVersion": "v1",
            "workflowId": "workflow-1",
            "runId": "run-1",
            "logicalStepId": "remediate",
            "executionOrdinal": 2,
            "stepExecutionId": step_id,
            "reason": "retry",
            "runtimeContextPolicy": "fresh_agent_run",
            "contextBundleRef": "artifact://context/2",
            "contextBundleDigest": "sha256:" + "c" * 64,
            "preparedInputRefs": [],
            "runtimeSelection": {},
            "skillSourcePolicy": {},
        },
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            }
        },
        remediationWorkspace={
            "loopId": "loop-1",
            "branchRef": "checkpoint-branch:loop-1",
            "attemptOrdinal": 2,
            "workflowId": "workflow-1",
            "runId": "run-1",
            "logicalStepId": "remediate",
            "stepExecutionId": step_id,
            "baseCheckpointRef": "artifact://workspace/C1",
            "baseWorkspaceDigest": "sha256:" + "a" * 64,
            "expectedHeadVersion": 2,
            "headAuthorityRef": "artifact://loop-head/2",
            "destinationWorkspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            "executionProfileRef": "codex",
            "hostProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "workspaceCapabilitySnapshot": {
                "locatorKind": "sandbox", "restore": True
            },
        },
        parameters={"repositoryMutationRequired": True},
    )

    with pytest.raises(RemediationWorkspaceError):
        await coordinator.execute(request)

    workspace_owner.admit_and_resolve.assert_awaited_once()
    lease_client.acquire_execution_lease.assert_not_awaited()
    hosts.create_or_get_host_lease.assert_not_awaited()


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


def test_legacy_checkpoint_without_effective_launch_ref_remains_loadable() -> None:
    payload = _checkpoint().model_dump(by_alias=True, mode="json")
    payload.pop("effectiveLaunchRef")

    checkpoint = OmnigentCheckpointIdentity.model_validate(payload)

    assert checkpoint.effective_launch_ref is None


def test_complete_checkpoint_validates_and_compiles_cold_restore_material() -> None:
    payloads = {
        "artifact://external-state": b"external",
        "artifact://head": b"head",
        "artifact://workspace-checkpoint": b"workspace",
        "artifact://instructions": b"instructions",
        "artifact://context": b"context",
    }
    checkpoint = _checkpoint().model_copy(
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


def test_oauth_host_runtime_defaults_to_published_image(monkeypatch) -> None:
    monkeypatch.delenv("OMNIGENT_HOST_IMAGE", raising=False)
    monkeypatch.delenv("OMNIGENT_HOST_IMAGE_TAG", raising=False)

    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())

    assert runtime._image == "ghcr.io/omnigent-ai/omnigent-host:latest"


def test_oauth_host_runtime_respects_image_tag_override(monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE", "ghcr.io/omnigent-ai/omnigent-host")
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_TAG", "0.2.12")

    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())

    assert runtime._image == "ghcr.io/omnigent-ai/omnigent-host:0.2.12"


@pytest.mark.parametrize(
    "image",
    [
        "localhost:5000/omnigent-host:stable",
        "ghcr.io/omnigent-ai/omnigent-host@sha256:1234",
    ],
)
def test_oauth_host_runtime_preserves_complete_image_reference(
    monkeypatch, image: str
) -> None:
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE", image)
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_TAG", "ignored")

    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())

    assert runtime._image == image


@pytest.mark.asyncio
async def test_runtime_preflight_uses_stock_runner_environment_constructor() -> None:
    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())
    calls: list[tuple[str, ...]] = []

    async def run(*args, **_kwargs):
        calls.append(args)
        return 0, "ready", ""

    runtime._run = run  # type: ignore[method-assign]
    binding = _binding().model_copy(update={"host_launch_profile_ref": "codex"})
    lease = _host_lease().model_copy(update={"container_name": "host-mm-1215"})

    result = await runtime._preflight_mounted_tools(
        binding=binding,
        host_lease=lease,
        required_capabilities=("gh",),
        repository="owner/repo",
        mutation_required=True,
    )

    assert result["status"] == "ready"
    runner_calls = [call for call in calls if "python" in call]
    assert len(runner_calls) == 6
    assert all(
        "from omnigent.host.connect import _build_runner_env" in call[5]
        for call in runner_calls
    )
    assert all(
        call[:4] == ("docker", "exec", "host-mm-1215", "python")
        for call in runner_calls
    )


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
    lease = await ProviderProfileLeaseClient(adapter).acquire_execution_lease(
        runtime_id="codex_cli",
        profile_id="codex",
        owner_id="profile-lease:execution_omnigent:retry",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        metadata={"ownerIsWorkflow": True, "workflowId": "workflow-1"},
    )
    assert lease.profile_id == "codex"
    assert adapter.payload["metadata"]["ownerIsWorkflow"] is False
    assert adapter.payload["metadata"]["workflowId"] == "workflow-1"


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


@pytest.mark.asyncio
async def test_on_demand_host_initializes_state_before_unprivileged_launch(
    tmp_path, monkeypatch
) -> None:
    environment_image = "registry.example/environment-host@sha256:" + "1" * 64
    snapshot_image = "registry.example/snapshot-host@sha256:" + "2" * 64
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE", environment_image)
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime.container_exists = AsyncMock(return_value=False)
    runtime._discover_upstream_path = AsyncMock(
        return_value="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
    )
    runtime._run = AsyncMock(
        side_effect=[
            (1, "", "no such container"),
            (0, "", ""),
            (0, "", ""),
        ]
    )
    binding = _binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    lease = _host_lease().model_copy(update={"container_name": "mm-host-lease-1"})

    effective_launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex",
    )
    effective_launch["hostImageRef"] = snapshot_image

    await runtime._launch_on_demand(
        binding=binding,
        host_lease=lease,
        container_name="mm-host-lease-1",
        workspace_source=tmp_path,
        skill_projection=tmp_path / "skills",
        effective_launch=effective_launch,
    )

    commands = [call.args for call in runtime._run.await_args_list]
    assert commands[0][:3] == ("docker", "inspect", "--format")
    assert "/opt/moonmind/init-oauth-host.sh" in commands[1]
    assert commands[2][:3] == ("docker", "run", "-d")
    runtime._discover_upstream_path.assert_awaited_once_with(snapshot_image)
    assert commands[1][-1] == snapshot_image
    assert commands[2][-2] == snapshot_image
    assert environment_image not in commands[1]
    assert environment_image not in commands[2]
    assert commands[1][commands[1].index("--user") + 1] == "0:0"
    assert commands[2][commands[2].index("--workdir") + 1] == "/home/app"
    assert (
        "type=volume,src=moonmind-omnigent-tools-gh-2.76.2,"
        "dst=/opt/moonmind-tools,readonly"
    ) in commands[2]
    assert "MOONMIND_ACTIVE_SKILLS_DIR=/opt/moonmind-skills" in commands[2]
    assert "OMNIGENT_EXECUTION_TIMEOUT_SECONDS=5400" in commands[2]
    assert "OMNIGENT_EXECUTION_TIMEOUT_OWNER=temporal_workflow" in commands[2]
    assert "OMNIGENT_CAPTURE_OWNER=moonmind_bridge" in commands[2]
    assert "OMNIGENT_CAPTURE_RETENTION_DAYS=30" in commands[2]
    assert commands[2][commands[2].index("--stop-timeout") + 1] == "20"
    assert (
        f"type=bind,src={tmp_path / 'skills'},dst=/opt/moonmind-skills,readonly"
    ) in commands[2]
    assert (
        "type=volume,src=mm-host-lease-1-artifacts,dst=/artifacts"
    ) in commands[2]
    assert (
        "type=volume,src=mm-host-lease-1-cache,dst=/home/app/.cache"
    ) in commands[2]
    assert (
        "PATH=/opt/moonmind-tools/bin:/opt/venv/bin:/usr/local/bin:"
        "/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
    ) in commands[2]


@pytest.mark.asyncio
async def test_on_demand_claude_host_uses_claude_runtime_adapter(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime.container_exists = AsyncMock(return_value=False)
    runtime._discover_upstream_path = AsyncMock(return_value="/usr/bin:/bin")
    runtime._run = AsyncMock(
        side_effect=[(1, "", "no such container"), (0, "", ""), (0, "", "")]
    )
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
        hostLaunchProfileRef="claude-oauth-v1",
    )
    lease = _host_lease().model_copy(
        update={
            "provider_profile_id": "claude",
            "credential_generation": 4,
            "container_name": "mm-host-lease-claude",
        }
    )
    effective_launch = compile_effective_launch(
        profile_ref="omnigent-claude@1",
        policy_ref="claude-on-demand@1",
        provider_profile_id="claude",
    )

    await runtime._launch_on_demand(
        binding=binding,
        host_lease=lease,
        container_name="mm-host-lease-claude",
        workspace_source=tmp_path,
        skill_projection=tmp_path / "skills",
        effective_launch=effective_launch,
    )

    init_command, launch_command = [
        call.args for call in runtime._run.await_args_list
    ][1:]
    assert (
        "type=volume,src=claude_auth_volume,dst=/home/app/.claude"
        in init_command
    )
    assert "/opt/moonmind/init-oauth-host.sh" in init_command
    assert "OAUTH_HOME=/home/app/.claude" in init_command
    assert (
        "type=volume,src=claude_auth_volume,dst=/home/app/.claude"
        in launch_command
    )
    assert "CLAUDE_CONFIG_DIR=/home/app/.claude" in launch_command
    assert "CLAUDE_HOME=/home/app/.claude" in launch_command
    assert "CLAUDE_CREDENTIAL_GENERATION=4" in launch_command
    assert launch_command[-1] == "/opt/moonmind/start-claude-oauth-host.sh"
    configured_env = [
        launch_command[index + 1]
        for index, value in enumerate(launch_command[:-1])
        if value == "--env"
    ]
    assert not any(value.startswith("CODEX_") for value in configured_env)


@pytest.mark.asyncio
async def test_on_demand_retry_rejects_stopped_container_from_another_lease(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_HOST_IMAGE",
        "registry.example/environment-host@sha256:" + "1" * 64,
    )
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime.container_exists = AsyncMock(return_value=False)
    runtime._discover_upstream_path = AsyncMock(return_value="/usr/bin:/bin")
    runtime._run = AsyncMock(return_value=(0, "another-host-lease\n", ""))
    binding = _binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    lease = _host_lease().model_copy(update={"container_name": "mm-host-lease-1"})
    effective_launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex",
    )

    with pytest.raises(OmnigentOAuthHostError) as raised:
        await runtime._launch_on_demand(
            binding=binding,
            host_lease=lease,
            container_name="mm-host-lease-1",
            workspace_source=tmp_path,
            skill_projection=tmp_path / "skills",
            effective_launch=effective_launch,
        )

    assert raised.value.code == "OMNIGENT_HOST_OWNERSHIP_MISMATCH"
    commands = [call.args for call in runtime._run.await_args_list]
    assert len(commands) == 1
    assert commands[0][:3] == ("docker", "inspect", "--format")


@pytest.mark.asyncio
async def test_host_preparation_resolves_pre_materialized_workspace_without_git(
    tmp_path,
) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path / "workspaces"
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))

    workspace_id = hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]

    workspace = tmp_path / "workspaces" / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    SandboxWorkspaceRecordStore(tmp_path / "workspaces").ensure(
        SandboxWorkspaceRecord(workspace_id, "workflow-1", "step-1", "repo")
    )

    resolved = await runtime._prepare_workspace(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
    )

    assert resolved == workspace
    runtime._run.assert_not_awaited()


@pytest.mark.asyncio
async def test_host_preparation_materializes_missing_owner_record(tmp_path) -> None:
    workspace_root = tmp_path / "workspaces"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=workspace_root
    )
    workspace_id = hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]
    workspace = workspace_root / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)

    resolved = await runtime._prepare_workspace(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
    )

    assert resolved == workspace
    assert SandboxWorkspaceRecordStore(workspace_root).load(workspace_id) == (
        SandboxWorkspaceRecord(workspace_id, "workflow-1", "step-1", "repo")
    )


@pytest.mark.asyncio
async def test_stop_host_cleans_volumes_when_container_is_absent(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace(), workspace_root=tmp_path)
    runtime._run = AsyncMock(return_value=(1, "", "not found"))
    binding = _binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    lease = _host_lease().model_copy(update={"container_name": "mm-host-lease-1"})

    await runtime.stop_host(binding=binding, host_lease=lease)

    commands = [call.args for call in runtime._run.await_args_list]
    assert commands[0][:3] == ("docker", "inspect", "--format")
    assert ("docker", "volume", "rm", "-f", "mm-host-lease-1-artifacts") in commands
    assert ("docker", "volume", "rm", "-f", "mm-host-lease-1-cache") in commands


@pytest.mark.asyncio
async def test_host_preparation_rejects_unmaterialized_workspace_without_mutation(tmp_path) -> None:
    workspace_root = tmp_path / "workspaces"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=workspace_root
    )

    runtime._run = AsyncMock(return_value=(0, "", ""))
    workspace_id = hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
        )

    assert exc.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
    assert not (workspace_root / "temporal_sandbox" / workspace_id / "repo").exists()
    runtime._run.assert_not_awaited()


@pytest.mark.asyncio
async def test_workspace_retry_rejects_tampered_owner_record_before_git_mutation(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=workspace_root
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))
    workspace_id = hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]
    (workspace_root / "temporal_sandbox" / workspace_id / "repo").mkdir(parents=True)
    records = workspace_root / "temporal_sandbox" / ".workspace_records"
    records.mkdir(parents=True)
    (records / f"{workspace_id}.json").write_text(
        json.dumps(
            {
                "workspace_id": workspace_id,
                "workflow_id": "foreign-workflow",
                "step_execution_id": "step-1",
                "relative_path": "repo",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
        )

    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"
    runtime._run.assert_not_awaited()


def test_tools_path_prepend_is_idempotent_and_preserves_upstream_path() -> None:
    upstream = "/custom/bin:/usr/local/bin:/usr/bin:/bin"
    expected = "/opt/moonmind-tools/bin:/custom/bin:/usr/local/bin:/usr/bin:/bin"

    assert OmnigentOAuthHostRuntime._prepend_tools_path(upstream) == expected
    assert OmnigentOAuthHostRuntime._prepend_tools_path(expected) == expected


def test_github_write_probe_uses_publish_or_skill_side_effect() -> None:
    base = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        idempotencyKey="idem",
        correlationId="corr",
        parameters={"requiredCapabilities": ["gh"], "publishMode": "none"},
    )

    assert not OmnigentProfileBoundExecutionCoordinator._github_mutation_required(base)
    assert OmnigentProfileBoundExecutionCoordinator._github_mutation_required(
        base.model_copy(update={"parameters": {**base.parameters, "publishMode": "pr"}})
    )
    assert OmnigentProfileBoundExecutionCoordinator._github_mutation_required(
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
async def test_tools_path_discovery_falls_back_when_image_is_not_local(
    tmp_path,
) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime._run = AsyncMock(return_value=(1, "", "No such image"))

    image_ref = "registry.example/snapshot-host@sha256:" + "2" * 64
    assert await runtime._discover_upstream_path(image_ref) == (
        "/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
    )
    assert runtime._run.await_args.args[-1] == image_ref
    assert runtime._run.await_args.kwargs["check"] is False


@pytest.mark.asyncio
async def test_tools_check_uses_host_login_shell_and_manifest(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))

    await runtime._exec_tools_check("mm-host-lease-1")

    command = runtime._run.await_args.args
    assert command[:5] == ("docker", "exec", "mm-host-lease-1", "bash", "-lc")
    assert "test -f /opt/moonmind-tools/manifest.json" in command[-1]
    assert "command -v gh" in command[-1]
    assert "gh --version" in command[-1]


@pytest.mark.asyncio
async def test_static_host_runtime_uses_only_canonical_compose_file(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))

    launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-static@1",
        provider_profile_id="codex",
    )
    workspace = tmp_path / "authorized-workspace"
    skills = tmp_path / "authorized-skills"
    await runtime._compose_static_check(
        workspace_source=workspace,
        skill_projection=skills,
        effective_launch=launch,
    )
    await runtime.stop_static_host()

    commands = [call.args for call in runtime._run.await_args_list]
    assert commands == [
        (
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "up",
            "-d",
            "omnigent-host-codex",
        ),
        (
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "exec",
            "-T",
            "omnigent-host-codex",
            "/opt/moonmind/check-runner-projections.sh",
        ),
        (
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "stop",
            "omnigent-host-codex",
        ),
    ]
    assert all("docker-compose.codex-host.yaml" not in command for command in commands)
    start_env = runtime._run.await_args_list[0].kwargs["env"]
    assert start_env["OMNIGENT_RUN_WORKSPACE"] == str(workspace)
    assert start_env["OMNIGENT_ACTIVE_SKILLS_DIR"] == str(skills)


def test_static_codex_compose_separates_authorized_mount_classes() -> None:
    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yaml").read_text(
        encoding="utf-8"
    )
    service = compose.split("  omnigent-host-codex:", 1)[1].split(
        "  omnigent-host-claude:", 1
    )[0]

    assert "${OMNIGENT_RUN_WORKSPACE:-./omnigent_workspaces/run}:/workspaces/run" in service
    assert "${OMNIGENT_ACTIVE_SKILLS_DIR" in service
    assert "omnigent-tools:/opt/moonmind-tools:ro" in service
    assert "omnigent-host-artifacts:/artifacts" in service
    assert "omnigent-host-cache:/home/app/.cache" in service


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


@pytest.mark.asyncio
async def test_host_rejects_missing_skill_projection_before_workspace_mutation(
    tmp_path,
) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path / "workspaces"
    )
    runtime._prepare_workspace = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(OmnigentOAuthHostError) as exc_info:
        await runtime.prepare_host(
            binding=_binding(),
            host_lease=_host_lease(),
            workspace_key="run-1",
            workspace_locator={"kind": "sandbox", "workspaceId": "unused"},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            effective_launch=compile_effective_launch(
                profile_ref="omnigent-codex@1",
                policy_ref="codex-static@1",
                provider_profile_id="codex",
            ),
        )

    assert exc_info.value.code == "OMNIGENT_SKILL_PROJECTION_UNAVAILABLE"
    runtime._prepare_workspace.assert_not_awaited()


@pytest.mark.asyncio
async def test_effective_policy_conflict_fails_before_host_mutation(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path / "workspaces"
    )
    runtime._prepare_skill_projection = AsyncMock()  # type: ignore[method-assign]
    runtime._prepare_workspace = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(OmnigentOAuthHostError) as exc_info:
        await runtime.prepare_host(
            binding=_binding(),
            host_lease=_host_lease(),
            workspace_key="run-1",
            workspace_locator={"kind": "sandbox", "workspaceId": "unused"},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            effective_launch=compile_effective_launch(
                profile_ref="omnigent-codex@1",
                policy_ref="codex-on-demand@1",
                provider_profile_id="codex",
            ),
        )

    assert exc_info.value.code == "OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT"
    runtime._prepare_skill_projection.assert_not_awaited()
    runtime._prepare_workspace.assert_not_awaited()


def test_projection_scripts_install_real_gh_and_resolve_login_shell(tmp_path) -> None:
    scripts = Path(__file__).resolve().parents[3] / "services" / "omnigent" / "scripts"
    fake_bin = tmp_path / "source"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text("#!/bin/sh\necho 'gh version 2.76.2 (fixture)'\n", encoding="utf-8")
    fake_gh.chmod(0o755)
    output = tmp_path / "bundle"
    env = {
        **os.environ,
        "MOONMIND_GH_SOURCE": str(fake_gh),
        "MOONMIND_GH_VERSION": "2.76.2",
        "MOONMIND_TOOL_BUNDLE_OUTPUT": str(output),
    }

    installed = subprocess.run(
        ["sh", str(scripts / "init-mounted-tools.sh")],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    assert json.loads((output / "manifest.json").read_text())["tools"][0]["name"] == "gh"
    assert (output / "bin" / "gh").stat().st_mode & 0o222 == 0
    fake_gh.write_text("#!/bin/sh\necho 'gh version 2.77.0 (fixture)'\n", encoding="utf-8")
    env["MOONMIND_GH_VERSION"] = "2.77.0"
    upgraded = subprocess.run(
        ["sh", str(scripts / "init-mounted-tools.sh")],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    assert json.loads((output / "manifest.json").read_text())["tools"][0]["version"] == "2.77.0"
    login_home = tmp_path / "home"
    login_home.mkdir()
    (login_home / ".bash_profile").write_text(
        f"export PATH={output / 'bin'}:$PATH\n", encoding="utf-8"
    )
    login = subprocess.run(
        ["bash", "-lc", "command -v gh && gh --version"],
        env={
            **os.environ,
            "HOME": str(login_home),
            "PATH": f"{output / 'bin'}:{os.environ.get('PATH', '')}",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert login.returncode == 0, login.stderr
    assert "2.77.0" in login.stdout


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

    for script_name in (
        "start-codex-oauth-host.sh",
        "start-claude-oauth-host.sh",
        "start-host-with-projections.sh",
    ):
        source = (scripts / script_name).read_text(encoding="utf-8")
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
    adapter_source = (repo_root / "moonmind/omnigent/oauth_host_runtime.py").read_text()
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
        == OmnigentRecoveryMode.LIVE_REATTACH
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
        == OmnigentRecoveryMode.COLD_RESTORE
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


@pytest.mark.asyncio
async def test_claude_live_recovery_reuses_shared_checkpoint_with_exact_harness() -> None:
    checkpoint = _checkpoint().model_copy(
        update={
            "provider_profile_id": "claude",
            "credential_generation": 4,
            "provider_lease_ref": "provider-lease-claude",
            "host_binding_ref": "omnigent-oauth:claude",
            "host_lease_ref": "host-lease-claude",
            "omnigent_host_id": "claude-host-1",
            "omnigent_session_id": "claude-session-1",
            "bridge_session_id": "claude-bridge-1",
        }
    )
    candidate = CandidateWorkspaceAuthority(
        loopId="mm:claude-recovery",
        attemptOrdinal=2,
        headRef="artifact://candidate-head/claude-2",
        headDigest="sha256:" + "a" * 64,
        checkpointRef="artifact://workspace-checkpoint/claude-2",
        checkpointDigest="sha256:" + "b" * 64,
    )
    runner = AsyncMock(return_value=AgentRunResult(summary="reattached"))
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=SimpleNamespace(),
        host_repository=SimpleNamespace(),
        host_runtime=SimpleNamespace(),
        run_store=SimpleNamespace(),
        execution_runner=runner,
        artifact_gateway=object(),
    )
    coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(runtime_id="claude_code")
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="claude",
        correlationId="workflow-claude",
        idempotencyKey="recovery-attempt",
        inputRefs=["artifact://context-pack/claude"],
    )

    result = await coordinator.recover_from_checkpoint(
        request=request,
        checkpoint=checkpoint,
        provider_lease={"active": True, "leaseId": "provider-lease-claude"},
        host_lease={
            "status": "assigned",
            "leaseId": "host-lease-claude",
            "credentialGeneration": 4,
        },
        host_registered=True,
        session_valid=True,
        first_message_consistent=True,
        current_credential_generation=4,
        candidate_workspace=candidate,
    )

    assert result.summary == "reattached"
    bound = runner.await_args.args[0]
    assert bound.idempotency_key == checkpoint.idempotency_key
    assert bound.parameters["omnigent"]["agent"]["harnessOverride"] == "claude-native"
    assert bound.parameters["omnigent"]["session"] == {
        "hostType": "external",
        "hostId": "claude-host-1",
        "workspace": "/workspaces/run",
    }
    assert bound.parameters["candidateWorkspace"]["checkpointRef"] == (
        "artifact://workspace-checkpoint/claude-2"
    )
    assert bound.input_refs == [
        "artifact://context-pack/claude",
        "artifact://candidate-head/claude-2",
        "artifact://workspace-checkpoint/claude-2",
        "artifact://external-state",
    ]


def test_candidate_workspace_authority_binds_exact_durable_restore_refs() -> None:
    candidate = CandidateWorkspaceAuthority(
        loopId="mm:loop-1",
        attemptOrdinal=2,
        headRef="artifact://candidate-head/2",
        headDigest="sha256:" + "a" * 64,
        checkpointRef="artifact://workspace-checkpoint/2",
        checkpointDigest="sha256:" + "b" * 64,
    )
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "mm:loop-1",
            "idempotencyKey": "attempt-3",
            "inputRefs": ["artifact://remaining-work/2"],
        }
    )

    bound = _bind_candidate_workspace(request, candidate)

    assert bound.parameters["candidateWorkspace"] == candidate.model_dump(
        by_alias=True, mode="json"
    )
    assert bound.input_refs == [
        "artifact://remaining-work/2",
        "artifact://candidate-head/2",
        "artifact://workspace-checkpoint/2",
    ]

    with pytest.raises(ValidationError, match="durable artifact reference"):
        CandidateWorkspaceAuthority(
            loopId="mm:loop-1",
            attemptOrdinal=2,
            headRef="/workspaces/original-root",
            headDigest="sha256:" + "a" * 64,
            checkpointRef="artifact://workspace-checkpoint/2",
            checkpointDigest="sha256:" + "b" * 64,
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
            static_host_id="host-1",
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
        starting = await repository.transition_host_lease(
            first.lease_id,
            expected_status="allocating",
            new_status="starting",
        )
        assert starting.status == "starting"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_coordinator_releases_provider_lease_after_host_cleanup() -> None:
    actions: list[str] = []
    lifecycle: list[tuple[str, str | None]] = []
    provider_lease = SimpleNamespace(
        profile_id="codex",
        runtime_id="codex_cli",
        lease_id="provider-lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )

    class LeaseClient:
        async def acquire_execution_lease(self, **_kwargs):
            actions.append("provider_acquired")
            return provider_lease

        async def release_lease(self, _lease):
            actions.append("provider_released")

        async def record_cooldown(self, **_kwargs):
            actions.append("cooldown")

    class Hosts:
        def __init__(self):
            self.lease = _host_lease().model_copy(
                update={"status": "allocating", "omnigent_host_id": None}
            )

        async def get_binding_for_profile(self, _profile_id):
            return _binding()

        async def create_or_update_static_binding(self, **kwargs):
            return _binding().model_copy(update={
                "execution_profile_ref": kwargs["execution_profile_ref"],
                "launch_policy_ref": kwargs["launch_policy_ref"],
                "effective_launch_snapshot": kwargs["effective_launch_snapshot"],
            })

        async def create_or_get_host_lease(self, **_kwargs):
            actions.append("host_lease_created")
            return self.lease

        async def transition_host_lease(
            self, _lease_id, *, expected_status, new_status, fields=None
        ):
            assert self.lease.status == expected_status
            self.lease = self.lease.model_copy(
                update={"status": new_status, **dict(fields or {})}
            )
            actions.append(f"host_{new_status}")
            return self.lease

        async def mark_host_lease_stopped(self, _lease_id):
            actions.append("host_stopped")
            self.lease = self.lease.model_copy(update={"status": "stopped"})
            return self.lease

        async def mark_host_lease_failed(self, *_args, **_kwargs):
            actions.append("host_failed")

    class Runtime:
        async def prepare_host(self, **_kwargs):
            actions.append("preflight")
            return {"hostId": "host-1", "workspacePath": "/workspaces/run"}

        async def stop_host(self, **_kwargs):
            actions.append("host_cleanup")

    class Store:
        async def get_or_create(self, **_kwargs):
            actions.append("bridge_envelope_created")
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def bind_profile_authorization(self, **_kwargs):
            actions.append("bridge_bound")
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def record_lifecycle_event(self, _key, *, event_type, **kwargs):
            actions.append(event_type)
            lifecycle.append((event_type, kwargs.get("status")))

    async def execute(request, **_kwargs):
        assert request.parameters["omnigent"]["session"] == {
            "hostType": "external",
            "hostId": "host-1",
            "workspace": "/workspaces/run",
        }
        actions.append("executed")
        return AgentRunResult(summary="done")

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=Runtime(),
        run_store=Store(),
        execution_runner=execute,
        artifact_gateway=object(),
    )
    coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED,
            disabled_reason=None,
            max_parallel_runs=1,
            cooldown_after_429_seconds=900,
            runtime_id="codex_cli",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
            volume_ref="codex_auth_volume",
            volume_mount_path="/home/app/.codex",
            secret_refs={},
            command_behavior={},
        )
    )
    result = await coordinator.execute(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            executionProfileRef="codex",
            correlationId="workflow-1",
            idempotencyKey="idem-1",
            workspaceSpec={
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": hashlib.sha256(b"workflow-1:idem-1").hexdigest()[:24],
                }
            },
            parameters={
                "omnigent": {"session": {"workspace": "https://example.com/repo.git"}}
            },
        )
    )
    assert result.summary == "done"
    assert actions[0] == "bridge_envelope_created"
    assert actions[-1] == "terminal"
    assert actions.index("host_stopped") < actions.index("profile_lease_release")
    assert actions.index("provider_released") < actions.index(
        "profile_lease_release", actions.index("provider_released")
    )
    for stage, success_status in (
        ("request_validated", "completed"),
        ("profile_resolution", "completed"),
        ("profile_readiness", "ready"),
        ("profile_lease_acquired", "completed"),
        ("host_binding_resolution", "completed"),
        ("host_lease_created", "completed"),
        ("container_start", "completed"),
        ("credential_mount", "completed"),
        ("credential_preflight", "ready"),
        ("host_registration", "completed"),
        ("harness_readiness", "ready"),
        ("bridge_authentication", "completed"),
        ("session_creation", "completed"),
        ("first_message_prepare", "completed"),
        ("first_message_post", "completed"),
        ("session_running", "completed"),
        ("resource_harvest", "completed"),
        ("host_cleanup", "completed"),
        ("profile_lease_release", "completed"),
    ):
        assert (stage, "started") in lifecycle
        assert (stage, success_status) in lifecycle
        assert lifecycle.index((stage, "started")) < lifecycle.index(
            (stage, success_status)
        )


@pytest.mark.asyncio
async def test_coordinator_records_runner_preflight_block_before_execution() -> None:
    events: list[tuple[str, dict]] = []
    execute = AsyncMock()
    provider_lease = SimpleNamespace(
        profile_id="codex",
        runtime_id="codex_cli",
        lease_id="provider-lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )

    class LeaseClient:
        async def acquire_execution_lease(self, **_kwargs):
            return provider_lease

        async def release_lease(self, _lease):
            return None

    class Hosts:
        def __init__(self):
            self.lease = _host_lease().model_copy(
                update={"status": "allocating", "omnigent_host_id": None}
            )

        async def get_binding_for_profile(self, _profile_id):
            return _binding()

        async def create_or_update_static_binding(self, **kwargs):
            return _binding().model_copy(update={
                "execution_profile_ref": kwargs["execution_profile_ref"],
                "launch_policy_ref": kwargs["launch_policy_ref"],
                "effective_launch_snapshot": kwargs["effective_launch_snapshot"],
            })

        async def create_or_get_host_lease(self, **_kwargs):
            return self.lease

        async def transition_host_lease(
            self, _lease_id, *, expected_status, new_status, fields=None
        ):
            assert self.lease.status == expected_status
            self.lease = self.lease.model_copy(
                update={"status": new_status, **dict(fields or {})}
            )
            return self.lease

        async def mark_host_lease_stopped(self, _lease_id):
            self.lease = self.lease.model_copy(update={"status": "stopped"})
            return self.lease

        async def mark_host_lease_failed(self, *_args, **_kwargs):
            return None

    class Runtime:
        async def prepare_host(self, **kwargs):
            assert kwargs["required_capabilities"] == ("gh",)
            raise MountedToolPreflightError(
                "Mounted gh preflight failed during runner authentication",
                code="github_auth_unavailable",
                evidence={
                    "tool": "gh",
                    "phase": "authentication",
                    "probes": [{"boundary": "runner", "status": "failed"}],
                },
            )

        async def stop_host(self, **_kwargs):
            return None

    class Store:
        async def get_or_create(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def bind_profile_authorization(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def record_lifecycle_event(self, _key, *, event_type, **kwargs):
            events.append((event_type, kwargs))

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=Runtime(),
        run_store=Store(),
        execution_runner=execute,
        artifact_gateway=object(),
    )
    coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED,
            disabled_reason=None,
            max_parallel_runs=1,
            cooldown_after_429_seconds=900,
            runtime_id="codex_cli",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
            volume_ref="codex_auth_volume",
            volume_mount_path="/home/app/.codex",
            secret_refs={},
            command_behavior={},
        )
    )
    coordinator._github_token = AsyncMock(return_value="resolved-token")  # type: ignore[method-assign]

    with pytest.raises(MountedToolPreflightError):
        await coordinator.execute(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                executionProfileRef="codex",
                correlationId="workflow-1",
                idempotencyKey="idem-1",
                workspaceSpec={
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": hashlib.sha256(b"workflow-1:idem-1").hexdigest()[:24],
                    }
                },
                parameters={
                    "repository": "owner/repo",
                    "requiredCapabilities": ["gh"],
                    "omnigent": {
                        "session": {"workspace": "https://github.com/owner/repo.git"}
                    },
                },
            )
        )

    execute.assert_not_awaited()
    blocked = next(
        kwargs for name, kwargs in events if name == "mounted_tool_preflight_blocked"
    )
    assert blocked["code"] == "github_auth_unavailable"
    assert blocked["metadata"] == {
        "tool": "gh",
        "phase": "authentication",
        "probes": [{"boundary": "runner", "status": "failed"}],
    }
    transitions = [(name, kwargs.get("status")) for name, kwargs in events]
    for stage in ("container_start", "credential_preflight"):
        assert (stage, "started") in transitions
        assert (stage, "failed") in transitions
    for stage in (
        "credential_mount",
        "host_registration",
        "harness_readiness",
        "bridge_authentication",
    ):
        assert (stage, "started") not in transitions
        assert (stage, "failed") not in transitions
    assert ("host_cleanup", "completed") in transitions
    assert ("profile_lease_release", "completed") in transitions
    assert transitions[-1] == ("terminal", "failed")


def _workspace_intent_profile():
    return SimpleNamespace(
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        disabled_reason=None,
        max_parallel_runs=1,
        cooldown_after_429_seconds=900,
        runtime_id="codex_cli",
        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        volume_ref="codex_auth_volume",
        volume_mount_path="/home/app/.codex",
        secret_refs={},
        command_behavior={},
    )


@pytest.mark.asyncio
async def test_coordinator_compiles_durable_workspace_intent_before_host_mutation() -> (
    None
):
    """The normal authoring path compiles one durable, versioned intent record,
    persists bounded evidence, and drives host preparation from it — before any
    host or Docker mutation."""

    from moonmind.omnigent.workspace_intent import compile_workspace_intent

    events: list[tuple[str, dict]] = []
    prepare_kwargs: dict = {}
    execute = AsyncMock()
    provider_lease = SimpleNamespace(
        profile_id="codex",
        runtime_id="codex_cli",
        lease_id="provider-lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )

    class LeaseClient:
        async def acquire_execution_lease(self, **_kwargs):
            return provider_lease

        async def release_lease(self, _lease):
            return None

    class Hosts:
        def __init__(self):
            self.lease = _host_lease().model_copy(
                update={"status": "allocating", "omnigent_host_id": None}
            )

        async def get_binding_for_profile(self, _profile_id):
            return _binding()

        async def create_or_update_static_binding(self, **kwargs):
            return _binding().model_copy(update={
                "execution_profile_ref": kwargs["execution_profile_ref"],
                "launch_policy_ref": kwargs["launch_policy_ref"],
                "effective_launch_snapshot": kwargs["effective_launch_snapshot"],
            })

        async def create_or_get_host_lease(self, **_kwargs):
            return self.lease

        async def transition_host_lease(
            self, _lease_id, *, expected_status, new_status, fields=None
        ):
            assert self.lease.status == expected_status
            self.lease = self.lease.model_copy(
                update={"status": new_status, **dict(fields or {})}
            )
            return self.lease

        async def mark_host_lease_stopped(self, _lease_id):
            self.lease = self.lease.model_copy(update={"status": "stopped"})
            return self.lease

        async def mark_host_lease_failed(self, *_args, **_kwargs):
            return None

    class Runtime:
        async def prepare_host(self, **kwargs):
            prepare_kwargs.update(kwargs)
            raise MountedToolPreflightError(
                "stop after compile",
                code="github_auth_unavailable",
                evidence={"tool": "gh", "phase": "authentication"},
            )

        async def stop_host(self, **_kwargs):
            return None

    class Store:
        async def get_or_create(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def bind_profile_authorization(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def record_lifecycle_event(self, _key, *, event_type, **kwargs):
            events.append((event_type, kwargs))

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=Runtime(),
        run_store=Store(),
        execution_runner=execute,
        artifact_gateway=object(),
    )
    coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=_workspace_intent_profile()
    )
    coordinator._github_token = AsyncMock(return_value="resolved-token")  # type: ignore[method-assign]

    workspace_id = hashlib.sha256(b"workflow-1:idem-1").hexdigest()[:24]
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId="workflow-1",
        idempotencyKey="idem-1",
        inputRefs=["artifact://in1"],
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            "repository": "https://github.com/owner/repo.git",
            "startingBranch": "main",
            "targetBranch": "feature/x",
            # A checkpoint restore that mixes an artifact input with a provider
            # external-state ref. Only the artifact ref may reach host artifact
            # materialization; the external-state ref must not be forwarded there.
            "restoreInputRefs": ["artifact://chk1", "external-state:sess-9"],
        },
        parameters={
            "repository": "https://github.com/owner/repo.git",
            "requiredCapabilities": ["gh"],
            "publishMode": "none",
        },
    )

    with pytest.raises(MountedToolPreflightError):
        await coordinator.execute(request)

    # Bounded, credential-free compilation evidence was persisted durably.
    compiled = next(
        kwargs for name, kwargs in events if name == "workspace_intent_compiled"
    )
    evidence = compiled["metadata"]
    expected = compile_workspace_intent(
        request, workflow_id="workflow-1", step_execution_id="idem-1"
    )
    assert evidence["intentDigest"] == expected.intent_digest
    # The durable event identity is scoped to the compiled intent digest so a
    # conflicting resubmission under the same idempotency key cannot silently
    # retain the stale evidence.
    assert compiled["event_identity"] == (
        f"workspace_intent_compiled:{expected.intent_digest}"
    )
    # Only the artifact-backed restore ref reaches host materialization; the
    # provider external-state ref is partitioned out of the compiled record.
    assert tuple(prepare_kwargs["restore_input_refs"]) == ("artifact://chk1",)
    assert evidence["schemaVersion"] == "v1"
    assert evidence["repositoryMutation"] is False
    assert evidence["publishMode"] == "none"
    assert evidence["repositoryKind"] == "github_https"
    assert evidence["locatorKind"] == "sandbox"
    assert evidence["inputRefCount"] == 1

    # Compilation happens before any host binding/mutation.
    stage_order = [name for name, _ in events]
    assert stage_order.index("workspace_intent_compilation") < stage_order.index(
        "container_start"
    )

    # Host preparation is driven from the typed locator and authored source in
    # the compiled record — never a caller bind path or volume name.
    assert prepare_kwargs["workspace_locator"] == {
        "kind": "sandbox",
        "workspaceId": workspace_id,
        "relativePath": "repo",
    }
    assert prepare_kwargs["repository_source"] == "https://github.com/owner/repo.git"
    assert prepare_kwargs["starting_branch"] == "main"
    assert prepare_kwargs["target_branch"] == "feature/x"


@pytest.mark.asyncio
async def test_coordinator_fails_closed_on_smuggled_runtime_shortcut() -> None:
    """A caller-authored Docker-authority shortcut fails closed at compile time,
    before any provider lease or host mutation."""

    events: list[tuple[str, dict]] = []
    lease_acquired = False
    prepared = False

    class LeaseClient:
        async def acquire_execution_lease(self, **_kwargs):
            nonlocal lease_acquired
            lease_acquired = True
            return SimpleNamespace(
                profile_id="codex",
                runtime_id="codex_cli",
                lease_id="provider-lease-1",
                owner_id="owner-1",
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            )

        async def release_lease(self, _lease):
            return None

    class Hosts:
        async def get_binding_for_profile(self, _profile_id):
            return _binding()

    class Runtime:
        async def prepare_host(self, **_kwargs):
            nonlocal prepared
            prepared = True
            return {}

        async def stop_host(self, **_kwargs):
            return None

    class Store:
        async def get_or_create(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def bind_profile_authorization(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def record_lifecycle_event(self, _key, *, event_type, **kwargs):
            events.append((event_type, kwargs))

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=Runtime(),
        run_store=Store(),
        execution_runner=AsyncMock(),
        artifact_gateway=object(),
    )
    coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=_workspace_intent_profile()
    )
    coordinator._github_token = AsyncMock(return_value=None)  # type: ignore[method-assign]

    workspace_id = hashlib.sha256(b"workflow-1:idem-1").hexdigest()[:24]
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId="workflow-1",
        idempotencyKey="idem-1",
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            "repository": "owner/repo",
            "dockerVolume": "operator-chosen-volume",
        },
        parameters={"repository": "owner/repo"},
    )

    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        await coordinator.execute(request)

    assert excinfo.value.code == "WORKSPACE_INTENT_UNSAFE_INPUT"
    # No workspace was compiled, no provider lease acquired, no host mutated.
    assert not any(name == "workspace_intent_compiled" for name, _ in events)
    assert lease_acquired is False
    assert prepared is False


def _launch_ready_profile():
    return SimpleNamespace(
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        disabled_reason=None,
        max_parallel_runs=1,
        cooldown_after_429_seconds=900,
        runtime_id="codex_cli",
        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        volume_ref="codex_auth_volume",
        volume_mount_path="/home/app/.codex",
        secret_refs={},
        command_behavior={},
    )


def _injected_launch_error(code: str) -> OmnigentOAuthHostError:
    error = OmnigentOAuthHostError("deterministic injected failure", code=code)
    error.diagnostics_ref = f"artifact://diagnostics/{code}"  # type: ignore[attr-defined]
    return error


async def _run_coordinator_failure_case(
    *, fail_at: str, code: str, release_failures: int = 0
):
    events: list[tuple[str, dict]] = []
    actions: list[str] = []
    provider_lease = SimpleNamespace(
        profile_id="codex",
        runtime_id="codex_cli",
        lease_id="provider-lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )
    error = _injected_launch_error(code)

    class FailureOwners:
        """Deterministic fakes for the concrete launch/cleanup owners.

        Keep these entry points separate: the coordinator test must prove which
        boundary produced an error, rather than merely assigning several labels
        to one shared ``prepare_host`` or execution-runner exception branch.
        """

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def fail(self, owner: str) -> None:
            self.calls.append(owner)
            if fail_at == owner:
                raise error

        async def stop_static_host(self) -> None:
            self.calls.append("host_stop")
            if fail_at == "host_stop":
                raise error
            actions.append("host_stopped")

        async def remove_on_demand_host(self) -> None:
            self.calls.append("host_remove")
            if fail_at == "host_remove":
                raise error
            actions.append("host_removed")

    owners = FailureOwners()

    class LeaseClient:
        remaining_release_failures = release_failures

        async def acquire_execution_lease(self, **_kwargs):
            if fail_at == "lease":
                raise error
            return provider_lease

        async def release_lease(self, _lease):
            if self.remaining_release_failures:
                self.remaining_release_failures -= 1
                raise _injected_launch_error("profile_lease_release_failed")
            actions.append("provider_released")

    class Hosts:
        def __init__(self):
            self.lease = _host_lease().model_copy(
                update={"status": "allocating", "omnigent_host_id": None}
            )

        async def get_binding_for_profile(self, _profile_id):
            if fail_at == "binding":
                raise error
            if fail_at in {
                "container_start", "image_pull", "network_start",
                "credential_volume_missing", "credential_volume_owner",
                "credential_generation", "credential_login",
                "host_registration", "host_registration_timeout",
                "host_capability", "harness_readiness",
                "bridge_authentication", "server_endpoint",
            }:
                return _binding().model_copy(
                    update={"static_host_id": None, "host_launch_profile_ref": "codex"}
                )
            return _binding()

        async def create_or_update_static_binding(self, **kwargs):
            binding = await self.get_binding_for_profile(kwargs["profile_id"])
            return binding.model_copy(update={
                "execution_profile_ref": kwargs["execution_profile_ref"],
                "launch_policy_ref": kwargs["launch_policy_ref"],
                "effective_launch_snapshot": kwargs["effective_launch_snapshot"],
            })

        async def create_or_get_host_lease(self, **_kwargs):
            if fail_at == "host_lease":
                raise error
            return self.lease

        async def transition_host_lease(
            self, _lease_id, *, expected_status, new_status, fields=None
        ):
            self.lease = self.lease.model_copy(
                update={"status": new_status, **dict(fields or {})}
            )
            return self.lease

        async def mark_host_lease_stopped(self, _lease_id):
            self.lease = self.lease.model_copy(update={"status": "stopped"})
            return self.lease

        async def mark_host_lease_failed(self, *_args, **_kwargs):
            return None

    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())
    runtime._prepare_skill_projection = AsyncMock(  # type: ignore[method-assign]
        return_value=Path("/tmp/skills")
    )
    runtime._prepare_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=Path("/tmp/workspace")
    )
    runtime._launch_on_demand = AsyncMock()  # type: ignore[method-assign]
    # Egress attestation is an orthogonal trusted-backend gate; this matrix
    # exercises coordinator failure/cleanup evidence, so treat enforcement as
    # already attested and let the intended failure stages surface.
    runtime._attest_egress = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
    runtime._exec_check = AsyncMock()  # type: ignore[method-assign]
    runtime._exec_tools_check = AsyncMock()  # type: ignore[method-assign]
    runtime._resolve_exact_host = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": "host-1", "harnesses": ["codex-native"]}
    )
    runtime._preflight_mounted_tools = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "not_required", "boundaries": []}
    )

    runtime_failure_owner = {
        "container_start": "_launch_on_demand",
        "image_pull": "_launch_on_demand",
        "network_start": "_launch_on_demand",
        "credential_volume_missing": "_launch_on_demand",
        "credential_volume_owner": "_launch_on_demand",
        "credential_generation": "_launch_on_demand",
        "credential_login": "_exec_check",
        "host_registration": "_resolve_exact_host",
        "host_registration_timeout": "_resolve_exact_host",
        "host_capability": "_resolve_exact_host",
        "harness_readiness": "_preflight_mounted_tools",
        "bridge_authentication": "_resolve_exact_host",
        "server_endpoint": "_resolve_exact_host",
    }.get(fail_at)
    if runtime_failure_owner is not None:
        owner_mock = getattr(runtime, runtime_failure_owner)

        async def fail_from_production_runtime(*_args, **_kwargs):
            owners.calls.append(fail_at)
            raise error

        owner_mock.side_effect = fail_from_production_runtime

    async def run_cleanup_command(*args, **_kwargs):
        command = tuple(args[:3])
        if command[:2] == ("docker", "stop"):
            owners.calls.append("host_remove")
            if fail_at == "host_remove":
                raise error
            actions.append("host_stopped")
        elif command == ("docker", "compose", "-f") and "stop" in args:
            owners.calls.append("host_stop")
            if fail_at == "host_stop":
                raise error
            actions.append("host_stopped")
        return 0, "", ""

    runtime._run = AsyncMock(side_effect=run_cleanup_command)  # type: ignore[method-assign]

    class Store:
        async def get_or_create(self, **_kwargs):
            actions.append("envelope_created")
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def bind_profile_authorization(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def record_lifecycle_event(self, _key, *, event_type, **kwargs):
            events.append((event_type, kwargs))

    async def execute(_request, **_kwargs):
        for owner in (
            "session_create",
            "first_message_digest",
            "first_message_reconcile",
            "resource_harvest",
        ):
            await owners.fail(owner)
        return AgentRunResult(summary="done")

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=runtime,
        run_store=Store(),
        execution_runner=execute,
        artifact_gateway=object(),
    )
    if fail_at in {"profile_missing", "profile_validation"}:
        coordinator._resolve_profile = AsyncMock(side_effect=error)  # type: ignore[method-assign]
    elif fail_at == "profile_readiness":
        coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                **{**vars(_launch_ready_profile()), "enabled": False}
            )
        )
    else:
        coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=_launch_ready_profile()
        )

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId="workflow-1",
        idempotencyKey="idem-failure-matrix",
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": hashlib.sha256(
                    b"workflow-1:idem-failure-matrix"
                ).hexdigest()[:24],
            }
        },
        parameters={
            "untrustedSupportValue": "github_pat_secret_value_must_not_persist",
            "omnigent": {"session": {"workspace": "https://example.com/repo.git"}},
        },
    )
    if fail_at == "host_remove":
        coordinator._hosts.get_binding_for_profile = AsyncMock(  # type: ignore[attr-defined]
            return_value=_binding().model_copy(
                update={"static_host_id": None, "host_launch_profile_ref": "codex"}
            )
        )

    if fail_at in {"host_stop", "host_remove", "release"}:
        await coordinator.execute(request)
    else:
        with pytest.raises(OmnigentOAuthHostError) as captured:
            await coordinator.execute(request)
        assert captured.value.code == code
    return events, actions, owners.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_at", "code", "failed_stage", "failure_class", "remediation"),
    [
        ("profile_missing", "profile_resolution_missing", "profile_resolution", "configuration_error", "select_execution_profile"),
        ("profile_validation", "profile_resolution_validation_failed", "profile_resolution", "configuration_error", "select_execution_profile"),
        ("profile_readiness", "profile_readiness_failed", "profile_readiness", "configuration_error", "validate_codex_oauth"),
        ("lease", "profile_lease_conflict", "profile_lease_wait", "resource_unavailable", "wait_for_profile_lease"),
        ("lease", "profile_lease_timeout", "profile_lease_wait", "resource_unavailable", "wait_for_profile_lease"),
        ("lease", "profile_lease_lost", "profile_lease_wait", "resource_unavailable", "wait_for_profile_lease"),
        ("lease", "profile_cooldown_active", "profile_lease_wait", "integration_error", "retry_transient_upstream"),
        ("binding", "host_binding_mismatch", "host_binding_resolution", "configuration_error", "correct_host_binding"),
        ("host_lease", "container_allocation_failed", "host_lease_created", "configuration_error", "repair_host_image"),
        ("container_start", "container_start_failed", "container_start", "configuration_error", "repair_host_image"),
        ("image_pull", "image_pull_failed", "container_start", "configuration_error", "repair_host_image"),
        ("network_start", "network_unavailable", "container_start", "integration_error", "repair_server_endpoint"),
        ("credential_volume_missing", "credential_volume_missing", "credential_mount", "configuration_error", "validate_codex_oauth"),
        ("credential_volume_owner", "credential_owner_mismatch", "credential_mount", "configuration_error", "validate_codex_oauth"),
        ("credential_generation", "credential_generation_stale", "credential_mount", "configuration_error", "validate_codex_oauth"),
        ("credential_login", "oauth_login_preflight_failed", "credential_preflight", "configuration_error", "validate_codex_oauth"),
        ("host_registration", "host_registration_failed", "host_registration", "integration_error", "retry_transient_upstream"),
        ("host_registration_timeout", "host_registration_timeout", "host_registration", "integration_error", "retry_transient_upstream"),
        ("host_capability", "codex_native_capability_missing", "harness_readiness", "configuration_error", "correct_host_binding"),
        ("harness_readiness", "harness_incompatible", "harness_readiness", "configuration_error", "correct_host_binding"),
        ("bridge_authentication", "bridge_auth_401", "bridge_authentication", "configuration_error", "repair_bridge_authentication"),
        ("server_endpoint", "server_endpoint_invalid", "bridge_authentication", "integration_error", "repair_server_endpoint"),
        ("session_create", "session_create_failed", "session_creation", "integration_error", "retry_transient_upstream"),
        ("first_message_digest", "first_message_digest_mismatch", "first_message_prepare", "integration_error", "retry_transient_upstream"),
        ("first_message_reconcile", "ambiguous_posting_reconciliation", "first_message_prepare", "integration_error", "retry_transient_upstream"),
        ("resource_harvest", "resource_harvest_failed", "resource_harvest", "integration_error", "retry_transient_upstream"),
    ],
)
async def test_coordinator_failure_matrix_preserves_actionable_terminal_evidence(
    fail_at: str,
    code: str,
    failed_stage: str,
    failure_class: str,
    remediation: str,
) -> None:
    events, actions, owner_calls = await _run_coordinator_failure_case(
        fail_at=fail_at, code=code
    )

    assert actions[0] == "envelope_created"
    failed = [
        kwargs
        for stage, kwargs in events
        if stage == failed_stage and kwargs.get("status") == "failed"
    ]
    assert failed, [(stage, kwargs.get("status")) for stage, kwargs in events]
    opening_index = next(
        index
        for index, (stage, kwargs) in enumerate(events)
        if stage == failed_stage and kwargs.get("status") in {"started", "waiting"}
    )
    failed_index = next(
        index
        for index, (stage, kwargs) in enumerate(events)
        if stage == failed_stage and kwargs.get("status") == "failed"
    )
    assert opening_index < failed_index
    assert failed[-1]["code"] == code
    assert failed[-1]["failure_class"] == failure_class
    assert failed[-1]["remediation_action"] == remediation
    expected_diagnostics = (
        None
        if fail_at == "profile_readiness"
        else f"artifact://diagnostics/{code}"
    )
    assert failed[-1]["diagnostics_ref"] == expected_diagnostics
    assert failed[-1]["metadata"]["workflowId"] == "workflow-1"
    assert events[-1][0] == "terminal"
    assert events[-1][1]["status"] == "failed"
    terminal = events[-1][1]["metadata"]
    assert terminal["cleanupCompleted"] is True
    assert terminal["leaseReleased"] is True
    assert "github_pat_secret_value_must_not_persist" not in json.dumps(events)
    if fail_at in owner_calls:
        assert owner_calls.count(fail_at) == 1
    if fail_at in {
        "container_start", "image_pull", "network_start", "credential_volume_missing",
        "credential_volume_owner", "credential_generation", "credential_login",
        "host_registration", "host_registration_timeout", "host_capability",
        "harness_readiness", "bridge_authentication", "server_endpoint",
        "session_create", "first_message_digest", "first_message_reconcile",
        "resource_harvest",
    }:
        assert actions.index("host_stopped") < actions.index("provider_released")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_at", "code"),
    [("host_stop", "host_stop_failed"), ("host_remove", "host_remove_failed")],
)
async def test_coordinator_cleanup_failure_defers_provider_release_and_requires_janitor(
    fail_at: str, code: str
) -> None:
    events, actions, owner_calls = await _run_coordinator_failure_case(
        fail_at=fail_at, code=code
    )
    assert owner_calls[-1] == fail_at
    cleanup = next(
        kwargs
        for stage, kwargs in events
        if stage == "host_cleanup" and kwargs.get("status") == "failed"
    )
    assert cleanup["remediation_action"] == "inspect_cleanup_diagnostics"
    assert cleanup["metadata"]["cleanupCompleted"] is False
    assert cleanup["metadata"]["janitorRequired"] is True
    release = next(
        kwargs
        for stage, kwargs in events
        if stage == "profile_lease_release" and kwargs.get("status") == "waiting"
    )
    assert release["code"] == "credential_cleanup_incomplete"
    assert release["metadata"]["leaseReleased"] is False
    assert "provider_released" not in actions
    assert events[-1][1]["metadata"] == {
        "workflowId": "workflow-1",
        "stepExecutionId": None,
        "cleanupCompleted": False,
        "leaseReleased": False,
        "janitorRequired": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("release_failures", "release_status", "janitor_required"),
    [(2, "completed", False), (3, "failed", True)],
)
async def test_coordinator_provider_release_has_bounded_retry_evidence(
    monkeypatch, release_failures: int, release_status: str, janitor_required: bool
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(
        "moonmind.omnigent.profile_bound_execution.asyncio.sleep", sleep
    )
    events, actions, _owner_calls = await _run_coordinator_failure_case(
        fail_at="release",
        code="profile_lease_release_failed",
        release_failures=release_failures,
    )
    release = next(
        kwargs
        for stage, kwargs in events
        if stage == "profile_lease_release" and kwargs.get("status") == release_status
    )
    assert sleep.await_count == 2
    assert release["metadata"]["leaseReleased"] is (not janitor_required)
    if janitor_required:
        assert release["remediation_action"] == "inspect_cleanup_diagnostics"
        assert release["metadata"]["janitorRequired"] is True
        assert "provider_released" not in actions
    else:
        assert "provider_released" in actions
    assert events[-1][1]["metadata"]["janitorRequired"] is janitor_required
@pytest.fixture(autouse=True)
def immutable_bootstrap_images(monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "example.test/omnigent@sha256:" + "1" * 64)
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_REF", "example.test/host@sha256:" + "2" * 64)


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#3507 — normal-workflow workspace materialization
# and single-boundary locator resolution.
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


def _current_branch(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _sandbox_id() -> str:
    return hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]


def _runtime_for(tmp_path: Path) -> OmnigentOAuthHostRuntime:
    # ``tmp_path`` is the authorized per-run source root, so local test source
    # repositories created under it are clonable while arbitrary host paths are
    # rejected.
    return OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        workspace_root=tmp_path / "workspaces",
        repository_source_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_prepare_workspace_materializes_repository_and_branch(tmp_path) -> None:
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()

    resolved = await runtime._prepare_workspace(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        repository_source=str(source),
        starting_branch="feature",
    )

    expected = (
        tmp_path / "workspaces" / "temporal_sandbox" / workspace_id / "repo"
    )
    assert resolved == expected
    assert (resolved / ".git").is_dir()
    assert (resolved / "feature.txt").is_file()
    assert _current_branch(resolved) == "feature"
    evidence = runtime._last_workspace_evidence
    assert evidence["locatorKind"] == "sandbox"
    assert evidence["identityVerified"] is True
    assert evidence["materialization"]["action"] == "materialized"
    assert evidence["materialization"]["checkedOut"] == "feature"
    # Bounded evidence never leaks a raw worker/daemon path.
    assert str(resolved) not in json.dumps(evidence)


@pytest.mark.asyncio
async def test_prepare_workspace_materialization_is_idempotent(tmp_path) -> None:
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()
    kwargs = dict(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        repository_source=str(source),
        starting_branch="main",
    )

    first = await runtime._prepare_workspace(**kwargs)
    marker = first / "retry-marker.txt"
    marker.write_text("preserved", encoding="utf-8")

    second = await runtime._prepare_workspace(**kwargs)

    assert first == second
    # A retry must not re-clone or discard existing working-tree state.
    assert marker.read_text(encoding="utf-8") == "preserved"
    assert runtime._last_workspace_evidence["materialization"]["action"] == (
        "reused_pre_materialized"
    )


@pytest.mark.asyncio
async def test_prepare_workspace_honors_authored_output_branch(tmp_path) -> None:
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()

    resolved = await runtime._prepare_workspace(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        repository_source=str(source),
        starting_branch="main",
        target_branch="agent/work",
    )

    assert _current_branch(resolved) == "agent/work"
    assert (resolved / "README.md").is_file()
    assert runtime._last_workspace_evidence["materialization"]["outputBranch"] == (
        "agent/work"
    )


@pytest.mark.asyncio
async def test_prepare_workspace_rejects_external_state_locator(tmp_path) -> None:
    runtime = _runtime_for(tmp_path)
    runtime._run = AsyncMock(return_value=(0, "", ""))

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={
                "kind": "external_state",
                "artifactRef": "artifact://checkpoint/123",
            },
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
        )

    assert exc.value.code == "WORKSPACE_LOCATOR_UNSUPPORTED"
    runtime._run.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_workspace_rejects_managed_runtime_locator(tmp_path) -> None:
    runtime = _runtime_for(tmp_path)
    runtime._run = AsyncMock(return_value=(0, "", ""))

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={
                "kind": "managed_runtime",
                "runtimeId": "codex_cli",
                "agentRunId": "run-1",
            },
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
        )

    assert exc.value.code == "WORKSPACE_LOCATOR_UNSUPPORTED"
    runtime._run.assert_not_awaited()


class _FakeArtifactService:
    """Durable-service fake keyed by scheme-stripped artifact id.

    Mirrors ``TemporalArtifactService``: reads address artifacts by id (never the
    ``artifact://`` scheme) and require a ``principal``; the recorded calls let
    tests assert the real read contract is honored.
    """

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads
        self.read_calls: list[dict] = []

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str | None = None,
        allow_restricted_raw: bool = False,
        **_kwargs,
    ):
        self.read_calls.append(
            {
                "artifact_id": artifact_id,
                "principal": principal,
                "allow_restricted_raw": allow_restricted_raw,
            }
        )
        return {}, self._payloads[artifact_id]


@pytest.mark.asyncio
async def test_prepare_workspace_materializes_restore_inputs_as_refs(tmp_path) -> None:
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()
    ref = "artifact://checkpoint/workspace-archive"
    # The durable service is addressed by the scheme-stripped id, not the ref.
    service = _FakeArtifactService({"checkpoint/workspace-archive": b"restore-bytes"})

    resolved = await runtime._prepare_workspace(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        repository_source=str(source),
        starting_branch="main",
        restore_input_refs=(ref,),
        artifact_gateway=service,
    )

    restore_dir = resolved / ".moonmind" / "restore"
    written = list(restore_dir.iterdir())
    assert len(written) == 1
    assert written[0].read_bytes() == b"restore-bytes"
    restore_evidence = runtime._last_workspace_evidence["materialization"][
        "restoreInputs"
    ]
    assert restore_evidence == [{"ref": ref, "bytes": len(b"restore-bytes")}]
    # The read went through the durable service contract: scheme-stripped id,
    # dedicated restore principal, and raw-access authorization.
    assert service.read_calls == [
        {
            "artifact_id": "checkpoint/workspace-archive",
            "principal": "service:omnigent_workspace_restore",
            "allow_restricted_raw": True,
        }
    ]


@pytest.mark.asyncio
async def test_prepare_workspace_rejects_local_path_restore_input(tmp_path) -> None:
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()
    service = _FakeArtifactService({})

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            repository_source=str(source),
            starting_branch="main",
            restore_input_refs=("/etc/passwd",),
            artifact_gateway=service,
        )

    # A restore input that is a local path must never be conflated with an
    # artifact ref.
    assert exc.value.code == "WORKSPACE_LOCATOR_UNSUPPORTED"


class _StreamingArtifactService:
    """Durable-service fake that supports metadata + chunked reads."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads
        self.metadata_calls: list[str] = []
        self.chunk_calls: list[str] = []

    async def get_metadata(self, *, artifact_id: str, principal: str, **_kwargs):
        self.metadata_calls.append(artifact_id)
        payload = self._payloads[artifact_id]
        artifact = SimpleNamespace(size_bytes=len(payload))
        return artifact, [], False, None

    async def read_chunks(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool = False,
        chunk_size: int = 1024,
    ):
        assert allow_restricted_raw is True
        assert principal == "service:omnigent_workspace_restore"
        self.chunk_calls.append(artifact_id)
        payload = self._payloads[artifact_id]
        chunks = [
            payload[index : index + chunk_size]
            for index in range(0, len(payload), chunk_size)
        ] or [b""]
        return SimpleNamespace(size_bytes=len(payload)), chunks


@pytest.mark.asyncio
async def test_prepare_workspace_streams_restore_inputs_when_supported(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()
    ref = "artifact://checkpoint/big-archive"
    payload = b"x" * (3 * 1024 * 1024)
    service = _StreamingArtifactService({"checkpoint/big-archive": payload})

    resolved = await runtime._prepare_workspace(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        repository_source=str(source),
        starting_branch="main",
        restore_input_refs=(ref,),
        artifact_gateway=service,
    )

    restore_dir = resolved / ".moonmind" / "restore"
    written = list(restore_dir.iterdir())
    assert len(written) == 1
    assert written[0].read_bytes() == payload
    # The payload was pre-checked from metadata and streamed via chunked reads.
    assert service.metadata_calls == ["checkpoint/big-archive"]
    assert service.chunk_calls == ["checkpoint/big-archive"]


@pytest.mark.asyncio
async def test_prepare_workspace_rejects_oversized_restore_from_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "moonmind.omnigent.oauth_host_runtime._MAX_RESTORE_INPUT_BYTES", 16
    )
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()
    ref = "artifact://checkpoint/too-big"
    service = _StreamingArtifactService({"checkpoint/too-big": b"y" * 64})

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            repository_source=str(source),
            starting_branch="main",
            restore_input_refs=(ref,),
            artifact_gateway=service,
        )

    assert exc.value.code == "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED"
    # Rejected before any bytes were streamed.
    assert service.chunk_calls == []


@pytest.mark.asyncio
async def test_prepare_workspace_enforces_cumulative_restore_budget(
    tmp_path, monkeypatch
) -> None:
    # Each ref is individually legal, but together they exceed the cumulative
    # budget, so the second ref is rejected.
    monkeypatch.setattr(
        "moonmind.omnigent.oauth_host_runtime._MAX_RESTORE_INPUT_BYTES", 1024
    )
    monkeypatch.setattr(
        "moonmind.omnigent.oauth_host_runtime._MAX_RESTORE_TOTAL_BYTES", 1024
    )
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()
    payloads = {
        "checkpoint/a": b"a" * 800,
        "checkpoint/b": b"b" * 800,
    }
    service = _FakeArtifactService(payloads)

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            repository_source=str(source),
            starting_branch="main",
            restore_input_refs=(
                "artifact://checkpoint/a",
                "artifact://checkpoint/b",
            ),
            artifact_gateway=service,
        )

    assert exc.value.code == "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED"


@pytest.mark.asyncio
async def test_prepare_workspace_rejects_local_source_outside_authorized_root(
    tmp_path,
) -> None:
    # A source outside the authorized per-run root cannot be cloned even though
    # it is a readable git repository on the worker.
    outside = tmp_path.parent / "outside-source"
    _init_source_repo(outside)
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        workspace_root=tmp_path / "workspaces",
        repository_source_root=tmp_path / "authorized",
    )
    workspace_id = _sandbox_id()

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            repository_source=str(outside),
            starting_branch="main",
        )

    assert exc.value.code == "WORKSPACE_LOCATOR_UNSUPPORTED"


@pytest.mark.asyncio
async def test_prepare_workspace_rebuilds_incomplete_workspace_on_retry(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    _init_source_repo(source)
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()
    locator = {"kind": "sandbox", "workspaceId": workspace_id}

    # Simulate a prior attempt that created the workspace directory but crashed
    # before writing durable completion evidence.
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecordStore,
    )

    partial = (
        tmp_path / "workspaces" / "temporal_sandbox" / workspace_id / "repo"
    )
    partial.mkdir(parents=True, exist_ok=True)
    (partial / "partial-clone.txt").write_text("incomplete", encoding="utf-8")
    assert not SandboxWorkspaceRecordStore(
        tmp_path / "workspaces"
    ).is_materialized(workspace_id)

    resolved = await runtime._prepare_workspace(
        workspace_locator=locator,
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        repository_source=str(source),
        starting_branch="feature",
    )

    # The incomplete directory was torn down and rebuilt from the authored state.
    assert not (resolved / "partial-clone.txt").exists()
    assert (resolved / ".git").is_dir()
    assert _current_branch(resolved) == "feature"
    assert runtime._last_workspace_evidence["materialization"]["action"] == (
        "materialized"
    )
    assert SandboxWorkspaceRecordStore(
        tmp_path / "workspaces"
    ).is_materialized(workspace_id)


def test_normalize_repository_source_rejects_spoofed_github_host() -> None:
    normalize = OmnigentOAuthHostRuntime._normalize_repository_source
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


def _execution_request(**overrides) -> AgentExecutionRequest:
    payload = dict(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-3507",
        idempotencyKey="idem-3507",
        parameters={"repository": "org/repo"},
    )
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


def test_coordinator_reads_repository_and_branch_intent_from_workspace_spec() -> None:
    request = _execution_request(
        workspaceSpec={
            "repository": "org/repo",
            "startingBranch": "release",
            "targetBranch": "agent/work",
            "baseCommit": "abc123",
            "restoreInputRefs": ["artifact://a", "artifact://a", "artifact://b"],
        }
    )

    assert OmnigentProfileBoundExecutionCoordinator._repository_source(request) == (
        "org/repo"
    )
    assert OmnigentProfileBoundExecutionCoordinator._starting_branch(request) == (
        "release"
    )
    assert OmnigentProfileBoundExecutionCoordinator._target_branch(request) == (
        "agent/work"
    )
    assert OmnigentProfileBoundExecutionCoordinator._checkout_commit(request) == (
        "abc123"
    )
    # De-duplicated, order-preserving durable refs only.
    assert OmnigentProfileBoundExecutionCoordinator._restore_input_refs(request) == (
        ("artifact://a", "artifact://b")
    )


def test_coordinator_repository_intent_defaults_are_empty() -> None:
    request = _execution_request(parameters={})

    assert OmnigentProfileBoundExecutionCoordinator._repository_source(request) == ""
    assert OmnigentProfileBoundExecutionCoordinator._starting_branch(request) is None
    assert OmnigentProfileBoundExecutionCoordinator._target_branch(request) is None
    assert OmnigentProfileBoundExecutionCoordinator._checkout_commit(request) is None
    assert OmnigentProfileBoundExecutionCoordinator._restore_input_refs(request) == ()


@pytest.mark.asyncio
async def test_github_token_resolves_clone_credential_without_gh_capability(
    monkeypatch,
) -> None:
    # publishMode=none read-only work derives `git` but not `gh`; a private
    # GitHub source must still resolve a clone credential.
    import moonmind.auth.github_credentials as github_credentials

    resolve = AsyncMock(
        return_value=SimpleNamespace(token="clone-token")
    )
    monkeypatch.setattr(github_credentials, "resolve_github_credential", resolve)

    request = _execution_request(
        parameters={"repository": "org/repo", "requiredCapabilities": ["git"]}
    )

    token = await OmnigentProfileBoundExecutionCoordinator._github_token(request)

    assert token == "clone-token"
    resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_github_token_public_clone_tolerates_missing_credential(
    monkeypatch,
) -> None:
    import moonmind.auth.github_credentials as github_credentials

    resolve = AsyncMock(return_value=SimpleNamespace(token=""))
    monkeypatch.setattr(github_credentials, "resolve_github_credential", resolve)

    request = _execution_request(
        parameters={"repository": "org/repo", "requiredCapabilities": ["git"]}
    )

    # No mounted-gh requirement, so a missing credential is not fatal: a public
    # clone can proceed unauthenticated (a private clone fails later at git).
    assert await OmnigentProfileBoundExecutionCoordinator._github_token(request) is None


@pytest.mark.asyncio
async def test_github_token_requires_credential_when_gh_capability_declared(
    monkeypatch,
) -> None:
    import moonmind.auth.github_credentials as github_credentials

    resolve = AsyncMock(return_value=SimpleNamespace(token=""))
    monkeypatch.setattr(github_credentials, "resolve_github_credential", resolve)

    request = _execution_request(
        parameters={"repository": "org/repo", "requiredCapabilities": ["git", "gh"]}
    )

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await OmnigentProfileBoundExecutionCoordinator._github_token(request)
    assert exc.value.code == "github_auth_unavailable"


@pytest.mark.asyncio
async def test_github_token_skipped_for_non_github_source(monkeypatch) -> None:
    import moonmind.auth.github_credentials as github_credentials

    resolve = AsyncMock(return_value=SimpleNamespace(token="unused"))
    monkeypatch.setattr(github_credentials, "resolve_github_credential", resolve)

    # A non-GitHub remote with no gh capability needs no GitHub clone credential.
    request = _execution_request(
        parameters={"requiredCapabilities": ["git"]},
        workspaceSpec={"repository": "https://gitlab.com/org/repo.git"},
    )

    assert await OmnigentProfileBoundExecutionCoordinator._github_token(request) is None
    resolve.assert_not_awaited()
