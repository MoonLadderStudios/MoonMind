"""Release qualification for long-lived provider-profile manager singletons.

Source: MoonLadderStudios/MoonMind#4363.

``provider-profile-manager:<runtime>`` workflows are singletons whose history
outlives any one release. A release whose worker cannot replay a manager's
recorded history wedges the singleton in a workflow-task failure loop
(``WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR``): slot signals queue
forever while ``update-moonmind`` reports success, and every waiter parks in
``AWAITING SLOT`` / ``awaiting_provider_capacity`` despite free DB capacity.

This module owns the definition of that wedged signature:

* the manager is not stuck in a repeated workflow-task failure loop,
  especially a nondeterminism loop;
* its ``get_state`` query succeeds while it reports running (running but
  unqueryable is the exact wedged signature from the incident);
* the DB held-lease count reconciles with the in-memory execution grants the
  manager reports — when both sides were observed.

:func:`scan_workflow_task_failure_tail` is that definition's one
implementation. :mod:`moonmind.provider_profiles.manager_recovery` reads it to
decide whether a blocked caller may replace the run, so automatic recovery and
a release-time liveness disposition can never disagree about whether a
singleton is wedged.

Evaluation itself (:func:`evaluate_provider_manager_liveness`) is pure and
dependency-free so it is unit-testable without Temporal or Postgres.
Observation collection (:func:`collect_provider_manager_liveness`) is the thin
async boundary that describes managers, queries ``get_state``, scans the
recent tail for failed workflow tasks, and optionally joins DB held counts.
A blocking disposition carries precise evidence plus the recovery owner and
runbook pointer; it never silently passes a wedged singleton.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


# A manager that cannot be queried while running is already actionable: the
# incident's wedged singleton kept ``running=true`` with every ``get_state``
# query failing. Two attempts keep one transient query timeout from blocking
# a release.
MANAGER_QUERY_ATTEMPTS = 2

# Consecutive tail workflow-task failures attributed to nondeterminism that
# block promotion. One failure can be a deploy flap; a repeated loop is the
# wedge signature.
NONDETERMINISM_FAILURE_THRESHOLD = 3

# How many recent history events the collector scans for failed workflow
# tasks. Singleton histories roll over via Continue-As-New, so the tail is
# bounded and recent failures are the ones that matter.
HISTORY_TAIL_SCAN_LIMIT = 200

# Recovery ownership published on every blocking disposition. A wedge is
# replaced automatically by manager_recovery when the durable ledger shows no
# held lease; the runbook covers the refused case, where capacity may be
# genuinely spent and an operator decides.
RECOVERY_OWNER = "provider-profile-manager-recovery"
RECOVERY_RUNBOOK = (
    "docs/Security/ProviderProfiles.md#119-wedged-singleton-recovery-4363"
)

# Visibility query that enumerates the manager singletons without touching
# any other workflow family.
MANAGER_VISIBILITY_QUERY = (
    "WorkflowId STARTS_WITH 'provider-profile-manager:'"
)


@dataclass
class ManagerLivenessObservation:
    """One observed ``provider-profile-manager:<runtime>`` singleton."""

    workflow_id: str
    runtime_id: str = ""
    run_id: str = ""
    running: bool = False
    status: str = "UNKNOWN"
    inspection_succeeded: bool = False
    inspection_status: str = ""
    error: str = ""
    workflow_task_failures: int = 0
    nondeterminism_failures: int = 0
    # DB held-lease count for this runtime, when the ledger was readable.
    db_held_leases: int | None = None
    # In-memory execution grants reported by get_state, when queryable.
    memory_execution_grants: int | None = None
    fencing_generation: int | None = None
    observed_from_history: bool = False


def _bounded(text: Any, limit: int = 500) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def _db_count_for_runtime(
    db_held_leases: Mapping[str, int] | None, runtime_id: str
) -> int | None:
    """Resolve the DB active-lease count for one runtime.

    When the ledger was readable (mapping is not None) a runtime with no
    matching rows is a confirmed count of zero — the grouped query returns no
    entry for that runtime. When the ledger was unreadable (None), preserve
    None so the evaluator records unavailable evidence instead of zero.
    """

    if db_held_leases is None:
        return None
    return int(db_held_leases.get(runtime_id, 0))


def _sum_execution_grants(state: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Total execution leases and fencing high-water mark from get_state.

    Returns ``(None, None)`` when the payload has no profile map, so a
    missing section reads as unobserved rather than zero.
    """

    profiles = state.get("profiles")
    if not isinstance(profiles, Mapping):
        return None, None
    total = 0
    fencing: int | None = None
    for profile in profiles.values():
        if not isinstance(profile, Mapping):
            continue
        leases = profile.get("current_leases")
        if isinstance(leases, list):
            total += len(leases)
        metadata = profile.get("lease_metadata")
        if isinstance(metadata, Mapping):
            for entry in metadata.values():
                if not isinstance(entry, Mapping):
                    continue
                try:
                    generation = int(entry.get("fencingGeneration") or 0)
                except (TypeError, ValueError):
                    continue
                if fencing is None or generation > fencing:
                    fencing = generation
    return total, fencing


