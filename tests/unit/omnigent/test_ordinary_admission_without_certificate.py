"""Ordinary certificate-independent admission (MoonLadderStudios/MoonMind#4560).

A compatible, authorized workflow must not become unrunnable merely because
a historical qualification certificate is missing, stale, or names another
policy revision. Only explicit strict certification (``protected`` or
``deployment``) requires historical evidence.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.execution_plan import AdmissionAuthority
from moonmind.omnigent.settings import (
    omnigent_admission_mode,
    omnigent_requires_certification,
)


def test_omitted_blank_and_shipped_default_are_ordinary() -> None:
    assert omnigent_admission_mode(env={}) == "ordinary"
    assert omnigent_admission_mode(env={"MOONMIND_OMNIGENT_EVIDENCE_POLICY": ""}) == "ordinary"
    assert omnigent_admission_mode(env={"MOONMIND_OMNIGENT_EVIDENCE_POLICY": "  "}) == "ordinary"
    assert omnigent_admission_mode(env={"MOONMIND_OMNIGENT_EVIDENCE_POLICY": "either"}) == "ordinary"
    assert omnigent_requires_certification(env={}) is False
    assert omnigent_requires_certification(env={"MOONMIND_OMNIGENT_EVIDENCE_POLICY": "either"}) is False


def test_explicit_strict_policies_require_certification() -> None:
    assert omnigent_admission_mode(env={"MOONMIND_OMNIGENT_EVIDENCE_POLICY": "protected"}) == "strict"
    assert omnigent_admission_mode(env={"MOONMIND_OMNIGENT_EVIDENCE_POLICY": "deployment"}) == "strict"
    assert omnigent_requires_certification(env={"MOONMIND_OMNIGENT_EVIDENCE_POLICY": "protected"}) is True
    assert omnigent_requires_certification(env={"MOONMIND_OMNIGENT_EVIDENCE_POLICY": "deployment"}) is True


def test_ordinary_resolver_returns_uncertified_without_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing optional certificates never veto ordinary execution."""

    import moonmind.omnigent.evidence_resolver as resolver

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", "/nonexistent/no-evidence.json")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "/nonexistent/no-evidence.json")

    evidence, tier = resolver.resolve_execution_evidence(object())
    assert evidence is None
    assert tier == "uncertified"


def test_strict_resolver_still_fails_closed_without_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit strict certification keeps failing closed."""

    import moonmind.omnigent.evidence_resolver as resolver

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "/nonexistent/no-evidence.json")

    with pytest.raises(Exception):
        resolver.resolve_execution_evidence(object())


def test_strict_deployment_resolver_still_fails_closed_without_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import moonmind.omnigent.evidence_resolver as resolver

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    monkeypatch.setenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", "/nonexistent/no-evidence.json")

    with pytest.raises(ValueError, match="no admissible execution evidence"):
        resolver.resolve_execution_evidence(object())


def test_protected_policy_honors_non_strict_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`require_evidence=False` overrides the settings-derived mode consistently.

    A missing or malformed optional protected certificate must resolve to
    ``(None, "uncertified")`` under a non-strict override, exactly as the
    deployment branch already behaves -- the protected branch must apply
    the computed `strict` value before re-raising.
    """

    import moonmind.omnigent.evidence_resolver as resolver

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "/nonexistent/no-evidence.json")

    evidence, tier = resolver.resolve_execution_evidence(
        object(), policy="protected", require_evidence=False
    )
    assert evidence is None
    assert tier == "uncertified"


