from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.credential_materializers import (
    CredentialMaterializationContext,
    CredentialRuntimeHandle,
    DockerOmnigentProviderConfigMaterializer,
    DockerOpencodeAuthJsonMaterializer,
)
from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import TrustState
from moonmind.omnigent.harness_platform.catalog_service import (
    InMemoryHarnessCatalogRepository,
    OmnigentHarnessCatalogService,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, get_launch_policy
from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentExecutionPlanningService,
    _ref,
)
from moonmind.omnigent.harness_platform.stores import (
    ExecutionPlanUsageIdentity,
    InMemoryExecutionPlanStore,
    InMemoryExecutionPlanUsageStore,
)
from moonmind.omnigent.host_leases import InMemoryOmnigentHostLeaseRepository
from moonmind.omnigent.host_services.attestation import _assert_exact_omnigent_build
from moonmind.omnigent.host_services.mounted_tools import OmnigentMountedToolService
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.provider_leases import (
    AcquiredProviderLease,
    OmnigentProviderLeaseCoordinator,
)
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.runtime_bindings import (
    InMemoryStableRuntimeBindingStore,
    RuntimeBindingState,
)
from moonmind.omnigent.secret_resolution import (
    OmnigentSecretResolutionService,
    ScopedSecretBundle,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


def test_exact_host_attestation_requires_catalog_build_label() -> None:
    expected = "sha256:" + "a" * 64
    _assert_exact_omnigent_build(
        {"Config": {"Labels": {"moonmind.omnigent.build_digest": expected}}},
        expected,
    )

    with pytest.raises(HarnessPlatformError) as exc:
        _assert_exact_omnigent_build(
            {"Config": {"Labels": {"moonmind.omnigent.build_digest": ""}}},
            expected,
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH


def test_planning_model_evidence_is_bound_to_generation_and_host_image() -> None:
    image_ref = "ghcr.io/moonmind/opencode@sha256:" + "a" * 64
    provider = SimpleNamespace(
        credential_generation=4,
        model_catalog_evidence_json={
            "credentialGeneration": 4,
            "imageRef": image_ref,
            "models": [{"qualifiedId": "opencode-go/model"}],
        },
    )
    OmnigentExecutionPlanningService._verify_model_evidence(
        provider,
        "opencode-go/model",
        expected_image_ref=image_ref,
    )

    provider.model_catalog_evidence_json["credentialGeneration"] = 3
    with pytest.raises(HarnessPlatformError) as stale:
        OmnigentExecutionPlanningService._verify_model_evidence(
            provider,
            "opencode-go/model",
            expected_image_ref=image_ref,
        )
    assert stale.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE

    provider.model_catalog_evidence_json["credentialGeneration"] = 4
    with pytest.raises(HarnessPlatformError) as wrong_image:
        OmnigentExecutionPlanningService._verify_model_evidence(
            provider,
            "opencode-go/model",
            expected_image_ref="ghcr.io/moonmind/opencode@sha256:" + "b" * 64,
        )
    assert wrong_image.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, key):
        return self._rows.get(key)


def _session_factory(rows):
    return lambda: _Session(rows)


class _InventoryClient:
    async def get_version(self) -> str:
        return "0.11.0"

    async def list_harnesses(self) -> list[dict[str, object]]:
        return [
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "capabilities": {
                    "integration_mode": "native-server",
                    "auth": "own-auth",
                    "model_family": "multi",
                    "effort": "none",
                    "interrupt": True,
                },
            },
            {
                "id": "third-party",
                "label": "Third Party",
                "package": "example.plugin",
                "plugin_entry_point": "example.plugin:harness",
                "plugin_load_error": "dependency unavailable",
            },
        ]

    async def list_agents(self) -> list[dict[str, object]]:
        return [{"id": "opencode-native-ui", "version": "7"}]

    async def list_hosts(self) -> list[dict[str, object]]:
        return [{"host_id": "host-1", "name": "connected", "status": "online"}]