def observation_from_manager_state(
    *,
    workflow_id: str,
    runtime_id: str,
    run_id: str,
    running: bool,
    status: str,
    inspection: Mapping[str, Any] | None,
    workflow_task_failures: int = 0,
    nondeterminism_failures: int = 0,
    db_held_leases: int | None = None,
    observed_from_history: bool = False,
) -> ManagerLivenessObservation:
    """Build one observation from a describe + get_state inspection pair."""

    inspection = inspection or {}
    succeeded = inspection.get("inspection_succeeded") is True
    grants: int | None = None
    fencing: int | None = None
    if succeeded:
        grants, fencing = _sum_execution_grants(inspection)
    inspection_status = str(
        inspection.get("inspection_status") or inspection.get("status") or ""
    )
    return ManagerLivenessObservation(
        workflow_id=workflow_id,
        runtime_id=runtime_id,
        run_id=run_id,
        running=running,
        status=status,
        inspection_succeeded=succeeded,
        inspection_status=inspection_status,
        error=_bounded(inspection.get("error") or ""),
        workflow_task_failures=int(workflow_task_failures or 0),
        nondeterminism_failures=int(nondeterminism_failures or 0),
        db_held_leases=db_held_leases,
        memory_execution_grants=grants,
        fencing_generation=fencing,
        observed_from_history=bool(observed_from_history),
    )


