"""Portable, GitHub-visible claim leases. No foreign runtime/database reads.

Version 2 is an explicit cooperative write-authority deadline, not evidence
that an agent stopped or that its private workspace contains no work. Version
1 comments retain their historical non-expiring ownership contract.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

LEASE_DURATION = timedelta(minutes=30)
RENEW_INTERVAL = timedelta(minutes=5)


def utc_now():
    return datetime.now(UTC)


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def valid_lease(handoff):
    renewed = parse_time(handoff.lease_renewed_at)
    expires = parse_time(handoff.lease_expires_at)
    return bool(
        renewed and expires and timedelta(0) < expires - renewed <= LEASE_DURATION
    )


def expired(handoff, *, now=None):
    return bool(
        valid_lease(handoff)
        and (now or utc_now()) >= parse_time(handoff.lease_expires_at)
    )


def with_lease(handoff, *, now=None):
    now = now or utc_now()
    return replace(
        handoff,
        lease_renewed_at=now.isoformat(),
        lease_expires_at=(now + LEASE_DURATION).isoformat(),
    )


def classified_attempts(comments, *, repository, issue_number, now=None):
    """Complete comment evidence -> active and expired identities, with holds."""
    from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment

    active, ended = [], []
    identities = {}
    for comment in comments:
        parsed = parse_attempt_comment(comment.get("body"))
        if parsed.status == "no_marker":
            continue
        handoff = parsed.handoff
        if (
            handoff is None
            or handoff.repository.casefold() != repository.casefold()
            or handoff.issue_number != issue_number
        ):
            raise ValueError("claim_evidence_incomplete")
        previous = identities.get(handoff.attempt_id)
        if previous is not None:
            if previous != comment.get("body"):
                raise ValueError("conflicting_attempt_copies")
            continue
        identities[handoff.attempt_id] = comment.get("body")
        if handoff.operator_hold or handoff.activity == "attention":
            raise ValueError("operator_hold")
        if handoff.activity == "released":
            continue
        item = (comment, handoff)
        (ended if expired(handoff, now=now) else active).append(item)
    return active, ended


async def reconcile_expired_issue(*, service, repository, issue_number, now=None):
    """Retire an expired advisory status without rewriting another owner.

    All decision inputs are issue labels and comments. Existing PR/branch
    handoffs remain available for assessment/continuation. No completion,
    stopped-writer, no-work, or workspace-deletion authority is produced.
    """
    from moonmind.workflows.temporal.activities.github_issue_finalization_activities import (
        _fetch_issue,
        _issue_label_names,
    )
    from moonmind.workflows.temporal.activities.github_issue_reconciliation_activities import (
        _trusted_posters,
    )

    actor = await service.issue_claim_actor(repo=repository)
    if not actor.get("ok"):
        raise ValueError("claim_actor_unavailable")
    trusted = {name.casefold() for name in _trusted_posters(service=service)}

    async def observe():
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
            raise ValueError("claim_read_failure")
        issue_state, labels = _issue_label_names(issue.get("issue"))
        active, ended = classified_attempts(
            listed["comments"],
            repository=repository,
            issue_number=issue_number,
            now=now,
        )
        for comment, _ in ended:
            user = comment.get("user") or {}
            if (
                str(user.get("id")) != actor["actorId"]
                and str(user.get("login", "")).casefold() not in trusted
                and comment.get("author_association")
                not in {"OWNER", "MEMBER", "COLLABORATOR"}
            ):
                raise ValueError("untrusted_claim_poster")
        return issue_state, labels, active, ended

    issue_state, labels, active, ended = await observe()
    if issue_state != "open" or active or not ended:
        return {"reclaimed": False, "reasonCode": "live_or_legacy_owner"}
    if any(
        label.startswith("status:") and label != "status: in-progress"
        for label in labels
    ):
        return {"reclaimed": False, "reasonCode": "settled_status_retained"}
    # A fresh observation protects against a renewal/new announcement between
    # discovery and mutation. GitHub provides no atomic label+comment CAS.
    issue_state, labels, active, ended = await observe()
    if (
        issue_state != "open"
        or active
        or not ended
        or any(
            label.startswith("status:") and label != "status: in-progress"
            for label in labels
        )
    ):
        return {"reclaimed": False, "reasonCode": "claim_changed"}
    if "status: in-progress" in labels:
        await service.remove_issue_label(
            repo=repository, issue_number=issue_number, label="status: in-progress"
        )
    issue_state, labels, active, ended = await observe()
    if (
        active
        and issue_state == "open"
        and not any(
            label.startswith("status:") and label != "status: in-progress"
            for label in labels
        )
    ):
        # Preserve an observed successor's advisory status after a concurrent
        # announcement; its comment already excludes duplicate admission.
        await service.add_issue_labels(
            repo=repository, issue_number=issue_number, labels=["status: in-progress"]
        )
        return {"reclaimed": False, "reasonCode": "successor_observed"}
    return {
        "reclaimed": issue_state == "open"
        and not active
        and not any(label.startswith("status:") for label in labels),
        "reasonCode": "lease_expired",
        "expiredAttempts": [handoff.attempt_id for _, handoff in ended],
    }


async def renew_owned_claim(*, store, service, owner):
    """Serialize renewal and retain intent/readback across uncertain PATCHes."""
    from sqlalchemy.ext.asyncio import async_object_session

    from moonmind.workflows.temporal.github_issue_attempts import (
        parse_attempt_comment,
        render_attempt_comment,
    )
    from moonmind.workflows.temporal.issue_claim_store import (
        ClaimReceipt,
        inspect_claim_comments,
    )

    async with store.locked(owner) as row:
        listed = await service.list_issue_comments(
            repo=row.repository, issue_number=row.issue_number
        )
        if not listed.get("ok") or not isinstance(listed.get("comments"), list):
            raise ValueError("claim_read_failure")
        receipt = ClaimReceipt.from_row(row)
        comment_id = inspect_claim_comments(receipt, listed["comments"])
        if row.released or not comment_id:
            raise ValueError("claim_lease_lost")
        observed = next(
            comment["body"]
            for comment in listed["comments"]
            if str(comment["id"]) == comment_id
        )
        if row.pending_comment_body == observed:
            row.comment_body, row.pending_comment_body = observed, None
        handoff = parse_attempt_comment(row.comment_body).handoff
        if handoff is None or not valid_lease(handoff):
            raise ValueError("claim_lease_required")
        if (
            expired(handoff)
            or handoff.activity not in {"preparing", "active", "awaiting-review"}
            or handoff.operator_hold
        ):
            raise ValueError("claim_lease_lost")
        now = utc_now()
        if (
            not row.pending_comment_body
            and now - parse_time(handoff.lease_renewed_at) < RENEW_INTERVAL
        ):
            return ClaimReceipt.from_row(row)
        renewed = row.pending_comment_body or render_attempt_comment(
            with_lease(handoff, now=now)
        )
        if row.pending_comment_body:
            pending = parse_attempt_comment(renewed).handoff
            if (
                pending is None
                or not valid_lease(pending)
                or replace(
                    pending,
                    lease_expires_at=handoff.lease_expires_at,
                    lease_renewed_at=handoff.lease_renewed_at,
                )
                != handoff
            ):
                raise ValueError("claim_update_pending")
        row.pending_comment_body = renewed
        await async_object_session(row).commit()
        await service.update_issue_comment(
            repo=row.repository, comment_id=int(comment_id), body=renewed
        )
        listed = await service.list_issue_comments(
            repo=row.repository, issue_number=row.issue_number
        )
        if not listed.get("ok") or not isinstance(listed.get("comments"), list):
            raise ValueError("claim_read_failure")
        inspect_claim_comments(ClaimReceipt.from_row(row), listed["comments"])
        if not any(
            str(comment["id"]) == comment_id and comment.get("body") == renewed
            for comment in listed["comments"]
        ):
            raise ValueError("claim_update_pending")
        row.comment_body, row.pending_comment_body = renewed, None
        return ClaimReceipt.from_row(row)


async def renew_execution_claim(lease, *, store=None, service=None, owner=None):
    """Resolve only our own ancestry; the shared ownership read stays in GitHub."""
    from moonmind.workflows.adapters.github_service import GitHubService
    from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment
    from moonmind.workflows.temporal.issue_claim_store import (
        IssueClaimStore,
        claim_owner,
    )

    store = store or IssueClaimStore()
    service = service or GitHubService()
    receipt = await store.for_execution(owner or claim_owner())
    if not receipt or any(
        lease.get(key) != value
        for key, value in {
            "owner": receipt.owner,
            "attemptId": receipt.attempt_id,
            "repository": receipt.repository,
            "issueNumber": receipt.issue_number,
            "commentId": receipt.comment_id,
        }.items()
    ):
        raise ValueError("claim_lease_owner_mismatch")
    try:
        receipt = await renew_owned_claim(
            store=store, service=service, owner=receipt.owner
        )
    except ValueError as exc:
        if str(exc) in {"claim_read_failure", "claim_update_pending"}:
            return {"status": "retry", "reasonCode": str(exc)}
        return {"status": "lost", "reasonCode": "claim_lease_lost"}
    handoff = parse_attempt_comment(receipt.comment_body).handoff
    return {
        "status": "renewed",
        "owner": receipt.owner,
        "attemptId": receipt.attempt_id,
        "leaseExpiresAt": handoff.lease_expires_at,
    }
