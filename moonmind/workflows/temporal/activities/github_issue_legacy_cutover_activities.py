"""Production Activity entrypoint for conservative legacy cutover (#4184).

Sibling runtime work calls :func:`assess_legacy_cutover_issue` through the
durable ``github_issue.assess_legacy`` activity binding (and
:func:`plan_legacy_cutover_repair` through
``github_issue.plan_legacy_repair``) instead of reimplementing cutover
semantics: every decision lives in
:mod:`moonmind.workflows.temporal.github_issue_legacy_cutover`, and this
module only supplies the production service object (``GitHubService``),
performs bounded GitHub reads, and threads already-read evidence into the
pure decision functions.

Ordering per issue: resolve token -> read current issue -> read attempt
comments (bounded; unreadable != absent) -> decide (pure
``assess_legacy_issue``) -> optionally plan one guarded repair (pure
``plan_legacy_repair`` through the shared ``plan_transition`` guards).
The assessment never mutates GitHub state; repairs are returned as
guarded plans for the existing finalization/reconciliation writers, which
retain write authority. Drainage (:func:`legacy_cutover_drainage_plan`)
and mixed-deployment qualification
(:func:`evaluate_legacy_cutover_deployment`) are pure wrappers over
``drainage_plan_for_pending`` and ``evaluate_mixed_deployment`` so every
cutover entrypoint is reachable through this existing tooling boundary.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal import github_issue_legacy_cutover as cutover


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value or "").strip()


def _trusted_posters(*, service: Any = None) -> list[str]:
    """Resolve the provenance allow-list for attempt-handoff validation."""
    import os

    raw = os.environ.get("MOONMIND_TRUSTED_POSTERS", "")
    posters = [part.strip() for part in str(raw or "").replace(";", ",").split(",") if part.strip()]
    candidate = getattr(service, "trusted_posters", None) if service is not None else None
    if isinstance(candidate, (list, tuple)):
        posters.extend(str(item).strip() for item in candidate if str(item).strip())
    seen: set[str] = set()
    ordered: list[str] = []
    for poster in posters:
        key = poster.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(poster)
    return ordered


def _comment_author(comment: Mapping[str, Any]) -> str:
    user = comment.get("user")
    if isinstance(user, Mapping):
        login = _string(user.get("login"))
        if login:
            return login
    for key in ("author_login", "authorLogin", "author"):
        value = comment.get(key)
        if isinstance(value, Mapping):
            login = _string(value.get("login"))
            if login:
                return login
        elif _string(value):
            return _string(value)
    return ""


async def _fetch_issue(*, service: Any, repository: str, issue_number: int) -> dict[str, Any]:
    if hasattr(service, "get_issue"):
        try:
            result = await service.get_issue(repo=repository, issue_number=issue_number)
            if isinstance(result, Mapping):
                return dict(result)
        except Exception as exc:  # noqa: BLE001 - transport shape mapped below
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": f"Issue read unknown: {exc.__class__.__name__}."}
    token, resolution_error = await service.resolve_github_token(repo=repository)
    if not token:
        return {"ok": False, "reasonCode": "auth_unavailable", "summary": resolution_error or "GitHub read unavailable."}
    import httpx

    headers = service._github_headers(token)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"https://api.github.com/repos/{repository}/issues/{issue_number}",
                headers=headers,
            )
            response.raise_for_status()
            return {"ok": True, "reasonCode": "read", "issue": response.json()}
        except Exception as exc:  # noqa: BLE001 - mapped to pending/unknown
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {401, 403, 404}:
                return {"ok": False, "reasonCode": "denied", "summary": f"Issue read denied with HTTP {status}."}
            if status == 429:
                return {"ok": False, "reasonCode": "rate_limited", "summary": "Issue read rate limited (HTTP 429)."}
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": f"Issue read unknown: {exc.__class__.__name__}."}


async def assess_legacy_cutover_issue(
    *,
    repository: str,
    issue_number: int,
    prs: Sequence[Mapping[str, Any]] | None = None,
    checkpoints: Sequence[Mapping[str, Any]] | None = None,
    issue_flags: Mapping[str, Any] | None = None,
    trusted_posters: Sequence[str] | None = None,
    service: Any | None = None,
) -> dict[str, Any]:
    """Assess one issue's legacy evidence through the production GitHub boundary.

    Bounded, read-only, repeatable: reads the current issue plus at most
    ``MAX_COMMENTS_ASSESSED`` comments, then delegates to pure
    :func:`assess_legacy_issue`. Never writes GitHub state. Unreadable or
    truncated evidence is reported as incomplete, never as a clean
    repository.
    """
    if not _string(repository) or int(issue_number or 0) <= 0:
        return {"ok": False, "reasonCode": "invalid_request", "summary": "assess_legacy_cutover_issue requires repository and issue_number."}
    if service is None:
        from moonmind.workflows.adapters.github_service import GitHubService

        service = GitHubService()
    fetched = await _fetch_issue(service=service, repository=_string(repository), issue_number=int(issue_number))
    if not fetched.get("ok"):
        return {
            "ok": False,
            "reasonCode": str(fetched.get("reasonCode") or "outcome_unknown"),
            "summary": str(fetched.get("summary") or "Issue read inconclusive; deferred without ownership decisions."),
        }
    issue_payload = fetched.get("issue") if isinstance(fetched.get("issue"), Mapping) else {}
    listed = await service.list_issue_comments(repo=_string(repository), issue_number=int(issue_number))
    comments: list[Mapping[str, Any]] = []
    comments_incomplete = False
    if isinstance(listed, Mapping) and listed.get("ok") and isinstance(listed.get("comments"), list):
        raw_comments = [c for c in listed["comments"] if isinstance(c, Mapping)]
        comments = raw_comments[: cutover.MAX_COMMENTS_ASSESSED]
        comments_incomplete = bool(listed.get("incomplete")) or len(raw_comments) > cutover.MAX_COMMENTS_ASSESSED
    elif isinstance(listed, Mapping):
        return {
            "ok": False,
            "reasonCode": str(listed.get("reasonCode") or "comments_unknown"),
            "summary": str(listed.get("summary") or "Comment read inconclusive; deferred without ownership decisions."),
        }
    else:
        return {"ok": False, "reasonCode": "comments_unknown", "summary": "Comment read inconclusive; deferred without ownership decisions."}
    normalized_comments = [
        {"body": str(comment.get("body") or ""), "author_login": _comment_author(comment)}
        for comment in comments
    ]
    allow_list = list(trusted_posters) if trusted_posters is not None else _trusted_posters(service=service)
    assessment = cutover.assess_legacy_issue(
        repository=_string(repository),
        issue_number=int(issue_number),
        issue=dict(issue_payload) if isinstance(issue_payload, Mapping) else {},
        comments=normalized_comments,
        prs=list(prs or []),
        checkpoints=list(checkpoints or []),
        trusted_posters=allow_list,
        issue_flags=dict(issue_flags or {}),
    )
    result = assessment.to_dict()
    result["ok"] = True
    result["reasonCode"] = "assessed"
    if comments_incomplete:
        result["complete"] = False
        result["completenessNote"] = (
            f"Comment history exceeded the {cutover.MAX_COMMENTS_ASSESSED}-comment bound; "
            "result is partial, never a clean repository."
        )
    return result


async def plan_legacy_cutover_repair(
    *,
    assessment: Mapping[str, Any],
    from_settled: str,
    to_target: str,
    evidence: Mapping[str, Any] | None = None,
    reason: str = "",
    operator_decision: Mapping[str, Any] | None = None,
    conclusively_stopped: bool = False,
) -> dict[str, Any]:
    """Plan one legacy repair through the shared evidence/authorization rules.

    Pure wrapper over :func:`plan_legacy_repair`: ambiguous evidence stays
    blocked without an explicit ``operator_decision`` carrying
    ``authorized_resolution``. Returns the guarded plan for the existing
    finalization/reconciliation writers; this function performs no GitHub
    writes itself.
    """
    rebuilt = cutover.LegacyAssessment(
        repository=_string(assessment.get("repository")),
        issue_number=int(assessment.get("issueNumber") or 0),
        settled=_string(assessment.get("settled")),
        complete=bool(assessment.get("complete", True)),
        completeness_note=_string(assessment.get("completenessNote")),
        findings=[
            cutover.LegacyFinding(
                finding=_string(item.get("finding")),
                evidence=_string(item.get("evidence")),
                owner=_string(item.get("owner") or "unknown"),
                suggested_action=_string(item.get("suggestedAction")),
            )
            for item in (assessment.get("findings") or [])
            if isinstance(item, Mapping)
        ],
        preserved=[str(item) for item in (assessment.get("preserved") or []) if str(item)],
    )
    plan = cutover.plan_legacy_repair(
        assessment=rebuilt,
        from_settled=_string(from_settled),
        to_target=_string(to_target),
        evidence=dict(evidence or {}),
        reason=reason,
        operator_decision=dict(operator_decision or {}),
        conclusively_stopped=bool(conclusively_stopped),
    )
    return {"ok": True, "reasonCode": plan.get("reasonCode") or "", **plan}


def legacy_cutover_drainage_plan(pending: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Build the tested drainage plan for pending retained updates."""
    return cutover.drainage_plan_for_pending(pending)


def evaluate_legacy_cutover_deployment(devices: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Evaluate whether participating devices may claim coordinated support."""
    return cutover.evaluate_mixed_deployment(devices)


__all__ = [
    "assess_legacy_cutover_issue",
    "plan_legacy_cutover_repair",
    "legacy_cutover_drainage_plan",
    "evaluate_legacy_cutover_deployment",
]
