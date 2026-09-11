"""Consumer wiring for MoonLadderStudios/MoonMind#4021.

Exercises the free-model eligibility helpers through their real production
consumers (bootstrap/profile/plan wiring, Settings authority, frozen-attempt
persistence, catalog surfacing). Hermetic only: inline fixtures, no network,
no inference calls.
"""

from __future__ import annotations

import pytest

from api_service.api.routers.omnigent_catalog import (
    _REASONS,
    free_model_gate_reason,
)
from moonmind.omnigent.bootstrap.free_model_eligibility import (
    FREE_PROVIDER_ID,
    NO_ELIGIBLE_FREE_MODEL_CODE,
    ZEN_FREE_TERMS_VERSION,
    DataUseDecision,
    EligibilityInput,
    attach_frozen_attempt_to_resolved,
    build_eligibility_input,
    build_live_qualification_record,
    evaluate_data_use,
    evaluate_eligibility,
    freeze_attempt,
    frozen_attempt_from_resolved,
    rank_approved_candidates,
    recheck_frozen_attempt,
    resolve_free_default_model,
    zen_free_data_use_decision_from_settings,
    zen_free_route_blocked_reason,
)
from moonmind.omnigent.bootstrap.models import BootstrapResolved
from moonmind.omnigent.bootstrap.opencode import (
    ZEN_FREE_QUALIFIED,
    resolve_bootstrap_model,
    resolve_model_exact,
)
from moonmind.workflows.executions.model_resolver import (
    coerce_effort_for_model,
    resolve_model_effort,
    resolve_opencode_effort,
)

ZERO_PRICING = {
    "request": 0,
    "input_token": 0,
    "output_token": 0,
    "cache_read": 0,
    "cache_write": 0,
    "reasoning": 0,
    "tool": 0,
}

NEW_FREE_ID = "opencode/some-new-free-model"


def _eligible_input(qualified_id: str = ZEN_FREE_QUALIFIED) -> EligibilityInput:
    decision = DataUseDecision(
        policy_version=ZEN_FREE_TERMS_VERSION,
        accepted=True,
        accepted_by="operator-settings",
    )
    return build_eligibility_input(
        qualified_id=qualified_id,
        catalog_ids=[qualified_id],
        pricing=dict(ZERO_PRICING),
        capabilities=("read", "write", "shell"),
        supported_efforts=("minimal", "low", "medium", "high", "xhigh"),
        requested_effort="xhigh",
        terms_version=ZEN_FREE_TERMS_VERSION,
        terms_require_approval=True,
        data_use_decision=decision,
    )


def test_bootstrap_exact_path_for_new_opencode_id_through_real_consumer() -> None:
    catalog = [
        {"qualifiedId": ZEN_FREE_QUALIFIED, "displayName": "Zen Free"},
        {"qualifiedId": NEW_FREE_ID, "displayName": "New Free"},
    ]
    # New canonical opencode/... ID resolves through the bootstrap consumer
    # only with the exact observed catalog.
    resolved = resolve_bootstrap_model(NEW_FREE_ID, catalog)
    assert resolved["qualifiedId"] == NEW_FREE_ID
    with pytest.raises(ValueError):
        resolve_bootstrap_model(NEW_FREE_ID, None)
    with pytest.raises(ValueError):
        resolve_bootstrap_model(NEW_FREE_ID, [{"qualifiedId": ZEN_FREE_QUALIFIED}])
    # Punctuation-normalized collision cannot choose a different provider.
    with pytest.raises(ValueError):
        resolve_model_exact(
            ZEN_FREE_QUALIFIED,
            [{"qualifiedId": "opencode-go/muse-spark-13-contributor-free"}],
        )


def test_bootstrap_effort_uses_actual_model_values() -> None:
    assert coerce_effort_for_model(ZEN_FREE_QUALIFIED, "xhigh") == "xhigh"
    with pytest.raises(ValueError):
        coerce_effort_for_model(ZEN_FREE_QUALIFIED, "ultra")
    # Profile/plan wiring path: controller._resolve_profile_model_effort uses
    # the same per-model validator (import here to prove the real consumer).
    from moonmind.omnigent.bootstrap.controller import _resolve_profile_model_effort

    class _Profile:
        runtime_id = "opencode"
        default_model = ZEN_FREE_QUALIFIED
        default_effort = "xhigh"
        model_tiers = []
        default_model_tier = 1

    model, effort = _resolve_profile_model_effort(_Profile())
    assert model == ZEN_FREE_QUALIFIED
    assert effort == "xhigh"

    class _BadEffortProfile(_Profile):
        default_effort = "ultra"

    with pytest.raises(ValueError):
        _resolve_profile_model_effort(_BadEffortProfile())


