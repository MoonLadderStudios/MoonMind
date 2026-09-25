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