@pytest.mark.asyncio
async def test_catalog_sync_binds_rows_to_real_build_and_persists_trust() -> None:
    repository = InMemoryHarnessCatalogRepository()
    service = OmnigentHarnessCatalogService(
        client=_InventoryClient(),
        repository=repository,
        endpoint_ref="default",
        omnigent_build_digest="sha256:" + "1" * 64,
        clock=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )

    result = await service.synchronize()

    assert result.snapshot.omnigentVersion == "0.11.0"
    assert result.snapshot.omnigentBuildDigest == "sha256:" + "1" * 64
    assert result.snapshot.sourceDigest != "sha256:" + "a" * 64
    assert result.diagnostics["agentCount"] == 1
    assert result.diagnostics["hostCount"] == 1
    assert await repository.load(result.snapshot.catalogRef) == result
    trust = {item.harnessId: item for item in result.trust_records}
    assert trust["opencode-native"].trustState is TrustState.core_trusted
    assert trust["third-party"].trustState is TrustState.quarantined
    assert trust["opencode-native"].implementation.digest not in {
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
    }
    opencode = next(
        row for row in result.snapshot.harnesses if row.id == "opencode-native"
    )
    assert opencode.capabilities.integrationMode == "native-server"
    assert opencode.capabilities.authModel == "own-auth"
    assert opencode.capabilities.modelFamily == "multi"
    assert opencode.capabilities.effortFamily == "none"


def _plan(model: str):
    digest = compute_model_config_digest(
        qualifiedId=model,
        effort=None,
        routeRef="opencode-go",
        normalizedOptions={},
    )
    return create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "harnessId": "opencode-native",
            "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
            + "3" * 64,
            "agentSource": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1",
                "upstreamSnapshotDigest": "sha256:" + "4" * 64,
            },
            "credentialBindingSetRef": "omnigent-credential-bindings:primary@1#sha256:"
            + "5" * 64,
            "credentialBindings": {
                "primary-model": {
                    "providerProfileRef": "opencode-go-primary",
                    "materializerRef": "opencode-auth-json@1",
                }
            },
            "hostClassRef": "omnigent-opencode@1",
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": model,
                "effort": None,
                "routeRef": "opencode-go",
                "normalizedOptions": {},
                "modelConfigDigest": digest,
            },
            "resolvedSkills": {
                "resolvedSkillSetRef": "artifact:skills",
                "resolvedSkillSetDigest": "sha256:" + "6" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "7" * 64,
            },
            "classAdmissionDecision": {
                "allowed": True,
                "requiredSatisfied": [],
                "preferredSatisfied": [],
                "preferredMissing": [],
                "reasons": [],
            },
            "runtimeValidationRequirements": ["live-model-option"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "8" * 64,
            "capturePolicyRef": None,
            "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
            "supportCombinationKey": "omnigent-support-combination:sha256:" + "0" * 64,
        }
    )


@pytest.mark.asyncio
async def test_plan_usage_retry_keeps_first_plan_and_rejects_changed_request() -> None:
    plans = InMemoryExecutionPlanStore()
    usages = InMemoryExecutionPlanUsageStore(plans)
    identity = ExecutionPlanUsageIdentity("workflow-1", "step-1", "idem-1")
    compilation_count = 0

    async def compile_first():
        nonlocal compilation_count
        compilation_count += 1
        return _plan("opencode-go/first")

    request = {"parameters": {"model": "opencode-go/first"}}
    first = await usages.load_or_bind(
        identity=identity,
        request_payload=request,
        compile_fn=compile_first,
    )
    retry = await usages.load_or_bind(
        identity=identity,
        request_payload=json.loads(json.dumps(request)),
        compile_fn=lambda: _plan("opencode-go/rotated"),
    )

    assert retry.planRef == first.planRef
    assert compilation_count == 1

    with pytest.raises(HarnessPlatformError) as exc:
        await usages.load_or_bind(
            identity=identity,
            request_payload={"parameters": {"model": "opencode-go/changed"}},
            compile_fn=lambda: _plan("opencode-go/changed"),
        )
    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT.value
    )


