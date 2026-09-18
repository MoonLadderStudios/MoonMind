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
        shared_started = set()
        def _scheduled_id(attrs, *names):
            for name in names:
                value = getattr(attrs, name, None)
                if value:
                    return value
            return None

        def _terminal_scheduled_id(event, *field_names):
            for field_name in field_names:
                try:
                    if not event.HasField(field_name):
                        continue
                except Exception:
                    continue
                attrs = getattr(event, field_name, None)
                if attrs is None:
                    continue
                scheduled_id = _scheduled_id(
                    attrs, "scheduled_event_id", "scheduled_eventId"
                )
                if scheduled_id:
                    return scheduled_id
            return None

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
            started_id = _terminal_scheduled_id(
                event, "activity_task_started_event_attributes"
            )
            if started_id:
                shared_started.add(started_id)
            for terminal_field in (
                "activity_task_timed_out_event_attributes",
                "activity_task_cancelled_event_attributes",
                "activity_task_canceled_event_attributes",
            ):
                terminal_id = _terminal_scheduled_id(event, terminal_field)
                if terminal_id and terminal_id not in shared_started:
                    # Scheduled but never started: a schedule-to-start timeout
                    # (for example renew_claim waiting for an unavailable
                    # worker) proves no activity code ran, so it cannot hold
                    # shared-mutation uncertainty. Activities that started
                    # before timing out or canceling may still have executed.
                    shared_effects.discard(terminal_id)
        if set(initiated) - settled_starts:
            raise ValueError("child_start_unsettled")
        if shared_effects:
            raise ValueError("shared_mutation_outcome_unknown")
    return agents


#: MoonMind's own low-cardinality harness codes that mean the deployment could
#: not give the attempt a runtime at all (``harness_platform.failures``). These
#: say nothing about the issue: they apply identically to every candidate, so a
#: single launcher outage must not be charged to the whole backlog. Failures
#: that describe the work itself (bad profile, unavailable model, failed
#: verification) are deliberately absent and still cost an attempt.
RUNTIME_PROVISIONING_FAILURES: tuple[str, ...] = (
    "OMNIGENT_HOST_LAUNCH_FAILED",
    "OMNIGENT_HOST_CAPACITY_UNAVAILABLE",
    "OMNIGENT_HOST_REGISTRATION_TIMEOUT",
    "OMNIGENT_HOST_HARNESS_NOT_READY",
    "OMNIGENT_HOST_CLASS_UNAVAILABLE",
    "OMNIGENT_GENERIC_REALIZER_NOT_READY",
    "OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE",
)


def runtime_provisioning_failure(detail: Any) -> bool:
    """Return True when *detail* carries a typed host-provisioning failure.

    These are MoonMind's own enumerated codes, not vendor log text: the
    taxonomy exists precisely so decisions key on a bounded code instead of
    provider prose. A test asserts this tuple stays a subset of the enum.
    """
    text = str(detail or "")
    return any(code in text for code in RUNTIME_PROVISIONING_FAILURES)


async def _runtime_provisioning_failed(client, receipt) -> bool:
    """Return True when the controlling run failed for want of a runtime.

    Best-effort and read-only: an unreadable or differently-shaped failure
    keeps the ordinary accounting, so an attempt is never excused from the
    allowance on a guess.
    """
    namespace, workflow_id = receipt.owner.split("/", 1)
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.result()
    except Exception as exc:  # noqa: BLE001 - the failure itself is the evidence
        detail = str(exc)
        cause = getattr(exc, "cause", None)
        while cause is not None:
            detail = f"{detail} {cause}"
            cause = getattr(cause, "cause", None)
        return runtime_provisioning_failure(detail)
    return False


def recovery_disposition_evidence(*, agent_started: bool, remaining: int, runtime_unavailable: bool = False) -> dict[str, Any]:
    """Build the terminal disposition evidence for one recovered claim.

    A typed host-provisioning failure with no agent child started means the
    deployment could not run this attempt and nothing was learned about the
    issue. That is a runtime fault, not an exhausted issue budget: reporting it
    as exhaustion escalated whole backlogs to needs-attention during a single
    launcher outage.
    """
    if runtime_unavailable and not agent_started:
        return {
            "trustworthyNoWork": True,
            "runtimeUnavailable": True,
            "freshRetryAllowed": True,
            "retryRemaining": max(0, int(remaining)),
        }
    return {
        "trustworthyNoWork": True,
        "freshRetryAllowed": remaining > 0,
        "budgetExhausted": remaining <= 0,
        "retryRemaining": remaining,
    }


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
        # never writes this table. A missing or released slot lease proves no
        # provider capacity needs MoonMind cleanup, but it proves nothing about
        # repository work the agent may have edited or committed before
        # failing. Without a binding row there is no inspected checkpoint, so
        # the claim must stay recoverable until runtime-specific workspace or
        # saved-work evidence is verified.
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
        raise ValueError("saved_work_requires_recovery")
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
                # Only asked when no agent child started: the narrow case where
                # the run may have died before any runtime existed.
                runtime_unavailable = not agents and await _runtime_provisioning_failed(
                    client, receipt
                )
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
                            disposition_evidence=recovery_disposition_evidence(
                                agent_started=bool(agents),
                                remaining=remaining,
                                runtime_unavailable=runtime_unavailable,
                            ),
                            reason="Automatic recovery: controlling run stopped without new repository work; saved checkpoints: "
                            + (", ".join(checkpoints) or "no agent started"),
                            next_action="fresh_retry"
                            if remaining or runtime_unavailable
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
                    # Ownership can end while the terminal bookkeeping is still
                    # pending; the sweep must report both facts, not one.
                    "ownershipEnded",
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
        "ownershipEnded": sum(bool(item.get("ownershipEnded")) for item in results),
        "results": results,
    }
