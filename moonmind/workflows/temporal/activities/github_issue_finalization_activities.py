"""Production Activity entrypoint for failed-attempt finalization (#4179).

Sibling runtime work calls :func:`finalize_failed_attempt` at the durable
controlling workflow boundary instead of reimplementing terminal-handoff
semantics: every decision lives in
:mod:`moonmind.workflows.temporal.github_issue_finalization`, and this
module only supplies the production service object (``GitHubService``) and
performs the ordered GitHub reads/writes (pre-mutation issue read ->
proposed comment (new, or appended to the canonical attempt comment) ->
destination labels -> read-back -> released comment).

Interruptibility: the result distinguishes ``released`` (label outcome
observed AND terminal comment published) from ``pending`` (interrupted or
unknown after any comment/label operation). Pending results remain
repairable by a later finalizer or the periodic reconciler and never claim
false release success. Failed GitHub reporting never discards the only
workspace/evidence (``workspaceRetained=True``).
"""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal import github_issue_finalization as finalization
from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle
from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment, render_attempt_comment
from moonmind.workflows.temporal.issue_claim_store import ClaimReceipt, IssueClaimStore, publish_claim_comment, reconcile_claim_comment


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value or "").strip()


def _issue_label_names(issue_payload: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    """Extract ``(state, label_names)`` from an issue payload."""
    payload = dict(issue_payload or {})
    names: list[str] = []
    raw_labels = payload.get("labels") or []
    for item in raw_labels if isinstance(raw_labels, list) else []:
        if isinstance(item, Mapping):
            names.append(str(item.get("name") or ""))
        else:
            names.append(str(item or ""))
    return str(payload.get("state") or "open"), names


def _scan_terminal_body(body: str) -> tuple[bool, str]:
    """Scan a rendered terminal comment; returns ``(blocked, redacted_detail)``.

    Reuses the repository outbound scanner (``moonmind.security``) with the
    same fail-closed posture as the canonical attempt-comment renderer:
    secret-like handoff text blocks posting instead of being published.
    """
    from moonmind.security import OutboundBundleItem, scan_outbound_bundle
    from moonmind.utils.logging import redact_sensitive_text

    try:
        result = scan_outbound_bundle(
            [OutboundBundleItem(location="finalization.terminal_comment", content=body)],
            high_security_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 - scanner failure must fail closed
        return True, f"scanner unavailable ({exc.__class__.__name__})"
    if not result.allowed:
        categories = sorted({finding.category for finding in result.findings})
        detail = redact_sensitive_text("; ".join(result.sanitized_diagnostics) or ",".join(categories))
        return True, detail or "secret-like content detected"
    return False, ""


async def _reuse_attempt_comment(
    *,
    service: Any,
    repository: str,
    issue_number: int,
    attempt_id: str,
    terminal_section: str,
) -> dict[str, Any]:
    """Append the terminal section to the canonical attempt comment.

    Returns ``{"action": "updated", "commentId": int}`` when exactly one
    comment carries this attempt's machine marker with agreeing embedded
    metadata (appending preserves the marker and metadata block, so
    admission keeps associating terminal comments with the attempt);
    ``{"action": "create"}`` when no such comment is readable (caller falls
    back to creating the terminal comment); ``{"action": "conflict"}`` when
    several copies share the marker (no write is authorized); and
    ``{"action": "pending", ...}`` when the update itself did not succeed.
    """
    from moonmind.workflows.temporal.github_issue_attempt import (
        ATTEMPT_MARKER_PREFIX,
        extract_attempt_metadata,
    )

    list_comments = getattr(service, "list_issue_comments", None)
    update_comment = getattr(service, "update_issue_comment", None)
    if list_comments is None or update_comment is None:
        return {"action": "create", "reason": "comment reuse surface unavailable"}
    try:
        listed = await service.list_issue_comments(repo=repository, issue_number=issue_number)
    except Exception as exc:  # noqa: BLE001 - unreadable != absent; caller creates
        return {"action": "create", "reason": f"comment list unknown: {exc.__class__.__name__}"}
    if not isinstance(listed, Mapping) or not listed.get("ok"):
        return {"action": "create", "reason": str((listed or {}).get("summary") or "comment list unavailable")}
    comments = listed.get("comments")
    if not isinstance(comments, list):
        return {"action": "create", "reason": "comment list malformed"}
    verified: list[tuple[int, str]] = []
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        body = str(comment.get("body") or "")
        if ATTEMPT_MARKER_PREFIX not in body or attempt_id not in body:
            continue
        metadata, _ = extract_attempt_metadata(body)
        if metadata is None or str(metadata.get("attemptId") or "") != attempt_id:
            continue
        try:
            candidate_id = int(comment.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        verified.append((candidate_id, body))
    if len(verified) > 1:
        return {
            "action": "conflict",
            "reason": f"{len(verified)} comments share attempt marker {attempt_id}; no overwrite authorized",
        }
    if not verified:
        return {"action": "create", "reason": "no canonical attempt comment found"}
    comment_id, remote_body = verified[0]
    merged = remote_body.rstrip() + "\n\n---\n\n" + terminal_section.strip() + "\n"
    try:
        updated = await service.update_issue_comment(repo=repository, comment_id=comment_id, body=merged)
    except Exception as exc:  # noqa: BLE001 - update result unknown; no duplicate created
        return {"action": "pending", "reason": f"attempt comment update unknown: {exc.__class__.__name__}"}
    if not isinstance(updated, Mapping) or not updated.get("ok"):
        code = str((updated or {}).get("reasonCode") or "update_failed")
        return {"action": "pending", "reason": f"attempt comment update {code}"}
    return {"action": "updated", "commentId": comment_id, "body": merged}


async def _fetch_issue(*, service: Any, repository: str, issue_number: int) -> dict[str, Any]:
    """Read the current issue through the production token boundary."""
    token, resolution_error = await service.resolve_github_token(repo=repository)
    if not token:
        return {"ok": False, "reasonCode": "auth_unavailable", "summary": resolution_error or "GitHub issue read unavailable."}
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
        except Exception as exc:  # noqa: BLE001 - transport shape mapped to pending
            name = exc.__class__.__name__
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {401, 403, 404}:
                return {"ok": False, "reasonCode": "denied", "summary": f"Issue read denied with HTTP {status}."}
            return {"ok": False, "reasonCode": "outcome_unknown", "summary": f"Issue read result unknown: {name}."}


async def finalize_failed_attempt(
    *,
    repository: str,
    issue_number: int,
    execution_event: Any = "",
    from_settled: str = "in_progress",
    current_labels: Sequence[Any] | None = None,
    writer_evidence: Mapping[str, Any] | None = None,
    mutation_evidence: Mapping[str, Any] | None = None,
    preservation_evidence: Mapping[str, Any] | None = None,
    disposition_evidence: Mapping[str, Any] | None = None,
    attempt_id: str = "",
    primary_outcome: str = "",
    met_requirements: Sequence[Any] | None = None,
    remaining_requirements: Sequence[Any] | None = None,
    retry_history: Any = "",
    next_action: str = "",
    reason: str = "",
    completion_mode: str = "pr_only_handoff",
    review_owner_ended: bool = False,
    cancellation_hold: bool = False,
    service: Any | None = None,
    claim_store: IssueClaimStore | None = None,
    claim_receipt: ClaimReceipt | None = None,
) -> dict[str, Any]:
    """Finalize one failed/canceled controlling attempt at the durable boundary.

    Ordered effects: pre-mutation issue read (labels + successor check) ->
    plan (pure) -> create proposed terminal comment (or append to the
    canonical attempt comment) -> apply destination labels add-before-remove
    -> close the issue when the destination is a closed terminal -> read back
    issue -> classify mutation outcome -> publish released terminal comment
    update. Any unknown or failed step returns ``released=False`` with
    ``pending`` detail and ``workspaceRetained=True``; only an observed label
    outcome plus a published terminal comment returns ``released=True``.
    """
    completion = finalization.route_completion_handoff(
        completion_mode=completion_mode,
        review_owner_ended=review_owner_ended,
        cancellation_hold=cancellation_hold,
    )
    if service is None:
        from moonmind.workflows.adapters.github_service import GitHubService

        service = GitHubService()
    saved_plan = None
    if claim_receipt is not None:
        if claim_store is None or (claim_receipt.repository, claim_receipt.issue_number) != (repository.casefold(), issue_number):
            raise ValueError("substitution_denied: finalization must use the controlling claim")
        attempt_id = claim_receipt.attempt_id
        receipts = claim_receipt.finalization_json or {}
        if "result" in receipts:
            return dict(receipts["result"])
        claim_receipt = await reconcile_claim_comment(claim_store, claim_receipt, service)
        if "plan" in receipts:
            saved_plan = finalization.FinalizationPlan(**receipts["plan"])
    # Step 0: read current issue state before planning any label mutation.
    # Caller-supplied labels win when present; otherwise the pre-mutation
    # read supplies them. A delayed finalizer that observes a successor
    # (closed issue, operator hold, or a newer settled state) abandons new
    # mutations before any GitHub write. An unreadable issue never proves a
    # successor: the post-mutation read-back remains the release gate.
    prefetched = {} if claim_receipt is not None and claim_receipt.released else await _fetch_issue(service=service, repository=repository, issue_number=issue_number)
    observed_labels: list[str] | None = None
    if prefetched.get("ok"):
        read_state, observed_labels = _issue_label_names(prefetched.get("issue"))
        try:
            observed = lifecycle.interpret_issue(
                {"state": read_state, "labels": observed_labels}
            )
            abandon, abandon_reason = lifecycle.should_abandon_retry(
                intended_from_settled=from_settled, observed=observed
            )
        except Exception:  # noqa: BLE001 - uninterpretable reads never authorize abandonment
            abandon, abandon_reason = False, ""
        # A crash after the label writes must resume the recorded comment phase.
        # Only the exact destination of our immutable plan can explain a changed
        # settled state; a different successor still revokes mutation authority.
        own_destination = False
        if saved_plan is not None and saved_plan.mutation:
            mutation = saved_plan.mutation
            observed_outcome = lifecycle.classify_mutation_outcome(
                plan=lifecycle.LabelMutationPlan(tuple(mutation.get("labelsToAdd") or []),
                    tuple(mutation.get("labelsToRemove") or []), bool(mutation.get("closeIssue"))),
                read_back={"state": read_state, "labels": observed_labels},
            )
            own_destination = observed_outcome.outcome in {lifecycle.OUTCOME_APPLIED, lifecycle.OUTCOME_ALREADY_APPLIED}
        if abandon and not own_destination:
            return {
                "released": False,
                "reasonCode": "successor_observed",
                "summary": f"Pre-mutation read observes a successor; no new mutations attempted: {abandon_reason}.",
                "disposition": "",
                "transition": None,
                "mutation": None,
                "completionRoute": completion["route"],
                "mergeAuthorized": False,
                "workspaceRetained": True,
                "pendingSync": None,
                "commentId": None,
                "mutationOutcome": None,
            }
    effective_labels: Sequence[Any] | None = current_labels
    if effective_labels is None and observed_labels is not None:
        effective_labels = observed_labels
    plan = saved_plan or finalization.plan_failed_attempt_finalization(
        repository=repository,
        issue_number=issue_number,
        from_settled=from_settled,
        execution_event=execution_event,
        writer_evidence=writer_evidence,
        mutation_evidence=mutation_evidence,
        preservation_evidence=preservation_evidence,
        disposition_evidence=disposition_evidence,
        current_labels=effective_labels,
        reason=reason,
        attempt_id=attempt_id,
        primary_outcome=primary_outcome,
        met_requirements=met_requirements,
        remaining_requirements=remaining_requirements,
        retry_history=retry_history,
        next_action=next_action,
        cancellation_hold=cancellation_hold,
        review_owner_ended=review_owner_ended,
    )
    base: dict[str, Any] = {
        "released": False,
        "reasonCode": plan.reason_code,
        "summary": plan.summary,
        "disposition": plan.disposition,
        "transition": plan.transition,
        "mutation": plan.mutation,
        "completionRoute": completion["route"],
        "mergeAuthorized": completion["mergeAuthorized"],
        "workspaceRetained": True,
        "pendingSync": plan.pending_sync,
        "commentId": None,
        "mutationOutcome": None,
    }
    if not plan.releasable or plan.mutation is None or plan.transition is None:
        return base
    if claim_receipt is not None:
        await claim_store.record_finalization(claim_receipt.owner, "plan", asdict(plan))
        if claim_receipt.released:
            base.update(released=True, reasonCode="released", summary=plan.summary,
                        workspaceRetained=bool(plan.workspace_retained), pendingSync=None,
                        commentId=claim_receipt.comment_id)
            await claim_store.record_finalization(claim_receipt.owner, "result", base)
            return base
    # Step 1: scan the proposed terminal comment through the repository
    # outbound scanner before any GitHub write; secret-like handoff text
    # blocks posting instead of being published.
    from moonmind.utils.logging import redact_sensitive_text

    blocked, block_detail = _scan_terminal_body(plan.terminal_comment)
    if blocked:
        base["reasonCode"] = "comment_blocked_by_scan"
        base["summary"] = f"Terminal comment blocked by outbound scan: {block_detail}."
        return base
    proposed_body = redact_sensitive_text(plan.terminal_comment)
    # Prefer the canonical attempt comment when it already exists: appending
    # keeps one marker-bearing comment per attempt (with its machine-readable
    # metadata block intact) instead of multiplying unmarked copies on retry.
    comment_body = proposed_body
    comment_id = None
    if claim_receipt is not None:
        parsed = parse_attempt_comment(claim_receipt.comment_body)
        if parsed.handoff is None:
            raise ValueError("claim_evidence_conflict: canonical handoff is unreadable")
        releasing = replace(parsed.handoff, activity="releasing", writers_stopped=True,
                            pending_disposition=plan.disposition, outcome="failed")
        comment_body = render_attempt_comment(releasing) + "\n\n" + proposed_body
        # Do not overwrite a pending released-comment update on restart. Its
        # remote effect is reconciled after rechecking the exact label outcome.
        if not claim_receipt.pending_comment_body:
            comment_id = await publish_claim_comment(claim_store, claim_receipt, service, comment_body)
        else:
            comment_id = claim_receipt.comment_id
    elif _string(attempt_id):
        reuse = await _reuse_attempt_comment(
            service=service,
            repository=repository,
            issue_number=issue_number,
            attempt_id=_string(attempt_id),
            terminal_section=proposed_body,
        )
        if reuse["action"] == "updated":
            comment_id = reuse["commentId"]
            comment_body = str(reuse["body"])
        elif reuse["action"] in {"conflict", "pending"}:
            base["reasonCode"] = (
                "conflicting_attempt_copies" if reuse["action"] == "conflict" else "attempt_comment_pending"
            )
            base["summary"] = str(reuse.get("reason") or "Canonical attempt comment is not safely updatable.")
            base["pendingSync"] = finalization.record_pending_sync(reason=str(reuse.get("reason") or "attempt comment"))
            return base
    if comment_id is None:
        try:
            created = await service.create_issue_comment(
                repo=repository, issue_number=issue_number, body=proposed_body
            )
        except Exception as exc:  # noqa: BLE001 - adapter failure stays pending
            base["reasonCode"] = "comment_unknown"
            base["summary"] = f"Proposed terminal comment result unknown: {exc.__class__.__name__}."
            base["pendingSync"] = finalization.record_pending_sync(reason="proposed comment unknown")
            return base
        if not created.get("ok"):
            code = str(created.get("reasonCode") or "comment_failed")
            if code == "outcome_unknown":
                base["reasonCode"] = "comment_unknown"
                base["summary"] = "Proposed terminal comment result unknown; reconcile before repeating effects."
                base["pendingSync"] = finalization.record_pending_sync(reason="proposed comment unknown")
            else:
                base["reasonCode"] = code
                base["summary"] = str(created.get("summary") or "Proposed terminal comment failed.")
            return base
        comment_id = created.get("commentId")
    base["commentId"] = comment_id
    # Step 2: destination labels add-before-remove through the #4176 plan.
    mutation_plan = plan.mutation
    labels_to_add = list(mutation_plan.get("labelsToAdd") or [])
    labels_to_remove = list(mutation_plan.get("labelsToRemove") or [])
    close_issue = bool(mutation_plan.get("closeIssue"))
    for label in labels_to_add:
        try:
            added = await service.add_issue_labels(repo=repository, issue_number=issue_number, labels=[label])
        except Exception as exc:  # noqa: BLE001
            base["reasonCode"] = "label_unknown"
            base["summary"] = f"Label add result unknown: {exc.__class__.__name__}."
            base["pendingSync"] = finalization.record_pending_sync(reason="label add unknown")
            return base
        if not added.get("ok"):
            code = str(added.get("reasonCode") or "add_failed")
            if code == "outcome_unknown":
                base["reasonCode"] = "label_unknown"
                base["summary"] = "Label add result unknown; pending state remains distinguishable and repairable."
                base["pendingSync"] = finalization.record_pending_sync(reason="label add unknown")
            elif code == "denied":
                outcome = lifecycle.classify_mutation_outcome(
                    plan=lifecycle.LabelMutationPlan(tuple(labels_to_add), tuple(labels_to_remove), close_issue),
                    read_back=None,
                    denied=True,
                    denied_detail=str(added.get("summary") or ""),
                )
                base["reasonCode"] = "mutation_denied"
                base["mutationOutcome"] = outcome.to_dict()
                base["summary"] = "Destination label denied; proposed comment preserved for reconciliation."
                base["pendingSync"] = finalization.record_pending_sync(reason="label add denied")
            else:
                base["reasonCode"] = code
                base["summary"] = str(added.get("summary") or "Label add failed.")
                base["pendingSync"] = finalization.record_pending_sync(reason="label add failed")
            return base
    for label in labels_to_remove:
        try:
            removed = await service.remove_issue_label(repo=repository, issue_number=issue_number, label=label)
        except Exception as exc:  # noqa: BLE001
            base["reasonCode"] = "label_unknown"
            base["summary"] = f"Label remove result unknown: {exc.__class__.__name__}."
            base["pendingSync"] = finalization.record_pending_sync(reason="label remove unknown")
            return base
        if not removed.get("ok"):
            code = str(removed.get("reasonCode") or "remove_failed")
            if code == "outcome_unknown":
                base["reasonCode"] = "label_unknown"
                base["summary"] = "Label remove result unknown; pending state remains distinguishable and repairable."
                base["pendingSync"] = finalization.record_pending_sync(reason="label remove unknown")
                return base
            base["reasonCode"] = code
            base["summary"] = str(removed.get("summary") or "Label remove failed.")
            base["pendingSync"] = finalization.record_pending_sync(reason="label remove failed")
            return base
    # Step 2b: close the issue when the destination is a closed terminal.
    # Mirrors the success-path update_github_issue_status close step: without
    # this, a to_closed mutation plan could never observe closed on read-back
    # and failure-after-merge could never release.
    if close_issue:
        try:
            closed = await service.close_issue(repo=repository, issue_number=issue_number)
        except Exception as exc:  # noqa: BLE001
            base["reasonCode"] = "close_unknown"
            base["summary"] = f"Issue close result unknown: {exc.__class__.__name__}."
            base["pendingSync"] = finalization.record_pending_sync(reason="issue close unknown")
            return base
        if not closed.get("ok"):
            code = str(closed.get("reasonCode") or "close_failed")
            if code == "outcome_unknown":
                base["reasonCode"] = "close_unknown"
                base["summary"] = "Issue close result unknown; pending state remains distinguishable and repairable."
                base["pendingSync"] = finalization.record_pending_sync(reason="issue close unknown")
            else:
                base["reasonCode"] = code
                base["summary"] = str(closed.get("summary") or "Issue close failed.")
                base["pendingSync"] = finalization.record_pending_sync(reason="issue close failed")
            return base
    # Step 3: read back and classify before claiming release.
    read = await _fetch_issue(service=service, repository=repository, issue_number=issue_number)
    if not read.get("ok"):
        base["reasonCode"] = "read_back_unknown"
        base["summary"] = "Label mutations applied but read-back is unknown; release not claimed."
        base["pendingSync"] = finalization.record_pending_sync(reason="read-back unknown")
        return base
    issue_payload = read.get("issue") or {}
    read_state, names = _issue_label_names(issue_payload)
    read_back = {"state": read_state, "labels": names}
    outcome = lifecycle.classify_mutation_outcome(
        plan=lifecycle.LabelMutationPlan(tuple(labels_to_add), tuple(labels_to_remove), close_issue),
        read_back=read_back,
    )
    base["mutationOutcome"] = outcome.to_dict()
    if outcome.outcome not in {lifecycle.OUTCOME_APPLIED, lifecycle.OUTCOME_ALREADY_APPLIED}:
        base["reasonCode"] = "mutation_incomplete"
        base["summary"] = f"Destination not observed on read-back: {outcome.detail} Pending state remains repairable."
        return base
    # Step 4: finalize the terminal comment as released. Every unsuccessful
    # update stays pending and unreleased: a denied or failed update must
    # never authorize cleanup while the attempt comment may still report
    # only the proposed disposition.
    released_body = comment_body.rstrip() + "\n\nReleased: label outcome observed on read-back.\n"
    if claim_receipt is not None:
        released_body = render_attempt_comment(replace(releasing, activity="released")) + "\n\n" + proposed_body
        claim_receipt = await claim_store.get(claim_receipt.owner)
        # Reconcile an interrupted proposed-comment write before advancing it.
        if claim_receipt.pending_comment_body and claim_receipt.pending_comment_body != released_body:
            await publish_claim_comment(claim_store, claim_receipt, service, claim_receipt.pending_comment_body)
            claim_receipt = await claim_store.get(claim_receipt.owner)
        await publish_claim_comment(claim_store, claim_receipt, service, released_body)
    elif comment_id is not None:
        try:
            updated = await service.update_issue_comment(repo=repository, comment_id=int(comment_id), body=released_body)
        except Exception as exc:  # noqa: BLE001 - update result unknown; never false release
            base["released"] = False
            base["reasonCode"] = "released_comment_unknown"
            base["summary"] = (
                f"Label outcome observed; released-comment update result unknown: {exc.__class__.__name__}. "
                "Pending reconciliation, not released."
            )
            base["pendingSync"] = finalization.record_pending_sync(reason="released comment unknown")
            base["workspaceRetained"] = True
            return base
        if not updated.get("ok"):
            code = str(updated.get("reasonCode") or "update_failed")
            base["released"] = False
            base["reasonCode"] = "released_comment_failed"
            base["summary"] = (
                f"Label outcome observed; released-comment update {code}. "
                "Pending reconciliation, not released."
            )
            base["pendingSync"] = finalization.record_pending_sync(reason="released comment failed")
            base["workspaceRetained"] = True
            return base
    base["released"] = True
    base["reasonCode"] = "released"
    base["summary"] = f"Failed-attempt finalization released to {plan.disposition}; label outcome observed and terminal comment published."
    base["workspaceRetained"] = bool(plan.workspace_retained)
    base["pendingSync"] = None
    if claim_receipt is not None:
        await claim_store.record_finalization(claim_receipt.owner, "result", base)
    return base


__all__ = ["finalize_failed_attempt"]
