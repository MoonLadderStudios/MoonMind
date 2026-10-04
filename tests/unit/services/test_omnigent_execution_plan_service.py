from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from api_service.services import omnigent_execution_plan_service as service
from api_service.services.omnigent_agent_profile_selection import (
    default_launch_policy_ref,
)
from api_service.services.omnigent_policies import bootstrap_document
from moonmind.omnigent.execution_support_evidence import (
    EXECUTION_SUPPORT_EVIDENCE_ISSUER,
    EXECUTION_SUPPORT_EVIDENCE_VERSION,
)
from moonmind.omnigent.harness_platform.catalog import (
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.omnigent.session_supervisor_rollback import (
    SUPERVISOR_ROLLBACK_POLICY_VERSION,
)
from moonmind.schemas.omnigent_session_models import (
    OMNIGENT_SESSION_COMPATIBILITY_VERSION,
    OMNIGENT_SESSION_FEATURE_GENERATION,
)

# The deployment-managed OpenCode Agent Profile allows both the generic and
# the harness-shaped launch policy; admission always selects the first.
_OPENCODE_ALLOWED_LAUNCH_POLICIES = [
    "omnigent-on-demand@1",
    "opencode-on-demand@1",
]
_SERVER_IMAGE_REF = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "6" * 64


def _configure_ready_host_image_pair(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Give plan tests exact resolver evidence for selected host images."""

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER_IMAGE_REF)

    provenance = {"hostBuildDigest": "sha256:" + "b" * 64, "hostVersion": "0.10.0"}

    def load_state():
        import os

        host_ref = os.environ.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "")
        shared_ref = os.environ.get("OMNIGENT_SHARED_HOST_IMAGE_REF", "")
        if not host_ref and not shared_ref:
            return None
        return SimpleNamespace(
            server_image_ref=os.environ.get("OMNIGENT_IMAGE_REF", _SERVER_IMAGE_REF),
            opencode_host_image_ref=host_ref,
            shared_host_image_ref=shared_ref,
            details={
                "hostImageProvenance": {
                    image_ref: {
                        "buildDigest": provenance["hostBuildDigest"],
                        "version": provenance["hostVersion"],
                    }
                    for image_ref in (host_ref, shared_ref)
                    if image_ref
                },
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": os.environ.get(
                        "OMNIGENT_IMAGE_REF", _SERVER_IMAGE_REF
                    ),
                    "hostImageRef": host_ref,
                    **provenance,
                },
            },
        )

    monkeypatch.setattr(store, "load_resolved_state", load_state)

    return provenance


@pytest.fixture(autouse=True)
def _ready_opencode_image_pair(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    return _configure_ready_host_image_pair(monkeypatch)


class _LegacyClassAdmissionDecision(BaseModel):
    """Exact class-decision shape consumed by the pre-cutover worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requiredSatisfied: tuple[str, ...]
    preferredSatisfied: tuple[str, ...]
    degraded: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


class _ArtifactService:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}
        self._index = 0

    async def create(self, **_kwargs):
        self._index += 1
        artifact = SimpleNamespace(artifact_id=f"art_plan_{self._index}")
        return artifact, SimpleNamespace()

    async def write_complete(self, *, artifact_id: str, payload: bytes, **_kwargs):
        self.payloads[artifact_id] = payload
        return SimpleNamespace(artifact_id=artifact_id)


class _PlanStore:
    persisted = None

    def __init__(self, _session_factory) -> None:
        pass

    async def persist(self, envelope):
        self.__class__.persisted = envelope
        return envelope


@pytest.mark.asyncio
async def test_plan_compilation_gates_unseeded_policy_authority(monkeypatch) -> None:
    from api_service.services import omnigent_policies

    class _PolicyService:
        def __init__(self, _session):
            pass

        async def resolve_runtime_snapshot(self, policy_ref: str):
            raise omnigent_policies.PolicyNotFound(policy_ref)

    monkeypatch.setattr(omnigent_policies, "OmnigentPolicyService", _PolicyService)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await service._resolve_runtime_policy_snapshot(
            policy_ref="opencode-on-demand@1",
            session_factory=object(),
            db_session=object(),
        )

    assert exc_info.value.code == "OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE"
    assert "startup reconciliation" in str(exc_info.value)


@pytest.mark.parametrize("payload_key", ["workflow", "task"])
def test_skill_selector_includes_nested_dynamic_execution_skills(
    payload_key: str,
) -> None:
    selector = service._skill_selector(
        {
            payload_key: {
                "steps": [
                    {
                        "skill": {
                            "id": "moonspec-verify",
                            "args": {
                                "payloadTemplate": {
                                    "selectedSkill": "not-current-run-intent"
                                }
                            },
                        }
                    },
                    {
                        "annotations": {
                            "remediationLoop": {
                                "kind": "remediation_loop",
                                "remediationTool": {
                                    "type": "skill",
                                    "name": "remediate-issue",
                                    "inputs": {},
                                },
                                "verificationTool": {
                                    "type": "agent_runtime",
                                    "name": "auto",
                                    "inputs": {"selectedSkill": "moonspec-verify"},
                                },
                            }
                        }
                    },
                ]
            }
        }
    )

    assert [entry.name for entry in selector.include] == [
        "moonspec-verify",
        "remediate-issue",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload_key", ["workflow", "task"])
async def test_admission_persists_nested_dynamic_execution_skills(
    payload_key: str,
) -> None:
    artifacts = _ArtifactService()

    resolved, manifest_ref, _manifest_digest, content_refs = (
        await service._resolve_and_persist_skills(
            session_factory=object(),
            artifact_service=artifacts,
            principal="user-1",
            workflow_id="mm:remediation-skill-snapshot",
            task_input_snapshot_digest="sha256:" + "1" * 64,
            initial_parameters={
                payload_key: {
                    "steps": [
                        {
                            "skill": {
                                "id": "moonspec-verify",
                                "args": {
                                    "payloadTemplate": {
                                        "selectedSkill": "not-current-run-intent"
                                    }
                                },
                            }
                        },
                        {
                            "annotations": {
                                "remediationLoop": {
                                    "kind": "remediation_loop",
                                    "remediationTool": {
                                        "type": "skill",
                                        "name": "remediate-issue",
                                        "inputs": {},
                                    },
                                    "verificationTool": {
                                        "type": "agent_runtime",
                                        "name": "auto",
                                        "inputs": {"selectedSkill": "moonspec-verify"},
                                    },
                                }
                            }
                        },
                    ]
                }
            },
        )
    )

    assert [entry.skill_name for entry in resolved.skills] == [
        "moonspec-verify",
        "remediate-issue",
    ]
    manifest = json.loads(artifacts.payloads[manifest_ref])
    assert [entry["skill_name"] for entry in manifest["skills"]] == [
        "moonspec-verify",
        "remediate-issue",
    ]
    assert len(content_refs) == 2


@pytest.mark.parametrize("workflow", [None, {}, {"skill": {"id": "moonspec-verify"}}])
def test_skill_admission_preserves_execution_envelope_precedence(workflow) -> None:
    parameters = {
        "workflow": workflow,
        "task": {"skill": {"id": "remediate-issue"}},
    }
    before = json.dumps(parameters, sort_keys=True)
    assert service.selected_skill_names(parameters) == [
        "moonspec-verify" if workflow else "remediate-issue"
    ]
    assert json.dumps(parameters, sort_keys=True) == before


@pytest.mark.parametrize("payload_key", ["workflow", "task"])
def test_skill_admission_rejects_excluded_selected_skill(payload_key: str) -> None:
    with pytest.raises(ValueError, match="selected Skills cannot also be excluded"):
        service._skill_selector(
            {
                payload_key: {
                    "skills": {"exclude": ["moonspec-verify"]},
                    "steps": [{"skill": {"id": "moonspec-verify"}}],
                }
            }
        )


def _snapshot(*, harness: str, policy: str, provider_id: str) -> dict:
    oauth_harness = harness in {"codex-native", "claude-native"}
    provider_runtime = {
        "codex-native": "codex_cli",
        "claude-native": "claude_code",
        "opencode-native": "opencode",
    }[harness]
    provider_name = {
        "codex-native": "openai",
        "claude-native": "anthropic",
        "opencode-native": "openai",
    }[harness]
    return {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": f"profile-{harness}",
        "version": 3,
        "digest": "sha256:" + "9" * 64,
        "providerProfileRef": provider_id,
        "executionProfileRef": f"omnigent-{harness.removesuffix('-native')}@1",
        "allowedLaunchPolicyRefs": [policy],
        "launchPolicyRef": policy,
        "agentId": f"{harness}-agent",
        "policyRef": "omnigent-policy:sha256:" + "8" * 64,
        "document": {
            "schemaVersion": "moonmind.omnigent-agent-profile.v1",
            "endpointRef": "default",
            "bridgeMode": "embedded",
            "source": {
                "upstreamId": f"{harness}-agent",
                "upstreamVersion": "1.0.0",
            },
            "harness": harness,
            "requiredCapabilities": [],
            "execution": {
                "defaultExecutionProfileRef": (
                    f"omnigent-{harness.removesuffix('-native')}@1"
                ),
                "allowedLaunchPolicyRefs": [policy],
            },
            "providerRequirements": {
                "runtimeId": provider_runtime,
                "providerIds": [provider_name],
                "credentialSource": "oauth_volume" if oauth_harness else "secret_ref",
                "materializationMode": (
                    "oauth_home" if oauth_harness else "generated_file"
                ),
            },
            "model": {"model": "example/model", "settings": {}},
            "workspace": {"mutation": "allowed"},
            "skills": ["github"],
            "tools": [],
            "capture": {"stream": True},
            "continuations": {"checkpoint": True},
            "publish": {"mode": "none"},
            "policyRef": "omnigent-policy:sha256:" + "8" * 64,
        },
    }


def _policy_snapshot(
    *,
    harness: str,
    policy: str,
    host_image_ref: str | None = None,
    architecture: str | None = None,
) -> dict:
    profile_ref = f"omnigent-{harness.removesuffix('-native')}@1"
    host_image_digest = "7" if harness == "opencode-native" else "f"
    document = bootstrap_document(
        host_mode="on_demand_docker",
        execution_profile_ref=profile_ref,
        server_image_ref="ghcr.io/example/omnigent-server@sha256:" + "a" * 64,
        host_image_ref=host_image_ref
        or "ghcr.io/example/omnigent-host@sha256:" + host_image_digest * 64,
    ).model_dump(mode="json", by_alias=True)
    document["execution"]["harness"] = harness
    document["execution"]["agentIdentities"] = [
        {
            "claude-native": "claude-native-ui",
            "opencode-native": "opencode",
        }.get(harness, "codex")
    ]
    document["providerProfile"]["compatibleProviders"] = [
        {
            "claude-native": "anthropic",
            "opencode-native": "opencode",
        }.get(harness, "codex")
    ]
    if architecture is not None:
        document["host"]["architectures"] = [architecture]
    policy_id, _, version = policy.rpartition("@")
    return compile_policy_snapshot(
        policy_id=policy_id,
        version=int(version),
        document=document,
        validation={"valid": True},
    )


def _protected_support_evidence(plan_payload) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "schemaVersion": EXECUTION_SUPPORT_EVIDENCE_VERSION,
        "evidenceIssuer": EXECUTION_SUPPORT_EVIDENCE_ISSUER,
        "status": "passed",
        "sourceCommit": "abc1234",
        "protectedRunRef": "https://example.invalid/actions/runs/123",
        "evidenceManifestRef": "artifact://protected-evidence-manifest",
        "evidenceManifestDigest": "sha256:" + "6" * 64,
        "generatedAt": now.isoformat(),
        "expiresAt": (now + timedelta(days=7)).isoformat(),
        "supportClassification": "fully_managed",
        "supportCombinationKey": plan_payload.supportCombinationKey,
        "supportIdentity": plan_payload.supportIdentity.model_dump(
            mode="json", by_alias=True
        ),
        "hostImageRef": plan_payload.hostImageRef,
        "policySnapshotDigest": plan_payload.policySnapshotDigest,
        "effectiveLaunchSnapshotDigest": (plan_payload.effectiveLaunchSnapshotDigest),
        "policyGateRef": "deployment-ready",
        "policyQualified": True,
        "exactArtifactsVerified": True,
        "featureGeneration": OMNIGENT_SESSION_FEATURE_GENERATION,
        "replayCompatibilityVersion": OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        "rollbackPolicyVersion": SUPERVISOR_ROLLBACK_POLICY_VERSION,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness", "policy", "realizer"),
    [
        ("codex-native", "codex-on-demand@1", "codex-profile-bound@1"),
        ("claude-native", "claude-on-demand@1", "generic-omnigent-host@1"),
        ("opencode-native", "opencode-on-demand@1", "generic-omnigent-host@1"),
        ("opencode-native", "opencode-on-demand@2", "generic-omnigent-host@1"),
    ],
)
async def test_product_boundary_persists_secret_free_plan_and_exact_realizer(
    monkeypatch, harness: str, policy: str, realizer: str
) -> None:
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setattr(service, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(
        service,
        "load_protected_execution_support_evidence",
        _protected_support_evidence,
    )

    # Also mock the resolver to use the protected evidence directly for this hermetic test
    def _mock_resolve(plan_payload, **_kwargs):
        return _protected_support_evidence(plan_payload), "supported"

    monkeypatch.setattr(service, "resolve_execution_evidence", _mock_resolve)

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(harness=harness, policy=policy)

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    if harness == "claude-native":
        monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED", "true")
    if harness in {"claude-native", "codex-native"}:
        monkeypatch.setenv(
            "OMNIGENT_SHARED_HOST_IMAGE_REF",
            "ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
        )
    if harness == "opencode-native":
        monkeypatch.setenv(
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
            "ghcr.io/example/omnigent-host@sha256:" + "7" * 64,
        )
    artifacts = _ArtifactService()
    provider_id = f"provider-{harness}"
    result = await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="mm:test-product-boundary",
        agent_profile_snapshot=_snapshot(
            harness=harness, policy=policy, provider_id=provider_id
        ),
        provider_profile=SimpleNamespace(
            profile_id=provider_id,
            runtime_id={
                "codex-native": "codex_cli",
                "claude-native": "claude_code",
                "opencode-native": "opencode",
            }[harness],
            provider_id={
                "codex-native": "openai",
                "claude-native": "anthropic",
                "opencode-native": "opencode-go",
            }[harness],
        ),
        initial_parameters={
            "model": "example/model",
            "targetRuntime": "omnigent",
            "publishMode": "none",
            "maxAttempts": 2,
            "workflow": {"instructions": "Use durable refs only."},
        },
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
    )

    assert result.envelope.payload.executionRealizerRef == realizer
    assert result.envelope.payload.policySnapshotRef.startswith("artifact:")
    assert result.envelope.payload.policySnapshotDigest.startswith("sha256:")
    assert result.envelope.payload.effectiveLaunchSnapshotRef.startswith("artifact:")
    assert result.envelope.payload.effectiveLaunchSnapshotDigest.startswith("sha256:")
    assert result.envelope.payload.hostImageRef
    assert result.envelope.payload.hostArchitecture in {
        "linux/amd64",
        "linux/arm64",
    }
    assert result.envelope.payload.supportIdentity is not None
    assert (
        result.envelope.payload.supportIdentity.architecture
        == result.envelope.payload.hostArchitecture
    )
    assert result.envelope.payload.authority is not None
    assert result.envelope.payload.authority.taskInputSnapshotRef == "art_request_1"
    admission = result.envelope.payload.admissionAuthority
    assert admission is not None
    assert admission.featureGeneration == OMNIGENT_SESSION_FEATURE_GENERATION
    assert (
        admission.replayCompatibilityVersion == OMNIGENT_SESSION_COMPATIBILITY_VERSION
    )
    assert admission.rollbackPolicyVersion == SUPERVISOR_ROLLBACK_POLICY_VERSION
    support_artifact_id = admission.supportEvidenceRef.removeprefix("artifact:")
    support_payload = json.loads(artifacts.payloads[support_artifact_id])
    assert support_payload["schemaVersion"] == EXECUTION_SUPPORT_EVIDENCE_VERSION
    assert (
        support_payload["supportCombinationKey"]
        == result.envelope.payload.supportCombinationKey
    )
    assert support_payload["policyQualified"] is True
    assert result.binding.plan_ref == result.envelope.planRef
    serialized = json.dumps(
        result.envelope.model_dump(mode="json", by_alias=True), sort_keys=True
    )
    for forbidden in (
        "credentialGeneration",
        "providerLeaseRef",
        "hostLeaseRef",
        "volumeName",
        "secretBody",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_product_boundary_uses_exact_arm64_architecture_for_support_identity(
    monkeypatch,
) -> None:
    """A multi-architecture Host Class must bind support to the selected host."""

    monkeypatch.setattr(service, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(
            harness="opencode-native",
            policy="opencode-on-demand@1",
            architecture="arm64",
        )

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "7" * 64,
    )
    artifacts = _ArtifactService()
    result = await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="mm:test-arm64-support-identity",
        agent_profile_snapshot=_snapshot(
            harness="opencode-native",
            policy="opencode-on-demand@1",
            provider_id="provider-opencode-native",
        ),
        provider_profile=SimpleNamespace(
            profile_id="provider-opencode-native",
            runtime_id="opencode",
            provider_id="opencode-go",
        ),
        initial_parameters={
            "model": "example/model",
            "targetRuntime": "omnigent",
            "publishMode": "none",
            "maxAttempts": 2,
            "workflow": {"instructions": "Use the selected ARM64 host."},
        },
        authored_request_ref="art_request_arm64",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_arm64",
        task_input_snapshot_digest="sha256:" + "1" * 64,
    )

    assert result.envelope.payload.hostArchitecture == "linux/arm64"
    assert result.envelope.payload.supportIdentity is not None
    assert result.envelope.payload.supportIdentity.architecture == "linux/arm64"

    from moonmind.omnigent import deployment_identity
    from moonmind.workflows.temporal.activities.omnigent_session_activities import (
        _validate_plan_support_authority,
    )

    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: (_ for _ in ()).throw(
            AssertionError("session admission consulted mutable deployment identity")
        ),
    )
    # Session admission validates immutable support only. Mutable deployment
    # drift is enforced for exact reruns and immediately before a fresh launch,
    # while a continuation uses its bound host attestation.
    _validate_plan_support_authority(result.envelope)


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog_access", ["factory", "session", "schedule_refresh"])
async def test_product_boundary_uses_profile_catalog_build_identity(
    monkeypatch,
    tmp_path,
    catalog_access,
    _ready_opencode_image_pair,
) -> None:
    """Replay the catalog handoff that failed in mm:9b176122 at 2026-09-07T06:00Z.

    A schedule passes session_factory=None and db_session=session. Its plan
    must keep readable profile authority through refresh and worker dispatch.
    """

    _ready_opencode_image_pair["hostVersion"] = "0.11.0"
    build_identity = "sha256:" + "b" * 64
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "server@" + build_identity)
    implementation_digest = "sha256:" + "c" * 64
    authority_catalog = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.11.0",
        omnigentBuildDigest=build_identity,
        sourceDigest="sha256:" + "d" * 64,
        observedAt=datetime.now(UTC) - timedelta(seconds=106_285),
        harnesses=[
            {
                "id": "opencode-native",
                "aliases": [],
                "label": "OpenCode",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "0.11.0",
                    "digest": implementation_digest,
                    "pluginEntryPoint": None,
                },
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "own-auth",
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        ],
    )
    harness = authority_catalog.harnesses[0]
    freshness_catalog = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion=authority_catalog.omnigentVersion,
        omnigentBuildDigest=authority_catalog.omnigentBuildDigest,
        sourceDigest="sha256:" + "e" * 64,
        observedAt=datetime.now(UTC),
        harnesses=[
            item.model_dump(mode="json", by_alias=True)
            for item in authority_catalog.harnesses
        ],
    )
    freshness_trust = classify_harness_trust(
        harnessId=harness.id,
        implementation=harness.implementation,
        trustState=TrustState.core_trusted,
    )

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api_service.db.models import (
        Base,
        ManagedAgentProviderProfile,
        TemporalArtifact,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        DbHarnessCatalogRepository,
        HarnessCatalogSyncResult,
    )
    from moonmind.omnigent.harness_platform.planning_service import (
        OmnigentPlannedHostResolver,
    )
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/admission.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalogs = DbHarnessCatalogRepository(factory)
    for observation in (authority_catalog, freshness_catalog):
        await catalogs.persist(
            HarnessCatalogSyncResult(
                snapshot=observation,
                trust_records=(freshness_trust,),
                diagnostics={},
            )
        )

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(
            harness="opencode-native",
            policy="opencode-on-demand@1",
        )

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "7" * 64,
    )
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )

    artifacts = _ArtifactService()
    snapshot = _snapshot(
        harness="opencode-native",
        policy="opencode-on-demand@1",
        provider_id="provider-opencode-native",
    )
    snapshot["document"]["harness"] = {
        "id": harness.id,
        "catalogRef": authority_catalog.catalogRef,
        "implementationRef": harness.implementation.implementation_ref(),
    }
    db_session = factory()
    transaction = await db_session.begin()
    result = await service.compile_and_persist_execution_plan(
        session_factory=factory if catalog_access == "factory" else None,
        db_session=db_session if catalog_access != "factory" else None,
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="mm:test-profile-catalog-build",
        agent_profile_snapshot=snapshot,
        provider_profile=SimpleNamespace(
            profile_id="provider-opencode-native",
            runtime_id="opencode",
            provider_id="opencode-go",
        ),
        initial_parameters={
            "model": "example/model",
            "targetRuntime": "omnigent",
            "publishMode": "none",
            "maxAttempts": 2,
            "workflow": {"instructions": "Use the pinned catalog."},
        },
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
        execution_plan_store=SessionExecutionPlanStore(db_session),
    )

    envelope = result.envelope
    if catalog_access == "schedule_refresh":
        from api_service.db.models import RecurringWorkflowDefinition
        from api_service.services.recurring_workflows_service import (
            RecurringWorkflowsService,
        )

        db_session.add(
            ManagedAgentProviderProfile(
                profile_id="provider-opencode-native",
                runtime_id="opencode",
                provider_id="opencode-go",
            )
        )
        db_session.add(
            TemporalArtifact(
                artifact_id="art_request_1",
                created_by_principal="user-1",
                sha256="1" * 64,
                storage_key="original-schedule-input",
            )
        )
        await db_session.flush()
        parameters = {
            "agentProfileSnapshot": snapshot,
            "targetRuntime": "omnigent",
            "model": "example/model",
            "publishMode": "none",
            "workflow": {"instructions": "Read the repository."},
            "omnigentExecutionPlan": result.binding.model_dump(
                mode="json",
                by_alias=True,
            ),
        }
        target = {"initialParameters": parameters, "agentProfileSnapshot": snapshot}
        definition = RecurringWorkflowDefinition(
            name="Catalog authority replay",
            cron="0 * * * *",
            timezone="UTC",
            owner_user_id=None,
            version=1,
            target=target,
        )
        db_session.add(definition)
        await db_session.flush()
        schedules = RecurringWorkflowsService(db_session, artifact_service=artifacts)
        assert await schedules._refresh_omnigent_execution_plan_target(
            definition,
            target=target,
            initial_parameters=parameters,
        )
        envelope = await SessionExecutionPlanStore(db_session).load(
            definition.target["initialParameters"]["omnigentExecutionPlan"]["planRef"]
        )

    assert transaction.is_active
    assert db_session.get_transaction() is transaction
    await db_session.commit()
    await db_session.close()

    class Gateway:
        async def read_bytes(self, ref):
            return artifacts.payloads[ref.removeprefix("artifact:")]

    # The next worker must be able to resolve the admitted plan from durable
    # storage, including plans refreshed with the schedule's session-only call.
    host_class, policy = await OmnigentPlannedHostResolver(
        catalog_repository=DbHarnessCatalogRepository(factory),
        artifact_gateway=Gateway(),
    )(envelope)
    assert host_class.ref == envelope.payload.hostClassRef
    assert policy.ref == envelope.payload.launchPolicyRef

    support = envelope.payload.supportIdentity
    assert support.omnigentServerBuildRef == build_identity
    assert support.omnigentHostBuildRef == build_identity
    assert envelope.payload.harnessCatalogRef == authority_catalog.catalogRef
    assert envelope.payload.harnessCatalogRef != freshness_catalog.catalogRef
    await engine.dispose()