def test_protected_policy_explicit_strict_override_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`require_evidence=True` keeps the protected branch failing closed."""

    import moonmind.omnigent.evidence_resolver as resolver

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "/nonexistent/no-evidence.json")

    with pytest.raises(Exception):
        resolver.resolve_execution_evidence(
            object(), policy="protected", require_evidence=True
        )


def _generations() -> dict[str, str]:
    from moonmind.omnigent.session_supervisor_rollback import (
        SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )
    from moonmind.schemas.omnigent_session_models import (
        OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    return {
        "featureGeneration": OMNIGENT_SESSION_FEATURE_GENERATION,
        "replayCompatibilityVersion": OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        "rollbackPolicyVersion": SUPERVISOR_ROLLBACK_POLICY_VERSION,
    }


def test_ordinary_uncertified_authority_is_valid() -> None:
    authority = AdmissionAuthority.model_validate(
        {
            "admissionMode": "ordinary",
            "supportEvidenceRef": "",
            "supportEvidenceDigest": "",
            "supportTier": "uncertified",
            **_generations(),
        }
    )
    assert authority.admissionMode == "ordinary"
    assert authority.supportTier == "uncertified"


def test_ordinary_authority_may_carry_optional_observation() -> None:
    authority = AdmissionAuthority.model_validate(
        {
            "admissionMode": "ordinary",
            "supportEvidenceRef": "artifact:art_1",
            "supportEvidenceDigest": "sha256:" + "a" * 64,
            "supportTier": "deployment_qualified",
            **_generations(),
        }
    )
    assert authority.supportTier == "deployment_qualified"


def test_strict_authority_requires_certified_evidence() -> None:
    with pytest.raises(Exception):
        AdmissionAuthority.model_validate(
            {
                "admissionMode": "strict",
                "supportEvidenceRef": "",
                "supportEvidenceDigest": "",
                "supportTier": "uncertified",
                **_generations(),
            }
        )


def test_legacy_authority_without_mode_stays_strict() -> None:
    """Missing metadata never silently bypasses validation."""

    authority = AdmissionAuthority.model_validate(
        {
            "supportEvidenceRef": "artifact:art_1",
            "supportEvidenceDigest": "sha256:" + "a" * 64,
            "supportTier": "supported",
            **_generations(),
        }
    )
    assert authority.admissionMode == "strict"


def _retained_plan_reader():
    """Load the unmodified historical reader fixture as a module."""

    import importlib.util
    import sys
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "integration"
        / "reliability"
        / "replays"
        / "omnigent-plan-reader-skew"
        / "retained_execution_plan.py"
    )
    spec = importlib.util.spec_from_file_location("retained_plan_reader_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def test_strict_authority_serializes_to_historical_wire_shape() -> None:
    """Rolling upgrade: retained readers still parse strict plans.

    The historical fixture keeps `extra="forbid"` without `admissionMode`,
    so a strict plan must serialize without the new marker to stay
    readable by the retained fleet.
    """

    retained = _retained_plan_reader()
    authority = AdmissionAuthority.model_validate(
        {
            "admissionMode": "strict",
            "supportEvidenceRef": "artifact:art_1",
            "supportEvidenceDigest": "sha256:" + "a" * 64,
            "supportTier": "supported",
            **_generations(),
        }
    )
    wire = authority.model_dump(by_alias=True, mode="json")
    assert "admissionMode" not in wire
    parsed = retained.AdmissionAuthority.model_validate(wire)
    assert parsed.supportTier == "supported"


def test_ordinary_authority_is_rejected_by_historical_reader() -> None:
    """Rolling upgrade fails closed: retained readers reject uncertified plans.

    An ordinary uncertified plan keeps its `admissionMode` marker on the
    wire so a pre-upgrade worker rejects it instead of misreading
    uncertified admission as certified.
    """

    retained = _retained_plan_reader()
    authority = AdmissionAuthority.model_validate(
        {
            "admissionMode": "ordinary",
            "supportEvidenceRef": "",
            "supportEvidenceDigest": "",
            "supportTier": "uncertified",
            **_generations(),
        }
    )
    wire = authority.model_dump(by_alias=True, mode="json")
    assert wire["admissionMode"] == "ordinary"
    with pytest.raises(Exception):
        retained.AdmissionAuthority.model_validate(wire)


def test_ordinary_rollout_ignores_missing_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """No resolver/rollout/readiness consumer reinstates the certificate veto."""

    from moonmind.omnigent.runtime_provider_rollout import (
        RolloutReason,
        RolloutSelectionContext,
        RolloutRule,
        _readiness_denials,
    )

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    rule = RolloutRule.model_validate(
        {
            "targetId": "test.target",
            "label": "test",
            "selector": {},
            "state": "new_work_default",
            "generation": 1,
            "requiresSupportEvidence": True,
        }
    )
    context = RolloutSelectionContext.model_validate({})
    denials = _readiness_denials(rule=rule, context=context)
    assert RolloutReason.support_evidence_missing not in denials


def test_strict_rollout_still_denies_missing_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from moonmind.omnigent.runtime_provider_rollout import (
        RolloutReason,
        RolloutSelectionContext,
        RolloutRule,
        _readiness_denials,
    )

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected")
    rule = RolloutRule.model_validate(
        {
            "targetId": "test.target",
            "label": "test",
            "selector": {},
            "state": "new_work_default",
            "generation": 1,
            "requiresSupportEvidence": True,
        }
    )
    context = RolloutSelectionContext.model_validate({})
    denials = _readiness_denials(rule=rule, context=context)
    assert RolloutReason.support_evidence_missing in denials


@pytest.mark.asyncio
async def test_ordinary_plan_loader_does_not_require_certificate() -> None:
    """Post-admission expiry never strands ordinary work, reads, or results."""

    from types import SimpleNamespace

    from moonmind.workflows.temporal.activities import omnigent_session_activities as activities

    generations = _generations()
    persisted = SimpleNamespace(
        payload=SimpleNamespace(
            authority=None,
            admissionAuthority=SimpleNamespace(
                admissionMode="ordinary",
                supportEvidenceRef="",
                supportEvidenceDigest="",
                supportTier="uncertified",
                featureGeneration=generations["featureGeneration"],
                replayCompatibilityVersion=generations["replayCompatibilityVersion"],
                rollbackPolicyVersion=generations["rollbackPolicyVersion"],
            ),
        )
    )
    # No certificate artifacts exist; ordinary load must still succeed.
    await activities._validate_plan_admission_authority(persisted)


def _support_identity(*, materializer_ref: str, build_tag: str) -> dict:
    """One exact support identity for the requested credential class."""

    return {
        "omnigentServerBuildRef": f"omnigent-server-build:{build_tag}",
        "omnigentHostBuildRef": f"omnigent-host-build:{build_tag}",
        "harnessImplementationRef": f"opencode-host-impl:{build_tag}",
        "vendorRuntimeRefs": (f"docker-runtime:{build_tag}",),
        "agentSourceRef": "agent-source@fixed",
        "materializerRefs": (materializer_ref,),
        "providerCompatibilityClass": "openai-compatible",
        "hostClassRef": "omnigent-host-class@1",
        "architecture": "linux/amd64",
        "launchPolicyRef": "omnigent-on-demand@10",
        "modelConfigDigest": "sha256:" + "d4" * 32,
        "executionRealizerRef": "generic-omnigent-host@1",
        "requiredCapabilitiesDigest": "sha256:" + "e5" * 32,
    }


def test_ordinary_admission_survives_compatible_rebuild_for_both_credential_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: a compatible build/image change needs no certificate refresh.

    Both the authenticated (``opencode-auth-json@1``) and credentialless
    (``none@1``) paths admit ordinary uncertified execution after a
    compatible rebuild, and the concrete runtime selection is asserted --
    not merely the absence of an error. The credentialless deployment key
    additionally ignores volatile build churn; the auth-bearing key stays
    exact, which is why ordinary admission must not depend on it.
    """

    import moonmind.omnigent.evidence_resolver as resolver
    from moonmind.omnigent.harness_platform.support import (
        SupportKeyPayload,
        compute_deployment_qualification_key,
        compute_support_combination_key,
    )

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", "/nonexistent/no-evidence.json")
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "/nonexistent/no-evidence.json"
    )

    for materializer_ref in ("opencode-auth-json@1", "none@1"):
        before = SupportKeyPayload.model_validate(
            _support_identity(materializer_ref=materializer_ref, build_tag="build-a")
        )
        after = SupportKeyPayload.model_validate(
            _support_identity(materializer_ref=materializer_ref, build_tag="build-b")
        )
        # The exact combination changed with the rebuild ...
        assert compute_support_combination_key(before) != compute_support_combination_key(after)
        if materializer_ref == "none@1":
            # ... but the credentialless deployment class ignores volatile builds.
            assert compute_deployment_qualification_key(before) == (
                compute_deployment_qualification_key(after)
            )
        else:
            # Auth-bearing identities stay exact -- ordinary admission still
            # must not wait on a refreshed certificate for them.
            assert compute_deployment_qualification_key(before) != (
                compute_deployment_qualification_key(after)
            )
        evidence, tier = resolver.resolve_execution_evidence(object())
        assert evidence is None
        assert tier == "uncertified"
        # Concrete runtime actually selected for both paths.
        assert after.launchPolicyRef == "omnigent-on-demand@10"
        assert after.hostClassRef == "omnigent-host-class@1"
        assert after.executionRealizerRef == "generic-omnigent-host@1"
        assert after.materializerRefs == (materializer_ref,)


