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
        "MOONMIND_GITHUB_RECONCILIATION_STATE_DIR", "var/artifacts/github_issue_reconciliation"
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


def _validated_handoffs(comments: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Extract provenance-validated attempt handoffs; never trust prose."""
    from moonmind.workflows.temporal.github_issue_attempt import extract_attempt_metadata

    handoffs: list[dict[str, Any]] = []
    incomplete = False
    for comment in comments:
        if not isinstance(comment, Mapping):
            incomplete = True
            continue
        metadata, error = extract_attempt_metadata(comment.get("body"))
        if metadata is None:
            if error:
                # Malformed marker-bearing comment: conflicting evidence is
                # surfaced, never silently treated as no owner.
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
        recon.drop_pending_effect(state, repository=repository, issue_number=issue_number)
        return outcome

    # Re-read attempt comments (bounded; unreadable != absent) (Req 6).
    listed = await service.list_issue_comments(repo=repository, issue_number=issue_number)
    if not _spend():
        outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="request_budget_exhausted", summary="Budget exhausted after issue read; deferred.")
        return outcome
    comments_ok = isinstance(listed, Mapping) and bool(listed.get("ok"))
    comments: list[Mapping[str, Any]] = []
    comments_incomplete = True
    if comments_ok and isinstance(listed.get("comments"), list):
        comments = [c for c in listed["comments"] if isinstance(c, Mapping)][: recon.MAX_COMMENTS_PER_ISSUE]
        comments_incomplete = bool(listed.get("incomplete")) or False
    elif _is_rate_limit(listed if isinstance(listed, Mapping) else {}):
        outcome.update(
            action=recon.ACTION_DEFERRED_UNKNOWN,
            reasonCode="rate_limited",
            summary="Comment read rate limited; deferred with explicit uncertainty.",
            rateLimited=True,
        )
        return outcome
    handoffs, malformed = _validated_handoffs(comments)
    trusted = [h for h in handoffs if _string(h.get("attemptId"))]
    pending = recon.pending_effects_for_issue(state, repository=repository, issue_number=issue_number)
    known = pending[0] if pending else {}

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
        preservation_evidence = {
            "save_method": "pr_head_verified",
            "pr_url": pr_url,
            "pr_head_sha": _string(preserved.get("prHeadSha")) or _string(pr_state.get("headSha")),
            "pr_base": _string(preserved.get("prBase")),
            "revision": _string(preserved.get("prHeadSha")) or _string(pr_state.get("headSha")),
            "preservation_verified": bool(pr_state.get("known")) and bool(_string(preserved.get("prHeadSha")) or _string(pr_state.get("headSha"))),
        }
    elif _string(newest.get("outcome")) == "no-work":
        preservation_evidence = {"save_method": "explicit_no_work", "trustworthy_no_work": True}
    proposed_disposition = _string(newest.get("pendingDisposition")) or _string(known.get("proposedDisposition"))
    intended_to = _string(known.get("intendedToTarget"))
    if not intended_to and proposed_disposition:
        intended_to = {
            "needs_attention": "to_needs_attention",
            "recovery_needed": "to_recovery_needed",
            "available": "to_available",
            "code_review": "to_code_review",
            "closed": "to_closed",
        }.get(proposed_disposition.strip().lower(), "")

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
                    recon.drop_pending_effect(state, repository=repository, issue_number=issue_number)
                    outcome.update(action=recon.ACTION_ABANDONED, reasonCode="successor_observed", summary=f"Repair abandoned on re-read: {abandon_reason}.")
                    return outcome
            # Targeted ops only, destination first (Req 5 / design 8.1).
            for label in mutation.get("labelsToAdd") or []:
                added = await service.add_issue_labels(repo=repository, issue_number=issue_number, labels=[label])
                if not _spend():
                    break
                if not (isinstance(added, Mapping) and added.get("ok")):
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="repair_write_unknown", summary=f"Repair add-label outcome unknown: {added.get('summary') if isinstance(added, Mapping) else 'transport'}. Pending evidence retained.")
                    recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair add-label unknown")
                    return outcome
            for label in mutation.get("labelsToRemove") or []:
                removed = await service.remove_issue_label(repo=repository, issue_number=issue_number, label=label)
                if not _spend():
                    break
                if not (isinstance(removed, Mapping) and removed.get("ok")):
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="repair_write_unknown", summary="Repair remove-label outcome unknown. Pending evidence retained.")
                    recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair remove-label unknown")
                    return outcome
            if mutation.get("closeIssue"):
                closed = await service.close_issue(repo=repository, issue_number=issue_number)
                if not _spend():
                    pass
                if not (isinstance(closed, Mapping) and closed.get("ok")):
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="repair_write_unknown", summary="Repair close outcome unknown. Pending evidence retained.")
                    recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="repair close unknown")
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
                    recon.drop_pending_effect(state, repository=repository, issue_number=issue_number)
                    outcome.update(action=recon.ACTION_COMPLETE, reasonCode="repaired", summary=f"Interrupted transition completed and observed: {classified['detail']}")
                else:
                    recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason=f"read-back {classified['outcome']}")
                    outcome.update(action=recon.ACTION_DEFERRED_UNKNOWN, reasonCode="repair_unobserved", summary=f"Repair not observed on read-back ({classified['detail']}); pending evidence retained.")
            else:
                recon.record_pending_effect(state, repository=repository, issue_number=issue_number, intended_from_settled=decision.from_settled, intended_to_target=decision.to_target, proposed_disposition=proposed_disposition, reason="read-back unknown")
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
        if comments_incomplete:
            outcome["summary"] += " Comment scan may be incomplete; uncertainty preserved."
        return outcome

    # no_action / abandoned / deferred: drop obsolete pending, keep the rest.
    if decision.action == recon.ACTION_ABANDONED:
        recon.drop_pending_effect(state, repository=repository, issue_number=issue_number)
    return outcome


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
    readiness = recon.check_reconciliation_readiness(
        token_available=bool(token),
        token_error=_string(token_error),
        repository_authorized=True,
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
