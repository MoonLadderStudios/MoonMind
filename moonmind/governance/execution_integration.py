"""Production wiring for per-run governance reports (MoonMind#3969).

This module is the single production entrypoint that invokes the portable
governance helpers from :mod:`moonmind.governance.run_reports` at the actual
terminal authority handoff. It maps the existing Temporal execution lifecycle
states to the governance terminal vocabulary, builds explicit
pending/unavailable evidence (never claiming unobserved success), finalizes
the report as an auxiliary step that preserves the canonical terminal
outcome, and builds the Workflow Detail presentation link from the same
result.

Reporting failure is auxiliary: this module never raises for reporting
problems and never changes the canonical task/publication outcome.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from moonmind.governance.run_reports import (
    GovernanceReportStore,
    ReportGenerationStatus,
    build_workflow_detail_governance_link,
    finalize_governance_report,
    reconcile_missing_reports,
)

GOVERNANCE_TERMINAL_OUTCOMES = ("succeeded", "failed", "cancelled", "timed_out")

_COMPLETED_STATES = {"completed", "no_commit"}
_FAILED_STATES = {"failed"}
_CANCELED_STATES = {"canceled", "cancelled"}


def terminal_state_to_governance_outcome(
    state: object,
    close_status: object = None,
) -> str | None:
    """Map a Temporal lifecycle state to the governance terminal vocabulary.

    Returns one of ``succeeded``/``failed``/``cancelled``/``timed_out`` for
    terminal executions, or ``None`` when the execution is not terminal and
    no governance report should be emitted yet. A ``timed_out`` close status
    always wins over the state mapping so retention-expiry stays explicit.
    Unknown or blank inputs return ``None`` (fail closed: no report).
    """

    close = str(close_status or "").strip().lower()
    if close == "timed_out":
        return "timed_out"
    normalized = str(state or "").strip().lower()
    if normalized in _COMPLETED_STATES:
        return "succeeded"
    if normalized in _FAILED_STATES:
        return "failed"
    if normalized in _CANCELED_STATES:
        return "cancelled"
    return None


def build_terminal_governance_evidence(
    *,
    logical_workflow_id: object,
    run_id: object,
    attempt: object = 0,
    terminal_outcome: object,
    owner: object = "unknown-owner",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build explicit pending/unavailable evidence for one terminal execution.

    Sections without collected evidence stay ``pending``/``unavailable`` so
    the report renders as ``partial`` and never claims unobserved cleanup
    succeeded, scans passed, or reviews approved.
    """

    current = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        attempt_value = int(attempt if attempt is not None else 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        attempt_value = 0
    if attempt_value < 0:
        attempt_value = 0
    workflow_id = str(logical_workflow_id or "").strip() or "unknown-workflow"
    run = str(run_id or "").strip() or "unknown-run"
    requester = str(owner or "").strip() or "unknown-owner"
    cutoff = current.isoformat()
    return {
        "logical_workflow_id": workflow_id,
        "run_id": run,
        "attempt": attempt_value,
        "terminal_outcome": str(terminal_outcome or "").strip(),
        "created_at": cutoff,
        "evidence_cutoff": cutoff,
        "completeness": "partial",
        "owner": requester,
        "policy": {"provenance": "unavailable"},
        "profile": {"provenance": "unavailable"},
        "image": {"provenance": "unavailable"},
        "credentials": [],
        "egress": [],
        "container_jobs": [],
        "approvals": [],
        "outbound_scans": [],
        "workspace_changes": [],
        "publications": [],
        "cleanup": {"provenance": "pending", "disposition": "pending"},
    }


def finalize_governance_for_terminal_execution(
    record: Mapping[str, Any],
    *,
    store: GovernanceReportStore | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Finalize one terminal execution record into a governance report.

    ``record`` is the real invocation shape used by the terminal service
    binding: ``workflow_id``/``logical_workflow_id``, ``run_id``,
    ``state``, ``close_status``, ``attempt``, and ``owner_id``/``owner``.
    Older payloads without governance keys remain compatible: missing
    evidence sections default to pending/unavailable and a missing attempt
    defaults to ``0``.

    Never raises. Unknown or non-terminal states yield a recoverable
    ``pending`` status with ``report=None``;store or build failures yield a
    recoverable ``failed`` status with ``report=None``. The canonical
    terminal outcome is always preserved in ``canonical_outcome``.
    """

    if not isinstance(record, Mapping):
        return {
            "canonical_outcome": "failed",
            "report": None,
            "generation_status": {
                "status": "failed",
                "reason_code": "MALFORMED_SOURCE",
                "detail": "terminal record is not a mapping",
                "recoverable": True,
            },
            "link": None,
        }
    state = record.get("state", record.get("mm_state"))
    close_status = record.get("close_status", record.get("closeStatus"))
    outcome = terminal_state_to_governance_outcome(state, close_status)
    if outcome is None:
        canonical = str(state or "").strip().lower() or "failed"
        return {
            "canonical_outcome": canonical,
            "report": None,
            "generation_status": {
                "status": "pending",
                "reason_code": "NOT_TERMINAL",
                "detail": "execution is not in a governance terminal state",
                "recoverable": True,
            },
            "link": None,
        }
    workflow_id = record.get("logical_workflow_id", record.get("workflow_id"))
    evidence = build_terminal_governance_evidence(
        logical_workflow_id=workflow_id,
        run_id=record.get("run_id"),
        attempt=record.get("attempt", 0),
        terminal_outcome=outcome,
        owner=record.get("owner", record.get("owner_id")),
        now=now,
    )
    try:
        final = finalize_governance_report(evidence, store=store)
    except Exception as exc:  # Auxiliary: never break the terminal handoff.
        return {
            "canonical_outcome": outcome,
            "report": None,
            "generation_status": {
                "status": "failed",
                "reason_code": "REPORT_STORE_UNAVAILABLE",
                "detail": str(exc)[:300],
                "recoverable": True,
            },
            "link": None,
        }
    status: ReportGenerationStatus | None = final.generation_status
    link = build_workflow_detail_governance_link(
        logical_workflow_id=str(evidence["logical_workflow_id"]),
        report=final.report,
        generation_status=status,
    )
    status_payload: dict[str, Any] | None = None
    if status is not None:
        status_payload = {
            "status": status.status,
            "reason_code": status.reason_code,
            "detail": status.detail,
            "recoverable": status.recoverable,
        }
    return {
        "canonical_outcome": final.canonical_outcome,
        "report": final.report,
        "generation_status": status_payload,
        "link": {
            "href": link.href,
            "status": link.status,
            "explanation": link.explanation,
            "download_ref": link.download_ref,
        },
    }


def reconcile_missing_governance_reports(
    executions: object,
    *,
    known_report_ids: object = (),
) -> list[dict[str, Any]]:
    """Emit retryable work items for terminal executions missing reports.

    Thin driver over :func:`reconcile_missing_reports` so existing
    worker/schedule infrastructure can re-drive finalization without a new
    always-on service. Malformed rows are skipped, never aborting the batch.
    """

    known = list(known_report_ids) if isinstance(known_report_ids, (list, tuple, set)) else []
    items = list(executions) if isinstance(executions, (list, tuple)) else []
    return reconcile_missing_reports(items, known_report_ids=known)


__all__ = [
    "GOVERNANCE_TERMINAL_OUTCOMES",
    "build_terminal_governance_evidence",
    "finalize_governance_for_terminal_execution",
    "reconcile_missing_governance_reports",
    "terminal_state_to_governance_outcome",
]