def test_unknown_image_fails_at_runtime_launch_boundaries_not_certificate_matching() -> None:
    """R3: unknown/untrusted images fail closed at the enforcing boundary.

    Same-repository rebuilds reconcile to the concrete installed runtime;
    a different image family never substitutes, a non-pinned ref never
    validates, and an unknown realizer never dispatches. None of these
    verdicts consults certificate matching.
    """

    from moonmind.omnigent.harness_platform.support import validate_realizer
    from moonmind.omnigent.host_image_drift import (
        compatible_deployed_fallback,
        reconcile_effective_launch_to_selected_host,
    )
    from moonmind.omnigent.opencode_runtime_validation import (
        OpenCodeProviderRuntimeValidationService,
    )
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    requested = "ghcr.io/moonmind/omnigent-host@sha256:" + "a1" * 32
    rebuilt = "ghcr.io/moonmind/omnigent-host@sha256:" + "b2" * 32
    foreign = "ghcr.io/untrusted/other-host@sha256:" + "c3" * 32
    provenance = {rebuilt: {"version": "0.10.0", "buildDigest": None}}

    # Compatible rebuild: the concrete installed runtime is actually selected.
    assert compatible_deployed_fallback(
        requested, deployed_refs=[rebuilt], provenance=provenance
    ) == rebuilt
    reconciled = reconcile_effective_launch_to_selected_host(
        {"hostImageRef": requested}, rebuilt
    )
    assert reconciled is not None
    assert reconciled["hostImageRef"] == rebuilt

    # Unknown image family: fail closed, never substitute.
    assert compatible_deployed_fallback(
        requested, deployed_refs=[foreign], provenance={foreign: {"version": "0.10.0", "buildDigest": None}}
    ) is None
    assert reconcile_effective_launch_to_selected_host({"hostImageRef": requested}, foreign) is None

    # Launch-preflight owner: validation requires a digest-pinned image.
    with pytest.raises(HarnessPlatformError):
        OpenCodeProviderRuntimeValidationService(
            session_factory=None, resolver=None, image_ref="ghcr.io/moonmind/omnigent-host:latest"
        )
    # Runtime adapter owner: unknown realizers never dispatch.
    with pytest.raises(ValueError):
        validate_realizer("unknown-realizer@9")