@pytest.mark.asyncio
async def test_bundle_source_reloads_artifact_receipt_and_import_projection() -> None:
    bundle = b"immutable omnigent agent bundle"
    bundle_digest = "sha256:" + hashlib.sha256(bundle).hexdigest()
    imported_snapshot = {"id": "imported-1", "version": "7", "ready": True}
    imported_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                imported_snapshot,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    receipt_payload = {
        "bundleArtifactRef": "artifact:bundle-1",
        "bundleDigest": bundle_digest,
        "endpointRef": "default",
        "importedAgentId": "imported-1",
        "importedAgentVersion": "7",
        "importedContentDigest": imported_digest,
    }
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "endpointRef": "default",
            "source": {
                "kind": "bundle",
                "bundleArtifactRef": receipt_payload["bundleArtifactRef"],
                "bundleDigest": receipt_payload["bundleDigest"],
                "importedAgentId": receipt_payload["importedAgentId"],
                "importedAgentVersion": receipt_payload["importedAgentVersion"],
                "importedContentDigest": receipt_payload["importedContentDigest"],
                "importReceiptRef": _ref("omnigent-agent-import", receipt_payload),
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": "omnigent-harness-catalog:sha256:" + "1" * 64,
                "implementationRef": "omnigent-harness-implementation:sha256:"
                + "2" * 64,
            },
        }
    )

    class Artifacts:
        async def read_bytes(self, ref):
            assert ref == "artifact:bundle-1"
            return bundle

    class Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(
                available=True,
                compatible=True,
                error=None,
                metadata_snapshot=imported_snapshot,
            )

    class Session:
        async def execute(self, _statement):
            return Result()

    service = object.__new__(OmnigentExecutionPlanningService)
    service._artifacts = Artifacts()
    from api_service.db.models import OmnigentUpstreamAgentProjection

    await service._verify_agent_source(
        Session(), profile, OmnigentUpstreamAgentProjection
    )

    changed = profile.model_copy(
        update={
            "source": profile.source.model_copy(
                update={"bundleDigest": "sha256:" + "f" * 64}
            )
        }
    )
    with pytest.raises(HarnessPlatformError) as exc:
        await service._verify_agent_source(
            Session(), changed, OmnigentUpstreamAgentProjection
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE


class _DockerBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.runtime_ref = ""
        self.generation = ""

    async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
        self.calls.append((list(argv), input_bytes))
        if argv[1:3] == ["volume", "inspect"]:
            return 0, f"{self.runtime_ref}|{self.generation}\n".encode(), b""
        return 0, b"", b""


class _Artifacts:
    def __init__(self) -> None:
        self.payloads: list[object] = []

    async def write_json(self, **kwargs):
        self.payloads.append(kwargs["payload"])
        return "artifact://omnigent/test/credential-attestation.json"


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "opencode-primary",
            "correlationId": "workflow-1",
            "idempotencyKey": "idem-1",
            "parameters": {},
        }
    )


@pytest.mark.asyncio
async def test_opencode_volume_materialization_transports_secret_only_on_stdin() -> (
    None
):
    secret = "super-sensitive-open-code-key"
    backend = _DockerBackend()
    artifacts = _Artifacts()
    lease = CredentialLease(
        profile_id="opencode-primary",
        runtime_id="opencode",
        lease_id="lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="opencode-primary",
        capacity_scope_ref="provider-profile:opencode-primary",
        provider_lease_ref="provider-profile-lease:lease-1",
        credential_generation=4,
        lease=lease,
    )
    secrets = ScopedSecretBundle(
        provider_profile_ref="opencode-primary",
        credential_generation=4,
        values={"opencode_api_key": secret},
    )
    materializer = DockerOpencodeAuthJsonMaterializer(backend)

    handle = await materializer.materialize(
        CredentialMaterializationContext(
            request=_request(),
            acquired=acquired,
            secrets=secrets,
            writer_image_ref="ghcr.io/example/opencode@sha256:" + "1" * 64,
            artifact_gateway=artifacts,
        )
    )
    backend.runtime_ref = handle.credentialRuntimeRef
    backend.generation = "4"

    inspectable = json.dumps(
        {
            "calls": [call[0] for call in backend.calls],
            "handle": handle.model_dump(by_alias=True, mode="json"),
            "artifacts": artifacts.payloads,
        },
        sort_keys=True,
    )
    assert secret not in inspectable
    stdin_payloads = [payload for _argv, payload in backend.calls if payload]
    assert len(stdin_payloads) == 1
    assert json.loads(stdin_payloads[0]) == {
        "opencode-go": {"type": "api", "key": secret}
    }
    assert secrets.values == {}
    assert handle.credentialGeneration == 4
    assert handle.attachments[0].targetPath == "/home/app/.local/share/opencode"
    assert handle.attachments[0].accessMode == "read-only"

    cleanup = await materializer.cleanup(handle, 4)
    assert cleanup.removed is True
    assert backend.calls[-1][0][1:3] == ["volume", "rm"]


