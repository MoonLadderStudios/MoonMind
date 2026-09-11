"""Schedule health for Temporal schedules with OverlapPolicy=Skip.

MoonLadderStudios/MoonMind#4226: one dead Omnigent turn held
``integration.omnigent.profile_bound_execute`` for six hours while the hourly
schedule kept skipping slots (lifetime ``SkippedOverlap=49``) with no alert.
The operational reconcile path records ``SkippedOverlap`` deltas from the
Temporal schedule description and raises one actionable diagnostic per
schedule once more than N skipped slots are observed; repeats for
the same skipped counter coalesce instead of re-alerting.

Alerting uses two complementary signals so both incident shapes are caught:

- ``delta`` (the per-tick growth of the lifetime counter) alerts
  immediately when a single dead turn drops several slots at once
  (the #4226 shape: 43 -> 49 in one tick);
- ``streak`` (the count of consecutive ticks that each observed new
  skips) alerts when a slow drip of one skipped slot per tick persists
  across ticks (e.g. a 10-minute reconcile watching an hourly schedule).

A first observation with no previously recorded baseline never alerts:
three unrelated historical skips must not fire when first seen. The caller
feeds the returned ``currentCounters``/``currentStreaks`` back as the next
tick's ``previous_counters``/``previous_streaks`` (the reconcile activity
also persists them to a best-effort state file), so successful ticks reset
the streak.

This module is pure (no Temporal imports) so the workflow boundary and the
unit suite can exercise the exact production decision function.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Consecutive skipped slots that raise the actionable diagnostic.
SKIPPED_OVERLAP_DIAGNOSTIC_THRESHOLD = 3

#: Diagnostic code surfaced in the reconcile summary.
SKIPPED_OVERLAP_DIAGNOSTIC_CODE = "SCHEDULE_SKIPPED_OVERLAP"


def _coerce_non_negative_int(value: Any) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def extract_skipped_overlap(description: Any) -> int | None:
    """Extract the lifetime skipped-overlap counter from a schedule description.

    Accepts the Temporal schedule ``describe`` payload in either SDK-object or
    plain-dict form. Returns ``None`` when the counter is absent so callers
    can distinguish "unknown" from "zero".
    """

    candidates: list[Any] = []
    if isinstance(description, Mapping):
        info = description.get("info") or description.get("scheduleInfo")
        if isinstance(info, Mapping):
            for key in (
                "skippedOverlap",
                "skipped_overlap",
                "numSkippedOverlap",
                "num_skipped_overlap",
                # Temporal Python SDK ScheduleDescription.info field and its
                # serialized camel-case form.
                "numActionsSkippedOverlap",
                "num_actions_skipped_overlap",
            ):
                if key in info:
                    candidates.append(info[key])
        for key in (
            "skippedOverlap",
            "skipped_overlap",
            "numSkippedOverlap",
            "num_skipped_overlap",
        ):
            if isinstance(description, Mapping) and key in description:
                candidates.append(description[key])
    else:
        info = getattr(description, "info", None)
        if info is not None:
            for attr in (
                "skipped_overlap",
                "skippedOverlap",
                "num_skipped_overlap",
                "numSkippedOverlap",
                # Temporal Python SDK ScheduleDescription.info field and its
                # serialized camel-case form.
                "num_actions_skipped_overlap",
                "numActionsSkippedOverlap",
            ):
                value = getattr(info, attr, None)
                if value is not None:
                    candidates.append(value)
        for attr in (
            "skipped_overlap",
            "skippedOverlap",
        ):
            value = getattr(description, attr, None)
            if value is not None:
                candidates.append(value)
    for candidate in candidates:
        coerced = _coerce_non_negative_int(candidate)
        if coerced is not None:
            return coerced
    return None


def evaluate_schedule_skipped_overlap(
    *,
    schedule_id: str,
    description: Any,
    previous_skipped: int | None,
    threshold: int = SKIPPED_OVERLAP_DIAGNOSTIC_THRESHOLD,
    last_alerted_skipped: int | None = None,
    previous_streak: int | None = None,
) -> dict[str, Any]:
    """Evaluate one schedule description and decide whether to alert.

    Returns a dict with ``scheduleId``, ``previousSkipped``,
    ``currentSkipped``, ``delta`` (``None`` when either side is unknown),
    ``streak`` (consecutive ticks that each observed new skips),
    ``diagnostic`` (``None`` unless a new alert fires), and ``coalesced``
    (``True`` when the alert condition holds but this exact skipped counter
    already alerted).
    """

    current = extract_skipped_overlap(description)
    previous = _coerce_non_negative_int(previous_skipped)
    last_alerted = _coerce_non_negative_int(last_alerted_skipped)
    prior_streak = _coerce_non_negative_int(previous_streak) or 0
    delta: int | None = None
    streak = 0
    if current is not None and previous is not None:
        delta = max(0, current - previous)
        if delta > 0:
            streak = prior_streak + 1
    # Without a previously recorded baseline this tick only establishes the
    # baseline: a large lifetime counter seen for the first time must not
    # alert, since unrelated historical skips are not consecutive evidence.
    # Likewise an unknown counter or a tick with no new skips resets/keeps
    # the streak at zero and never alerts; successful occurrences reset it.
    diagnostic: dict[str, Any] | None = None
    coalesced = False
    if current is not None and previous is not None and delta is not None and delta > 0:
        # Immediate dead-turn signal (one tick drops several slots) or a
        # persistent slow drip (each tick drops at least one slot).
        if delta >= max(1, int(threshold)) or streak >= max(1, int(threshold)):
            if last_alerted is not None and last_alerted == current:
                coalesced = True
            else:
                diagnostic = {
                    "code": SKIPPED_OVERLAP_DIAGNOSTIC_CODE,
                    "scheduleId": schedule_id,
                    "skippedOverlap": current,
                    "delta": delta,
                    "streak": streak,
                    "threshold": int(threshold),
                    "message": (
                        f"Schedule {schedule_id} skipped {delta} "
                        f"slot(s) this tick (streak={streak}, "
                        f"skippedOverlap={current}); inspect the "
                        "running UserWorkflow turn-start watchdog before the "
                        "next OverlapPolicy=Skip slot is dropped"
                    ),
                }
    return {
        "scheduleId": schedule_id,
        "previousSkipped": previous,
        "currentSkipped": current,
        "delta": delta,
        "streak": streak,
        "diagnostic": diagnostic,
        "coalesced": coalesced,
    }


def evaluate_reconcile_schedules(
    *,
    schedule_descriptions: Mapping[str, Any],
    previous_counters: Mapping[str, Any] | None = None,
    last_alerted: Mapping[str, Any] | None = None,
    previous_streaks: Mapping[str, Any] | None = None,
    threshold: int = SKIPPED_OVERLAP_DIAGNOSTIC_THRESHOLD,
) -> dict[str, Any]:
    """Evaluate every injected schedule description for one reconcile tick."""

    previous_counters = previous_counters or {}
    last_alerted = last_alerted or {}
    previous_streaks = previous_streaks or {}
    evaluated: dict[str, Any] = {}
    diagnostics: list[dict[str, Any]] = []
    coalesced_count = 0
    for schedule_id, description in schedule_descriptions.items():
        outcome = evaluate_schedule_skipped_overlap(
            schedule_id=str(schedule_id),
            description=description,
            previous_skipped=previous_counters.get(str(schedule_id)),
            threshold=threshold,
            last_alerted_skipped=last_alerted.get(str(schedule_id)),
            previous_streak=previous_streaks.get(str(schedule_id)),
        )
        evaluated[str(schedule_id)] = outcome
        if outcome["diagnostic"] is not None:
            diagnostics.append(outcome["diagnostic"])
        if outcome["coalesced"]:
            coalesced_count += 1
    return {
        "evaluated": evaluated,
        "diagnostics": diagnostics,
        "coalesced": coalesced_count,
        "currentCounters": {
            schedule_id: outcome["currentSkipped"]
            for schedule_id, outcome in evaluated.items()
            if outcome["currentSkipped"] is not None
        },
        "currentStreaks": {
            schedule_id: outcome["streak"]
            for schedule_id, outcome in evaluated.items()
        },
    }
