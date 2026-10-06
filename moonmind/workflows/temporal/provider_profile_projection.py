"""Recorded Provider Profile projection for the Workflows list.

MoonLadderStudios/MoonMind#4640: the ordinary Workflows list shows the
workflow's *recorded* Provider Profile selection, not a live current-agent
indicator or today's profile inventory. Admission snapshots a compact
selection block (stable IDs plus small display metadata) into the workflow
parameters; this module is the single owner that

* builds that admission snapshot,
* summarizes recorded selection from parameters, including historical rows
  that only carry IDs, and
* encodes the summary into the ``mm_provider_profile`` Search Attribute that
  the list, count, and facet queries filter on before pagination.

``mm_provider_profile`` is a ``Text`` Search Attribute because the SQL
Visibility ``Keyword`` and ``KeywordList`` slots are fully allocated. Text
``=`` / ``!=`` match whole lexemes, so the value is a space-separated set of
lowercase alphanumeric tokens: one state token and one hex-encoded token per
recorded profile ID. Those tokens are safe for the PostgreSQL
``tsvector``/``tsquery`` casts and the SQLite FTS tokenizer.

Historical workflows started before this projection have no attribute.
Missing coverage is unavailable information, never a known absence state.

Keep this module dependency-free (standard library only).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

PROVIDER_PROFILE_SEARCH_ATTRIBUTE = "mm_provider_profile"
PROVIDER_PROFILE_SELECTION_PARAMETER = "providerProfileSelection"

SELECTION_STATE_RECORDED = "recorded"
SELECTION_STATE_PENDING = "pending"
SELECTION_STATE_NOT_RECORDED = "not_recorded"
SELECTION_STATE_NOT_APPLICABLE = "not_applicable"
PROVIDER_PROFILE_SELECTION_STATES = (
    SELECTION_STATE_RECORDED,
    SELECTION_STATE_PENDING,
    SELECTION_STATE_NOT_RECORDED,
    SELECTION_STATE_NOT_APPLICABLE,
)
PROVIDER_PROFILE_ABSENCE_STATES = (
    SELECTION_STATE_PENDING,
    SELECTION_STATE_NOT_RECORDED,
    SELECTION_STATE_NOT_APPLICABLE,
)

# Bounds keep the admitted snapshot, list rows, and the Search Attribute small.
MAX_RECORDED_PROFILES = 32
MAX_PROFILE_ID_LENGTH = 255
MAX_PROFILE_LABEL_LENGTH = 120
MAX_HARNESS_LENGTH = 64
# Temporal rejects Search Attribute values over 2 KiB by default, which would
# fail workflow start. IDs past this bound stay in the recorded summary but are
# not indexed for filtering.
MAX_SEARCH_ATTRIBUTE_LENGTH = 2000

_STATE_TOKEN_PREFIX = "ppstate"
_ID_TOKEN_PREFIX = "ppid"


@dataclass(frozen=True)
class RecordedProviderProfile:
    """One recorded Provider Profile association."""

    profile_id: str
    label: str | None = None
    harness: str | None = None


@dataclass(frozen=True)
class ProviderProfileSummary:
    """Compact recorded selection summary for one workflow."""

    selection_state: str
    profiles: tuple[RecordedProviderProfile, ...] = ()


def _bounded_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _dedupe(profiles: Iterable[RecordedProviderProfile]) -> tuple[RecordedProviderProfile, ...]:
    seen: dict[str, RecordedProviderProfile] = {}
    for profile in profiles:
        profile_id = _bounded_text(profile.profile_id, MAX_PROFILE_ID_LENGTH)
        if not profile_id:
            continue
        existing = seen.get(profile_id)
        normalized = RecordedProviderProfile(
            profile_id=profile_id,
            label=_bounded_text(profile.label, MAX_PROFILE_LABEL_LENGTH),
            harness=_bounded_text(profile.harness, MAX_HARNESS_LENGTH),
        )
        if existing is None:
            if len(seen) >= MAX_RECORDED_PROFILES:
                continue
            seen[profile_id] = normalized
        elif existing.label is None and normalized.label is not None:
            # A later association may carry the display snapshot the first
            # reference lacked; keep the stable ID and first-seen order.
            seen[profile_id] = RecordedProviderProfile(
                profile_id=profile_id,
                label=normalized.label,
                harness=existing.harness or normalized.harness,
            )
    return tuple(seen.values())


def build_provider_profile_selection(
    *,
    profiles: Iterable[RecordedProviderProfile],
    agent_applicable: bool,
) -> dict[str, Any]:
    """Return the admission snapshot recorded in workflow parameters.

    ``agent_applicable`` distinguishes an unresolved agent selection
    (``pending``) from work where no agent profile applies
    (``not_applicable``) when no profile ID is known at admission.
    """

    recorded = _dedupe(profiles)
    if recorded:
        state = SELECTION_STATE_RECORDED
    elif agent_applicable:
        state = SELECTION_STATE_PENDING
    else:
        state = SELECTION_STATE_NOT_APPLICABLE
    return provider_profile_summary_payload(ProviderProfileSummary(state, recorded))


def provider_profile_summary_payload(summary: ProviderProfileSummary) -> dict[str, Any]:
    """Return the compact JSON form of a summary (snapshot and memo shape)."""

    items: list[dict[str, str]] = []
    for profile in summary.profiles:
        item = {"id": profile.profile_id}
        if profile.label:
            item["label"] = profile.label
        if profile.harness:
            item["harness"] = profile.harness
        items.append(item)
    return {"state": summary.selection_state, "profiles": items}


def _summary_from_snapshot(raw: object) -> ProviderProfileSummary | None:
    snapshot = _mapping(raw)
    state = snapshot.get("state")
    raw_profiles = snapshot.get("profiles")
    if state not in PROVIDER_PROFILE_SELECTION_STATES or not isinstance(
        raw_profiles, list
    ):
        return None
    profiles = _dedupe(
        RecordedProviderProfile(
            profile_id=item.get("id"),
            label=item.get("label"),
            harness=item.get("harness"),
        )
        for item in raw_profiles
        if isinstance(item, Mapping)
    )
    if profiles:
        return ProviderProfileSummary(SELECTION_STATE_RECORDED, profiles)
    if state == SELECTION_STATE_RECORDED:
        return None
    return ProviderProfileSummary(state, ())


def _historical_profile_ids(parameters: Mapping[str, Any]) -> list[str]:
    # Only fields that admission records as validated *Provider Profile* IDs.
    # ``agentProfile.profileId`` and ``agentProfileSnapshot.profileId`` are the
    # execution-configuration identity and are deliberately never read here.
    ids: list[str] = [
        parameters.get("profileId"),
        parameters.get("providerProfileRef"),
        _mapping(parameters.get("agentProfileSnapshot")).get("providerProfileRef"),
    ]
    workflow_payload = _mapping(parameters.get("workflow")) or _mapping(
        parameters.get("task")
    )
    steps = workflow_payload.get("steps")
    if isinstance(steps, list):
        for step in steps:
            runtime = _mapping(_mapping(step).get("runtime"))
            ids.append(runtime.get("providerProfileRef"))
    return [value for value in ids if isinstance(value, str) and value.strip()]


def summarize_recorded_provider_profiles(
    parameters: Mapping[str, Any] | None,
) -> ProviderProfileSummary:
    """Summarize the workflow's recorded Provider Profile selection."""

    params = _mapping(parameters)
    summary = _summary_from_snapshot(params.get(PROVIDER_PROFILE_SELECTION_PARAMETER))
    if summary is not None:
        return summary
    profiles = _dedupe(
        RecordedProviderProfile(profile_id=profile_id)
        for profile_id in _historical_profile_ids(params)
    )
    if profiles:
        return ProviderProfileSummary(SELECTION_STATE_RECORDED, profiles)
    return ProviderProfileSummary(SELECTION_STATE_NOT_RECORDED, ())