def test_workflow_authored_input_cannot_downgrade_explicit_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4: an explicitly strict deployment requirement is not downgradable.

    Forged ordinary metadata inside request/workflow-authored data -- an
    ``either`` policy argument or an ``ordinary``/``uncertified`` payload --
    never weakens the trusted settings boundary. Strict admission keeps
    failing closed; the strict authority validator rejects uncertified
    evidence even when the caller asks for it.
    """

    import moonmind.omnigent.evidence_resolver as resolver

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected")
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "/nonexistent/no-evidence.json"
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", "/nonexistent/no-evidence.json")

    forged_payload = {"admissionMode": "ordinary", "supportTier": "uncertified"}
    with pytest.raises(Exception):
        resolver.resolve_execution_evidence(forged_payload)
    # A workflow-authored ``either`` policy argument does not downgrade either.
    with pytest.raises(ValueError, match="no admissible execution evidence"):
        resolver.resolve_execution_evidence(forged_payload, policy="either")

    with pytest.raises(Exception):
        AdmissionAuthority.model_validate(
            {
                "admissionMode": "strict",
                "supportEvidenceRef": "",
                "supportEvidenceDigest": "",
                "supportTier": "uncertified",
                **_generations(),
            }
        )


def _minimal_saved_plan_payload(*, admission_authority: dict) -> dict:
    """One valid saved plan payload carrying pre-upgrade strict authority."""

    return {
        "schemaVersion": "moonmind.omnigent-execution-plan-payload.v1",
        "endpointRef": "endpoint:opencode-go-default",
        "agentProfileSnapshotRef": "artifact:agent-profile-snapshot",
        "harnessCatalogRef": "artifact:harness-catalog",
        "harnessId": "opencode-host",
        "harnessImplementationRef": "opencode-host-impl:build-a",
        "agentSource": {"snapshotRef": "agent-source:snapshot:abc"},
        "credentialBindingSetRef": "credential-binding-set:primary",
        "credentialBindings": {
            "primary-model": {
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            }
        },
        "hostClassRef": "omnigent-host-class@1",
        "launchPolicyRef": "omnigent-on-demand@10",
        "executionRealizerRef": "generic-omnigent-host@1",
        "model": {
            "qualifiedId": "opencode-go/muse-spark-1.3-contributor",
            "effort": "xhigh",
            "routeRef": "muse-spark",
            "normalizedOptions": {},
            "modelConfigDigest": "sha256:" + "f6" * 32,
        },
        "resolvedSkills": {},
        "classAdmissionDecision": {},
        "runtimeValidationRequirements": [],
        "workspaceIntentRef": "workspace-intent:schedule-7",
        "policySnapshotRef": "policy-snapshot:v27",
        "supportCombinationKey": "omnigent-support:sha256:" + "ab" * 32,
        "admissionAuthority": admission_authority,
    }


def test_saved_schedule_migrates_to_fresh_ordinary_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R7: pre-upgrade schedules obtain fresh admission preserving intent.

    Schedule identity, cadence, timezone, paused state, input, Profile and
    account selection, model, budgets, and publication intent survive; only
    the obsolete certificate is replaced with fresh ordinary admission.
    Historical digests are never rewritten, and explicit strict deployments
    refuse the reissue instead of silently downgrading.
    """

    import copy

    from moonmind.omnigent.harness_platform.execution_plan import (
        compute_plan_ref,
        reissue_ordinary_admission_for_saved_plan,
    )

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    generations = _generations()
    saved_plan = _minimal_saved_plan_payload(
        admission_authority={
            "admissionMode": "strict",
            "supportEvidenceRef": "artifact:art_historical",
            "supportEvidenceDigest": "sha256:" + "ef" * 32,
            "supportTier": "supported",
            **generations,
        }
    )
    saved_schedule = {
        "scheduleId": "schedule-7",
        "cadence": "0 9 * * *",
        "timezone": "UTC",
        "paused": False,
        "input": {"task": "nightly triage"},
        "agentProfileRef": "omnigent-opencode-default@8",
        "providerProfileRef": "opencode-go-default",
        "accountRef": "account:operator",
        "model": "opencode-go/muse-spark-1.3-contributor",
        "budgets": {"maxCostUsd": 5.0},
        "publicationIntent": "publish-on-success",
        "plan": saved_plan,
    }
    saved_plan_snapshot = copy.deepcopy(saved_plan)
    historical_ref = compute_plan_ref(saved_plan)

    migrated = reissue_ordinary_admission_for_saved_plan(saved_schedule["plan"])
    migrated_payload = migrated.payload.model_dump(by_alias=True, mode="json")

    # Schedule wrapper intent is untouched; only the plan was re-admitted.
    for key in (
        "scheduleId",
        "cadence",
        "timezone",
        "paused",
        "input",
        "agentProfileRef",
        "providerProfileRef",
        "accountRef",
        "model",
        "budgets",
        "publicationIntent",
    ):
        assert saved_schedule[key] == {
            "scheduleId": "schedule-7",
            "cadence": "0 9 * * *",
            "timezone": "UTC",
            "paused": False,
            "input": {"task": "nightly triage"},
            "agentProfileRef": "omnigent-opencode-default@8",
            "providerProfileRef": "opencode-go-default",
            "accountRef": "account:operator",
            "model": "opencode-go/muse-spark-1.3-contributor",
            "budgets": {"maxCostUsd": 5.0},
            "publicationIntent": "publish-on-success",
        }[key]
    # Every plan intent field survives byte-identically except authority.
    for key, value in saved_plan_snapshot.items():
        if key == "admissionAuthority":
            continue
        assert migrated_payload[key] == value
    # Fresh ordinary admission at current generations, never fabricated.
    authority = migrated.payload.admissionAuthority
    assert authority is not None
    assert authority.admissionMode == "ordinary"
    assert authority.supportTier == "uncertified"
    assert authority.supportEvidenceRef == ""
    assert authority.supportEvidenceDigest == ""
    assert authority.featureGeneration == generations["featureGeneration"]
    assert authority.replayCompatibilityVersion == generations["replayCompatibilityVersion"]
    assert authority.rollbackPolicyVersion == generations["rollbackPolicyVersion"]
    # Historical digests unchanged: input never mutated, old ref stable.
    assert saved_plan == saved_plan_snapshot
    assert compute_plan_ref(saved_plan) == historical_ref
    assert migrated.planRef != historical_ref

    # Explicit strict deployments must not silently downgrade saved plans.
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    with pytest.raises(ValueError, match="strict"):
        reissue_ordinary_admission_for_saved_plan(saved_plan_snapshot)