def evaluate_provider_manager_liveness(
    observations: list[ManagerLivenessObservation],
    *,
    nondeterminism_threshold: int = NONDETERMINISM_FAILURE_THRESHOLD,
) -> dict[str, Any]:
    """Decide whether promotion may proceed past the manager singletons.

    Fails closed: a running-but-unqueryable manager, a repeated
    nondeterminism failure loop, or a DB-vs-memory lease disagreement blocks
    with precise evidence and the recovery owner. A manager that is not
    running passes with a note — ``ensure_manager`` starts it on demand, so
    absence is not a wedge. An empty observation set (no singletons
    visible) also passes with a note rather than inventing a failure.
    """

    evidence: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []
    for observation in observations:
        entry = asdict(observation)
        if observation.status in {"DESCRIBE_FAILED", "VISIBILITY_MISSING"}:
            blocked_reasons.append(
                f"{observation.workflow_id}: manager inventory is uncertain "
                f"({observation.inspection_status or observation.status}); "
                "Visibility is eventually consistent and must not authorize promotion"
            )
            entry["finding"] = "observation_failed_closed"
            evidence.append(entry)
            continue
        if not observation.running:
            entry["finding"] = "not_running_starts_on_demand"
            evidence.append(entry)
            continue
        if not observation.inspection_succeeded:
            blocked_reasons.append(
                f"{observation.workflow_id}: running but get_state query failed "
                f"({observation.inspection_status or 'unknown'})"
            )
            entry["finding"] = "running_unqueryable"
            evidence.append(entry)
            continue
        if observation.nondeterminism_failures >= nondeterminism_threshold:
            blocked_reasons.append(
                f"{observation.workflow_id}: "
                f"{observation.nondeterminism_failures} recent nondeterminism "
                "workflow-task failures"
            )
            entry["finding"] = "nondeterminism_loop"
            evidence.append(entry)
            continue
        if (
            observation.inspection_succeeded
            and observation.db_held_leases is not None
            and observation.memory_execution_grants is not None
            and int(observation.db_held_leases)
            != int(observation.memory_execution_grants)
        ):
            blocked_reasons.append(
                f"{observation.workflow_id}: DB held leases "
                f"({observation.db_held_leases}) disagree with in-memory "
                f"execution grants ({observation.memory_execution_grants})"
            )
            entry["finding"] = "ledger_disagreement"
            evidence.append(entry)
            continue
        entry["finding"] = "healthy"
        evidence.append(entry)

    if not observations:
        evidence.append({"finding": "no_manager_singletons_visible"})

    blocked = bool(blocked_reasons)
    reason_code = (
        "provider_manager_liveness_blocked"
        if blocked
        else "provider_manager_liveness_ok"
    )
    return {
        "blocked": blocked,
        "reasonCode": reason_code,
        "reasons": blocked_reasons,
        "evidence": evidence,
        "recoveryOwner": RECOVERY_OWNER if blocked else None,
        "recoveryRunbook": RECOVERY_RUNBOOK if blocked else None,
        "recoveryHint": (
            "A provider-profile manager singleton shows the wedged-singleton "
            f"signature from MoonMind#4363. {RECOVERY_OWNER} replaces the run "
            "automatically when the durable ledger reports no held lease for "
            f"the runtime; when it reports one, follow {RECOVERY_RUNBOOK} "
            "(reconcile the held row, then retry). Do not promote past an "
            "unqueryable singleton."
            if blocked
            else None
        ),
    }


def _is_nondeterminism_cause(event: Any) -> bool:
    """Whether a WorkflowTaskFailed history event names nondeterminism."""

    try:
        attrs = event.workflow_task_failed_event_attributes
    except AttributeError:
        return False
    cause = getattr(attrs, "cause", None)
    if cause is None:
        return False
    try:
        from temporalio.api.enums.v1 import WorkflowTaskFailedCause

        return int(cause) == int(
            WorkflowTaskFailedCause.WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR
        )
    except Exception:
        # No SDK enum available (or a string test double): fall back to the
        # cause name instead of misreading an unknown value as healthy.
        return "NON_DETERMINISTIC" in str(getattr(cause, "name", cause)).upper()


def _is_workflow_task_failed(event: Any) -> bool:
    try:
        return bool(event.HasField("workflow_task_failed_event_attributes"))
    except Exception:
        return False


def _is_workflow_task_completed(event: Any) -> bool:
    """Whether a history event is a successful workflow task."""

    try:
        return bool(event.HasField("workflow_task_completed_event_attributes"))
    except Exception:
        return False


def _workflow_task_failure_message(event: Any) -> str:
    """The recorded failure message of one WorkflowTaskFailed event."""

    try:
        return _bounded(event.workflow_task_failed_event_attributes.failure.message)
    except Exception:
        return ""


@dataclass(frozen=True)
class WorkflowTaskFailureTail:
    """The unbroken run of failed workflow tasks at a history's tail.

    A successful workflow task, or a failure from another cause, ends the run:
    only an unbroken nondeterminism streak is the wedge signature. Recovery and
    the release liveness gate share this one definition so they can never
    disagree about whether a singleton is wedged.
    """

    task_failures: int = 0
    nondeterminism_failures: int = 0
    last_failure_message: str = ""


def scan_workflow_task_failure_tail(events: Iterable[Any]) -> WorkflowTaskFailureTail:
    """Count the unbroken tail of failed workflow tasks, in history order.

    ``events`` is the history tail in forward order. Events that always sit
    between two workflow tasks are skipped; the scan stops at the first
    successful task or non-nondeterminism failure.
    """

    task_failures = 0
    nondeterminism_failures = 0
    last_failure_message = ""
    for event in reversed(list(events)):
        if _is_workflow_task_failed(event):
            task_failures += 1
            if not _is_nondeterminism_cause(event):
                break
            nondeterminism_failures += 1
            if not last_failure_message:
                last_failure_message = _workflow_task_failure_message(event)
        elif _is_workflow_task_completed(event):
            break
        else:
            continue
    return WorkflowTaskFailureTail(
        task_failures=task_failures,
        nondeterminism_failures=nondeterminism_failures,
        last_failure_message=last_failure_message,
    )


