"""Portable, GitHub-visible issue reservations. No foreign runtime/database reads.

A reservation suppresses duplicate work only while its owner maintains a valid,
bounded lease. Historical comments, stale labels, and incomplete cleanup never
independently create permanent ownership, and losing ownership never authorizes
discarding work or overwriting another contributor's changes.

Version 2 carries the explicit cooperative write-authority deadline. Version 1
predates that agreement, so its reservations end at an operator-declared
migration cutover (``MOONMIND_ISSUE_CLAIM_LEGACY_CUTOVER_AT``) rather than at an
invented deadline its writers never accepted.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

# Running ownership is renewable; announcement is deliberately short-lived so a
# deployment that dies between selection and dispatch stops blocking the issue
# within minutes instead of a full execution lease.
LEASE_DURATION = timedelta(minutes=30)
RENEW_INTERVAL = timedelta(minutes=5)
# Announcement covers only selection-to-dispatch, so it gets a fraction of the
# running lease and its renew interval. One knob keeps the two phases
# consistent: shortening the running lease shortens the announcement too.
PREPARING_LEASE_DIVISOR = 6
# A marked but unreadable comment cannot prove ownership. Bound its veto by
# GitHub's own timestamps instead of letting it veto the issue forever.
UNREADABLE_RESERVATION_GRACE = LEASE_DURATION

RENEWABLE_ACTIVITIES = frozenset({"preparing", "active", "awaiting-review"})

# Typed reservation dispositions. These are diagnostics and admission inputs,
# never new GitHub labels.
RESERVATION_LIVE = "live_reservation"
RESERVATION_EXPIRED = "expired_reservation"
RESERVATION_RELEASED = "released_reservation"
RESERVATION_OPERATOR_HOLD = "operator_hold"
RESERVATION_LEGACY_PENDING = "legacy_reservation_awaiting_migration"
RESERVATION_LEGACY_RETIRED = "legacy_reservation_retired"
RESERVATION_UNREADABLE = "unreadable_reservation"
RESERVATION_UNREADABLE_STALE = "unreadable_reservation_stale"

#: Dispositions that still suppress a new attempt. Everything else is history.
BLOCKING_RESERVATIONS = frozenset(
    {
        RESERVATION_LIVE,
        RESERVATION_OPERATOR_HOLD,
        RESERVATION_LEGACY_PENDING,
        RESERVATION_UNREADABLE,
    }
)

_UNSET = object()


def utc_now():
    return datetime.now(UTC)


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def lease_duration_for(activity):
    """Announcement gets minutes; admitted execution gets the renewable lease."""
    if activity == "preparing":
        return LEASE_DURATION / PREPARING_LEASE_DIVISOR
    return LEASE_DURATION


def renew_interval_for(activity):
    """Renew often enough that the shorter announcement lease stays coverable."""
    if activity == "preparing":
        return RENEW_INTERVAL / PREPARING_LEASE_DIVISOR
    return RENEW_INTERVAL


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
        lease_expires_at=(now + lease_duration_for(handoff.activity)).isoformat(),
    )


def legacy_cutover_at():
    """Operator declaration that every version-1 writer is upgraded or stopped.

    Read at the Activity boundary only; deterministic workflow code passes the
    resolved instant instead of importing deployment configuration.
    """
    from moonmind.config.settings import settings

    return parse_time(
        getattr(settings.github, "issue_claim_legacy_cutover_at", None) or ""
    )


def _announced_at(comment):
    """GitHub's own timestamps, never a locally invented announcement time."""
    if not comment:
        return None
    return parse_time(comment.get("created_at")) or parse_time(
        comment.get("updated_at")
    )


def reservation_status(parsed, comment=None, *, now=None, legacy_cutover=_UNSET):
    """The one interpretation of what an attempt comment means for ownership.

    Search, explicit issue loading, continuation, publication checks, and
    maintenance all resolve effective ownership here so one path cannot reclaim
    an issue while another keeps treating its historical comment as a lock.
    Returns ``None`` when the comment is not attempt evidence at all.
    """
    if parsed.status == "no_marker":
        return None
    now = now or utc_now()
    if legacy_cutover is _UNSET:
        legacy_cutover = legacy_cutover_at()
    handoff = parsed.handoff
    if handoff is None:
        announced = _announced_at(comment)
        if announced is not None and now - announced > UNREADABLE_RESERVATION_GRACE:
            return RESERVATION_UNREADABLE_STALE
        return RESERVATION_UNREADABLE
    if handoff.activity == "released":
        return RESERVATION_RELEASED
    if handoff.operator_hold or handoff.activity == "attention":
        return RESERVATION_OPERATOR_HOLD
    if valid_lease(handoff):
        return (
            RESERVATION_EXPIRED
            if now >= parse_time(handoff.lease_expires_at)
            else RESERVATION_LIVE
        )
    # Version 1 never agreed to a deadline. Only an explicit operator cutover,
    # applied to claims GitHub itself timestamps before that instant, ends one.
    announced = _announced_at(comment)
    if (
        legacy_cutover is not None
        and announced is not None
        and announced < legacy_cutover
        and now >= legacy_cutover
    ):
        return RESERVATION_LEGACY_RETIRED
    return RESERVATION_LEGACY_PENDING


