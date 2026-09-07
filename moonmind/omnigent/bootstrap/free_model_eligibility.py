"""Credentialless (Zen free-route) model eligibility qualification.

MoonLadderStudios/MoonMind#4021.

The seeded ``opencode-zen-free`` / ``none@1`` identity is identifier
preparation only until this module qualifies it. Catalog presence, a name
containing ``free``, a successful list request, or an installed binary never
establishes eligible execution on its own.

This module is pure and hermetic by construction: it performs no network or
live-inference calls. Catalog discovery and local probes consume its inputs;
the explicitly authorized, budgeted live qualification step consumes its
outputs. Provider outage, unavailable pricing, or policy uncertainty therefore
never blocks access to Settings or already saved artifacts here -- they only
produce a truthful ``no_eligible_free_model`` reason.

Design notes (one compact record, exact IDs, explicit policy):

* Eligibility binds the exact runtime/provider catalog entry plus trusted
  pricing/data-use evidence with provenance/time/version. Missing, null,
  unparsable, negative, or non-finite cost fields are unknown/invalid, never
  zero. Omitted inapplicable dimensions must be declared inapplicable, not
  assumed free.
* Execution selection uses exact qualified IDs only
  (``opencode/<provider-model-id>``). Friendly labels and punctuation-
  insensitive aliases are display/historical-loading aids and can never select
  a different model/provider for execution.
* The permitted data-use policy gates ranking of the small approved set. The
  existing Settings authority (``OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE``)
  supplies the operator decision; this module records which policy version
  that decision authorized. Prior usage, a connected flag, or a seeded profile
  is never historical consent.
* Admission freezes model ID, route/materializer, catalog/pricing/data-use
  evidence, and selection-policy version with the attempt through existing
  immutable references. Discovery may refresh without rewriting active inputs;
  callers recheck immediately before a new model send or renewed attempt when
  external eligibility expires or changes.
* Provider/host capacity saturation is a wait state, never evidence that a
  qualified model became ineligible and never authorization for a paid or
  different-data-use fallback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

FREE_ROUTE_POLICY_VERSION = "free-route-eligibility.v1"
FREE_ROUTE_MATERIALIZER_REF = "none@1"
FREE_ROUTE_PROVIDER_ID = "opencode"
NO_ELIGIBLE_FREE_MODEL = "no_eligible_free_model"

# Cost dimensions every candidate must account for. A dimension absent from a
# candidate's pricing map is unknown unless the candidate explicitly declares
# it inapplicable via ``inapplicable_dimensions``.
COST_DIMENSIONS = (
    "request",
    "input_token",
    "output_token",
    "cache_read",
    "cache_write",
    "reasoning_token",
    "tool_call",
)

# How long a frozen admission remains authoritative before callers must
# recheck external eligibility immediately before a new model send or a
# renewed attempt. Mirrors the catalog interval family; discovery refreshes
# underneath without rewriting the frozen inputs.
DEFAULT_ELIGIBILITY_TTL = timedelta(hours=6)


def parse_cost_value(value: Any) -> tuple[bool, bool]:
    """Parse one cost field into ``(known, is_free)``.

    Unknown/invalid -- missing, null, empty, unparsable, negative,
    non-finite (NaN/inf) -- returns ``(False, False)``. Unknown is never
    zero: callers must treat it as ineligible, not free.
    """

    if value is None:
        return (False, False)
    if isinstance(value, bool):
        return (False, False)
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or number < 0:
            return (False, False)
        return (True, number == 0.0)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return (False, False)
        try:
            number = float(text)
        except ValueError:
            return (False, False)
        if not math.isfinite(number) or number < 0:
            return (False, False)
        return (True, number == 0.0)
    return (False, False)


def is_exact_qualified_match(selected: str, candidate: str) -> bool:
    """Return whether ``selected`` names ``candidate`` for execution.

    Exact qualified-ID equality after surrounding-whitespace stripping only.
    Case, punctuation, provider-prefix, and alias normalization must never
    select execution; those aids are display/historical-loading only.
    """

    return bool(selected and candidate) and selected.strip() == candidate.strip()


@dataclass(frozen=True, slots=True)
class EligibilityReasons:
    """Separate availability / pricing / capability / privacy verdicts."""

    availability: str | None = None
    pricing: str | None = None
    capability: str | None = None
    privacy: str | None = None

    def as_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.availability:
            out["availability"] = self.availability
        if self.pricing:
            out["pricing"] = self.pricing
        if self.capability:
            out["capability"] = self.capability
        if self.privacy:
            out["privacy"] = self.privacy
        return out


@dataclass(frozen=True, slots=True)
class FreeModelEligibility:
    """One candidate's qualification verdict with bound evidence."""

    qualified_id: str
    eligible: bool
    reasons: EligibilityReasons = field(default_factory=EligibilityReasons)
    evidence_ref: str = ""
    policy_version: str = FREE_ROUTE_POLICY_VERSION
    decided_at: str = ""

    def blocked_reason(self) -> str:
        if self.eligible:
            return ""
        parts = [f"{key}: {value}" for key, value in self.reasons.as_dict().items()]
        return "; ".join(parts) or "ineligible"