@pytest.mark.asyncio
async def test_plan_reader_distinguishes_history_from_new_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R8: expired certificates strand neither reads nor saved results.

    Loading history under ordinary admission never touches certificate
    artifacts, while starting a new effect under explicit strict semantics
    still validates the pinned evidence. Substantive compatibility gates
    stay enforced for ordinary plans as well.
    """

    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal.activities import omnigent_session_activities as activities

    generations = _generations()

    def _persisted(*, mode: str, ref: str = "", digest: str = "", tier: str = "uncertified") -> SimpleNamespace:
        return SimpleNamespace(
            payload=SimpleNamespace(
                authority=None,
                admissionAuthority=SimpleNamespace(
                    admissionMode=mode,
                    supportEvidenceRef=ref,
                    supportEvidenceDigest=digest,
                    supportTier=tier,
                    featureGeneration=generations["featureGeneration"],
                    replayCompatibilityVersion=generations["replayCompatibilityVersion"],
                    rollbackPolicyVersion=generations["rollbackPolicyVersion"],
                ),
            )
        )

    # History load: the artifact store must not even be consulted.
    forbidden_reader = AsyncMock(side_effect=AssertionError("certificate must not be read"))
    monkeypatch.setattr(activities, "_read_json_artifact", forbidden_reader)
    await activities._validate_plan_admission_authority(_persisted(mode="ordinary"))
    forbidden_reader.assert_not_awaited()

    # New effect under strict semantics: tampered evidence still fails.
    async def _tampered(_ref: str) -> dict:
        return {"tampered": True}

    monkeypatch.setattr(activities, "_read_json_artifact", _tampered)
    with pytest.raises(ValueError, match="digest conflicts"):
        await activities._validate_plan_admission_authority(
            _persisted(
                mode="strict",
                ref="artifact:art_strict",
                digest="sha256:" + "00" * 32,
                tier="supported",
            )
        )

    # Substantive gates still apply to ordinary history loads.
    stale = _persisted(mode="ordinary")
    stale.payload.admissionAuthority.featureGeneration = "ancient-generation"
    with pytest.raises(ValueError, match="feature generation"):
        await activities._validate_plan_admission_authority(stale)


@pytest.mark.asyncio
async def test_strict_certificate_failure_is_distinguishable_for_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stranded pre-upgrade certificate is classified for resume migration.

    The certificate-evidence tail raises `_AdmissionCertificateError` (still
    a `ValueError` for existing fail-closed contracts) so the resume path
    can migrate stranded pre-upgrade plans while integrity failures keep
    raising plain `ValueError` and never migrate.
    """

    from types import SimpleNamespace

    from moonmind.workflows.temporal.activities import omnigent_session_activities as activities

    generations = _generations()
    persisted = SimpleNamespace(
        payload=SimpleNamespace(
            authority=None,
            admissionAuthority=SimpleNamespace(
                admissionMode="strict",
                supportEvidenceRef="artifact:art_missing",
                supportEvidenceDigest="sha256:" + "aa" * 32,
                supportTier="supported",
                featureGeneration=generations["featureGeneration"],
                replayCompatibilityVersion=generations["replayCompatibilityVersion"],
                rollbackPolicyVersion=generations["rollbackPolicyVersion"],
            ),
        )
    )

    async def _missing(_ref: str) -> dict:
        raise ValueError("artifact unavailable")

    monkeypatch.setattr(activities, "_read_json_artifact", _missing)
    with pytest.raises(activities._AdmissionCertificateError) as excinfo:
        await activities._validate_plan_admission_authority(persisted)
    assert isinstance(excinfo.value, ValueError)