def test_settings_authority_exact_version_gates_ranking() -> None:
    # Real authority: default env accepts the exact current terms version.
    decision = zen_free_data_use_decision_from_settings(env={})
    assert decision is not None
    assert decision.policy_version == ZEN_FREE_TERMS_VERSION
    assert decision.accepted is True
    ok, _ = evaluate_data_use(
        terms_version=ZEN_FREE_TERMS_VERSION,
        terms_require_approval=True,
        decision=decision,
    )
    assert ok is True
    # Operator decline blocks (no decision).
    assert (
        zen_free_data_use_decision_from_settings(
            env={"OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE": "false"}
        )
        is None
    )
    # Wrong-version or missing decision blocks through the real gate.
    wrong = DataUseDecision(policy_version="zen-terms.v2", accepted=True)
    ok2, _ = evaluate_data_use(
        terms_version=ZEN_FREE_TERMS_VERSION,
        terms_require_approval=True,
        decision=wrong,
    )
    assert ok2 is False
    ok3, _ = evaluate_data_use(
        terms_version=ZEN_FREE_TERMS_VERSION,
        terms_require_approval=True,
        decision=None,
    )
    assert ok3 is False
    # Approved-set ranking honors the exact-version decision.
    blocked_input = EligibilityInput(
        qualified_id=ZEN_FREE_QUALIFIED,
        in_catalog=True,
        pricing=dict(ZERO_PRICING),
        capabilities=("read", "write", "shell"),
        supported_efforts=("minimal", "low", "medium", "high", "xhigh"),
        requested_effort="xhigh",
        terms_version=ZEN_FREE_TERMS_VERSION,
        terms_require_approval=True,
        data_use_decision=None,
    )
    selected, blocked = rank_approved_candidates(
        {ZEN_FREE_QUALIFIED: blocked_input}, catalog_order=[ZEN_FREE_QUALIFIED]
    )
    assert selected is None
    assert blocked["privacy"].startswith("privacy:unaccepted_terms:")
    selected2, _ = rank_approved_candidates(
        {ZEN_FREE_QUALIFIED: _eligible_input()}, catalog_order=[ZEN_FREE_QUALIFIED]
    )
    assert selected2 == ZEN_FREE_QUALIFIED


def test_frozen_attempt_persists_and_rechecks_without_rewrite() -> None:
    catalog = {"ids": [ZEN_FREE_QUALIFIED]}
    frozen = freeze_attempt(
        qualified_id=ZEN_FREE_QUALIFIED,
        catalog=catalog,
        pricing=dict(ZERO_PRICING),
        data_use_version=ZEN_FREE_TERMS_VERSION,
    )
    resolved = BootstrapResolved(
        qualifiedModelId=ZEN_FREE_QUALIFIED,
        displayName="Zen Free",
    )
    attached = attach_frozen_attempt_to_resolved(resolved, frozen)
    assert attached.free_model_attempt is not None
    assert attached.free_model_attempt["modelId"] == ZEN_FREE_QUALIFIED
    # Active inputs unchanged: the original record still carries no bundle.
    assert resolved.free_model_attempt is None
    # Round-trip through the durable store shape.
    reloaded = BootstrapResolved.model_validate(
        attached.model_dump(mode="json", by_alias=True)
    )
    recovered = frozen_attempt_from_resolved(reloaded)
    assert recovered is not None
    assert recovered.model_id == ZEN_FREE_QUALIFIED
    current, _ = recheck_frozen_attempt(
        recovered,
        catalog=catalog,
        pricing=dict(ZERO_PRICING),
        data_use_version=ZEN_FREE_TERMS_VERSION,
    )
    assert current is True
    # Changed evidence expires the frozen bundle without rewriting it.
    current2, reason2 = recheck_frozen_attempt(
        recovered,
        catalog={"ids": ["opencode/other"]},
        pricing=dict(ZERO_PRICING),
        data_use_version=ZEN_FREE_TERMS_VERSION,
    )
    assert current2 is False
    assert "catalog" in reason2
    current3, reason3 = recheck_frozen_attempt(
        recovered,
        catalog=catalog,
        pricing=dict(ZERO_PRICING),
        data_use_version="zen-terms.v9",
    )
    assert current3 is False
    assert "terms" in reason3
    # resolve_free_default_model still preserves pins and never substitutes.
    selected, _ = resolve_free_default_model(
        explicit_pin=ZEN_FREE_QUALIFIED,
        catalog_ids=[ZEN_FREE_QUALIFIED],
        candidates={ZEN_FREE_QUALIFIED: _eligible_input()},
        default_policy_authorizes_auto=True,
    )
    assert selected == ZEN_FREE_QUALIFIED


