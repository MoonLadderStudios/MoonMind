"""Helpers for OpenCode model resolution."""

from __future__ import annotations

import re
from typing import Any

# Official model IDs per OpenCode docs
DEFAULT_OPENCODE_MODEL_DISPLAY = "Muse Spark 1.3 Contributor"
DEFAULT_OPENCODE_PROVIDER_ID = "muse-spark-1.3-contributor"
DEFAULT_OPENCODE_QUALIFIED = f"opencode-go/{DEFAULT_OPENCODE_PROVIDER_ID}"

# Zen free tier — available via OpenCode's built-in provider
ZEN_FREE_MODEL_DISPLAY = "Muse Spark 1.3 Contributor Free"
ZEN_FREE_PROVIDER_ID = "muse-spark-1.3-contributor-free"
ZEN_FREE_QUALIFIED = f"opencode/{ZEN_FREE_PROVIDER_ID}"

# Friendly name normalization: case-insensitive, punctuation-insensitive
def normalize_model_display(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


_MODEL_ALIASES = {
    normalize_model_display(DEFAULT_OPENCODE_MODEL_DISPLAY): {
        "displayName": DEFAULT_OPENCODE_MODEL_DISPLAY,
        "providerModelId": DEFAULT_OPENCODE_PROVIDER_ID,
        "qualifiedId": DEFAULT_OPENCODE_QUALIFIED,
    },
    normalize_model_display("mus spark 1.3 contributor"): {
        "displayName": DEFAULT_OPENCODE_MODEL_DISPLAY,
        "providerModelId": DEFAULT_OPENCODE_PROVIDER_ID,
        "qualifiedId": DEFAULT_OPENCODE_QUALIFIED,
    },
    normalize_model_display(ZEN_FREE_MODEL_DISPLAY): {
        "displayName": ZEN_FREE_MODEL_DISPLAY,
        "providerModelId": ZEN_FREE_PROVIDER_ID,
        "qualifiedId": ZEN_FREE_QUALIFIED,
    },
    normalize_model_display(ZEN_FREE_QUALIFIED): {
        "displayName": ZEN_FREE_MODEL_DISPLAY,
        "providerModelId": ZEN_FREE_PROVIDER_ID,
        "qualifiedId": ZEN_FREE_QUALIFIED,
    },
}


def resolve_model_by_display(
    display: str,
    available_models: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Resolve friendly display name to provider and qualified IDs.

    If available_models is provided (from live catalog), verify existence.
    Otherwise, use alias table.
    """
    normalized = normalize_model_display(display)
    alias = _MODEL_ALIASES.get(normalized)
    if alias is None:
        # Try to find in available list directly by qualified or provider id
        if available_models:
            for m in available_models:
                qid = str(m.get("qualifiedId") or "").strip()
                if normalize_model_display(qid) == normalized or qid.lower() == display.lower().strip():
                    # Extract provider id after slash
                    provider_id = qid.split("/", 1)[-1] if "/" in qid else qid
                    return {
                        "displayName": display,
                        "providerModelId": provider_id,
                        "qualifiedId": qid,
                    }
        qualified = display.strip()
        provider_prefix, separator, provider_model_id = qualified.partition("/")
        if (
            available_models is None
            and separator
            and provider_prefix.startswith("opencode-")
            and provider_model_id
        ):
            # Bootstrap resolves images before live credential-scoped model
            # validation. Preserve a current Provider Profile's canonical model
            # identity here; qualification still fails closed if the later live
            # catalog does not contain it.
            return {
                "displayName": qualified,
                "providerModelId": provider_model_id,
                "qualifiedId": qualified,
            }
        raise ValueError(f"Requested model is unavailable for this OpenCode Go account: {display!r}")
    # If catalog available, verify alias exists in catalog
    if available_models is not None:
        qualified = alias["qualifiedId"]
        if not any(str(m.get("qualifiedId") or "") == qualified for m in available_models):
            # Provide live alternatives in error detail
            alternatives = ", ".join(str(m.get("qualifiedId") or "") for m in available_models[:5])
            raise ValueError(
                f"Requested model {display!r} is unavailable for this OpenCode Go account. "
                f"Available: {alternatives or 'none'}"
            )
    return alias


def validate_effort(effort: str, available_efforts: list[str] | None = None) -> str:
    normalized = effort.strip().lower()
    allowed = {"minimal", "low", "medium", "high", "xhigh"}
    if normalized not in allowed:
        raise ValueError(f"effort {effort!r} is not supported")
    if available_efforts is not None and normalized not in {e.lower() for e in available_efforts}:
        raise ValueError(f"effort {effort!r} is not supported by the selected model")
    return normalized
