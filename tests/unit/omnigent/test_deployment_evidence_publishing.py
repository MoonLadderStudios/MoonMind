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
    assert_deployment_evidence_matches_plan,
    find_deployment_evidence_entry,
    load_deployment_evidence,
    load_deployment_evidence_entries,
    sign_deployment_evidence,
    validate_deployment_evidence,
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

    # A new model digest is a different exact support combination. Publishing
    # it qualifies that combination; it supersedes nothing, so the previous
    # document survives and the other credential class is untouched.
    added = _evidence(
        "opencode-auth-json@1",
        profile_ref="opencode-go-default",
        model_digest="sha256:" + "b" * 64,
    )
    write_deployment_evidence(added, path=path)

    published = load_deployment_evidence_entries(path=path)
    assert {entry.support_combination_key for entry in published} == {
        go_evidence["supportCombinationKey"],
        zen_evidence["supportCombinationKey"],
        added["supportCombinationKey"],
    }


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


def _variant(evidence: dict, *, agent_source: str) -> dict:
    """One sibling document in the same deployment qualification class."""

    identity = _identity("opencode-auth-json@1").model_copy(
        update={"agentSourceRef": "agent-source:sha256:" + agent_source * 64}
    )
    return {
        **evidence,
        "supportIdentity": identity.model_dump(mode="json", by_alias=True),
        "supportCombinationKey": compute_support_combination_key(identity),
    }


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


def test_plan_match_selects_on_the_qualification_key_and_host_image(
    tmp_path, monkeypatch,
) -> None:
    """The qualification key is the whole identity comparison.

    ``compute_deployment_qualification_key`` owns which identity fields a
    deployment qualifies. A second projection here could disagree with the key
    that admission and publication already select on, which is exactly how a
    plan and its evidence drift apart.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    auth = validate_deployment_evidence(
        _evidence("opencode-auth-json@1", profile_ref="opencode-go-default")
    )
    none = validate_deployment_evidence(
        _evidence("none@1", profile_ref="opencode-zen-free")
    )

    def _plan(identity: SupportKeyPayload, *, host_image_ref: str) -> SimpleNamespace:
        return SimpleNamespace(
            supportIdentity=identity,
            supportCombinationKey=compute_support_combination_key(identity),
            hostImageRef=host_image_ref,
        )

    image = auth.host_image_ref
    # Same class, same key: per-run model and capability variance is admitted.
    assert_deployment_evidence_matches_plan(
        auth,
        _plan(
            _identity("opencode-auth-json@1", model_digest="sha256:" + "b" * 64),
            host_image_ref=image,
        ),
    )
    # none@1 additionally admits volatile build drift.
    assert_deployment_evidence_matches_plan(
        none,
        _plan(
            _identity("none@1").model_copy(
                update={"omnigentServerBuildRef": "sha256:" + "f" * 64}
            ),
            host_image_ref=image,
        ),
    )
    # Auth-bearing build drift is a different qualified combination.
    with pytest.raises(ValueError, match="conflicts with the execution plan"):
        assert_deployment_evidence_matches_plan(
            auth,
            _plan(
                _identity("opencode-auth-json@1").model_copy(
                    update={"omnigentServerBuildRef": "sha256:" + "f" * 64}
                ),
                host_image_ref=image,
            ),
        )
    # A credential class never qualifies another, in either direction.
    with pytest.raises(ValueError, match="conflicts with the execution plan"):
        assert_deployment_evidence_matches_plan(
            none, _plan(_identity("opencode-auth-json@1"), host_image_ref=image)
        )
    with pytest.raises(ValueError, match="conflicts with the execution plan"):
        assert_deployment_evidence_matches_plan(
            auth, _plan(_identity("none@1"), host_image_ref=image)
        )
    # The qualified host image is deployment substrate, not per-run variance.
    with pytest.raises(ValueError, match="conflicts with the execution plan"):
        assert_deployment_evidence_matches_plan(
            auth,
            _plan(
                _identity("opencode-auth-json@1"),
                host_image_ref="ghcr.io/example/opencode@sha256:" + "0" * 64,
            ),
        )
    # A plan without an admitted identity can never match.
    with pytest.raises(ValueError, match="lacks exact support identity"):
        assert_deployment_evidence_matches_plan(
            auth, SimpleNamespace(supportIdentity=None, hostImageRef=image)
        )


def test_readiness_probe_and_admission_select_the_same_document(
    tmp_path, monkeypatch,
) -> None:
    """A rollout gate must not call a class expired that admission accepts.

    ``find_deployment_evidence_entry`` reports readiness on the same
    qualification key admission selects on. Once a class can hold more than one
    published document, returning a different document than admission does lets
    the gate reject a plan the loader would have qualified.
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
    stale = _reissue(
        _variant(current, agent_source="d"), generated_at=now - timedelta(days=60)
    )
    path = tmp_path / "deployment-evidence.json"
    # Publication order deliberately puts the expired document first.
    path.write_text(json.dumps({"entries": [stale, current]}))

    identity = _identity("opencode-auth-json@1")
    entry = find_deployment_evidence_entry(identity, path=path)
    assert entry is not None
    assert entry.generated_at.isoformat() == current["generatedAt"].replace(
        "Z", "+00:00"
    )
    plan = SimpleNamespace(
        supportIdentity=identity,
        supportCombinationKey=current["supportCombinationKey"],
        hostImageRef=current["hostImageRef"],
    )
    assert load_deployment_evidence(plan, path=path)["generatedAt"] == (
        current["generatedAt"]
    )


