"""Operator-authorized, audited retry resets for GitHub issue attempt history.

The portable retry allowance charges every attempt an issue's comments record.
When charged attempts never recorded their outcome and the deployments that
owned them can no longer recover the execution evidence, no sweep can finish
that accounting. The repair is an explicit operator decision: one
GitHub-visible reset record, posted by the authenticated operator account,
that names who authorized it, when, why, and which attempts it supersedes.
Those attempts stay in lineage for prior-work assessment; they stop counting
against the allowance (see ``compute_effective_retry``).

What it deliberately does not do: it never deletes or rewrites a comment,
never changes labels, never acts over a live reservation, an explicit hold,
or conflicting or unreadable evidence, and never resets an issue whose history
does not currently exhaust the allowance. By default it only resets issues
where at least one charged attempt never recorded a terminal outcome; recorded
failures stay charged unless the operator explicitly includes them.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal.github_issue_attempts import (
    INSTALLATION_ID_ENV_VAR,
    build_retry_reset_handoff,
    new_attempt_id,
    parse_attempt_comment,
    reconcile_uncertain_creation,
    reconstruct_from_comments,
    render_attempt_comment,
    resolve_installation_id,
)
from moonmind.workflows.temporal.github_issue_claim_lease import classified_attempts

ACTION_RESET = "reset"
ACTION_NONE = "none"
ACTION_REFUSE = "refuse"

#: Allowance applied when no recorded attempt names one; matches admission.
DEFAULT_ALLOWANCE = 3

#: Attempt identity recorded when the operator's shell names no installation.
OPERATOR_DEPLOYMENT_ID = "operator-retry-reset"

_COLLABORATOR_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def _string(value: Any) -> str:
    return str(value or "").strip()


def trusted_attempt_posters(
    comments: Sequence[Mapping[str, Any]], *, actor_id: str, trusted: Sequence[str] = ()
) -> list[str]:
    """Admission's provenance rule: configured posters, this actor, collaborators."""
    return list(trusted) + [
        _string((comment.get("user") or {}).get("login"))
        for comment in comments
        if _string((comment.get("user") or {}).get("id")) == _string(actor_id)
        or comment.get("author_association") in _COLLABORATOR_ASSOCIATIONS
    ]


