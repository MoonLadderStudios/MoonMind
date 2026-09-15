"""Recover local terminal claims through the existing scheduled reconciler.

Age requests observation; Temporal history and the runtime's saved-work proof
authorize release. Remote-only comments never become locally owned receipts.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from temporalio.client import WorkflowExecutionStatus

from api_service.db.models import (
    GitHubIssueClaim,
    OmnigentRuntimeBindingRecord,
    ProviderProfileSlotLease,
)
from moonmind.provider_profiles.lease_client import DurableLeaseState
from moonmind.workflows.executions.repository_contract import (
    github_repository_name_from_value,
)
from moonmind.workflows.temporal.github_issue_attempts import (
    parse_attempt_comment,
    reconstruct_from_comments,
)
from moonmind.workflows.temporal.issue_claim_store import (
    ClaimReceipt,
    IssueClaimStore,
    inspect_claim_comments,
)

CLEANUP_GRACE = timedelta(minutes=5)
MAX_CLAIMS = 25
MAX_EXECUTIONS = 64
MAX_HISTORY_EVENTS = 25000
MAX_CLAIM_SECONDS = 30
MAX_SWEEP_SECONDS = 120


def _repository_identity(value):
    """Compare canonical repository identities, not authored spellings."""
    slug = github_repository_name_from_value(value)
    return (slug or str(value or "")).casefold()


async def _continue_as_new_chain(client, workflow_id):
    """Terminal-first ``(workflow_id, run_id)`` chain across continue-as-new.

    Resolving ``(workflow_id, None)`` inspects only the latest execution, so
    histories from earlier runs — including their ``MoonMind.AgentRun``
    children and runtime bindings — would never be visited. Follow the exact
    continue-as-new chain before authorizing release.
    """
    handle = client.get_workflow_handle(workflow_id)
    described = await handle.describe()
    terminal_run_id = getattr(described, "run_id", None)
    chain = [(workflow_id, terminal_run_id)]
    seen = {terminal_run_id}
    run_id = terminal_run_id
    while run_id:
        scoped = client.get_workflow_handle(workflow_id, run_id=run_id)
        first_event = None
        events = scoped.fetch_history_events(page_size=1)
        try:
            async for event in events:
                first_event = event
                break
        finally:
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()
        previous = ""
        if first_event is not None and first_event.HasField(
            "workflow_execution_started_event_attributes"
        ):
            previous = (
                first_event.workflow_execution_started_event_attributes.continued_execution_run_id
                or ""
            )
        if not previous or previous in seen:
            break
        seen.add(previous)
        chain.append((workflow_id, previous))
        if len(chain) > MAX_EXECUTIONS:
            raise ValueError("execution_scan_incomplete")
        run_id = previous
    return chain


async def _closed_execution_tree(client, receipt, now):
    namespace, workflow_id = receipt.owner.split("/", 1)
    chain = await _continue_as_new_chain(client, workflow_id)
    chain_ids = set(chain)
    terminal = chain[0]
    pending = list(chain)
    executions = set()
    agents = set()
    total_events = 0
    while pending:
        identity = pending.pop()
        if identity in executions:
            continue
        executions.add(identity)
        if len(executions) > MAX_EXECUTIONS:
            raise ValueError("execution_scan_incomplete")
        handle = client.get_workflow_handle(identity[0], run_id=identity[1])
        described = await handle.describe()
        is_historical = (
            identity in chain_ids and identity != terminal and identity[1] is not None
        )
        if is_historical:
            # Historical continue-as-new link: the terminal liveness holds
            # below do not apply, but the link must itself be closed via
            # continue-as-new for the chain to authorize release. Its history
            # still scans below for prior-run children and shared effects.
            # Grace is inherited: the link closed before the terminal run
            # started, so a terminal that clears the grace window implies the
            # link does too.
            if described.status in {
                None,
                WorkflowExecutionStatus.RUNNING,
            }:
                raise ValueError("owner_or_child_running")
            if described.status != WorkflowExecutionStatus.CONTINUED_AS_NEW:
                raise ValueError("continued_chain_broken")
        elif described.status in {
            None,
            WorkflowExecutionStatus.RUNNING,
            WorkflowExecutionStatus.CONTINUED_AS_NEW,
        }:
            raise ValueError("owner_or_child_running")
        if not is_historical:
            if not described.close_time or now - described.close_time < CLEANUP_GRACE:
                raise ValueError("cleanup_grace")
            if identity == terminal:
                if described.status == WorkflowExecutionStatus.CANCELED:
                    raise ValueError("cancellation_hold")
                if described.status == WorkflowExecutionStatus.COMPLETED:
                    # Success may still own PR review/finalization. Its ordinary
                    # completion owner retains authority; this repairs failed runs.
                    raise ValueError("successful_owner_requires_finalization")
        if described.workflow_type == "MoonMind.AgentRun":
            agents.add((identity[0], described.run_id))
        initiated = {}
        settled_starts = set()
        shared_effects = set()
        async for event in handle.fetch_history_events(page_size=500):
            total_events += 1
            if total_events > MAX_HISTORY_EVENTS:
                raise ValueError("history_scan_incomplete")
            if event.HasField(
                "start_child_workflow_execution_initiated_event_attributes"
            ):
                initiated[event.event_id] = (
                    event.start_child_workflow_execution_initiated_event_attributes
                )
            if event.HasField("child_workflow_execution_started_event_attributes"):
                child = event.child_workflow_execution_started_event_attributes
                start = initiated.get(child.initiated_event_id)
                if start is None or start.namespace not in {"", namespace}:
                    raise ValueError("child_authority_unknown")
                pending.append(
                    (
                        child.workflow_execution.workflow_id,
                        child.workflow_execution.run_id,
                    )
                )
                settled_starts.add(child.initiated_event_id)
            if event.HasField("start_child_workflow_execution_failed_event_attributes"):
                settled_starts.add(
                    event.start_child_workflow_execution_failed_event_attributes.initiated_event_id
                )
            if event.HasField("activity_task_scheduled_event_attributes"):
                name = event.activity_task_scheduled_event_attributes.activity_type.name
                # These are the production shared-mutation boundaries. A
                # failed/unknown call cannot be inferred absent from labels.
                if name == "mm.tool.execute" or name.startswith(
                    ("github_issue.", "merge_automation.")
                ):
                    shared_effects.add(event.event_id)
            if event.HasField("activity_task_completed_event_attributes"):
                shared_effects.discard(
                    event.activity_task_completed_event_attributes.scheduled_event_id
                )
        if set(initiated) - settled_starts:
            raise ValueError("child_start_unsettled")
        if shared_effects:
            raise ValueError("shared_mutation_outcome_unknown")
    return agents


async def _runtime_no_work(store, agents, receipt, service):
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore

    if not agents:
        return []  # Full controlling history proves no agent child started.
    async with store.sessions() as session:
        rows = (
            (
                await session.execute(
                    select(OmnigentRuntimeBindingRecord).where(
                        OmnigentRuntimeBindingRecord.phase_results_json["owner"][
                            "workflowId"
                        ]
                        .as_string()
                        .in_([owner for owner, _ in agents])
                    )
                )
            )
            .scalars()
            .all()
        )
    covered = set()
    checkpoints = []
    for row in rows:
        binding = DbRuntimeBindingStore._from_row(row)
        phases = binding.phaseResults or {}
        owner = phases.get("owner") or {}
        identity = (owner.get("workflowId"), owner.get("runId"))
        if (
            identity not in agents
            or owner.get("namespace") != receipt.owner.split("/", 1)[0]
        ):
            raise ValueError("runtime_owner_mismatch")
        covered.add(identity)
        if row.state != "cleaned":
            raise ValueError("runtime_cleanup_pending")
        if not row.session_id and not phases.get("workspace"):
            continue  # Fenced cleanup completed before a provider/workspace existed.
        saved = phases.get("saved") or {}
        proof = saved.get("recoveryEvidence") or {}
        if (
            proof.get("schemaVersion") != "repository-worktree-state/v1"
            or proof.get("worktreeClean") is not True
            or _repository_identity(proof.get("repository"))
            != _repository_identity(receipt.repository)
            or not saved.get("checkpointRef")
            or not saved.get("archiveDigest")
            or proof.get("checkpointArchiveDigest") != saved.get("archiveDigest")
            or not saved.get("headCommit")
            or proof.get("headSha") != saved.get("headCommit")
            or any(key.startswith("publication_failure:") for key in phases)
        ):
            raise ValueError("saved_work_requires_recovery")
        # A stopped clean checkout can still contain unique commits. Re-read
        # GitHub on every sweep so outage recovery needs no host or agent turn.
        head = proof["headSha"]
        base = proof.get("baseBranch")
        if (
            not re.fullmatch(r"[0-9a-f]{40,64}", head)
            or not isinstance(base, str)
            or not base
        ):
            raise ValueError("saved_work_requires_recovery")
        token, _ = await service.resolve_github_token(repo=receipt.repository)
        if not token:
            raise ValueError("remote_preservation_unavailable")
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.get(
                f"https://api.github.com/repos/{receipt.repository}/compare/{head}...{quote(base, safe='')}",
                params={"per_page": 1},
                headers=service._github_headers(token),
            )
            response.raise_for_status()
            compared = response.json()
        if (
            compared.get("status") not in {"identical", "ahead"}
            or (compared.get("base_commit") or {}).get("sha") != head
            or (compared.get("merge_base_commit") or {}).get("sha") != head
        ):
            raise ValueError("saved_work_requires_recovery")
        checkpoints.append(saved["checkpointRef"])
    uncovered = agents - covered
    if uncovered:
        # No binding row covers these agents: an Omnigent run that failed
        # before binding creation, or a managed/external run whose runtime
        # never writes this table. Fall back to the durable slot-lease
        # ledger — the same authority the ProviderProfileManager uses for
        # crash recovery — instead of stranding the claim forever. A closed
        # run with no unreleased lease owns no provider capacity requiring
        # MoonMind cleanup, and with no binding row there is no saved-work
        # checkpoint to preserve.
        missing = sorted({owner for owner, _ in uncovered})
        async with store.sessions() as session:
            leases = (
                (
                    await session.execute(
                        select(ProviderProfileSlotLease).where(
                            ProviderProfileSlotLease.workflow_id.in_(missing)
                        )
                    )
                )
                .scalars()
                .all()
            )
        for lease in leases:
            if lease.lease_state != DurableLeaseState.RELEASED.value:
                # Held, cleanup-requested, or unreconciled: the slot manager
                # owns the wait and reaps leases of dead owners, so recovery
                # retries on a later sweep instead of releasing now.
                raise ValueError("runtime_cleanup_pending")
        covered |= uncovered
    return checkpoints


async def reconcile_local_claims(
    *,
    state: dict[str, Any],
    repository: str = "",
    store=None,
    service=None,
    client_factory=None,
    now=None,
):
    """Bounded, rotating sweep of receipts, including missing-status-label cases."""
    from moonmind.config.settings import settings
    from moonmind.workflows.adapters.github_service import GitHubService
    from moonmind.workflows.temporal.activities.github_issue_finalization_activities import (
        finalize_failed_attempt,
    )
    from moonmind.workflows.temporal.client import get_temporal_client

    store = store or IssueClaimStore()
    service = service or GitHubService()
    client_factory = client_factory or get_temporal_client
    now = now or datetime.now(UTC)
    query = select(GitHubIssueClaim).where(GitHubIssueClaim.released.is_(False))
    if repository:
        query = query.where(GitHubIssueClaim.repository == repository.casefold())
    cursor_key = repository.casefold() or "*"
    cursor = (state.get("localClaimCursor") or {}).get(cursor_key, "")
    async with store.sessions() as session:
        rows = (
            (
                await session.execute(
                    query.where(GitHubIssueClaim.owner > cursor)
                    .order_by(GitHubIssueClaim.owner)
                    .limit(MAX_CLAIMS)
                )
            )
            .scalars()
            .all()
        )
        receipts = [ClaimReceipt.from_row(row) for row in rows]
    results = []
    started = time.monotonic()
    for receipt in receipts:
        if time.monotonic() - started >= MAX_SWEEP_SECONDS:
            break
        result = {
            "owner": receipt.owner,
            "issueNumber": receipt.issue_number,
            "repository": receipt.repository,
            "released": False,
        }
        try:
            async with asyncio.timeout(MAX_CLAIM_SECONDS):
                client = await client_factory(
                    settings.temporal.address, receipt.owner.split("/", 1)[0]
                )
                unannounced = (
                    not receipt.announcement_started and not receipt.comment_id
                )
                try:
                    agents = await _closed_execution_tree(client, receipt, now)
                except ValueError as exc:
                    if (
                        not unannounced
                        or str(exc).split(":", 1)[0]
                        != "shared_mutation_outcome_unknown"
                    ):
                        raise
                    # Proven-unannounced: the receipt proves no claim write
                    # was authorized — announcement intent is committed before
                    # any POST, and abandon_unannounced re-verifies the row
                    # under lock. A tool failure before announcement must not
                    # retain the reservation forever.
                    agents = []
                checkpoints = await _runtime_no_work(store, agents, receipt, service)
                if unannounced:
                    released = await store.abandon_unannounced(
                        receipt.owner, receipt.attempt_id
                    )
                    result.update(
                        released=released,
                        reasonCode="unannounced_reservation_released"
                        if released
                        else "announcement_changed",
                    )
                else:
                    handoff = parse_attempt_comment(receipt.comment_body).handoff
                    if (
                        handoff is None
                        or handoff.operator_hold
                        or handoff.pr_url
                        or handoff.saved_branch
                    ):
                        raise ValueError("existing_handoff_requires_recovery")
                    listed = await service.list_issue_comments(
                        repo=receipt.repository, issue_number=receipt.issue_number
                    )
                    if not listed.get("ok") or not isinstance(
                        listed.get("comments"), list
                    ):
                        raise ValueError("retry_lineage_unavailable")
                    comments = listed["comments"]
                    inspect_claim_comments(receipt, comments)
                    lineage = reconstruct_from_comments(
                        comments,
                        expected_repository=receipt.repository,
                        expected_issue_number=receipt.issue_number,
                        trusted_posters=[
                            (comment.get("user") or {}).get("login", "")
                            for comment in comments
                            if str((comment.get("user") or {}).get("id"))
                            == receipt.actor_id
                        ],
                        max_attempts=handoff.retry_allowance,
                    )
                    if lineage.reason_code not in {"allowed", "budget_exhausted"}:
                        raise ValueError("retry_lineage_requires_recovery")
                    remaining = min(handoff.retry_remaining, lineage.retry_remaining)
                    result.update(
                        await finalize_failed_attempt(
                            repository=receipt.repository,
                            issue_number=receipt.issue_number,
                            execution_event="failed",
                            from_settled="in_progress",
                            writer_evidence={
                                "writersStopped": True,
                                "stopMethod": "runtime_quiescence",
                                "stopEvidence": f"Temporal owner and descendants closed; runtime cleanup verified: {receipt.owner}",
                            },
                            mutation_evidence={
                                "pushOutcome": "verified_absent",
                                "prOutcome": "verified_absent",
                                "mergeOutcome": "verified_absent",
                            },
                            preservation_evidence={
                                "saveMethod": "explicit_no_work",
                                "trustworthyNoWork": True,
                            },
                            disposition_evidence={
                                "trustworthyNoWork": True,
                                "freshRetryAllowed": remaining > 0,
                                "budgetExhausted": remaining <= 0,
                                "retryRemaining": remaining,
                            },
                            reason="Automatic recovery: controlling run stopped without new repository work; saved checkpoints: "
                            + (", ".join(checkpoints) or "no agent started"),
                            next_action="fresh_retry"
                            if remaining
                            else "obtain_attention",
                            service=service,
                            claim_store=store,
                            claim_receipt=receipt,
                        )
                    )
        except Exception as exc:  # noqa: BLE001 -- one claim must not starve the sweep
            # Exact owner/issue and typed disposition survive serialization;
            # credential/transport messages never enter the result or GitHub.
            code = str(exc).split(":", 1)[0]
            result["reasonCode"] = (
                code
                if isinstance(exc, ValueError)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,80}", code)
                else type(exc).__name__
            )
        results.append(
            {
                key: result[key]
                for key in (
                    "owner",
                    "issueNumber",
                    "repository",
                    "released",
                    "reasonCode",
                )
                if key in result
            }
        )
    state.setdefault("localClaimCursor", {})[cursor_key] = (
        results[-1]["owner"]
        if results and (len(receipts) == MAX_CLAIMS or len(results) < len(receipts))
        else ""
    )
    return {
        "repositories": sorted({receipt.repository for receipt in receipts}),
        "examined": len(results),
        "released": sum(bool(item["released"]) for item in results),
        "results": results,
    }
