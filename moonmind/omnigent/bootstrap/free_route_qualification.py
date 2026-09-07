"""Qualify the existing credentialless OpenCode route with explicit policy.

MoonLadderStudios/MoonMind#4021.

This boundary turns the seeded ``opencode-zen-free`` / ``none@1`` identity
into an *eligible* default only when exact catalog, pricing, data-use, and
capability evidence says so. It introduces no new profile family, no
marketplace, and no paid fallback: the identities stay
``opencode-zen-free`` and ``none@1``.

What this module owns (pure, hermetic, no live inference):

* a compact eligibility record bound to provenance/time/version
  (req-02);
* strict cost-field handling where missing/null/unparsable/negative/
  non-finite is unknown/invalid, never zero (req-02);
* exact qualified-ID selection where aliases and normalized collisions
  can never choose execution (req-03);
* the permitted data-use policy gate reusing Settings authority
  (req-04);
* the frozen attempt binding plus the authoritative recheck rule and
  the ``no_eligible_free_model`` taxonomy (req-05);
* credentialless isolation helpers including the ``OPENCODE_API_KEY``
  non-rescue rule (req-06);
* bounded validation helpers separating discovery/local probes from
  live inference (req-07);
* the one first-result path that reuses the existing owners
  (seed/default, catalog normalization, model/effort resolution,
  materializer, exact-host admission) without forking them (req-01).

Live provider terms are reverified by callers through the existing
pinned-runtime validation path; this module only records the evidence
those probes return.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

# Existing identities. This issue must not create a new free-model family.
FREE_PROFILE_ID = "opencode-zen-free"
FREE_PROVIDER_ID = "opencode"
FREE_MATERIALIZER_REF = "none@1"
FREE_RUNTIME_ID = "opencode"

# Failure taxonomy for a free external service that can disappear.
NO_ELIGIBLE_FREE_MODEL = "no_eligible_free_model"

# Selection-policy version frozen with each admitted attempt.
FREE_ROUTE_SELECTION_POLICY_VERSION = "free-route-selection.v1"

# Ambient keys that must never enter the credentialless runtime. Extends the
# runtime-pack forbidden list with the deployment key and auth caches that
# would otherwise rescue or contaminate a Zen attempt.
FORBIDDEN_AMBIENT_ENV_FOR_ZEN: tuple[str, ...] = (
    "OPENCODE_AUTH_CONTENT",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_CONTENT",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENCODE_API_KEY",
)

# Inherited OpenCode configuration / plugin / provider overrides that must
# stay out of the credentialless host boundary.
FORBIDDEN_ZEN_CONFIG_KEYS: tuple[str, ...] = (
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_CONTENT",
    "OPENCODE_AUTH_CONTENT",
)

# Pricing dimensions checked for every candidate. Omitted inapplicable
# dimensions must be listed explicitly in ``inapplicable``; they are never
# assumed free.
PRICING_DIMENSIONS: tuple[str, ...] = (
    "request",
    "input_token",
    "output_token",
    "cache",
    "reasoning",
    "tool",
)


@dataclass(frozen=True, slots=True)
class FreeRouteReason:
    """One machine-readable reason an admission decision was made."""

    category: str  # availability | pricing | capability | privacy
    detail: str


@dataclass(frozen=True, slots=True)
class FreeEligibilityRecord:
    """Compact eligibility decision for one exact catalog candidate."""

    qualified_id: str
    provider_route: str
    materializer_ref: str
    supported_efforts: tuple[str, ...]
    required_tools: tuple[str, ...] = ()
    required_modalities: tuple[str, ...] = ()
    eligible: bool = False
    reasons: tuple[FreeRouteReason, ...] = ()
    pricing_source: str = ""
    data_use_source: str = ""
    catalog_version: str = ""
    policy_version: str = FREE_ROUTE_SELECTION_POLICY_VERSION
    observed_at: str = ""

    def blocked_reason(self) -> str:
        if self.eligible:
            return ""
        parts = [f"{r.category}:{r.detail}" for r in self.reasons]
        return "; ".join(parts) or NO_ELIGIBLE_FREE_MODEL


@dataclass(frozen=True, slots=True)
class FrozenFreeAttempt:
    """Immutable binding frozen with an admitted credentialless attempt."""

    model_id: str
    route_ref: str
    materializer_ref: str
    catalog_evidence_ref: str
    pricing_evidence_ref: str
    data_use_evidence_ref: str
    policy_version: str = FREE_ROUTE_SELECTION_POLICY_VERSION
    frozen_at: str = ""
    # External eligibility expires; the recheck below re-verifies it.
    eligibility_expires_at: str | None = None


def parse_cost_value(value: Any) -> float | None:
    """Parse one cost field strictly.

    Missing, null, unparsable, negative, NaN, or infinite values return
    ``None`` (unknown/invalid), never zero. Only a finite value >= 0 is a
    known cost.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if parsed < 0:
        return None
    return parsed


