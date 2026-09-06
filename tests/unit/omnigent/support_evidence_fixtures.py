"""Shared builders for protected execution-support documents.

Source issue: MoonLadderStudios/MoonMind#3885.

The protected support index is read by admission, by the rollout readiness
probe, by the operator migration projection, and by the publisher that writes
it. One builder keeps every one of those tests describing the same document, so
a schema change cannot pass in one suite while another still asserts the old
shape.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from moonmind.omnigent.execution_support_evidence import (
    EXECUTION_SUPPORT_EVIDENCE_ISSUER,
    EXECUTION_SUPPORT_EVIDENCE_VERSION,
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


def support_identity(
    *,
    model_digest: str = "sha256:" + "1" * 64,
    host_class_ref: str = "omnigent-opencode@1",
    execution_realizer_ref: str = "generic-omnigent-host@1",
) -> SupportKeyPayload:
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
        hostClassRef=host_class_ref,
        architecture="linux/amd64",
        launchPolicyRef="opencode-on-demand@1",
        modelConfigDigest=model_digest,
        executionRealizerRef=execution_realizer_ref,
        requiredCapabilitiesDigest="sha256:" + "7" * 64,
    )


def support_plan(identity: SupportKeyPayload | None = None) -> SimpleNamespace:
    """The plan-shaped view admission compares a document against."""

    selected = identity or support_identity()
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


def support_row(
    plan: SimpleNamespace,
    *,
    generated_at: datetime | None = None,
    status: str = "passed",
    ttl: timedelta = timedelta(days=7),
) -> dict[str, Any]:
    now = generated_at or datetime.now(UTC)
    return {
        "schemaVersion": EXECUTION_SUPPORT_EVIDENCE_VERSION,
        "evidenceIssuer": EXECUTION_SUPPORT_EVIDENCE_ISSUER,
        "status": status,
        "sourceCommit": "abcdef1234567890",
        "protectedRunRef": "https://example.invalid/actions/runs/123",
        "evidenceManifestRef": "artifact://manifest-123",
        "evidenceManifestDigest": "sha256:" + "b" * 64,
        "generatedAt": now.isoformat(),
        "expiresAt": (now + ttl).isoformat(),
        "supportClassification": "fully_managed",
        "supportCombinationKey": plan.supportCombinationKey,
        "supportIdentity": plan.supportIdentity.model_dump(
            mode="json", by_alias=True
        ),
        "hostImageRef": plan.hostImageRef,
        "policySnapshotDigest": plan.policySnapshotDigest,
        "effectiveLaunchSnapshotDigest": plan.effectiveLaunchSnapshotDigest,
        "policyGateRef": "deployment-ready",
        "policyQualified": status == "passed",
        "exactArtifactsVerified": True,
        "featureGeneration": OMNIGENT_SESSION_FEATURE_GENERATION,
        "replayCompatibilityVersion": OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        "rollbackPolicyVersion": SUPERVISOR_ROLLBACK_POLICY_VERSION,
    }


def concurrency_record(
    plan: SimpleNamespace, *, level: int
) -> dict[str, Any]:
    """A concurrency record whose highest cross-layer observed level is ``level``."""

    from moonmind.omnigent.concurrency_qualification import (
        ConcurrencyQualificationLayer,
        ConcurrencyQualificationRecord,
        ConcurrencyQualificationRow,
        ConcurrencyRowStatus,
        ConcurrencySupportIdentity,
        ExecutionOverlapSample,
        MachineResourceClass,
        ObservedOverlapEvidence,
        compute_concurrency_evidence_digest,
    )

    resource_class = MachineResourceClass(
        # What a real 4-core/8-GiB machine measures: MemTotal is physical RAM
        # minus the kernel's reservation, so it never reports a whole 8 GiB.
        resource_class_ref="ci-standard-4x8@1",
        cpu_cores=4,
        memory_mib=7947,
    )
    overlap = ObservedOverlapEvidence(
        requested_level=level,
        effective_limit=level,
        barrier_synchronized=True,
        samples=tuple(
            ExecutionOverlapSample(
                execution_ref=f"run-{index}", started_at=0.0, ended_at=5.0
            )
            for index in range(level)
        ),
    )
    rows = tuple(
        ConcurrencyQualificationRow(
            layer=layer,
            level=level,
            status=ConcurrencyRowStatus.passed,
            overlap=overlap,
            evidence_ref=f"artifact://concurrency/{layer.value}/{level}",
            evidence_digest=compute_concurrency_evidence_digest(
                {"layer": layer.value, "level": level}
            ),
            resource_class=resource_class,
        )
        for layer in (
            ConcurrencyQualificationLayer.hermetic,
            ConcurrencyQualificationLayer.exact_docker,
        )
    )
    record = ConcurrencyQualificationRecord(
        identity=ConcurrencySupportIdentity(
            supportCombinationKey=plan.supportCombinationKey,
            moonmindCommit="abcdef1234567890",
            # The exact artifact the level was observed on. The publisher
            # refuses a record whose image is not the one the entry names.
            hostImageRef=plan.hostImageRef,
            workerBuildRef="moonmind-worker@test",
            providerCapacityPolicyVersion="omnigent-provider-capacity@1",
            hostCapacityPolicyVersion="omnigent-host-capacity@1",
            transportPoolPolicyVersion="omnigent-transport-pool@1",
            workerTopologyRef="single-replica@1",
            resourceClass=resource_class,
        ),
        generatedAt=datetime.now(UTC),
        rows=rows,
    )
    return record.as_payload()


__all__ = [
    "concurrency_record",
    "support_identity",
    "support_plan",
    "support_row",
]
