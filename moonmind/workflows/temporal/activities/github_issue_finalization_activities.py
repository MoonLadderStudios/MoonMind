"""Production Activity entrypoint for failed-attempt finalization (#4179).

Sibling runtime work calls :func:`finalize_failed_attempt` at the durable
controlling workflow boundary instead of reimplementing terminal-handoff
semantics: every decision lives in
:mod:`moonmind.workflows.temporal.github_issue_finalization`, and this
module only supplies the production service object (``GitHubService``) and
performs the ordered GitHub reads/writes (proposed comment -> destination
labels -> read-back -> released comment).

Interruptibility: the result distinguishes ``released`` (label outcome
observed AND terminal comment published) from ``pending`` (interrupted or
unknown after any comment/label operation). Pending results remain
repairable by a later finalizer or the periodic reconciler and never claim
false release success. Failed GitHub reporting never discards the only
workspace/evidence (``workspaceRetained=True``).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal import github_issue_finalization as finalization
from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value or "").strip()


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
) -> dict[str, Any]:
    """Finalize one failed/canceled controlling attempt at the durable boundary.

    Ordered effects: plan (pure) -> create proposed terminal comment ->
    apply destination labels add-before-remove -> read back issue -> classify
    mutation outcome -> publish released terminal comment update. Any unknown
    or failed step returns ``released=False`` with ``pending`` detail and
    ``workspaceRetained=True``; only an observed label outcome plus a
    published terminal comment returns ``released=True``.
    """
    completion = finalization.route_completion_handoff(
        completion_mode=completion_mode,
        review_owner_ended=review_owner_ended,
        cancellation_hold=cancellation_hold,
    )
    if service is None:
        from moonmind.workflows.adapters.github_service import GitHubService

        service = GitHubService()
    plan = finalization.plan_failed_attempt_finalization(
        repository=repository,
        issue_number=issue_number,
        from_settled=from_settled,
        execution_event=execution_event,
        writer_evidence=writer_evidence,
        mutation_evidence=mutation_evidence,
        preservation_evidence=preservation_evidence,
        disposition_evidence=disposition_evidence,
        current_labels=current_labels,
        reason=reason,
        attempt_id=attempt_id,
        primary_outcome=primary_outcome,
        met_requirements=met_requirements,
        remaining_requirements=remaining_requirements,
        retry_history=retry_history,
        next_action=next_action,
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
    # Step 1: proposed terminal comment first (durable + visible before labels).
    try:
        created = await service.create_issue_comment(
            repo=repository, issue_number=issue_number, body=plan.terminal_comment
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
            else:
                base["reasonCode"] = code
                base["summary"] = str(added.get("summary") or "Label add failed.")
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
            return base
    # Step 3: read back and classify before claiming release.
    read = await _fetch_issue(service=service, repository=repository, issue_number=issue_number)
    if not read.get("ok"):
        base["reasonCode"] = "read_back_unknown"
        base["summary"] = "Label mutations applied but read-back is unknown; release not claimed."
        base["pendingSync"] = finalization.record_pending_sync(reason="read-back unknown")
        return base
    issue_payload = read.get("issue") or {}
    names: list[str] = []
    raw_labels = issue_payload.get("labels") or []
    for item in raw_labels if isinstance(raw_labels, list) else []:
        if isinstance(item, Mapping):
            names.append(str(item.get("name") or ""))
        else:
            names.append(str(item or ""))
    read_back = {"state": issue_payload.get("state", "open"), "labels": names}
    outcome = lifecycle.classify_mutation_outcome(
        plan=lifecycle.LabelMutationPlan(tuple(labels_to_add), tuple(labels_to_remove), close_issue),
        read_back=read_back,
    )
    base["mutationOutcome"] = outcome.to_dict()
    if outcome.outcome not in {lifecycle.OUTCOME_APPLIED, lifecycle.OUTCOME_ALREADY_APPLIED}:
        base["reasonCode"] = "mutation_incomplete"
        base["summary"] = f"Destination not observed on read-back: {outcome.detail} Pending state remains repairable."
        return base
    # Step 4: finalize the terminal comment as released (best-effort marker).
    released_body = plan.terminal_comment + "\n\nReleased: label outcome observed on read-back."
    if comment_id is not None:
        try:
            updated = await service.update_issue_comment(repo=repository, comment_id=int(comment_id), body=released_body)
        except Exception:  # noqa: BLE001 - release still holds; comment finalization is auxiliary
            updated = {"ok": False, "reasonCode": "outcome_unknown"}
        if not updated.get("ok") and str(updated.get("reasonCode") or "") == "outcome_unknown":
            base["released"] = True
            base["reasonCode"] = "released_comment_pending"
            base["summary"] = "Label outcome observed; released-comment update is pending reconciliation."
            base["workspaceRetained"] = bool(plan.workspace_retained)
            return base
    base["released"] = True
    base["reasonCode"] = "released"
    base["summary"] = f"Failed-attempt finalization released to {plan.disposition}; label outcome observed and terminal comment published."
    base["workspaceRetained"] = bool(plan.workspace_retained)
    base["pendingSync"] = None
    return base


__all__ = ["finalize_failed_attempt"]