def assess_free_model_candidate(
    *,
    candidate: Mapping[str, Any],
    catalog_ids: set[str],
    required_tools: tuple[str, ...] = ("serve",),
    required_modalities: tuple[str, ...] = (),
    requested_effort: str | None = None,
    data_use_accepted: bool = False,
    accepted_data_use_policy_versions: set[str] | frozenset[str] | tuple[str, ...] = (),
    data_use_policy_version: str = FREE_ROUTE_POLICY_VERSION,
    evidence_ref: str = "",
    now: datetime | None = None,
) -> FreeModelEligibility:
    """Qualify one exact-catalog candidate for the credentialless route."""

    qualified_id = str(candidate.get("qualifiedId") or "").strip()
    decided = (now or datetime.now(UTC)).isoformat()
    reasons = EligibilityReasons()

    if not qualified_id or "/" not in qualified_id:
        return FreeModelEligibility(
            qualified_id=qualified_id,
            eligible=False,
            reasons=EligibilityReasons(availability="candidate lacks a canonical qualified ID"),
            evidence_ref=evidence_ref,
            decided_at=decided,
        )
    provider_route = qualified_id.split("/", 1)[0]
    if provider_route != FREE_ROUTE_PROVIDER_ID:
        return FreeModelEligibility(
            qualified_id=qualified_id,
            eligible=False,
            reasons=EligibilityReasons(
                availability=(
                    f"route {provider_route!r} is not the credentialless "
                    f"{FREE_ROUTE_PROVIDER_ID!r} route"
                )
            ),
            evidence_ref=evidence_ref,
            decided_at=decided,
        )
    if qualified_id not in catalog_ids:
        return FreeModelEligibility(
            qualified_id=qualified_id,
            eligible=False,
            reasons=EligibilityReasons(
                availability="model absent from the exact runtime/provider catalog"
            ),
            evidence_ref=evidence_ref,
            decided_at=decided,
        )

    # --- pricing / subscription gate (req2) ---
    pricing = candidate.get("pricing")
    pricing_map = pricing if isinstance(pricing, Mapping) else {}
    inapplicable = candidate.get("inapplicable_dimensions")
    inapplicable_set = (
        {str(item) for item in inapplicable} if isinstance(inapplicable, (list, tuple, set)) else set()
    )
    unknown_dimensions: list[str] = []
    nonzero_dimensions: list[str] = []
    for dimension in COST_DIMENSIONS:
        if dimension in inapplicable_set:
            continue
        if dimension not in pricing_map:
            unknown_dimensions.append(dimension)
            continue
        known, is_free = parse_cost_value(pricing_map.get(dimension))
        if not known:
            unknown_dimensions.append(dimension)
        elif not is_free:
            nonzero_dimensions.append(dimension)
    if unknown_dimensions:
        reasons = EligibilityReasons(
            availability=reasons.availability,
            pricing=(
                "unproven cost dimensions (treated as unknown, not zero): "
                + ", ".join(sorted(unknown_dimensions))
            ),
            capability=reasons.capability,
            privacy=reasons.privacy,
        )
    elif nonzero_dimensions:
        reasons = EligibilityReasons(
            availability=reasons.availability,
            pricing=(
                "non-zero charges on: " + ", ".join(sorted(nonzero_dimensions))
            ),
            capability=reasons.capability,
            privacy=reasons.privacy,
        )
    # A zero price behind a subscription/key prerequisite is not
    # credentialless-eligible even when every dimension parses as zero.
    prerequisite = str(candidate.get("subscription_or_key_prerequisite") or "").strip()
    if prerequisite and prerequisite.lower() not in {"none", "false", "no"}:
        reasons = EligibilityReasons(
            availability=reasons.availability,
            pricing=f"requires subscription/key prerequisite: {prerequisite}",
            capability=reasons.capability,
            privacy=reasons.privacy,
        )

    # --- capability gate (required tools/modalities + supported effort) ---
    tools = candidate.get("required_tools", candidate.get("tools"))
    tool_set = {str(item) for item in tools} if isinstance(tools, (list, tuple, set)) else set()
    missing_tools = [tool for tool in required_tools if tool not in tool_set]
    modalities = candidate.get("modalities")
    modality_set = (
        {str(item) for item in modalities} if isinstance(modalities, (list, tuple, set)) else set()
    )
    missing_modalities = [m for m in required_modalities if m not in modality_set]
    supported_efforts = candidate.get("supported_efforts", candidate.get("efforts"))
    effort_problem: str | None = None
    if requested_effort is not None and isinstance(supported_efforts, (list, tuple, set)):
        lowered = {str(item).lower() for item in supported_efforts}
        if requested_effort.strip().lower() not in lowered:
            effort_problem = (
                f"effort {requested_effort!r} not supported by {qualified_id} "
                f"(supports: {sorted(lowered) or 'none stated'})"
            )
    if missing_tools or missing_modalities or effort_problem:
        details: list[str] = []
        if missing_tools:
            details.append("missing tools: " + ", ".join(sorted(missing_tools)))
        if missing_modalities:
            details.append("missing modalities: " + ", ".join(sorted(missing_modalities)))
        if effort_problem:
            details.append(effort_problem)
        reasons = EligibilityReasons(
            availability=reasons.availability,
            pricing=reasons.pricing,
            capability="; ".join(details),
            privacy=reasons.privacy,
        )

    # --- permitted data-use policy gate (req4) ---
    data_use = candidate.get("data_use")
    data_use_map = data_use if isinstance(data_use, Mapping) else {}
    requires_consent = bool(
        data_use_map.get("contributor_training")
        or data_use_map.get("requires_consent")
        or data_use_map.get("changed_terms_require_approval")
    )
    accepted_versions = set(accepted_data_use_policy_versions or set())
    if requires_consent and (
        not data_use_accepted or data_use_policy_version not in accepted_versions
    ):
        terms = str(data_use_map.get("terms_version") or data_use_policy_version)
        reasons = EligibilityReasons(
            availability=reasons.availability,
            pricing=reasons.pricing,
            capability=reasons.capability,
            privacy=(
                "contributor/training data-use terms "
                f"(version {terms}) require an explicit recorded operator "
                "decision for that policy/version; prior usage, a connected "
                "flag, or a seeded profile is not consent"
            ),
        )

    eligible = not reasons.as_dict()
    return FreeModelEligibility(
        qualified_id=qualified_id,
        eligible=eligible,
        reasons=reasons,
        evidence_ref=evidence_ref,
        policy_version=FREE_ROUTE_POLICY_VERSION,
        decided_at=decided,
    )