def test_production_resolver_coerces_opencode_effort() -> None:
    """The real resolve_model_effort path enforces per-model effort upstream.

    MoonLadderStudios/MoonMind#4021 req-3: plan/launch consumers share
    resolve_model_effort, so the opencode/ route fails closed on unsupported
    effort there instead of assuming the seeded default. Other runtimes keep
    pass-through behavior.
    """
    from types import SimpleNamespace

    def _profile(**overrides: object) -> SimpleNamespace:
        values: dict[str, object] = {
            "runtime_id": "opencode",
            "provider_id": FREE_PROVIDER_ID,
            "default_model": ZEN_FREE_QUALIFIED,
            "default_effort": "xhigh",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    resolved = resolve_model_effort(
        runtime_id="opencode",
        profile=_profile(),
        require_launch_ready=False,
    )
    assert resolved.model == ZEN_FREE_QUALIFIED
    assert resolved.effort == "xhigh"

    with pytest.raises(ValueError):
        resolve_model_effort(
            runtime_id="opencode",
            profile=_profile(),
            requested_effort="ultra",
            require_launch_ready=False,
        )

    # Explicit helper scoping: opencode/ coerces, other routes pass through.
    assert resolve_opencode_effort(ZEN_FREE_QUALIFIED, "xhigh") == "xhigh"
    assert resolve_opencode_effort(ZEN_FREE_QUALIFIED, None) is None
    assert resolve_opencode_effort("other-provider/some-model", "ultra") == "ultra"
    assert resolve_opencode_effort(None, "xhigh") == "xhigh"


def test_production_gate_blocks_only_declined_free_route() -> None:
    """The Settings-sourced gate is exact-version and free-route scoped."""
    # Default deployments accept: no block for either route.
    assert zen_free_route_blocked_reason("opencode", env={}) is None
    # The keyed route is never gated by the free-route decision.
    assert (
        zen_free_route_blocked_reason(
            "opencode-go", env={"OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE": "false"}
        )
        is None
    )
    assert zen_free_route_blocked_reason("other", env={}) is None
    # An explicit operator decline blocks the free route with the exact
    # per-version privacy reason.
    blocked = zen_free_route_blocked_reason(
        "opencode", env={"OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE": "false"}
    )
    assert blocked == "privacy:unaccepted_terms:" + ZEN_FREE_TERMS_VERSION


def test_planning_admission_blocks_declined_free_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan-time admission enforces the free-route authorization (req-4)."""
    from types import SimpleNamespace

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.harness_platform.planning_service import (
        OmnigentExecutionPlanningService,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    service = SimpleNamespace(_deployment_default_model="")
    provider = SimpleNamespace(
        runtime_id="opencode",
        provider_id=FREE_PROVIDER_ID,
        default_model=ZEN_FREE_QUALIFIED,
        default_effort="xhigh",
    )

    def _request() -> AgentExecutionRequest:
        return AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
        )

    monkeypatch.delenv("OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE", raising=False)
    qualified, effort, route = OmnigentExecutionPlanningService._resolve_model(
        service, _request(), None, provider
    )
    assert qualified == ZEN_FREE_QUALIFIED
    assert effort == "xhigh"
    assert route == FREE_PROVIDER_ID

    monkeypatch.setenv("OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE", "false")
    with pytest.raises(HarnessPlatformError) as excinfo:
        OmnigentExecutionPlanningService._resolve_model(
            service, _request(), None, provider
        )
    assert NO_ELIGIBLE_FREE_MODEL_CODE in str(excinfo.value)
    assert "privacy:unaccepted_terms:" in str(excinfo.value)


def test_catalog_no_eligible_signal_and_capacity_wait_state() -> None:
    assert "no_eligible_free_model" in _REASONS
    reason = free_model_gate_reason(
        {
            "availability": "not_in_catalog",
            "pricing": "unknown_pricing:tool",
            "capability": "missing_tools:shell",
            "privacy": "privacy:unaccepted_terms:zen-terms.v3",
        }
    )
    assert reason.code == "no_eligible_free_model"
    for axis in ("availability=", "pricing=", "capability=", "privacy="):
        assert axis in reason.message
    # Capacity stays a wait state with its own codes; it never requalifies a
    # model or selects a paid fallback.
    assert "omnigent_capacity_wait" in _REASONS
    assert "profile_capacity_unavailable" in _REASONS
    assert _REASONS["no_eligible_free_model"] != _REASONS["omnigent_capacity_wait"]


def test_live_qualification_record_bounds_without_network() -> None:
    record = build_live_qualification_record(
        qualified_id=ZEN_FREE_QUALIFIED,
        host_image_ref="ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "a" * 64,
        runtime_pack_ref="opencode-native-pack@1",
        pricing_evidence=dict(ZERO_PRICING),
        data_use_version=ZEN_FREE_TERMS_VERSION,
        data_use_authorized=True,
        catalog_max_age_hours=6.0,
        probe_timeout_seconds=60.0,
        max_probe_attempts=1,
        blocked_cases=("pricing:unknown_pricing:tool",),
        unexecuted_cases=("live-inference:not-attempted-hermetic",),
    )
    assert record["modelId"] == ZEN_FREE_QUALIFIED
    assert record["materializerRef"] == "none@1"
    assert record["hostImageRef"].endswith("a" * 64)
    assert record["runtimePackRef"] == "opencode-native-pack@1"
    assert record["dataUseAuthorized"] is True
    assert record["bounds"]["singleFlight"] is True
    assert record["bounds"]["maxProbeAttempts"] == 1
    assert "pricing:unknown_pricing:tool" in record["blockedCases"]
    assert record["unexecutedCases"] == ["live-inference:not-attempted-hermetic"]
    assert evaluate_eligibility(_eligible_input()).eligible is True
