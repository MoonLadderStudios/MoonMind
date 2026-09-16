"""One controlled migration of version-1 issue reservations.

Version 1 attempt comments predate the cooperative lease agreement, so nothing
in them expires on its own. This module retires those reservations exactly
once, against an operator-declared cutover instant, by stamping the lease the
comment never carried: renewed one lease-duration before the cutover, expired
at the cutover.

What it deliberately does not do: it never claims writers stopped, never
asserts completion or no-work, never deletes a comment, never touches labels,
never rewrites a successor's comment, and never discards a preserved PR or
branch. Retiring a reservation means only that the old attempt no longer holds
current write authority; its work references remain available for assessment
and continuation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal.github_issue_attempts import (
    parse_attempt_comment,
    render_attempt_comment,
)
from moonmind.workflows.temporal.github_issue_claim_lease import (
    LEASE_DURATION,
    RESERVATION_LEGACY_PENDING,
    RESERVATION_LEGACY_RETIRED,
    RESERVATION_LIVE,
    RESERVATION_OPERATOR_HOLD,
    RESERVATION_UNREADABLE,
    classified_attempts,
    parse_time,
    reservation_status,
)

ACTION_RETIRE = "retire_reservation"
ACTION_PRESERVE = "preserve_for_continuation"
ACTION_RETAIN_LIVE = "retain_live_reservation"
ACTION_OPERATOR_HOLD = "operator_hold"
ACTION_UNREADABLE = "unreadable_evidence"
ACTION_UNTRUSTED = "untrusted_poster"
ACTION_NONE = "no_action"

#: Actions whose comment body this migration rewrites.
WRITE_ACTIONS = frozenset({ACTION_RETIRE, ACTION_PRESERVE})


def _trusted_comment(
    comment: Mapping[str, Any], *, actor_id: str, trusted: Sequence[str]
) -> bool:
    user = comment.get("user") or {}
    return (
        str(user.get("id")) == str(actor_id)
        or str(user.get("login", "")).casefold()
        in {str(name).casefold() for name in trusted}
        or comment.get("author_association") in {"OWNER", "MEMBER", "COLLABORATOR"}
    )


def retired_comment_body(handoff, *, cutover: datetime, note: str) -> str:
    """Stamp the expired lease the version-1 comment never carried.

    The handoff's own fields are otherwise untouched: no writer, mutation,
    preservation, or completion claim is added or changed.
    """
    retired = replace(
        handoff,
        lease_renewed_at=(cutover - LEASE_DURATION).isoformat(),
        lease_expires_at=cutover.isoformat(),
    )
    return render_attempt_comment(retired).rstrip() + "\n\n" + note.strip() + "\n"


def recovery_note(handoff, *, cutover: datetime) -> str:
    preserved = [
        text
        for text in (
            f"pull request {handoff.pr_url}" if handoff.pr_url else "",
            f"branch {handoff.saved_branch}" if handoff.saved_branch else "",
        )
        if text
    ]
    lines = [
        f"Reservation retired by the operator-declared version-1 migration cutover ({cutover.isoformat()}).",
        "",
        "This records only that the attempt no longer holds current write "
        "authority for this issue. It makes no claim that its writers stopped, "
        "that its workspace is empty, or that the issue's work is complete.",
    ]
    if preserved:
        lines += [
            "",
            "Preserved work retained for assessment or continuation: "
            + ", ".join(preserved)
            + ".",
        ]
    return "\n".join(lines)


def plan_issue_recovery(
    *,
    repository: str,
    issue: Mapping[str, Any],
    comments: Sequence[Mapping[str, Any]],
    cutover: datetime,
    actor_id: str,
    trusted: Sequence[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify one issue's attempt evidence into a reviewable disposition."""
    labels = [
        str(label.get("name") if isinstance(label, Mapping) else label)
        for label in issue.get("labels") or []
    ]
    attempts: list[dict[str, Any]] = []
    for comment in comments:
        parsed = parse_attempt_comment(comment.get("body"))
        status = reservation_status(
            parsed, comment, now=now, legacy_cutover=cutover
        )
        if status is None:
            continue
        handoff = parsed.handoff
        record: dict[str, Any] = {
            "commentId": str(comment.get("id") or ""),
            "attemptId": parsed.attempt_id,
            "formatVersion": parsed.format_version,
            "reservationStatus": status,
            "announcedAt": str(comment.get("created_at") or ""),
            "author": str((comment.get("user") or {}).get("login") or ""),
        }
        if handoff is not None:
            record.update(
                {
                    "activity": handoff.activity,
                    "deploymentId": handoff.deployment_id,
                    "workflowId": handoff.workflow_id,
                    "prUrl": handoff.pr_url,
                    "savedBranch": handoff.saved_branch,
                    "operatorHold": handoff.operator_hold,
                }
            )
        if status == RESERVATION_LEGACY_RETIRED:
            if not _trusted_comment(comment, actor_id=actor_id, trusted=trusted):
                record["action"] = ACTION_UNTRUSTED
            elif handoff.pr_url or handoff.saved_branch:
                record["action"] = ACTION_PRESERVE
            else:
                record["action"] = ACTION_RETIRE
        elif status == RESERVATION_OPERATOR_HOLD:
            record["action"] = ACTION_OPERATOR_HOLD
        elif status == RESERVATION_LIVE:
            record["action"] = ACTION_RETAIN_LIVE
        elif status == RESERVATION_LEGACY_PENDING:
            # Announced at or after the cutover: its writer may legitimately
            # predate the upgrade window, so it is reported, not retired.
            record["action"] = ACTION_RETAIN_LIVE
        elif status == RESERVATION_UNREADABLE:
            record["action"] = ACTION_UNREADABLE
        else:
            record["action"] = ACTION_NONE
        attempts.append(record)
    blocking = [
        item["action"]
        for item in attempts
        if item["action"] in {ACTION_RETAIN_LIVE, ACTION_OPERATOR_HOLD, ACTION_UNREADABLE}
    ]
    writes = [item for item in attempts if item["action"] in WRITE_ACTIONS]
    if blocking:
        # Never retire a reservation while a live owner, an explicit hold, or
        # unreadable evidence still governs the same issue.
        writes = []
        for item in attempts:
            if item["action"] in WRITE_ACTIONS:
                item["action"] = ACTION_NONE
                item["skipReason"] = blocking[0]
    return {
        "repository": repository,
        "issueNumber": issue.get("number"),
        "title": str(issue.get("title") or "")[:200],
        "labels": labels,
        "attempts": attempts,
        "plannedWrites": len(writes),
        "staleStatusLabel": not attempts and "status: in-progress" in labels,
        "nextAction": (
            "continuation"
            if any(item["action"] == ACTION_PRESERVE for item in attempts)
            else "reassess"
            if writes
            else "none"
        ),
    }


