"""Normal-product authority handoffs for MoonLadderStudios/MoonMind#3701."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform.admission import (
    compile_normal_product_execution_plan,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.stores import (
    InMemoryExecutionPlanStore,
    InMemoryRuntimeBindingStore,
)
from moonmind.omnigent.host_runtime import GenericOmnigentHostRuntime
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.realizers.codex_profile_bound import CodexProfileBoundRealizer
from moonmind.omnigent.realizers.runtime_authority import (
    ProviderProfileRuntimeAuthority,
)
from moonmind.omnigent.realizers.deployment_adapters import (
    DeploymentGenericHostServices,
    TrustedCredentialMaterializer,
)
from moonmind.omnigent.realizers.registry import (
    get_default_registry,
    reset_default_registry,
)
from moonmind.omnigent.harness_platform.support import (
    compute_support_combination_key,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _try_generic_realizer_dispatch,
)
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _session_execution_authority_metadata,
)


def _snapshot(*, harness: str = "opencode-native") -> dict[str, object]:
    implementation = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + ("a" if harness == "opencode-native" else "e") * 64,
            "pluginEntryPoint": None,
        }
    )
    observed_at = datetime.now(UTC).isoformat()
    return {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": f"{harness}-profile",
        "version": 7,
        "digest": "sha256:" + "1" * 64,
        "providerProfileRef": f"{harness}-provider",
        "executionProfileRef": f"{harness}@1",
        "launchPolicyRef": (
            "omnigent-on-demand@1"
            if harness == "opencode-native"
            else "codex-on-demand@1"
        ),
        "agentId": f"{harness}-agent",
        "policyRef": "omnigent-policy:test",
        "document": {
            "schemaVersion": "moonmind.omnigent-agent-profile.v1",
            "endpointRef": "default",
            "bridgeMode": "proxy",
            "source": {"upstreamId": f"{harness}-agent", "upstreamVersion": "1.0.0"},
            "harness": harness,
            "requiredCapabilities": ["streaming"],
            "execution": {
                "defaultExecutionProfileRef": f"{harness}@1",
                "allowedLaunchPolicyRefs": [
                    (
                        "omnigent-on-demand@1"
                        if harness == "opencode-native"
                        else "codex-on-demand@1"
                    )
                ],
            },
            "providerRequirements": {
                "runtimeId": (
                    "opencode_go" if harness == "opencode-native" else "codex_cli"
                ),
                "credentialSource": (
                    "api_key" if harness == "opencode-native" else "oauth"
                ),
                "materializationMode": (
                    "generated_file" if harness == "opencode-native" else "oauth_home"
                ),
                "providerIds": [
                    "opencode" if harness == "opencode-native" else "openai"
                ],
            },
            "model": {"model": "provider/model", "settings": {}},
            "workspace": {"mutation": "allowed", "requiredCapabilities": []},
            "skills": [],
            "tools": [],
            "capture": {"stream": True, "evidence": True},
            "rag": {"initial": {}, "followUp": {}},
            "continuations": {"checkpoint": True, "branch": True, "remediation": True},
            "publish": {"mode": "none"},
            "policyRef": "omnigent-policy:test",
        },
        "upstreamSnapshot": {
            "id": f"{harness}-agent",
            "version": "1.0.0",
            "harness": harness,
            "catalogObservedAt": observed_at,
            "harnessImplementation": implementation.model_dump(
                by_alias=True, mode="json"
            ),
        },
        "validationResult": {"ready": True},
    }


def test_normal_product_plan_freezes_registered_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64,
    )
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(),
        workflow_parameters={
            "model": "provider/model",
            "effort": "high",
            "requiredCapabilities": ["streaming"],
            "workflow": {"skills": {"include": []}},
        },
        workflow_id="mm:normal-product",
    )

    assert plan.payload.harnessId == "opencode-native"
    assert plan.payload.executionRealizerRef == "generic-omnigent-host@1"
    assert plan.payload.hostClassRef == "omnigent-opencode@1"
    assert plan.payload.credentialBindings["primary-model"]["providerProfileRef"] == (
        "opencode-native-provider"
    )
    assert plan.payload.agentProfileSnapshotRef.startswith(
        "omnigent-agent-profile:sha256:"
    )
    assert plan.payload.supportIdentity is not None
    assert (
        plan.payload.supportIdentity.executionRealizerRef == "generic-omnigent-host@1"
    )
    serialized = plan.model_dump_json(by_alias=True)
    assert "providerLeaseRef" not in serialized
    assert "credentialGeneration" not in serialized
    assert "hostLeaseRef" not in serialized


def test_default_registry_composes_every_generic_host_production_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The shipped registry must be executable without test dependency injection."""

    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "OMNIGENT_GENERIC_RUNTIME_ROOT", str(tmp_path / "generic-runtime")
    )
    reset_default_registry()
    try:
        realizer = get_default_registry().require("generic-omnigent-host@1")
        dependencies = realizer._production_dependencies()

        assert isinstance(
            dependencies.runtime_authority, ProviderProfileRuntimeAuthority
        )
        materializer = dependencies.runtime_authority._credential_materializer
        assert isinstance(materializer, TrustedCredentialMaterializer)
        assert isinstance(dependencies.host_runtime, GenericOmnigentHostRuntime)

        services = dependencies.host_runtime._launcher
        assert isinstance(services, DeploymentGenericHostServices)
        assert dependencies.host_runtime._workspace_service is services
        assert dependencies.host_runtime._skill_service is services
        assert dependencies.host_runtime._egress_service is services
        assert dependencies.host_runtime._registration_waiter is services
        assert dependencies.host_runtime._image_attestor is services
        assert dependencies.host_runtime._cleanup_service is services
        assert dependencies.host_runtime._context_service is services
        assert services._credential_materializer is materializer
        dependencies.host_runtime.assert_ready()
    finally:
        reset_default_registry()


