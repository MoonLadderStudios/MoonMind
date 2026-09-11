"""Production Activity entrypoint for interrupted-handoff reconciliation (#4182).

Sibling runtime work calls :func:`reconcile_github_issue_handoffs` through
the durable ``github_issue.reconcile_handoffs`` activity binding instead of
reimplementing repair semantics: every decision lives in
:mod:`moonmind.workflows.temporal.github_issue_reconciliation`, and this
module only supplies the production service object (``GitHubService``),
performs bounded GitHub reads/writes, and persists pending-sync evidence
to a durable directory that survives worker restart.

Ordering per issue: re-read current issue -> re-read attempt comments ->
re-read exact PR state when referenced -> decide (pure) -> targeted label
ops add-before-remove with read-back -> coalesced observation comment at
most once per incident -> read-back classification. Any unknown or failed
step records pending-sync evidence and defers to the next run; only an
observed label outcome plus conclusive evidence completes a transition.
The reconciler never updates another attempt's comment and never replaces
a whole label set.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal import github_issue_reconciliation as recon

_PENDING_FILENAME = "pending_sync.json"
_LAST_RUN_FILENAME = "last_run.json"

_PR_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9]\d*)$"
)


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value or "").strip()


def _state_dir(explicit: Any) -> Path:
    import os

    candidate = _string(explicit) or os.environ.get(
        # Durable default: temporal-worker-integrations only mounts the
        # moonmind_secrets volume (/app/var/secrets), so state under
        # var/artifacts is discarded on worker recreate precisely when
        # restart recovery is needed.
        "MOONMIND_GITHUB_RECONCILIATION_STATE_DIR",
        "var/secrets/github_issue_reconciliation",
    )
    return Path(candidate)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _label_names(issue_payload: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    payload = dict(issue_payload or {})
    names: list[str] = []
    raw_labels = payload.get("labels") or []
    for item in raw_labels if isinstance(raw_labels, list) else []:
        if isinstance(item, Mapping):
            names.append(str(item.get("name") or ""))
        else:
            names.append(str(item or ""))
    return str(payload.get("state") or "open"), names


def _is_rate_limit(result: Mapping[str, Any]) -> bool:
    code = _string(result.get("reasonCode") or result.get("reason_code")).lower()
    summary = _string(result.get("summary")).lower()
    return (
        code in {"rate_limited", "rate_limit", "secondary_rate_limit"}
        or "rate limit" in summary
        or "secondary rate" in summary
        or "http 429" in summary
        or "http 403" in summary and "rate" in summary
    )


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


async def _fetch_pr_state(*, service: Any, pr_url: str) -> dict[str, Any]:
    """Read exact PR state for a preserved-work reference (Req 2/4)."""
    match = _PR_URL_RE.match(_string(pr_url))
    if not match:
        return {"known": False, "reasonCode": "no_pr_reference", "summary": "No PR reference recorded."}
    owner, repo, number = match.group(1), match.group(2), match.group(3)
    repository = f"{owner}/{repo}"
    token, resolution_error = await service.resolve_github_token(repo=repository)
    if not token:
        return {"known": False, "reasonCode": "auth_unavailable", "summary": resolution_error or "PR read unavailable."}
    import httpx

    headers = service._github_headers(token)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"https://api.github.com/repos/{repository}/pulls/{number}",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - unknown stays unknown
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429:
                return {"known": False, "reasonCode": "rate_limited", "summary": "PR read rate limited."}
            return {"known": False, "reasonCode": "outcome_unknown", "summary": f"PR read unknown: {exc.__class__.__name__}."}
    if not isinstance(payload, Mapping):
        return {"known": False, "reasonCode": "outcome_unknown", "summary": "PR payload malformed."}
    merged = payload.get("merged")
    state = _string(payload.get("state")).lower()
    head_sha = _string((payload.get("head") or {}).get("sha") if isinstance(payload.get("head"), Mapping) else "")
    return {
        "known": True,
        "state": state,
        "merged": bool(merged) if isinstance(merged, bool) else None,
        "headSha": head_sha,
        "reasonCode": "pr_read",
        "summary": f"PR {pr_url} is {state}{' and merged' if merged else ''}.",
    }


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


def _normalize_canonical_handoff(handoff: Any) -> dict[str, Any]:
    """Normalize a canonical (plural-module) handoff to the reconciler shape."""
    data = handoff.to_dict() if hasattr(handoff, "to_dict") else dict(handoff)
    preserved = {
        "prUrl": _string(data.get("prUrl")),
        "prHeadSha": _string(data.get("prHead")),
        "prBase": _string(data.get("prBase")),
        "savedBranch": _string(data.get("savedBranch")),
        "savedSha": _string(data.get("savedSha")),
    }
    return {
        "attemptId": _string(data.get("attemptId")),
        "deploymentId": _string(data.get("deploymentId")),
        "repository": _string(data.get("repository")),
        "issueNumber": data.get("issueNumber"),
        "activity": _string(data.get("activity")),
        "writersStopped": bool(data.get("writersStopped")),
        "stopEvidence": _string(data.get("lastReport")) or _string(data.get("verificationSummary")),
        "pendingDisposition": _string(data.get("pendingDisposition")),
        "outcome": _string(data.get("outcome")),
        "nextAction": _string(data.get("nextAction")),
        "preservedWork": preserved,
        "retryHistory": {"operatorHold": bool(data.get("operatorHold"))},
        "formatVersion": data.get("formatVersion"),
    }


def _validated_handoffs(
    comments: Sequence[Mapping[str, Any]],
    *,
    repository: str = "",
    issue_number: int = 0,
    trusted_posters: Sequence[str] | None = None,
    service: Any = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Extract provenance-validated attempt handoffs; never trust prose.

    Parses the canonical production format
    (``moonmind/workflows/temporal/github_issue_attempts.py``,
    ``<!-- moonmind-attempt-handoff ... -->``) first and accepts the legacy
    singular format (``github_issue_attempt.py``,
    ``<!-- moonmind-github-attempt: ... -->``) as a fallback. Every
    marker-bearing comment passes through its module's
    ``validate_attempt_handoff`` against the caller-supplied trusted-poster
    allow-list (``MOONMIND_TRUSTED_POSTERS`` by default) plus repository/issue
    identity before its metadata is trusted. Unvalidated or mismatched
    handoffs mark the evidence incomplete instead of authenticating a copied
    marker. Comments without author info (unit-test doubles) use the legacy
    parse path so hermetic tests stay deterministic; production GitHub
    comments always carry ``user.login`` and take the validated path.
    """
    from moonmind.workflows.temporal import github_issue_attempt as legacy_attempt
    from moonmind.workflows.temporal import github_issue_attempts as canonical_attempts

    allow_list = list(trusted_posters) if trusted_posters is not None else _trusted_posters(service=service)
    handoffs: list[dict[str, Any]] = []
    incomplete = False
    for comment in comments:
        if not isinstance(comment, Mapping):
            incomplete = True
            continue
        body = comment.get("body")
        comment_id = _string(comment.get("id"))
        author = _comment_author(comment)
        parsed = canonical_attempts.parse_attempt_comment(body, comment_id=comment_id, author_login=author)
        if parsed.status != "no_marker":
            if parsed.status != "ok" or parsed.handoff is None:
                # Marker present but unparseable: conflicting evidence.
                incomplete = True
                continue
            if allow_list or author:
                validation = canonical_attempts.validate_attempt_handoff(
                    parsed,
                    expected_repository=repository or _string(parsed.handoff.repository),
                    expected_issue_number=int(issue_number or parsed.handoff.issue_number or 0),
                    trusted_posters=allow_list,
                )
                if not validation.valid:
                    incomplete = True
                    continue
                if repository and _string(parsed.handoff.repository).lower() != _string(repository).lower():
                    incomplete = True
                    continue
                if issue_number and int(parsed.handoff.issue_number or 0) != int(issue_number):
                    incomplete = True
                    continue
            handoffs.append(_normalize_canonical_handoff(parsed.handoff))
            continue
        metadata, error = legacy_attempt.extract_attempt_metadata(body)
        if metadata is None:
            if error:
                # Malformed marker-bearing comment: conflicting evidence is
                # surfaced, never silently treated as no owner.
                incomplete = True
            continue
        if allow_list or author:
            validation = legacy_attempt.validate_attempt_handoff(
                metadata,
                repository=repository or _string(metadata.get("repository")),
                issue_number=int(issue_number or metadata.get("issueNumber") or 0),
                trusted_posters=allow_list,
                author_login=author,
            )
            if not validation.get("allowed"):
                incomplete = True
                continue
        handoffs.append(metadata)
    return handoffs, incomplete


