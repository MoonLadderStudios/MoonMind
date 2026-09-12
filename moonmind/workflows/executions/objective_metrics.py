"""Bounded objective and attempt projections from authoritative workflow memos.

Unknown lineage/chronology is explicit. A recovered success never erases the
failed attempt histogram, and idle scans never enter the eligible denominator.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

OUTCOMES = frozenset(
    {"succeeded", "failed", "cancelled", "idle", "verification_blocked", "active"}
)
COUNTERS = (
    "remediationAttempts",
    "evidenceRetries",
    "contractRepairs",
    "activityRetries",
    "consecutiveNoProgress",
    "repeatedFailureSignature",
)


def _time(value):
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        return (
            parsed.replace(tzinfo=UTC)
            if parsed.tzinfo is None
            else parsed.astimezone(UTC)
        )
    except (ValueError, TypeError):
        return None


def objective_sample_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latest = {}
    attempts = {}
    unknown_chronology = 0
    for row in rows:
        identity = str(row.get("workflow_id") or "")
        timestamp = _time(row.get("created_at") or row.get("started_at"))
        if not identity or timestamp is None:
            unknown_chronology += 1
            continue
        memo = row.get("memo")
        memo = memo if isinstance(memo, Mapping) else {}
        item = (timestamp, memo, str(row.get("run_id") or ""))
        if identity not in latest or timestamp > latest[identity][0]:
            latest[identity] = item
        attempt_key = (identity, item[2] or timestamp.isoformat())
        attempts[attempt_key] = item
    roots = {}
    children = unknown = 0
    attempt_outcomes = Counter()
    for identity, (_, memo, _) in latest.items():
        if "objectiveParentId" not in memo:
            unknown += 1
        elif memo.get("objectiveParentId"):
            children += 1
        elif memo.get("objectiveOutcome") not in OUTCOMES:
            unknown += 1
        else:
            roots[identity] = memo
    for (identity, _), (_, memo, _) in attempts.items():
        if identity in roots and memo.get("objectiveOutcome") in OUTCOMES:
            attempt_outcomes[memo["objectiveOutcome"]] += 1

    def origin(identity):
        visited = set()
        while identity in roots and len(visited) < 64:
            if identity in visited:
                return None
            visited.add(identity)
            source = roots[identity].get("objectiveRecoverySource")
            if source is None:
                return identity
            if not isinstance(source, Mapping) or source.get("verified") is not True:
                return None
            parent, run_id = source.get("workflowId"), source.get("runId")
            if parent not in roots or (parent, run_id) not in attempts:
                return None
            identity = parent
        return None

    grouped = defaultdict(list)
    for identity in roots:
        root = origin(identity)
        if root is None:
            unknown += 1
        else:
            grouped[root].append(identity)
    outcomes = Counter()
    scheduled_outcomes = Counter()
    unknown_scheduling = 0
    recovered = recovered_successes = 0
    progress = Counter()
    known_progress = saved = known_saved = 0
    for identities in grouped.values():
        latest_identity = max(identities, key=lambda identity: latest[identity][0])
        outcome = roots[latest_identity]["objectiveOutcome"]
        outcomes[outcome] += 1
        scheduling = [
            roots[identity].get("objectiveScheduled") for identity in identities
        ]
        if any(value is True for value in scheduling):
            scheduled_outcomes[outcome] += 1
        elif not all(value is False for value in scheduling):
            unknown_scheduling += 1
        if len(identities) > 1:
            recovered += 1
            recovered_successes += int(outcome == "succeeded")
        for (identity, _), (_, memo, _) in attempts.items():
            if identity not in identities:
                continue
            item = memo.get("objectiveProgress")
            if not isinstance(item, Mapping):
                continue
            known_progress += 1
            known_saved += int(type(item.get("savedWorkAvailable")) is bool)
            saved += int(item.get("savedWorkAvailable") is True)
            for counter in COUNTERS:
                value = item.get(counter)
                if type(value) is int and value >= 0:
                    progress[counter] += value
    eligible = sum(
        outcomes[key] for key in ("succeeded", "failed", "verification_blocked")
    )
    scheduled_eligible = sum(
        scheduled_outcomes[key]
        for key in ("succeeded", "failed", "verification_blocked")
    )
    grouped_ids = {identity for group in grouped.values() for identity in group}
    grouped_attempt_count = sum(identity in grouped_ids for identity, _ in attempts)
    return {
        "scheduledObjectives": {
            "outcomes": dict(scheduled_outcomes),
            "eligible": scheduled_eligible,
            "successRate": (
                scheduled_outcomes["succeeded"] / scheduled_eligible
                if scheduled_eligible
                else None
            ),
            "unknownScheduling": unknown_scheduling,
        },
        "scope": "observed_sample",
        "observedRuns": len(rows),
        "distinctWorkflows": len(latest),
        "observedWorkflowRuns": len(attempts),
        "childWorkflows": children,
        "unknownOutcomesOrLineage": unknown,
        "unknownChronology": unknown_chronology,
        "outcomes": dict(outcomes),
        "attemptOutcomes": dict(attempt_outcomes),
        "eligibleObjectives": eligible,
        "objectiveSuccessRate": outcomes["succeeded"] / eligible if eligible else None,
        "recoveredObjectives": recovered,
        "recoveredSuccessfulObjectives": recovered_successes,
        "recoverySuccessRate": recovered_successes / recovered if recovered else None,
        "attemptProgress": dict(progress),
        "attemptsWithProgressEvidence": known_progress,
        "attemptsWithSavedWork": saved,
        "attemptsWithSavedWorkEvidence": known_saved,
        "unknownSavedWorkAvailability": grouped_attempt_count - known_saved,
    }