def check_pricing_eligible(
    pricing: Mapping[str, Any] | None,
    *,
    inapplicable: tuple[str, ...] | list[str] = (),
) -> tuple[bool, FreeRouteReason | None]:
    """Check all relevant charges for one candidate.

    Every dimension in :data:`PRICING_DIMENSIONS` must either be an
    explicitly-known zero cost or be named in ``inapplicable``. Anything
    else (missing, unknown, nonzero, subscription/key prerequisite) is
    ineligible. Truthiness or a name containing ``free`` never qualifies.
    """

    inapplicable_set = {str(item) for item in (inapplicable or ())}
    if pricing is None:
        return False, FreeRouteReason(
            category="pricing", detail="pricing evidence is missing"
        )
    if not isinstance(pricing, Mapping):
        return False, FreeRouteReason(
            category="pricing", detail="pricing evidence is unparsable"
        )
    prerequisite = str(pricing.get("subscription_required") or pricing.get("key_required") or "").strip().lower()
    if prerequisite in {"1", "true", "yes", "required"}:
        return False, FreeRouteReason(
            category="pricing", detail="subscription or key prerequisite"
        )
    rates = pricing.get("rates")
    if rates is not None and not isinstance(rates, Mapping):
        return False, FreeRouteReason(
            category="pricing", detail="pricing rates are unparsable"
        )
    for dim in PRICING_DIMENSIONS:
        if dim in inapplicable_set:
            continue
        raw = rates.get(dim) if isinstance(rates, Mapping) else pricing.get(dim)
        if dim not in (rates.keys() if isinstance(rates, Mapping) else pricing.keys()):
            return False, FreeRouteReason(
                category="pricing", detail=f"pricing dimension {dim!r} is unknown"
            )
        known = parse_cost_value(raw)
        if known is None:
            return False, FreeRouteReason(
                category="pricing", detail=f"pricing dimension {dim!r} is unknown/invalid"
            )
        if known != 0:
            return False, FreeRouteReason(
                category="pricing", detail=f"pricing dimension {dim!r} is nonzero"
            )
    unknown_dims = [
        str(item)
        for item in inapplicable_set
        if item not in PRICING_DIMENSIONS
    ]
    if unknown_dims:
        return False, FreeRouteReason(
            category="pricing",
            detail=f"unknown inapplicable dimensions: {sorted(unknown_dims)}",
        )
    return True, None


