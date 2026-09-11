"""Credentialless free-model eligibility for the existing OpenCode Zen route.

MoonLadderStudios/MoonMind#4021

This module is the hermetic authority for deciding whether an exact
``opencode/<model>`` catalog entry is genuinely eligible as the
credentialless default. Catalog presence alone never qualifies a model;
eligibility additionally requires explicit pricing evidence (every relevant
charge dimension is zero or explicitly inapplicable) and an explicit
per-policy-version data-use authorization.

Only key names, model IDs, and evidence digests flow through here. Secret
values are never accepted, recorded, or emitted.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

FREE_PROFILE_ID = "opencode-zen-free"
FREE_PROVIDER_ID = "opencode"
FREE_MATERIALIZER_REF = "none@1"
FREE_ROUTE_PREFIX = "opencode/"
SELECTION_POLICY_VERSION = "free-model-selection.v1"

NO_ELIGIBLE_FREE_MODEL_CODE = "no_eligible_free_model"

# Every charge dimension the eligibility check understands. A pricing payload
# must speak about each of these or explicitly mark it inapplicable; an
# omitted dimension is unknown, never free.
PRICING_DIMENSIONS: tuple[str, ...] = (
    "request",
    "input_token",
    "output_token",
    "cache_read",
    "cache_write",
    "reasoning",
    "tool",
)

# Canonical tool/capability gate for execution: a free candidate must carry
# the tools/modalities the runtime needs. Kept small and explicit so tests
# can exercise missing-capability rejection without live catalogs.
REQUIRED_TOOLS: tuple[str, ...] = ("read", "write", "shell")


def parse_cost_value(raw: Any) -> float | None:
    """Parse one cost field; return None when unknown/invalid.

    Missing, null, unparsable, negative, or non-finite values are
    unknown/invalid, never zero. Only a finite value >= 0 parses.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    try:
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            value = float(text)
        else:
            value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if value < 0:
        return None
    return value


def _pricing_entry_costs(entry: Any) -> float | None:
    """Unwrap one pricing dimension entry to a parsed cost or None."""
    if isinstance(entry, Mapping):
        # Explicit inapplicable marking is handled by the caller; here an
        # entry shaped as {"value": ...} unwraps, anything else is unknown.
        if "value" in entry:
            return parse_cost_value(entry.get("value"))
        return None
    return parse_cost_value(entry)


@dataclass(frozen=True, slots=True)
class PricingVerdict:
    eligible: bool
    reason: str
    unknown_dimensions: tuple[str, ...] = ()
    nonzero_dimensions: tuple[str, ...] = ()


def evaluate_pricing(
    pricing: Mapping[str, Any] | None,
    *,
    inapplicable: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
    requires_subscription_or_key: bool = False,
) -> PricingVerdict:
    """Decide whether trusted pricing evidence proves zero cost.

    - Every dimension in PRICING_DIMENSIONS must either parse to exactly 0.0
      or be explicitly listed in ``inapplicable``.
    - Omitted dimensions not marked inapplicable are unknown and block.
    - ``requires_subscription_or_key`` models a subscription/key prerequisite:
      a zero price behind such a prerequisite is not credentialless-eligible.
    """
    inapplicable_set = frozenset(inapplicable)
    unknown: list[str] = []
    nonzero: list[str] = []
    source = pricing or {}
    for dim in PRICING_DIMENSIONS:
        if dim in inapplicable_set:
            continue
        if dim not in source:
            unknown.append(dim)
            continue
        cost = _pricing_entry_costs(source.get(dim))
        if cost is None:
            unknown.append(dim)
        elif cost != 0.0:
            nonzero.append(dim)
    if requires_subscription_or_key:
        return PricingVerdict(
            eligible=False,
            reason="subscription_or_key_required",
            unknown_dimensions=tuple(unknown),
            nonzero_dimensions=tuple(nonzero),
        )
    if unknown:
        return PricingVerdict(
            eligible=False,
            reason="unknown_pricing:" + ",".join(unknown),
            unknown_dimensions=tuple(unknown),
            nonzero_dimensions=tuple(nonzero),
        )
    if nonzero:
        return PricingVerdict(
            eligible=False,
            reason="nonzero_pricing:" + ",".join(nonzero),
            unknown_dimensions=tuple(unknown),
            nonzero_dimensions=tuple(nonzero),
        )
    return PricingVerdict(eligible=True, reason="zero_cost")


@dataclass(frozen=True, slots=True)
class DataUseDecision:
    """Recorded authorization for one data-use policy version."""

    policy_version: str
    accepted: bool
    accepted_at: str = ""
    accepted_by: str = ""