@pytest.mark.asyncio
async def test_pi_provider_config_uses_same_generic_volume_contract() -> None:
    secret = "second-harness-provider-key"
    backend = _DockerBackend()
    artifacts = _Artifacts()
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="pi-anthropic-primary",
        capacity_scope_ref="provider-profile:pi-anthropic-primary",
        provider_lease_ref="provider-profile-lease:lease-pi",
        credential_generation=8,
        lease=CredentialLease(
            profile_id="pi-anthropic-primary",
            runtime_id="omnigent",
            lease_id="lease-pi",
            owner_id="owner-pi",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )
    secrets = ScopedSecretBundle(
        provider_profile_ref="pi-anthropic-primary",
        credential_generation=8,
        values={"api_key": secret},
    )

    handle = await DockerOmnigentProviderConfigMaterializer(backend).materialize(
        CredentialMaterializationContext(
            request=_request(),
            acquired=acquired,
            secrets=secrets,
            writer_image_ref="ghcr.io/example/pi@sha256:" + "2" * 64,
            artifact_gateway=artifacts,
            model_qualified_id="anthropic/claude-sonnet-4-6",
            provider_route_ref="anthropic",
        )
    )

    inspectable = json.dumps(
        {
            "calls": [call[0] for call in backend.calls],
            "handle": handle.model_dump(by_alias=True, mode="json"),
            "artifacts": artifacts.payloads,
        },
        sort_keys=True,
    )
    assert secret not in inspectable
    stdin_payloads = [payload for _argv, payload in backend.calls if payload]
    assert len(stdin_payloads) == 1
    config = json.loads(stdin_payloads[0])
    assert config["providers"]["moonmind"]["default"] == ["anthropic", "pi"]
    assert config["providers"]["moonmind"]["anthropic"]["api_key"] == secret
    assert handle.materializerRef == "omnigent-provider-config@1"
    assert handle.runtimeEnvironment == {
        "OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config"
    }
    assert handle.attachments[0].accessMode == "read-only"
    assert secrets.values == {}


@pytest.mark.asyncio
async def test_runtime_binding_identity_stays_stable_and_cas_fences_stale_updates() -> (
    None
):
    store = InMemoryStableRuntimeBindingStore()
    provider_leases = {
        "primary-model": {
            "providerProfileRef": "opencode-primary",
            "providerLeaseRef": "provider-profile-lease:lease-1",
            "credentialGeneration": 4,
            "credentialRuntimeRef": "pending",
            "materializerRef": "opencode-auth-json@1",
        }
    }
    initial = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "1" * 64,
        idempotency_key="idem-1",
        provider_leases=provider_leases,
    )
    credentials = await store.update(
        initial.bindingId,
        expected_revision=1,
        expected_fencing_generation=1,
        state=RuntimeBindingState.credentials_materialized,
        updates={
            "credentialRuntimeHandles": {
                "primary-model": {
                    "credentialRuntimeRef": "credential-runtime:sha256:" + "2" * 64,
                    "materializerRef": "opencode-auth-json@1",
                }
            },
            "cleanupAuthorityRefs": ["credential-cleanup:sha256:" + "3" * 64],
        },
    )

    assert credentials.bindingId == initial.bindingId
    assert credentials.latestSnapshotRef != initial.latestSnapshotRef
    assert credentials.revision == 2
    assert credentials.providerLeases == initial.providerLeases

    with pytest.raises(HarnessPlatformError) as exc:
        await store.update(
            initial.bindingId,
            expected_revision=1,
            expected_fencing_generation=1,
            state=RuntimeBindingState.host_allocating,
        )
    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT.value
    )


