"""Hermetic coverage for MoonLadderStudios/MoonMind#4021.

Qualify the existing free OpenCode route with explicit pricing and privacy
policy. No external inference calls; all catalog/pricing/terms inputs are
inline fixtures.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.bootstrap.free_model_eligibility import (
    DataUseDecision,
    EligibilityInput,
    FreeModelUnavailableError,
    NoEligibleFreeModelError,
    evaluate_eligibility,
    evaluate_pricing,
    format_no_eligible_free_model,
    freeze_attempt,
    parse_cost_value,
    resolve_free_default_model,
)
from moonmind.omnigent.bootstrap.opencode import (
    ZEN_FREE_QUALIFIED,
    get_supported_efforts,
    resolve_model_by_display,
    resolve_model_exact,
    validate_effort_for_model,
)
from moonmind.provider_profiles.isolation_policy import derive_isolation_policy


ZERO_PRICING = {
    "request": 0,
    "input_token": 0,
    "output_token": 0,
    "cache_read": 0,
    "cache_write": 0,
    "reasoning": 0,
    "tool": 0,
}


def _eligible_candidate(**overrides) -> EligibilityInput:
    base = {
        "qualified_id": ZEN_FREE_QUALIFIED,
        "in_catalog": True,
        "pricing": dict(ZERO_PRICING),
        "capabilities": ("read", "write", "shell"),
        "supported_efforts": ("minimal", "low", "medium", "high", "xhigh"),
        "requested_effort": "xhigh",
        "terms_version": "zen-terms.v3",
        "terms_require_approval": True,
        "data_use_decision": DataUseDecision(
            policy_version="zen-terms.v3",
            accepted=True,
            accepted_at="2026-09-10T00:00:00Z",
            accepted_by="operator",
        ),
    }
    base.update(overrides)
    return EligibilityInput(**base)


def test_parse_cost_value_treats_unknown_as_none_not_zero() -> None:
    assert parse_cost_value(None) is None
    assert parse_cost_value("") is None
    assert parse_cost_value("free") is None
    assert parse_cost_value("nan") is None
    assert parse_cost_value("inf") is None
    assert parse_cost_value(float("nan")) is None
    assert parse_cost_value(float("inf")) is None
    assert parse_cost_value(-1) is None
    assert parse_cost_value("-0.5") is None
    assert parse_cost_value(True) is None
    assert parse_cost_value(0) == 0.0
    assert parse_cost_value("0") == 0.0
    assert parse_cost_value(0.0) == 0.0


def test_pricing_missing_dimension_blocks() -> None:
    partial = dict(ZERO_PRICING)
    del partial["tool"]
    verdict = evaluate_pricing(partial)
    assert not verdict.eligible
    assert "tool" in verdict.unknown_dimensions


def test_pricing_nonzero_and_subscription_blocks() -> None:
    nonzero = dict(ZERO_PRICING)
    nonzero["input_token"] = 0.01
    verdict = evaluate_pricing(nonzero)
    assert not verdict.eligible
    assert "input_token" in verdict.nonzero_dimensions
    verdict2 = evaluate_pricing(dict(ZERO_PRICING), requires_subscription_or_key=True)
    assert not verdict2.eligible
    assert verdict2.reason == "subscription_or_key_required"


def test_pricing_explicit_inapplicable_passes() -> None:
    pricing = dict(ZERO_PRICING)
    del pricing["tool"]
    verdict = evaluate_pricing(pricing, inapplicable=frozenset({"tool"}))
    assert verdict.eligible


def test_eligibility_requires_catalog_presence_and_route() -> None:
    verdict = evaluate_eligibility(_eligible_candidate(in_catalog=False))
    assert not verdict.eligible
    assert verdict.reasons["availability"] == "not_in_catalog"
    verdict2 = evaluate_eligibility(
        _eligible_candidate(
            qualified_id="opencode-go/some-paid",
            provider_id="opencode-go",
        )
    )
    assert not verdict2.eligible
    assert "availability" in verdict2.reasons


def test_eligibility_missing_tools_and_effort() -> None:
    verdict = evaluate_eligibility(_eligible_candidate(capabilities=("read",)))
    assert not verdict.eligible
    assert verdict.reasons["capability"].startswith("missing_tools:")
    verdict2 = evaluate_eligibility(
        _eligible_candidate(supported_efforts=("low",), requested_effort="xhigh")
    )
    assert not verdict2.eligible
    assert verdict2.reasons["capability"].startswith("unsupported_effort:")


def test_eligibility_unaccepted_terms_blocks() -> None:
    verdict = evaluate_eligibility(_eligible_candidate(data_use_decision=None))
    assert not verdict.eligible
    assert verdict.reasons["privacy"].startswith("privacy:unaccepted_terms:")
    # Historical consent must not count: wrong version is still blocked.
    verdict2 = evaluate_eligibility(
        _eligible_candidate(
            data_use_decision=DataUseDecision(policy_version="zen-terms.v2", accepted=True)
        )
    )
    assert not verdict2.eligible


def test_eligibility_happy_path() -> None:
    assert evaluate_eligibility(_eligible_candidate()).eligible


def test_frozen_attempt_and_signal_shape() -> None:
    frozen = freeze_attempt(
        qualified_id=ZEN_FREE_QUALIFIED,
        catalog={"ids": [ZEN_FREE_QUALIFIED]},
        pricing=dict(ZERO_PRICING),
        data_use_version="zen-terms.v3",
    )
    payload = frozen.as_dict()
    assert payload["modelId"] == ZEN_FREE_QUALIFIED
    assert payload["materializerRef"] == "none@1"
    assert payload["selectionPolicyVersion"] == "free-model-selection.v1"
    assert payload["catalogDigest"].startswith("sha256:")
    signal = format_no_eligible_free_model({"pricing": "x", "privacy": "y"})
    assert signal["code"] == "no_eligible_free_model"
    assert set(signal["reasons"]) == {"pricing", "privacy"}


def test_resolve_free_default_preserves_explicit_pin() -> None:
    catalog = [ZEN_FREE_QUALIFIED]
    candidates = {ZEN_FREE_QUALIFIED: _eligible_candidate()}
    selected, frozen = resolve_free_default_model(
        explicit_pin=ZEN_FREE_QUALIFIED,
        catalog_ids=catalog,
        candidates=candidates,
        default_policy_authorizes_auto=True,
    )
    assert selected == ZEN_FREE_QUALIFIED
    assert frozen.model_id == ZEN_FREE_QUALIFIED
    with pytest.raises(FreeModelUnavailableError):
        resolve_free_default_model(
            explicit_pin="opencode/missing-model",
            catalog_ids=catalog,
            candidates=candidates,
            default_policy_authorizes_auto=True,
        )


def test_resolve_free_default_ineligible_pin_raises_no_eligible() -> None:
    catalog = [ZEN_FREE_QUALIFIED]
    bad = _eligible_candidate(pricing={"request": 1, **{k: 0 for k in ZERO_PRICING if k != "request"}})
    with pytest.raises(NoEligibleFreeModelError) as exc:
        resolve_free_default_model(
            explicit_pin=ZEN_FREE_QUALIFIED,
            catalog_ids=catalog,
            candidates={ZEN_FREE_QUALIFIED: bad},
            default_policy_authorizes_auto=True,
        )
    assert exc.value.details["code"] == "no_eligible_free_model"
    assert "pricing" in exc.value.details["reasons"]


def test_resolve_free_default_auto_requires_policy() -> None:
    with pytest.raises(FreeModelUnavailableError):
        resolve_free_default_model(
            explicit_pin=None,
            catalog_ids=[ZEN_FREE_QUALIFIED],
            candidates={ZEN_FREE_QUALIFIED: _eligible_candidate()},
            default_policy_authorizes_auto=False,
        )
    selected, _ = resolve_free_default_model(
        explicit_pin=None,
        catalog_ids=[ZEN_FREE_QUALIFIED],
        candidates={ZEN_FREE_QUALIFIED: _eligible_candidate()},
        default_policy_authorizes_auto=True,
    )
    assert selected == ZEN_FREE_QUALIFIED


def test_resolve_free_default_empty_catalog_reports_no_eligible() -> None:
    with pytest.raises(NoEligibleFreeModelError) as exc:
        resolve_free_default_model(
            explicit_pin=None,
            catalog_ids=[],
            candidates={},
            default_policy_authorizes_auto=True,
        )
    assert exc.value.details["code"] == "no_eligible_free_model"


def test_resolve_model_exact_requires_exact_catalog_match() -> None:
    catalog = [{"qualifiedId": ZEN_FREE_QUALIFIED, "displayName": "Free"}]
    resolved = resolve_model_exact(ZEN_FREE_QUALIFIED, catalog)
    assert resolved["qualifiedId"] == ZEN_FREE_QUALIFIED
    # Punctuation-normalized collision must not select a different provider.
    other = [{"qualifiedId": "opencode-go/muse-spark-13-contributor-free"}]
    with pytest.raises(ValueError):
        resolve_model_exact(ZEN_FREE_QUALIFIED, other)
    with pytest.raises(ValueError):
        resolve_model_exact(ZEN_FREE_QUALIFIED, None)
    # Name containing free alone never qualifies.
    with pytest.raises(ValueError):
        resolve_model_exact("opencode/some-free-thing", catalog)


def test_resolve_by_display_free_route_never_preserved_pre_validation() -> None:
    # Keyed route preserves its canonical identity before live validation.
    keyed = resolve_model_by_display("opencode-go/gpt-5.6-luna")
    assert keyed["qualifiedId"] == "opencode-go/gpt-5.6-luna"
    # Credentialless opencode/* route must come from the exact catalog.
    with pytest.raises(ValueError) as exc:
        resolve_model_by_display("opencode/some-new-free-model")
    assert "credentialless" in str(exc.value)


def test_validate_effort_for_model_uses_actual_values() -> None:
    assert validate_effort_for_model("xhigh", ZEN_FREE_QUALIFIED) == "xhigh"
    assert get_supported_efforts(ZEN_FREE_QUALIFIED) is not None
    with pytest.raises(ValueError):
        validate_effort_for_model("ultra", ZEN_FREE_QUALIFIED)


def test_credentialless_isolation_clears_ambient_keys() -> None:
    policy = derive_isolation_policy(
        runtime_id="opencode",
        provider_id="opencode",
        authentication_method="none",
        credential_source="none",
        runtime_materialization_mode="composite",
    )
    assert policy is not None
    assert "OPENCODE_API_KEY" in policy.keys
    assert "OPENCODE_AUTH_CONTENT" in policy.keys
    assert "OPENCODE_CONFIG" in policy.keys
    assert "OPENCODE_CONFIG_CONTENT" in policy.keys
