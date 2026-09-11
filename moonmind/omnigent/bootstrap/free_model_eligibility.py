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
# Terms version the current Settings/policy authority records acceptance for.
# Bump only with an explicit operator-facing policy change; a version change
# blocks new admission until the operator re-accepts, and never rewrites an
# active attempt (MoonLadderStudios/MoonMind#4021 req-4/req-5).
ZEN_FREE_TERMS_VERSION = "zen-terms.v3"

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


def zen_free_data_use_decision_from_settings(
    env: Mapping[str, Any] | None = None,
) -> DataUseDecision | None:
    """Build the Zen free-route DataUseDecision from the existing authority.

    MoonLadderStudios/MoonMind#4021 req-4: reuse the existing Settings/policy
    authority (``OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE`` via
    :func:`moonmind.omnigent.settings.opencode_contributor_data_use_accepted`),
    not a marketplace or a new consent subsystem. Only an explicit accepted
    decision for the exact :data:`ZEN_FREE_TERMS_VERSION` authorizes; existing
    usage, a connected flag, or an automatically seeded profile is never
    consent and therefore never consulted here.
    """
    from moonmind.omnigent.settings import opencode_contributor_data_use_accepted

    try:
        accepted = bool(opencode_contributor_data_use_accepted(env=env))
    except ValueError:
        return None
    if not accepted:
        return None
    return DataUseDecision(
        policy_version=ZEN_FREE_TERMS_VERSION,
        accepted=True,
        accepted_by="operator-settings",
    )