def evaluate_data_use(
    *,
    terms_version: str,
    terms_require_approval: bool,
    decision: DataUseDecision | None,
) -> tuple[bool, str]:
    """Apply the permitted data-use policy before ranking.

    Existing usage, a connected flag, or an automatically seeded profile is
    never consent. Only an explicit accepted decision for the exact
    ``terms_version`` authorizes. Returns (eligible, reason).
    """
    if not terms_require_approval:
        return True, "no_approval_required"
    if decision is None:
        return False, "privacy:unaccepted_terms:" + terms_version
    if decision.policy_version != terms_version or not decision.accepted:
        return False, "privacy:unaccepted_terms:" + terms_version
    return True, "authorized:" + terms_version


@dataclass(frozen=True, slots=True)
class EligibilityInput:
    qualified_id: str
    provider_id: str = FREE_PROVIDER_ID
    materializer_ref: str = FREE_MATERIALIZER_REF
    in_catalog: bool = False
    pricing: Mapping[str, Any] | None = None
    inapplicable_dimensions: tuple[str, ...] = ()
    requires_subscription_or_key: bool = False
    capabilities: tuple[str, ...] = ()
    supported_efforts: tuple[str, ...] = ()
    requested_effort: str = "xhigh"
    terms_version: str = ""
    terms_require_approval: bool = False
    data_use_decision: DataUseDecision | None = None


@dataclass(frozen=True, slots=True)
class EligibilityVerdict:
    eligible: bool
    reasons: dict[str, str] = field(default_factory=dict)


def evaluate_eligibility(candidate: EligibilityInput) -> EligibilityVerdict:
    """Evaluate one exact catalog candidate across the four reason axes."""
    reasons: dict[str, str] = {}
    if not candidate.in_catalog:
        reasons["availability"] = "not_in_catalog"
    if (
        candidate.provider_id != FREE_PROVIDER_ID
        or not candidate.qualified_id.startswith(FREE_ROUTE_PREFIX)
    ):
        reasons["availability"] = "wrong_route:" + candidate.qualified_id
    if candidate.materializer_ref != FREE_MATERIALIZER_REF:
        reasons["availability"] = "wrong_materializer:" + candidate.materializer_ref
    pricing = evaluate_pricing(
        candidate.pricing,
        inapplicable=frozenset(candidate.inapplicable_dimensions),
        requires_subscription_or_key=candidate.requires_subscription_or_key,
    )
    if not pricing.eligible:
        reasons["pricing"] = pricing.reason
    missing_tools = [t for t in REQUIRED_TOOLS if t not in set(candidate.capabilities)]
    if missing_tools:
        reasons["capability"] = "missing_tools:" + ",".join(missing_tools)
    elif (
        candidate.supported_efforts
        and candidate.requested_effort.strip().lower()
        not in {e.lower() for e in candidate.supported_efforts}
    ):
        reasons["capability"] = "unsupported_effort:" + candidate.requested_effort
    data_ok, data_reason = evaluate_data_use(
        terms_version=candidate.terms_version,
        terms_require_approval=candidate.terms_require_approval,
        decision=candidate.data_use_decision,
    )
    if not data_ok:
        reasons["privacy"] = data_reason
    return EligibilityVerdict(eligible=not reasons, reasons=reasons)


def _evidence_digest(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenFreeModelAttempt:
    """Immutable evidence bundle frozen with one admitted attempt."""

    model_id: str
    route: str = FREE_ROUTE_PREFIX.rstrip("/")
    materializer_ref: str = FREE_MATERIALIZER_REF
    catalog_digest: str = ""
    pricing_digest: str = ""
    data_use_version: str = ""
    selection_policy_version: str = SELECTION_POLICY_VERSION
    observed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "modelId": self.model_id,
            "route": self.route,
            "materializerRef": self.materializer_ref,
            "catalogDigest": self.catalog_digest,
            "pricingDigest": self.pricing_digest,
            "dataUseVersion": self.data_use_version,
            "selectionPolicyVersion": self.selection_policy_version,
            "observedAt": self.observed_at,
        }


def freeze_attempt(
    *,
    qualified_id: str,
    catalog: Any,
    pricing: Mapping[str, Any] | None,
    data_use_version: str,
    observed_at: str | None = None,
) -> FrozenFreeModelAttempt:
    """Freeze model/route/evidence/policy-version with the admitted attempt."""
    catalog_payload = catalog if isinstance(catalog, Mapping) else {"catalog": catalog}
    pricing_payload = dict(pricing or {})
    return FrozenFreeModelAttempt(
        model_id=qualified_id,
        catalog_digest=_evidence_digest(catalog_payload if isinstance(catalog_payload, Mapping) else {"v": str(catalog_payload)}),
        pricing_digest=_evidence_digest(pricing_payload),
        data_use_version=data_use_version,
        observed_at=observed_at or datetime.now(UTC).isoformat(),
    )