@pytest.mark.parametrize(
    ("harness", "expected_realizer", "expected_host_class"),
    [
        ("codex-native", "codex-profile-bound@1", "omnigent-codex-current@1"),
        (
            "opencode-native",
            "generic-omnigent-host@1",
            "omnigent-opencode@1",
        ),
    ],
)
def test_normal_product_records_admitted_support_identity_for_each_realizer(
    monkeypatch: pytest.MonkeyPatch,
    harness: str,
    expected_realizer: str,
    expected_host_class: str,
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64,
    )
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(harness=harness),
        workflow_parameters={"model": "provider/model", "workflow": {}},
        workflow_id=f"mm:support:{harness}",
    )

    identity = plan.payload.supportIdentity
    assert identity is not None
    assert identity.executionRealizerRef == expected_realizer
    assert identity.hostClassRef == expected_host_class
    assert identity.harnessImplementationRef == plan.payload.harnessImplementationRef
    assert identity.modelConfigDigest == plan.payload.modelConfig.modelConfigDigest
    # The dedicated OpenCode Host Class pins its vendor CLI.  The replay-
    # visible Codex Host Class still records an empty tuple until protected
    # exact-host evidence supplies the legacy vendor-runtime identity; do not
    # invent a version at admission.
    if harness == "opencode-native":
        assert identity.vendorRuntimeRefs
    else:
        assert identity.vendorRuntimeRefs == ()
    assert identity.materializerRefs
    assert identity.providerCompatibilityClass
    assert compute_support_combination_key(identity) == (
        plan.payload.supportCombinationKey
    )


@pytest.mark.asyncio
async def test_codex_realizer_returns_the_admitted_support_identity() -> None:
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(harness="codex-native"),
        workflow_parameters={"model": "gpt-5", "workflow": {}},
        workflow_id="mm:codex-support",
    )
    coordinator = SimpleNamespace(
        execute=AsyncMock(return_value=AgentRunResult(summary="codex completed"))
    )
    realizer = CodexProfileBoundRealizer(
        session_factory=SimpleNamespace(),
        coordinator_factory=lambda **_kwargs: coordinator,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-codex",
        idempotencyKey="idem-codex",
        parameters={"executionPlanRef": plan.planRef},
    )

    result = await realizer.execute(request, plan)

    assert result.summary == "codex completed"
    assert result.metadata["executionPlanRef"] == plan.planRef
    assert (
        result.metadata["supportCombinationIdentity"]["executionRealizerRef"]
        == "codex-profile-bound@1"
    )