def evaluate_data_use_policy(
    data_use: Mapping[str, Any] | None,
    *,
    policy_accepted: bool,
    policy_version: str = FREE_ROUTE_SELECTION_POLICY_VERSION,
) -> tuple[bool, FreeRouteReason | None, dict[str, Any]]:
    """Apply the permitted data-use policy before ranking.

    Reuses Settings/policy authority via ``policy_accepted`` (wired to
    ``opencode_contributor_data_use_accepted`` by callers); this module
    never invents a marketplace or consent subsystem. Returns the
    (permitted, reason, authorized-decision-record) triple so a blocked
    default can explain itself for that policy/version.
    """

    record: dict[str, Any] = {
        "policy_version": policy_version,
        "accepted": bool(policy_accepted),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    if not isinstance(data_use, Mapping):
        record["decision"] = "blocked"
        record["reason"] = "data-use terms are unknown"
        return False, FreeRouteReason(category="privacy", detail="data-use terms are unknown"), record
    terms_version = str(data_use.get("terms_version") or "").strip()
    record["terms_version"] = terms_version
    if not terms_version:
        record["decision"] = "blocked"
        record["reason"] = "data-use terms version is unknown"
        return False, FreeRouteReason(category="privacy", detail="data-use terms version is unknown"), record
    training = str(data_use.get("training_use") or data_use.get("contributor_training") or "").strip().lower()
    record["training_use"] = training
    if training in {"1", "true", "yes", "required", "opt-out"} and not policy_accepted:
        record["decision"] = "blocked"
        record["reason"] = f"contributor/training terms {terms_version!r} need approval"
        return (
            False,
            FreeRouteReason(
                category="privacy",
                detail=f"contributor/training terms {terms_version!r} need approval",
            ),
            record,
        )
    changed = bool(data_use.get("terms_changed"))
    if changed and not policy_accepted:
        record["decision"] = "blocked"
        record["reason"] = f"changed data-use terms {terms_version!r} need approval"
        return (
            False,
            FreeRouteReason(
                category="privacy",
                detail=f"changed data-use terms {terms_version!r} need approval",
            ),
            record,
        )
    record["decision"] = "permitted"
    return True, None, record


def qualify_free_candidate(
    *,
    qualified_id: str,
    catalog_entry: Mapping[str, Any] | None,
    pricing: Mapping[str, Any] | None,
    pricing_source: str = "",
    data_use: Mapping[str, Any] | None,
    data_use_source: str = "",
    policy_accepted: bool,
    required_tools: tuple[str, ...] | list[str] = (),
    required_modalities: tuple[str, ...] | list[str] = (),
    supported_efforts: tuple[str, ...] | list[str] | None = None,
    catalog_version: str = "",
    observed_at: str | None = None,
    policy_version: str = FREE_ROUTE_SELECTION_POLICY_VERSION,
) -> FreeEligibilityRecord:
    """Build one compact eligibility record from exact catalog evidence.

    Catalog presence, a name containing ``free``, a successful list
    request, or an installed binary alone never establishes eligibility:
    the candidate must name a canonical qualified ID present in the exact
    runtime catalog, carry zero pricing across every applicable dimension,
    pass the data-use policy, and declare required tools/modalities and
    supported effort values.
    """

    reasons: list[FreeRouteReason] = []
    qid = str(qualified_id or "").strip()
    provider_route, sep, _model = qid.partition("/")
    if not sep or provider_route != FREE_PROVIDER_ID:
        reasons.append(
            FreeRouteReason(category="availability", detail=f"{qid!r} is not a canonical {FREE_PROVIDER_ID}/... ID")
        )
    catalog_models: set[str] = set()
    catalog_supported_efforts: list[str] = []
    if isinstance(catalog_entry, Mapping):
        raw_models = catalog_entry.get("models")
        if isinstance(raw_models, list):
            for item in raw_models:
                if isinstance(item, Mapping):
                    found = str(item.get("qualifiedId") or "").strip()
                    if found:
                        catalog_models.add(found)
                elif isinstance(item, str) and item.strip():
                    catalog_models.add(item.strip())
        raw_efforts = catalog_entry.get("supported_efforts", catalog_entry.get("supportedEfforts"))
        if isinstance(raw_efforts, list):
            catalog_supported_efforts = [str(e).strip().lower() for e in raw_efforts if str(e).strip()]
    if qid and qid not in catalog_models:
        reasons.append(
            FreeRouteReason(category="availability", detail=f"{qid!r} absent from the exact runtime catalog")
        )
    inapplicable = tuple((pricing or {}).get("inapplicable") or ())
    pricing_ok, pricing_reason = check_pricing_eligible(pricing, inapplicable=inapplicable)
    if not pricing_ok and pricing_reason is not None:
        reasons.append(pricing_reason)
    data_ok, data_reason, _decision = evaluate_data_use_policy(
        data_use, policy_accepted=policy_accepted, policy_version=policy_version
    )
    if not data_ok and data_reason is not None:
        reasons.append(data_reason)
    tools = tuple(str(t) for t in (required_tools or ()))
    modalities = tuple(str(m) for m in (required_modalities or ()))
    if isinstance(catalog_entry, Mapping):
        available_tools = {
            str(t) for t in (catalog_entry.get("tools") or []) if str(t).strip()
        }
        available_modalities = {
            str(m) for m in (catalog_entry.get("modalities") or []) if str(m).strip()
        }
        missing_tools = [t for t in tools if t not in available_tools] if tools else []
        missing_modalities = [m for m in modalities if m not in available_modalities] if modalities else []
        if missing_tools:
            reasons.append(
                FreeRouteReason(category="capability", detail=f"missing required tools: {sorted(missing_tools)}")
            )
        if missing_modalities:
            reasons.append(
                FreeRouteReason(category="capability", detail=f"missing required modalities: {sorted(missing_modalities)}")
            )
    declared_efforts = tuple(
        str(e).strip().lower()
        for e in (supported_efforts if supported_efforts is not None else catalog_supported_efforts)
        if str(e).strip()
    )
    if not declared_efforts:
        reasons.append(
            FreeRouteReason(category="capability", detail="supported effort values are unknown")
        )
    return FreeEligibilityRecord(
        qualified_id=qid,
        provider_route=FREE_PROVIDER_ID,
        materializer_ref=FREE_MATERIALIZER_REF,
        supported_efforts=declared_efforts,
        required_tools=tools,
        required_modalities=modalities,
        eligible=not reasons,
        reasons=tuple(reasons),
        pricing_source=str(pricing_source or ""),
        data_use_source=str(data_use_source or ""),
        catalog_version=str(catalog_version or ""),
        policy_version=policy_version,
        observed_at=observed_at or datetime.now(UTC).isoformat(),
    )


def resolve_exact_free_model(
    requested: str,
    *,
    catalog_qualified_ids: list[str],
) -> str:
    """Resolve one requested model to an exact catalog ID for execution.

    Only a byte-exact qualified ID present in the exact catalog selects.
    Friendly labels, punctuation-normalized collisions, and substring
    matches (including a name containing ``free``) never select a
    different model/provider. Raises ``ValueError`` with an actionable
    error instead of silently substituting.
    """

    wanted = str(requested or "").strip()
    if not wanted:
        raise ValueError(f"{NO_ELIGIBLE_FREE_MODEL}: empty model selection")
    catalog = [str(q).strip() for q in (catalog_qualified_ids or []) if str(q).strip()]
    if wanted in catalog:
        provider_route, sep, _model = wanted.partition("/")
        if not sep or provider_route != FREE_PROVIDER_ID:
            raise ValueError(
                f"{NO_ELIGIBLE_FREE_MODEL}: {wanted!r} is not a credentialless "
                f"{FREE_PROVIDER_ID}/... route"
            )
        return wanted
    # Deliberately no normalized/alias/substring fallback here: those paths
    # are display or historical-loading helpers, never execution selection.
    raise ValueError(
        f"{NO_ELIGIBLE_FREE_MODEL}: {wanted!r} is unavailable in the exact "
        "runtime catalog; select an exact observed qualified ID"
    )


def validate_effort_for_model(effort: str, *, supported_efforts: tuple[str, ...] | list[str]) -> str:
    """Validate effort against the selected model's actual supported values."""

    normalized = str(effort or "").strip().lower()
    supported = {str(e).strip().lower() for e in (supported_efforts or ()) if str(e).strip()}
    if not supported:
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: supported effort values are unknown; "
            "refuse to assume a seeded default applies"
        )
    if normalized not in supported:
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: effort {effort!r} is not supported by "
            f"the selected model (supported: {sorted(supported)})"
        )
    return normalized


