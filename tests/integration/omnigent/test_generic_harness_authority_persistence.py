"""Production persistence boundary for generic harness facade authority."""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentBridgeSession,
    ProviderProfileAuthState,
)
from api_service.services.omnigent_policies import bootstrap_document
from moonmind.omnigent.bridge_store import (
    OmnigentBridgeSessionStore,
    OmnigentIdempotencyError,
)
from moonmind.omnigent.effective_capabilities import (
    CAPABILITY_NAMES,
    resolve_bridge_row_capabilities,
)
from moonmind.omnigent.generic_opencode_runtime import (
    GenericHostRuntimeObservation,
    build_generic_harness_authority,
)
from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    compute_attestation_ref,
)
from moonmind.omnigent.harness_platform.capabilities import (
    ClassAdmissionDecision,
    ExactHostCapabilityDecision,
    compute_class_admission_ref,
    compute_exact_host_capability_decision_ref,
)
from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.host_classes import get_host_class
from moonmind.omnigent.harness_platform.runtime_binding import create_runtime_binding
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import (
    OmnigentOAuthHostError,
    OmnigentOAuthHostRepository,
)
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
    compile_persisted_effective_launch_for_intent,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.security.egress import (
    EGRESS_CONFIG_DIGEST,
    ENFORCER_IMPLEMENTATION,
    EgressAttestation,
    OMNIGENT_EGRESS_PROFILE,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.activities import (
    omnigent_session_activities,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]


def _request(
    *,
    execution_plan: dict | None = None,
    harness_implementation: dict | None = None,
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="generic-authority-workflow",
        idempotencyKey="generic-authority-session",
        omnigentExecutionPlan=execution_plan,
        omnigentHarnessImplementation=harness_implementation,
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "generic-authority-workspace",
            }
        },
    )


def _preflight_evidence(
    *,
    observed_at: datetime | None = None,
    host_binding_ref: str = "host-binding:1",
    host_lease_ref: str = "host-lease:1",
    host_lease_generation: int = 1,
    provider_profile_id: str = "provider-1",
    provider_lease_ref: str = "provider-lease-1",
    credential_generation: int = 4,
    session_id: str | None = "provider-session",
    launch_policy_ref: str = "codex-on-demand@1",
    policy_snapshot_ref: str = "policy:sha256:" + "6" * 64,
    host_image_ref: str = "example.invalid/host@sha256:" + "8" * 64,
) -> dict:
    """Build exact-host evidence only through production schema constructors."""

    implementation = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "e" * 64,
            "pluginEntryPoint": None,
        }
    )
    plan = create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "harness-catalog:sha256:" + "2" * 64,
            "harnessId": "codex-native",
            "harnessImplementationRef": implementation.implementation_ref(),
            "agentSource": {"kind": "upstream", "upstreamId": "agent-1"},
            "credentialBindingSetRef": "credential-bindings:sha256:" + "3" * 64,
            "credentialBindings": {
                "primary-model": {
                    "providerProfileRef": provider_profile_id,
                    "materializerRef": "codex-oauth-home@1",
                }
            },
            "hostClassRef": "omnigent-codex-current@1",
            "launchPolicyRef": launch_policy_ref,
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": "openai/test",
                "effort": None,
                "routeRef": "codex",
                "normalizedOptions": {},
                "modelConfigDigest": "sha256:" + "4" * 64,
            },
            "resolvedSkills": {},
            "classAdmissionDecision": {
                "requiredSatisfied": ["interrupt"],
                "preferredSatisfied": [],
                "degraded": [],
                "unknown": [],
            },
            "runtimeValidationRequirements": ["exact-harness-implementation"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "5" * 64,
            "policySnapshotRef": policy_snapshot_ref,
            "supportCombinationKey": "support:sha256:" + "7" * 64,
        }
    )
    attestation_payload = {
        "hostId": "host-1",
        "hostClassRef": "omnigent-codex-current@1",
        "hostImageRef": host_image_ref,
        "omnigentVersion": "1.0.0",
        "omnigentBuildDigest": "sha256:" + "9" * 64,
        "harnessId": "codex-native",
        "harnessImplementation": implementation.model_dump(
            by_alias=True, mode="json"
        ),
        "runtimeDependencies": [],
        "configured": True,
        "capabilities": {"interrupt": True},
        "attestationGeneration": host_lease_generation,
        "observedAt": observed_at or datetime.now(UTC),
    }
    attestation = HostHarnessAttestation.model_validate(attestation_payload)
    attestation_payload["attestationRef"] = compute_attestation_ref(attestation)
    attestation = HostHarnessAttestation.model_validate(attestation_payload)
    class_decision = ClassAdmissionDecision.model_validate(
        plan.payload.classAdmissionDecision
    )
    exact_decision = ExactHostCapabilityDecision.model_validate(
        {
            "classAdmissionRef": compute_class_admission_ref(class_decision),
            "exactHostAttested": True,
            "requiredSatisfied": ["interrupt"],
            "missingRequired": [],
            "degraded": [],
        }
    )
    decision_ref = compute_exact_host_capability_decision_ref(exact_decision)
    runtime_binding = create_runtime_binding(
        executionPlanRef=plan.planRef,
        providerLeases={
            "primary-model": {
                "providerProfileRef": provider_profile_id,
                "providerLeaseRef": provider_lease_ref,
                "credentialGeneration": credential_generation,
                "credentialRuntimeRef": "codex_auth_volume",
            }
        },
        hostBindingRef=host_binding_ref,
        hostLeaseRef=host_lease_ref,
        hostLeaseGeneration=host_lease_generation,
        omnigentHostId="host-1",
        hostHarnessAttestationRef=attestation.attestationRef,
        exactHostCapabilityDecisionRef=decision_ref,
        omnigentSessionId=session_id,
    )
    return {
        "executionPlan": plan.model_dump(by_alias=True, mode="json"),
        "runtimeBinding": runtime_binding.model_dump(by_alias=True, mode="json"),
        "hostHarnessAttestation": attestation.model_dump(
            by_alias=True, mode="json"
        ),
        "exactHostCapabilityDecision": exact_decision.model_dump(
            by_alias=True, mode="json"
        ),
    }