@pytest.mark.asyncio
async def test_canonical_terminal_projection_names_plan_binding_and_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.harness_platform.stores import (
        DbExecutionPlanStore,
        DbRuntimeBindingStore,
    )

    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(harness="codex-native"),
        workflow_parameters={"model": "gpt-5", "workflow": {}},
        workflow_id="mm:terminal-support",
    )
    bindings = InMemoryRuntimeBindingStore()
    binding = await bindings.create_initial(
        execution_plan_ref=plan.planRef,
        provider_leases={
            "primary-model": {
                "providerProfileRef": "codex-native-provider",
                "providerLeaseRef": "provider-lease:terminal",
                "credentialGeneration": 2,
                "credentialRuntimeRef": "credential-runtime:terminal",
            }
        },
    )
    monkeypatch.setattr(
        DbExecutionPlanStore,
        "load",
        AsyncMock(return_value=plan),
    )
    monkeypatch.setattr(
        DbRuntimeBindingStore,
        "get",
        AsyncMock(return_value=binding),
    )

    metadata = await _session_execution_authority_metadata(
        SimpleNamespace(
            execution_plan_ref=plan.planRef,
            runtime_binding_ref=binding.runtimeBindingRef,
        )
    )

    assert metadata["executionPlanRef"] == plan.planRef
    assert metadata["runtimeBindingRef"] == binding.runtimeBindingRef
    assert metadata["supportCombinationIdentity"]["identityComplete"] is True


@pytest.mark.asyncio
async def test_generic_dispatch_loads_persisted_plan_without_replanning() -> None:
    store = InMemoryExecutionPlanStore()
    plan = OmnigentExecutionPlanEnvelope.model_validate(
        compile_normal_product_execution_plan(
            agent_profile_snapshot=_snapshot(harness="codex-native"),
            workflow_parameters={"model": "gpt-5", "workflow": {}},
            workflow_id="mm:dispatch",
        )
    )
    await store.persist(plan)
    realizer = SimpleNamespace(
        execute=AsyncMock(return_value=AgentRunResult(summary="ok"))
    )
    registry = SimpleNamespace(require=lambda ref: realizer)
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr",
        idempotencyKey="idem",
        parameters={"executionPlanRef": plan.planRef},
    )

    result = await _try_generic_realizer_dispatch(
        request,
        plan_store=store,
        realizer_registry=registry,
    )

    assert result is not None and result.summary == "ok"
    realizer.execute.assert_awaited_once_with(request, plan)


@pytest.mark.asyncio
async def test_generic_request_without_admitted_plan_fails_closed() -> None:
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr",
        idempotencyKey="idem",
        parameters={"omnigent": {"agentProfileV2": {"schemaVersion": "v2"}}},
    )

    result = await _try_generic_realizer_dispatch(
        request,
        plan_store=InMemoryExecutionPlanStore(),
        realizer_registry=SimpleNamespace(require=lambda _ref: None),
    )

    assert result is not None
    assert result.provider_error_code == "OMNIGENT_EXECUTION_PLAN_REQUIRED"