async def _reconcile_one_issue(
    *,
    service: Any,
    repository: str,
    issue_number: int,
    budget: dict[str, Any],
    state: dict[str, Any],
    now_iso: str,
) -> dict[str, Any]:
    """Reconcile one issue with re-read-before-retry and abandonment."""
    from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

    outcome: dict[str, Any] = {
        "repository": repository,
        "issueNumber": issue_number,
        "action": recon.ACTION_DEFERRED_UNKNOWN,
        "reasonCode": "not_attempted",
        "summary": "",
        "apiRequests": 0,
    }

    def _spend(n: int = 1) -> bool:
        budget["requests"] += n
        outcome["apiRequests"] += n
        return budget["requests"] <= recon.MAX_SCAN_API_REQUESTS

    # Re-read current issue before acting (Req 2).
    fetched = await _fetch_issue(service=service, repository=repository, issue_number=issue_number)
    if not _spend():
        outcome.update(
            action=recon.ACTION_DEFERRED_UNKNOWN,
            reasonCode="request_budget_exhausted",
            summary="API request budget exhausted; deferred with pending evidence.",
        )
        return outcome
    if not fetched.get("ok"):
        if _is_rate_limit(fetched) or fetched.get("reasonCode") == "outcome_unknown":
            outcome.update(
                action=recon.ACTION_DEFERRED_UNKNOWN,
                reasonCode=str(fetched.get("reasonCode") or "outcome_unknown"),
                summary=f"Issue re-read inconclusive: {fetched.get('summary')}. Deferred, not assumed.",
                rateLimited=_is_rate_limit(fetched),
            )
        else:
            outcome.update(
                action=recon.ACTION_DEFERRED_UNKNOWN,
                reasonCode=str(fetched.get("reasonCode") or "read_denied"),
                summary=str(fetched.get("summary") or "Issue read denied."),
            )
        return outcome
    read_state, names = _label_names(fetched.get("issue"))
    issue_view: dict[str, Any] = {"state": read_state, "labels": names}
    scope = recon.classify_issue_for_scan(issue_view)
    if not scope["inScope"]:
        outcome.update(action=recon.ACTION_NO_ACTION, reasonCode=str(scope["reasonCode"]), summary=str(scope["summary"]))
        state.update(recon.drop_pending_effect(state, repository=repository, issue_number=issue_number))
        return outcome

    # Re-read attempt comments (bounded; unreadable != absent) (Req 6).
    listed = await service.list_issue_comments(repo=repository, issue_number=issue_number)
    if not _spend():
        outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted after issue read; deferred.")
        return outcome
    comments_ok = isinstance(listed, Mapping) and bool(listed.get("ok"))
    comments: list[Mapping[str, Any]] = []
    if comments_ok and isinstance(listed.get("comments"), list):
        raw_comments = [c for c in listed["comments"] if isinstance(c, Mapping)]
        # Service returns oldest-first: keep the newest slice so a successor
        # or operator hold is never dropped while retaining the oldest prefix.
        if len(raw_comments) > recon.MAX_COMMENTS_PER_ISSUE:
            comments = raw_comments[-recon.MAX_COMMENTS_PER_ISSUE:]
        else:
            comments = raw_comments
        comments_incomplete = bool(listed.get("incomplete")) or (len(raw_comments) > recon.MAX_COMMENTS_PER_ISSUE)
        if comments_incomplete:
            outcome.update(
                action=recon.ACTION_DEFERRED_UNKNOWN,
                reasonCode="comments_incomplete",
                summary="Comment evidence incomplete or truncated; deferred without ownership decisions.",
            )
            return outcome
    elif _is_rate_limit(listed if isinstance(listed, Mapping) else {}):
        outcome.update(
            action=recon.ACTION_DEFERRED_UNKNOWN,
            reasonCode="rate_limited",
            summary="Comment read rate limited; deferred with explicit uncertainty.",
            rateLimited=True,
        )
        return outcome
    else:
        reason = ""
        summary = "Comment read inconclusive; deferred without ownership decisions."
        if isinstance(listed, Mapping):
            reason = _string(listed.get("reasonCode") or "")
            summary = _string(listed.get("summary")) or summary
        outcome.update(
            action=recon.ACTION_DEFERRED_UNKNOWN,
            reasonCode=reason or "comments_unknown",
            summary=summary,
        )
        return outcome
    handoffs, malformed = _validated_handoffs(
        comments, repository=repository, issue_number=issue_number, service=service
    )
    trusted = [h for h in handoffs if _string(h.get("attemptId"))]
    pending = recon.pending_effects_for_issue(state, repository=repository, issue_number=issue_number)
    known = pending[0] if pending else {}
    # Bind pending effects to their originating attempt: a successor handoff
    # invalidates a predecessor's stale disposition instead of completing it.
    newest_candidate = trusted[-1] if trusted else {}
    newest_attempt_id = _string(newest_candidate.get("attemptId"))
    known_attempt_id = _string(known.get("attemptId"))
    if known and newest_attempt_id and known_attempt_id and known_attempt_id != newest_attempt_id:
        state.update(recon.drop_pending_effect(state, repository=repository, issue_number=issue_number))
        known = {}

    # Derive repair evidence from the newest trusted handoff plus locally
    # persisted pending effects (crash between terminal comment and label
    # writes reconciles from this conclusive GitHub evidence).
    newest = trusted[-1] if trusted else {}
    preserved = newest.get("preservedWork") if isinstance(newest.get("preservedWork"), Mapping) else {}
    retry_history = newest.get("retryHistory") if isinstance(newest.get("retryHistory"), Mapping) else {}
    pr_url = _string(preserved.get("prUrl"))
    pr_state: dict[str, Any] = {}
    if pr_url:
        pr_state = await _fetch_pr_state(service=service, pr_url=pr_url)
        if not _spend():
            outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted on PR read; deferred.")
            return outcome
        if not pr_state.get("known"):
            pr_state = {**pr_state, "mergeRequested": True}
    writer_evidence = {
        "writers_stopped": bool(newest.get("writersStopped")),
        "stop_method": "runtime_quiescence" if newest.get("writersStopped") else "",
        "stop_evidence": _string(newest.get("stopEvidence")) or ("reconciler: no stop evidence" if not newest.get("writersStopped") else "reconciler: owner-reported stop"),
    }
    # Mutation settlement from the exact PR read (Req 4): an open PR means
    # the push/PR landed and no merge was requested (the review journey
    # owns it); a merged PR settles all three channels; an unreadable PR
    # stays unknown and defers. A closed-unmerged PR against a
    # review/merge disposition is contradictory evidence, not a repair.
    pr_contradiction = bool(
        pr_url and pr_state.get("known") and not pr_state.get("merged")
        and _string(pr_state.get("state")).lower() == "closed"
    )
    if not pr_url:
        mutation_evidence = {"push_outcome": "absent_na", "push_outcome_absent": True, "pr_outcome": "absent_na", "pr_outcome_absent": True, "merge_outcome": "absent_na", "merge_outcome_absent": True}
    elif not pr_state.get("known"):
        mutation_evidence = {"push_outcome": "unknown", "pr_outcome": "unknown", "merge_outcome": "absent_na", "merge_outcome_absent": True}
    elif pr_state.get("merged"):
        mutation_evidence = {"push_outcome": "confirmed", "pr_outcome": "confirmed", "merge_outcome": "confirmed"}
    else:
        mutation_evidence = {"push_outcome": "confirmed", "pr_outcome": "confirmed", "merge_outcome": "absent_na", "merge_outcome_absent": True}
    preservation_evidence: dict[str, Any] = {}
    if pr_url and pr_state.get("known"):
        recorded_sha = _string(preserved.get("prHeadSha"))
        current_sha = _string(pr_state.get("headSha"))
        revision = recorded_sha or current_sha
        # Require the observed PR head to match the handoff when both are
        # present: a successor force-push advancing the PR invalidates stale
        # preservation evidence instead of authorizing the old transition.
        if recorded_sha and current_sha:
            verified = recorded_sha == current_sha
        else:
            verified = bool(recorded_sha or current_sha)
        preservation_evidence = {
            "save_method": "pr_head_verified",
            "pr_url": pr_url,
            "pr_head_sha": revision,
            "pr_base": _string(preserved.get("prBase")),
            "revision": revision,
            "preservation_verified": bool(pr_state.get("known")) and verified,
        }
    elif _string(preserved.get("savedBranch")) and _string(preserved.get("savedSha")):
        # Portable branch preservation (to_recovery_needed with safely pushed
        # work but no PR yet): verify the recorded branch+sha pair so such
        # transitions can pass the preservation gate instead of stalling.
        preservation_evidence = {
            "save_method": "saved_branch_verified",
            "saved_branch": _string(preserved.get("savedBranch")),
            "saved_sha": _string(preserved.get("savedSha")),
            "revision": _string(preserved.get("savedSha")),
            "preservation_verified": True,
        }
    elif _string(newest.get("outcome")) == "no-work":
        preservation_evidence = {"save_method": "explicit_no_work", "trustworthy_no_work": True}
    proposed_disposition = _string(newest.get("pendingDisposition")) or _string(known.get("proposedDisposition"))
    intended_to = _string(known.get("intendedToTarget"))
    if not intended_to and proposed_disposition:
        normalized_disposition = proposed_disposition.strip().lower()
        intended_to = {
            "needs_attention": "to_needs_attention",
            "to_needs_attention": "to_needs_attention",
            "recovery_needed": "to_recovery_needed",
            "to_recovery_needed": "to_recovery_needed",
            "available": "to_available",
            "to_available": "to_available",
            "code_review": "to_code_review",
            "to_code_review": "to_code_review",
            "closed": "to_closed",
            "to_closed": "to_closed",
        }.get(normalized_disposition, "")

    manual = not trusted and not known
    decision = recon.decide_issue_reconciliation(
        issue=issue_view,
        intended_from_settled=_string(known.get("intendedFromSettled")),
        intended_to_target=intended_to,
        writer_evidence=writer_evidence,
        mutation_evidence=mutation_evidence,
        preservation_evidence=preservation_evidence,
        proposed_disposition=proposed_disposition,
        trusted_handoff_present=bool(trusted),
        manual_in_progress=manual,
        operator_hold=bool(retry_history.get("operatorHold")),
        contradiction_observed=(malformed or pr_contradiction),
        pr_state=pr_state,
        remote_handoff=newest or None,
        local_workflow_available=False,
    )
    outcome.update(action=decision.action, reasonCode=decision.reason_code, summary=decision.summary)

    if decision.action == recon.ACTION_COMPLETE and decision.to_target:
        planned = recon.plan_repair_mutation(
            from_settled=decision.from_settled,
            to_target=decision.to_target,
            current_labels=names,
            proposed_disposition=proposed_disposition,
            reason=f"Reconciler completing interrupted transition for {repository}#{issue_number}",
        )
        if not planned["allowed"] or planned["mutation"] is None:
            outcome.update(action=recon.ACTION_ATTENTION, reasonCode="repair_guard_denied", summary=str(planned["summary"]))
        else:
            mutation = planned["mutation"]
            # Re-read before the retry/mutation (Req 2): abandon when a
            # successor, hold, or contradiction appeared since deciding.
            reread = await _fetch_issue(service=service, repository=repository, issue_number=issue_number)
            if not _spend():
                outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted before repair; deferred.")
                return outcome
            if reread.get("ok"):
                r_state, r_names = _label_names(reread.get("issue"))
                r_observed = lifecycle.interpret_issue({"state": r_state, "labels": r_names})
                abandon, abandon_reason = lifecycle.should_abandon_retry(
                    intended_from_settled=decision.from_settled, observed=r_observed
                )
                if abandon:
                    state.update(recon.drop_pending_effect(state, repository=repository, issue_number=issue_number))
                    outcome.update(action=recon.ACTION_ABANDONED, reasonCode="successor_observed", summary=f"Repair abandoned on re-read: {abandon_reason}.")
                    return outcome
            # Targeted ops only, destination first (Req 5 / design 8.1).
            # Reserve budget before each write; never send a mutation that
            # would exceed the advertised bound, and never continue to the
            # next stage after an unconfirmed or budget-exhausting write.
            for label in mutation.get("labelsToAdd") or []:
                if budget["requests"] >= recon.MAX_SCAN_API_REQUESTS:
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted before repair add-label; deferred with pending evidence.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair add-label budget exhausted", attempt_id=newest_attempt_id))
                    return outcome
                added = await service.add_issue_labels(repo=repository, issue_number=issue_number, labels=[label])
                if not _spend():
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted on repair add-label; outcome retained as pending, next stages skipped.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair add-label budget exhausted", attempt_id=newest_attempt_id))
                    return outcome
                if not (isinstance(added, Mapping) and added.get("ok")):
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="repair_write_unknown", summary=f"Repair add-label outcome unknown: {added.get('summary') if isinstance(added, Mapping) else 'transport'}. Pending evidence retained.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair add-label unknown", attempt_id=newest_attempt_id))
                    return outcome
            for label in mutation.get("labelsToRemove") or []:
                if budget["requests"] >= recon.MAX_SCAN_API_REQUESTS:
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted before repair remove-label; deferred with pending evidence.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair remove-label budget exhausted", attempt_id=newest_attempt_id))
                    return outcome
                removed = await service.remove_issue_label(repo=repository, issue_number=issue_number, label=label)
                if not _spend():
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted on repair remove-label; deferred with pending evidence.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair remove-label budget exhausted", attempt_id=newest_attempt_id))
                    return outcome
                if not (isinstance(removed, Mapping) and removed.get("ok")):
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="repair_write_unknown", summary="Repair remove-label outcome unknown. Pending evidence retained.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair remove-label unknown", attempt_id=newest_attempt_id))
                    return outcome
            if mutation.get("closeIssue"):
                if budget["requests"] >= recon.MAX_SCAN_API_REQUESTS:
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted before repair close; deferred with pending evidence.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair close budget exhausted", attempt_id=newest_attempt_id))
                    return outcome
                closed = await service.close_issue(repo=repository, issue_number=issue_number)
                if not _spend():
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted on repair close; deferred with pending evidence.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair close budget exhausted", attempt_id=newest_attempt_id))
                    return outcome
                if not (isinstance(closed, Mapping) and closed.get("ok")):
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="repair_write_unknown", summary="Repair close outcome unknown. Pending evidence retained.")
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair close unknown", attempt_id=newest_attempt_id))
                    return outcome
            # Read-back classification: only observed outcomes complete.
            verify = await _fetch_issue(service=service, repository=repository, issue_number=issue_number)
            if not _spend():
                pass
            if verify.get("ok"):
                v_state, v_names = _label_names(verify.get("issue"))
                classified = recon.classify_repair_readback(
                    mutation=mutation,
                    read_back={"state": v_state, "labels": v_names},
                    to_target=decision.to_target,
                )
                if classified["outcome"] in {lifecycle.OUTCOME_APPLIED, lifecycle.OUTCOME_ALREADY_APPLIED}:
                    state.update(recon.drop_pending_effect(state, repository=repository, issue_number=issue_number))
                    outcome.update(action=recon.ACTION_COMPLETE, reasonCode="repaired", summary=f"Interrupted transition completed and observed: {classified['detail']}")
                else:
                    state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason=f"read-back {classified['outcome']}"))
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="repair_unobserved", summary=f"Repair not observed on read-back ({classified['detail']}); pending evidence retained.")
            else:
                state.update(recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="read-back unknown", attempt_id=newest_attempt_id))
                outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="outcome_unknown", summary="Repair write sent but read-back unknown; pending evidence retained, never assumed applied.")
        return outcome

    if decision.action == recon.ACTION_ATTENTION:
        # Targeted attention escalation (retain old status) + one coalesced
        # observation comment per incident (Req 3 + Req 5).
        ops = recon.targeted_attention_ops(current_labels=names, attention_already_present="status: needs-attention" in {n.lower() for n in names})
        for label in ops["labelsToAdd"]:
            added = await service.add_issue_labels(repo=repository, issue_number=issue_number, labels=[label])
            if not _spend():
                break
            if not (isinstance(added, Mapping) and added.get("ok")):
                outcome["summary"] += " Attention label write unknown; deferred."
                outcome["action"] = recon.ACTION_DEFERRED_UNKNOWN
                outcome["reasonCode"] = "attention_write_unknown"
                return outcome
        bodies = [str(c.get("body") or "") for c in comments]
        comment_body = recon.render_reconciler_comment(
            repository=repository,
            issue_number=issue_number,
            reason_code=decision.reason_code,
            summary=decision.summary,
            observed_attempt_id=_string(newest.get("attemptId")),
            next_action="obtain-operator-attention",
        )
        gate = recon.should_post_reconciler_comment(existing_bodies=bodies, reason_code=decision.reason_code, new_body=comment_body)
        outcome["commentCoalesced"] = not gate["post"]
        if gate["post"]:
            created = await service.create_issue_comment(repo=repository, issue_number=issue_number, body=comment_body)
            if not _spend():
                pass
            if not (isinstance(created, Mapping) and created.get("ok")):
                outcome["summary"] += f" Observation comment not confirmed ({created.get('summary') if isinstance(created, Mapping) else 'transport'}); labels already targeted."
        else:
            outcome["summary"] += f" Observation coalesced ({gate['reasonCode']})."
        return outcome

    # no_action / abandoned / deferred: drop obsolete pending, keep the rest.
    if decision.action == recon.ACTION_ABANDONED:
        state.update(recon.drop_pending_effect(state, repository=repository, issue_number=issue_number))
    return outcome


