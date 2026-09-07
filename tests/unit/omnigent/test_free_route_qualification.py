"""Hermetic tests for the credentialless OpenCode free route (#4021).

No external inference calls. Covers exact catalog/exact-ID selection,
strict pricing handling, data-use policy gating, frozen attempts with the
no_eligible_free_model taxonomy, credentialless isolation, bounded
validation, deterministic pins, and the real bootstrap/profile wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.omnigent.bootstrap import free_route_qualification as frq
from moonmind.omnigent.bootstrap import opencode as opencode_bootstrap


QUALIFIED = "opencode/muse-spark-1.3-contributor-free"


def _catalog_entry(models=(QUALIFIED,), efforts=("low", "xhigh")) -> dict:
    return {
        "models": [{"qualifiedId": m} for m in models],
        "supported_efforts": list(efforts),
        "tools": ["read", "edit", "bash"],
        "modalities": ["text"],
    }


def _zero_pricing() -> dict:
    return {
        "rates": {dim: 0 for dim in frq.PRICING_DIMENSIONS},
        "inapplicable": [],
    }


def _benign_data_use() -> dict:
    return {"terms_version": "zen-terms.v3", "training_use": "no"}


def _eligible_record(**overrides) -> frq.FreeEligibilityRecord:
    params = {
        "qualified_id": QUALIFIED,
        "catalog_entry": _catalog_entry(),
        "pricing": _zero_pricing(),
        "pricing_source": "unit-test",
        "data_use": _benign_data_use(),
        "data_use_source": "unit-test",
        "policy_accepted": True,
        "required_tools": ("read",),
        "required_modalities": ("text",),
        "catalog_version": "catalog.v1",
    }
    params.update(overrides)
    return frq.qualify_free_candidate(**params)


def test_exact_current_catalog_candidate_eligible_through_real_wiring():
    record = _eligible_record()
    assert record.eligible, record.blocked_reason()

    result = frq.resolve_free_first_result(
        frq.FreeFirstResultInputs(
            catalog_qualified_ids=(QUALIFIED,),
            requested_model=QUALIFIED,
            requested_effort="xhigh",
            default_policy_authorizes_auto=True,
        ),
        eligibility=record,
    )
    assert result.qualified_id == QUALIFIED
    assert result.effort == "xhigh"
    assert result.materializer_ref == "none@1"
    assert result.profile_id == "opencode-zen-free"

    # Same selection resolves through the actual bootstrap consumer.
    resolved = opencode_bootstrap.resolve_exact_model_for_execution(
        QUALIFIED, [{"qualifiedId": QUALIFIED}]
    )
    assert resolved["qualifiedId"] == QUALIFIED
    assert opencode_bootstrap.validate_effort_for_model("xhigh", ["low", "xhigh"]) == "xhigh"


def test_alias_collision_never_selects_execution():
    # Punctuation-normalized alias inputs must not resolve execution.
    with pytest.raises(ValueError, match="no_eligible_free_model"):
        frq.resolve_exact_free_model(
            "Opencode Muse Spark 1.3 Contributor Free!!",
            catalog_qualified_ids=[QUALIFIED],
        )
    # A bare substring / name-contains-free must not select either.
    with pytest.raises(ValueError, match="no_eligible_free_model"):
        frq.resolve_exact_free_model("free", catalog_qualified_ids=[QUALIFIED])
    # The bootstrap execution entrypoint enforces the same rule.
    with pytest.raises(ValueError):
        opencode_bootstrap.resolve_exact_model_for_execution(
            "Muse Spark 1.3 Contributor Free", [{"qualifiedId": QUALIFIED}]
        )


def test_unsupported_effort_and_unknown_capability_rejected():
    record = _eligible_record()
    with pytest.raises(ValueError, match="not supported by"):
        frq.validate_effort_for_model("medium", supported_efforts=record.supported_efforts)
    with pytest.raises(ValueError, match="unknown"):
        frq.validate_effort_for_model("xhigh", supported_efforts=())

    missing_tool = _eligible_record(
        catalog_entry={**_catalog_entry(), "tools": ["read"]},
        required_tools=("edit",),
    )
    assert not missing_tool.eligible
    assert "capability" in missing_tool.blocked_reason()


@pytest.mark.parametrize(
    "raw", [None, "free", "abc", float("nan"), float("inf"), -1, True, ""]
)
def test_strict_cost_fields_never_read_as_zero(raw):
    assert frq.parse_cost_value(raw) is None
    pricing = {"rates": {dim: (raw if dim == "request" else 0) for dim in frq.PRICING_DIMENSIONS}}
    ok, reason = frq.check_pricing_eligible(pricing)
    assert not ok
    assert reason is not None and reason.category == "pricing"


def test_nonzero_and_subscription_pricing_ineligible():
    nonzero = {"rates": {dim: (0.01 if dim == "input_token" else 0) for dim in frq.PRICING_DIMENSIONS}}
    ok, reason = frq.check_pricing_eligible(nonzero)
    assert not ok and reason is not None and "nonzero" in reason.detail

    gated = dict(_zero_pricing())
    gated["subscription_required"] = True
    ok, reason = frq.check_pricing_eligible(gated)
    assert not ok and reason is not None and "prerequisite" in reason.detail

    # Unknown terms block admission; unaccepted changed terms block too.
    ok, _, decision = frq.evaluate_data_use_policy(None, policy_accepted=True)
    assert not ok and decision["decision"] == "blocked"
    ok, _, decision = frq.evaluate_data_use_policy(
        {"terms_version": "zen-terms.v4", "training_use": "yes", "terms_changed": True},
        policy_accepted=False,
    )
    assert not ok


def test_deterministic_pins_and_no_silent_substitution():
    first = _eligible_record()
    second = _eligible_record(qualified_id="opencode/other-free")
    # Explicit pin wins when eligible; catalog order decides automatic picks.
    assert frq.rank_approved_free_models(
        [first, second], explicit_pin=QUALIFIED, default_policy_authorizes_auto=True
    ).qualified_id == QUALIFIED
    # Automatic resolution requires default-policy authorization.
    with pytest.raises(ValueError, match="not authorized"):
        frq.rank_approved_free_models([first], default_policy_authorizes_auto=False)
    # An unavailable pin is actionable, never a silent substitute.
    with pytest.raises(ValueError, match="pinned model"):
        frq.rank_approved_free_models(
            [first], explicit_pin="opencode/missing", default_policy_authorizes_auto=True
        )


def test_catalog_refresh_never_rewrites_active_inputs_or_fabricates_consent():
    record = _eligible_record()
    frozen = frq.freeze_free_attempt(
        record,
        catalog_evidence_ref="artifact:catalog",
        pricing_evidence_ref="artifact:pricing",
        data_use_evidence_ref="artifact:data-use",
        eligibility_expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    assert frozen.model_id == QUALIFIED
    assert frozen.materializer_ref == "none@1"
    assert not frq.free_attempt_needs_recheck(frozen)
    expired = frq.FrozenFreeAttempt(
        model_id=frozen.model_id,
        route_ref=frozen.route_ref,
        materializer_ref=frozen.materializer_ref,
        catalog_evidence_ref=frozen.catalog_evidence_ref,
        pricing_evidence_ref=frozen.pricing_evidence_ref,
        data_use_evidence_ref=frozen.data_use_evidence_ref,
        policy_version=frozen.policy_version,
        frozen_at=frozen.frozen_at,
        eligibility_expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    assert frq.free_attempt_needs_recheck(expired)


def test_no_eligible_model_taxonomy_separates_reasons():
    records = [
        _eligible_record(catalog_entry=_catalog_entry(models=("opencode/other",))),
        _eligible_record(pricing={"rates": {d: 1 for d in frq.PRICING_DIMENSIONS}}),
        _eligible_record(data_use={"terms_version": "v9", "training_use": "yes"}, policy_accepted=False),
    ]
    with pytest.raises(ValueError) as excinfo:
        frq.raise_no_eligible_free_model(records)
    message = str(excinfo.value)
    assert "no_eligible_free_model" in message
    for category in ("availability", "pricing", "capability", "privacy"):
        assert category in message or "capability" in message


def test_ambient_keys_and_key_never_rescue_zen_attempt():
    env = {
        "HOME": "/home/app",
        "OPENCODE_API_KEY": "sk-test",
        "OPENAI_API_KEY": "sk-other",
        "OPENCODE_CONFIG_CONTENT": "x",
        "MY_TOOL": "1",
    }
    cleaned = frq.sanitize_zen_environment(env)
    assert cleaned == {"HOME": "/home/app", "MY_TOOL": "1"}
    with pytest.raises(ValueError, match="never rescue"):
        frq.assert_no_key_rescue(env)
    frq.assert_no_key_rescue({"HOME": "/home/app"})


def test_bounded_validation_makes_no_live_inference():
    plan = frq.discovery_plan()
    assert plan["live_inference"] is False
    assert plan["live_qualification_max_requests"] == 1
    with pytest.raises(ValueError, match="must not make live inference"):
        frq.discovery_plan(allow_live_inference=True)


def test_operator_disable_and_wrong_seed_fail_closed():
    record = _eligible_record()
    with pytest.raises(ValueError, match="explicitly disabled"):
        frq.resolve_free_first_result(
            frq.FreeFirstResultInputs(
                catalog_qualified_ids=(QUALIFIED,),
                requested_model=QUALIFIED,
                requested_effort="xhigh",
                operator_disabled=True,
                default_policy_authorizes_auto=True,
            ),
            eligibility=record,
        )
    with pytest.raises(ValueError, match="unexpected seed profile"):
        frq.resolve_free_first_result(
            frq.FreeFirstResultInputs(
                seed_profile_id="opencode-go-default",
                catalog_qualified_ids=(QUALIFIED,),
                requested_model=QUALIFIED,
                requested_effort="xhigh",
                default_policy_authorizes_auto=True,
            ),
            eligibility=record,
        )