@pytest.mark.asyncio
async def test_generic_realizer_persists_generations_and_exact_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64,
    )
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(),
        workflow_parameters={"model": "provider/model", "workflow": {}},
        workflow_id="mm:binding",
    )
    binding_store = InMemoryRuntimeBindingStore()
    authority = SimpleNamespace(
        acquire=AsyncMock(
            return_value=SimpleNamespace(
                provider_leases={
                    "primary-model": {
                        "providerProfileRef": "opencode-native-provider",
                        "providerLeaseRef": "provider-lease:exact",
                        "credentialGeneration": 4,
                        "credentialRuntimeRef": "credential-runtime:exact",
                    }
                },
                credential_handles=[
                    {
                        "providerProfileRef": "opencode-native-provider",
                        "providerLeaseRef": "provider-lease:exact",
                        "credentialGeneration": 4,
                        "credentialRuntimeRef": "credential-runtime:exact",
                        "materializerRef": "opencode-auth-json@1",
                        "cleanupRef": "credential-cleanup:exact",
                    }
                ],
            )
        ),
        release=AsyncMock(),
    )
    host_runtime = SimpleNamespace(
        assert_ready=lambda: None,
        realize=AsyncMock(
            return_value={
                "hostId": "host-exact",
                "hostBindingRef": "host-binding:exact",
                "hostLeaseRef": "host-lease:exact",
                "hostLeaseGeneration": 8,
                "hostClassRef": "omnigent-opencode@1",
                "launchPolicyRef": "omnigent-on-demand@1",
                "workspacePath": "/workspaces/run",
            }
        ),
        cleanup=AsyncMock(),
    )
    driver = AsyncMock(return_value=AgentRunResult(summary="completed"))
    turn_commands = SimpleNamespace(
        claim=AsyncMock(
            return_value=SimpleNamespace(
                session_id="canonical-session",
                turn_attempt_id="turn-1",
                command_id="command-1",
                claim_token="claim-1",
                expected_session_revision=2,
                fencing_generation=1,
                owns_delivery=True,
            )
        ),
        bind_runtime_authority=AsyncMock(),
        settle=AsyncMock(),
    )
    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=binding_store,
        runtime_authority=authority,
        host_runtime=host_runtime,
        execution_driver=driver,
        turn_command_service=turn_commands,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr",
        idempotencyKey="idem",
        parameters={"executionPlanRef": plan.planRef},
    )

    result = await realizer.execute(request, plan)

    assert result.summary == "completed"
    enriched = driver.await_args.args[0]
    binding_ref = enriched.parameters["runtimeBindingRef"]
    binding = await binding_store.get(binding_ref)
    assert binding is not None
    assert binding.executionPlanRef == plan.planRef
    assert binding.providerLeases["primary-model"].credentialGeneration == 4
    assert binding.omnigentHostId == "host-exact"
    assert binding.hostLeaseGeneration == 8
    assert binding.cleanupAuthorityRefs == ("credential-cleanup:exact",)
    assert result.metadata["supportCombinationIdentity"] == {
        **plan.payload.supportIdentity.model_dump(by_alias=True, mode="json"),
        "supportCombinationKey": plan.payload.supportCombinationKey,
        "identityComplete": True,
    }
    authority.release.assert_awaited_once()
    host_runtime.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_generic_realizer_does_not_repeat_settled_turn_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64,
    )
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(),
        workflow_parameters={"model": "provider/model", "workflow": {}},
        workflow_id="mm:no-repeat",
    )
    authority = SimpleNamespace(acquire=AsyncMock(), release=AsyncMock())
    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=InMemoryRuntimeBindingStore(),
        runtime_authority=authority,
        host_runtime=SimpleNamespace(
            assert_ready=lambda: None,
            realize=AsyncMock(),
            cleanup=AsyncMock(),
        ),
        execution_driver=AsyncMock(),
        turn_command_service=SimpleNamespace(
            claim=AsyncMock(
                return_value=SimpleNamespace(
                    session_id="canonical-session", owns_delivery=False
                )
            )
        ),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr",
        idempotencyKey="idem",
        parameters={"executionPlanRef": plan.planRef},
    )

    with pytest.raises(HarnessPlatformError):
        await realizer.execute(request, plan)

    authority.acquire.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_realizer_requires_trusted_secret_materializer_before_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64,
    )
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(),
        workflow_parameters={"model": "provider/model", "workflow": {}},
        workflow_id="mm:materializer-readiness",
    )
    turn_commands = SimpleNamespace(claim=AsyncMock())
    authority = ProviderProfileRuntimeAuthority(
        session_factory=SimpleNamespace(),
        lease_client=SimpleNamespace(),
    )
    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=InMemoryRuntimeBindingStore(),
        runtime_authority=authority,
        host_runtime=SimpleNamespace(assert_ready=lambda: None),
        execution_driver=AsyncMock(),
        turn_command_service=turn_commands,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr",
        idempotencyKey="idem-materializer",
        parameters={"executionPlanRef": plan.planRef},
    )

    with pytest.raises(HarnessPlatformError) as exc_info:
        await realizer.execute(request, plan)

    assert exc_info.value.code == "OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE"
    turn_commands.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_realizer_parks_claim_when_authority_acquisition_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64,
    )
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(),
        workflow_parameters={"model": "provider/model", "workflow": {}},
        workflow_id="mm:acquire-failure",
    )
    turn_commands = SimpleNamespace(
        claim=AsyncMock(
            return_value=SimpleNamespace(
                session_id="canonical-session",
                turn_attempt_id="turn-acquire",
                command_id="command-acquire",
                claim_token="claim-acquire",
                expected_session_revision=2,
                fencing_generation=1,
                owns_delivery=True,
            )
        ),
        settle=AsyncMock(),
    )
    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=InMemoryRuntimeBindingStore(),
        runtime_authority=SimpleNamespace(
            acquire=AsyncMock(side_effect=RuntimeError("lease unavailable")),
            release=AsyncMock(),
        ),
        host_runtime=SimpleNamespace(assert_ready=lambda: None),
        execution_driver=AsyncMock(),
        turn_command_service=turn_commands,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr",
        idempotencyKey="idem-acquire-failure",
        parameters={"executionPlanRef": plan.planRef},
    )

    with pytest.raises(RuntimeError, match="lease unavailable"):
        await realizer.execute(request, plan)

    settle = turn_commands.settle.await_args.kwargs
    assert settle["idempotency_key"] == "idem-acquire-failure"
    assert settle["outcome"].value == "delivery_unknown"