@pytest.mark.asyncio
async def test_product_boundary_rejects_revoked_freshness_trust(
    monkeypatch,
    _ready_opencode_image_pair,
) -> None:
    """A matching fresh implementation cannot override its current denial."""
    _ready_opencode_image_pair["hostVersion"] = "0.11.0"

    catalog = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.11.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "d" * 64,
        observedAt=datetime.now(UTC),
        harnesses=[
            {
                "id": "opencode-native",
                "aliases": [],
                "label": "OpenCode",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "0.11.0",
                    "digest": "sha256:" + "c" * 64,
                    "pluginEntryPoint": None,
                },
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "own-auth",
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        ],
    )
    harness = catalog.harnesses[0]

    async def load_authority(**_kwargs):
        return {
            "hostClassRef": "omnigent-opencode@1",
            "implementationDigest": harness.implementation.digest,
            "materializerRef": "opencode-auth-json@1",
            "authModel": "own-auth",
            "integrationMode": "native-server",
            "_catalogSnapshot": catalog,
            "_freshnessCatalogSnapshot": catalog,
            "_freshnessTrustRecord": classify_harness_trust(
                harnessId=harness.id,
                implementation=harness.implementation,
                trustState=TrustState.blocked,
            ),
            "_harnessRecord": harness,
        }

    monkeypatch.setattr(service, "_try_load_real_harness_config", load_authority)
    with pytest.raises(HarnessPlatformError) as exc_info:
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
        )

    assert exc_info.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED


