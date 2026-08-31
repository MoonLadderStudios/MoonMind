from __future__ import annotations

import json

from moonmind.omnigent.bootstrap.evidence import (
    build_deployment_evidence,
    write_deployment_evidence,
)
from moonmind.omnigent.deployment_evidence import (
    load_deployment_evidence_entries,
)
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_support_combination_key,
)


def _identity(
    materializer_ref: str,
    *,
    model_digest: str = "sha256:" + "1" * 64,
) -> SupportKeyPayload:
    return SupportKeyPayload(
        omnigentServerBuildRef="sha256:" + "2" * 64,
        omnigentHostBuildRef="sha256:" + "3" * 64,
        harnessImplementationRef=("omnigent-harness-implementation:sha256:" + "4" * 64),
        vendorRuntimeRefs=["opencode@1.18.11#sha256:" + "5" * 64],
        agentSourceRef="agent-source:sha256:" + "6" * 64,
        materializerRefs=[materializer_ref],
        providerCompatibilityClass="opencode-native.primary-model",
        hostClassRef="omnigent-opencode@1",
        architecture="linux/amd64",
        launchPolicyRef="omnigent-on-demand@1",
        modelConfigDigest=model_digest,
        executionRealizerRef="generic-omnigent-host@1",
        requiredCapabilitiesDigest="sha256:" + "7" * 64,
    )


def _evidence(
    materializer_ref: str,
    *,
    profile_ref: str,
    model_digest: str = "sha256:" + "1" * 64,
) -> dict:
    identity = _identity(materializer_ref, model_digest=model_digest)
    return build_deployment_evidence(
        support_identity=identity,
        support_combination_key=compute_support_combination_key(identity),
        host_image_ref="ghcr.io/example/opencode@sha256:" + "8" * 64,
        policy_snapshot_digest="sha256:" + "9" * 64,
        effective_launch_snapshot_digest="sha256:" + "a" * 64,
        provider_profile_ref=profile_ref,
        credential_generation=1,
        qualified_model_id="opencode/example",
        effort="xhigh",
        results={"readQualification": "passed"},
        evidence_refs={"readRun": "artifact:read-run"},
        resolved_state=None,
    )


def test_publisher_preserves_independent_materializer_qualifications(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    path = tmp_path / "deployment-execution-evidence.json"
    go_evidence = _evidence(
        "opencode-auth-json@1",
        profile_ref="opencode-go-default",
    )
    zen_evidence = _evidence(
        "none@1",
        profile_ref="opencode-zen-free",
    )

    write_deployment_evidence(go_evidence, path=path)
    write_deployment_evidence(zen_evidence, path=path)

    loaded = load_deployment_evidence_entries(path=path)
    assert {entry.support_identity.materializerRefs for entry in loaded} == {
        ("opencode-auth-json@1",),
        ("none@1",),
    }
    assert len(json.loads(path.read_text(encoding="utf-8"))["entries"]) == 2

    replacement = _evidence(
        "opencode-auth-json@1",
        profile_ref="opencode-go-default",
        model_digest="sha256:" + "b" * 64,
    )
    write_deployment_evidence(replacement, path=path)

    replaced = load_deployment_evidence_entries(path=path)
    assert len(replaced) == 2
    go_entry = next(
        entry
        for entry in replaced
        if entry.support_identity.materializerRefs == ("opencode-auth-json@1",)
    )
    assert go_entry.support_identity.modelConfigDigest == "sha256:" + "b" * 64