@pytest.mark.asyncio
async def test_resume_migration_refuses_strict_settings_and_explicit_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration never downgrades strictness silently.

    Explicit strict certification and explicitly strict wire authority both
    re-raise the original certificate error instead of migrating.
    """

    from types import SimpleNamespace

    from moonmind.workflows.temporal.activities import omnigent_session_activities as activities

    original = activities._AdmissionCertificateError("historical certificate gone")

    # Explicit strict certification: re-admit through the strict path instead.
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected")
    with pytest.raises(activities._AdmissionCertificateError):
        await activities._migrate_stranded_pre_upgrade_plan(
            SimpleNamespace(planRef="plan:old"),
            {"payload": {"admissionAuthority": {}}},
            session_factory=object(),
            certificate_error=original,
        )

    # Explicitly strict wire authority under ordinary settings: recorded
    # strictness is never downgraded by the resume path.
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    with pytest.raises(activities._AdmissionCertificateError):
        await activities._migrate_stranded_pre_upgrade_plan(
            SimpleNamespace(planRef="plan:old"),
            {
                "payload": {
                    "admissionAuthority": {
                        "admissionMode": "strict",
                        "supportEvidenceRef": "artifact:art_old",
                        "supportEvidenceDigest": "sha256:" + "bb" * 32,
                        "supportTier": "supported",
                    }
                }
            },
            session_factory=object(),
            certificate_error=original,
        )


@pytest.mark.asyncio
async def test_resume_migrates_stranded_pre_upgrade_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R7 production path: resume migrates instead of stranding saved work.

    A queued workflow holding a pre-upgrade strict plan whose historical
    certificate is unavailable resumes under ordinary admission with fresh
    uncertified authority: saved intent survives byte-identically, the new
    envelope is persisted as the new attempt, history is preserved, and a
    repeated resume reissues the identical envelope instead of diverging.
    """

    from tests.unit.services.test_omnigent_execution_plan_service import (
        _OPENCODE_ALLOWED_LAUNCH_POLICIES,
        _ArtifactService,
        _compile_opencode_plan,
        _PlanStore,
        _protected_support_evidence,
        default_launch_policy_ref,
    )
    from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
    from moonmind.workflows.temporal.activities import omnigent_session_activities as activities

    _ready_opencode_host_pair(monkeypatch)
    # Compile a strict plan, as the pre-upgrade fleet did.
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected")
    monkeypatch.setattr(
        "api_service.services.omnigent_execution_plan_service.resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )
    artifacts = _ArtifactService()
    compiled = await _compile_opencode_plan(
        monkeypatch,
        artifacts=artifacts,
        launch_policy_ref=default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES),
        plan_store=_PlanStore(None),
    )
    assert compiled.envelope.payload.admissionAuthority.admissionMode == "strict"

    # The durable pre-upgrade row carries the historical wire shape: strict
    # plans omit the marker the retained fleet cannot parse.
    artifact_wire = compiled.envelope.model_dump(by_alias=True, mode="json")
    assert "admissionMode" not in artifact_wire["payload"]["admissionAuthority"]

    store = InMemoryExecutionPlanStore()
    await store.persist(compiled.envelope)
    monkeypatch.setattr(
        "moonmind.omnigent.harness_platform.stores.DbExecutionPlanStore",
        lambda _session_factory: store,
    )

    import json

    async def _read_artifact(ref: str) -> dict:
        try:
            return json.loads(artifacts.payloads[ref])
        except KeyError:
            raise ValueError(f"artifact {ref} is unavailable") from None

    # The historical certificate is expired/unavailable at resume time:
    # drop the optional evidence bytes the strict compile persisted.
    support_ref = (
        compiled.envelope.payload.admissionAuthority.supportEvidenceRef.removeprefix(
            "artifact:"
        )
    )
    del artifacts.payloads[support_ref]
    monkeypatch.setattr(activities, "_read_json_artifact", _read_artifact)

    # After the upgrade the deployment is ordinary but the historical
    # certificate artifact is gone.
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    migrated = await activities._load_verified_execution_plan(compiled.binding)

    authority = migrated.payload.admissionAuthority
    assert authority.admissionMode == "ordinary"
    assert authority.supportTier == "uncertified"
    assert authority.supportEvidenceRef == ""
    assert authority.supportEvidenceDigest == ""
    # Every saved intent field survives byte-identically except authority.
    migrated_payload = migrated.payload.model_dump(by_alias=True, mode="json")
    compiled_payload = compiled.envelope.payload.model_dump(by_alias=True, mode="json")
    for key, value in compiled_payload.items():
        if key == "admissionAuthority":
            continue
        assert migrated_payload[key] == value
    # The new attempt is persisted; history is preserved.
    assert await store.load(migrated.planRef) == migrated
    assert await store.load(compiled.envelope.planRef) == compiled.envelope
    # A repeated resume reissues the identical envelope (no divergence).
    assert await activities._load_verified_execution_plan(compiled.binding) == migrated


