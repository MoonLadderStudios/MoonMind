from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from api_service.services import omnigent_execution_plan_service as service
from api_service.services.omnigent_policies import bootstrap_document
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.omnigent.execution_support_evidence import (
    EXECUTION_SUPPORT_EVIDENCE_ISSUER,
    EXECUTION_SUPPORT_EVIDENCE_VERSION,
)
from moonmind.omnigent.session_supervisor_rollback import (
    SUPERVISOR_ROLLBACK_POLICY_VERSION,
)
from moonmind.schemas.omnigent_session_models import (
    OMNIGENT_SESSION_COMPATIBILITY_VERSION,
    OMNIGENT_SESSION_FEATURE_GENERATION,
)


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
                "runtimeId": (
                    "codex_cli" if harness == "codex-native" else "opencode"
                ),
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
    *, harness: str, policy: str, host_image_ref: str | None = None
) -> dict:
    profile_ref = f"omnigent-{harness.removesuffix('-native')}@1"
    host_image_digest = "7" if harness == "opencode-native" else "f"
    document = bootstrap_document(
        host_mode="on_demand_docker",
        execution_profile_ref=profile_ref,
        server_image_ref="ghcr.io/example/omnigent-server@sha256:" + "a" * 64,
        host_image_ref=host_image_ref
        or "ghcr.io/example/omnigent-host@sha256:"
        + host_image_digest * 64,
    ).model_dump(mode="json", by_alias=True)
    document["execution"]["harness"] = harness
    document["execution"]["agentIdentities"] = [
        "opencode" if harness == "opencode-native" else "codex"
    ]
    document["providerProfile"]["compatibleProviders"] = [
        "opencode" if harness == "opencode-native" else "codex"
    ]
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
        "effectiveLaunchSnapshotDigest": (
            plan_payload.effectiveLaunchSnapshotDigest
        ),
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
            provider_id="openai",
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
    assert result.envelope.payload.effectiveLaunchSnapshotRef.startswith(
        "artifact:"
    )
    assert result.envelope.payload.effectiveLaunchSnapshotDigest.startswith(
        "sha256:"
    )
    assert result.envelope.payload.hostImageRef
    assert result.envelope.payload.hostArchitecture == "linux/amd64"
    assert result.envelope.payload.authority is not None
    assert result.envelope.payload.authority.taskInputSnapshotRef == "art_request_1"
    admission = result.envelope.payload.admissionAuthority
    assert admission is not None
    assert admission.featureGeneration == OMNIGENT_SESSION_FEATURE_GENERATION
    assert (
        admission.replayCompatibilityVersion
        == OMNIGENT_SESSION_COMPATIBILITY_VERSION
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
