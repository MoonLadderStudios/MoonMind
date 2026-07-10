"""Canonical model/effort resolver for managed runtime task submissions.

Single source of truth for resolving which model and effort should be used for
a managed agent run.  The legacy model-only precedence chain is:

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

__all__ = ["ResolvedModelEffort", "resolve_effective_model", "resolve_model_effort"]

# legacy_run contract — the model_source value "task_override" is persisted in
# execution parameters/diagnostics; the value renames at the
# MoonMind.UserWorkflow v2 cutover (MM-730).
_MODEL_SOURCE_TASK_OVERRIDE = "task_override"
_MODEL_SOURCE_PROFILE_DEFAULT = "provider_profile_default"
_MODEL_SOURCE_RUNTIME_DEFAULT = "runtime_default"
_MODEL_SOURCE_NONE = "none"
_MODEL_SOURCE_REQUESTED_TIER = "requested_tier"
_MODEL_SOURCE_PROFILE_DEFAULT_TIER = "profile_default_tier"
_FALLBACK_CLAMP = "clamp"
_FALLBACK_STRICT = "strict"
_EFFORT_APPLICATION_UNKNOWN = "unknown"
_FALLBACK_REQUESTED_TIER_BELOW_RANGE = "requested_tier_below_configured_range"
_FALLBACK_REQUESTED_TIER_ABOVE_RANGE = "requested_tier_above_configured_range"


@dataclass(frozen=True)
class ResolvedModelEffort:
    """Resolved model/effort policy for a launch-ready provider profile."""

    model: str | None
    effort: str | None
    requested_model_tier: int | None
    effective_model_tier: int | None
    tier_label: str | None
    model_source: str
    effort_source: str
    fallback_reason: str | None
    effort_application_status: str | None
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
    # Preserve the raw requested_model exactly for pass-through; only use a
    # clean copy internally to detect blankness. This keeps `codex.model` and
    # `codex.effort` inputs unmodified (Compatibility Policy §codex).
    clean_requested = (str(requested_model or "").strip()) or None

    # 1. Explicit task-level model takes top priority.
    if clean_requested:
        return clean_requested, _MODEL_SOURCE_TASK_OVERRIDE

    # 2. Provider profile default_model.
    if profile is not None:
        profile_model = str(getattr(profile, "default_model", None) or "").strip() or None
        if profile_model:
            return profile_model, _MODEL_SOURCE_PROFILE_DEFAULT

    # 3. Runtime default.  Always normalize (normalize_runtime_id falls back to
    # DEFAULT_WORKFLOW_RUNTIME for None/empty), so the runtime-default tier applies
    # even when the caller omits targetRuntime.
    canonical_runtime = normalize_runtime_id(runtime_id)
    runtime_model, _ = resolve_runtime_defaults(
        canonical_runtime,
        workflow_settings=workflow_settings,
        env=env,
    )
    if runtime_model:
        return runtime_model, _MODEL_SOURCE_RUNTIME_DEFAULT

    return None, _MODEL_SOURCE_NONE


def resolve_model_effort(
    *,
    runtime_id: str | None,
    profile: Any | None,
    requested_model_tier: int | None = None,
    requested_model: str | None = None,
    requested_effort: str | None = None,
    tier_fallback: str = _FALLBACK_CLAMP,
    advisory_preview: Mapping[str, Any] | None = None,
    require_launch_ready: bool = True,
    workflow_settings: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> ResolvedModelEffort:
    """Resolve model and effort from explicit overrides, profile tiers, and defaults.

    This is the tier-aware backend resolver for MM-1170.  The legacy
    ``resolve_effective_model`` tuple helper remains available for current
    callers that only need the effective model and source.
    """
    if require_launch_ready:
        _ensure_launch_ready_profile(profile)
    requested_tier = _normalize_requested_tier(requested_model_tier)
    fallback_policy = _normalize_tier_fallback(tier_fallback)

    clean_requested_model = _clean(requested_model)
    clean_requested_effort = _clean(requested_effort)
    if clean_requested_model:
        runtime_model, runtime_effort = _runtime_defaults(
            runtime_id,
            workflow_settings=workflow_settings,
            env=env,
        )
        model, model_source = _first_value(
            (clean_requested_model, _MODEL_SOURCE_TASK_OVERRIDE),
            (_legacy_profile_value(profile, "default_model"), _MODEL_SOURCE_PROFILE_DEFAULT),
            (runtime_model, _MODEL_SOURCE_RUNTIME_DEFAULT),
        )
        effort, effort_source = _first_value(
            (clean_requested_effort, _MODEL_SOURCE_TASK_OVERRIDE),
            (_legacy_profile_value(profile, "default_effort"), _MODEL_SOURCE_PROFILE_DEFAULT),
            (runtime_effort, _MODEL_SOURCE_RUNTIME_DEFAULT),
        )
        return _with_preview_mismatch(
            ResolvedModelEffort(
                model=model,
                effort=effort,
                requested_model_tier=requested_tier,
                effective_model_tier=None,
                tier_label=None,
                model_source=model_source,
                effort_source=effort_source,
                fallback_reason=None,
                effort_application_status=_EFFORT_APPLICATION_UNKNOWN,
            ),
            advisory_preview,
        )

    tiers = _profile_model_tiers(profile)
    if tiers:
        effective_tier, fallback_reason, tier_source = _resolve_effective_tier(
            requested_tier=requested_tier,
            default_tier=_profile_default_model_tier(profile),
            tier_count=len(tiers),
            tier_fallback=fallback_policy,
        )
        tier = tiers[effective_tier - 1]
        tier_model = _clean(tier.get("model"))
        tier_effort = _clean(tier.get("effort"))
        tier_label = _clean(tier.get("label"))
        runtime_model, runtime_effort = _runtime_defaults(
            runtime_id,
            workflow_settings=workflow_settings,
            env=env,
        )
        model, model_source = _first_value(
            (tier_model, tier_source),
            (_legacy_profile_value(profile, "default_model"), _MODEL_SOURCE_PROFILE_DEFAULT),
            (runtime_model, _MODEL_SOURCE_RUNTIME_DEFAULT),
        )
        effort, effort_source = _first_value(
            (clean_requested_effort, _MODEL_SOURCE_TASK_OVERRIDE),
            (tier_effort, tier_source),
            (_legacy_profile_value(profile, "default_effort"), _MODEL_SOURCE_PROFILE_DEFAULT),
            (runtime_effort, _MODEL_SOURCE_RUNTIME_DEFAULT),
        )
        return _with_preview_mismatch(
            ResolvedModelEffort(
                model=model,
                effort=effort,
                requested_model_tier=requested_tier,
                effective_model_tier=effective_tier,
                tier_label=tier_label,
                model_source=model_source,
                effort_source=effort_source,
                fallback_reason=fallback_reason,
                effort_application_status=_EFFORT_APPLICATION_UNKNOWN,
            ),
            advisory_preview,
        )

    runtime_model, runtime_effort = _runtime_defaults(
        runtime_id,
        workflow_settings=workflow_settings,
        env=env,
    )
    model, model_source = _first_value(
        (_legacy_profile_value(profile, "default_model"), _MODEL_SOURCE_PROFILE_DEFAULT),
        (runtime_model, _MODEL_SOURCE_RUNTIME_DEFAULT),
    )
    effort, effort_source = _first_value(
        (clean_requested_effort, _MODEL_SOURCE_TASK_OVERRIDE),
        (_legacy_profile_value(profile, "default_effort"), _MODEL_SOURCE_PROFILE_DEFAULT),
        (runtime_effort, _MODEL_SOURCE_RUNTIME_DEFAULT),
    )
    return _with_preview_mismatch(
        ResolvedModelEffort(
            model=model,
            effort=effort,
            requested_model_tier=requested_tier,
            effective_model_tier=None,
            tier_label=None,
            model_source=model_source,
            effort_source=effort_source,
            fallback_reason=None,
            effort_application_status=_EFFORT_APPLICATION_UNKNOWN,
        ),
        advisory_preview,
    )


def _clean(value: Any | None) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


def _with_preview_mismatch(
    resolved: ResolvedModelEffort,
    advisory_preview: Mapping[str, Any] | None,
) -> ResolvedModelEffort:
    if not advisory_preview:
        return resolved
    expected = {
        "requestedTier": resolved.requested_model_tier,
        "effectiveTier": resolved.effective_model_tier,
        "model": resolved.model,
        "effort": resolved.effort,
        "fallbackReason": resolved.fallback_reason,
    }
    preview_mismatch = any(
        key in advisory_preview and advisory_preview.get(key) != value
        for key, value in expected.items()
    )
    return replace(resolved, preview_mismatch=preview_mismatch)


def _runtime_defaults(
    runtime_id: str | None,
    *,
    workflow_settings: Any | None,
    env: Mapping[str, str] | None,
) -> tuple[str | None, str | None]:
    canonical_runtime = normalize_runtime_id(runtime_id)
    return resolve_runtime_defaults(
        canonical_runtime,
        workflow_settings=workflow_settings,
        env=env,
    )


def _first_value(*candidates: tuple[str | None, str]) -> tuple[str | None, str]:
    for value, source in candidates:
        if value:
            return value, source
    return None, _MODEL_SOURCE_NONE


def _legacy_profile_value(profile: Any | None, field_name: str) -> str | None:
    if profile is None:
        return None
    if isinstance(profile, Mapping):
        return _clean(profile.get(field_name))
    return _clean(getattr(profile, field_name, None))


def _profile_model_tiers(profile: Any | None) -> list[dict[str, Any]]:
    raw = None
    if profile is not None:
        raw = (
            profile.get("model_tiers")
            if isinstance(profile, Mapping)
            else getattr(profile, "model_tiers", None)
        )
    if not isinstance(raw, list):
        return []

    tiers: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            tiers.append(dict(entry))
            continue
        model_dump = getattr(entry, "model_dump", None)
        if not callable(model_dump):
            raise ValueError("profile.model_tiers entries must be mappings")
        tiers.append(dict(model_dump()))
    return tiers


def _profile_default_model_tier(profile: Any | None) -> int:
    raw = 1
    if profile is not None:
        raw = (
            profile.get("default_model_tier", 1)
            if isinstance(profile, Mapping)
            else getattr(profile, "default_model_tier", 1)
        )
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(
            "profile.default_model_tier must be an integer greater than or equal to 1"
        )
    if raw < 1:
        raise ValueError(
            "profile.default_model_tier must be an integer greater than or equal to 1"
        )
    return raw


def _normalize_requested_tier(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("modelTier must be an integer greater than or equal to 1")
    if value < 1:
        raise ValueError("modelTier must be an integer greater than or equal to 1")
    return value


def _normalize_tier_fallback(value: str | None) -> str:
    normalized = str(value or _FALLBACK_CLAMP).strip().lower()
    if normalized not in {_FALLBACK_CLAMP, _FALLBACK_STRICT}:
        raise ValueError("tierFallback must be clamp or strict")
    return normalized


def _resolve_effective_tier(
    *,
    requested_tier: int | None,
    default_tier: int,
    tier_count: int,
    tier_fallback: str,
) -> tuple[int, str | None, str]:
    raw_tier = requested_tier if requested_tier is not None else default_tier
    source = (
        _MODEL_SOURCE_REQUESTED_TIER
        if requested_tier is not None
        else _MODEL_SOURCE_PROFILE_DEFAULT_TIER
    )
    fallback_reason = None
    if raw_tier < 1:
        fallback_reason = _FALLBACK_REQUESTED_TIER_BELOW_RANGE
    elif raw_tier > tier_count:
        fallback_reason = _FALLBACK_REQUESTED_TIER_ABOVE_RANGE

    if fallback_reason and tier_fallback == _FALLBACK_STRICT:
        raise ValueError("requested_model_tier_unavailable")

    return max(1, min(raw_tier, tier_count)), fallback_reason, source


def _ensure_launch_ready_profile(profile: Any | None) -> None:
    if profile is None:
        raise ValueError("selected provider profile is required for tier-aware resolution")
    if isinstance(profile, Mapping):
        enabled = profile.get("enabled", True)
        auth_state = profile.get("auth_state", profile.get("authState"))
        disabled_reason = profile.get("disabled_reason", profile.get("disabledReason"))
        launch_ready = profile.get("launch_ready", profile.get("launchReady", True))
    else:
        enabled = getattr(profile, "enabled", True)
        auth_state = getattr(profile, "auth_state", getattr(profile, "authState", None))
        disabled_reason = getattr(
            profile, "disabled_reason", getattr(profile, "disabledReason", None)
        )
        launch_ready = getattr(profile, "launch_ready", getattr(profile, "launchReady", True))

    if enabled is False:
        raise ValueError("selected provider profile is not launch-ready")
    if _enum_value(auth_state) not in {None, "connected"}:
        raise ValueError("selected provider profile is not launch-ready")
    if disabled_reason is not None:
        raise ValueError("selected provider profile is not launch-ready")
    if launch_ready is False:
        raise ValueError("selected provider profile is not launch-ready")


def _enum_value(value: Any | None) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value)).strip().lower() or None