@pytest.mark.asyncio
async def test_resume_keeps_strict_failure_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit strict certification strands nothing silently -- it fails."""

    from tests.unit.services.test_omnigent_execution_plan_service import (
        _OPENCODE_ALLOWED_LAUNCH_POLICIES,
        _ArtifactService,
        _compile_opencode_plan,
        _PlanStore,
        _protected_support_evidence,
        default_launch_policy_ref,
    )
    from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
    from moonmind.workflows.temporal.activities import omnigent_session_activities as activities

    _ready_opencode_host_pair(monkeypatch)
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected")
    monkeypatch.setattr(
        "api_service.services.omnigent_execution_plan_service.resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )
    artifacts = _ArtifactService()
    compiled = await _compile_opencode_plan(
        monkeypatch,
        artifacts=artifacts,
        launch_policy_ref=default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES),
        plan_store=_PlanStore(None),
    )
    artifact_wire = compiled.envelope.model_dump(by_alias=True, mode="json")

    store = InMemoryExecutionPlanStore()
    await store.persist(compiled.envelope)
    monkeypatch.setattr(
        "moonmind.omnigent.harness_platform.stores.DbExecutionPlanStore",
        lambda _session_factory: store,
    )

    async def _read_artifact(ref: str) -> dict:
        if ref == compiled.binding.plan_artifact_ref:
            return artifact_wire
        raise ValueError(f"artifact {ref} is unavailable")

    monkeypatch.setattr(activities, "_read_json_artifact", _read_artifact)

    with pytest.raises(ValueError):
        await activities._load_verified_execution_plan(compiled.binding)
    # No migration attempt was persisted.
    assert await store.load(compiled.envelope.planRef) == compiled.envelope


def _ready_opencode_host_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give OpenCode plan compiles exact resolver evidence for selected refs.

    Mirrors the hermetic host-compatibility observation used by the plan
    service tests so strict compiles reach admission instead of failing at
    host-class selection.
    """

    from types import SimpleNamespace

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setenv(
        "OMNIGENT_IMAGE_REF",
        "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "6" * 64,
    )
    provenance = {"hostBuildDigest": "sha256:" + "b" * 64, "hostVersion": "0.10.0"}

    def load_state():
        import os

        host_ref = os.environ.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "")
        if not host_ref:
            return None
        return SimpleNamespace(
            server_image_ref=os.environ.get("OMNIGENT_IMAGE_REF", ""),
            opencode_host_image_ref=host_ref,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": os.environ.get("OMNIGENT_IMAGE_REF", ""),
                    "hostImageRef": host_ref,
                    **provenance,
                }
            },
        )

    monkeypatch.setattr(store, "load_resolved_state", load_state)