def _generic_harness_authority() -> dict:
    evidence = _preflight_evidence()
    return build_generic_harness_authority(
        execution_plan=evidence["executionPlan"],
        runtime_binding=evidence["runtimeBinding"],
        host_attestation=evidence["hostHarnessAttestation"],
        exact_host_decision=evidence["exactHostCapabilityDecision"],
    )


def _launch(authority: dict) -> dict:
    plan = authority["executionPlan"]
    grants = dict.fromkeys(CAPABILITY_NAMES, True)
    return {
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "executionProfileRef": "agent-profile://p/versions/7",
        "executionProfileDigest": "sha256:agent",
        "launchPolicyRef": "policy://launch/3",
        "executionPlanRef": plan["planRef"],
        "executionRealizerRef": "generic-omnigent-host@1",
        "agentProfileCapabilities": grants,
        "capabilities": grants,
        "sessionStateCapabilities": grants,
        "policyAuthority": {
            "policyId": "generic-launch",
            "policyVersion": 1,
            "policyRef": "policy://launch/3",
            "policyDigest": "sha256:policy",
            "snapshotRef": "artifact://policy",
            "validation": {"valid": True},
        },
    }


def _with_launch_digest(payload: dict) -> dict:
    launch = dict(payload)
    launch.pop("snapshotRef", None)
    canonical = json.dumps(launch, sort_keys=True, separators=(",", ":"))
    launch["snapshotRef"] = "omnigent-launch:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    return launch


def _production_launch_and_evidence(
) -> tuple[dict, dict, dict, AgentExecutionRequest]:
    host_class = get_host_class("omnigent-codex-current@1")
    policy = compile_policy_snapshot(
        policy_id="codex-on-demand",
        version=1,
        document=bootstrap_document(
            host_mode="on_demand_docker",
            execution_profile_ref="omnigent-codex@1",
            server_image_ref="example.invalid/server@sha256:" + "1" * 64,
            host_image_ref=host_class.imageRef,
        ),
        validation={"valid": True, "diagnostics": []},
    )
    evidence = _preflight_evidence(
        launch_policy_ref=policy["policyRef"],
        policy_snapshot_ref=policy["snapshotRef"],
        host_image_ref=host_class.imageRef,
    )
    request = _request(
        execution_plan=evidence["executionPlan"],
        harness_implementation=evidence["hostHarnessAttestation"][
            "harnessImplementation"
        ],
    )
    launch = compile_persisted_effective_launch_for_intent(
        policy,
        request=request,
        provider_profile_id="provider-1",
    )
    return policy, launch, evidence, request