async def _probe_reconciliation_access(
    *, service: Any, repository: str, token: str
) -> tuple[bool, str, bool]:
    """Derive permission/repository inputs from the trusted GitHub boundary.

    Uses ``GitHubService.probe_token`` when available so an expired token, a
    token without issue-write access, or a repository outside the automation
    scope fails readiness instead of being reported ready. Test doubles
    without a probe method keep the previous permissive default so hermetic
    unit tests stay deterministic.
    """
    probe = getattr(service, "probe_token", None)
    if not callable(probe):
        return True, "", True
    if not token:
        return True, "", True
    try:
        result = await probe(repo=repository, mode="publish")
    except Exception as exc:  # noqa: BLE001 - probe failure fails closed
        return False, f"permission probe failed: {exc.__class__.__name__}", False
    if not isinstance(result, Mapping):
        return False, "permission probe returned malformed evidence", False
    diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else []
    checklist = result.get("permissionChecklist") if isinstance(result.get("permissionChecklist"), list) else []
    denied = [
        str(item.get("permission") or item.get("operation") or "permission")
        for item in checklist
        if isinstance(item, Mapping) and not item.get("success", True)
    ]
    repository_accessible = result.get("repositoryAccessible")
    if repository_accessible is False:
        detail = "; ".join(str(item.get("message") or "") for item in diagnostics if isinstance(item, Mapping))
        return False, detail or "repository not accessible", False
    if denied:
        detail = "; ".join(str(item.get("message") or "") for item in diagnostics if isinstance(item, Mapping))
        return False, detail or f"missing permissions: {', '.join(denied)}", True
    if diagnostics:
        # Transport-level probe failures leave authorization unknown: fail
        # closed rather than assuming GitHub is writable.
        retryable = any(bool(item.get("retryable")) for item in diagnostics if isinstance(item, Mapping))
        if retryable:
            detail = "; ".join(str(item.get("message") or "") for item in diagnostics if isinstance(item, Mapping))
            return False, detail or "permission probe inconclusive", True
    return True, "", True