def blocks_new_work(status):
    return status in BLOCKING_RESERVATIONS


def classified_attempts(
    comments, *, repository, issue_number, now=None, legacy_cutover=_UNSET
):
    """Complete comment evidence -> blocking and ended identities, with holds.

    The third element reports whether any attempt evidence exists at all, so a
    caller can distinguish "another attempt owns this" from "only a stale
    advisory label remains".
    """
    from moonmind.workflows.temporal.github_issue_attempt import ATTEMPT_MARKER_PREFIX
    from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment

    active, ended = [], []
    identities = {}
    seen = 0
    for comment in comments:
        parsed = parse_attempt_comment(comment.get("body"))
        status = reservation_status(
            parsed, comment, now=now, legacy_cutover=legacy_cutover
        )
        if status is None:
            # The superseded singular marker is still attempt evidence. It has
            # no reservation semantics here, but its presence means the issue
            # is not merely carrying a left-behind advisory label.
            if ATTEMPT_MARKER_PREFIX in str(comment.get("body") or ""):
                seen += 1
            continue
        seen += 1
        if status in {RESERVATION_UNREADABLE, RESERVATION_UNREADABLE_STALE}:
            # Unparseable evidence has no identity to compare or retire. A
            # recent one still blocks; a stale one is history, not ownership.
            if status == RESERVATION_UNREADABLE:
                raise ValueError("claim_evidence_incomplete")
            continue
        handoff = parsed.handoff
        if (
            handoff.repository.casefold() != repository.casefold()
            or handoff.issue_number != issue_number
        ):
            raise ValueError("claim_evidence_incomplete")
        previous = identities.get(handoff.attempt_id)
        if previous is not None:
            if previous != comment.get("body"):
                raise ValueError("conflicting_attempt_copies")
            continue
        identities[handoff.attempt_id] = comment.get("body")
        if status == RESERVATION_OPERATOR_HOLD:
            raise ValueError("operator_hold")
        if status == RESERVATION_RELEASED:
            continue
        item = (comment, handoff)
        (active if blocks_new_work(status) else ended).append(item)
    return active, ended, seen


async def reconcile_expired_issue(
    *, service, repository, issue_number, now=None, legacy_cutover=_UNSET
):
    """Retire an expired advisory status without rewriting another owner.

    All decision inputs are issue labels and comments. Existing PR/branch
    handoffs remain available for assessment/continuation. No completion,
    stopped-writer, no-work, or workspace-deletion authority is produced.
    """
    from moonmind.workflows.temporal.activities.github_issue_finalization_activities import (
        _issue_label_names,
    )
    from moonmind.workflows.temporal.activities.github_issue_reconciliation_activities import (
        _fetch_issue,
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
        active, ended, seen = classified_attempts(
            listed["comments"],
            repository=repository,
            issue_number=issue_number,
            now=now,
            legacy_cutover=legacy_cutover,
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
        return issue_state, labels, active, ended, seen

    def settled_elsewhere(labels):
        return any(
            label.startswith("status:") and label != "status: in-progress"
            for label in labels
        )

    def reclaimable(issue_state, labels, active, ended, seen):
        # A stale advisory label with no attempt evidence at all is bookkeeping
        # left behind, not ownership: the protocol always announces its comment
        # before applying the label.
        return (
            issue_state == "open"
            and not active
            and (bool(ended) or seen == 0)
            and not settled_elsewhere(labels)
        )

    issue_state, labels, active, ended, seen = await observe()
    if not reclaimable(issue_state, labels, active, ended, seen):
        return {
            "reclaimed": False,
            "reasonCode": "settled_status_retained"
            if issue_state == "open" and settled_elsewhere(labels)
            else "live_or_legacy_owner",
        }
    if seen == 0 and "status: in-progress" not in labels:
        return {"reclaimed": False, "reasonCode": "nothing_to_reconcile"}
    # A fresh observation protects against a renewal/new announcement between
    # discovery and mutation. GitHub provides no atomic label+comment CAS.
    issue_state, labels, active, ended, seen = await observe()
    if not reclaimable(issue_state, labels, active, ended, seen):
        return {"reclaimed": False, "reasonCode": "claim_changed"}
    if "status: in-progress" in labels:
        await service.remove_issue_label(
            repo=repository, issue_number=issue_number, label="status: in-progress"
        )
    issue_state, labels, active, ended, seen = await observe()
    if active and issue_state == "open" and not settled_elsewhere(labels):
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
        "reasonCode": "lease_expired" if ended else "stale_status_label",
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
        # Ownership that this attempt already relinquished is never renewed,
        # even while its bookkeeping retries.
        if row.released or row.ownership_ended or not comment_id:
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
            or handoff.activity not in RENEWABLE_ACTIVITIES
            or handoff.operator_hold
        ):
            raise ValueError("claim_lease_lost")
        now = utc_now()
        if (
            not row.pending_comment_body
            and now - parse_time(handoff.lease_renewed_at)
            < renew_interval_for(handoff.activity)
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
