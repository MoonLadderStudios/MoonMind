"""Provider Profile tier editor capabilities."""
from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from api_service.db.models import ManagedAgentProviderProfile

_TIER_CATALOG: dict[tuple[str, str], dict[str, Any]] = {
    ("codex_cli", "openai"): {
        "runtime_default_model": "gpt-5.5",
        "runtime_default_effort": "medium",
        "allow_custom_model": True,
        "allow_custom_effort": False,
        "effort_supported": True,
        "effort_application": "native",
        "models": [
            {"value": "gpt-5.5", "label": "GPT-5.5", "description": "General coding model", "status": "available", "recommended": True},
            {"value": "gpt-5.5-mini", "label": "GPT-5.5 Mini", "description": "Faster, lower cost", "status": "available", "recommended": False},
            {"value": "gpt-5.3-codex-spark", "label": "GPT-5.3 Codex Spark", "description": "Spark-optimized coding", "status": "available", "recommended": False},
            {"value": "gpt-4.1", "label": "GPT-4.1", "description": "Legacy model", "status": "deprecated", "recommended": False},
        ],
        "efforts": [
            {"value": "low", "label": "Low", "description": None, "status": "available", "compatible_models": None},
            {"value": "medium", "label": "Medium", "description": None, "status": "available", "compatible_models": None},
            {"value": "high", "label": "High", "description": None, "status": "available", "compatible_models": None},
            {"value": "xhigh", "label": "Extra high", "description": None, "status": "available", "compatible_models": None},
        ],
    },
    ("claude_code", "anthropic"): {
        "runtime_default_model": "claude-opus-4",
        "runtime_default_effort": "medium",
        "allow_custom_model": True,
        "allow_custom_effort": False,
        "effort_supported": True,
        "effort_application": "native",
        "models": [
            {"value": "claude-opus-4", "label": "Claude Opus 4", "description": "Most capable", "status": "available", "recommended": True},
            {"value": "claude-sonnet-4", "label": "Claude Sonnet 4", "description": "Balanced", "status": "available", "recommended": False},
            {"value": "claude-haiku-4", "label": "Claude Haiku 4", "description": "Fast", "status": "available", "recommended": False},
        ],
        "efforts": [
            {"value": "low", "label": "Low", "description": None, "status": "available", "compatible_models": None},
            {"value": "medium", "label": "Medium", "description": None, "status": "available", "compatible_models": None},
            {"value": "high", "label": "High", "description": None, "status": "available", "compatible_models": None},
            {"value": "xhigh", "label": "Extra high", "description": None, "status": "available", "compatible_models": None},
        ],
    },
    ("opencode", "opencode-go"): {
        "runtime_default_model": "opencode-default",
        "runtime_default_effort": "medium",
        "allow_custom_model": True,
        "allow_custom_effort": False,
        "effort_supported": True,
        "effort_application": "config",
        "models": [
            {"value": "opencode-default", "label": "OpenCode Default", "description": None, "status": "available", "recommended": True},
            {"value": "gpt-5.5", "label": "GPT-5.5 (via OpenCode)", "description": None, "status": "available", "recommended": False},
        ],
        "efforts": [
            {"value": "low", "label": "Low", "description": None, "status": "available", "compatible_models": None},
            {"value": "medium", "label": "Medium", "description": None, "status": "available", "compatible_models": None},
            {"value": "high", "label": "High", "description": None, "status": "available", "compatible_models": None},
        ],
    },
}

_GENERIC_CATALOG: dict[str, Any] = {
    "runtime_default_model": None,
    "runtime_default_effort": None,
    "allow_custom_model": True,
    "allow_custom_effort": True,
    "effort_supported": False,
    "effort_application": "unsupported",
    "models": [
        {"value": "generic-model", "label": "Generic Model", "description": None, "status": "available", "recommended": False},
    ],
    "efforts": [],
}


def _catalog_for(runtime_id: str, provider_id: str) -> dict[str, Any]:
    return _TIER_CATALOG.get((runtime_id.strip(), provider_id.strip()), _GENERIC_CATALOG)


def _evidence_for_profile(profile: ManagedAgentProviderProfile) -> dict[str, Any]:
    raw_evidence = profile.model_catalog_evidence_json or {}
    # If evidence doesn't match current generation/image, mark stale
    generation = profile.credential_generation
    image_ref = profile.runtime_validation_image_ref
    evidence_generation = raw_evidence.get("credential_generation")
    evidence_image = raw_evidence.get("image_ref")
    stale = False
    if evidence_generation is not None and evidence_generation != generation:
        stale = True
    if evidence_image is not None and image_ref is not None and evidence_image != image_ref:
        stale = True
    # if no evidence at all, consider stale
    if not raw_evidence:
        stale = True
    observed_at = raw_evidence.get("observed_at") or (profile.last_validated_at.isoformat() if profile.last_validated_at else None)
    return {
        "source": "profile_catalog_evidence",
        "credential_generation": generation,
        "image_ref": image_ref,
        "observed_at": observed_at,
        "stale": stale,
    }


def _draft_evidence() -> dict[str, Any]:
    return {
        "source": "runtime_draft",
        "credential_generation": None,
        "image_ref": None,
        "observed_at": None,
        "stale": False,
    }


def _version_for(runtime_id: str, provider_id: str, evidence: dict[str, Any]) -> str:
    gen = evidence.get("credential_generation")
    img = evidence.get("image_ref") or "no-image"
    src = evidence.get("source")
    # opaque version changes when evidence identity changes
    return f"{runtime_id}:{provider_id}:{src}:{gen}:{img}"


def build_tier_capabilities(
    *,
    runtime_id: str,
    provider_id: str,
    profile_id: str | None,
    evidence: dict[str, Any],
    version: str | None = None,
) -> dict[str, Any]:
    catalog = _catalog_for(runtime_id, provider_id)
    ver = version or _version_for(runtime_id, provider_id, evidence)
    diagnostics: list[dict[str, Any]] = []
    if evidence.get("stale"):
        diagnostics.append({"code": "catalog_stale", "level": "warning", "message": "Catalog evidence is stale; choices may be outdated."})
    return {
        "version": ver,
        "profile_id": profile_id,
        "runtime_id": runtime_id,
        "provider_id": provider_id,
        "evidence": evidence,
        "tier_constraints": {"min_count": 1, "max_count": None},
        "model": {
            "runtime_default": catalog.get("runtime_default_model"),
            "allow_custom": bool(catalog.get("allow_custom_model")),
            "options": catalog.get("models", []),
        },
        "effort": {
            "supported": bool(catalog.get("effort_supported")),
            "runtime_default": catalog.get("runtime_default_effort"),
            "allow_custom": bool(catalog.get("allow_custom_effort")),
            "application": catalog.get("effort_application", "unknown"),
            "options": catalog.get("efforts", []),
        },
        "diagnostics": diagnostics,
    }


def tier_capabilities_for_profile(profile: ManagedAgentProviderProfile) -> dict[str, Any]:
    evidence = _evidence_for_profile(profile)
    return build_tier_capabilities(
        runtime_id=profile.runtime_id,
        provider_id=profile.provider_id,
        profile_id=profile.profile_id,
        evidence=evidence,
    )


def tier_capabilities_for_draft(*, runtime_id: str, provider_id: str) -> dict[str, Any]:
    evidence = _draft_evidence()
    return build_tier_capabilities(
        runtime_id=runtime_id.strip(),
        provider_id=provider_id.strip(),
        profile_id=None,
        evidence=evidence,
    )