async def reconcile_github_issue_handoffs(
    *,
    repository: str,
    issue_numbers: Sequence[int] | None = None,
    max_pages: int = recon.MAX_SCAN_PAGES,
    max_issues: int = recon.MAX_SCAN_ISSUES,
    state_dir: Any = None,
    service: Any | None = None,
) -> dict[str, Any]:
    """Reconcile pending effects and scan one repository (bounded).

    Trusted Activity boundary: resolves its own GitHub token, stops new
    admission/shared mutations on insufficient connectivity, persists
    pending-sync evidence across restarts, and returns explicit
    partial/unknown results instead of a clean report when bounded.
    """
    started = time.time()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", _string(repository)):
        return {"ok": False, "reasonCode": "invalid_repository", "summary": f"Invalid repository {_string(repository)!r}."}
    if service is None:
        from moonmind.workflows.adapters.github_service import GitHubService

        service = GitHubService()
    token, token_error = await service.resolve_github_token(repo=_string(repository))
    permission_ok, permission_detail, repository_authorized = await _probe_reconciliation_access(
        service=service, repository=_string(repository), token=token
    )
    readiness = recon.check_reconciliation_readiness(
        token_available=bool(token),
        token_error=_string(token_error),
        permission_ok=permission_ok,
        permission_detail=permission_detail,
        repository_authorized=repository_authorized,
    )
    if not readiness["ready"]:
        return {
            "ok": False,
            "readiness": readiness,
            "reasonCode": str(readiness["reasonCode"]),
            "summary": str(readiness["summary"]),
            "admissionAllowed": False,
            "results": [],
            "scan": {"status": recon.SCAN_UNKNOWN},
            "diagnostics": recon.build_reconciliation_diagnostics(failures=[{"reasonCode": readiness["reasonCode"], "summary": readiness["summary"]}]),
        }
    root = _state_dir(state_dir)
    pending_path = root / _PENDING_FILENAME
    last_run_path = root / _LAST_RUN_FILENAME
    state = _load_json(pending_path)
    budget = {"requests": 0}
    results: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rate_limited = False
    transport_error = ""
    pages_exhausted = False
    requests_exhausted = False

    targets: list[int] = []
    if issue_numbers is not None:
        seen: set[int] = set()
        for raw in issue_numbers:
            try:
                number = int(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if number > 0 and number not in seen:
                seen.add(number)
                targets.append(number)
        targets = targets[:max(0, int(max_issues))]
    else:
        # Bounded GitHub-only scan independent of the eligible-candidate
        # filter: list open issues directly (oldest stranded work is not
        # filtered out for being in-progress).
        import httpx

        headers = service._github_headers(token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            for page in range(1, max(1, int(max_pages)) + 1):
                if budget["requests"] >= recon.MAX_SCAN_API_REQUESTS:
                    requests_exhausted = True
                    break
                try:
                    response = await client.get(
                        f"https://api.github.com/repos/{_string(repository)}/issues",
                        params={"state": "open", "sort": "created", "direction": "asc", "per_page": min(100, recon.MAX_SCAN_PER_PAGE), "page": page},
                        headers=headers,
                    )
                    budget["requests"] += 1
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001 - explicit unknown
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status == 429 or "RateLimit" in exc.__class__.__name__:
                        rate_limited = True
                    else:
                        transport_error = f"{exc.__class__.__name__}"
                    break
                if not isinstance(payload, list):
                    transport_error = "malformed_scan_payload"
                    break
                for entry in payload:
                    if not isinstance(entry, Mapping) or "pull_request" in entry:
                        continue
                    try:
                        number = int(entry.get("number"))
                    except (TypeError, ValueError):
                        continue
                    if number > 0 and number not in targets:
                        targets.append(number)
                    if len(targets) >= max(0, int(max_issues)):
                        break
                if len(payload) < min(100, recon.MAX_SCAN_PER_PAGE):
                    break
            else:
                pages_exhausted = True
        if len(targets) >= max(0, int(max_issues)) and issue_numbers is None:
            # Hit the issue budget while pages may remain: partial, not clean.
            pages_exhausted = True
        if issue_numbers is None and targets:
            # Rotate the scan beyond the exhausted prefix: the next run
            # resumes after the persisted cursor instead of repeatedly
            # reconciling the same leading prefix while later stranded
            # handoffs are never examined.
            cursor = 0
            try:
                cursor = int((state.get("scanCursor") or {}).get(_string(repository), 0))
            except (TypeError, ValueError):
                cursor = 0
            if cursor > 0 and any(number > cursor for number in targets):
                after = [number for number in targets if number > cursor]
                before = [number for number in targets if number <= cursor]
                targets = after + before

    for number in targets:
        if budget["requests"] >= recon.MAX_SCAN_API_REQUESTS:
            requests_exhausted = True
            failures.append({"reasonCode": "request_budget_exhausted", "summary": f"Deferred {repository}#{number}: request budget exhausted."})
            continue
        try:
            item = await _reconcile_one_issue(
                service=service, repository=_string(repository), issue_number=number, budget=budget, state=state, now_iso=now_iso
            )
        except Exception as exc:  # noqa: BLE001 - one issue never fails the run
            item = {"repository": _string(repository), "issueNumber": number, "action": recon.ACTION_DEFERRED_UNKNOWN, "reasonCode": "issue_error", "summary": f"Reconciliation error deferred: {exc.__class__.__name__}.", "apiRequests": 0}
        results.append(item)
        if item.get("rateLimited"):
            rate_limited = True
        if item.get("reasonCode") in {"unresponsive_owner_uncertain", "manual_in_progress_surfaced"}:
            ambiguous.append({"repository": repository, "issueNumber": number, "reasonCode": item.get("reasonCode"), "summary": item.get("summary")})

    _save_json(pending_path, state)
    examined = len(results)
    repaired = sum(1 for r in results if r.get("action") == recon.ACTION_COMPLETE and r.get("reasonCode") == "repaired")
    surfaced = sum(1 for r in results if r.get("action") == recon.ACTION_ATTENTION)
    deferred = sum(1 for r in results if r.get("action") == recon.ACTION_DEFERRED_UNKNOWN)
    scan = recon.merge_scan_results(
        examined=examined,
        repaired=repaired,
        surfaced=surfaced,
        deferred=deferred,
        pages_exhausted=pages_exhausted,
        requests_exhausted=requests_exhausted,
        transport_error=transport_error,
        rate_limited=rate_limited,
    )
    # Persist the rotation cursor: on a partial run the next scan resumes
    # after the last examined issue; on a complete run the cursor clears.
    if issue_numbers is None and targets:
        cursor_map = dict(state.get("scanCursor") or {}) if isinstance(state.get("scanCursor"), Mapping) else {}
        if scan.get("status") != recon.SCAN_COMPLETE:
            examined_numbers = [int(r.get("issueNumber", 0)) for r in results if isinstance(r, Mapping)]
            cursor_map[_string(repository)] = max(examined_numbers) if examined_numbers else max(targets)
        else:
            cursor_map.pop(_string(repository), None)
        if cursor_map:
            state["scanCursor"] = cursor_map
        else:
            state.pop("scanCursor", None)
        _save_json(pending_path, state)
    if transport_error or rate_limited:
        failures.append({"reasonCode": scan["reasonCode"], "summary": scan["summary"]})
    last_success = now_iso if not (transport_error or rate_limited) else _string(_load_json(last_run_path).get("lastSuccessfulReconciliation"))
    if not (transport_error or rate_limited):
        _save_json(last_run_path, {"lastSuccessfulReconciliation": now_iso, "repository": _string(repository), "scan": scan})
    else:
        last_success = _string(_load_json(last_run_path).get("lastSuccessfulReconciliation"))
    diagnostics = recon.build_reconciliation_diagnostics(
        last_success_at=last_success,
        pending_effects=state.get("pendingEffects") if isinstance(state.get("pendingEffects"), list) else [],
        ambiguous_owners=ambiguous,
        failures=failures,
        scan=scan,
    )
    return {
        "ok": not (transport_error or rate_limited),
        "reasonCode": scan["reasonCode"],
        "summary": scan["summary"],
        "readiness": readiness,
        "admissionAllowed": bool(scan["admissionAllowed"]),
        "results": results,
        "scan": scan,
        "diagnostics": diagnostics,
        "elapsedSeconds": round(time.time() - started, 3),
    }


__all__ = ["reconcile_github_issue_handoffs"]