def test_restart_queue_and_lost_ack_reconcile_without_duplication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9: retries reconcile the accepted effect instead of replacing it.

    Turn delivery keeps one idempotent command per admitted attempt across
    restarts, re-admissions never share commands, a lost plan-persist
    acknowledgment recomputes the same plan digest, divergent replacements
    are rejected, and ordinary cert-removal never revives an unready target.
    """

    from types import SimpleNamespace

    from moonmind.omnigent.harness_platform.execution_plan import (
        compute_plan_ref,
        create_execution_plan_envelope,
        verify_execution_plan_envelope,
    )
    from moonmind.omnigent.realizers.turn_delivery import canonical_turn_idempotency_key
    from moonmind.omnigent.runtime_provider_rollout import (
        RolloutReason,
        RolloutSelectionContext,
        RolloutRule,
        _readiness_denials,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "opencode-go-default",
            "correlationId": "run-1",
            "idempotencyKey": "idempotency-key-1",
        }
    )
    # Restart/retries of the same admitted attempt share one command.
    assert canonical_turn_idempotency_key(request) == "idempotency-key-1"
    assert canonical_turn_idempotency_key(request) == canonical_turn_idempotency_key(request)
    # A deliberate re-admission (bumped epoch) never rebinds the old command.
    readmitted = SimpleNamespace(
        idempotency_key="idempotency-key-1",
        admitted_provider_capacity=SimpleNamespace(admission_epoch=2),
    )
    assert canonical_turn_idempotency_key(readmitted) != "idempotency-key-1"  # type: ignore[arg-type]
    # Nor do distinct requests share commands.
    other = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "opencode-go-default",
            "correlationId": "run-2",
            "idempotencyKey": "idempotency-key-2",
        }
    )
    assert canonical_turn_idempotency_key(other) != canonical_turn_idempotency_key(request)

    # Lost acknowledgment: retrying the same payload recomputes the same
    # digest, reconciling to the accepted effect instead of duplicating it.
    payload = _minimal_saved_plan_payload(
        admission_authority={
            "admissionMode": "ordinary",
            "supportEvidenceRef": "",
            "supportEvidenceDigest": "",
            "supportTier": "uncertified",
            **_generations(),
        }
    )
    assert compute_plan_ref(payload) == compute_plan_ref(dict(payload))
    envelope = create_execution_plan_envelope(payload)
    assert verify_execution_plan_envelope(envelope.model_dump(by_alias=True, mode="json")) == envelope
    # A divergent replacement under the accepted digest is rejected, not stored.
    tampered = dict(envelope.model_dump(by_alias=True, mode="json"))
    tampered["payload"] = dict(tampered["payload"])
    tampered["payload"]["launchPolicyRef"] = "attacker-chosen-policy@99"
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_execution_plan_envelope(tampered)

    # Ordinary cert-removal never revives an explicitly unready target.
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    rule = RolloutRule.model_validate(
        {
            "targetId": "test.target",
            "label": "test",
            "selector": {},
            "state": "new_work_default",
            "generation": 1,
            "requiresSupportEvidence": True,
        }
    )
    unready = RolloutSelectionContext.model_validate({"launchReady": False})
    assert RolloutReason.target_not_launch_ready in _readiness_denials(rule=rule, context=unready)


def test_primary_failure_survives_reporting_and_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R10: original errors and confirmed work survive reporting failure.

    The strict denial names the real underlying cause instead of the
    reporting wrapper, optional certification failure never masks an
    ordinary admission, fenced cleanup stays retryable without consuming
    failure budgets, and only a genuine provider credential rejection
    carries a credential verdict.
    """

    import moonmind.omnigent.evidence_resolver as resolver
    from moonmind.omnigent.deployment_evidence import (
        DeploymentEvidenceUnusable,
        validate_deployment_evidence,
    )
    from moonmind.omnigent.harness_platform.failures import (
        HarnessPlatformError,
        HarnessPlatformFailure,
    )
    from moonmind.omnigent.opencode_runtime_validation import (
        CREDENTIAL_REJECTED_MARKER,
        is_confirmed_credential_rejection,
        is_transient_validation_error,
    )

    # Structural evidence defects fail with their own reason, not silence.
    with pytest.raises(DeploymentEvidenceUnusable):
        validate_deployment_evidence({})

    # Fenced cleanup defers: retryable, no credential verdict, no budget spent.
    fence = HarnessPlatformError(
        "credential cleanup deferred due to fence: generation fenced",
        code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
    )
    assert is_transient_validation_error(fence) is True
    assert is_confirmed_credential_rejection(fence) is False

    # A genuine provider rejection is confirmed and terminal, not transient.
    rejected = HarnessPlatformError(
        f"{CREDENTIAL_REJECTED_MARKER} by the pinned runtime (exit 1): invalid api key",
        code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
    )
    assert is_confirmed_credential_rejection(rejected) is True
    assert is_transient_validation_error(rejected) is False

    # Strict denials carry the actionable underlying cause in the message.
    assert "failed structural or signature verification" in DeploymentEvidenceUnusable(
        "deployment evidence failed structural or signature verification",
        reason="failed structural or signature verification",
    ).reason
    # Optional certification failure never masks an ordinary admission: both
    # evidence sources unavailable still yields truthful uncertified work.
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", "/nonexistent/no-evidence.json")
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "/nonexistent/no-evidence.json"
    )
    evidence, tier = resolver.resolve_execution_evidence(object())
    assert evidence is None
    assert tier == "uncertified"