@pytest.mark.asyncio
async def test_runtime_binding_allows_usage_metrics_but_rejects_credential_keys() -> (
    None
):
    store = InMemoryStableRuntimeBindingStore()
    initial = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "1" * 64,
        idempotency_key="idem-terminal-result",
        provider_leases={},
    )
    terminal = AgentRunResult(
        summary="complete",
        metrics={
            "tokenUsage": {
                "inputTokens": 12,
                "outputTokens": 4,
                "totalTokens": 16,
            }
        },
    )
    updated = await store.update(
        initial.bindingId,
        expected_revision=initial.revision,
        expected_fencing_generation=initial.fencingGeneration,
        updates={
            "terminalResult": terminal.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
        },
    )
    assert updated.terminalResult == terminal.model_dump(
        by_alias=True, mode="json", exclude_none=True
    )

    with pytest.raises(ValueError, match="forbidden secret-bearing key key"):
        await store.update(
            updated.bindingId,
            expected_revision=updated.revision,
            expected_fencing_generation=updated.fencingGeneration,
            updates={
                "credentialRuntimeHandles": {"primary-model": {"key": "raw-credential"}}
            },
        )


@pytest.mark.asyncio
async def test_host_cleanup_claim_fences_a_stale_activity() -> None:
    repository = InMemoryOmnigentHostLeaseRepository()
    lease = await repository.acquire(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        runtime_binding_id="omnigent-runtime-binding:sha256:" + "b" * 64,
        host_class_ref="omnigent-opencode@1",
        launch_policy_ref="omnigent-on-demand@1",
        harness_id="opencode-native",
        harness_implementation_ref="omnigent-harness-implementation:sha256:" + "c" * 64,
        provider_profile_refs=("profile-a",),
    )
    ready = await repository.mark_ready(
        lease.leaseRef,
        expected_generation=lease.generation,
        omnigent_host_id="host-a",
        cleanup_handle={"containerName": "mm-host-a", "launchGeneration": 1},
    )
    claimed = await repository.claim_cleanup(
        ready.leaseRef, expected_generation=ready.generation
    )
    assert claimed.generation == ready.generation + 1
    assert claimed.launchGeneration == 1

    with pytest.raises(HarnessPlatformError) as exc:
        await repository.claim_cleanup(
            ready.leaseRef, expected_generation=ready.generation
        )
    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT.value
    )


