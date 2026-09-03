"""Provider Profile tier editor capability contract.

MoonLadderStudios/MoonMind#3815 — backend-driven model/effort selector
per docs/UI/ProviderProfileModelEffortTierSettings.md section 12.

Capabilities are scoped to the profile being edited so they reflect the
profile's persisted evidence (credential generation, image ref). Draft
creation forms use runtime/provider only.

This module deliberately returns advisory catalog data without exposing
credentials. The write path remains authoritative.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_MODEL_OPTIONS_BY_PROVIDER: dict[str, list[dict[str, Any]]] = {
    "openai": [
        {"value": "gpt-5.5", "label": "GPT-5.5", "description": "General coding model", "status": "available", "recommended": True},
        {"value": "gpt-4o", "label": "GPT-4o", "description": "Multimodal flagship", "status": "available", "recommended": False},
        {"value": "gpt-4o-mini", "label": "GPT-4o mini", "description": "Cost-efficient", "status": "available", "recommended": False},
    ],
    "anthropic": [
        {"value": "claude-4-sonnet", "label": "Claude 4 Sonnet", "description": "Balanced", "status": "available", "recommended": True},
        {"value": "claude-4-opus", "label": "Claude 4 Opus", "description": "Highest capability", "status": "available", "recommended": False},
        {"value": "claude-3-5-sonnet", "label": "Claude 3.5 Sonnet", "description": "Previous generation", "status": "deprecated", "recommended": False},
    ],
    "default": [
        {"value": "gpt-5.5", "label": "GPT-5.5", "description": "General coding model", "status": "available", "recommended": True},
        {"value": "gpt-4o", "label": "GPT-4o", "description": None, "status": "available", "recommended": False},
    ],
}

_DEFAULT_EFFORT_OPTIONS: list[dict[str, Any]] = [
    {"value": "low", "label": "Low", "description": None, "status": "available", "compatible_models": None},
    {"value": "medium", "label": "Medium", "description": None, "status": "available", "compatible_models": None},
    {"value": "high", "label": "High", "description": None, "status": "available", "compatible_models": None},
    {"value": "xhigh", "label": "Extra high", "description": None, "status": "available", "compatible_models": None},
]

_RUNTIME_DEFAULTS: dict[str, dict[str, Any]] = {
    "codex_cli": {"model": "gpt-5.5", "effort": "medium"},
    "claude_code": {"model": "claude-4-sonnet", "effort": "medium"},
    "default": {"model": None, "effort": "medium"},
}


def _runtime_defaults(runtime_id: str) -> dict[str, Any]:
    return _RUNTIME_DEFAULTS.get(runtime_id, _RUNTIME_DEFAULTS["default"])


def _model_options(provider_id: str) -> list[dict[str, Any]]:
    return _MODEL_OPTIONS_BY_PROVIDER.get(provider_id, _MODEL_OPTIONS_BY_PROVIDER["default"])


def _evidence_model_options(evidence: Any, default_model: str | None) -> list[dict[str, Any]] | None:
    """Build model options from persisted profile catalog evidence.

    Evidence `models` entries carry `qualifiedId` values observed from the
    pinned runtime. Returning those instead of the provider-name table keeps
    the editor from offering models the profile's own catalog never contained.
    Returns None when the evidence carries no usable model entries so callers
    can fall back to the advisory provider table with a diagnostic.
    """
    if not isinstance(evidence, dict):
        return None
    raw_models = evidence.get("models")
    if not isinstance(raw_models, list):
        return None
    options: list[dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        qualified_id = str(item.get("qualifiedId") or item.get("qualified_id") or "").strip()
        if not qualified_id:
            continue
        options.append({
            "value": qualified_id,
            "label": str(item.get("label") or item.get("name") or qualified_id),
            "description": item.get("description") if isinstance(item.get("description"), str) else None,
            "status": str(item.get("status") or "available"),
            "recommended": bool(default_model and qualified_id == default_model),
        })
    if not options:
        return None
    if default_model and not any(o["recommended"] for o in options):
        # The persisted default is absent from the observed catalog; keep the
        # first observed entry recommended instead of inventing a selection.
        options[0]["recommended"] = True
    return options


def _evidence_is_fresh(evidence: Any) -> bool:
    """Report whether the catalog observation is still inside its interval.

    Falls back to True when the shared freshness helper is unavailable so a
    missing import cannot mark healthy evidence stale.
    """
    try:
        from moonmind.omnigent.bootstrap.provider_revalidation import (
            evidence_observation_is_current,
        )
    except Exception:
        return True
    try:
        return bool(evidence_observation_is_current(evidence))
    except Exception:
        return True


def _expected_materializer_ref(runtime_id: Any, provider_id: Any) -> str | None:
    """Return the launch-path materializer ref, or None when unresolvable."""
    try:
        from moonmind.omnigent.harness_platform.materializers import (
            materializer_ref_for_provider,
        )
    except Exception:
        return None
    try:
        return str(materializer_ref_for_provider(str(runtime_id or ""), str(provider_id or "")))
    except Exception:
        return None


def _version(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"tier-cap-v1-{digest}"


def build_tier_capabilities(
    *,
    runtime_id: str,
    provider_id: str,
    profile_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    credential_generation: int | None = None,
    image_ref: str | None = None,
    observed_at: str | None = None,
    stale: bool = False,
    diagnostics: list[dict[str, Any]] | None = None,
    model_options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the ProviderProfileEditorCapabilities payload.

    Callers choose evidence.source and stale flag; this helper builds the
    advisory catalog envelope around that identity. `model_options` overrides
    the provider-name table when the caller derived options from evidence.
    """

    runtime_id = (runtime_id or "").strip() or "unknown"
    provider_id = (provider_id or "").strip() or "unknown"
    defaults = _runtime_defaults(runtime_id)
    resolved_model_options = model_options if model_options is not None else _model_options(provider_id)
    effort_options = _DEFAULT_EFFORT_OPTIONS

    evidence_payload: dict[str, Any] = {
        "source": evidence.get("source") if evidence else ("runtime_draft" if profile_id is None else "profile_catalog_evidence"),
        "credential_generation": credential_generation,
        "image_ref": image_ref,
        "observed_at": observed_at,
        "stale": stale,
    } if evidence is None else {
        "source": evidence.get("source", "runtime_draft" if profile_id is None else "profile_catalog_evidence"),
        "credential_generation": evidence.get("credential_generation", credential_generation),
        "image_ref": evidence.get("image_ref", image_ref),
        "observed_at": evidence.get("observed_at", observed_at),
        "stale": bool(evidence.get("stale", stale)),
    }

    base: dict[str, Any] = {
        "profile_id": profile_id,
        "runtime_id": runtime_id,
        "provider_id": provider_id,
        "evidence": evidence_payload,
        "tier_constraints": {"min_count": 1, "max_count": None},
        "model": {
            "runtime_default": defaults.get("model"),
            "allow_custom": True,
            "options": resolved_model_options,
        },
        "effort": {
            "supported": True,
            "runtime_default": defaults.get("effort"),
            "allow_custom": False,
            "application": "native",
            "options": effort_options,
        },
        "diagnostics": diagnostics or [],
    }
    # version is opaque catalog identity; it changes when catalog or evidence changes
    base["version"] = _version({k: v for k, v in base.items() if k != "version"})
    return base


