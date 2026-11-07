"""Bounded identity/workforce admission and explainable evidence reconciliation."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from moonmind.security.frameworks import FrameworkContract, Strings

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Organisation = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9][a-z0-9-]{0,62}\.(?:okta\.com|oktapreview\.com|okta-emea\.com)$"
    ),
]


def utcnow() -> datetime:
    return datetime.now(UTC)


class IdentityPeriod(FrameworkContract):
    since: AwareDatetime
    until: AwareDatetime

    @model_validator(mode="after")
    def ordered(self):
        if self.until <= self.since:
            raise ValueError("identity evidence requires an ordered period")
        return self


class IdentityLifecycleInput(IdentityPeriod):
    provider: Literal["okta"]
    organisation: Organisation
    workforce_ref: str = Field(pattern=r"^art_[A-Za-z0-9_-]+$")
    secret_ref: str = Field(pattern=r"^db://[A-Za-z0-9_-]{1,128}$")
    egress_policy_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    removal_hours: float = Field(ge=0, le=8760, allow_inf_nan=False)
    privileged_group_ids: Annotated[list[Identifier], Field(max_length=256)] = Field(
        default_factory=list
    )
    requirement_ids: Strings = Field(default_factory=list)
    max_requests: int = Field(default=128, ge=1, le=256)
    max_pages: int = Field(default=16, ge=1, le=64)
    max_items: int = Field(default=10000, ge=1, le=20000)
    max_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    max_seconds: int = Field(default=60, ge=1, le=120)


class RosterIdentity(FrameworkContract):
    # Unsupported HR exports can contain extra attributes. They are discarded,
    # never propagated to collected evidence or ordinary summaries.
    model_config = ConfigDict(extra="ignore")
    person_id: Identifier | None = None
    account_id: Identifier | None = None
    account_kind: Literal["human", "service", "unknown"] = "unknown"

    @model_validator(mode="after")
    def has_identity(self):
        if not self.person_id and not self.account_id:
            raise ValueError("roster requires an explicit source identity")
        return self


class WorkforceEvent(FrameworkContract):
    model_config = ConfigDict(extra="ignore")
    id: Identifier
    person_id: Identifier
    type: Literal["joiner", "mover", "leaver"]
    effective_at: AwareDatetime | None
    remove_group_ids: Annotated[list[Identifier], Field(max_length=256)] = Field(
        default_factory=list
    )


class Workforce(IdentityPeriod):
    model_config = ConfigDict(extra="ignore")
    schema_version: Literal["workforce-events/v1"]
    organisation: Organisation
    roster: Annotated[list[RosterIdentity], Field(max_length=20000)]
    events: Annotated[list[WorkforceEvent], Field(max_length=20000)]


def reconcile(selection, workforce, accounts, events, *, observed_at):
    """Use only explicit workforce account IDs and successful, bounded events.

    Audit publication times describe recorded timing; they cannot attest the
    effective removal of downstream access. Snapshots never fill event gaps.
    """
    by_person, people_by_account, kinds = (
        defaultdict(set),
        defaultdict(set),
        defaultdict(set),
    )
    for row in workforce.roster:
        if row.person_id:
            by_person[row.person_id]
        if row.account_id:
            if row.account_kind != "unknown":
                kinds[row.account_id].add(row.account_kind)
            if row.person_id:
                by_person[row.person_id].add(row.account_id)
                people_by_account[row.account_id].add(row.person_id)
    ambiguous = {a for a, p in people_by_account.items() if len(p) > 1}
    ambiguous |= {a for a, k in kinds.items() if len(k) > 1}
    seen_accounts = defaultdict(set)
    for account in accounts:
        seen_accounts[account["id"]].add(account["status"])
    ambiguous |= {a for a, states in seen_accounts.items() if len(states) > 1}
    event_ids = defaultdict(int)
    for event in workforce.events:
        event_ids[event.id] += 1
    lifecycle = []
    for event in workforce.events:
        ids = sorted(by_person[event.person_id])
        row = {
            "workforceEventId": event.id,
            "personId": event.person_id,
            "type": event.type,
            "accountIds": ids,
            "matchMethod": "explicit_source_account_id",
            "status": "review_required",
            "evidenceRefs": [selection.workforce_ref],
            "sourceEventIds": [],
        }
        if event_ids[event.id] > 1:
            reason = "duplicate_workforce_event"
        elif any(a in ambiguous for a in ids):
            reason = "ambiguous_identity"
        elif not ids:
            reason = "unmatched_person"
        elif event.effective_at is None:
            reason = "unknown_effective_date"
        elif not selection.since <= event.effective_at <= selection.until:
            reason = "workforce_event_outside_period"
        elif event.type == "mover" and not event.remove_group_ids:
            reason = "unspecified_mover_change"
        else:
            relevant = []
            for record in events:
                if not record["successful"] or not record.get("published"):
                    continue
                at = datetime.fromisoformat(record["published"].replace("Z", "+00:00"))
                if event.effective_at <= at <= min(selection.until, observed_at):
                    relevant.append((at, record))
            relevant.sort(key=lambda pair: pair[0])
            required = [
                (a, g)
                for a in ids
                for g in (event.remove_group_ids if event.type == "mover" else [None])
            ]
            matched = []
            restorations = []
            for account_id, group_id in required:
                types = {
                    "joiner": {"user.lifecycle.create", "user.lifecycle.activate"},
                    "leaver": {
                        "user.lifecycle.deactivate",
                        "user.lifecycle.delete.completed",
                    },
                    "mover": {"group.user_membership.remove"},
                }[event.type]
                candidates = [
                    (at, record)
                    for at, record in relevant
                    if account_id in record["accountIds"]
                    and record["type"] in types
                    and (group_id is None or group_id in record["groupIds"])
                ]
                if not candidates:
                    continue
                matched.append(candidates[0])
                if event.type != "joiner":
                    restorations.extend(
                        (at, r)
                        for at, r in relevant
                        if at >= candidates[0][0]
                        and account_id in r["accountIds"]
                        and (
                            r["type"]
                            in {"user.lifecycle.activate", "user.lifecycle.unsuspend"}
                            if group_id is None
                            else r["type"] == "group.user_membership.add"
                            and group_id in r["groupIds"]
                        )
                    )
            for _, record in [*matched, *restorations]:
                row["evidenceRefs"].extend(record["evidenceRefs"])
                row["sourceEventIds"].append(record["id"])
            row["sourceEventIds"] = list(dict.fromkeys(row["sourceEventIds"]))
            row["historyComplete"] = len(matched) == len(required)
            if matched:
                delay = max(
                    (at - event.effective_at).total_seconds() / 3600
                    for at, _ in matched
                )
                row["recordedDelayHours"] = round(delay, 6)
                exception = event.type != "joiner" and (
                    delay > selection.removal_hours or bool(restorations)
                )
            else:
                exception = False
            if exception:
                row["status"] = "policy_exception"
                reason = (
                    "access_restored_after_removal"
                    if restorations
                    else "recorded_removal_outside_policy"
                )
            elif not row["historyComplete"]:
                reason = "missing_historical_event"
            else:
                row["status"] = "event_observed"
                reason = "successful_event_observed"
        row["reason"] = reason
        row["evidenceRefs"] = list(dict.fromkeys(row["evidenceRefs"]))
        lifecycle.append(row)
    unmatched = [
        {
            "accountId": a,
            "accountKind": next(iter(kinds[a])) if len(kinds[a]) == 1 else "unknown",
            "disposition": "review_required",
        }
        for a in sorted(seen_accounts)
        if not people_by_account[a]
    ]
    return {
        "controlOperation": "not_established",
        "lifecycle": lifecycle,
        "ambiguousAccounts": sorted(ambiguous),
        "unmatchedAccounts": unmatched,
        "accountKinds": {
            a: next(iter(kinds[a])) if len(kinds[a]) == 1 else "unknown"
            for a in sorted(seen_accounts)
        },
        "unmatchedPeople": sorted(
            p for p, ids in by_person.items() if not ids & seen_accounts.keys()
        ),
        "counts": {
            "accounts": len(seen_accounts),
            "workforceEvents": len(lifecycle),
            "policyExceptions": sum(
                r["status"] == "policy_exception" for r in lifecycle
            ),
            "reviewRequired": sum(r["status"] == "review_required" for r in lifecycle),
            "ambiguousAccounts": len(ambiguous),
            "unmatchedAccounts": len(unmatched),
        },
    }