@pytest.mark.asyncio
async def test_generic_realizer_persists_authority_and_releases_provider_last() -> None:
    events: list[str] = []
    runtime_store = InMemoryStableRuntimeBindingStore()
    host_leases = InMemoryOmnigentHostLeaseRepository()
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="opencode-go-primary",
        capacity_scope_ref="provider-profile:opencode-go-primary",
        provider_lease_ref="provider-profile-lease:lease-1",
        credential_generation=4,
        lease=CredentialLease(
            profile_id="opencode-go-primary",
            runtime_id="opencode",
            lease_id="lease-1",
            owner_id="owner-1",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )

    class Leases:
        async def acquire_all(self, **_kwargs):
            events.append("provider-acquired")
            return (acquired,)

        async def release_all(self, _leases):
            events.append("provider-released")

    handle = CredentialRuntimeHandle.model_validate(
        {
            "credentialRuntimeRef": "credential-runtime:sha256:" + "d" * 64,
            "providerProfileRef": "opencode-go-primary",
            "providerLeaseRef": "provider-profile-lease:lease-1",
            "credentialGeneration": 4,
            "materializerRef": "opencode-auth-json@1",
            "attachments": [
                {
                    "kind": "volume",
                    "sourceRef": "mm-credential-1",
                    "targetPath": "/home/app/.local/share/opencode",
                    "accessMode": "read-only",
                }
            ],
            "cleanupRef": "credential-cleanup:sha256:" + "e" * 64,
            "attestationRef": "artifact://credential-attestation",
        }
    )

    class Credentials:
        async def materialize_all(self, **_kwargs):
            events.append("credentials-materialized")
            return (handle,)

        async def cleanup_all(self, handles):
            assert handles == (handle,)
            events.append("credentials-cleaned")
            return ()

    class HostRuntime:
        async def prepare(self, **kwargs):
            events.append("host-inputs-prepared")
            sink = kwargs["authority_sink"]
            await sink({"kind": "skills", "cleanupRef": "skill-cleanup:one"})
            from moonmind.omnigent.host_runtime import PreparedHostInputs

            return PreparedHostInputs(
                workspace_attachment={
                    "kind": "bind",
                    "sourceRef": "/tmp/work",
                    "targetPath": "/workspaces/run",
                    "accessMode": "read-write",
                },
                skill_attachment={
                    "kind": "bind",
                    "sourceRef": "/tmp/skills",
                    "targetPath": "/opt/moonmind/skills_active",
                    "accessMode": "read-only",
                    "deliveryRef": "skill-delivery:one",
                },
                tool_attachments=(),
                egress_attestation={
                    "networkRef": "egress",
                    "attestationRef": "artifact://egress",
                },
            )

        async def realize(self, **kwargs):
            events.append("host-ready")
            return {
                "omnigentHostId": "host-1",
                "hostId": "host-1",
                "containerName": "mm-host-1",
                "stateVolumeRef": "mm-state-1",
                "hostClassRef": "omnigent-opencode@1",
                "launchPolicyRef": "omnigent-on-demand@1",
                "workspacePath": "/workspaces/run",
                "hostHarnessAttestationRef": "artifact://host",
                "modelOptionAttestationRef": "artifact://models",
                "hostCleanupRef": "host-cleanup:one",
            }

        async def cleanup(self, **_kwargs):
            events.append("host-cleaned")
            return {"containerRemoved": True}

        async def cleanup_prepared(self, _prepared):
            events.append("inputs-cleaned")

    host_class = HostClass.model_validate(
        {
            "hostClassId": "omnigent-opencode",
            "version": 1,
            "imageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
            "omnigentVersion": "0.11.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "opencode-native",
                    "implementationRef": "omnigent-harness-implementation:sha256:"
                    + "3" * 64,
                    "runtimeDependencies": [{"name": "opencode", "version": "1.18.11"}],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["opencode-auth-json@1"],
            "features": {
                "workspaceBind": True,
                "restrictedEgress": True,
                "mountedSkills": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )

    async def resolve_host(_plan):
        return host_class, get_launch_policy("omnigent-on-demand@1")

    async def session_driver(request, *, session_authority_sink):
        await session_authority_sink.session_created("session-1")
        assert session_authority_sink.binding.omnigentSessionId == "session-1"
        assert request.parameters["omnigent"]["session"]["hostId"] == "host-1"
        authorization = request.parameters["omnigent"][
            "_moonmindProfileAuthorization"
        ]
        assert authorization["executionPlanRef"] == request.parameters[
            "executionPlanRef"
        ]
        assert authorization["runtimeBindingRef"] == request.parameters[
            "runtimeBindingRef"
        ]
        assert authorization["providerProfileId"] == "opencode-go-primary"
        assert authorization["providerLeaseRef"] == (
            "provider-profile-lease:lease-1"
        )
        assert authorization["credentialGeneration"] == 4
        assert authorization["hostBindingRef"]
        assert authorization["hostLeaseRef"]
        events.append("message-completed")
        return AgentRunResult(summary="done")

    class SessionCleanup:
        async def drain(self, session_id):
            assert session_id == "session-1"
            events.append("session-drained")
            return {"sessionId": session_id, "stopped": True}

    class TurnCommands:
        async def claim(self, **kwargs):
            assert kwargs["payload_digest"] == _plan("opencode-go/model").planRef
            events.append("command-claimed")
            return SimpleNamespace(owns_delivery=True)

        async def settle(self, **kwargs):
            events.append(f"command-settled:{kwargs['outcome'].value}")

    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=runtime_store,
        provider_lease_coordinator=Leases(),
        credential_provisioning_service=Credentials(),
        host_lease_repository=host_leases,
        host_runtime=HostRuntime(),
        planned_host_resolver=resolve_host,
        session_driver=session_driver,
        session_cleanup_service=SessionCleanup(),
        turn_command_service=TurnCommands(),
    )
    result = await realizer.execute(_request(), _plan("opencode-go/model"))

    assert result.summary == "done"
    assert result.metadata["executionPlanRef"] == _plan(
        "opencode-go/model"
    ).planRef
    assert result.metadata["runtimeBindingRef"].startswith(
        "omnigent-runtime-binding:sha256:"
    )
    assert result.metadata["supportCombinationIdentity"][
        "supportCombinationKey"
    ] == _plan("opencode-go/model").payload.supportCombinationKey
    assert events[-1] == "command-settled:applied"
    assert events.index("command-claimed") < events.index("provider-acquired")
    assert events.index("provider-released") < events.index(
        "command-settled:applied"
    )
    assert events.index("host-cleaned") < events.index("credentials-cleaned")
    assert events.index("credentials-cleaned") < events.index("provider-released")

    first_execution_events = tuple(events)
    replay = await realizer.execute(_request(), _plan("opencode-go/model"))

    assert replay.summary == "done"
    assert events[len(first_execution_events) :] == []


@pytest.mark.asyncio
async def test_provider_leases_acquire_sorted_and_release_reverse_order() -> None:
    profiles = {
        "profile-a": SimpleNamespace(
            enabled=True,
            auth_state="connected",
            runtime_id="opencode",
            capacity_scope_ref="provider-profile:profile-a",
            credential_generation=3,
        ),
        "profile-z": SimpleNamespace(
            enabled=True,
            auth_state="connected",
            runtime_id="opencode",
            capacity_scope_ref="provider-profile:profile-z",
            credential_generation=9,
        ),
    }
    events: list[str] = []

    class LeaseClient:
        async def acquire_execution_lease(self, **kwargs):
            profile_id = kwargs["profile_id"]
            events.append(f"acquire:{profile_id}")
            return CredentialLease(
                profile_id=profile_id,
                runtime_id=kwargs["runtime_id"],
                lease_id=f"lease-{profile_id}",
                owner_id=kwargs["owner_id"],
                purpose=kwargs["purpose"],
            )

        async def inspect_lease(self, lease):
            events.append(f"inspect:{lease.profile_id}")
            return {"active": True}

        async def release_lease(self, lease):
            events.append(f"release:{lease.profile_id}")

    base = _plan("opencode-go/model").payload.model_dump(by_alias=True, mode="json")
    base["credentialBindings"] = {
        "z-slot": {
            "providerProfileRef": "profile-z",
            "materializerRef": "opencode-auth-json@1",
        },
        "a-slot": {
            "providerProfileRef": "profile-a",
            "materializerRef": "opencode-auth-json@1",
        },
    }
    plan = create_execution_plan_envelope(base)
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(profiles), lease_client=LeaseClient()
    )

    acquired = await coordinator.acquire_all(
        plan=plan,
        workflow_id="workflow",
        step_execution_id="step",
        idempotency_key="idem",
    )
    await coordinator.release_all(acquired)

    assert [(item.slot, item.credential_generation) for item in acquired] == [
        ("a-slot", 3),
        ("z-slot", 9),
    ]
    assert events == [
        "acquire:profile-a",
        "inspect:profile-a",
        "acquire:profile-z",
        "inspect:profile-z",
        "release:profile-z",
        "release:profile-a",
    ]


