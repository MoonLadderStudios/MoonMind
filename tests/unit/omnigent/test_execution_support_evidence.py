from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from moonmind.omnigent.execution_support_evidence import (
    EXECUTION_SUPPORT_EVIDENCE_ISSUER,
    EXECUTION_SUPPORT_EVIDENCE_VERSION,
    assert_protected_evidence_matches_plan,
    load_protected_execution_support_evidence,
    validate_protected_execution_support_evidence,
)
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_support_combination_key,
)
from moonmind.omnigent.session_supervisor_rollback import (
    SUPERVISOR_ROLLBACK_POLICY_VERSION,
)
from moonmind.schemas.omnigent_session_models import (
    OMNIGENT_SESSION_COMPATIBILITY_VERSION,
    OMNIGENT_SESSION_FEATURE_GENERATION,
)


def _identity(*, model_digest: str = "sha256:" + "1" * 64) -> SupportKeyPayload:
    return SupportKeyPayload(
        omnigentServerBuildRef="sha256:" + "2" * 64,
        omnigentHostBuildRef="sha256:" + "3" * 64,
        harnessImplementationRef=(
            "omnigent-harness-implementation:sha256:" + "4" * 64
        ),
        vendorRuntimeRefs=["opencode@1.2.3#sha256:" + "5" * 64],
        agentSourceRef="agent-source:sha256:" + "6" * 64,
        materializerRefs=["opencode-auth-json@1"],
        providerCompatibilityClass="omnigent-provider-binding-set@1",
        hostClassRef="omnigent-opencode@1",
        architecture="linux/amd64",
        launchPolicyRef="opencode-on-demand@1",
        modelConfigDigest=model_digest,
        executionRealizerRef="generic-omnigent-host@1",
        requiredCapabilitiesDigest="sha256:" + "7" * 64,
    )


def _plan(identity: SupportKeyPayload | None = None) -> SimpleNamespace:
    selected = identity or _identity()
    return SimpleNamespace(
        supportIdentity=selected,
        supportCombinationKey=compute_support_combination_key(selected),
        hostImageRef="ghcr.io/example/opencode@sha256:" + "8" * 64,
        policySnapshotDigest="sha256:" + "9" * 64,
        effectiveLaunchSnapshotDigest="sha256:" + "a" * 64,
        admissionAuthority=SimpleNamespace(
            featureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
            replayCompatibilityVersion=OMNIGENT_SESSION_COMPATIBILITY_VERSION,
            rollbackPolicyVersion=SUPERVISOR_ROLLBACK_POLICY_VERSION,
        ),
    )


def _evidence(
    plan: SimpleNamespace, *, generated_at: datetime | None = None
) -> dict[str, object]:
    now = generated_at or datetime.now(UTC)
    return {
        "schemaVersion": EXECUTION_SUPPORT_EVIDENCE_VERSION,
        "evidenceIssuer": EXECUTION_SUPPORT_EVIDENCE_ISSUER,
        "status": "passed",
        "sourceCommit": "abcdef1234567890",
        "protectedRunRef": "https://example.invalid/actions/runs/123",
        "evidenceManifestRef": "artifact://manifest-123",
        "evidenceManifestDigest": "sha256:" + "b" * 64,
        "generatedAt": now.isoformat(),
        "expiresAt": (now + timedelta(days=7)).isoformat(),
        "supportClassification": "fully_managed",
        "supportCombinationKey": plan.supportCombinationKey,
        "supportIdentity": plan.supportIdentity.model_dump(
            mode="json", by_alias=True
        ),
        "hostImageRef": plan.hostImageRef,
        "policySnapshotDigest": plan.policySnapshotDigest,
        "effectiveLaunchSnapshotDigest": plan.effectiveLaunchSnapshotDigest,
        "policyGateRef": "deployment-ready",
        "policyQualified": True,
        "exactArtifactsVerified": True,
        "featureGeneration": OMNIGENT_SESSION_FEATURE_GENERATION,
        "replayCompatibilityVersion": OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        "rollbackPolicyVersion": SUPERVISOR_ROLLBACK_POLICY_VERSION,
    }


def test_loader_selects_one_exact_protected_combination(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    other = _plan(_identity(model_digest="sha256:" + "c" * 64))
    path = tmp_path / "execution-support-evidence.json"
    path.write_text(
        json.dumps({"entries": [_evidence(other), _evidence(plan)]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", str(path))
    monkeypatch.setenv("MOONMIND_SOURCE_COMMIT", "abcdef1234567890")

    loaded = load_protected_execution_support_evidence(plan)

    assert loaded["supportCombinationKey"] == plan.supportCombinationKey


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("stale", "stale or expired"),
        ("wrong_commit", "source commit"),
        ("wrong_issuer", "evidenceIssuer"),
        ("insufficient", "classification is not admissible"),
    ],
)
def test_protected_evidence_fails_closed(
    mutation: str, message: str
) -> None:
    plan = _plan()
    evidence = _evidence(plan)
    expected_commit = "abcdef1234567890"
    now = datetime.now(UTC)
    if mutation == "stale":
        evidence = _evidence(plan, generated_at=now - timedelta(days=40))
        evidence["expiresAt"] = (now + timedelta(days=1)).isoformat()
    elif mutation == "wrong_commit":
        expected_commit = "different123456"
    elif mutation == "wrong_issuer":
        evidence["evidenceIssuer"] = "untrusted-conformance@1"
    else:
        evidence["supportClassification"] = "experimental"

    with pytest.raises(ValueError, match=message):
        validate_protected_execution_support_evidence(
            evidence, now=now, expected_source_commit=expected_commit
        )


def test_protected_evidence_rejects_exact_model_or_policy_drift() -> None:
    plan = _plan()
    evidence = validate_protected_execution_support_evidence(_evidence(plan))
    drifted = _plan(_identity(model_digest="sha256:" + "d" * 64))

    with pytest.raises(ValueError, match="conflicts with the execution plan"):
        assert_protected_evidence_matches_plan(evidence, drifted)

    plan.policySnapshotDigest = "sha256:" + "e" * 64
    with pytest.raises(ValueError, match="conflicts with the execution plan"):
        assert_protected_evidence_matches_plan(evidence, plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidenceManifestDigest", "sha256:not-a-digest"),
        ("policySnapshotDigest", "sha256:" + "1" * 63),
        ("effectiveLaunchSnapshotDigest", "sha256:" + "2" * 65),
        ("hostImageRef", "ghcr.io/example/opencode:mutable"),
    ],
)
def test_protected_evidence_requires_exact_immutable_artifacts(
    field: str, value: str
) -> None:
    plan = _plan()
    evidence = _evidence(plan)
    evidence[field] = value

    with pytest.raises(ValueError):
        validate_protected_execution_support_evidence(evidence)