def _host_binding(launch: dict | None) -> OmnigentOAuthHostBinding:
    return OmnigentOAuthHostBinding(
        bindingRef="host-binding:1",
        providerProfileId="provider-1",
        endpointRef="default",
        harness="codex-native",
        credentialMountRef=CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId="provider-1",
                runtimeId="codex_cli",
                providerId="openai",
                volumeRef="codex_auth_volume",
                credentialGeneration=4,
                ownerUserId="user-1",
            ),
            targetPath="/home/app/.codex",
            runtimeUid=1000,
            runtimeGid=1000,
        ),
        hostLaunchProfileRef="codex-on-demand@1",
        executionProfileRef="omnigent-codex@1",
        launchPolicyRef="codex-on-demand@1",
        effectiveLaunchSnapshot=launch,
    )


async def test_production_intent_compiler_binds_generic_plan_before_host_preparation(
) -> None:
    policy, launch, evidence, request = _production_launch_and_evidence()

    assert launch["executionPlanRef"] == evidence["executionPlan"]["planRef"]
    assert launch["executionRealizerRef"] == "generic-omnigent-host@1"
    assert launch["genericHarnessAttachContract"] == {
        "schemaVersion": "moonmind.omnigent-generic-harness-attach.v1",
        "hostCatalogContract": "omnigent.http.host-catalog.v1",
        "executionPlan": evidence["executionPlan"],
        "harnessImplementation": evidence["hostHarnessAttestation"][
            "harnessImplementation"
        ],
    }
    assert compile_persisted_effective_launch_for_intent(
        policy,
        request=AgentExecutionRequest.model_validate(
            request.model_dump(by_alias=True, mode="json", exclude_none=True)
        ),
        provider_profile_id="provider-1",
    ) == launch


@pytest.mark.parametrize(
    "missing_field",
    ["omnigentExecutionPlan", "omnigentHarnessImplementation"],
)
async def test_production_intent_compiler_rejects_partial_generic_authority(
    missing_field: str,
) -> None:
    policy, _launch, evidence, _request_value = _production_launch_and_evidence()
    intent_fields = {
        "execution_plan": evidence["executionPlan"],
        "harness_implementation": evidence["hostHarnessAttestation"][
            "harnessImplementation"
        ],
    }
    intent_fields.pop(
        "execution_plan"
        if missing_field == "omnigentExecutionPlan"
        else "harness_implementation"
    )
    request = _request(**intent_fields)

    with pytest.raises(ValueError, match="requires both execution plan"):
        compile_persisted_effective_launch_for_intent(
            policy,
            request=request,
            provider_profile_id="provider-1",
        )


async def test_profile_bound_coordinator_rejects_partial_planner_authority_before_lease(
) -> None:
    policy, _launch, evidence, _request_value = _production_launch_and_evidence()
    request = _request(
        execution_plan=evidence["executionPlan"],
    ).model_copy(update={"execution_profile_ref": "provider-1"})
    lease_client = SimpleNamespace(
        acquire_execution_lease=AsyncMock(),
        release_lease=AsyncMock(),
    )
    run_store = SimpleNamespace(
        get_or_create=AsyncMock(return_value=SimpleNamespace()),
        record_lifecycle_event=AsyncMock(),
    )
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=lease_client,
        host_repository=SimpleNamespace(
            get_binding_for_profile=AsyncMock(return_value=_host_binding(None))
        ),
        host_runtime=SimpleNamespace(),
        run_store=run_store,
        execution_runner=AsyncMock(),
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
            credential_source="oauth_volume",
            runtime_materialization_mode="oauth_home",
            volume_ref="codex_auth_volume",
            volume_mount_path="/home/app/.codex",
            secret_refs={},
            command_behavior={},
        )
    )
    coordinator._resolve_policy_snapshot = AsyncMock(  # type: ignore[method-assign]
        return_value=policy
    )

    with pytest.raises(ValueError, match="requires both execution plan"):
        await coordinator.execute(request)

    lease_client.acquire_execution_lease.assert_not_awaited()
    assert any(
        call.kwargs.get("event_type") == "policy_authority_resolution"
        and call.kwargs.get("status") == "failed"
        for call in run_store.record_lifecycle_event.await_args_list
    )


