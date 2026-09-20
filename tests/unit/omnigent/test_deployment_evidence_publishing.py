from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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


def test_none_fast_path_tolerates_volatile_build_drift_but_blocks_isolation_drift(
    tmp_path, monkeypatch,
) -> None:
    """none@1 keeps materializer/image/policy enforcement without rebuild churn."""
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    evidence = _evidence("none@1", profile_ref="opencode-zen-free")
    path = tmp_path / "deployment-evidence.json"
    path.write_text(json.dumps({"entries": [evidence]}))

    # Volatile build digests advance (server/host/harness/vendor/agent) — still admitted.
    drifted = _identity("none@1").model_copy(
        update={
            "omnigentServerBuildRef": "sha256:" + "f" * 64,
            "omnigentHostBuildRef": "sha256:" + "e" * 64,
            "harnessImplementationRef": "omnigent-harness-implementation:sha256:" + "d" * 64,
            "vendorRuntimeRefs": ("opencode@1.19.0#sha256:" + "c" * 64,),
            "agentSourceRef": "agent-source:sha256:" + "b" * 64,
        }
    )
    plan = SimpleNamespace(
        supportIdentity=drifted,
        supportCombinationKey=compute_support_combination_key(drifted),
        hostImageRef=evidence["hostImageRef"],
    )
    assert load_deployment_evidence(plan, path=path) == evidence

    # Isolation-relevant fields still fail closed.
    for field, value in [
        ("materializerRefs", ("opencode-auth-json@1",)),
        ("launchPolicyRef", "omnigent-on-demand@9"),
        ("hostClassRef", "omnigent-opencode@9"),
        ("providerCompatibilityClass", "other-class"),
        ("executionRealizerRef", "codex-profile-bound@1"),
    ]:
        blocked = _identity("none@1").model_copy(update={field: value})
        blocked_plan = SimpleNamespace(
            supportIdentity=blocked,
            supportCombinationKey=compute_support_combination_key(blocked),
            hostImageRef=evidence["hostImageRef"],
        )
        with pytest.raises(ValueError) as failure:
            load_deployment_evidence(blocked_plan, path=path)
        assert f"{field} differs" in str(failure.value)

    # Auth-bearing identities stay exact: a secret mount must never silently
    # qualify across builds, so the same volatile build drift still blocks.
    auth_evidence = _evidence("opencode-auth-json@1", profile_ref="opencode-go-default")
    auth_path = tmp_path / "deployment-evidence-auth.json"
    auth_path.write_text(json.dumps({"entries": [auth_evidence]}))
    for field, value in [
        ("omnigentServerBuildRef", "sha256:" + "f" * 64),
        ("omnigentHostBuildRef", "sha256:" + "e" * 64),
        (
            "harnessImplementationRef",
            "omnigent-harness-implementation:sha256:" + "d" * 64,
        ),
        ("vendorRuntimeRefs", ("opencode@1.19.0#sha256:" + "c" * 64,)),
    ]:
        auth_drifted = _identity("opencode-auth-json@1").model_copy(
            update={field: value}
        )
        auth_plan = SimpleNamespace(
            supportIdentity=auth_drifted,
            supportCombinationKey=compute_support_combination_key(auth_drifted),
            hostImageRef=auth_evidence["hostImageRef"],
        )
        with pytest.raises(ValueError) as failure:
            load_deployment_evidence(auth_plan, path=auth_path)
        assert f"{field} differs" in str(failure.value)


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
        update={"hostClassRef": "omnigent-opencode@9"}
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
    assert "closest published identity: hostClassRef differs" in message
    assert message.count("hostClassRef differs") == 1
    assert "omnigentHostBuildRef differs" not in message
    assert "launchPolicyRef differs" not in message
    assert "untrusted-private" not in message
    assert "retry deployment qualification" in message
    # The default OpenCode Go failure names the exact stale credential and
    # policy classes so the operator can revalidate the right profile.
    assert "requested materializerRefs=[opencode-auth-json@1]" in message
    assert "launchPolicyRef=omnigent-on-demand@1" in message
    assert len(message) < 900

    exact_plan = SimpleNamespace(
        supportIdentity=_identity("opencode-auth-json@1"),
        supportCombinationKey=evidence["supportCombinationKey"],
        hostImageRef=evidence["hostImageRef"],
    )
    # A verified document for the requested class that the plan contradicts
    # still fails closed, and reports the conflict rather than reporting the
    # class as unqualified.
    conflicting_plan = SimpleNamespace(
        supportIdentity=_identity("opencode-auth-json@1"),
        supportCombinationKey=evidence["supportCombinationKey"],
        hostImageRef="ghcr.io/example/opencode@sha256:" + "0" * 64,
    )
    path.write_text(json.dumps({"entries": [evidence]}))
    with pytest.raises(ValueError, match="conflicts with the execution plan"):
        load_deployment_evidence(conflicting_plan, path=path)

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


