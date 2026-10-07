"""Compact recorded Provider Profile projection for the Workflows list.

MoonLadderStudios/MoonMind#4640: the ordinary Workflows list shows the
workflow's *recorded* Provider Profile selection instead of the legacy runtime
identifier. Admission records one bounded summary in the workflow memo (for
display) and one ``mm_provider_profile`` Text Search Attribute (for filtering,
counts, and facets before pagination). Both derive from the same admitted
parameters, so rows, filters, counts, and facets share one projection.

Temporal SQL Visibility allows only three ``KeywordList`` attributes and all
three are in use, so membership is stored as space-separated opaque tokens in a
``Text`` attribute. Tokens are lowercase alphanumeric so every Visibility store
matches them as exact single terms; profile IDs are hashed rather than embedded
so arbitrary ID characters cannot alter full-text query syntax.

The summary never stores credentials, OAuth paths, raw provider payloads, or
host/container handles: only stable Provider Profile IDs, a small display-name
snapshot, and the optional Harness id.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Literal

PROVIDER_PROFILE_SEARCH_ATTRIBUTE = "mm_provider_profile"
PROVIDER_PROFILE_MEMO_KEY = "providerProfile"
PROVIDER_PROFILE_SUMMARY_LIMIT = 8
_LABEL_LIMIT = 120

ProviderProfileSelectionState = Literal[
    "recorded", "pending", "not_recorded", "not_applicable"
]
PROVIDER_PROFILE_SELECTION_STATES: tuple[str, ...] = (
    "recorded",
    "pending",
    "not_recorded",
    "not_applicable",
)
# Absence states the list filter exposes as typed values. ``not_recorded`` is
# never written: it is the honest state of records that predate the projection
# (no memo summary and no Search Attribute).
PROVIDER_PROFILE_ABSENCE_STATES: tuple[str, ...] = (
    "pending",
    "not_recorded",
    "not_applicable",
)
_WRITTEN_STATES = frozenset({"recorded", "pending", "not_applicable"})
_ID_TOKEN_PREFIX = "ppid"
_STATE_TOKEN_PREFIX = "ppst"


def provider_profile_id_token(profile_id: str) -> str:
    """Return the opaque Visibility token for one stable Provider Profile ID."""

    digest = hashlib.sha256(str(profile_id).encode("utf-8")).hexdigest()
    return f"{_ID_TOKEN_PREFIX}{digest[:40]}"


def provider_profile_state_token(state: str) -> str:
    """Return the Visibility token for one written selection state."""

    if state not in _WRITTEN_STATES:
        raise ValueError(f"Provider Profile state {state!r} is never recorded.")
    return f"{_STATE_TOKEN_PREFIX}{state.replace('_', '')}"


def _text(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _workflow_payload(parameters: Mapping[str, Any]) -> Mapping[str, Any]:
    workflow = parameters.get("workflow")
    if isinstance(workflow, Mapping):
        return workflow
    return _mapping(parameters.get("task"))


def _snapshot_harness(snapshot: Mapping[str, Any]) -> str | None:
    document = _mapping(snapshot.get("document"))
    return _text(_mapping(document.get("harness")).get("id")) or _text(
        snapshot.get("harnessId")
    )


def recorded_provider_profile_ids(
    parameters: Mapping[str, Any] | None,
) -> list[tuple[str, str | None]]:
    """Return ``(profile_id, harness)`` pairs from admitted parameters.

    Only trusted admitted selection is used: the agent-profile snapshot's
    ``providerProfileRef`` (or the top-level ``profileId`` admission sets to
    the same Provider Profile ID) plus explicit or resolved step-level Provider
    Profile refs. Execution-configuration ``profileId`` values, runtime ids,
    models, and current defaults never establish an association.
    """

    if not isinstance(parameters, Mapping):
        return []
    ordered: dict[str, str | None] = {}
    snapshot = _mapping(parameters.get("agentProfileSnapshot"))
    workflow_profile = _text(snapshot.get("providerProfileRef")) or _text(
        parameters.get("profileId")
    )
    if workflow_profile:
        ordered[workflow_profile] = _snapshot_harness(snapshot)
    steps = _workflow_payload(parameters).get("steps")
    if isinstance(steps, list):
        for step in steps:
            runtime = _mapping(_mapping(step).get("runtime"))
            step_profile = _text(runtime.get("providerProfileRef")) or _text(
                runtime.get("providerProfile")
            )
            if step_profile and step_profile not in ordered:
                ordered[step_profile] = None
    return list(ordered.items())


def _agent_applies(parameters: Mapping[str, Any]) -> bool:
    if _text(parameters.get("targetRuntime")):
        return True
    workflow = _workflow_payload(parameters)
    if _text(_mapping(workflow.get("runtime")).get("mode")):
        return True
    steps = workflow.get("steps")
    if isinstance(steps, list):
        return any(
            _text(_mapping(_mapping(step).get("runtime")).get("mode"))
            for step in steps
        )
    return False


def build_provider_profile_summary(
    parameters: Mapping[str, Any] | None,
    *,
    labels: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Build the bounded memo summary from admitted parameters.

    ``labels`` is the display-name snapshot captured at admission; IDs without a
    label keep ``label=None`` so readers fall back to the stable ID instead of a
    guessed former name.
    """

    params = parameters if isinstance(parameters, Mapping) else {}
    recorded = recorded_provider_profile_ids(params)
    label_map = labels or {}
    profiles = []
    for profile_id, harness in recorded[:PROVIDER_PROFILE_SUMMARY_LIMIT]:
        label = _text(label_map.get(profile_id))
        entry: dict[str, Any] = {"id": profile_id}
        if label:
            entry["label"] = label[:_LABEL_LIMIT]
        if harness:
            entry["harness"] = harness
        profiles.append(entry)
    if recorded:
        state = "recorded"
    elif _agent_applies(params):
        state = "pending"
    else:
        state = "not_applicable"
    return {
        "selectionState": state,
        "profiles": profiles,
        "profileCount": len(recorded),
    }


