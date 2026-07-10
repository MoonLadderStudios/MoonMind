"""Canonical effective-model resolver for managed runtime task submissions.

Single source of truth for resolving which model should be used for a
managed agent run.  The precedence chain is:

1. Explicit model chosen on the task (``"task_override"``).
2. ``default_model`` on the selected provider profile
   (``"provider_profile_default"``).
3. Runtime default for the managed runtime (``"runtime_default"``).
4. No model resolved (``"none"``).

Usage::

    from moonmind.workflows.executions.model_resolver import resolve_effective_model

    resolved_model, model_source = resolve_effective_model(
        runtime_id="codex_cli",
        profile=profile_db_row,    # may be None
        requested_model=task_model,  # from the task payload, may be None
    )
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from moonmind.workflows.executions.runtime_defaults import (
    normalize_runtime_id,
    resolve_runtime_defaults,
)

__all__ = [
    "DEFAULT_MODEL_TIER",
    "ResolvedModelEffort",
    "normalize_model_tiers",
    "resolve_effective_model",
    "resolve_model_effort",
]

# legacy_run contract — the model_source value "task_override" is persisted in
# execution parameters/diagnostics; the value renames at the
# MoonMind.UserWorkflow v2 cutover (MM-730).
_MODEL_SOURCE_TASK_OVERRIDE = "task_override"
_MODEL_SOURCE_PROFILE_DEFAULT = "provider_profile_default"
_MODEL_SOURCE_RUNTIME_DEFAULT = "runtime_default"
_MODEL_SOURCE_NONE = "none"
_MODEL_SOURCE_REQUESTED_TIER = "requested_tier"
_MODEL_SOURCE_PROFILE_DEFAULT_TIER = "profile_default_tier"

DEFAULT_MODEL_TIER: dict[str, Any] = {
    "label": "Runtime default",
    "model": None,
    "effort": None,
    "parameters": {},
    "annotations": {},
}


@dataclass(frozen=True, slots=True)
class ResolvedModelEffort:
    model: str | None
    effort: str | None
    requested_model_tier: int | None
    effective_model_tier: int | None
    tier_label: str | None
    model_source: str
    effort_source: str
    fallback_reason: str | None
    effort_application_status: str | None = "unknown"
    preview_mismatch: bool = False

    def as_metadata(self) -> dict[str, Any]:
        return {
            "requestedModelTier": self.requested_model_tier,
            "effectiveModelTier": self.effective_model_tier,
            "tierLabel": self.tier_label,
            "fallbackReason": self.fallback_reason,
            "resolvedModel": self.model,
            "resolvedEffort": self.effort,
            "modelSource": self.model_source,
            "effortSource": self.effort_source,
            "effortApplicationStatus": self.effort_application_status,
            "previewMismatch": self.preview_mismatch,
        }


def normalize_model_tiers(profile: Any | None) -> list[dict[str, Any]]:
    """Return a validated tier list, deriving one tier from legacy defaults."""
    if profile is None:
        return [dict(DEFAULT_MODEL_TIER)]
    raw_tiers = getattr(profile, "model_tiers", None)
    if isinstance(raw_tiers, list) and raw_tiers:
        return [_normalize_tier_entry(tier) for tier in raw_tiers]

    legacy_model = str(getattr(profile, "default_model", None) or "").strip() or None
    legacy_effort = str(getattr(profile, "default_effort", None) or "").strip() or None
    if legacy_model or legacy_effort:
        return [
            {
                "label": "Default",
                "model": legacy_model,
                "effort": legacy_effort,
                "parameters": {},
                "annotations": {"migratedFrom": "default_model_default_effort"},
            }
        ]
    return [dict(DEFAULT_MODEL_TIER)]


def resolve_model_effort(
    *,
    runtime_id: str | None,
    profile: Any | None,
    requested_model: str | None = None,
    requested_effort: str | None = None,
    requested_model_tier: int | None = None,
    tier_fallback: str | None = None,
    advisory_preview: Mapping[str, Any] | None = None,
    workflow_settings: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> ResolvedModelEffort:
    """Resolve model and effort from explicit overrides, tiers, profile defaults, and runtime defaults."""
    clean_requested_model = str(requested_model or "").strip() or None
    clean_requested_effort = str(requested_effort or "").strip() or None
    tiers = normalize_model_tiers(profile)
    default_tier = _default_model_tier(profile, len(tiers))
    effective_tier, fallback_reason = _effective_tier(
        requested_model_tier,
        default_tier=default_tier,
        tier_count=len(tiers),
        tier_fallback=tier_fallback,
    )
    tier = tiers[effective_tier - 1]
    tier_model = str(tier.get("model") or "").strip() or None
    tier_effort = str(tier.get("effort") or "").strip() or None
    tier_source = (
        _MODEL_SOURCE_PROFILE_DEFAULT_TIER
        if requested_model_tier is None
        else _MODEL_SOURCE_REQUESTED_TIER
    )
    if requested_model_tier is None:
        fallback_reason = fallback_reason or _MODEL_SOURCE_PROFILE_DEFAULT_TIER

    runtime_model, runtime_effort = resolve_runtime_defaults(
        normalize_runtime_id(runtime_id),
        workflow_settings=workflow_settings,
        env=env,
    )

    profile_model = (
        str(getattr(profile, "default_model", None) or "").strip() or None
        if profile is not None
        else None
    )
    profile_effort = (
        str(getattr(profile, "default_effort", None) or "").strip() or None
        if profile is not None
        else None
    )

    if clean_requested_model:
        model, model_source = clean_requested_model, _MODEL_SOURCE_TASK_OVERRIDE
    elif tier_model:
        model, model_source = tier_model, tier_source
    elif profile_model:
        model, model_source = profile_model, _MODEL_SOURCE_PROFILE_DEFAULT
    elif runtime_model:
        model, model_source = runtime_model, _MODEL_SOURCE_RUNTIME_DEFAULT
    else:
        model, model_source = None, _MODEL_SOURCE_NONE

    if clean_requested_effort:
        effort, effort_source = clean_requested_effort, _MODEL_SOURCE_TASK_OVERRIDE
    elif tier_effort:
        effort, effort_source = tier_effort, tier_source
    elif profile_effort:
        effort, effort_source = profile_effort, _MODEL_SOURCE_PROFILE_DEFAULT
    elif runtime_effort:
        effort, effort_source = runtime_effort, _MODEL_SOURCE_RUNTIME_DEFAULT
    else:
        effort, effort_source = None, _MODEL_SOURCE_NONE

    resolved = ResolvedModelEffort(
        model=model,
        effort=effort,
        requested_model_tier=requested_model_tier,
        effective_model_tier=effective_tier,
        tier_label=str(tier.get("label") or "").strip() or None,
        model_source=model_source,
        effort_source=effort_source,
        fallback_reason=fallback_reason,
        effort_application_status="unknown" if effort else None,
        preview_mismatch=False,
    )
    return replace(
        resolved,
        preview_mismatch=_preview_mismatch(advisory_preview, resolved),
    )

def resolve_effective_model(
    *,
    runtime_id: str | None,
    profile: Any | None,
    requested_model: str | None,
    workflow_settings: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str | None, str]:
    """Compute the effective model and its source for a managed runtime task.

    Parameters
    ----------
    runtime_id:
        Raw runtime id from the task payload (e.g. ``"codex_cli"`` or alias
        ``"codex"``).  Normalized internally before lookup.
    profile:
        The selected provider profile DB row (``ManagedAgentProviderProfile``),
        or ``None`` when no profile is selected.
    requested_model:
        The model explicitly chosen on the task, or ``None`` when the task
        does not specify one.
    workflow_settings:
        Optional ``WorkflowSettings`` instance for env-override behavior
        (passed through to ``resolve_runtime_defaults``).
    env:
        Optional environment mapping override (defaults to ``os.environ``).

    Returns
    -------
    tuple[str | None, str]
        ``(resolved_model, model_source)`` where ``model_source`` is one of
        ``"task_override"``, ``"provider_profile_default"``,
        ``"runtime_default"``, or ``"none"``.
    """
    # Preserve the legacy helper contract for callers that only ask for model
    # resolution and assert persisted source values.
    clean_requested = (str(requested_model or "").strip()) or None
    if clean_requested:
        return clean_requested, _MODEL_SOURCE_TASK_OVERRIDE

    if profile is not None:
        profile_model = str(getattr(profile, "default_model", None) or "").strip() or None
        if profile_model:
            return profile_model, _MODEL_SOURCE_PROFILE_DEFAULT

    canonical_runtime = normalize_runtime_id(runtime_id)
    runtime_model, _ = resolve_runtime_defaults(
        canonical_runtime,
        workflow_settings=workflow_settings,
        env=env,
    )
    if runtime_model:
        return runtime_model, _MODEL_SOURCE_RUNTIME_DEFAULT

    return None, _MODEL_SOURCE_NONE


def _normalize_tier_entry(tier: Any) -> dict[str, Any]:
    if not isinstance(tier, Mapping):
        return dict(DEFAULT_MODEL_TIER)
    return {
        "label": str(tier.get("label") or "").strip() or "Tier",
        "model": str(tier.get("model") or "").strip() or None,
        "effort": str(tier.get("effort") or "").strip() or None,
        "parameters": dict(tier.get("parameters") or {}),
        "annotations": dict(tier.get("annotations") or {}),
    }


def _default_model_tier(profile: Any | None, tier_count: int) -> int:
    raw_default = getattr(profile, "default_model_tier", 1) if profile is not None else 1
    try:
        default_tier = int(raw_default or 1)
    except (TypeError, ValueError):
        default_tier = 1
    return max(1, min(default_tier, max(tier_count, 1)))


def _effective_tier(
    requested_tier: int | None,
    *,
    default_tier: int,
    tier_count: int,
    tier_fallback: str | None,
) -> tuple[int, str | None]:
    raw = requested_tier if requested_tier is not None else default_tier
    try:
        tier = int(raw or 1)
    except (TypeError, ValueError):
        tier = default_tier
    if tier_fallback == "strict" and (tier < 1 or tier > tier_count):
        raise ValueError("requested_model_tier_unavailable")
    if tier < 1:
        return 1, "requested_tier_below_configured_range"
    if tier > tier_count:
        return tier_count, "requested_tier_above_configured_range"
    return tier, None


def _preview_mismatch(
    advisory_preview: Mapping[str, Any] | None,
    resolved: ResolvedModelEffort,
) -> bool:
    if not advisory_preview:
        return False
    expected = {
        "requestedTier": resolved.requested_model_tier,
        "effectiveTier": resolved.effective_model_tier,
        "model": resolved.model,
        "effort": resolved.effort,
        "fallbackReason": resolved.fallback_reason,
    }
    for key, value in expected.items():
        if key in advisory_preview and advisory_preview.get(key) != value:
            return True
    return False