def _reissue(evidence: dict, *, generated_at: datetime, ttl_days: int = 30) -> dict:
    """Re-sign one evidence document at an explicit publication time."""

    from moonmind.omnigent.deployment_evidence import sign_deployment_evidence

    payload = {
        key: value for key, value in evidence.items() if key != "signature"
    }
    payload["generatedAt"] = generated_at.isoformat()
    payload["expiresAt"] = (generated_at + timedelta(days=ttl_days)).isoformat()
    return sign_deployment_evidence(payload)


def test_pinned_agent_profile_admits_across_agent_source_derivation_change(
    tmp_path, monkeypatch,
) -> None:
    """A durable plan must not be stranded when the pinned source ref changes.

    MoonLadderStudios/MoonMind#4438 changed which Agent Profile digest the
    plan compiler pins as ``agentSourceRef``. Schedules, checkpoints, and
    in-flight executions keep running earlier Agent Profile snapshots that the
    bootstrap never requalifies, so evidence published by the previous
    compiler generation is the only evidence those plans will ever find. The
    agent source is verified exactly against the presented Agent Profile
    artifact at launch, so deployment qualification must not also pin it.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    evidence = _evidence("opencode-auth-json@1", profile_ref="opencode-go-default")
    path = tmp_path / "deployment-evidence.json"
    path.write_text(json.dumps({"entries": [evidence]}))

    requested = _identity("opencode-auth-json@1").model_copy(
        update={"agentSourceRef": "agent-source:sha256:" + "c" * 64}
    )
    plan = SimpleNamespace(
        supportIdentity=requested,
        supportCombinationKey=compute_support_combination_key(requested),
        hostImageRef=evidence["hostImageRef"],
    )
    assert load_deployment_evidence(plan, path=path) == evidence


def test_collapsed_publication_history_admits_the_current_qualification(
    tmp_path, monkeypatch,
) -> None:
    """Repeated qualifications of one class must not read as ambiguity.

    The publisher appends one signed document per qualification run and only
    replaces entries sharing the *current* projection, so every earlier
    projection leaves several documents describing the same deployment class.
    Admission owns choosing the current one; expired or superseded history
    must not make a live qualification inadmissible.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    now = datetime.now(UTC)
    current = _reissue(
        _evidence("opencode-auth-json@1", profile_ref="opencode-go-default"),
        generated_at=now - timedelta(days=1),
    )
    superseded = _reissue(
        {
            **current,
            "supportIdentity": {
                **current["supportIdentity"],
                "agentSourceRef": "agent-source:sha256:" + "d" * 64,
            },
            "supportCombinationKey": compute_support_combination_key(
                _identity("opencode-auth-json@1").model_copy(
                    update={"agentSourceRef": "agent-source:sha256:" + "d" * 64}
                )
            ),
        },
        generated_at=now - timedelta(days=10),
    )
    expired = _reissue(
        {
            **current,
            "supportIdentity": {
                **current["supportIdentity"],
                "agentSourceRef": "agent-source:sha256:" + "e" * 64,
            },
            "supportCombinationKey": compute_support_combination_key(
                _identity("opencode-auth-json@1").model_copy(
                    update={"agentSourceRef": "agent-source:sha256:" + "e" * 64}
                )
            ),
        },
        generated_at=now - timedelta(days=60),
    )
    path = tmp_path / "deployment-evidence.json"
    path.write_text(json.dumps({"entries": [expired, superseded, current]}))

    plan = SimpleNamespace(
        supportIdentity=_identity("opencode-auth-json@1"),
        supportCombinationKey=current["supportCombinationKey"],
        hostImageRef=current["hostImageRef"],
    )
    assert load_deployment_evidence(plan, path=path) == current
