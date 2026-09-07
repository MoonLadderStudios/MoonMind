"""Qualification coverage for the credentialless Zen free route (#4021).

Hermetic by construction: no test here makes an external inference call.
The live-qualification record is a separate budgeted step that consumes the
pure verdicts exercised below.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.omnigent.bootstrap.free_model_eligibility import (
    NO_ELIGIBLE_FREE_MODEL,
    FrozenFreeModelSelection,
    assess_free_model_candidate,
    is_exact_qualified_match,
    parse_cost_value,
    select_eligible_free_model,
)
from moonmind.omnigent.bootstrap.opencode import resolve_exact_qualified_id
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.materializers import (
    CREDENTIALLESS_FORBIDDEN_AMBIENT_ENV_KEYS,
    assert_no_credentialless_ambient_env,
    clear_credentialless_ambient_env,
)

FREE_ID = "opencode/muse-spark-1.3-contributor-free"


def _candidate(**overrides):
    pricing = {dim: 0 for dim in (
        "request", "input_token", "output_token", "cache_read",
        "cache_write", "reasoning_token", "tool_call",
    )}
    base = {
        "qualifiedId": FREE_ID,
        "pricing": pricing,
        "required_tools": ["serve"],
        "supported_efforts": ["minimal", "low", "medium", "high", "xhigh"],
    }
    base.update(overrides)
    return base


def test_no_eligible_free_model_code_name():
    assert NO_ELIGIBLE_FREE_MODEL == "no_eligible_free_model"


@pytest.mark.parametrize(
    "value", [None, "", "   ", "abc", "free", "-1", -0.5, float("nan"), float("inf")],
)
def test_parse_cost_unknown_is_never_zero(value):
    known, is_free = parse_cost_value(value)
    assert known is False
    assert is_free is False


@pytest.mark.parametrize("value", [0, 0.0, "0", "0.0", " 0 "])
def test_parse_cost_zero_is_free(value):
    known, is_free = parse_cost_value(value)
    assert (known, is_free) == (True, True)


def test_parse_cost_nonzero_is_known_not_free():
    assert parse_cost_value(0.01) == (True, False)
    assert parse_cost_value("12.5") == (True, False)


def test_exact_match_rejects_normalization_collisions():
    assert is_exact_qualified_match(FREE_ID, FREE_ID) is True
    assert is_exact_qualified_match(f"  {FREE_ID}  ", FREE_ID) is True
    # Case / punctuation differences are display aids, never execution.
    assert is_exact_qualified_match(FREE_ID.upper(), FREE_ID) is False
    assert is_exact_qualified_match("opencode/musespark13contributorfree", FREE_ID) is False
    assert is_exact_qualified_match("opencode-go/muse-spark-1.3-contributor-free", FREE_ID) is False


def test_happy_path_candidate_is_eligible():
    verdict = assess_free_model_candidate(
        candidate=_candidate(), catalog_ids={FREE_ID}, requested_effort="xhigh",
    )
    assert verdict.eligible is True
    assert verdict.reasons.as_dict() == {}


def test_missing_pricing_dimension_is_unknown_not_zero():
    pricing = {"request": 0}
    verdict = assess_free_model_candidate(
        candidate=_candidate(pricing=pricing), catalog_ids={FREE_ID},
    )
    assert verdict.eligible is False
    assert "input_token" in (verdict.reasons.pricing or "")


def test_malformed_pricing_is_unknown():
    pricing = {dim: 0 for dim in (
        "request", "input_token", "output_token", "cache_read",
        "cache_write", "reasoning_token",
    )}
    pricing["tool_call"] = "free"
    verdict = assess_free_model_candidate(
        candidate=_candidate(pricing=pricing), catalog_ids={FREE_ID},
    )
    assert verdict.eligible is False
    assert "tool_call" in (verdict.reasons.pricing or "")


def test_nonzero_pricing_is_ineligible():
    pricing = _candidate()["pricing"]
    pricing = dict(pricing, input_token=0.003)
    verdict = assess_free_model_candidate(
        candidate=_candidate(pricing=pricing), catalog_ids={FREE_ID},
    )
    assert verdict.eligible is False
    assert "input_token" in (verdict.reasons.pricing or "")


def test_subscription_dependent_zero_is_not_credentialless_eligible():
    verdict = assess_free_model_candidate(
        candidate=_candidate(subscription_or_key_prerequisite="contributor subscription"),
        catalog_ids={FREE_ID},
    )
    assert verdict.eligible is False
    assert "subscription" in (verdict.reasons.pricing or "").lower()


def test_inapplicable_dimensions_must_be_declared():
    pricing = {dim: 0 for dim in ("request", "input_token", "output_token")}
    verdict = assess_free_model_candidate(
        candidate=_candidate(
            pricing=pricing,
            inapplicable_dimensions=["cache_read", "cache_write", "reasoning_token", "tool_call"],
        ),
        catalog_ids={FREE_ID},
    )
    assert verdict.eligible is True


def test_wrong_route_is_not_credentialless():
    candidate = _candidate(qualifiedId="opencode-go/muse-spark-1.3-contributor")
    verdict = assess_free_model_candidate(
        candidate=candidate,
        catalog_ids={"opencode-go/muse-spark-1.3-contributor"},
    )
    assert verdict.eligible is False
    assert verdict.reasons.availability


def test_absent_from_catalog_is_unavailable():
    verdict = assess_free_model_candidate(
        candidate=_candidate(), catalog_ids={"opencode/some-other-model"},
    )
    assert verdict.eligible is False
    assert "catalog" in (verdict.reasons.availability or "").lower()


def test_catalog_presence_alone_does_not_make_eligible():
    # A bare catalog entry with no pricing evidence is unknown, not free.
    verdict = assess_free_model_candidate(
        candidate={"qualifiedId": FREE_ID}, catalog_ids={FREE_ID},
    )
    assert verdict.eligible is False
    assert verdict.reasons.pricing


def test_missing_tool_and_unsupported_effort_are_capability_reasons():
    verdict = assess_free_model_candidate(
        candidate=_candidate(required_tools=[], supported_efforts=["low"]),
        catalog_ids={FREE_ID},
        requested_effort="xhigh",
    )
    assert verdict.eligible is False
    assert verdict.reasons.capability
    assert "xhigh" in verdict.reasons.capability


def test_consent_required_without_recorded_decision_blocks_default():
    candidate = _candidate(data_use={"contributor_training": True, "terms_version": "2026-09"})
    verdict = assess_free_model_candidate(
        candidate=candidate, catalog_ids={FREE_ID},
        data_use_accepted=True, accepted_data_use_policy_versions=set(),
    )
    assert verdict.eligible is False
    assert "consent" in verdict.reasons.privacy.lower() or "decision" in verdict.reasons.privacy.lower()


def test_consent_with_recorded_policy_version_is_eligible():
    from moonmind.omnigent.bootstrap.free_model_eligibility import FREE_ROUTE_POLICY_VERSION

    candidate = _candidate(data_use={"contributor_training": True})
    verdict = assess_free_model_candidate(
        candidate=candidate, catalog_ids={FREE_ID},
        data_use_accepted=True,
        accepted_data_use_policy_versions={FREE_ROUTE_POLICY_VERSION},
        data_use_policy_version=FREE_ROUTE_POLICY_VERSION,
    )
    assert verdict.eligible is True


def test_pinned_selection_preserves_pin_without_substitution():
    other = _candidate(qualifiedId="opencode/other-free")
    verdict = select_eligible_free_model(
        candidates=[other, _candidate()], catalog_ids={FREE_ID, "opencode/other-free"},
        pinned_qualified_id=FREE_ID,
    )
    assert verdict.eligible is True
    assert verdict.qualified_id == FREE_ID


def test_pinned_but_absent_is_actionable_not_substitute():
    verdict = select_eligible_free_model(
        candidates=[_candidate()], catalog_ids={FREE_ID},
        pinned_qualified_id="opencode/does-not-exist",
    )
    assert verdict.eligible is False
    assert "does-not-exist" in (verdict.reasons.availability or "")
    assert "opencode/other" not in (verdict.reasons.availability or "")


def test_automatic_resolution_requires_default_policy_authority():
    verdict = select_eligible_free_model(
        candidates=[_candidate()], catalog_ids={FREE_ID}, allow_automatic=False,
    )
    assert verdict.eligible is False
    assert "not authorized" in (verdict.reasons.availability or "").lower()


def test_automatic_resolution_is_deterministic():
    first = _candidate(qualifiedId="opencode/a-free")
    second = _candidate(qualifiedId="opencode/b-free")
    verdict = select_eligible_free_model(
        candidates=[second, first],
        catalog_ids={"opencode/a-free", "opencode/b-free"},
        allow_automatic=True,
    )
    assert verdict.eligible is True
    assert verdict.qualified_id == "opencode/a-free"


def test_frozen_selection_recheck_before_new_send():
    fresh = FrozenFreeModelSelection(
        qualified_id=FREE_ID, decided_at=datetime.now(UTC).isoformat(),
    )
    assert fresh.needs_recheck() is False
    expired = FrozenFreeModelSelection(
        qualified_id=FREE_ID,
        decided_at=(datetime.now(UTC) - timedelta(hours=7)).isoformat(),
        ttl=timedelta(hours=6),
    )
    assert expired.needs_recheck() is True
    assert FrozenFreeModelSelection(qualified_id=FREE_ID).needs_recheck() is True


def test_resolve_exact_qualified_id_requires_catalog_membership():
    catalog = [{"qualifiedId": FREE_ID}]
    resolved = resolve_exact_qualified_id(FREE_ID, catalog)
    assert resolved["qualifiedId"] == FREE_ID
    with pytest.raises(ValueError, match="unavailable in the exact"):
        resolve_exact_qualified_id("opencode/other-model", catalog)
    # A punctuation-normalized collision is not an exact match.
    with pytest.raises(ValueError, match="unavailable in the exact"):
        resolve_exact_qualified_id("opencode/musespark13contributorfree", catalog)
    with pytest.raises(ValueError, match="canonical qualified ID"):
        resolve_exact_qualified_id("not-a-qualified-id", catalog)


def test_credentialless_boundary_rejects_deployment_key(monkeypatch):
    assert "OPENCODE_API_KEY" in CREDENTIALLESS_FORBIDDEN_AMBIENT_ENV_KEYS
    monkeypatch.setenv("OPENCODE_API_KEY", "secret-value")
    with pytest.raises(HarnessPlatformError):
        assert_no_credentialless_ambient_env()
    monkeypatch.delenv("OPENCODE_API_KEY")
    for key in CREDENTIALLESS_FORBIDDEN_AMBIENT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert_no_credentialless_ambient_env()  # passes when clean


def test_clear_credentialless_env_reports_names_only(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "secret-value")
    cleared = clear_credentialless_ambient_env()
    assert cleared == ["OPENCODE_API_KEY"]
    assert os.environ.get("OPENCODE_API_KEY") is None