@pytest.mark.asyncio
async def test_generic_realizer_retains_provider_authority_until_cleanup_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64,
    )
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(),
        workflow_parameters={"model": "provider/model", "workflow": {}},
        workflow_id="mm:cleanup-order",
    )
    authority = SimpleNamespace(
        acquire=AsyncMock(
            return_value=SimpleNamespace(
                provider_leases={
                    "primary-model": {
                        "providerProfileRef": "opencode-native-provider",
                        "providerLeaseRef": "provider-lease:exact",
                        "credentialGeneration": 4,
                        "credentialRuntimeRef": "credential-runtime:exact",
                    }
                },
                credential_handles=[],
            )
        ),
        release=AsyncMock(),
    )
    host_runtime = SimpleNamespace(
        assert_ready=lambda: None,
        realize=AsyncMock(
            return_value={
                "hostId": "host-exact",
                "hostBindingRef": "host-binding:exact",
                "hostLeaseRef": "host-lease:exact",
                "hostLeaseGeneration": 8,
                "hostClassRef": "omnigent-opencode@1",
                "launchPolicyRef": "omnigent-on-demand@1",
                "workspacePath": "/workspaces/run",
            }
        ),
        cleanup=AsyncMock(side_effect=RuntimeError("cleanup pending")),
    )
    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=InMemoryRuntimeBindingStore(),
        runtime_authority=authority,
        host_runtime=host_runtime,
        execution_driver=AsyncMock(return_value=AgentRunResult(summary="completed")),
        turn_command_service=SimpleNamespace(
            claim=AsyncMock(
                return_value=SimpleNamespace(
                    session_id="canonical-session",
                    turn_attempt_id="turn-cleanup",
                    command_id="command-cleanup",
                    claim_token="claim-cleanup",
                    expected_session_revision=2,
                    fencing_generation=1,
                    owns_delivery=True,
                )
            ),
            bind_runtime_authority=AsyncMock(),
            settle=AsyncMock(),
        ),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr",
        idempotencyKey="idem-cleanup",
        parameters={"executionPlanRef": plan.planRef},
    )

    result = await realizer.execute(request, plan)

    assert result.summary == "completed"
    host_runtime.cleanup.assert_awaited_once()
    authority.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_host_side_effects_receive_one_command_and_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "9" * 64,
    )
    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=_snapshot(),
        workflow_parameters={"model": "provider/model", "workflow": {}},
        workflow_id="mm:side-effect-fence",
    )
    from moonmind.omnigent.harness_platform.attestation import (
        HostHarnessAttestation,
        compute_attestation_ref,
    )
    from moonmind.omnigent.harness_platform.host_classes import get_host_class

    host_class = get_host_class(plan.payload.hostClassRef)
    host_entry = next(
        entry
        for entry in host_class.declaredHarnessImplementations
        if entry.harnessId == plan.payload.harnessId
    )
    raw_attestation = {
        "hostId": "host-exact",
        "hostClassRef": host_class.ref,
        "hostImageRef": host_class.imageRef,
        "omnigentVersion": host_class.omnigentVersion,
        "omnigentBuildDigest": host_class.omnigentBuildDigest,
        "harnessId": plan.payload.harnessId,
        "harnessImplementation": _snapshot()["upstreamSnapshot"][
            "harnessImplementation"
        ],
        "runtimeDependencies": list(host_entry.runtimeDependencies),
        "configured": True,
        "capabilities": {"streaming": True},
        "architecture": plan.payload.supportIdentity.architecture,
        "attestationGeneration": 8,
        "observedAt": datetime.now(UTC).isoformat(),
    }
    parsed_attestation = HostHarnessAttestation.model_validate(raw_attestation)
    raw_attestation["attestationRef"] = compute_attestation_ref(parsed_attestation)
    workspace = SimpleNamespace(
        materialize=AsyncMock(
            return_value={
                "path": "/workspaces/run",
                "resolutionRef": "artifact:workspace-exact",
            }
        )
    )
    skills = SimpleNamespace(
        materialize=AsyncMock(
            return_value={
                "deliveryRef": plan.payload.resolvedSkills["skillDeliveryRef"],
                "attestationRef": "artifact:skills-exact",
            }
        )
    )
    launcher = SimpleNamespace(
        launch=AsyncMock(
            return_value={
                "hostId": "host-exact",
                "hostBindingRef": "host-binding:exact",
                "hostLeaseRef": "host-lease:exact",
                "hostLeaseGeneration": 8,
            }
        )
    )
    registration = SimpleNamespace(
        wait_for_registration=AsyncMock(
            return_value={
                "hostId": "host-exact",
                "attestation": raw_attestation,
                "exactHostCapabilityDecisionRef": "artifact:capability-exact",
                "modelOptionAttestation": {
                    "modelId": plan.payload.modelConfig.qualifiedId,
                    "available": True,
                    "attestationRef": "artifact:model-exact",
                },
            }
        )
    )
    image = SimpleNamespace(
        attest=AsyncMock(
            return_value={
                "observedImageRef": host_class.imageRef,
                "attestationRef": "artifact:image-exact",
            }
        )
    )
    egress = SimpleNamespace(
        attest=AsyncMock(
            return_value={
                "enforced": True,
                "attestationRef": "artifact:egress-exact",
            }
        )
    )
    cleanup = SimpleNamespace(cleanup=AsyncMock())
    runtime = GenericOmnigentHostRuntime(
        launcher=launcher,
        workspace_service=workspace,
        skill_service=skills,
        egress_service=egress,
        registration_waiter=registration,
        image_attestor=image,
        cleanup_service=cleanup,
    )
    command = {
        "commandId": "command-1",
        "claimToken": "claim-1",
        "sessionId": "session-1",
        "turnAttemptId": "turn-1",
        "expectedSessionRevision": 2,
        "fencingGeneration": 1,
    }
    binding_ref = "omnigent-runtime-binding:sha256:" + "7" * 64
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr",
        idempotencyKey="idem-host",
        parameters={"executionPlanRef": plan.planRef},
    )

    await runtime.realize(
        request=request,
        plan=plan,
        credential_handles=[{"materializerRef": "opencode-auth-json@1"}],
        runtime_binding_ref=binding_ref,
        command_authority=command,
    )
    await runtime.cleanup(
        plan.planRef,
        binding_ref,
        host_id="host-exact",
        command_authority=command,
    )

    expected = {
        **command,
        "executionPlanRef": plan.planRef,
        "runtimeBindingRef": binding_ref,
    }
    assert workspace.materialize.await_args.kwargs["authority"] == expected
    assert skills.materialize.await_args.kwargs["authority"] == expected
    assert launcher.launch.await_args.kwargs["authority"] == expected
    assert registration.wait_for_registration.await_args.kwargs["authority"] == expected
    assert image.attest.await_args.kwargs["authority"] == expected
    assert egress.attest.await_args.kwargs["authority"] == expected
    assert cleanup.cleanup.await_args.kwargs["authority"] == expected
