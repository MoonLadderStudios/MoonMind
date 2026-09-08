"""Caller-backed upstream-duplication audit boundary.

Source issue: MoonLadderStudios/MoonMind#3954 (parent #3928).

Proves the audit deliverable: every suspected duplicate carries production
callers and a disposition, the boundary has one owner per mutation with
evidence kept downstream, production paths retain coverage, fail-closed
rejections hold for drift/wrong-owner/stale-generation/unknown-route/
missing-evidence/credential-mismatch, no table/field is removed with
unresolved consumers, and reduction is measured after preserving behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.omnigent import upstream_duplication_audit as audit
from moonmind.omnigent.bridge_proxy import (
    OmnigentBridgeError,
    validate_bridge_host_fields,
)
from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT
from moonmind.omnigent.native_ui import (
    SUPPORTED_NATIVE_UI_VERSIONS,
    evaluate_native_ui_compatibility,
    is_valid_chat_binding_id,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

REQUIRED_PRODUCTION_BOUNDARIES = (
    "launch",
    "auth/profile admission",
    "session/turn control",
    "retry/cancel",
    "terminal evidence",
    "post-host-cleanup reads",
    "publication",
    "cleanup",
)


def test_every_row_carries_required_columns_and_real_callers() -> None:
    assert len(audit.OWNERSHIP_TABLE) >= 8
    for row in audit.OWNERSHIP_TABLE:
        assert row.candidate_id.strip()
        assert row.production_entrypoint.strip()
        assert row.state_decision_owned.strip()
        assert row.persisted_consumers and all(
            consumer.strip() for consumer in row.persisted_consumers
        )
        assert row.upstream_api_at_pinned_commit.strip()
        assert row.surviving_owner.strip()
        assert row.evidence_test.strip()
        assert row.disposition.strip()
        # Production entrypoints are caller-backed: each names a real module.
        modules = [
            part.split("::")[0].strip()
            for part in row.production_entrypoint.split("+")
        ]
        assert modules
        for module in modules:
            assert (REPO_ROOT / module).is_file(), module


def test_one_owner_per_mutation_with_evidence_downstream() -> None:
    candidate_ids = [row.candidate_id for row in audit.OWNERSHIP_TABLE]
    assert len(set(candidate_ids)) == len(candidate_ids)
    for row in audit.OWNERSHIP_TABLE:
        # MoonMind governance is never surrendered as redundant: every
        # surviving owner keeps the MoonMind side authoritative while upstream
        # owns only runtime mechanics or raw observations.
        assert "MoonMind" in row.surviving_owner, row.candidate_id
        assert "upstream owns" in row.surviving_owner, row.candidate_id


def test_production_boundary_coverage_is_caller_backed() -> None:
    covered = dict(audit.PRODUCTION_BOUNDARY_COVERAGE)
    for boundary in REQUIRED_PRODUCTION_BOUNDARIES:
        assert boundary in covered, boundary
        audit.get_ownership_row(covered[boundary])


def test_unknown_candidate_fails_closed() -> None:
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.get_ownership_row("not-a-real-candidate")
    assert exc_info.value.code == "omnigent_audit_unknown_candidate"


def test_upstream_drift_is_rejected() -> None:
    assert audit.check_upstream_pin(PINNED_OMNIGENT_COMMIT) == PINNED_OMNIGENT_COMMIT
    for bad in ("", "  ", "deadbeef" * 5):
        with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
            audit.check_upstream_pin(bad)
        assert exc_info.value.code == "omnigent_audit_upstream_drift"
    # The #3954 review baseline differs from the implementation pin, so audit
    # evidence alone cannot authorize a removal.
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.check_audit_baseline_current()
    assert exc_info.value.code == "omnigent_audit_upstream_drift"


def test_native_ui_version_gate_rejects_drift() -> None:
    pinned = sorted(SUPPORTED_NATIVE_UI_VERSIONS)[0]
    assert evaluate_native_ui_compatibility(pinned).ready is True
    assert (
        evaluate_native_ui_compatibility("unverified-next-build").ready is False
    )
    assert evaluate_native_ui_compatibility("").reason == "native_ui_version_unknown"
    assert is_valid_chat_binding_id("chatb_abc123") is True
    assert is_valid_chat_binding_id("not-a-binding") is False


def test_bridge_binding_rejects_cross_owner_reuse() -> None:
    assert (
        audit.check_owner_match(stored_workflow_id="wf-1", calling_workflow_id="wf-1")
        == "wf-1"
    )
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.check_owner_match(stored_workflow_id="wf-1", calling_workflow_id="wf-2")
    assert exc_info.value.code == "omnigent_audit_wrong_owner"
    # The production facade enforces the same ownership at the boundary.
    validate_bridge_host_fields(host_type="managed", host_id=None, workspace=None)
    with pytest.raises(OmnigentBridgeError):
        validate_bridge_host_fields(host_type="managed", host_id="h-1", workspace=None)
    with pytest.raises(OmnigentBridgeError):
        validate_bridge_host_fields(
            host_type="direct-upstream", host_id=None, workspace=None
        )


def test_stale_generation_is_rejected() -> None:
    assert (
        audit.check_generation_match(observed="gen-7", authorized="gen-7") == "gen-7"
    )
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.check_generation_match(observed="gen-6", authorized="gen-7")
    assert exc_info.value.code == "omnigent_audit_stale_generation"


def test_unknown_catalog_route_is_rejected() -> None:
    assert audit.check_route_allowed("changed_files") == "changed_files"
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.check_route_allowed("upstream-direct-proxy")
    assert exc_info.value.code == "omnigent_audit_unknown_route"


def test_unknown_resource_operation_is_rejected() -> None:
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.check_route_allowed("../escape")
    assert exc_info.value.code == "omnigent_audit_unknown_route"


def test_missing_terminal_evidence_is_rejected() -> None:
    refs = {"terminalRef": "artifact:terminal", "captureRef": "artifact:capture"}
    assert audit.check_evidence_present(refs) == refs
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.check_evidence_present({"terminalRef": "artifact:terminal", "captureRef": ""})
    assert exc_info.value.code == "omnigent_audit_missing_evidence"


def test_credential_scope_rejects_mismatch() -> None:
    assert (
        audit.check_credential_scope(
            presented_profile_ref="codex_openai_oauth",
            authorized_profile_ref="codex_openai_oauth",
            generation_matches=True,
        )
        == "codex_openai_oauth"
    )
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.check_credential_scope(
            presented_profile_ref="other_profile",
            authorized_profile_ref="codex_openai_oauth",
            generation_matches=True,
        )
    assert exc_info.value.code == "omnigent_audit_credential_mismatch"
    with pytest.raises(audit.UpstreamDuplicationAuditError):
        audit.check_credential_scope(
            presented_profile_ref="codex_openai_oauth",
            authorized_profile_ref="codex_openai_oauth",
            generation_matches=False,
        )


def test_profile_snapshot_guard_rejects_credential_mismatch() -> None:
    # Profile admission never substitutes a different credential source: a
    # presented profile outside the authorized scope fails closed even when a
    # generation value is present.
    with pytest.raises(audit.UpstreamDuplicationAuditError) as exc_info:
        audit.check_credential_scope(
            presented_profile_ref="",
            authorized_profile_ref="codex_openai_oauth",
            generation_matches=True,
        )
    assert exc_info.value.code == "omnigent_audit_credential_mismatch"


def test_no_table_or_field_removal_with_unresolved_consumers() -> None:
    for row in audit.OWNERSHIP_TABLE:
        decision = audit.evaluate_removal_eligibility(row.candidate_id)
        assert decision.eligible is False
        assert decision.blockers, row.candidate_id
        assert row.removal_criteria.strip(), row.candidate_id


def test_reduction_measured_after_preserving_behavior() -> None:
    summary = audit.audit_reduction_summary()
    assert summary.candidates_examined == len(audit.OWNERSHIP_TABLE)
    assert summary.preserved == len(audit.OWNERSHIP_TABLE)
    assert summary.removed_tables_or_fields == 0
    assert set(summary.residual_dependencies) == {
        row.candidate_id for row in audit.OWNERSHIP_TABLE
    }