async def apply_issue_recovery(
    *, service, plan: Mapping[str, Any], cutover: datetime
) -> dict[str, Any]:
    """Revalidate the whole issue before each write and verify its remote body."""
    from moonmind.workflows.temporal.activities.github_issue_reconciliation_activities import (
        _fetch_issue,
        _trusted_posters,
    )

    repository = str(plan["repository"])
    issue_number = int(plan["issueNumber"])
    actor = await service.issue_claim_actor(repo=repository)
    if not actor.get("ok"):
        return {"applied": 0, "reasonCode": "claim_actor_unavailable"}
    applied: list[str] = []
    reason = "verified"
    for attempt in plan["attempts"]:
        if attempt["action"] not in WRITE_ACTIONS:
            continue
        issue = await _fetch_issue(
            service=service, repository=repository, issue_number=issue_number
        )
        listed = await service.list_issue_comments(
            repo=repository, issue_number=issue_number
        )
        if (
            not issue.get("ok")
            or not listed.get("ok")
            or not isinstance(listed.get("comments"), list)
        ):
            reason = "claim_read_failure"
            break
        if issue["issue"].get("state") != "open":
            reason = "issue_closed"
            break
        # The target comment alone cannot rule out an intervening successor,
        # hold, conflicting copy, or evidence bound to a different issue.
        try:
            active, _, _ = classified_attempts(
                listed["comments"],
                repository=repository,
                issue_number=issue_number,
                legacy_cutover=cutover,
            )
        except ValueError as exc:
            reason = str(exc)
            break
        if active:
            reason = "live_or_legacy_owner"
            break
        fresh = plan_issue_recovery(
            repository=repository,
            issue=issue["issue"],
            comments=listed["comments"],
            cutover=cutover,
            actor_id=actor["actorId"],
            trusted=_trusted_posters(service=service),
        )
        if not any(
            item["commentId"] == attempt["commentId"]
            and item["attemptId"] == attempt["attemptId"]
            and item["action"] in WRITE_ACTIONS
            for item in fresh["attempts"]
        ):
            reason = "claim_changed"
            break
        observed = {str(item.get("id")): item for item in listed["comments"]}
        comment = observed.get(attempt["commentId"]) or {}
        parsed = parse_attempt_comment(comment.get("body"))
        body = retired_comment_body(
            parsed.handoff,
            cutover=cutover,
            note=recovery_note(parsed.handoff, cutover=cutover),
        )
        await service.update_issue_comment(
            repo=repository, comment_id=int(attempt["commentId"]), body=body
        )
        # PATCH acknowledgement can be lost after GitHub commits the write.
        # The readback, not the transport outcome, establishes retirement.
        verified = await service.list_issue_comments(
            repo=repository, issue_number=issue_number
        )
        if not verified.get("ok") or not isinstance(verified.get("comments"), list):
            reason = "claim_read_failure"
            break
        if not any(
            str(item.get("id")) == attempt["commentId"] and item.get("body") == body
            for item in verified["comments"]
        ):
            reason = "claim_write_unconfirmed"
            break
        applied.append(attempt["attemptId"])
    return {"applied": len(applied), "attemptIds": applied, "reasonCode": reason}


def parse_cutover(value: str) -> datetime:
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError(
            "The migration cutover must be an explicit ISO-8601 instant with a "
            "UTC offset, for example 2026-09-14T00:00:00Z."
        )
    return parsed


__all__ = [
    "ACTION_NONE",
    "ACTION_OPERATOR_HOLD",
    "ACTION_PRESERVE",
    "ACTION_RETAIN_LIVE",
    "ACTION_RETIRE",
    "ACTION_UNREADABLE",
    "ACTION_UNTRUSTED",
    "WRITE_ACTIONS",
    "apply_issue_recovery",
    "parse_cutover",
    "plan_issue_recovery",
    "recovery_note",
    "retired_comment_body",
]