async def test_planner_intent_rejects_raw_credentials_and_wrong_profile() -> None:
    policy, _launch, evidence, _request_value = _production_launch_and_evidence()
    payload = copy.deepcopy(evidence["executionPlan"]["payload"])
    payload["model"]["normalizedOptions"] = {"apiKey": "must-not-persist"}
    leaked_plan = create_execution_plan_envelope(payload).model_dump(
        by_alias=True, mode="json"
    )

    with pytest.raises(ValueError, match="must not contain raw credential keys"):
        _request(
            execution_plan=leaked_plan,
            harness_implementation=evidence["hostHarnessAttestation"][
                "harnessImplementation"
            ],
        )

    with pytest.raises(ValueError, match="does not select the launch Provider Profile"):
        compile_persisted_effective_launch_for_intent(
            policy,
            request=_request(
                execution_plan=evidence["executionPlan"],
                harness_implementation=evidence["hostHarnessAttestation"][
                    "harnessImplementation"
                ],
            ),
            provider_profile_id="different-provider",
        )


async def test_production_intent_compiler_keeps_existing_codex_launch_unchanged(
) -> None:
    policy, _launch, _evidence, _request_value = _production_launch_and_evidence()

    launch = compile_persisted_effective_launch_for_intent(
        policy,
        request=_request(),
        provider_profile_id="provider-1",
    )

    assert "executionPlanRef" not in launch
    assert "executionRealizerRef" not in launch
    assert "genericHarnessAttachContract" not in launch


def _host_lease() -> OmnigentHostLease:
    now = datetime.now(UTC)
    return OmnigentHostLease(
        leaseId="host-lease:1",
        providerProfileId="provider-1",
        providerLeaseId="provider-lease-1",
        bindingRef="host-binding:1",
        credentialGeneration=4,
        containerName="generic-authority-host",
        omnigentHostId="host-1",
        omnigentSessionId="provider-session",
        status="ready",
        acquiredAt=now,
        lastHeartbeatAt=now,
        expiresAt=now + timedelta(hours=1),
    )


def _egress_attestation() -> EgressAttestation:
    return EgressAttestation(
        profileRef=OMNIGENT_EGRESS_PROFILE.ref,
        profileDigest=OMNIGENT_EGRESS_PROFILE.digest,
        enforcerImplementation=ENFORCER_IMPLEMENTATION,
        backendRef="container-backend",
        networkRef=OMNIGENT_EGRESS_PROFILE.network_ref,
        gatewayRef=OMNIGENT_EGRESS_PROFILE.gateway_ref,
        appliedRuleDigest="sha256:" + "b" * 64,
        configDigest=EGRESS_CONFIG_DIGEST,
        gatewayImageDigest="sha256:" + "c" * 64,
        healthResult="healthy",
        validatedAt=datetime.now(UTC),
        validationResult="passed",
    )