def zen_free_route_blocked_reason(
    provider_id: str | None,
    *,
    env: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the blocking privacy reason for the credentialless free route.

    MoonLadderStudios/MoonMind#4021 req-4: production admission paths call
    this with the selected provider ID. It reuses the existing Settings/policy
    authority (:func:`zen_free_data_use_decision_from_settings`), never a
    marketplace or a new consent subsystem. Returns ``None`` when the route
    is not the credentialless free route or when the recorded per-version
    authorization exists, so default-accepted deployments observe no change.
    Only the evaluated privacy axis is ever reported; pricing/capability
    axes need the trusted feed that has no production source yet and are
    never fabricated here.
    """
    if str(provider_id or "").strip() != FREE_PROVIDER_ID:
        return None
    if zen_free_data_use_decision_from_settings(env=env) is not None:
        return None
    return "privacy:unaccepted_terms:" + ZEN_FREE_TERMS_VERSION


def build_eligibility_input(
    *,
    qualified_id: str,
    catalog_ids: list[str],
    pricing: Mapping[str, Any] | None,
    inapplicable_dimensions: tuple[str, ...] = (),
    requires_subscription_or_key: bool = False,
    capabilities: tuple[str, ...] = (),
    supported_efforts: tuple[str, ...] = (),
    requested_effort: str = "xhigh",
    terms_version: str = ZEN_FREE_TERMS_VERSION,
    terms_require_approval: bool = True,
    data_use_decision: DataUseDecision | None = None,
) -> EligibilityInput:
    """Build an EligibilityInput from the live exact-host catalog boundary.

    ``catalog_ids`` is the exact observed catalog (qualified IDs). Presence in
    it sets ``in_catalog``; nothing else (name matching, list-request success,
    installed binaries) qualifies. Callers supply trusted pricing/terms
    evidence alongside; omitted pricing dimensions stay unknown, never free.
    """
    wanted = qualified_id.strip()
    return EligibilityInput(
        qualified_id=wanted,
        provider_id=wanted.split("/", 1)[0] if "/" in wanted else FREE_PROVIDER_ID,
        materializer_ref=FREE_MATERIALIZER_REF,
        in_catalog=wanted in catalog_ids,
        pricing=pricing,
        inapplicable_dimensions=inapplicable_dimensions,
        requires_subscription_or_key=requires_subscription_or_key,
        capabilities=capabilities,
        supported_efforts=supported_efforts,
        requested_effort=requested_effort,
        terms_version=terms_version,
        terms_require_approval=terms_require_approval,
        data_use_decision=data_use_decision,
    )


def rank_approved_candidates(
    candidates: Mapping[str, EligibilityInput],
    *,
    catalog_order: list[str],
) -> tuple[str | None, dict[str, str]]:
    """Rank a small approved set only after the data-use policy authorizes it.

    Each candidate already carries its own exact-terms decision; a candidate
    whose terms are unaccepted for its exact version is skipped with its
    privacy reason preserved. Returns (selected_id, blocked_reasons). No
    speculative quality ranking: deterministic catalog order decides.
    """
    blocked: dict[str, str] = {}
    for qualified_id in catalog_order:
        candidate = candidates.get(qualified_id)
        if candidate is None:
            blocked = {"availability": f"no_evidence:{qualified_id}"}
            continue
        verdict = evaluate_eligibility(candidate)
        if verdict.eligible:
            return qualified_id, {}
        blocked = verdict.reasons
    return None, blocked


def attach_frozen_attempt_to_resolved(
    resolved: Any,
    frozen: FrozenFreeModelAttempt,
) -> Any:
    """Persist the frozen bundle with the admitted attempt (no rewrite).

    Returns a copy of the given ``BootstrapResolved`` carrying
    ``freeModelAttempt``. Discovery refresh creates a new frozen bundle for a
    new attempt; it never mutates the active attempt in place.
    """
    payload = frozen.as_dict()
    if hasattr(resolved, "model_copy"):
        return resolved.model_copy(update={"free_model_attempt": payload})
    if isinstance(resolved, dict):
        updated = dict(resolved)
        updated["freeModelAttempt"] = payload
        return updated
    raise TypeError("resolved must be a BootstrapResolved or mapping")


def frozen_attempt_from_resolved(resolved: Any) -> FrozenFreeModelAttempt | None:
    """Recover the frozen bundle persisted with an attempt, if any."""
    payload: Any = None
    if hasattr(resolved, "free_model_attempt"):
        payload = getattr(resolved, "free_model_attempt")
    elif isinstance(resolved, Mapping):
        payload = resolved.get("freeModelAttempt", resolved.get("free_model_attempt"))
    if not isinstance(payload, Mapping):
        return None
    try:
        return FrozenFreeModelAttempt(
            model_id=str(payload.get("modelId") or ""),
            route=str(payload.get("route") or FREE_ROUTE_PREFIX.rstrip("/")),
            materializer_ref=str(payload.get("materializerRef") or FREE_MATERIALIZER_REF),
            catalog_digest=str(payload.get("catalogDigest") or ""),
            pricing_digest=str(payload.get("pricingDigest") or ""),
            data_use_version=str(payload.get("dataUseVersion") or ""),
            selection_policy_version=str(
                payload.get("selectionPolicyVersion") or SELECTION_POLICY_VERSION
            ),
            observed_at=str(payload.get("observedAt") or ""),
        )
    except (TypeError, ValueError):
        return None


def recheck_frozen_attempt(
    frozen: FrozenFreeModelAttempt,
    *,
    catalog: Any,
    pricing: Mapping[str, Any] | None,
    data_use_version: str,
    selection_policy_version: str = SELECTION_POLICY_VERSION,
) -> tuple[bool, str]:
    """Authoritative recheck before a new model send or renewed attempt.

    Compares the live catalog/pricing/data-use/policy evidence against the
    frozen bundle without rewriting active inputs. Returns (current, reason):
    current True means the frozen attempt is still authoritative; False names
    the expired/changed axis so the caller can fail safely or start an
    explicitly authorized new attempt preserving saved work. Never swaps
    provider/model inside a billed request.
    """
    catalog_payload = catalog if isinstance(catalog, Mapping) else {"catalog": catalog}
    pricing_payload = dict(pricing or {})
    if frozen.selection_policy_version != selection_policy_version:
        return False, "selection_policy_changed:" + selection_policy_version
    if frozen.data_use_version != data_use_version:
        return False, "privacy:terms_changed:" + data_use_version
    if frozen.catalog_digest != _evidence_digest(
        catalog_payload if isinstance(catalog_payload, Mapping) else {"v": str(catalog_payload)}
    ):
        return False, "availability:catalog_changed"
    if frozen.pricing_digest != _evidence_digest(pricing_payload):
        return False, "pricing:evidence_changed"
    return True, "current"


def build_live_qualification_record(
    *,
    qualified_id: str,
    host_image_ref: str,
    runtime_pack_ref: str,
    materializer_ref: str = FREE_MATERIALIZER_REF,
    pricing_evidence: Mapping[str, Any] | None = None,
    data_use_version: str = ZEN_FREE_TERMS_VERSION,
    data_use_authorized: bool = False,
    catalog_max_age_hours: float | None = None,
    probe_timeout_seconds: float = 60.0,
    max_probe_attempts: int = 1,
    blocked_cases: tuple[str, ...] = (),
    unexecuted_cases: tuple[str, ...] = (),
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build the bounded, non-sensitive live-qualification record shape.

    MoonLadderStudios/MoonMind#4021 req-7: protected live qualification is
    explicitly authorized, non-sensitive, and budgeted. This helper records
    the exact model/image/runtime/materializer, pricing/privacy evidence
    references, request bounds (lease/probe/budget), and blocked/unexecuted
    cases truthfully. It performs no network or inference calls; hermetic
    tests use it with inline fixtures only.
    """
    return {
        "schemaVersion": "moonmind.free-model-live-qualification/v1",
        "modelId": qualified_id,
        "route": FREE_ROUTE_PREFIX.rstrip("/"),
        "materializerRef": materializer_ref,
        "hostImageRef": host_image_ref,
        "runtimePackRef": runtime_pack_ref,
        "pricingEvidence": dict(pricing_evidence or {}),
        "dataUseVersion": data_use_version,
        "dataUseAuthorized": bool(data_use_authorized),
        "selectionPolicyVersion": SELECTION_POLICY_VERSION,
        "bounds": {
            "catalogMaxAgeHours": catalog_max_age_hours,
            "probeTimeoutSeconds": probe_timeout_seconds,
            "maxProbeAttempts": max_probe_attempts,
            "singleFlight": True,
        },
        "blockedCases": list(blocked_cases),
        "unexecutedCases": list(unexecuted_cases),
        "observedAt": observed_at or datetime.now(UTC).isoformat(),
    }


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
    "ZEN_FREE_TERMS_VERSION",
    "DataUseDecision",
    "EligibilityInput",
    "EligibilityVerdict",
    "FreeModelUnavailableError",
    "FrozenFreeModelAttempt",
    "NoEligibleFreeModelError",
    "PricingVerdict",
    "attach_frozen_attempt_to_resolved",
    "build_eligibility_input",
    "build_live_qualification_record",
    "evaluate_data_use",
    "evaluate_eligibility",
    "evaluate_pricing",
    "format_no_eligible_free_model",
    "freeze_attempt",
    "frozen_attempt_from_resolved",
    "parse_cost_value",
    "rank_approved_candidates",
    "recheck_frozen_attempt",
    "resolve_free_default_model",
    "zen_free_data_use_decision_from_settings",
    "zen_free_route_blocked_reason",
]
