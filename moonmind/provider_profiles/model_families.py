"""Provider Profile model-family policy: built-in families and resolution.

A family tier (``family`` + ``model_mode``) is authored policy on the Provider
Profile tier contract (:mod:`moonmind.provider_profiles.model_tiers`). This
module owns the small amount of provider knowledge needed to turn that policy
into a concrete model for one profile route:

* which concrete IDs belong to a family (strict ID shapes, so specialized
  lines, previews, and context variants never count as the family);
* how releases order (major/minor version, not timestamps or lexicographic
  IDs); and
* which observed catalog rows the selected route can actually use.

Catalog rows come from the selected profile's persisted observations. Without
usable observations a built-in family resolves to its cold-start placeholder,
which is reported as unverified rather than as an observed release.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "FAMILY_MODE_AUTOMATIC",
    "FAMILY_MODE_PINNED",
    "FamilyResolution",
    "ModelFamily",
    "built_in_family",
    "catalog_lists_family",
    "catalog_rows_from_evidence",
    "family_for_model",
    "first_party_family_tier_template",
    "is_first_party_family_route",
    "pinned_model_status",
    "resolve_family_model",
]

FAMILY_MODE_AUTOMATIC = "automatic"
FAMILY_MODE_PINNED = "pinned"

# Resolution status values exposed to admission, previews, and the editor.
STATUS_RESOLVED = "resolved"
STATUS_UNVERIFIED = "unverified"
STATUS_UNAVAILABLE = "unavailable"

SOURCE_PROFILE_CATALOG = "profile_catalog"
SOURCE_COLD_START = "cold_start_placeholder"

_FIRST_PARTY_EFFORT = "medium"
_FIRST_PARTY_DEFAULT_TIER = 2

_INELIGIBLE_STATUSES = frozenset(
    {
        "deprecated",
        "retired",
        "unavailable",
        "disabled",
        "preview",
        "beta",
        "experimental",
    }
)


@dataclass(frozen=True)
class ModelFamily:
    """One built-in model family a tier can follow."""

    family_id: str
    provider_id: str
    display_name: str
    cold_start_model: str


_ANTHROPIC_PATTERNS = (
    # claude-opus-5-5, claude-opus-5, claude-sonnet-4-5-20250929
    re.compile(
        r"^claude-(?P<family>[a-z]+)-(?P<major>\d+)(?:-(?P<minor>\d{1,2}))?"
        r"(?:-(?P<date>\d{8}))?$"
    ),
    # Earlier naming: claude-3-5-sonnet-20241022, claude-3-opus-20240229
    re.compile(
        r"^claude-(?P<major>\d+)(?:-(?P<minor>\d{1,2}))?-(?P<family>[a-z]+)"
        r"(?:-(?P<date>\d{8}))?$"
    ),
)
_OPENAI_PATTERNS = (
    # gpt-6.1-sol, gpt-6-luna, gpt-6.1-terra-2026-11-05
    re.compile(
        r"^gpt-(?P<major>\d+)(?:\.(?P<minor>\d+))?-(?P<family>[a-z]+)"
        r"(?:-(?P<date>\d{4}-\d{2}-\d{2}))?$"
    ),
)
_PATTERNS_BY_PROVIDER: dict[str, tuple[re.Pattern[str], ...]] = {
    "anthropic": _ANTHROPIC_PATTERNS,
    "openai": _OPENAI_PATTERNS,
}

# Cold-start placeholders are the releases verified when these defaults were
# introduced. They only stand in until the selected route reports a catalog.
_BUILT_IN_FAMILIES: tuple[ModelFamily, ...] = (
    ModelFamily("astra", "openai", "Astra", "gpt-6-astra"),
    ModelFamily("sol", "openai", "Sol", "gpt-6.1-sol"),
    ModelFamily("terra", "openai", "Terra", "gpt-6.1-terra"),
    ModelFamily("luna", "openai", "Luna", "gpt-6-luna"),
    ModelFamily("fable", "anthropic", "Fable", "claude-fable-5-1"),
    ModelFamily("opus", "anthropic", "Opus", "claude-opus-5-5"),
    ModelFamily("sonnet", "anthropic", "Sonnet", "claude-sonnet-5-5"),
    ModelFamily("haiku", "anthropic", "Haiku", "claude-haiku-5-5"),
)
_FAMILIES_BY_ID = {family.family_id: family for family in _BUILT_IN_FAMILIES}

# The two first-party built-ins: OpenAI through Codex, Anthropic through
# Claude Code. Tier order is the template order; Tier 2 is the default.
_FIRST_PARTY_TEMPLATES: dict[tuple[str, str], tuple[str, ...]] = {
    ("codex_cli", "openai"): ("astra", "sol", "terra", "luna"),
    ("claude_code", "anthropic"): ("fable", "opus", "sonnet", "haiku"),
}


def built_in_family(family_id: Any) -> ModelFamily | None:
    """Return the built-in family for an id, or None when it is unknown."""

    return _FAMILIES_BY_ID.get(str(family_id or "").strip().lower())


def is_first_party_family_route(runtime_id: Any, provider_id: Any) -> bool:
    """Whether a runtime/provider pair receives the built-in family template."""

    key = (str(runtime_id or "").strip(), str(provider_id or "").strip())
    return key in _FIRST_PARTY_TEMPLATES


def first_party_family_tier_template(
    runtime_id: Any, provider_id: Any
) -> tuple[list[dict[str, Any]], int] | None:
    """Return the built-in automatic family tiers and default tier, if any."""

    key = (str(runtime_id or "").strip(), str(provider_id or "").strip())
    family_ids = _FIRST_PARTY_TEMPLATES.get(key)
    if family_ids is None:
        return None
    tiers = [
        {
            "label": _FAMILIES_BY_ID[family_id].display_name,
            "family": family_id,
            "model_mode": FAMILY_MODE_AUTOMATIC,
            "model": None,
            "effort": _FIRST_PARTY_EFFORT,
            "parameters": {},
            "annotations": {},
        }
        for family_id in family_ids
    ]
    return tiers, _FIRST_PARTY_DEFAULT_TIER


@dataclass(frozen=True, order=True)
class _Release:
    major: int
    minor: int
    undated: int
    date: str


def _model_basename(model_id: str) -> str:
    """Drop a provider qualifier (``anthropic/claude-opus-5``) for matching."""

    return model_id.rsplit("/", 1)[-1].strip().lower()


def _parse_release(model_id: str, family: ModelFamily) -> _Release | None:
    basename = _model_basename(model_id)
    for pattern in _PATTERNS_BY_PROVIDER.get(family.provider_id, ()):
        match = pattern.match(basename)
        if match is None or match.group("family") != family.family_id:
            continue
        date = match.group("date") or ""
        return _Release(
            major=int(match.group("major")),
            minor=int(match.group("minor") or 0),
            undated=0 if date else 1,
            date=date,
        )
    return None


def family_for_model(model_id: Any, provider_id: Any = None) -> ModelFamily | None:
    """Return the built-in family a concrete model ID belongs to, if any."""

    text = str(model_id or "").strip()
    if not text:
        return None
    provider = str(provider_id or "").strip() or None
    for family in _BUILT_IN_FAMILIES:
        if provider is not None and family.provider_id != provider:
            continue
        if _parse_release(text, family) is not None:
            return family
    return None


def _row_model_id(row: Mapping[str, Any]) -> str:
    for key in ("qualifiedId", "qualified_id", "model", "id", "value"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _row_efforts(row: Mapping[str, Any]) -> frozenset[str] | None:
    for key in (
        "supportedReasoningEfforts",
        "supportedEfforts",
        "supported_efforts",
        "efforts",
    ):
        raw = row.get(key)
        if not isinstance(raw, list):
            continue
        efforts = set()
        for item in raw:
            if isinstance(item, Mapping):
                item = (
                    item.get("reasoningEffort") or item.get("value") or item.get("id")
                )
            text = str(item or "").strip().lower()
            if text:
                efforts.add(text)
        return frozenset(efforts)
    return None


def _row_status(row: Mapping[str, Any]) -> str:
    status = str(row.get("status") or row.get("lifecycle") or "").strip().lower()
    if row.get("disabled") is True:
        return "disabled"
    return status or "available"


def _row_declared_family(row: Mapping[str, Any]) -> str | None:
    """Return authoritative family metadata (for example Anthropic ``line``)."""

    for key in ("family", "line", "modelFamily", "model_family"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return re.split(r"[\s_-]+", value.strip().lower())[-1]
    return None


def catalog_rows_from_evidence(evidence: Any) -> list[dict[str, Any]] | None:
    """Return observed catalog rows from persisted profile evidence.

    Returns None when the evidence carries no usable model list so callers can
    distinguish "never observed" from "observed, family absent".
    """

    if not isinstance(evidence, Mapping):
        return None
    raw_models = evidence.get("models")
    if not isinstance(raw_models, list):
        return None
    rows = [
        dict(item)
        for item in raw_models
        if isinstance(item, Mapping) and _row_model_id(item)
    ]
    return rows or None


def catalog_lists_family(
    family_id: Any, catalog_rows: Iterable[Mapping[str, Any]] | None
) -> bool:
    """Report whether any observed row is a release of this family, eligible or not."""

    family = built_in_family(family_id)
    if family is None:
        return False
    return any(
        _parse_release(_row_model_id(row), family) is not None
        and _row_declared_family(row) in {None, family.family_id}
        for row in catalog_rows or ()
    )


@dataclass(frozen=True)
class FamilyResolution:
    """The concrete model one automatic family tier resolves to."""

    family: str
    model: str | None
    status: str
    source: str | None
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.model is not None and self.status != STATUS_UNAVAILABLE

    def as_metadata(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "model": self.model,
            "status": self.status,
            "source": self.source,
            "reason": self.reason,
        }


def _eligible_releases(
    family: ModelFamily,
    rows: Iterable[Mapping[str, Any]],
    *,
    effort: str | None,
) -> tuple[list[tuple[_Release, str]], bool]:
    """Return eligible (release, model_id) pairs and whether effort excluded any."""

    eligible: list[tuple[_Release, str]] = []
    excluded_by_effort = False
    wanted_effort = str(effort or "").strip().lower() or None
    for row in rows:
        model_id = _row_model_id(row)
        declared = _row_declared_family(row)
        if declared is not None and declared != family.family_id:
            continue
        release = _parse_release(model_id, family)
        if release is None:
            continue
        if _row_status(row) in _INELIGIBLE_STATUSES or row.get("hidden") is True:
            continue
        efforts = _row_efforts(row)
        if wanted_effort and efforts is not None and wanted_effort not in efforts:
            excluded_by_effort = True
            continue
        eligible.append((release, model_id))
    return eligible, excluded_by_effort


def resolve_family_model(
    family_id: Any,
    *,
    provider_id: Any,
    catalog_rows: Sequence[Mapping[str, Any]] | None,
    effort: str | None = None,
) -> FamilyResolution:
    """Resolve an automatic family tier against one profile's catalog.

    Picks the newest eligible release of exactly this family. Never crosses
    into another family or provider, and never orders by timestamp alone.
    """

    family = built_in_family(family_id)
    family_text = str(family_id or "").strip().lower()
    if family is None:
        return FamilyResolution(
            family=family_text,
            model=None,
            status=STATUS_UNAVAILABLE,
            source=None,
            reason=f"Model family {family_text!r} is not recognized.",
        )
    provider = str(provider_id or "").strip()
    qualified_route = bool(catalog_rows) and any(
        "/" in _row_model_id(row) for row in catalog_rows or ()
    )
    if provider and provider != family.provider_id and not qualified_route:
        return FamilyResolution(
            family=family.family_id,
            model=None,
            status=STATUS_UNAVAILABLE,
            source=None,
            reason=(
                f"{family.display_name} is a {family.provider_id} family; this "
                f"profile uses provider {provider!r}."
            ),
        )
    if not catalog_rows:
        if provider and provider != family.provider_id:
            return FamilyResolution(
                family=family.family_id,
                model=None,
                status=STATUS_UNAVAILABLE,
                source=None,
                reason="This profile has not reported a model catalog yet.",
            )
        return FamilyResolution(
            family=family.family_id,
            model=family.cold_start_model,
            status=STATUS_UNVERIFIED,
            source=SOURCE_COLD_START,
            reason=(
                "This profile has not reported a model catalog yet; using the "
                "built-in starting release until it does."
            ),
        )
    eligible, excluded_by_effort = _eligible_releases(
        family, catalog_rows, effort=effort
    )
    if not eligible:
        reason = (
            f"No {family.display_name} release in this profile's catalog "
            f"supports effort {effort!r}."
            if excluded_by_effort
            else f"No eligible {family.display_name} release is available for "
            "this profile."
        )
        return FamilyResolution(
            family=family.family_id,
            model=None,
            status=STATUS_UNAVAILABLE,
            source=SOURCE_PROFILE_CATALOG,
            reason=reason,
        )
    _, model_id = max(eligible, key=lambda pair: pair[0])
    return FamilyResolution(
        family=family.family_id,
        model=model_id,
        status=STATUS_RESOLVED,
        source=SOURCE_PROFILE_CATALOG,
    )


def pinned_model_status(
    model_id: Any, *, catalog_rows: Sequence[Mapping[str, Any]] | None
) -> str:
    """Report a pinned model's observed status without changing the pin.

    ``unverified`` means no catalog has been observed for the route; a pin
    missing from an observed catalog is ``unavailable``. Lifecycle statuses
    such as ``deprecated`` or ``retired`` pass through.
    """

    text = str(model_id or "").strip()
    if not catalog_rows:
        return STATUS_UNVERIFIED
    for row in catalog_rows:
        if _row_model_id(row) == text:
            status = _row_status(row)
            return "available" if status in {"available", "active", "ga"} else status
    return STATUS_UNAVAILABLE