def rank_approved_free_models(
    records: list[FreeEligibilityRecord],
    *,
    explicit_pin: str | None = None,
    default_policy_authorizes_auto: bool = False,
) -> FreeEligibilityRecord:
    """Rank a small approved set deterministically.

    An explicit user Profile/model/effort pin always wins when it is
    eligible; an unavailable pin raises an actionable error rather than
    silently substituting. Automatic resolution applies only where the
    existing default policy authorizes it. No speculative quality ranking:
    the first eligible record in catalog order wins.
    """

    eligible = [r for r in (records or []) if r.eligible]
    pin = str(explicit_pin or "").strip()
    if pin:
        for record in records or []:
            if record.qualified_id == pin:
                if record.eligible:
                    return record
                raise ValueError(
                    f"{NO_ELIGIBLE_FREE_MODEL}: pinned model {pin!r} is not "
                    f"eligible ({record.blocked_reason()}); select an explicit "
                    "valid alternative"
                )
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: pinned model {pin!r} is unavailable; "
            "select an explicit valid alternative"
        )
    if not default_policy_authorizes_auto:
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: automatic eligible-model resolution "
            "is not authorized by the default policy; select a Profile/model explicitly"
        )
    if not eligible:
        raise_no_eligible_free_model(records or [])
    return eligible[0]