def provider_profile_state_token(state: str) -> str:
    """Return the Search Attribute token for one selection state."""

    if state not in PROVIDER_PROFILE_SELECTION_STATES:
        raise ValueError(f"Unknown Provider Profile selection state: {state!r}.")
    return _STATE_TOKEN_PREFIX + state.replace("_", "")


def provider_profile_id_token(profile_id: str) -> str:
    """Return the Search Attribute token for one stable profile ID."""

    return _ID_TOKEN_PREFIX + profile_id.encode("utf-8").hex()


def provider_profile_search_attribute_value(summary: ProviderProfileSummary) -> str:
    """Encode a summary as the ``mm_provider_profile`` Text value."""

    value = provider_profile_state_token(summary.selection_state)
    for profile in summary.profiles:
        token = provider_profile_id_token(profile.profile_id)
        if len(value) + 1 + len(token) > MAX_SEARCH_ATTRIBUTE_LENGTH:
            break
        value = f"{value} {token}"
    return value


def provider_profile_ids_from_search_attribute(value: object) -> list[str]:
    """Decode recorded profile IDs from an ``mm_provider_profile`` value."""

    if isinstance(value, (list, tuple)):
        value = " ".join(str(item) for item in value if isinstance(item, str))
    if not isinstance(value, str):
        return []
    ids: list[str] = []
    for token in value.split():
        if not token.startswith(_ID_TOKEN_PREFIX):
            continue
        try:
            profile_id = bytes.fromhex(token[len(_ID_TOKEN_PREFIX) :]).decode("utf-8")
        except ValueError:
            continue
        if profile_id and profile_id not in ids:
            ids.append(profile_id)
    return ids