def _runtime_observation(
    *, validated_at: datetime | None = None
) -> GenericHostRuntimeObservation:
    host_class = get_host_class("omnigent-codex-current@1")
    return GenericHostRuntimeObservation(
        workloadImageRef=host_class.imageRef,
        workloadImageDigest="sha256:" + "2" * 64,
        architecture="amd64",
        attachmentIdentity="generic-authority-host",
        networkIdentity="network-1",
        endpointIdentity="endpoint-1",
        validationResult="passed",
        validatedAt=validated_at or datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def persisted(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/authority.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(factory)
    authority = _generic_harness_authority()
    request = _request()
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="default",
        provider_profile_id="provider-1",
        provider_lease_id="provider-lease-1",
        credential_generation=4,
        host_binding_ref="host-binding:1",
        host_lease_ref="host-lease:1",
        omnigent_host_id="host-1",
        effective_launch_snapshot=_launch(authority),
    )
    await store.attach_session(request.idempotency_key, "provider-session")
    row = await store.bind_harness_authority(
        request=request,
        harness_authority=authority,
    )
    row = await store.record_session_created(
        request.idempotency_key,
        session_id="provider-session",
        capabilities=dict.fromkeys(CAPABILITY_NAMES, True),
        session_status="active",
    )
    yield store, factory, request, authority, row
    await engine.dispose()


async def test_production_writer_feeds_facade_capability_authority(persisted) -> None:
    store, _factory, request, authority, row = persisted

    assert row.metadata_["harnessAuthority"] == authority
    retried = await store.bind_harness_authority(
        request=request,
        harness_authority=copy.deepcopy(authority),
    )
    assert retried.metadata_["harnessAuthority"] == authority
    decision = resolve_bridge_row_capabilities(
        retried,
        caller_capabilities=dict.fromkeys(CAPABILITY_NAMES, True),
    )
    assert all(decision.capabilities.values()), decision.disabled_reasons


async def test_production_writer_rejects_tampered_authority(persisted) -> None:
    store, _factory, request, authority, _row = persisted
    tampered = copy.deepcopy(authority)
    tampered["hostHarnessAttestation"]["hostId"] = "foreign-host"

    with pytest.raises(OmnigentIdempotencyError, match="harness_authority_invalid"):
        await store.bind_harness_authority(
            request=request,
            harness_authority=tampered,
        )


async def test_persisted_authority_fails_closed_after_host_fence_changes(
    persisted,
) -> None:
    _store, factory, _request_value, _authority, row = persisted
    async with factory() as session:
        stored = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        stored.omnigent_host_id = "replacement-host"
        await session.commit()
        await session.refresh(stored)
        session.expunge(stored)

    decision = resolve_bridge_row_capabilities(
        stored,
        caller_capabilities=dict.fromkeys(CAPABILITY_NAMES, True),
    )
    assert set(decision.capabilities.values()) == {False}
    assert set(decision.disabled_reasons.values()) == {"harness_authority_invalid"}


async def test_ensure_host_activity_persists_real_preflight_authority_for_facade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise preflight -> Activity -> bridge -> facade without store seeding."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/activity.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    policy, launch, _evidence, request = _production_launch_and_evidence()
    binding = _host_binding(None)
    lease = _host_lease()
    canonical_session = SimpleNamespace(
        session_id="canonical-session",
        revision=1,
        fencing_generation=0,
        host_lease_generation=1,
        provider_profile_id="provider-1",
        moonmind_workflow_id=request.correlation_id,
        moonmind_agent_run_id="agent-run-1",
        step_execution_id="step-1",
        metadata={
            "providerLeaseRef": "provider-lease-1",
            "providerRuntimeId": "codex_cli",
        },
    )

    class FakeSessions:
        async def get(self, session_id: str):
            assert session_id == canonical_session.session_id
            return canonical_session

        async def bind_runtime_authority(self, session_id: str, **kwargs):
            assert session_id == canonical_session.session_id
            assert kwargs["host_lease_generation"] == 1
            canonical_session.revision += 1
            canonical_session.metadata.update(kwargs["metadata_patch"])
            return canonical_session

    class FakeControlPlaneStore:
        def __init__(self, _factory) -> None:
            self.sessions = FakeSessions()

        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(sessions=self.sessions)

    class FakeHostRepository(OmnigentOAuthHostRepository):
        def __init__(self, _factory) -> None:
            self.lease = lease
            self.binding = binding

        async def get_binding_for_profile(self, profile_id: str):
            assert profile_id == "provider-1"
            return self.binding

        async def create_or_update_static_binding(self, **kwargs):
            assert kwargs["profile_id"] == "provider-1"
            compiled_launch = kwargs["effective_launch_snapshot"]
            assert compiled_launch["executionPlanRef"] == _evidence[
                "executionPlan"
            ]["planRef"]
            assert compiled_launch["genericHarnessAttachContract"]
            self.binding = self.binding.model_copy(
                update={"effective_launch_snapshot": compiled_launch}
            )
            return self.binding

        async def create_or_get_host_lease(self, **kwargs):
            assert kwargs["provider_lease_id"] == "provider-lease-1"
            return self.lease

        async def transition_host_lease(
            self,
            lease_id: str,
            *,
            expected_status: str,
            new_status: str,
            fields: dict | None = None,
        ):
            assert lease_id == self.lease.lease_id
            assert self.lease.status == expected_status
            updates = {"status": new_status}
            if fields:
                if "omnigent_host_id" in fields:
                    updates["omnigent_host_id"] = fields["omnigent_host_id"]
                if "bridge_session_id" in fields:
                    updates["bridge_session_id"] = fields["bridge_session_id"]
            self.lease = self.lease.model_copy(update=updates)
            return self.lease

    class FakePolicyService:
        def __init__(self, _session) -> None:
            pass

        async def resolve_runtime_snapshot(self, policy_ref: str):
            assert policy_ref == "codex-on-demand@1"
            return policy

    class FakeHttpClient:
        async def aclose(self) -> None:
            pass

    host_catalog = {
        "host_id": "host-1",
        "name": "generic-authority-host",
        "owner": "user-1",
        "status": "online",
        "sandbox_provider": None,
        "configured_harnesses": {"codex-native": True},
        "gateway_inference": None,
    }

    async def stock_host_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/hosts"
        return httpx.Response(200, json={"hosts": [host_catalog]})

    stock_client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(stock_host_handler),
    )
    runtime = OmnigentOAuthHostRuntime(client=stock_client, workspace_root=tmp_path)
    runtime._prepare_skill_projection = AsyncMock(  # type: ignore[method-assign]
        return_value=tmp_path / "skills"
    )
    runtime_egress = _egress_attestation()
    runtime._attest_egress = AsyncMock(  # type: ignore[method-assign]
        return_value=runtime_egress
    )
    runtime._prepare_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=tmp_path / "workspace"
    )
    runtime._attest_server_image = AsyncMock(  # type: ignore[method-assign]
        return_value={}
    )
    runtime._align_workspace_ownership = MagicMock()  # type: ignore[method-assign]
    runtime._resolve_daemon_workspace_root = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    runtime._prepare_daemon_runtime_scripts = MagicMock(  # type: ignore[method-assign]
        return_value=tmp_path / "runtime-scripts"
    )
    runtime._launch_on_demand = AsyncMock()  # type: ignore[method-assign]
    runtime._resolve_workload_attachment_identity = AsyncMock(  # type: ignore[method-assign]
        return_value="generic-authority-host"
    )
    runtime._attest_launched_workload_egress = AsyncMock(  # type: ignore[method-assign]
        return_value=_runtime_observation(
            validated_at=runtime_egress.validated_at
        ).model_dump(
            by_alias=True, mode="json"
        )
    )
    runtime._publish_host_egress_evidence = AsyncMock(  # type: ignore[method-assign]
        side_effect=["artifact://egress-pending", "artifact://egress-attested"]
    )
    runtime._exec_check = AsyncMock()  # type: ignore[method-assign]
    runtime._exec_tools_check = AsyncMock()  # type: ignore[method-assign]
    runtime._preflight_mounted_tools = AsyncMock(  # type: ignore[method-assign]
        return_value={}
    )
    direct_kwargs = {
        "binding": binding,
        "host_lease": lease,
        "workspace_key": "generic-authority-workflow:step-1",
        "workspace_locator": {
            "kind": "sandbox",
            "workspaceId": "generic-authority-workspace",
        },
        "current_workflow_id": request.correlation_id,
        "current_step_execution_id": "step-1",
        "effective_launch": launch,
        "host_lease_generation": 1,
    }

    missing_contract = dict(launch)
    missing_contract.pop("genericHarnessAttachContract")
    missing_contract = _with_launch_digest(missing_contract)
    tampered_plan = copy.deepcopy(launch)
    tampered_plan["genericHarnessAttachContract"]["executionPlan"]["payload"][
        "endpointRef"
    ] = "tampered-endpoint"
    tampered_plan = _with_launch_digest(tampered_plan)
    for invalid_launch in (missing_contract, tampered_plan):
        with pytest.raises(OmnigentOAuthHostError) as exc_info:
            await runtime.prepare_host(
                **{**direct_kwargs, "effective_launch": invalid_launch}
            )
        assert exc_info.value.code == "OMNIGENT_HARNESS_AUTHORITY_INVALID"

    runtime._attest_launched_workload_egress.return_value = (
        _runtime_observation(
            validated_at=datetime.now(UTC) - timedelta(minutes=11)
        ).model_dump(by_alias=True, mode="json")
    )
    with pytest.raises(OmnigentOAuthHostError) as exc_info:
        await runtime.prepare_host(**direct_kwargs)
    assert exc_info.value.code == "OMNIGENT_HARNESS_AUTHORITY_INVALID"
    runtime._attest_launched_workload_egress.return_value = (
        _runtime_observation(
            validated_at=runtime_egress.validated_at
        ).model_dump(by_alias=True, mode="json")
    )

    codex_launch = dict(launch)
    codex_launch.pop("executionPlanRef")
    codex_launch.pop("executionRealizerRef")
    codex_launch.pop("genericHarnessAttachContract")
    codex_launch = _with_launch_digest(codex_launch)
    codex_preflight = await runtime.prepare_host(
        **{**direct_kwargs, "effective_launch": codex_launch}
    )
    assert "harnessAuthority" not in codex_preflight

    async def fake_claim(_activity_request):
        return SimpleNamespace(status="claimed"), True

    async def fake_settle(_activity_request, **_kwargs):
        return {"commandId": "command-1", "outcome": "applied"}

    async def fake_load_intent(_activity_request):
        return request

    async def fake_client_context():
        return FakeHttpClient(), object()

    import api_service.db.base as db_base
    import api_service.services.omnigent_policies as policy_module
    import moonmind.omnigent.control_plane as control_plane_module
    import moonmind.omnigent.oauth_host_runtime as runtime_module
    import moonmind.omnigent.oauth_hosts as hosts_module

    monkeypatch.setattr(db_base, "async_session_maker", factory)
    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        FakeControlPlaneStore,
    )
    monkeypatch.setattr(policy_module, "OmnigentPolicyService", FakePolicyService)
    monkeypatch.setattr(hosts_module, "OmnigentOAuthHostRepository", FakeHostRepository)
    monkeypatch.setattr(
        runtime_module,
        "OmnigentOAuthHostRuntime",
        lambda *, client: runtime,
    )
    monkeypatch.setattr(omnigent_session_activities, "_claim_command", fake_claim)
    monkeypatch.setattr(omnigent_session_activities, "_settle_command", fake_settle)
    monkeypatch.setattr(
        omnigent_session_activities,
        "_load_intent_request",
        fake_load_intent,
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_omnigent_client_context",
        fake_client_context,
    )
    activity_payload = {
        "sessionId": canonical_session.session_id,
        "compiledExecutionIntentRef": "artifact://intent",
        "compiledExecutionIntentDigest": "sha256:" + "d" * 64,
        "expectedRevision": 1,
        "fencingGeneration": 0,
        "commandId": "command-1",
    }
    result = await omnigent_session_activities.omnigent_ensure_host_activity(
        activity_payload
    )
    assert result["hostLeaseGeneration"] == 1

    bridge_store = OmnigentBridgeSessionStore(factory)
    persisted = await bridge_store.get_existing(request.idempotency_key)
    assert persisted is not None
    assert persisted.metadata_["harnessAuthority"]["runtimeBinding"][
        "hostLeaseGeneration"
    ] == 1
    first_authority = copy.deepcopy(persisted.metadata_["harnessAuthority"])
    retry_result = await omnigent_session_activities.omnigent_ensure_host_activity(
        activity_payload
    )
    assert retry_result["hostLeaseGeneration"] == 1
    persisted = await bridge_store.get_existing(request.idempotency_key)
    assert persisted is not None
    assert persisted.metadata_["harnessAuthority"] == first_authority
    assert runtime._publish_host_egress_evidence.await_count == 2

    await bridge_store.attach_session(request.idempotency_key, "provider-session")
    persisted = await bridge_store.record_session_created(
        request.idempotency_key,
        session_id="provider-session",
        capabilities=dict.fromkeys(CAPABILITY_NAMES, True),
        session_status="active",
    )
    facade = resolve_bridge_row_capabilities(
        persisted,
        caller_capabilities=dict.fromkeys(CAPABILITY_NAMES, True),
    )
    assert facade.capabilities["sendMessage"] is True, facade.disabled_reasons
    assert "harness_authority_invalid" not in set(facade.disabled_reasons.values())

    await engine.dispose()
