"""Normal-product boundary coverage for the default generic realizer.

The test keeps the production registry, SQL stores, canonical command service,
credential materializer, workspace owner, host services, and cleanup path. Only
the deployment-owned external endpoints (Temporal lease RPC, Docker command
runner, Omnigent HTTP API, and secret resolver) are bounded test fixtures.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentCredentialRuntimeRecord,
    OmnigentHostLeaseRecordV2,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.harness_platform.admission import (
    compile_normal_product_execution_plan,
)
from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    compute_attestation_ref,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
)
from moonmind.omnigent.harness_platform.host_classes import get_host_class
from moonmind.omnigent.harness_platform.materializers import (
    FORBIDDEN_AMBIENT_ENV_KEYS,
)
from moonmind.omnigent.harness_platform.stores import (
    DbExecutionPlanStore,
    DbRuntimeBindingStore,
)
from moonmind.omnigent.realizers.registry import (
    get_default_registry,
    reset_default_registry,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.security.egress import (
    EGRESS_CONFIG_DIGEST,
    ENFORCER_IMPLEMENTATION,
    EgressAttestation,
    OMNIGENT_EGRESS_PROFILE,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _try_generic_realizer_dispatch,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _agent_profile_snapshot() -> tuple[dict[str, object], dict[str, object]]:
    implementation = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "pluginEntryPoint": None,
        }
    )
    implementation_payload = implementation.model_dump(by_alias=True, mode="json")
    snapshot: dict[str, object] = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": "opencode-native-profile",
        "version": 7,
        "digest": "sha256:" + "1" * 64,
        "providerProfileRef": "opencode-native-provider",
        "executionProfileRef": "opencode-native@1",
        "launchPolicyRef": "omnigent-on-demand@1",
        "agentId": "opencode-native-agent",
        "policyRef": "omnigent-policy:integration",
        "document": {
            "schemaVersion": "moonmind.omnigent-agent-profile.v1",
            "endpointRef": "default",
            "bridgeMode": "proxy",
            "source": {
                "upstreamId": "opencode-native-agent",
                "upstreamVersion": "1.0.0",
            },
            "harness": "opencode-native",
            "requiredCapabilities": ["streaming"],
            "execution": {
                "defaultExecutionProfileRef": "opencode-native@1",
                "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
            },
            "providerRequirements": {
                "runtimeId": "opencode_go",
                "credentialSource": "api_key",
                "materializationMode": "generated_file",
                "providerIds": ["opencode"],
            },
            "model": {"model": "provider/model", "settings": {}},
            "workspace": {"mutation": "allowed", "requiredCapabilities": []},
            "skills": [],
            "tools": [],
            "capture": {"stream": True, "evidence": True},
            "rag": {"initial": {}, "followUp": {}},
            "continuations": {
                "checkpoint": True,
                "branch": True,
                "remediation": True,
            },
            "publish": {"mode": "none"},
            "policyRef": "omnigent-policy:integration",
        },
        "upstreamSnapshot": {
            "id": "opencode-native-agent",
            "version": "1.0.0",
            "harness": "opencode-native",
            "catalogObservedAt": datetime.now(UTC).isoformat(),
            "harnessImplementation": implementation_payload,
        },
        "validationResult": {"ready": True},
    }
    return snapshot, implementation_payload


@pytest.mark.asyncio
async def test_default_registry_executes_and_cleans_up_opencode_product_path(
    pg_store,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Exercise the default registry across persisted production boundaries."""

    image_ref = (
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64
    )
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "local")
    monkeypatch.delenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", raising=False)
    monkeypatch.setenv("OMNIGENT_GENERIC_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.invalid")
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", image_ref)
    monkeypatch.chdir(tmp_path)
    for name in FORBIDDEN_AMBIENT_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)

    snapshot, implementation_payload = _agent_profile_snapshot()
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=snapshot,
        workflow_parameters={"model": "provider/model", "workflow": {}},
        workflow_id="mm:default-generic-product-path",
    )
    plan_store = DbExecutionPlanStore(pg_store._session_factory)
    await plan_store.persist(plan)

    async with pg_store._session_factory() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="opencode-native-provider",
                runtime_id="opencode_go",
                provider_id="opencode",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=(
                    RuntimeMaterializationMode.CONFIG_BUNDLE
                ),
                secret_refs={"api_key": {"kind": "managed", "ref": "test-key"}},
                credential_generation=6,
                max_parallel_runs=2,
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
                last_auth_method=ProviderProfileAuthMethod.SECRET_REF,
            )
        )
        await session.commit()

    correlation_id = "mm:default-generic-product-path"
    idempotency_key = "step-default-generic-product-path"
    workspace_id = hashlib.sha256(
        f"{correlation_id}:{idempotency_key}".encode("utf-8")
    ).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id=correlation_id,
            step_execution_id=idempotency_key,
            relative_path="repo",
        )
    )

    released_leases: list[str] = []

    async def acquire_execution_lease(_client, **kwargs):
        return CredentialLease(
            profile_id=kwargs["profile_id"],
            runtime_id=kwargs["runtime_id"],
            lease_id="provider-lease:default-product-path",
            owner_id=kwargs["owner_id"],
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        )

    async def release_lease(_client, lease):
        released_leases.append(lease.lease_id)

    async def resolve_secret(_secret_ref, *, field_name):
        assert "api_key SecretRef" in field_name
        return "integration-only-opencode-key"

    monkeypatch.setattr(
        "moonmind.provider_profiles.lease_client."
        "ProviderProfileLeaseClient.acquire_execution_lease",
        acquire_execution_lease,
    )
    monkeypatch.setattr(
        "moonmind.provider_profiles.lease_client.ProviderProfileLeaseClient.release_lease",
        release_lease,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.deployment_adapters.resolve_managed_api_key_reference",
        resolve_secret,
    )

    docker_state: dict[str, object] = {"launched": False, "removed": False}

    async def run_docker(argv, *, env=None):
        args = list(argv)
        if args[0] == "run":
            docker_state["launched"] = True
            docker_state["container"] = args[args.index("--name") + 1]
            docker_state["image"] = args[-1]
            docker_state["lease"] = next(
                value.split("=", 1)[1]
                for index, value in enumerate(args)
                if args[index - 1] == "--label"
                and value.startswith("moonmind.host_lease_id=")
            )
            assert env is not None
            return 0, b"container-id", b""
        if args[:2] == ["rm", "-f"]:
            docker_state["removed"] = True
            return 0, b"", b""
        if args[0] == "inspect" and "{{json .Config.Image}}" in args:
            return 0, json.dumps(docker_state["image"]).encode("utf-8"), b""
        if args[0] == "inspect":
            if docker_state["launched"]:
                return 0, str(docker_state["lease"]).encode("utf-8"), b""
            return 1, b"", b"not found"
        raise AssertionError(f"unexpected deployment command: {args[0]}")

    monkeypatch.setattr(
        "moonmind.omnigent.realizers.deployment_adapters._run_docker", run_docker
    )

    egress_attestation = EgressAttestation(
        profileRef=OMNIGENT_EGRESS_PROFILE.ref,
        profileDigest=OMNIGENT_EGRESS_PROFILE.digest,
        enforcerImplementation=ENFORCER_IMPLEMENTATION,
        backendRef="docker-cli/trusted-worker",
        networkRef=OMNIGENT_EGRESS_PROFILE.network_ref,
        gatewayRef=OMNIGENT_EGRESS_PROFILE.gateway_ref,
        appliedRuleDigest="sha256:" + "2" * 64,
        configDigest=EGRESS_CONFIG_DIGEST,
        gatewayImageDigest="sha256:" + "3" * 64,
        validatedAt=datetime.now(UTC),
    )

    async def attest_egress(**_kwargs):
        return egress_attestation

    async def attest_workload_egress(**kwargs):
        assert kwargs["attachment_identity"] == docker_state["container"]
        assert kwargs["expected_image_ref"] == image_ref
        return {
            "profileRef": OMNIGENT_EGRESS_PROFILE.ref,
            "attachmentIdentity": kwargs["attachment_identity"],
            "enforced": True,
        }

    monkeypatch.setattr(
        "moonmind.omnigent.realizers.deployment_adapters.attest_docker_egress",
        attest_egress,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.deployment_adapters.attest_docker_workload_egress",
        attest_workload_egress,
    )

    host_class = get_host_class(plan.payload.hostClassRef)
    host_entry = next(
        entry
        for entry in host_class.declaredHarnessImplementations
        if entry.harnessId == plan.payload.harnessId
    )

    async def list_hosts(_client):
        capabilities = {
            capability: True
            for capability in plan.payload.classAdmissionDecision.get("required", [])
        }
        capabilities.update({"mountedSkills": True, "restricted-egress": True})
        raw = {
            "hostId": "omnigent-host:default-product-path",
            "hostClassRef": host_class.ref,
            "hostImageRef": host_class.imageRef,
            "omnigentVersion": host_class.omnigentVersion,
            "omnigentBuildDigest": host_class.omnigentBuildDigest,
            "harnessId": plan.payload.harnessId,
            "harnessImplementation": implementation_payload,
            "runtimeDependencies": [
                dict(dependency)
                for dependency in host_entry.runtimeDependencies
            ],
            "configured": True,
            "capabilities": capabilities,
            "architecture": plan.payload.supportIdentity.architecture,
            "attestationGeneration": 1,
            "observedAt": datetime.now(UTC).isoformat(),
        }
        attestation = HostHarnessAttestation.model_validate(raw)
        raw["attestationRef"] = compute_attestation_ref(attestation)
        return [
            {
                "id": "omnigent-host:default-product-path",
                "name": docker_state["container"],
                "status": "online",
                "attestation": raw,
            }
        ]

    async def get_model_options(_client, host_id):
        assert host_id == "omnigent-host:default-product-path"
        return {"models": [{"qualifiedId": "provider/model"}]}

    monkeypatch.setattr(
        "moonmind.workflows.adapters.omnigent_client.OmnigentHttpClient.list_hosts",
        list_hosts,
    )
    monkeypatch.setattr(
        "moonmind.workflows.adapters.omnigent_client.OmnigentHttpClient.get_host_model_options",
        get_model_options,
    )

    observed_driver_requests: list[AgentExecutionRequest] = []

    async def execute_session(request, **_kwargs):
        observed_driver_requests.append(request)
        return AgentRunResult(
            summary="default registry completed",
            metadata={"omnigentSessionId": "provider-session:default-product-path"},
        )

    monkeypatch.setattr(
        "moonmind.omnigent.realizers.composition.run_omnigent_execution",
        execute_session,
    )

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=correlation_id,
        idempotencyKey=idempotency_key,
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            }
        },
        parameters={"executionPlanRef": plan.planRef},
    )
    reset_default_registry()
    try:
        registry = get_default_registry()
        default_realizer = registry.require("generic-omnigent-host@1")
        default_realizer._session_factory = pg_store._session_factory
        result = await _try_generic_realizer_dispatch(
            request,
            plan_store=plan_store,
            realizer_registry=registry,
        )
    finally:
        reset_default_registry()

    assert result is not None and result.summary == "default registry completed"
    assert result.metadata["executionPlanRef"] == plan.planRef
    assert result.metadata["supportCombinationIdentity"]["identityComplete"] is True
    assert observed_driver_requests[0].parameters["omnigent"]["session"]["hostId"] == (
        "omnigent-host:default-product-path"
    )
    assert docker_state["launched"] is True
    assert docker_state["removed"] is True
    assert released_leases == ["provider-lease:default-product-path"]

    binding = await DbRuntimeBindingStore(pg_store._session_factory).latest_for_plan(
        plan.planRef
    )
    assert binding is not None
    assert binding.providerLeases["primary-model"].credentialGeneration == 6
    assert binding.omnigentHostId == "omnigent-host:default-product-path"
    assert binding.hostLeaseGeneration == 1
    assert binding.omnigentSessionId == "provider-session:default-product-path"
    assert binding.hostHarnessAttestationRef
    assert binding.exactHostCapabilityDecisionRef
    assert binding.modelOptionAttestationRef

    async with pg_store._session_factory() as session:
        host_lease = await session.get(
            OmnigentHostLeaseRecordV2, binding.hostLeaseRef
        )
        credential_record = (
            await session.execute(
                select(OmnigentCredentialRuntimeRecord)
            )
        ).scalars().one()
    assert host_lease is not None and host_lease.status == "cleaned"
    assert host_lease.omnigent_host_id == "omnigent-host:default-product-path"
    assert credential_record.credential_generation == 6
    credential_root = tmp_path / "runtime" / "credentials"
    assert not any(credential_root.rglob("auth.json"))
    assert "integration-only-opencode-key" not in json.dumps(
        result.model_dump(by_alias=True, mode="json")
    )
