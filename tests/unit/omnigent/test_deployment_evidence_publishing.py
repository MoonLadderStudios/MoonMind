from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from moonmind.omnigent.bootstrap.evidence import (
    build_deployment_evidence,
    write_deployment_evidence,
)
from moonmind.omnigent.deployment_evidence import (
    load_deployment_evidence,
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


def test_profile_drift_reports_closest_identity_without_historical_error_explosion(
    tmp_path, monkeypatch,
) -> None:
    """Replay admission after a profile advances while its qualification stalls."""
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    evidence = _evidence("opencode-auth-json@1", profile_ref="opencode-go-default")
    requested = _identity("opencode-auth-json@1").model_copy(
        update={"agentSourceRef": "agent-source:sha256:" + "c" * 64}
    )
    plan = SimpleNamespace(
        supportIdentity=requested,
        supportCombinationKey=compute_support_combination_key(requested),
        hostImageRef=evidence["hostImageRef"],
    )
    historical = {
        **evidence,
        "supportIdentity": {
            **evidence["supportIdentity"],
            "omnigentHostBuildRef": "sha256:" + "d" * 64,
            "launchPolicyRef": "omnigent-on-demand@0",
            # Even untrusted field names must never be copied into diagnostics.
            "untrusted-private-field": "untrusted-private-value",
        },
    }
    path = tmp_path / "deployment-evidence.json"
    path.write_text(json.dumps({"entries": [historical] * 300 + [evidence]}))
    with pytest.raises(ValueError) as failure:
        load_deployment_evidence(plan, path=path)
    message = str(failure.value)
    assert "closest published identity: agentSourceRef differs" in message
    assert message.count("agentSourceRef differs") == 1
    assert "omnigentHostBuildRef differs" not in message
    assert "launchPolicyRef differs" not in message
    assert "untrusted-private" not in message
    assert "retry deployment qualification" in message
    assert len(message) < 700

    # Duplicate exact identities still fail closed with a truthful diagnostic.
    exact_plan = SimpleNamespace(
        supportIdentity=_identity("opencode-auth-json@1"),
        supportCombinationKey=evidence["supportCombinationKey"],
        hostImageRef=evidence["hostImageRef"],
    )
    path.write_text(json.dumps({"entries": [evidence, evidence]}))
    with pytest.raises(ValueError, match="does not resolve to unique"):
        load_deployment_evidence(exact_plan, path=path)

    # An unrecognized field cannot become a diagnostic label or evidence.
    unknown_field = {
        **evidence,
        "supportIdentity": {
            **evidence["supportIdentity"],
            "untrusted-private-field": "untrusted-private-value",
        },
    }
    path.write_text(json.dumps({"entries": [unknown_field]}))
    with pytest.raises(ValueError) as failure:
        load_deployment_evidence(exact_plan, path=path)
    assert "untrusted-private" not in str(failure.value)

    # Diagnostics do not change admission: one valid exact row still works.
    path.write_text(json.dumps({"entries": [historical] * 300 + [evidence]}))
    assert load_deployment_evidence(exact_plan, path=path) == evidence
