"""Unit tests for per-run evidence-backed governance reports (MoonMind#3969)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from moonmind.governance.run_reports import (
    GOVERNANCE_REPORT_CONTRACT_VERSION,
    GovernanceReportError,
    GovernanceReportStore,
    ReportGenerationStatus,
    authorize_governance_report_access,
    build_governance_report,
    build_input_digest,
    build_workflow_detail_governance_link,
    finalize_governance_report,
    reconcile_missing_reports,
    render_governance_report_markdown,
    review_outcome_is_approved,
    scan_outcome_is_pass,
)

CANARY = "ghp_canaryValue1234567890abcdef"
CANARY_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"


def _evidence(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "logical_workflow_id": "wf-3930",
        "run_id": "run-001",
        "attempt": 0,
        "terminal_outcome": "succeeded",
        "created_at": "2026-09-01T10:00:00Z",
        "evidence_cutoff": "2026-09-01T10:05:00Z",
        "completeness": "complete",
        "owner": "owner-1",
        "policy": {"provenance": "observed", "ref": "policy:v3", "source": "policy-store"},
        "profile": {"provenance": "observed", "ref": "profile:prod"},
        "image": {"provenance": "observed", "ref": "image:sha256-abc"},
        "credentials": [
            {"lease_ref": "lease-1", "ownership": "run_owned", "state": "released"},
            {"lease_ref": "lease-2", "ownership": "profile_owned", "state": "active"},
        ],
        "egress": [{"destination_ref": "api.provider.example:443", "decision": "allow", "policy_ref": "egress:v2"}],
        "container_jobs": [{"job_ref": "job-1", "status": "succeeded"}],
        "approvals": [{"review_ref": "review-1", "outcome": "approved"}],
        "outbound_scans": [
            {
                "surface": "chat.openai.messages[0].content",
                "outcome": "pass",
                "finding_categories": [],
                "location_refs": ["chat.openai.messages[0].content"],
            }
        ],
        "workspace_changes": [{"change_ref": "art-change-1", "status": "applied"}],
        "publications": [{"publication_ref": "pub-1", "status": "published"}],
        "cleanup": {"provenance": "observed", "disposition": "succeeded"},
        "spend_usage": {"provenance": "unavailable", "measurements": []},
        "source_artifact_digests": {"primary": "a" * 32},
    }
    base.update(overrides)
    return base


def test_terminal_outcomes_each_produce_report_or_recoverable_status() -> None:
    for outcome in ("succeeded", "failed", "cancelled", "timed_out"):
        final = finalize_governance_report(_evidence(terminal_outcome=outcome), store=GovernanceReportStore())
        assert final.canonical_outcome == outcome
        assert final.canonical_outcome_preserved is True
        assert final.report is not None
        assert final.report["terminal_outcome"] == outcome


def test_deterministic_fixture_matches_json_and_rendering_with_missing_states() -> None:
    evidence = _evidence(
        credentials=[],
        egress=[],
        approvals=[],
        outbound_scans=[],
        cleanup=None,
        completeness="partial",
    )
    first = build_governance_report(evidence)
    second = build_governance_report(json.loads(json.dumps(evidence)))
    assert first == second
    assert first["contract_version"] == GOVERNANCE_REPORT_CONTRACT_VERSION
    assert first["status"] == "partial"
    assert first["credentials"]["provenance"] == "unavailable"
    assert first["egress"]["provenance"] == "unavailable"
    assert first["cleanup"]["disposition"] == "unknown"
    assert first["cleanup"]["provenance"] == "pending"
    assert "unavailable is never a pass" in render_governance_report_markdown(first)
    assert "not" in first["egress"]["coverage_note"].lower() or "only" in first["egress"]["coverage_note"]
    # Deterministic serialization: sorted-keys canonical bytes are stable.
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_duplicate_finalization_reuses_result_and_late_evidence_supersedes() -> None:
    store = GovernanceReportStore()
    evidence = _evidence()
    first = finalize_governance_report(evidence, store=store)
    second = finalize_governance_report(evidence, store=store)
    assert first.report is not None and second.report is not None
    assert first.report["report_id"] == second.report["report_id"]
    assert len(store) == 1

    superseded = store.supersede(
        first.report["report_id"],
        _evidence(evidence_cutoff="2026-09-01T11:00:00Z", completeness="complete"),
    )
    assert superseded["supersedes"] == first.report["report_id"]
    assert superseded["report_id"] != first.report["report_id"]
    assert superseded["logical_workflow_id"] == "wf-3930"
    assert superseded["run_id"] == "run-001"
    assert store.get(first.report["report_id"]) is not None
    assert len(store) == 2


def test_store_failure_cancellation_and_partial_cleanup_stay_auxiliary() -> None:
    failing = GovernanceReportStore(fail_writes=True)
    failed = finalize_governance_report(_evidence(), store=failing)
    assert failed.report is None
    assert failed.canonical_outcome == "succeeded"
    assert failed.canonical_outcome_preserved is True
    assert isinstance(failed.generation_status, ReportGenerationStatus)
    assert failed.generation_status.status == "failed"
    assert failed.generation_status.recoverable is True
    assert failed.generation_status.reason_code == "REPORT_STORE_UNAVAILABLE"

    cancelled = finalize_governance_report(_evidence(), store=GovernanceReportStore(), cancelled_during_finalization=True)
    assert cancelled.report is None
    assert cancelled.canonical_outcome == "succeeded"
    assert cancelled.generation_status is not None
    assert cancelled.generation_status.status == "pending"
    assert cancelled.generation_status.reason_code == "CANCELLED_DURING_FINALIZATION"

    partial = build_governance_report(_evidence(cleanup={"provenance": "observed", "disposition": "partial"}))
    assert partial["cleanup"]["disposition"] == "partial"
    assert partial["status"] == "ready"  # completeness complete; cleanup stays partial, not rewritten


def test_canaries_absent_from_output_rendering_diagnostics_and_logs() -> None:
    with pytest.raises(GovernanceReportError):
        build_governance_report(_evidence(annotation=CANARY))
    with pytest.raises(GovernanceReportError):
        build_governance_report(_evidence(policy={"provenance": "observed", "ref": CANARY}))
    with pytest.raises(GovernanceReportError):
        build_governance_report(_evidence(credentials=[{"lease_ref": "l", "ownership": "run_owned", "state": CANARY_KEY}]))
    with pytest.raises(GovernanceReportError):
        build_governance_report(_evidence(**{"api_key": "x"}))

    report = build_governance_report(_evidence())
    blob = json.dumps(report) + render_governance_report_markdown(report)
    assert "ghp_" not in blob
    assert "PRIVATE KEY" not in blob


def test_access_expiry_digest_and_malformed_sources_have_safe_outcomes() -> None:
    report = build_governance_report(_evidence())
    assert authorize_governance_report_access(report, requester_owner="owner-1").allowed is True

    wrong_owner = authorize_governance_report_access(report, requester_owner="owner-2")
    assert (wrong_owner.allowed, wrong_owner.code) == (False, "WRONG_OWNER")

    expired = authorize_governance_report_access(
        report, requester_owner="owner-1", now=datetime(2027, 12, 1, tzinfo=UTC)
    )
    assert (expired.allowed, expired.code) == (False, "EVIDENCE_EXPIRED")

    mismatch = authorize_governance_report_access(
        report, requester_owner="owner-1", expected_digests={"primary": "b" * 32}
    )
    assert (mismatch.allowed, mismatch.code) == (False, "DIGEST_MISMATCH")

    malformed = authorize_governance_report_access({"report_kind": "nope"}, requester_owner="owner-1")
    assert (malformed.allowed, malformed.code) == (False, "MALFORMED_SOURCE")

    with pytest.raises(GovernanceReportError, match="MALFORMED_SOURCE"):
        build_governance_report(_evidence(terminal_outcome="exploded"))
    with pytest.raises(GovernanceReportError, match="MALFORMED_SOURCE"):
        build_governance_report(_evidence(source_artifact_digests={"primary": "not-a-digest"}))


def test_missing_scans_reviews_never_upgrade_to_pass_or_approval() -> None:
    assert scan_outcome_is_pass({"outcome": "unavailable"}) is False
    assert scan_outcome_is_pass({"outcome": "pending"}) is False
    assert scan_outcome_is_pass({"outcome": "pass"}) is True
    assert review_outcome_is_approved({"outcome": "unavailable"}) is False
    assert review_outcome_is_approved({"outcome": "approved"}) is True

    report = build_governance_report(_evidence(outbound_scans=[], approvals=[]))
    assert report["outbound_scans"]["provenance"] == "unavailable"
    assert report["approvals"]["provenance"] == "unavailable"
    rendered = render_governance_report_markdown(report)
    assert "PASS" not in rendered
    assert "unavailable is never a pass" in rendered


def test_reconciler_is_bounded_and_skips_known_reports() -> None:
    executions = [
        {"logical_workflow_id": "wf-1", "run_id": "run-1", "attempt": 0, "terminal_outcome": "succeeded"},
        {"logical_workflow_id": "wf-1", "run_id": "run-2", "attempt": 1, "terminal_outcome": "timed_out", "report_id": "govrep_x"},
        {"logical_workflow_id": "wf-1", "run_id": "", "terminal_outcome": "failed"},
        {"logical_workflow_id": "wf-1", "run_id": "run-3", "terminal_outcome": "exploded"},
    ]
    items = reconcile_missing_reports(executions, known_report_ids=["govrep_x"])
    assert [item["run_id"] for item in items] == ["run-1"]
    assert items[0]["reason"] == "missing_report_after_terminal_outcome"


def test_workflow_detail_link_statuses_and_authorized_download() -> None:
    report = build_governance_report(_evidence())
    link = build_workflow_detail_governance_link(
        logical_workflow_id="wf-3930",
        report=report,
        download_ref={"artifact_ref_v": 1, "artifact_id": "art-report-1"},
    )
    assert link.status == "ready"
    assert "wf-3930" in link.href
    assert link.download_ref == {"artifact_ref_v": 1, "artifact_id": "art-report-1"}

    pending = build_workflow_detail_governance_link(
        logical_workflow_id="wf-3930",
        generation_status=ReportGenerationStatus(status="pending", reason_code="REPORT_PENDING"),
    )
    assert pending.status == "pending"
    assert "never claim unresolved cleanup succeeded" in pending.explanation

    historical = build_workflow_detail_governance_link(
        logical_workflow_id="wf/gone", report=build_governance_report(_evidence(cleanup=None))
    )
    assert historical.status == "partial"
    assert CANARY not in historical.explanation


def test_input_digest_and_report_identity_are_stable() -> None:
    evidence = _evidence()
    assert build_input_digest({"a": 1}) == build_input_digest({"a": 1})
    assert build_governance_report(evidence)["report_id"] == build_governance_report(evidence)["report_id"]
    other = build_governance_report(_evidence(evidence_cutoff="2026-09-01T12:00:00Z"))
    assert other["report_id"] != build_governance_report(evidence)["report_id"]


def test_evidence_ttl_boundary() -> None:
    report = build_governance_report(_evidence(evidence_cutoff="2026-09-01T10:05:00Z"))
    cutoff = datetime(2026, 9, 1, 10, 5, tzinfo=UTC)
    inside = cutoff + timedelta(hours=24 * 30) - timedelta(seconds=1)
    outside = cutoff + timedelta(hours=24 * 30) + timedelta(seconds=1)
    assert authorize_governance_report_access(report, requester_owner="owner-1", now=inside).code == "OK"
    assert authorize_governance_report_access(report, requester_owner="owner-1", now=outside).code == "EVIDENCE_EXPIRED"