def format_no_eligible_free_model(reasons: Mapping[str, str]) -> dict[str, Any]:
    """Expose the no_eligible_free_model signal with separate reason axes."""
    ordered = {k: reasons[k] for k in ("availability", "pricing", "capability", "privacy") if k in reasons}
    ordered.update({k: v for k, v in reasons.items() if k not in ordered})
    return {"code": NO_ELIGIBLE_FREE_MODEL_CODE, "reasons": dict(ordered)}


class FreeModelUnavailableError(ValueError):
    """An explicit pinned free model cannot be satisfied; never substituted."""

    code = "free_model_unavailable"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NoEligibleFreeModelError(ValueError):
    """Automatic selection found no eligible free model."""

    code = NO_ELIGIBLE_FREE_MODEL_CODE

    def __init__(self, reasons: Mapping[str, str]) -> None:
        payload = format_no_eligible_free_model(reasons)
        super().__init__(f"no eligible free model ({payload['reasons']})")
        self.message = str(self)
        self.details = payload


def resolve_free_default_model(
    *,
    explicit_pin: str | None,
    catalog_ids: list[str],
    candidates: Mapping[str, EligibilityInput],
    default_policy_authorizes_auto: bool,
) -> tuple[str, FrozenFreeModelAttempt]:
    """Map seed/default/catalog/materializer into one first-result path.

    - An explicit user pin is preserved: when it is unavailable or
      ineligible the call raises instead of silently substituting.
    - Automatic eligible-model resolution runs only where the existing
      default policy authorizes it; otherwise it raises.
    - Discovery inputs never rewrite an active attempt: the returned frozen
      bundle must be persisted with the attempt by the caller.
    """
    pinned = (explicit_pin or "").strip()
    if pinned:
        candidate = candidates.get(pinned)
        if candidate is None or pinned not in catalog_ids:
            raise FreeModelUnavailableError(
                f"Requested free model {pinned!r} is unavailable for the "
                f"credentialless {FREE_PROFILE_ID} route. Available catalog: "
                f"{', '.join(catalog_ids[:5]) or 'none'}",
                requested=pinned,
            )
        verdict = evaluate_eligibility(candidate)
        if not verdict.eligible:
            raise NoEligibleFreeModelError(verdict.reasons)
        frozen = freeze_attempt(
            qualified_id=pinned,
            catalog={"ids": sorted(catalog_ids)},
            pricing=dict(candidate.pricing or {}),
            data_use_version=candidate.terms_version,
        )
        return pinned, frozen
    if not default_policy_authorizes_auto:
        raise FreeModelUnavailableError(
            "No explicit free model is pinned and automatic eligible-model "
            "resolution is not authorized by the default policy.",
            requested="",
        )
    # Deterministic first-result order over the observed catalog.
    last_reasons: dict[str, str] = {"availability": "empty_catalog"}
    for qualified_id in catalog_ids:
        candidate = candidates.get(qualified_id)
        if candidate is None:
            last_reasons = {"availability": f"no_evidence:{qualified_id}"}
            continue
        verdict = evaluate_eligibility(candidate)
        if verdict.eligible:
            frozen = freeze_attempt(
                qualified_id=qualified_id,
                catalog={"ids": sorted(catalog_ids)},
                pricing=dict(candidate.pricing or {}),
                data_use_version=candidate.terms_version,
            )
            return qualified_id, frozen
        last_reasons = verdict.reasons
    raise NoEligibleFreeModelError(last_reasons)


__all__ = [
    "FREE_MATERIALIZER_REF",
    "FREE_PROFILE_ID",
    "FREE_PROVIDER_ID",
    "FREE_ROUTE_PREFIX",
    "NO_ELIGIBLE_FREE_MODEL_CODE",
    "PRICING_DIMENSIONS",
    "REQUIRED_TOOLS",
    "SELECTION_POLICY_VERSION",
    "DataUseDecision",
    "EligibilityInput",
    "EligibilityVerdict",
    "FreeModelUnavailableError",
    "FrozenFreeModelAttempt",
    "NoEligibleFreeModelError",
    "PricingVerdict",
    "evaluate_data_use",
    "evaluate_eligibility",
    "evaluate_pricing",
    "format_no_eligible_free_model",
    "freeze_attempt",
    "parse_cost_value",
    "resolve_free_default_model",
]