def raise_no_eligible_free_model(records: list[FreeEligibilityRecord]) -> None:
    """Raise the free-route taxonomy with separate reason categories."""

    by_category: dict[str, list[str]] = {
        "availability": [],
        "pricing": [],
        "capability": [],
        "privacy": [],
    }
    for record in records or []:
        for reason in record.reasons:
            bucket = by_category.setdefault(reason.category, [])
            bucket.append(f"{record.qualified_id or '?'}: {reason.detail}")
    detail = "; ".join(
        f"{category}=[{', '.join(sorted(set(items))) or 'n/a'}]"
        for category, items in sorted(by_category.items())
    )
    raise ValueError(f"{NO_ELIGIBLE_FREE_MODEL}: {detail or 'no candidates observed'}")


def freeze_free_attempt(
    record: FreeEligibilityRecord,
    *,
    catalog_evidence_ref: str,
    pricing_evidence_ref: str,
    data_use_evidence_ref: str,
    eligibility_expires_at: str | None = None,
    frozen_at: str | None = None,
) -> FrozenFreeAttempt:
    """Freeze selection plus evidence with the admitted attempt."""

    if not record.eligible:
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: cannot freeze an ineligible model "
            f"({record.blocked_reason()})"
        )
    return FrozenFreeAttempt(
        model_id=record.qualified_id,
        route_ref=f"{FREE_RUNTIME_ID}/{record.provider_route}",
        materializer_ref=record.materializer_ref,
        catalog_evidence_ref=str(catalog_evidence_ref or ""),
        pricing_evidence_ref=str(pricing_evidence_ref or ""),
        data_use_evidence_ref=str(data_use_evidence_ref or ""),
        policy_version=record.policy_version,
        frozen_at=frozen_at or datetime.now(UTC).isoformat(),
        eligibility_expires_at=eligibility_expires_at,
    )


def free_attempt_needs_recheck(
    attempt: FrozenFreeAttempt,
    *,
    now: datetime | None = None,
) -> bool:
    """Define the authoritative recheck immediately before a new send.

    Discovery may refresh without rewriting active inputs, but when
    external eligibility has expired or changed, the attempt must fail
    safely or start an explicitly authorized new attempt preserving saved
    work. The provider/model inside a billed request is never replaced.
    """

    if not attempt.eligibility_expires_at:
        return False
    try:
        expires = datetime.fromisoformat(str(attempt.eligibility_expires_at))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) >= expires


def sanitize_zen_environment(env: Mapping[str, Any]) -> dict[str, str]:
    """Return the credentialless environment with ambient keys removed.

    Keeps only the declared no-material route: every ambient model key,
    auth cache pointer, and inherited OpenCode config/plugin/provider
    override is dropped. ``OPENCODE_API_KEY`` may configure the separate
    keyed profile but never rescues this attempt.
    """

    cleaned: dict[str, str] = {}
    for key, value in (env or {}).items():
        name = str(key)
        if name in FORBIDDEN_AMBIENT_ENV_FOR_ZEN:
            continue
        if name in FORBIDDEN_ZEN_CONFIG_KEYS:
            continue
        if name.startswith("OPENCODE_") and "AUTH" in name:
            continue
        cleaned[name] = str(value)
    return cleaned


def assert_no_key_rescue(env: Mapping[str, Any]) -> None:
    """Fail when the keyed profile's key would rescue a Zen attempt."""

    if str((env or {}).get("OPENCODE_API_KEY") or "").strip():
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: OPENCODE_API_KEY must never rescue a "
            "credentialless attempt; configure the separate keyed profile instead"
        )


@dataclass(frozen=True, slots=True)
class FreeFirstResultInputs:
    """Inputs to the one credentialless first-result path (req-01)."""

    seed_profile_id: str = FREE_PROFILE_ID
    seed_materializer_ref: str = FREE_MATERIALIZER_REF
    catalog_qualified_ids: tuple[str, ...] = ()
    requested_model: str = ""
    requested_effort: str = ""
    operator_disabled: bool = False
    default_policy_authorizes_auto: bool = False
    evidence: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FreeFirstResult:
    """Outcome of the one first-result path reusing existing owners."""

    qualified_id: str
    effort: str
    materializer_ref: str = FREE_MATERIALIZER_REF
    profile_id: str = FREE_PROFILE_ID
    evidence_refs: Mapping[str, Any] = field(default_factory=dict)