@dataclass(frozen=True, slots=True)
class FrozenFreeModelSelection:
    """Immutable admission snapshot frozen with the attempt (req5)."""

    qualified_id: str
    materializer_ref: str = FREE_ROUTE_MATERIALIZER_REF
    catalog_evidence_ref: str = ""
    pricing_evidence_ref: str = ""
    data_use_evidence_ref: str = ""
    policy_version: str = FREE_ROUTE_POLICY_VERSION
    decided_at: str = ""
    ttl: timedelta = DEFAULT_ELIGIBILITY_TTL

    def needs_recheck(self, *, now: datetime | None = None) -> bool:
        """Report whether external eligibility must be rechecked now.

        The authoritative recheck runs immediately before a new model send
        or a renewed attempt when external eligibility expires or changes.
        Discovery refreshes underneath without rewriting these frozen inputs;
        an expired snapshot fails safely (new explicitly authorized attempt
        preserving saved work) rather than substituting provider/model
        inside a billed request.
        """

        if not self.decided_at:
            return True
        try:
            decided = datetime.fromisoformat(self.decided_at)
        except ValueError:
            return True
        if decided.tzinfo is None:
            decided = decided.replace(tzinfo=UTC)
        return (now or datetime.now(UTC)) - decided > self.ttl


def select_eligible_free_model(
    *,
    candidates: list[Mapping[str, Any]],
    catalog_ids: set[str],
    pinned_qualified_id: str | None = None,
    allow_automatic: bool = False,
    **assess_kwargs: Any,
) -> FreeModelEligibility:
    """Select the credentialless model for execution (req1/req3/req4).

    An explicit user Profile/model/effort pin is preserved: only that exact
    qualified ID is considered, and its unavailability or ineligibility is an
    actionable ``no_eligible_free_model`` error, never a silent substitute.
    Automatic eligible-model resolution runs only where the existing default
    policy authorizes it (``allow_automatic=True``); otherwise a small
    approved set is ranked deterministically without speculative quality
    ordering.
    """

    if pinned_qualified_id:
        pinned = pinned_qualified_id.strip()
        match = next(
            (
                candidate
                for candidate in candidates
                if is_exact_qualified_match(str(candidate.get("qualifiedId") or ""), pinned)
            ),
            None,
        )
        if match is None:
            return FreeModelEligibility(
                qualified_id=pinned,
                eligible=False,
                reasons=EligibilityReasons(
                    availability=(
                        f"pinned model {pinned!r} is unavailable: no exact "
                        "qualified-ID match in the current catalog candidates; "
                        "select a valid alternative rather than substituting"
                    )
                ),
            )
        verdict = assess_free_model_candidate(
            candidate=match, catalog_ids=catalog_ids, **assess_kwargs
        )
        if verdict.eligible:
            return verdict
        return FreeModelEligibility(
            qualified_id=verdict.qualified_id,
            eligible=False,
            reasons=verdict.reasons,
            evidence_ref=verdict.evidence_ref,
            policy_version=verdict.policy_version,
            decided_at=verdict.decided_at,
        )
    if not allow_automatic:
        return FreeModelEligibility(
            qualified_id="",
            eligible=False,
            reasons=EligibilityReasons(
                availability=(
                    "automatic eligible-model resolution is not authorized by "
                    "the current default policy; an explicit Profile/model "
                    "selection or an operator default choice is required"
                )
            ),
        )
    # Deterministic order: exact qualified-ID sort, no speculative ranking.
    for candidate in sorted(candidates, key=lambda item: str(item.get("qualifiedId") or "")):
        verdict = assess_free_model_candidate(
            candidate=candidate, catalog_ids=catalog_ids, **assess_kwargs
        )
        if verdict.eligible:
            return verdict
    first_pricing = next(
        (
            assess_free_model_candidate(candidate=item, catalog_ids=catalog_ids, **assess_kwargs)
            for item in sorted(candidates, key=lambda item: str(item.get("qualifiedId") or ""))
        ),
        None,
    )
    if first_pricing is not None and first_pricing.reasons.as_dict():
        return first_pricing
    return FreeModelEligibility(
        qualified_id="",
        eligible=False,
        reasons=EligibilityReasons(
            availability="no candidate models observed in the exact catalog"
        ),
    )


__all__ = [
    "COST_DIMENSIONS",
    "DEFAULT_ELIGIBILITY_TTL",
    "FREE_ROUTE_MATERIALIZER_REF",
    "FREE_ROUTE_POLICY_VERSION",
    "FREE_ROUTE_PROVIDER_ID",
    "NO_ELIGIBLE_FREE_MODEL",
    "EligibilityReasons",
    "FreeModelEligibility",
    "FrozenFreeModelSelection",
    "assess_free_model_candidate",
    "is_exact_qualified_match",
    "parse_cost_value",
    "select_eligible_free_model",
]