@pytest.mark.asyncio
async def test_secret_resolution_is_role_scoped_and_generation_fenced() -> None:
    profile = SimpleNamespace(
        credential_generation=4,
        secret_refs={
            "opencode_api_key": "env://OPENCODE_TEST_KEY",
            "unrelated_secret": "env://MUST_NOT_BE_READ",
        },
    )
    resolved_roles: list[str] = []

    class Resolver:
        async def resolve(self, ref):
            resolved_roles.append(str(ref))
            return "scoped-value"

    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="profile-a",
        capacity_scope_ref="provider-profile:profile-a",
        provider_lease_ref="provider-profile-lease:lease-a",
        credential_generation=4,
        lease=CredentialLease(
            profile_id="profile-a",
            runtime_id="opencode",
            lease_id="lease-a",
            owner_id="owner-a",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )
    service = OmnigentSecretResolutionService(
        session_factory=_session_factory({"profile-a": profile}),
        resolver=Resolver(),
    )

    bundle = await service.resolve(
        acquired=acquired, allowed_secret_roles=["opencode_api_key"]
    )
    assert bundle.values == {"opencode_api_key": "scoped-value"}
    assert len(resolved_roles) == 1
    assert "MUST_NOT_BE_READ" not in resolved_roles[0]

    profile.credential_generation = 5
    with pytest.raises(HarnessPlatformError) as exc:
        await service.resolve(
            acquired=acquired, allowed_secret_roles=["opencode_api_key"]
        )
    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED.value
    )