async def collect_provider_manager_liveness(
    client: Any,
    *,
    runtimes: list[str] | None = None,
    db_held_leases: Mapping[str, int] | None = None,
    query_timeout_seconds: float = 10,
    history_tail_limit: int = HISTORY_TAIL_SCAN_LIMIT,
) -> list[ManagerLivenessObservation]:
    """Observe every ``provider-profile-manager:*`` singleton via Temporal.

    For each visible manager: describe it, query ``get_state`` (retried once
    so a transient timeout cannot block a release), and scan the recent
    history tail for failed workflow tasks with a nondeterminism cause.
    ``db_held_leases`` optionally maps runtime_id to the DB held-lease count
    so the evaluator can reconcile the ledger against in-memory grants.
    Observation errors fail closed into an unqueryable observation rather
    than vanishing — a manager that cannot be observed cannot be promoted
    past.
    """

    import asyncio

    observations: list[ManagerLivenessObservation] = []
    wanted = {
        str(runtime or "").strip()
        for runtime in (runtimes or [])
        if str(runtime or "").strip()
    }

    try:
        executions = [
            execution
            async for execution in client.list_workflows(query=MANAGER_VISIBILITY_QUERY)
        ]
    except Exception as exc:
        raise RuntimeError(
            "provider manager liveness: cannot list provider-profile-manager "
            f"singletons: {exc}"
        ) from exc

    for execution in executions:
        workflow_id = str(getattr(execution, "id", "") or "")
        runtime_id = workflow_id.split(":", 1)[1] if ":" in workflow_id else ""
        if wanted and runtime_id not in wanted:
            continue
        run_id = str(getattr(execution, "run_id", "") or "")
        handle = client.get_workflow_handle(workflow_id)

        running = False
        status: str
        observed_run_id = run_id
        try:
            description = await handle.describe()
            status = str(getattr(description.status, "name", description.status))
            running = status == "RUNNING"
            observed_run_id = str(getattr(description, "run_id", "") or run_id)
        except Exception as exc:
            observations.append(
                ManagerLivenessObservation(
                    workflow_id=workflow_id,
                    runtime_id=runtime_id,
                    run_id=observed_run_id,
                    running=False,
                    status="DESCRIBE_FAILED",
                    inspection_succeeded=False,
                    inspection_status="DESCRIBE_FAILED",
                    error=_bounded(exc),
                    db_held_leases=_db_count_for_runtime(db_held_leases, runtime_id),
                )
            )
            continue

        inspection: dict[str, Any] = {"inspection_succeeded": False}
        if running:
            for _ in range(MANAGER_QUERY_ATTEMPTS):
                try:
                    state = await asyncio.wait_for(
                        handle.query("get_state"), timeout=query_timeout_seconds
                    )
                    if isinstance(state, dict):
                        inspection = {"inspection_succeeded": True, **state}
                    else:
                        inspection = {
                            "inspection_succeeded": False,
                            "inspection_status": "INVALID_QUERY_PAYLOAD",
                        }
                    break
                except (asyncio.TimeoutError, TimeoutError):
                    # One transient timeout must not block a release; the
                    # retry above runs once more before this record stands.
                    inspection = {
                        "inspection_succeeded": False,
                        "inspection_status": "QUERY_TIMEOUT",
                        "error": (
                            "get_state query timed out after "
                            f"{query_timeout_seconds}s"
                        ),
                    }
                except Exception as exc:
                    inspection = {
                        "inspection_succeeded": False,
                        "inspection_status": getattr(
                            getattr(exc, "status", None), "name", type(exc).__name__
                        ),
                        "error": _bounded(exc),
                    }
                    break

        task_failures = 0
        nondeterminism_failures = 0
        observed_from_history = False
        if running:
            # Only a live run's recent failures can wedge promotion; a
            # closed run's history is not evidence against its successor.
            try:
                tail: list[Any] = []
                async for event in handle.fetch_history_events(
                    page_size=history_tail_limit
                ):
                    tail.append(event)
                    if len(tail) > history_tail_limit:
                        tail.pop(0)
                observed_from_history = True
                failure_tail = scan_workflow_task_failure_tail(tail)
                task_failures = failure_tail.task_failures
                nondeterminism_failures = failure_tail.nondeterminism_failures
            except Exception:
                # History scanning is best-effort evidence; the describe +
                # query pair above already decides the fail-closed cases.
                observed_from_history = False

        observations.append(
            observation_from_manager_state(
                workflow_id=workflow_id,
                runtime_id=runtime_id,
                run_id=observed_run_id,
                running=running,
                status=status,
                inspection=inspection,
                workflow_task_failures=task_failures,
                nondeterminism_failures=nondeterminism_failures,
                db_held_leases=_db_count_for_runtime(db_held_leases, runtime_id),
                observed_from_history=observed_from_history,
            )
        )
    if db_held_leases is not None:
        observed_runtimes = {
            str(obs.runtime_id or "").strip()
            for obs in observations
            if str(obs.runtime_id or "").strip()
        }
        for runtime_id, count in db_held_leases.items():
            normalized = str(runtime_id or "").strip()
            if not normalized or normalized in observed_runtimes:
                continue
            if wanted and normalized not in wanted:
                continue
            try:
                active = int(count)
            except (TypeError, ValueError):
                continue
            if active <= 0:
                continue
            observations.append(
                ManagerLivenessObservation(
                    workflow_id=f"provider-profile-manager:{normalized}",
                    runtime_id=normalized,
                    run_id="",
                    running=False,
                    status="VISIBILITY_MISSING",
                    inspection_succeeded=False,
                    inspection_status="VISIBILITY_MISSING",
                    error=_bounded(
                        "manager has active durable leases but no Temporal "
                        "Visibility row; Visibility is eventually consistent "
                        "and must not authorize promotion"
                    ),
                    db_held_leases=active,
                )
            )
    return observations