@pytest.mark.asyncio
async def test_real_harness_config_fails_closed_when_freshness_read_errors(
    monkeypatch,
) -> None:
    class Repository:
        def __init__(self, _session_factory):
            pass

        async def load(self, _catalog_ref):
            return SimpleNamespace(snapshot=object())

        async def latest(self, _endpoint_ref):
            raise RuntimeError("database unavailable")

    from moonmind.omnigent.harness_platform import catalog_service

    monkeypatch.setattr(catalog_service, "DbHarnessCatalogRepository", Repository)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await service._try_load_real_harness_config(
            harness_id="opencode-native",
            agent_profile_snapshot={
                "document": {
                    "endpointRef": "default",
                    "harness": {
                        "catalogRef": "omnigent-harness-catalog:sha256:" + "1" * 64
                    },
                }
            },
            session_factory=lambda: None,
        )

    assert (
        exc_info.value.code
        == HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_qualified_claude_catalog_admits_pinned_native_harness(
    monkeypatch,
) -> None:
    from api_service.services.omnigent_agent_profile_service import (
        _overlay_native_harnesses,
    )
    from moonmind.omnigent.harness_platform import catalog_service
    from moonmind.omnigent.harness_platform.catalog_service import (
        HarnessCatalogSyncResult,
    )

    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED", "true")
    observed = HarnessCatalogSyncResult(
        snapshot=create_catalog_snapshot(
            endpointRef="default",
            omnigentVersion="1.0.0",
            omnigentBuildDigest="sha256:" + "b" * 64,
            sourceDigest="sha256:" + "d" * 64,
            observedAt=datetime.now(UTC),
            harnesses=[],
        ),
        trust_records=(),
        diagnostics={
            "agents": [
                {
                    "id": "ag_claude",
                    "name": "claude-native-ui",
                    "version": "2",
                    "harness": "claude-native",
                }
            ]
        },
    )
    catalog = _overlay_native_harnesses(observed)

    class Repository:
        def __init__(self, _session_factory):
            pass

        async def load(self, catalog_ref):
            assert catalog_ref == catalog.snapshot.catalogRef
            return catalog

        async def latest(self, endpoint_ref):
            assert endpoint_ref == "default"
            return catalog

    monkeypatch.setattr(catalog_service, "DbHarnessCatalogRepository", Repository)
    authority = await service._try_load_real_harness_config(
        harness_id="claude-native",
        agent_profile_snapshot={
            "document": {
                "endpointRef": "default",
                "harness": {
                    "id": "claude-native",
                    "catalogRef": catalog.snapshot.catalogRef,
                },
            }
        },
        session_factory=lambda: None,
    )

    assert authority is not None
    assert authority["authModel"] == "oauth_volume"
    assert authority["integrationMode"] == "native-server"
    assert authority["_harnessRecord"].id == "claude-native"
    assert authority["_freshnessTrustRecord"].trustState == TrustState.core_trusted


@pytest.mark.asyncio
@pytest.mark.parametrize("generic_admitted", [False, True])
async def test_codex_oauth_profile_compiles_against_synchronized_inventory(
    monkeypatch, tmp_path, generic_admitted, _ready_opencode_image_pair
) -> None:
    """Codex via Omnigent compiles from the inventory a real endpoint reports.

    Omnigent's ``/v1/harnesses`` picker lists the ``codex`` CLI harness but
    omits the ``codex-native`` wrapper; only the ``codex-native-ui`` stock
    agent in ``/v1/agents`` proves the wrapper exists. The seeded Codex Agent
    Profile names ``codex-native`` and pins no catalog ref, so admission reads
    the latest synchronized observation.
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api_service.db.models import Base
    from api_service.services.omnigent_agent_profile_service import (
        _overlay_native_harnesses,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        DbHarnessCatalogRepository,
        OmnigentHarnessCatalogService,
    )
    from moonmind.omnigent.harness_platform.planning_service import (
        OmnigentPlannedHostResolver,
    )
    from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore

    class Endpoint:
        async def get_version(self):
            return "0.16.0"

        async def list_harnesses(self):
            return [
                {
                    "id": "codex",
                    "label": "Codex",
                    "capabilities": {
                        "integration_mode": "cli-subprocess",
                        "model_family": "gpt",
                    },
                }
            ]

        async def list_agents(self):
            return [
                {
                    "id": "16a06503889b0c3034496821afd41b9e",
                    "name": "codex-native-ui",
                    "version": "1",
                    "harness": "codex-native",
                }
            ]

        async def list_hosts(self):
            return []

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/codex.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    synchronized = await OmnigentHarnessCatalogService(
        client=Endpoint(),
        repository=DbHarnessCatalogRepository(factory),
        endpoint_ref="default",
        omnigent_build_digest="sha256:" + "b" * 64,
        observation_overlay=_overlay_native_harnesses,
    ).synchronize()

    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", str(generic_admitted)
    )
    monkeypatch.delenv("MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK", raising=False)
    host_image_ref = "ghcr.io/example/omnigent-host@sha256:" + "f" * 64
    host_build_digest = "sha256:" + "d" * 64
    _ready_opencode_image_pair["hostBuildDigest"] = host_build_digest
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", host_image_ref)
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(harness="codex-native", policy="codex-on-demand@2")

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    snapshot = _snapshot(
        harness="codex-native",
        policy="codex-on-demand@2",
        provider_id="codex-oauth",
    )
    artifacts = _ArtifactService()
    plan_store = DbExecutionPlanStore(factory)
    result = await service.compile_and_persist_execution_plan(
        session_factory=factory,
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="mm:test-codex-synchronized-inventory",
        agent_profile_snapshot=snapshot,
        provider_profile=SimpleNamespace(
            profile_id="codex-oauth",
            runtime_id="codex_cli",
            provider_id="openai",
        ),
        initial_parameters={
            "model": "gpt-5.5",
            "targetRuntime": "omnigent",
            "publishMode": "none",
            "maxAttempts": 2,
            "workflow": {"instructions": "Read the repository."},
        },
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
        execution_plan_store=plan_store,
    )

    payload = result.envelope.payload
    assert payload.executionRealizerRef == (
        "generic-omnigent-host@1" if generic_admitted else "codex-profile-bound@1"
    )
    assert payload.hostImageRef == host_image_ref
    assert payload.omnigentHostBuildDigest == host_build_digest
    assert payload.supportIdentity.omnigentHostBuildRef == host_build_digest
    assert (
        payload.supportIdentity.omnigentServerBuildRef
        == synchronized.snapshot.omnigentBuildDigest
    )
    assert payload.harnessCatalogRef == synchronized.snapshot.catalogRef
    codex = next(
        row for row in synchronized.snapshot.harnesses if row.id == "codex-native"
    )
    assert payload.harnessImplementationRef == (
        codex.implementation.implementation_ref()
    )

    class Gateway:
        async def read_bytes(self, ref):
            return artifacts.payloads[ref.removeprefix("artifact:")]

    persisted = await plan_store.load(result.envelope.planRef)
    host_class, policy = await OmnigentPlannedHostResolver(
        catalog_repository=DbHarnessCatalogRepository(factory),
        artifact_gateway=Gateway(),
    )(persisted)
    assert host_class.imageRef == host_image_ref
    assert host_class.omnigentBuildDigest == host_build_digest
    assert policy.ref == "codex-on-demand@2"
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("generic_admitted", [False, True])
async def test_codex_admission_rejects_conflicting_policy_image_before_persistence(
    monkeypatch, generic_admitted
) -> None:
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", str(generic_admitted)
    )
    monkeypatch.delenv("MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK", raising=False)
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "8" * 64,
    )
    monkeypatch.setattr(
        service, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified")
    )

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(harness="codex-native", policy="codex-on-demand@1")

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    artifacts = _ArtifactService()
    with pytest.raises(ValueError, match="effective launch host image conflicts"):
        await service.compile_and_persist_execution_plan(
            session_factory=object(),
            artifact_service=artifacts,
            principal="user-1",
            workflow_id="mm:codex-conflicting-host-image",
            agent_profile_snapshot=_snapshot(
                harness="codex-native", policy="codex-on-demand@1", provider_id="codex"
            ),
            provider_profile=SimpleNamespace(
                profile_id="codex", runtime_id="codex_cli", provider_id="openai"
            ),
            initial_parameters={
                "targetRuntime": "omnigent",
                "publishMode": "none",
                "workflow": {"instructions": "Use the admitted host."},
            },
            authored_request_ref="art_request_1",
            authored_request_digest="sha256:" + "1" * 64,
            task_input_snapshot_ref="art_request_1",
            task_input_snapshot_digest="sha256:" + "1" * 64,
            execution_plan_store=_PlanStore(object()),
        )
    assert artifacts.payloads == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("access", ["factory", "session"])
@pytest.mark.parametrize("failure", ["missing", "database_error"])
async def test_catalog_authority_failure_cannot_select_fixture_or_latest(
    monkeypatch,
    access,
    failure,
) -> None:
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from moonmind.omnigent.harness_platform import catalog_service

    session = SimpleNamespace()

    @asynccontextmanager
    async def factory():
        yield session

    latest = AsyncMock()

    class Repository:
        def __init__(self, session_factory):
            self.factory = session_factory

        async def load(self, _catalog_ref):
            async with self.factory() as borrowed:
                assert borrowed is session
                if failure == "database_error":
                    raise RuntimeError("database unavailable")
                return None

    Repository.latest = latest
    monkeypatch.setattr(catalog_service, "DbHarnessCatalogRepository", Repository)
    with pytest.raises(HarnessPlatformError) as error:
        await service._try_load_real_harness_config(
            harness_id="opencode-native",
            agent_profile_snapshot={
                "document": {
                    "harness": {
                        "id": "opencode-native",
                        "catalogRef": "omnigent-harness-catalog:sha256:" + "1" * 64,
                    },
                },
            },
            session_factory=factory if access == "factory" else None,
            db_session=session if access == "session" else None,
        )
    assert (
        error.value.code == HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE
    )
    latest.assert_not_awaited()


async def _compile_opencode_plan(
    monkeypatch,
    *,
    artifacts,
    launch_policy_ref: str,
    plan_store=None,
    extra_parameters: dict | None = None,
    provider_id: str = "opencode-go",
    document_source: dict | None = None,
    session_factory=None,
    workflow_id="mm:test-deployment-evidence",
    task_input_snapshot_ref="art_request_1",
    task_input_snapshot_digest="sha256:" + "1" * 64,
    profile_tools: tuple[str, ...] = (),
):
    """Compile one real OpenCode plan through the product admission boundary."""

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(harness="opencode-native", policy=launch_policy_ref)

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "7" * 64,
    )
    snapshot = _snapshot(
        harness="opencode-native",
        policy=launch_policy_ref,
        provider_id="provider-opencode-native",
    )
    snapshot["allowedLaunchPolicyRefs"] = _OPENCODE_ALLOWED_LAUNCH_POLICIES
    snapshot["document"]["execution"][
        "allowedLaunchPolicyRefs"
    ] = _OPENCODE_ALLOWED_LAUNCH_POLICIES
    if document_source is not None:
        snapshot["document"]["source"] = document_source
    snapshot["document"]["tools"] = list(profile_tools)
    return await service.compile_and_persist_execution_plan(
        session_factory=session_factory or object(),
        artifact_service=artifacts,
        principal="user-1",
        workflow_id=workflow_id,
        agent_profile_snapshot=snapshot,
        provider_profile=SimpleNamespace(
            profile_id="provider-opencode-native",
            runtime_id="opencode",
            provider_id=provider_id,
        ),
        initial_parameters={
            "model": "example/model",
            "targetRuntime": "omnigent",
            "publishMode": "none",
            "maxAttempts": 2,
            "workflow": {"instructions": "Use durable refs only."},
            **(extra_parameters or {}),
        },
        authored_request_ref=task_input_snapshot_ref,
        authored_request_digest=task_input_snapshot_digest,
        task_input_snapshot_ref=task_input_snapshot_ref,
        task_input_snapshot_digest=task_input_snapshot_digest,
        execution_plan_store=plan_store,
    )


@pytest.mark.asyncio
async def test_profile_github_tool_admits_collaboration_without_skill_capability(
    monkeypatch, tmp_path
) -> None:
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api_service.db.models import Base
    from tests.helpers.repository_connections import (
        github_pat_connection,
        github_repository_assignment,
        record_repository_connections,
    )

    repository = "MoonLadderStudios/MoonMind"
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        github_pat_connection("profile-github", "PROFILE_GITHUB_PAT"),
        assignments=[github_repository_assignment("profile-github", repository)],
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        service, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        service, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified")
    )
    artifacts = _ArtifactService()
    try:
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            session_factory=sessions,
            profile_tools=("GH",),
            extra_parameters={"repository": repository},
        )
    finally:
        await engine.dispose()

    plan = compiled.envelope.payload
    assert plan.resolvedTools["tools"] == ["gh"]
    assert plan.credentialBindings["collaboration"].repositoryRole == "collaboration"
    assert "destination" not in plan.credentialBindings
    access = plan.resolvedTools["repositoryAccess"]["collaboration"]
    selection = json.loads(
        artifacts.payloads[access["artifactRef"].removeprefix("artifact:")]
    )["selection"]
    assert selection["connectionId"] == "profile-github"
    assert selection["operations"] == ["read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("access_mode", ["explicit", "routed"])
async def test_schedule_refresh_retains_original_repository_principal(
    monkeypatch, tmp_path, access_mode
) -> None:
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api_service.db.models import (
        Base,
        ManagedAgentProviderProfile,
        RecurringWorkflowDefinition,
        TemporalArtifact,
    )
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )
    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.omnigent.harness_platform.stores import (
        DbExecutionPlanStore,
        SessionExecutionPlanStore,
    )
    from tests.helpers.repository_connections import (
        github_pat_connection,
        github_repository_assignment,
        record_repository_connections,
    )

    monkeypatch.setenv(
        "OMNIGENT_IMAGE_REF", "ghcr.io/example/omnigent-server@sha256:" + "b" * 64
    )
    repository = "MoonLadderStudios/MoonMind"
    engine = await record_repository_connections(monkeypatch, tmp_path)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    connection = github_pat_connection("schedule-repository", "SCHEDULE_REPOSITORY_PAT")
    connection = connection.model_copy(
        update={
            "ownership": connection.ownership.model_copy(
                update={"owner_ref": "user-1", "allowed_principal_refs": ("user-1",)}
            )
        }
    )
    async with sessions() as session:
        connections = RepositoryConnectionService(session)
        await connections.create_connection(
            connection,
            actor_ref="user-1",
            request_id="schedule-connection",
            principal_ref="user-1",
            principal_scope=("system", None),
        )
        await connections.set_assignment(
            github_repository_assignment("schedule-repository", repository),
            actor_ref="user-1",
            request_id="schedule-assignment",
            principal_ref="user-1",
            principal_scope=("system", None),
        )
        session.add(
            ManagedAgentProviderProfile(
                profile_id="provider-opencode-native",
                runtime_id="opencode",
                provider_id="opencode-go",
            )
        )
        session.add(
            TemporalArtifact(
                artifact_id="art_request_1",
                created_by_principal="user-1",
                sha256="1" * 64,
                storage_key="original-schedule-input",
            )
        )
        await session.commit()

    monkeypatch.setattr(
        service, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        service, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified")
    )
    artifacts = _ArtifactService()
    definition_id = uuid4()
    target_repository = (
        {
            "provider": "git",
            "connectionRef": "schedule-repository",
            "repository": {"name": repository},
        }
        if access_mode == "explicit"
        else repository
    )
    try:
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=DbExecutionPlanStore(sessions),
            session_factory=sessions,
            workflow_id=f"mm-schedule:{definition_id}",
            extra_parameters={"repository": target_repository},
        )
        snapshot = _snapshot(
            harness="opencode-native",
            policy="opencode-on-demand@1",
            provider_id="provider-opencode-native",
        )
        snapshot["allowedLaunchPolicyRefs"] = _OPENCODE_ALLOWED_LAUNCH_POLICIES
        snapshot["document"]["execution"][
            "allowedLaunchPolicyRefs"
        ] = _OPENCODE_ALLOWED_LAUNCH_POLICIES
        initial_parameters = {
            "repository": target_repository,
            "targetRuntime": "omnigent",
            "model": "example/model",
            "publishMode": "none",
            "workflow": {"instructions": "Refresh the original connection."},
            "agentProfileSnapshot": snapshot,
            "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True),
        }
        target = {
            "agentProfileSnapshot": snapshot,
            "initialParameters": initial_parameters,
        }
        async with sessions() as session:
            definition = RecurringWorkflowDefinition(
                id=definition_id,
                name="Frozen repository principal",
                cron="0 * * * *",
                timezone="UTC",
                owner_user_id=uuid4(),  # Legacy provenance cannot replace admission.
                version=1,
                target=target,
            )
            session.add(definition)
            await session.flush()
            schedules = RecurringWorkflowsService(session, artifact_service=artifacts)
            assert await schedules._refresh_omnigent_execution_plan_target(
                definition, target=target, initial_parameters=initial_parameters
            )
            refreshed_binding = definition.target["initialParameters"][
                "omnigentExecutionPlan"
            ]
            refreshed = await SessionExecutionPlanStore(session).load(
                refreshed_binding["planRef"]
            )
            access = refreshed.payload.resolvedTools["repositoryAccess"]["source"]
            selection = json.loads(
                artifacts.payloads[access["artifactRef"].removeprefix("artifact:")]
            )["selection"]
            assert selection["principalRef"] == "user-1"
            assert selection["connectionId"] == "schedule-repository"
            assert refreshed_binding["taskInputSnapshotRef"] == "art_request_1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_default_connection_admits_explicit_repository_without_assignment(
    monkeypatch, tmp_path
) -> None:
    """The migrated pre-assignment default keeps its legacy repository scope.

    Migration 391 records the deployment's legacy credential as
    ``repository-connection:git-default`` without assignments. Selecting it
    explicitly (as every saved schedule does) must still compile. A default
    the operator recorded, or one the operator has scoped with assignments,
    an unassigned recorded connection, and routed selection all stay strict.
    """

    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api_service.db.models import Base, RepositoryConnectionAuditEvent
    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
    )
    from tests.helpers.repository_connections import (
        github_pat_connection,
        github_repository_assignment,
        record_repository_connections,
    )

    monkeypatch.setenv(
        "OMNIGENT_IMAGE_REF", "ghcr.io/example/omnigent-server@sha256:" + "b" * 64
    )
    repository = "MoonLadderStudios/Tactics"
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "GITHUB_TOKEN"),
        github_pat_connection("unassigned-repository", "UNASSIGNED_PAT"),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        service, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        service, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified")
    )

    async def compile_for(target_repository):
        return await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            session_factory=sessions,
            extra_parameters={"repository": target_repository, "publishMode": "pr"},
        )

    def explicit(connection_ref):
        return {
            "provider": "git",
            "connectionRef": connection_ref,
            "repository": {"name": repository},
        }

    artifacts = _ArtifactService()
    try:
        # Without migration provenance the default is operator-owned.
        with pytest.raises(Exception, match="REPOSITORY_SETUP_REQUIRED"):
            await compile_for(explicit(DEFAULT_GIT_CONNECTION_REF))

        async with sessions() as session:
            session.add(
                RepositoryConnectionAuditEvent(
                    request_id="migration:391:legacy-github-credential",
                    actor_ref="system:migration-391",
                    action="connection.create",
                    connection_id=DEFAULT_GIT_CONNECTION_REF,
                    scope_type="system",
                    policy_revision=1,
                    detail_json={"migration": "391_legacy_github_cred_4023"},
                )
            )
            await session.commit()

        compiled = await compile_for(explicit(DEFAULT_GIT_CONNECTION_REF))
        access = compiled.envelope.payload.resolvedTools["repositoryAccess"]
        assert set(access) == {"source", "destination"}
        snapshot = json.loads(
            artifacts.payloads[
                access["destination"]["artifactRef"].removeprefix("artifact:")
            ]
        )
        assert snapshot["selection"]["connectionId"] == DEFAULT_GIT_CONNECTION_REF
        assert snapshot["selection"]["operations"] == [
            "write",
            "branch_write",
            "review_request",
        ]
        assert snapshot["repositoryIdentity"] == {
            "endpoint": "https://github.com",
            "providerRepoId": None,
            "canonicalRemote": f"https://github.com/{repository}.git",
            "displayName": repository,
        }

        with pytest.raises(Exception, match="REPOSITORY_SETUP_REQUIRED"):
            await compile_for(explicit("unassigned-repository"))
        # The legacy scope is never routed authority.
        with pytest.raises(ValueError, match="missing or ambiguous"):
            await compile_for(repository)

        # Once the operator scopes the default, its assignments decide.
        async with sessions() as session:
            await RepositoryConnectionService(session).set_assignment(
                github_repository_assignment(
                    DEFAULT_GIT_CONNECTION_REF, "MoonLadderStudios/MoonMind"
                ),
                actor_ref="system:deployment",
                request_id="scope-default",
                principal_ref="system:deployment",
                principal_scope=("system", None),
            )
        with pytest.raises(Exception, match="REPOSITORY_SETUP_REQUIRED"):
            await compile_for(explicit(DEFAULT_GIT_CONNECTION_REF))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("generic_admitted", [False, True])
async def test_repository_codex_product_admission_requires_capable_realizer(
    monkeypatch, generic_admitted
) -> None:
    from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore

    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
    )
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", str(generic_admitted)
    )
    monkeypatch.delenv("MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK", raising=False)
    monkeypatch.setattr(
        service, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified")
    )

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(harness="codex-native", policy="codex-on-demand@1")

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    store = InMemoryExecutionPlanStore()
    artifacts = _ArtifactService()

    async def admit():
        return await service.compile_and_persist_execution_plan(
            session_factory=object(),
            execution_plan_store=store,
            artifact_service=artifacts,
            principal="user-1",
            workflow_id="mm:codex-repository-admission",
            agent_profile_snapshot=_snapshot(
                harness="codex-native", policy="codex-on-demand@1", provider_id="codex"
            ),
            provider_profile=SimpleNamespace(
                profile_id="codex", runtime_id="codex_cli", provider_id="openai"
            ),
            initial_parameters={
                "model": "example/model",
                "targetRuntime": "omnigent",
                "publishMode": "none",
                "workflow": {"instructions": "Read the admitted repository."},
            },
            authored_request_ref="art_request_1",
            authored_request_digest="sha256:" + "1" * 64,
            task_input_snapshot_ref="art_request_1",
            task_input_snapshot_digest="sha256:" + "1" * 64,
            repository_bindings={
                "source": {
                    "authorityKind": "repository_connection",
                    "connectionRef": "selected-repository",
                    "repositoryAccessSnapshotRef": "repository-access-snapshot:sha256:"
                    + "2" * 64,
                    "materializerRef": "repository-broker@1",
                    "repositoryRole": "source_read",
                }
            },
            trusted_repository_declarations={
                "source": {
                    "allowedRoles": ("source_read",),
                    "allowedMaterializers": ("repository-broker@1",),
                }
            },
            workspace_source_kind="repository",
        )

    if generic_admitted:
        compiled = await admit()
        assert (
            compiled.envelope.payload.executionRealizerRef == "generic-omnigent-host@1"
        )
        assert await store.load(compiled.envelope.planRef) == compiled.envelope
    else:
        with pytest.raises(HarnessPlatformError, match="realizer.*repository"):
            await admit()
        assert store._plans == {}
        assert all(
            json.loads(payload).get("schemaVersion")
            != "moonmind.omnigent-execution-plan-envelope.v1"
            for payload in artifacts.payloads.values()
        )


@pytest.mark.asyncio
async def test_credentialless_zen_plan_uses_noop_materializer(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )

    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref="opencode-on-demand@1",
        plan_store=_PlanStore(object()),
        provider_id="opencode",
    )

    binding = result.envelope.payload.credentialBindings["primary-model"]
    assert binding.materializerRef == "none@1"


@pytest.mark.asyncio
async def test_resolved_skill_capabilities_drive_plan_and_mounted_tools(
    monkeypatch,
) -> None:
    async def resolve_skills(**_kwargs):
        return (
            SimpleNamespace(
                skills=[
                    SimpleNamespace(required_capabilities=["git", "gh"]),
                    SimpleNamespace(required_capabilities=["Git"]),
                ]
            ),
            "art_skill_manifest",
            "sha256:" + "5" * 64,
            (),
        )

    monkeypatch.setattr(service, "_resolve_and_persist_skills", resolve_skills)

    def resolve_evidence(plan_payload, **_kwargs):
        return _protected_support_evidence(plan_payload), "supported"

    monkeypatch.setattr(service, "resolve_execution_evidence", resolve_evidence)
    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref="opencode-on-demand@1",
        plan_store=_PlanStore(object()),
    )

    assert result.envelope.payload.resolvedTools["tools"] == ["gh"]
    assert result.envelope.payload.classAdmissionDecision["requiredSatisfied"] == [
        "gh",
        "git",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("contract_kind", ["skill", "tool"])
async def test_authored_step_capabilities_reach_mounted_tool_authority(
    monkeypatch,
    contract_kind,
) -> None:
    from unittest.mock import AsyncMock

    from api_service.api.routers.executions import _merge_workflow_required_capabilities
    from moonmind.omnigent.host_services.github_credentials import (
        OmnigentGithubCredentialService,
    )
    from moonmind.omnigent.host_services.mounted_tools import OmnigentMountedToolService

    requirements = _merge_workflow_required_capabilities(
        [],
        {},
        steps=[{contract_kind: {"requiredCapabilities": ["git", "GH"]}}],
    )
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda payload, **_kwargs: (_protected_support_evidence(payload), "supported"),
    )
    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref="opencode-on-demand@1",
        plan_store=_PlanStore(object()),
        extra_parameters={"requiredCapabilities": requirements},
    )
    resolved = result.envelope.payload.resolvedTools
    backend = SimpleNamespace(run=AsyncMock())
    mounts = await OmnigentMountedToolService(backend=backend).materialize(resolved)
    assert mounts[0]["tools"][0]["name"] == "gh"
    assert mounts[0]["accessMode"] == "read-only"
    assert OmnigentGithubCredentialService.required(resolved)
    assert result.envelope.payload.classAdmissionDecision["requiredSatisfied"] == [
        "gh",
        "git",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["workflow", "step"])
async def test_workflow_cannot_self_attest_an_unknown_capability(
    monkeypatch,
    source,
) -> None:
    """Authored requirements are requests, never bridge support evidence."""

    from api_service.api.routers.executions import _merge_workflow_required_capabilities
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    requirements = _merge_workflow_required_capabilities(
        ["custom-capability"] if source == "workflow" else [],
        {},
        steps=(
            [{"skill": {"requiredCapabilities": ["custom-capability"]}}]
            if source == "step"
            else []
        ),
    )

    with pytest.raises(HarnessPlatformError, match="custom-capability"):
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            extra_parameters={"requiredCapabilities": requirements},
        )


@pytest.mark.asyncio
async def test_docker_only_plan_materializes_the_projected_container_cli(monkeypatch):
    from unittest.mock import AsyncMock

    from moonmind.omnigent.host_services.github_credentials import (
        OmnigentGithubCredentialService,
    )
    from moonmind.omnigent.host_services.mounted_tools import OmnigentMountedToolService

    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda payload, **_kwargs: (_protected_support_evidence(payload), "supported"),
    )
    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref="opencode-on-demand@1",
        plan_store=_PlanStore(object()),
        extra_parameters={"requiredCapabilities": ["docker"]},
    )
    resolved = result.envelope.payload.resolvedTools
    assert resolved["tools"] == ["docker"]
    mounts = await OmnigentMountedToolService(
        backend=SimpleNamespace(run=AsyncMock())
    ).materialize(resolved)
    assert mounts[0]["tools"][0]["path"] == "bin/moonmind"
    assert mounts[0]["tools"][0]["executableDigests"]
    assert not OmnigentGithubCredentialService.required(resolved)


@pytest.mark.asyncio
async def test_selected_omnigent_runtime_is_safe_for_pre_cutover_worker(
    monkeypatch,
) -> None:
    """New API plans preserve the class-decision shape an old worker consumes."""

    def resolve_evidence(plan_payload, **_kwargs):
        return _protected_support_evidence(plan_payload), "supported"

    monkeypatch.setattr(service, "resolve_execution_evidence", resolve_evidence)
    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref="opencode-on-demand@1",
        plan_store=_PlanStore(object()),
        extra_parameters={"requiredCapabilities": ["omnigent"]},
    )

    decision = result.envelope.payload.classAdmissionDecision
    legacy_decision = _LegacyClassAdmissionDecision.model_validate(decision)
    assert legacy_decision.requiredSatisfied == ()
    assert "exactHostRequired" not in decision


@pytest.mark.asyncio
async def test_resolved_fanout_is_admitted_as_platform_owned_capability(
    monkeypatch,
) -> None:
    """A trusted batch Skill must compile without inventing host evidence."""

    async def resolve_skills(**_kwargs):
        return (
            SimpleNamespace(
                skills=[SimpleNamespace(required_capabilities=["execution.fanout"])]
            ),
            "art_skill_manifest",
            "sha256:" + "5" * 64,
            (),
        )

    monkeypatch.setattr(service, "_resolve_and_persist_skills", resolve_skills)

    def resolve_evidence(plan_payload, **_kwargs):
        return _protected_support_evidence(plan_payload), "supported"

    monkeypatch.setattr(service, "resolve_execution_evidence", resolve_evidence)
    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref="opencode-on-demand@1",
        plan_store=_PlanStore(object()),
    )

    decision = result.envelope.payload.classAdmissionDecision
    legacy_decision = _LegacyClassAdmissionDecision.model_validate(decision)
    assert legacy_decision.requiredSatisfied == ()
    assert legacy_decision.unknown == ()
    assert result.envelope.payload.resolvedTools["tools"] == []


async def _capture_plan_payload(
    *,
    launch_policy_ref: str,
    provider_id: str = "opencode-go",
):
    """Return the compiled plan payload deployment qualification must match.

    Bootstrap qualification compiles the same plan to learn the exact support
    identity it has to attest, so the test derives evidence the same way.
    """

    captured: dict[str, object] = {}

    def _capture(plan_payload, **_kwargs):
        captured["payload"] = plan_payload
        raise ValueError("captured")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service, "resolve_execution_evidence", _capture)
        with pytest.raises(ValueError):
            await _compile_opencode_plan(
                patch,
                artifacts=_ArtifactService(),
                launch_policy_ref=launch_policy_ref,
                provider_id=provider_id,
            )
    return captured["payload"]


def _write_deployment_evidence(
    tmp_path, monkeypatch, *, plan_payload, launch_policy_ref: str
) -> None:
    """Sign and publish deployment evidence for one exact combination."""

    from moonmind.omnigent.bootstrap.evidence import (
        build_deployment_evidence,
        write_deployment_evidence,
    )
    from moonmind.omnigent.harness_platform.support import (
        compute_support_combination_key,
    )

    identity = plan_payload.supportIdentity.model_copy(
        update={"launchPolicyRef": launch_policy_ref}
    )
    evidence = build_deployment_evidence(
        support_identity=identity,
        support_combination_key=compute_support_combination_key(identity),
        host_image_ref=plan_payload.hostImageRef,
        policy_snapshot_digest=plan_payload.policySnapshotDigest,
        effective_launch_snapshot_digest=(plan_payload.effectiveLaunchSnapshotDigest),
        provider_profile_ref="provider-opencode-native",
        credential_generation=1,
        qualified_model_id="example/model",
        effort="xhigh",
        results={"readQualification": "passed"},
        evidence_refs={"readRun": "artifact:read-run"},
        resolved_state=None,
    )
    # Keep the writer's Compose mirror inside this test's owned directory.
    from moonmind.omnigent.bootstrap import evidence as bootstrap_evidence

    original_path = bootstrap_evidence.Path

    def evidence_path(value):
        if (
            str(value)
            == "/workspace/omnigent-evidence/deployment-execution-evidence.json"
        ):
            return tmp_path / "compose-deployment-execution-evidence.json"
        return original_path(value)

    monkeypatch.setattr(bootstrap_evidence, "Path", evidence_path)
    destination = tmp_path / "deployment-execution-evidence.json"
    write_deployment_evidence(evidence, path=destination)
    monkeypatch.setenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", str(destination))


@pytest.mark.asyncio
async def test_deployment_evidence_admits_the_launch_policy_admission_selects(
    tmp_path, monkeypatch
) -> None:
    """Qualification derived from the Agent Profile admits the compiled plan."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    admitted_policy = default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES)
    plan_payload = await _capture_plan_payload(launch_policy_ref=admitted_policy)
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=plan_payload,
        launch_policy_ref=admitted_policy,
    )

    artifacts = _ArtifactService()
    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=artifacts,
        launch_policy_ref=admitted_policy,
        plan_store=_PlanStore(None),
    )

    admission = result.envelope.payload.admissionAuthority
    assert admission is not None
    assert admission.supportTier == "deployment_qualified"