@pytest.mark.asyncio
async def test_janitor_recovers_binding_that_crashed_before_host_lease() -> None:
    bindings = InMemoryStableRuntimeBindingStore()
    binding = await bindings.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "1" * 64,
        idempotency_key="credential-only-crash",
        provider_leases={
            "primary-model": {
                "providerProfileRef": "profile-a",
                "providerLeaseRef": "provider-profile-lease:lease-a",
                "credentialGeneration": 4,
                "credentialRuntimeRef": "credential-runtime:sha256:" + "2" * 64,
                "materializerRef": "opencode-auth-json@1",
            }
        },
    )
    calls: list[tuple[str, str]] = []

    class Realizer:
        async def reconcile(self, plan_ref, binding_id):
            calls.append((plan_ref, binding_id))

    janitor = GenericOmnigentHostJanitor(
        host_leases=InMemoryOmnigentHostLeaseRepository(),
        runtime_bindings=bindings,
        realizer=Realizer(),
        stale_after_seconds=-1,
    )

    result = await janitor.run()

    assert calls == [(binding.executionPlanRef, binding.bindingId)]
    assert result["runtimeBindingsExamined"] == 1
    assert result["reconciled"] == 1


@pytest.mark.asyncio
async def test_tool_delivery_uses_plan_names_and_deployment_owned_volume(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.lock.json"
    manifest.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "gh",
                        "version": "2.76.2",
                        "path": "bin/gh",
                        "platforms": {"linux/amd64": {"executableSha256": "a" * 64}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    class Backend:
        async def run(self, argv, **_kwargs):
            calls.append(list(argv))
            return b""

    service = OmnigentMountedToolService(
        backend=Backend(),
        manifest_path=manifest,
        volume_ref="deployment-tools",
    )
    result = await service.materialize(
        {"toolDeliveryRef": "tool-delivery:sha256:" + "1" * 64, "tools": ["gh"]}
    )

    assert calls == [["docker", "volume", "inspect", "deployment-tools"]]
    assert result[0]["sourceRef"] == "deployment-tools"
    assert result[0]["accessMode"] == "read-only"
    assert result[0]["tools"][0]["executableDigests"] == ["a" * 64]


@pytest.mark.asyncio
async def test_workspace_attachment_translates_to_daemon_visible_volume_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "worker"
    workspace = workspace_root / "run-1"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")

    async def runner(argv):
        assert argv[-1] == "agent-workspaces"
        return 0, "/daemon/agent_workspaces\n", ""

    service = OmnigentWorkspaceMaterializer(
        command_runner=runner,
        workspace_root=workspace_root,
        workspace_volume="agent-workspaces",
    )
    request = _request().model_copy(
        update={"workspace_spec": {"workspacePath": str(workspace)}}
    )

    attachment = await service.materialize(request)

    assert attachment["sourceRef"] == "/daemon/agent_workspaces/run-1"
    assert attachment["accessMode"] == "read-write"