async def read_db_held_lease_counts() -> dict[str, int] | None:
    """Read DB active-lease counts per runtime for ledger reconciliation.

    Counts both ``held`` and ``cleanup_requested`` rows: both states spend a
    slot (see ``ACTIVE_DURABLE_LEASE_STATES``) and cleanup-requested leases
    remain in the manager's ``current_leases``. Counting only ``held`` would
    manufacture a ledger disagreement whenever cleanup is pending.

    Returns ``None`` when the ledger is unreadable so the caller records the
    gap as unavailable evidence instead of treating a missing read as zero
    held leases (which would be exactly backwards).
    """

    try:
        from sqlalchemy import func, select

        from api_service.db.base import get_async_session_context
        from api_service.db.models import ProviderProfileSlotLease
        from moonmind.provider_profiles.lease_client import (
            ACTIVE_DURABLE_LEASE_STATES,
        )
    except Exception:
        return None
    try:
        async with get_async_session_context() as session:
            result = await session.execute(
                select(
                    ProviderProfileSlotLease.runtime_id,
                    func.count(ProviderProfileSlotLease.id),
                ).where(
                    ProviderProfileSlotLease.lease_state.in_(
                        sorted(ACTIVE_DURABLE_LEASE_STATES)
                    )
                ).group_by(
                    ProviderProfileSlotLease.runtime_id
                )
            )
            return {str(runtime_id): int(count) for runtime_id, count in result.all()}
    except Exception:
        return None


def describe_liveness_block(disposition: Mapping[str, Any]) -> str:
    """Render one actionable line per blocking reason for release records."""

    reasons = disposition.get("reasons") or []
    lines = [str(reason) for reason in reasons if str(reason).strip()]
    if not lines:
        return "provider manager liveness gate blocked promotion (no reasons recorded)"
    return "; ".join(lines)