def plan_retry_reset(
    *,
    repository: str,
    issue: Mapping[str, Any],
    comments: Sequence[Mapping[str, Any]],
    actor_id: str,
    trusted: Sequence[str] = (),
    include_recorded_outcomes: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify one issue's retry history into a reviewable reset disposition."""
    now = now or datetime.now(UTC)
    number = int(issue.get("number") or 0)
    marked = [
        (comment, parsed)
        for comment in comments
        for parsed in [parse_attempt_comment(comment.get("body"))]
        if parsed.status != "no_marker"
    ]
    recorded = [
        parsed.handoff.retry_allowance for _, parsed in marked if parsed.handoff
    ]
    allowance = next(
        (value for value in reversed(recorded) if value > 0), DEFAULT_ALLOWANCE
    )
    plan: dict[str, Any] = {
        "repository": repository,
        "issueNumber": number,
        "title": _string(issue.get("title"))[:200],
        "labels": [
            _string(label.get("name") if isinstance(label, Mapping) else label)
            for label in issue.get("labels") or []
        ],
        "includeRecordedOutcomes": bool(include_recorded_outcomes),
        "allowance": allowance,
        "action": ACTION_NONE,
        "reasonCode": "",
        "retry": None,
        "supersedes": [],
        "predecessor": None,
    }
    if _string(issue.get("state")) != "open":
        return {**plan, "reasonCode": "issue_closed"}
    # Ownership first: a reset never acts over a live owner, a hold, or
    # evidence that cannot be read or reconciled.
    try:
        active, _ended, _seen = classified_attempts(
            comments, repository=repository, issue_number=number, now=now
        )
    except ValueError as exc:
        return {
            **plan,
            "action": ACTION_REFUSE,
            "reasonCode": str(exc).split(":", 1)[0],
        }
    if active:
        return {**plan, "action": ACTION_REFUSE, "reasonCode": "live_reservation"}
    lineage = reconstruct_from_comments(
        comments,
        expected_repository=repository,
        expected_issue_number=number,
        trusted_posters=trusted_attempt_posters(
            comments, actor_id=actor_id, trusted=trusted
        ),
        max_attempts=allowance,
        now_epoch=now.timestamp(),
    )
    plan["retry"] = lineage.retry
    if lineage.reason_code != "budget_exhausted":
        action = ACTION_REFUSE if lineage.outcome == "needs_attention" else ACTION_NONE
        return {**plan, "action": action, "reasonCode": lineage.reason_code}
    retry = lineage.retry or {}
    if not retry.get("unresolvedAttempts") and not include_recorded_outcomes:
        return {**plan, "reasonCode": "recorded_outcomes_only"}
    comment, latest = marked[-1]
    return {
        **plan,
        "action": ACTION_RESET,
        "reasonCode": "budget_exhausted",
        "supersedes": [
            item["attemptId"] for item in retry.get("chargedAttempts") or []
        ],
        "predecessor": {
            "attemptId": latest.attempt_id,
            "commentId": _string(comment.get("id")),
        },
    }


async def _operator(service: Any, repository: str) -> dict[str, Any]:
    """The authenticated account that will post, and therefore authorize, a reset."""
    token, error = await service.resolve_github_token(repo=repository)
    if not token:
        raise ValueError(
            f"auth_unavailable: {error or 'GitHub credentials are unavailable'}"
        )
    identity, failure = await service.get_authenticated_user(token=token)
    if identity is None:
        raise ValueError(
            f"operator_identity_unavailable: {(failure or {}).get('summary') or 'unknown account'}"
        )
    return {"id": str(identity["id"]), "login": str(identity["login"]), "token": token}


async def _open_issues(
    *, service: Any, repository: str, token: str, limit: int, explicit: Sequence[int]
) -> list[dict[str, Any]]:
    import httpx

    headers = service._github_headers(token)
    issues: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        if explicit:
            for number in explicit:
                response = await client.get(
                    f"https://api.github.com/repos/{repository}/issues/{int(number)}",
                    headers=headers,
                )
                response.raise_for_status()
                issues.append(response.json())
            return issues
        page = 1
        while len(issues) < limit and page <= 10:
            response = await client.get(
                f"https://api.github.com/repos/{repository}/issues",
                params={"state": "open", "per_page": 100, "page": page},
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                break
            issues.extend(item for item in payload if "pull_request" not in item)
            if len(payload) < 100:
                break
            page += 1
    return issues[:limit]


async def inventory_retry_resets(
    *,
    service: Any,
    repository: str = "",
    issue_numbers: Sequence[int] = (),
    limit: int = 500,
    include_recorded_outcomes: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only inventory of retry resets for one repository; writes nothing.

    The repository defaults to the deployment's configured workflow
    repository, so an operator can inventory with no arguments.
    """
    from moonmind.workflows.temporal.activities.github_issue_reconciliation_activities import (
        _trusted_posters,
    )

    if not _string(repository):
        from moonmind.config.settings import settings

        repository = _string(settings.workflow.github_repository)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(
            f"invalid_repository: {repository!r} is not an owner/name repository; "
            "set WORKFLOW_GITHUB_REPOSITORY or pass --repository"
        )
    operator = await _operator(service, repository)
    trusted = _trusted_posters(service=service, authenticated_login=operator["login"])
    issues = await _open_issues(
        service=service,
        repository=repository,
        token=operator["token"],
        limit=limit,
        explicit=issue_numbers,
    )
    plans: list[dict[str, Any]] = []
    for issue in issues:
        listed = await service.list_issue_comments(
            repo=repository, issue_number=int(issue["number"])
        )
        if not listed.get("ok") or not isinstance(listed.get("comments"), list):
            # Unreadable history is never treated as no history.
            plans.append(
                {
                    "repository": repository,
                    "issueNumber": issue["number"],
                    "action": ACTION_REFUSE,
                    "reasonCode": "claim_read_failure",
                }
            )
            continue
        plans.append(
            plan_retry_reset(
                repository=repository,
                issue=issue,
                comments=listed["comments"],
                actor_id=operator["id"],
                trusted=trusted,
                include_recorded_outcomes=include_recorded_outcomes,
                now=now,
            )
        )
    return {
        "repository": repository,
        "operator": operator["login"],
        "includeRecordedOutcomes": bool(include_recorded_outcomes),
        "issuesExamined": len(plans),
        "plannedResets": sum(1 for plan in plans if plan["action"] == ACTION_RESET),
        "issues": plans,
    }


async def apply_retry_reset(
    *,
    service: Any,
    plan: Mapping[str, Any],
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-verify one planned reset against fresh GitHub evidence, then record it.

    The reset is posted only if a fresh plan still supersedes exactly the
    reviewed attempts. A lost write acknowledgement is an unknown result:
    the readback by stable attempt marker decides whether it was recorded.
    """
    from moonmind.workflows.temporal.activities.github_issue_reconciliation_activities import (
        _fetch_issue,
        _trusted_posters,
    )

    if plan.get("action") != ACTION_RESET:
        return {
            "applied": False,
            "reasonCode": _string(plan.get("reasonCode")) or "not_planned",
        }
    if not _string(reason):
        return {"applied": False, "reasonCode": "reason_required"}
    now = now or datetime.now(UTC)
    repository = _string(plan["repository"])
    number = int(plan["issueNumber"])
    operator = await _operator(service, repository)
    issue = await _fetch_issue(
        service=service, repository=repository, issue_number=number
    )
    listed = await service.list_issue_comments(repo=repository, issue_number=number)
    if (
        not issue.get("ok")
        or not listed.get("ok")
        or not isinstance(listed.get("comments"), list)
    ):
        return {"applied": False, "reasonCode": "claim_read_failure"}
    fresh = plan_retry_reset(
        repository=repository,
        issue=issue["issue"],
        comments=listed["comments"],
        actor_id=operator["id"],
        trusted=_trusted_posters(
            service=service, authenticated_login=operator["login"]
        ),
        include_recorded_outcomes=bool(plan.get("includeRecordedOutcomes")),
        now=now,
    )
    if (
        fresh["action"] != ACTION_RESET
        or fresh["supersedes"] != list(plan.get("supersedes") or [])
        or fresh["predecessor"] != plan.get("predecessor")
    ):
        return {
            "applied": False,
            "reasonCode": "claim_changed",
            "currentAction": fresh["action"],
            "currentReasonCode": fresh["reasonCode"],
        }
    handoff = build_retry_reset_handoff(
        attempt_id=new_attempt_id(repository=repository, issue_number=number),
        deployment_id=resolve_installation_id(os.getenv(INSTALLATION_ID_ENV_VAR))
        or OPERATOR_DEPLOYMENT_ID,
        repository=repository,
        issue_number=number,
        authorized_by=operator["login"],
        authorized_at=now.isoformat(),
        reason=reason,
        predecessor_attempt_id=fresh["predecessor"]["attemptId"],
        predecessor_comment_id=fresh["predecessor"]["commentId"],
        superseded_attempt_ids=fresh["supersedes"],
        allowance=fresh["allowance"],
    )
    created = await service.create_issue_comment(
        repo=repository, issue_number=number, body=render_attempt_comment(handoff)
    )
    if not created.get("ok") and created.get("reasonCode") != "outcome_unknown":
        return {
            "applied": False,
            "reasonCode": _string(created.get("reasonCode")) or "reset_write_failed",
            "attemptId": handoff.attempt_id,
        }
    verified = await service.list_issue_comments(repo=repository, issue_number=number)
    observed = verified.get("comments") if verified.get("ok") else None
    reconciliation = (
        reconcile_uncertain_creation(observed, handoff.attempt_id)
        if isinstance(observed, list)
        else None
    )
    if reconciliation is None or reconciliation.outcome != "already_created":
        return {
            "applied": False,
            "reasonCode": "reset_unconfirmed",
            "attemptId": handoff.attempt_id,
        }
    return {
        "applied": True,
        "reasonCode": "reset_recorded",
        "attemptId": handoff.attempt_id,
        "commentId": reconciliation.comment_id,
        "authorizedBy": operator["login"],
        "supersedes": list(fresh["supersedes"]),
    }


__all__ = [
    "ACTION_NONE",
    "ACTION_REFUSE",
    "ACTION_RESET",
    "DEFAULT_ALLOWANCE",
    "apply_retry_reset",
    "inventory_retry_resets",
    "plan_retry_reset",
    "trusted_attempt_posters",
]
