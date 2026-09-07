"""Unit tests for the governance terminal-handoff integration (MoonMind#3969)."""

from __future__ import annotations

from datetime import UTC, datetime

from moonmind.governance.execution_integration import (
    build_terminal_governance_evidence,
    finalize_governance_for_terminal_execution,
    reconcile_missing_governance_reports,
    terminal_state_to_governance_outcome,
)
from moonmind.governance.run_reports import GovernanceReportStore


def _record(**overrides):
    base = {
        "workflow_id": "wf-3930",
        "run_id": "run-001",
        "attempt": 0,
        "state": "completed",
        "close_status": "completed",
        "owner_id": "owner-1",
    }
    base.update(overrides)
    return base


def test_terminal_state_mapping_covers_all_outcomes() -> None:
    assert terminal_state_to_governance_outcome("completed", "completed") == "succeeded"
    assert terminal_state_to_governance_outcome("no_commit", "completed") == "succeeded"
    assert terminal_state_to_governance_outcome("failed", "failed") == "failed"
    assert terminal_state_to_governance_outcome("canceled", "canceled") == "cancelled"
    # A timed_out close status always wins so retention expiry stays explicit.
    assert terminal_state_to_governance_outcome("completed", "timed_out") == "timed_out"
    assert terminal_state_to_governance_outcome("failed", "timed_out") == "timed_out"


def test_non_terminal_and_degraded_inputs_emit_nothing() -> None:
    assert terminal_state_to_governance_outcome("executing", "running") is None
    assert terminal_state_to_governance_outcome("", "") is None
    assert terminal_state_to_governance_outcome(None, None) is None
    assert terminal_state_to_governance_outcome("exploded", "weird") is None


def test_finalize_emits_partial_report_for_every_terminal_outcome() -> None:
    now = datetime(2026, 9, 1, 10, 6, tzinfo=UTC)
    for state, close, outcome in (
        ("completed", "completed", "succeeded"),
        ("failed", "failed", "failed"),
        ("canceled", "canceled", "cancelled"),
        ("failed", "timed_out", "timed_out"),
    ):
        result = finalize_governance_for_terminal_execution(
            _record(state=state, close_status=close), now=now
        )
        assert result["canonical_outcome"] == outcome
        assert result["report"] is not None
        assert result["report"]["terminal_outcome"] == outcome
        # Minimal evidence defaults to partial, never ready with pending cleanup.
        assert result["report"]["status"] == "partial"
        assert result["link"] is not None
        assert result["link"]["href"].startswith("/workflows/wf-3930/evidence")


def test_finalize_preserves_canonical_outcome_when_store_fails() -> None:
    store = GovernanceReportStore(fail_writes=True)
    result = finalize_governance_for_terminal_execution(_record(), store=store)
    assert result["canonical_outcome"] == "succeeded"
    assert result["report"] is None
    assert result["generation_status"]["recoverable"] is True


def test_finalize_never_raises_for_malformed_or_non_terminal_records() -> None:
    pending = finalize_governance_for_terminal_execution(_record(state="executing"))
    assert pending["report"] is None
    assert pending["generation_status"]["reason_code"] == "NOT_TERMINAL"

    malformed = finalize_governance_for_terminal_execution("not-a-mapping")  # type: ignore[arg-type]
    assert malformed["report"] is None
    assert malformed["generation_status"]["reason_code"] == "MALFORMED_SOURCE"


def test_evidence_defaults_are_explicitly_partial() -> None:
    evidence = build_terminal_governance_evidence(
        logical_workflow_id="wf-1",
        run_id="run-9",
        terminal_outcome="succeeded",
        now=datetime(2026, 9, 1, 10, 6, tzinfo=UTC),
    )
    assert evidence["completeness"] == "partial"
    assert evidence["cleanup"] == {"provenance": "pending", "disposition": "pending"}
    assert evidence["attempt"] == 0


def test_reconciler_is_bounded_and_skips_known_reports() -> None:
    executions = [
        {"logical_workflow_id": "wf-1", "run_id": "run-1", "attempt": 0, "terminal_outcome": "succeeded"},
        {"logical_workflow_id": "wf-1", "run_id": "run-2", "attempt": 1, "terminal_outcome": "timed_out",
         "report_id": "govrep_x"},
        {"logical_workflow_id": "wf-1", "run_id": "", "terminal_outcome": "failed"},
        "malformed-row",
    ]
    items = reconcile_missing_governance_reports(executions, known_report_ids=["govrep_x"])
    assert [item["run_id"] for item in items] == ["run-1"]


def test_legacy_record_shape_without_governance_keys_stays_compatible() -> None:
    legacy = {"workflow_id": "wf-legacy", "run_id": "run-legacy", "state": "failed"}
    result = finalize_governance_for_terminal_execution(legacy)
    assert result["canonical_outcome"] == "failed"
    assert result["report"] is not None
