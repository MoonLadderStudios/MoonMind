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

# Known per-model supported effort values. Generic validation must use the
# selected model's actual values rather than assuming seeded xhigh applies
# everywhere (MoonLadderStudios/MoonMind#4021).
KNOWN_MODEL_EFFORTS: dict[str, tuple[str, ...]] = {
    DEFAULT_OPENCODE_QUALIFIED: ("minimal", "low", "medium", "high", "xhigh"),
    ZEN_FREE_QUALIFIED: ("minimal", "low", "medium", "high", "xhigh"),
}


def get_supported_efforts(qualified_id: str) -> tuple[str, ...] | None:
    """Return the known supported efforts for a qualified model, if known."""
    return KNOWN_MODEL_EFFORTS.get(qualified_id.strip())


# Friendly name normalization: case-insensitive, punctuation-insensitive.
# Display-only: friendly labels and aliases may help display or classified
# historical loading, but punctuation-normalized collisions must never choose
# a different model/provider for execution (MoonLadderStudios/MoonMind#4021).
# Execution selection uses resolve_model_exact below.
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


def _route_label(qualified_or_display: str) -> str:
    text = qualified_or_display.strip()
    provider_prefix = text.split("/", 1)[0].strip() if "/" in text else ""
    if (
        text == ZEN_FREE_QUALIFIED
        or provider_prefix == "opencode"
        or normalize_model_display(text)
        in {
            normalize_model_display(ZEN_FREE_MODEL_DISPLAY),
            normalize_model_display(ZEN_FREE_QUALIFIED),
        }
    ):
        return "the credentialless opencode-zen-free route"
    return "this OpenCode Go account"


def resolve_model_exact(
    qualified_id: str,
    available_models: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Resolve one exact qualified model ID for execution selection.

    MoonLadderStudios/MoonMind#4021: execution selection uses exact IDs only.
    Punctuation-normalized collisions, friendly labels, catalog presence of a
    different ID, a name containing "free", a successful list request, or an
    installed binary never qualifies a model. ``available_models`` is required
    (the exact runtime/provider catalog); a missing catalog fails closed.
    """
    wanted = qualified_id.strip()
    if not wanted or "/" not in wanted:
        raise ValueError(
            f"Requested model {qualified_id!r} is unavailable: execution selection "
            "requires an exact qualified model ID (provider/model)."
        )
    if available_models is None:
        raise ValueError(
            f"Requested model {wanted!r} is unavailable: no exact catalog is "
            "available, so eligibility cannot be established."
        )
    for entry in available_models:
        qid = str(entry.get("qualifiedId") or "").strip()
        if qid == wanted:
            provider_id = qid.split("/", 1)[-1]
            return {
                "displayName": str(entry.get("displayName") or qid),
                "providerModelId": provider_id,
                "qualifiedId": qid,
            }
    alternatives = ", ".join(
        str(m.get("qualifiedId") or "") for m in available_models[:5]
    )
    raise ValueError(
        f"Requested model {wanted!r} is unavailable for {_route_label(wanted)}. "
        f"Available: {alternatives or 'none'}"
    )


def resolve_model_by_display(
    display: str,
    available_models: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Resolve friendly display name to provider and qualified IDs.

    Display-only helper: friendly labels and aliases may help display or
    classified historical loading. Execution selection must use
    :func:`resolve_model_exact` with the exact catalog ID instead of relying
    on punctuation-insensitive alias collisions.

    If available_models is provided (from live catalog), verify existence
    with an exact qualified-ID match. Otherwise, use alias table.
    """
    normalized = normalize_model_display(display)
    alias = _MODEL_ALIASES.get(normalized)
    if alias is None:
        # Try to find in available list by exact qualified ID only. A
        # punctuation-normalized collision must never select a different
        # model/provider for execution.
        if available_models:
            wanted = display.strip()
            for m in available_models:
                qid = str(m.get("qualifiedId") or "").strip()
                if qid == wanted:
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
            and provider_prefix == "opencode-go"
            and provider_model_id
        ):
            # Bootstrap resolves images before live credential-scoped model
            # validation. Preserve a current keyed Provider Profile's canonical
            # model identity here; qualification still fails closed if the
            # later live catalog does not contain it. The credentialless
            # opencode/* route is never preserved pre-validation: it must come
            # from the exact observed catalog (MoonLadderStudios/MoonMind#4021).
            return {
                "displayName": qualified,
                "providerModelId": provider_model_id,
                "qualifiedId": qualified,
            }
        raise ValueError(
            f"Requested model {display!r} is unavailable for {_route_label(display)}"
        )
    # If catalog available, verify alias exists in catalog via exact match.
    if available_models is not None:
        qualified = alias["qualifiedId"]
        if not any(str(m.get("qualifiedId") or "") == qualified for m in available_models):
            # Provide live alternatives in error detail
            alternatives = ", ".join(str(m.get("qualifiedId") or "") for m in available_models[:5])
            raise ValueError(
                f"Requested model {display!r} is unavailable for {_route_label(display)}. "
                f"Available: {alternatives or 'none'}"
            )
    return alias


def resolve_bootstrap_model(
    display: str,
    available_models: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Resolve one bootstrap model through the coordinated first-result path.

    MoonLadderStudios/MoonMind#4021 req-1/req-3: the credentialless
    ``opencode/*`` route never resolves pre-validation. A qualified
    ``opencode/<model>`` request requires the exact observed catalog and an
    exact qualified-ID match via :func:`resolve_model_exact`; otherwise it
    fails closed with an actionable error instead of silently substituting.
    Display aliases remain valid only for the keyed ``opencode-go`` route and
    for historical/display loading, never for credentialless execution
    selection.
    """
    text = display.strip()
    if "/" in text and text.split("/", 1)[0].strip() == "opencode":
        # Credentialless route: exact catalog match is mandatory, even when
        # the alias table happens to contain the seeded ID. This gates the
        # obsolete pre-validation display path for this route.
        return resolve_model_exact(text, available_models)
    return resolve_model_by_display(display, available_models)


def validate_effort(effort: str, available_efforts: list[str] | None = None) -> str:
    normalized = effort.strip().lower()
    allowed = {"minimal", "low", "medium", "high", "xhigh"}
    if normalized not in allowed:
        raise ValueError(f"effort {effort!r} is not supported")
    if available_efforts is not None and normalized not in {e.lower() for e in available_efforts}:
        raise ValueError(f"effort {effort!r} is not supported by the selected model")
    return normalized


def validate_effort_for_model(effort: str, qualified_id: str) -> str:
    """Validate effort against the selected model's actual supported values.

    MoonLadderStudios/MoonMind#4021: the seeded xhigh default must not be
    assumed for every model. Known models use their recorded values; unknown
    models fall back to the generic set and still fail closed on unknown
    effort names.
    """
    supported = get_supported_efforts(qualified_id)
    if supported is None:
        return validate_effort(effort)
    return validate_effort(effort, available_efforts=list(supported))