@pytest.mark.asyncio
async def test_deployment_evidence_for_another_launch_policy_is_inadmissible(
    tmp_path, monkeypatch
) -> None:
    """Evidence qualified for a launch policy admission never selects fails."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    admitted_policy = default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES)
    unselected_policy = _OPENCODE_ALLOWED_LAUNCH_POLICIES[1]
    assert unselected_policy != admitted_policy
    plan_payload = await _capture_plan_payload(launch_policy_ref=admitted_policy)
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=plan_payload,
        launch_policy_ref=unselected_policy,
    )

    with pytest.raises(ValueError) as excinfo:
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref=admitted_policy,
            plan_store=_PlanStore(None),
        )
    assert "execution evidence unavailable under policy=deployment" in str(
        excinfo.value
    )


def test_launch_policy_is_part_of_the_support_combination_key() -> None:
    """A restated launch policy changes the exact combination evidence binds."""

    assert default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES) == (
        _OPENCODE_ALLOWED_LAUNCH_POLICIES[0]
    )
    with pytest.raises(ValueError):
        default_launch_policy_ref([])


@pytest.mark.asyncio
async def test_deployment_evidence_admits_a_plan_that_requests_capabilities(
    tmp_path, monkeypatch
) -> None:
    """Required capabilities are per-run intent, not a qualification dimension.

    Class admission already refuses unsupported or unknown capabilities before
    the support key exists, so binding deployment evidence to one capability
    set would make every ordinary workflow inadmissible.
    """

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    admitted_policy = default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES)
    plan_payload = await _capture_plan_payload(launch_policy_ref=admitted_policy)
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=plan_payload,
        launch_policy_ref=admitted_policy,
    )

    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref=admitted_policy,
        plan_store=_PlanStore(None),
        extra_parameters={"requiredCapabilities": ["session.start"]},
    )

    payload = result.envelope.payload
    assert payload.admissionAuthority.supportTier == "deployment_qualified"
    # The qualified combination did not include this capability set.
    assert (
        payload.supportIdentity.requiredCapabilitiesDigest
        != plan_payload.supportIdentity.requiredCapabilitiesDigest
    )


@pytest.mark.asyncio
async def test_unsupported_required_capability_is_refused_before_evidence(
    tmp_path, monkeypatch
) -> None:
    """Relaxing the qualification match must not weaken the capability gate."""

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    admitted_policy = default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES)
    plan_payload = await _capture_plan_payload(launch_policy_ref=admitted_policy)
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=plan_payload,
        launch_policy_ref=admitted_policy,
    )

    with pytest.raises(HarnessPlatformError) as excinfo:
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref=admitted_policy,
            plan_store=_PlanStore(None),
            extra_parameters={"requiredCapabilities": ["streaming"]},
        )
    assert "streaming" in str(excinfo.value)


@pytest.mark.asyncio
async def test_default_evidence_policy_admits_a_selected_profile_model(
    tmp_path, monkeypatch
) -> None:
    """Per-run model choice must not require deployment requalification."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    admitted_policy = default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES)
    plan_payload = await _capture_plan_payload(launch_policy_ref=admitted_policy)
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=plan_payload,
        launch_policy_ref=admitted_policy,
    )

    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref=admitted_policy,
        plan_store=_PlanStore(None),
        extra_parameters={
            "model": "opencode/muse-spark-1.2-contributor-free",
            "effort": "medium",
        },
    )

    payload = result.envelope.payload
    assert payload.admissionAuthority.supportTier == "deployment_qualified"
    assert payload.modelConfig.qualifiedId == (
        "opencode/muse-spark-1.2-contributor-free"
    )
    assert (
        payload.supportIdentity.modelConfigDigest
        != plan_payload.supportIdentity.modelConfigDigest
    )