def resolve_free_first_result(
    inputs: FreeFirstResultInputs,
    *,
    eligibility: FreeEligibilityRecord,
) -> FreeFirstResult:
    """Map seed/default, catalog, model/effort, materializer, and exact-host
    admission into one first-result path.

    Reuses each owner's verdict (this function coordinates, it does not
    reimplement them): the seed owns the profile identity, the catalog
    owns normalization/presence, the eligibility record owns pricing and
    data-use, the materializer registry owns the no-material route, and
    the exact host owns admission. An explicit operator disable stays
    authoritative; an unavailable pinned choice raises instead of
    substituting.
    """

    if inputs.operator_disabled:
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: {FREE_PROFILE_ID} is explicitly "
            "disabled by the operator"
        )
    if inputs.seed_profile_id != FREE_PROFILE_ID:
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: unexpected seed profile "
            f"{inputs.seed_profile_id!r} for the credentialless route"
        )
    if inputs.seed_materializer_ref != FREE_MATERIALIZER_REF:
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: unexpected materializer "
            f"{inputs.seed_materializer_ref!r} for the credentialless route"
        )
    if not eligibility.eligible:
        raise_no_eligible_free_model([eligibility])
    selected = rank_approved_free_models(
        [eligibility],
        explicit_pin=inputs.requested_model or None,
        default_policy_authorizes_auto=inputs.default_policy_authorizes_auto,
    )
    exact = resolve_exact_free_model(
        selected.qualified_id,
        catalog_qualified_ids=list(inputs.catalog_qualified_ids),
    )
    effort = validate_effort_for_model(
        inputs.requested_effort or "xhigh",
        supported_efforts=selected.supported_efforts,
    )
    return FreeFirstResult(
        qualified_id=exact,
        effort=effort,
        materializer_ref=FREE_MATERIALIZER_REF,
        profile_id=FREE_PROFILE_ID,
        evidence_refs=dict(inputs.evidence or {}),
    )


# Bounded validation: catalog discovery and local probes must not make live
# inference calls. Protected live qualification is explicitly authorized,
# non-sensitive, and budgeted through the existing lease/host budgets.
DISCOVERY_ALLOWS_LIVE_INFERENCE = False
LIVE_QUALIFICATION_MAX_REQUESTS = 1
LIVE_QUALIFICATION_SENSITIVE = False


def discovery_plan(*, allow_live_inference: bool = False) -> dict[str, Any]:
    """Return the bounded validation plan for the free route."""

    if allow_live_inference:
        raise ValueError(
            f"{NO_ELIGIBLE_FREE_MODEL}: catalog discovery and local probes "
            "must not make live inference calls"
        )
    return {
        "live_inference": DISCOVERY_ALLOWS_LIVE_INFERENCE,
        "live_qualification_max_requests": LIVE_QUALIFICATION_MAX_REQUESTS,
        "live_qualification_sensitive": LIVE_QUALIFICATION_SENSITIVE,
        "single_flight": True,
    }


__all__ = [
    "DISCOVERY_ALLOWS_LIVE_INFERENCE",
    "FORBIDDEN_AMBIENT_ENV_FOR_ZEN",
    "FORBIDDEN_ZEN_CONFIG_KEYS",
    "FREE_MATERIALIZER_REF",
    "FREE_PROFILE_ID",
    "FREE_PROVIDER_ID",
    "FREE_RUNTIME_ID",
    "FREE_ROUTE_SELECTION_POLICY_VERSION",
    "LIVE_QUALIFICATION_MAX_REQUESTS",
    "LIVE_QUALIFICATION_SENSITIVE",
    "NO_ELIGIBLE_FREE_MODEL",
    "PRICING_DIMENSIONS",
    "FreeEligibilityRecord",
    "FreeFirstResult",
    "FreeFirstResultInputs",
    "FreeRouteReason",
    "FrozenFreeAttempt",
    "assert_no_key_rescue",
    "check_pricing_eligible",
    "discovery_plan",
    "evaluate_data_use_policy",
    "freeze_free_attempt",
    "free_attempt_needs_recheck",
    "parse_cost_value",
    "qualify_free_candidate",
    "raise_no_eligible_free_model",
    "rank_approved_free_models",
    "resolve_exact_free_model",
    "resolve_free_first_result",
    "sanitize_zen_environment",
    "validate_effort_for_model",
]