def build_provider_profile_projection(
    parameters: Mapping[str, Any] | None,
    *,
    labels: Mapping[str, str | None] | None = None,
) -> tuple[dict[str, Any], str]:
    """Return ``(memo_summary, search_attribute_value)`` for admission.

    Every recorded ID is indexed, including IDs beyond the display bound, so
    membership filters stay complete even when the memo summary is truncated.
    """

    summary = build_provider_profile_summary(parameters, labels=labels)
    tokens = [provider_profile_state_token(summary["selectionState"])]
    for profile_id, _harness in recorded_provider_profile_ids(parameters):
        tokens.append(provider_profile_id_token(profile_id))
    return summary, " ".join(tokens)


def merge_resolved_provider_profile(
    summary: Mapping[str, Any] | None,
    search_value: str | None,
    profile_id: str | None,
    *,
    label: str | None = None,
) -> tuple[dict[str, Any], str] | None:
    """Fold one launch-resolved Provider Profile into the admitted projection.

    Admission records ``pending`` when no Provider Profile was selected yet; the
    workflow folds in the profile its agent launch actually used so rows,
    filters, and facets stop reporting an unresolved selection. Returns
    ``None`` when nothing changes, including when no admission projection
    exists (historical records stay ``not_recorded``). ``search_value`` is the
    current ``mm_provider_profile`` value, which indexes IDs beyond the memo
    display bound.
    """

    raw = _mapping(summary)
    resolved_id = _text(profile_id)
    if not resolved_id or raw.get("selectionState") not in _WRITTEN_STATES:
        return None
    profiles = [
        dict(entry)
        for entry in raw.get("profiles") or []
        if isinstance(entry, Mapping) and _text(entry.get("id"))
    ][:PROVIDER_PROFILE_SUMMARY_LIMIT]
    id_tokens = [
        token
        for token in (search_value or "").split()
        if not token.startswith(_STATE_TOKEN_PREFIX)
    ]
    if not id_tokens:
        id_tokens = [provider_profile_id_token(entry["id"]) for entry in profiles]
    resolved_token = provider_profile_id_token(resolved_id)
    if raw.get("selectionState") == "recorded" and resolved_token in id_tokens:
        return None
    count = raw.get("profileCount")
    if isinstance(count, bool) or not isinstance(count, int) or count < len(profiles):
        count = len(profiles)
    if resolved_token not in id_tokens:
        id_tokens.append(resolved_token)
        count += 1
        if len(profiles) < PROVIDER_PROFILE_SUMMARY_LIMIT:
            entry: dict[str, Any] = {"id": resolved_id}
            resolved_label = _text(label)
            if resolved_label:
                entry["label"] = resolved_label[:_LABEL_LIMIT]
            profiles.append(entry)
    merged_summary = {
        "selectionState": "recorded",
        "profiles": profiles,
        "profileCount": count,
    }
    tokens = [provider_profile_state_token("recorded"), *id_tokens]
    return merged_summary, " ".join(tokens)


def provider_profile_summary_from_memo(memo: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read the recorded summary for a list row.

    Records that predate the projection report ``not_recorded``; their
    runtime, model, or present defaults never fill the gap.
    """

    raw = _mapping(_mapping(memo).get(PROVIDER_PROFILE_MEMO_KEY))
    state = raw.get("selectionState")
    if state not in _WRITTEN_STATES:
        return {"selectionState": "not_recorded", "profiles": [], "profileCount": 0}
    profiles: list[dict[str, Any]] = []
    raw_profiles = raw.get("profiles")
    if isinstance(raw_profiles, list):
        for item in raw_profiles[:PROVIDER_PROFILE_SUMMARY_LIMIT]:
            entry = _mapping(item)
            profile_id = _text(entry.get("id"))
            if not profile_id:
                continue
            profiles.append(
                {
                    "id": profile_id,
                    "label": _text(entry.get("label")),
                    "harness": _text(entry.get("harness")),
                }
            )
    if state != "recorded":
        return {"selectionState": state, "profiles": [], "profileCount": 0}
    if not profiles:
        return {"selectionState": "not_recorded", "profiles": [], "profileCount": 0}
    count = raw.get("profileCount")
    if isinstance(count, bool) or not isinstance(count, int) or count < len(profiles):
        count = len(profiles)
    return {"selectionState": "recorded", "profiles": profiles, "profileCount": count}
