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
from moonmind.omnigent.harness_platform.catalog import create_catalog_snapshot
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
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


@pytest.fixture(autouse=True)
def _ready_opencode_image_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give OpenCode plan tests exact resolver evidence for selected refs."""

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER_IMAGE_REF)

    def load_state():
        import os

        host_ref = os.environ.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "")
        if not host_ref:
            return None
        return SimpleNamespace(
            server_image_ref=_SERVER_IMAGE_REF,
            opencode_host_image_ref=host_ref,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_IMAGE_REF,
                    "hostImageRef": host_ref,
                }
            },
        )

    monkeypatch.setattr(store, "load_resolved_state", load_state)


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


def test_skill_selector_includes_nested_dynamic_execution_skills() -> None:
    selector = service._skill_selector(
        {
            "workflow": {
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
async def test_admission_persists_nested_dynamic_execution_skills() -> None:
    artifacts = _ArtifactService()

    resolved, manifest_ref, _manifest_digest, content_refs = (
        await service._resolve_and_persist_skills(
            session_factory=object(),
            artifact_service=artifacts,
            principal="user-1",
            workflow_id="mm:remediation-skill-snapshot",
            task_input_snapshot_digest="sha256:" + "1" * 64,
            initial_parameters={
                "workflow": {
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


def _snapshot(*, harness: str, policy: str, provider_id: str) -> dict:
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
                "runtimeId": ("codex_cli" if harness == "codex-native" else "opencode"),
                "providerIds": ["openai"],
                "credentialSource": (
                    "oauth_volume" if harness == "codex-native" else "secret_ref"
                ),
                "materializationMode": (
                    "oauth_home" if harness == "codex-native" else "generated_file"
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
        "opencode" if harness == "opencode-native" else "codex"
    ]
    document["providerProfile"]["compatibleProviders"] = [
        "opencode" if harness == "opencode-native" else "codex"
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
            runtime_id=("opencode" if harness == "opencode-native" else "codex_cli"),
            provider_id=("opencode-go" if harness == "opencode-native" else "openai"),
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

    from moonmind.workflows.temporal.activities.omnigent_session_activities import (
        _validate_plan_support_authority,
    )
    from moonmind.omnigent import deployment_identity

    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: result.envelope.payload.supportIdentity.omnigentServerBuildRef,
    )
    _validate_plan_support_authority(result.envelope)

    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: "sha256:" + "9" * 64,
    )
    with pytest.raises(
        deployment_identity.OmnigentDeploymentIdentityConflict,
        match="no longer deployed",
    ):
        _validate_plan_support_authority(result.envelope)


@pytest.mark.asyncio
async def test_product_boundary_uses_profile_catalog_build_identity(
    monkeypatch,
) -> None:
    """A server image manifest digest must not replace the shared build ref."""

    build_identity = "sha256:" + "b" * 64
    implementation_digest = "sha256:" + "c" * 64
    catalog = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.11.0",
        omnigentBuildDigest=build_identity,
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
    harness = catalog.harnesses[0]

    async def load_authority(**_kwargs):
        return {
            "hostClassRef": "omnigent-opencode@1",
            "implementationDigest": implementation_digest,
            "materializerRef": "opencode-auth-json@1",
            "authModel": "own-auth",
            "integrationMode": "native-server",
            "_catalogSnapshot": catalog,
            "_harnessRecord": harness,
        }

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(
            harness="opencode-native",
            policy="opencode-on-demand@1",
        )

    monkeypatch.setattr(service, "_try_load_real_harness_config", load_authority)
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

    result = await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=_ArtifactService(),
        principal="user-1",
        workflow_id="mm:test-profile-catalog-build",
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
            "workflow": {"instructions": "Use the pinned catalog."},
        },
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
        execution_plan_store=_PlanStore(object()),
    )

    support = result.envelope.payload.supportIdentity
    assert support.omnigentServerBuildRef == build_identity
    assert support.omnigentHostBuildRef == build_identity
    assert result.envelope.payload.harnessCatalogRef == catalog.catalogRef


async def _compile_opencode_plan(
    monkeypatch,
    *,
    artifacts,
    launch_policy_ref: str,
    plan_store=None,
    extra_parameters: dict | None = None,
    provider_id: str = "opencode-go",
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
    return await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="mm:test-deployment-evidence",
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
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
        execution_plan_store=plan_store,
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
async def test_workflow_cannot_self_attest_an_unknown_capability(
    monkeypatch,
) -> None:
    """Authored requirements are requests, never bridge support evidence."""

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    with pytest.raises(HarnessPlatformError, match="custom-capability"):
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            extra_parameters={"requiredCapabilities": ["custom-capability"]},
        )


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
                skills=[
                    SimpleNamespace(
                        required_capabilities=["execution.fanout"]
                    )
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