@pytest.mark.asyncio
async def test_untrusted_evidence_values_are_redacted_from_admission_errors(
    tmp_path, monkeypatch
) -> None:
    """Malformed evidence is never reflected into workflow-visible errors."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    plan_payload = await _capture_plan_payload(
        launch_policy_ref=default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES)
    )
    untrusted_value = "sensitive-candidate-value"
    candidate_identity = plan_payload.supportIdentity.model_dump(
        mode="json", by_alias=True
    )
    candidate_identity["launchPolicyRef"] = untrusted_value
    destination = tmp_path / "deployment-execution-evidence.json"
    destination.write_text(
        json.dumps({"entries": [{"supportIdentity": candidate_identity}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", str(destination))

    with pytest.raises(ValueError) as excinfo:
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref=default_launch_policy_ref(
                _OPENCODE_ALLOWED_LAUNCH_POLICIES
            ),
            plan_store=_PlanStore(None),
        )

    message = str(excinfo.value)
    assert "launchPolicyRef differs" in message
    assert untrusted_value not in message
    assert "/api/omnigent/bootstrap/opencode/retry" not in message


@pytest.mark.asyncio
async def test_create_plan_reports_bootstrap_quarantine_instead_of_invalid_digest(
    monkeypatch,
):
    """A pinned pre-cache image must expose its real rejection at Create."""
    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    host_ref = "ghcr.io/example/omnigent-host@sha256:" + "7" * 64
    state = SimpleNamespace(
        server_image_ref=_SERVER_IMAGE_REF,
        opencode_host_image_ref=host_ref,
        details={
            "opencodeHostCompatibility": {
                "status": "blocked",
                "failureCode": "omnigent_host_bootstrap_contract_missing",
                "serverImageRef": _SERVER_IMAGE_REF,
                "hostImageRef": host_ref,
            }
        },
    )
    monkeypatch.setattr(store, "load_resolved_state", lambda: state)
    with pytest.raises(
        HarnessPlatformError, match="omnigent_host_bootstrap_contract_missing"
    ):
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
        )


def test_build_v2_profile_keeps_stable_agent_source_across_model_only_bump() -> None:
    """A model-only Agent Profile version must not change agentSourceRef.

    The deployment qualification key excludes per-run model/effort, so a
    default-model migration (for example 1.2 -> 1.3) must not invalidate
    deployment evidence with ``agentSourceRef differs``. The stable upstream
    projection digest from the document owns the agent source, not the
    profile version digest which includes the default model.
    """
    import hashlib

    stable_projection = "sha256:" + "d" * 64

    def _doc(model_qualified: str) -> dict:
        return {
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1",
                "upstreamSnapshotDigest": stable_projection,
            },
            "harness": {"id": "opencode-native"},
            "providerRequirements": {},
            "model": {"qualifiedId": model_qualified, "effort": "xhigh"},
            "workspace": {"mutation": "allowed"},
            "skills": [],
            "tools": [],
            "capture": {"stream": True, "evidence": True},
            "continuations": {"checkpoint": True, "branch": True},
            "publish": {"mode": "none"},
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }

    def _snapshot(doc: dict, version: int) -> dict:
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        return {
            "document": doc,
            "digest": digest,
            "version": version,
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }

    doc12 = _doc("opencode-go/muse-spark-1.2-contributor")
    doc13 = _doc("opencode-go/muse-spark-1.3-contributor")
    # Version digests differ because the default model differs.
    assert _snapshot(doc12, 27)["digest"] != _snapshot(doc13, 28)["digest"]

    catalog_ref = "omnigent-harness-catalog:sha256:" + "e" * 64
    impl_ref = "omnigent-harness-implementation:sha256:" + "c" * 64
    v2_12 = service._build_v2_profile(
        snapshot=_snapshot(doc12, 27),
        catalog_ref=catalog_ref,
        implementation_ref=impl_ref,
        harness_id="opencode-native",
        auth_model="own-auth",
    )
    v2_13 = service._build_v2_profile(
        snapshot=_snapshot(doc13, 28),
        catalog_ref=catalog_ref,
        implementation_ref=impl_ref,
        harness_id="opencode-native",
        auth_model="own-auth",
    )
    src12 = v2_12.source.model_dump(by_alias=True, mode="json")
    src13 = v2_13.source.model_dump(by_alias=True, mode="json")
    assert src12 == src13
    assert src12["upstreamSnapshotDigest"] == stable_projection


@pytest.mark.asyncio
async def test_ordinary_admission_compiles_without_historical_certificate(
    monkeypatch,
) -> None:
    """Ordinary work reaches execution without requalification (MM#4560).

    Requested authenticated OpenCode policy with historical evidence naming
    another revision (or no evidence at all): when the actual requested
    policy/runtime checks pass, ordinary admission succeeds without a
    historical certificate and never fabricates a passing one.
    """

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", "/nonexistent/no-evidence.json"
    )
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE",
        "/nonexistent/no-evidence.json",
    )

    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref=default_launch_policy_ref(
            _OPENCODE_ALLOWED_LAUNCH_POLICIES
        ),
        plan_store=_PlanStore(None),
    )

    admission = result.envelope.payload.admissionAuthority
    assert admission is not None
    assert admission.admissionMode == "ordinary"
    assert admission.supportTier == "uncertified"
    assert admission.supportEvidenceRef == ""
    assert admission.supportEvidenceDigest == ""


@pytest.mark.asyncio
async def test_ordinary_evidence_persistence_failure_falls_back_to_uncertified(
    monkeypatch,
) -> None:
    """An optional evidence write must not veto ordinary admission (MM#4560).

    When ordinary admission finds a valid historical certificate but the
    optional support-evidence artifact write fails, compilation falls back
    to the already supported uncertified authority instead of aborting --
    the same outcome as if the certificate had been absent.
    """

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )
    real_persist = service.persist_json_artifact

    async def _fail_support_evidence_write(*, artifact_class, **kwargs):
        if artifact_class == "omnigent.execution_support_evidence":
            raise RuntimeError("artifact store unavailable")
        return await real_persist(artifact_class=artifact_class, **kwargs)

    monkeypatch.setattr(service, "persist_json_artifact", _fail_support_evidence_write)

    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref=default_launch_policy_ref(
            _OPENCODE_ALLOWED_LAUNCH_POLICIES
        ),
        plan_store=_PlanStore(None),
    )

    admission = result.envelope.payload.admissionAuthority
    assert admission is not None
    assert admission.admissionMode == "ordinary"
    assert admission.supportTier == "uncertified"
    assert admission.supportEvidenceRef == ""
    assert admission.supportEvidenceDigest == ""


@pytest.mark.asyncio
async def test_strict_evidence_persistence_failure_still_fails_closed(
    monkeypatch,
) -> None:
    """Explicit strict certification keeps a failed evidence write fatal."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected")
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )

    real_persist = service.persist_json_artifact

    async def _fail_support_evidence_write(*, artifact_class, **kwargs):
        if artifact_class == "omnigent.execution_support_evidence":
            raise RuntimeError("artifact store unavailable")
        return await real_persist(artifact_class=artifact_class, **kwargs)

    monkeypatch.setattr(service, "persist_json_artifact", _fail_support_evidence_write)

    with pytest.raises(RuntimeError, match="artifact store unavailable"):
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref=default_launch_policy_ref(
                _OPENCODE_ALLOWED_LAUNCH_POLICIES
            ),
            plan_store=_PlanStore(None),
        )


@pytest.mark.asyncio
async def test_strict_deployment_still_rejects_mismatched_historical_certificate(
    tmp_path, monkeypatch
) -> None:
    """Explicit strict certification keeps rejecting another policy's evidence."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    admitted_policy = default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES)
    unselected_policy = _OPENCODE_ALLOWED_LAUNCH_POLICIES[1]
    assert unselected_policy != admitted_policy
    plan_payload = await _capture_plan_payload(launch_policy_ref=admitted_policy)
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=plan_payload,
        launch_policy_ref=unselected_policy,
    )

    with pytest.raises(ValueError) as excinfo:
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref=admitted_policy,
            plan_store=_PlanStore(None),
        )
    assert "execution evidence unavailable" in str(excinfo.value)
    admission = _PlanStore.persisted
    _ = admission