def tier_capabilities_for_profile(profile: Any) -> dict[str, Any]:
    """Build capabilities scoped to a persisted profile's evidence.

    Staleness reuses the full evidence identity used by launch admission
    (credential generation, host image, materializer, observation freshness)
    rather than a generation-only check, so the editor never advertises
    models the same profile cannot launch.
    """
    runtime_id = getattr(profile, "runtime_id", "unknown")
    provider_id = getattr(profile, "provider_id", "unknown")
    profile_id = getattr(profile, "profile_id", None)
    evidence_raw = getattr(profile, "model_catalog_evidence_json", None)
    credential_generation = getattr(profile, "credential_generation", None)
    default_model = str(getattr(profile, "default_model", None) or "").strip() or None
    profile_image_ref = getattr(profile, "runtime_validation_image_ref", None)

    if not evidence_raw:
        return build_tier_capabilities(
            runtime_id=runtime_id,
            provider_id=provider_id,
            profile_id=profile_id,
            credential_generation=credential_generation,
            image_ref=profile_image_ref,
            observed_at=None,
            stale=True,
            diagnostics=[{"code": "evidence_missing", "level": "warning", "message": "No catalog evidence for this profile; model choices are advisory."}],
            evidence={"source": "profile_catalog_evidence", "credential_generation": credential_generation, "image_ref": profile_image_ref, "observed_at": None, "stale": True},
        )

    evidence_generation = evidence_raw.get("credentialGeneration") or evidence_raw.get("credential_generation")
    evidence_image_ref = evidence_raw.get("imageRef") or evidence_raw.get("image_ref")
    evidence_materializer_ref = evidence_raw.get("materializerRef") or evidence_raw.get("materializer_ref")
    observed_at = evidence_raw.get("validatedAt") or evidence_raw.get("validated_at") or evidence_raw.get("observed_at")

    stale_reasons: list[str] = []
    try:
        generation_stale = bool(
            evidence_generation is not None
            and credential_generation is not None
            and int(evidence_generation) != int(credential_generation)
        )
    except (TypeError, ValueError):
        generation_stale = True
    if generation_stale:
        stale_reasons.append("credential generation changed since the catalog was observed")
    if profile_image_ref and evidence_image_ref and str(profile_image_ref) != str(evidence_image_ref):
        stale_reasons.append("host image changed since the catalog was observed")
    expected_materializer = _expected_materializer_ref(runtime_id, provider_id)
    if expected_materializer and evidence_materializer_ref and str(evidence_materializer_ref) != expected_materializer:
        stale_reasons.append("launch materializer changed since the catalog was observed")
    if not _evidence_is_fresh(evidence_raw):
        stale_reasons.append("catalog observation is outside its freshness interval")

    stale = bool(stale_reasons)
    diagnostics = []
    if stale:
        diagnostics.append({"code": "evidence_stale", "level": "warning", "message": "Profile catalog evidence is stale (" + "; ".join(stale_reasons) + "); refresh before trusting model choices."})

    evidence_options = _evidence_model_options(evidence_raw, default_model)
    if evidence_options is None:
        diagnostics.append({"code": "evidence_models_fallback", "level": "warning", "message": "Catalog evidence carries no model list; showing advisory provider defaults instead of observed models."})

    return build_tier_capabilities(
        runtime_id=runtime_id,
        provider_id=provider_id,
        profile_id=profile_id,
        credential_generation=credential_generation,
        image_ref=profile_image_ref or evidence_image_ref,
        observed_at=observed_at,
        stale=stale,
        diagnostics=diagnostics,
        model_options=evidence_options,
        evidence={"source": "profile_catalog_evidence", "credential_generation": credential_generation, "image_ref": profile_image_ref or evidence_image_ref, "observed_at": observed_at, "stale": stale},
    )


def tier_capabilities_for_draft(runtime_id: str, provider_id: str) -> dict[str, Any]:
    """Advisory capabilities for a not-yet-created profile."""
    return build_tier_capabilities(
        runtime_id=runtime_id,
        provider_id=provider_id,
        profile_id=None,
        evidence={"source": "runtime_draft", "credential_generation": None, "image_ref": None, "observed_at": None, "stale": False},
        credential_generation=None,
        image_ref=None,
        observed_at=None,
        stale=False,
    )