def test_unusable_qualification_reports_a_bounded_reason(
    tmp_path, monkeypatch,
) -> None:
    """Routine expiry must stay distinguishable from missing qualification.

    A published class whose documents have all lapsed is an actionable,
    different problem from a combination this deployment never qualified.
    Candidate documents are untrusted until schema, secret scan, and HMAC pass,
    so the reason must come from this module's own bounded vocabulary.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    now = datetime.now(UTC)
    evidence = _evidence("opencode-auth-json@1", profile_ref="opencode-go-default")
    expired = _reissue(evidence, generated_at=now - timedelta(days=60))
    path = tmp_path / "deployment-evidence.json"
    path.write_text(json.dumps({"entries": [expired]}))

    plan = SimpleNamespace(
        supportIdentity=_identity("opencode-auth-json@1"),
        supportCombinationKey=evidence["supportCombinationKey"],
        hostImageRef=evidence["hostImageRef"],
    )
    with pytest.raises(ValueError) as failure:
        load_deployment_evidence(plan, path=path)
    message = str(failure.value)
    assert "expired" in message
    assert "not qualified" not in message
    assert len(message) < 900

    # An unverifiable document reports the bounded structural reason and never
    # quotes the candidate it came from.
    forged = {
        **expired,
        "signature": {**expired["signature"], "value": "0" * 64},
        "provider": {**expired["provider"], "profileRef": "untrusted-private-value"},
    }
    path.write_text(json.dumps({"entries": [forged]}))
    with pytest.raises(ValueError) as failure:
        load_deployment_evidence(plan, path=path)
    message = str(failure.value)
    assert "verification" in message
    assert "untrusted-private" not in message


def test_publisher_supersedes_only_the_combination_it_requalifies(
    tmp_path, monkeypatch,
) -> None:
    """Publication must not destroy valid evidence it did not supersede.

    Two documents in one qualification class describe two exact support
    combinations. Dropping the whole class on every publish deletes signed,
    unexpired evidence — including the only document a retained or rolled-back
    worker generation can still match — so a publish replaces the exact
    combination it requalifies and nothing else.
    """
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    path = tmp_path / "deployment-execution-evidence.json"
    first = _evidence("opencode-auth-json@1", profile_ref="opencode-go-default")
    write_deployment_evidence(first, path=path)

    # Same deployment class, different exact combination (another agent source).
    other_source = sign_deployment_evidence(
        {
            key: value
            for key, value in _variant(first, agent_source="d").items()
            if key != "signature"
        }
    )
    write_deployment_evidence(other_source, path=path)
    keys = {
        entry.support_combination_key
        for entry in load_deployment_evidence_entries(path=path)
    }
    assert keys == {
        first["supportCombinationKey"],
        other_source["supportCombinationKey"],
    }

    # Requalifying one exact combination replaces only that document.
    requalified = sign_deployment_evidence(
        {
            key: value
            for key, value in first.items()
            if key != "signature"
        }
        | {"results": {"readQualification": "passed", "writeQualification": "passed"}}
    )
    write_deployment_evidence(requalified, path=path)
    entries = load_deployment_evidence_entries(path=path)
    assert {entry.support_combination_key for entry in entries} == keys
    replaced = next(
        entry
        for entry in entries
        if entry.support_combination_key == first["supportCombinationKey"]
    )
    assert replaced.results == {
        "readQualification": "passed",
        "writeQualification": "passed",
    }
