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
) -> dict[str, Any]:
    """Return the ProviderProfileEditorCapabilities payload.

    Callers choose evidence.source and stale flag; this helper builds the
    advisory catalog envelope around that identity.
    """

    runtime_id = (runtime_id or "").strip() or "unknown"
    provider_id = (provider_id or "").strip() or "unknown"
    defaults = _runtime_defaults(runtime_id)
    model_options = _model_options(provider_id)
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
            "options": model_options,
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
    """Build capabilities scoped to a persisted profile's evidence."""
    runtime_id = getattr(profile, "runtime_id", "unknown")
    provider_id = getattr(profile, "provider_id", "unknown")
    profile_id = getattr(profile, "profile_id", None)
    evidence_raw = getattr(profile, "model_catalog_evidence_json", None)
    credential_generation = getattr(profile, "credential_generation", None)
    image_ref = getattr(profile, "runtime_validation_image_ref", None) or (evidence_raw or {}).get("imageRef") or (evidence_raw or {}).get("image_ref")
    observed_at = (evidence_raw or {}).get("validatedAt") or (evidence_raw or {}).get("validated_at") or (evidence_raw or {}).get("observed_at")

    if not evidence_raw:
        return build_tier_capabilities(
            runtime_id=runtime_id,
            provider_id=provider_id,
            profile_id=profile_id,
            credential_generation=credential_generation,
            image_ref=image_ref,
            observed_at=observed_at,
            stale=True,
            diagnostics=[{"code": "evidence_missing", "level": "warning", "message": "No catalog evidence for this profile; model choices are advisory."}],
            evidence={"source": "profile_catalog_evidence", "credential_generation": credential_generation, "image_ref": image_ref, "observed_at": observed_at, "stale": True},
        )

    # Evidence exists; if generation mismatch, treat as stale
    evidence_generation = evidence_raw.get("credentialGeneration") or evidence_raw.get("credential_generation")
    stale = bool(evidence_generation is not None and credential_generation is not None and int(evidence_generation) != int(credential_generation))
    diagnostics = []
    if stale:
        diagnostics.append({"code": "evidence_stale", "level": "warning", "message": "Profile catalog evidence is stale; refresh before trusting model choices."})
    return build_tier_capabilities(
        runtime_id=runtime_id,
        provider_id=provider_id,
        profile_id=profile_id,
        credential_generation=credential_generation,
        image_ref=image_ref,
        observed_at=observed_at,
        stale=stale,
        diagnostics=diagnostics,
        evidence={"source": "profile_catalog_evidence", "credential_generation": credential_generation, "image_ref": image_ref, "observed_at": observed_at, "stale": stale},
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
