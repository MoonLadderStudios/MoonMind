"""Hermetic coverage for the remediation link summary projection (issue #3512).

Verifies AC11 fields (autonomous origin, operator takeover) and the current
phase are serialized from canonical link data, not reconstructed from logs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from api_service.api.routers.executions import _serialize_remediation_link_summary


def _link(**overrides):
    base = dict(
        remediation_workflow_id="mm:remediate",
        remediation_run_id="run-r",
        target_workflow_id="mm:target",
        target_run_id="run-t",
        mode="snapshot_then_follow",
        authority_mode="admin_auto",
        status="acting",
        trigger_type="policy",
        action_policy_ref="admin_healer_default",
        created_by_workflow_id="mm:parent",
        remediation_phase="acting",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_autonomous_origin_is_visible_from_link_data():
    model = _serialize_remediation_link_summary(_link())
    assert model.autonomousOrigin is not None
    assert model.autonomousOrigin.triggerOrigin == "policy"
    assert model.autonomousOrigin.autonomous is True
    assert model.autonomousOrigin.policyRef == "admin_healer_default"
    assert model.currentPhase == "acting"


def test_manual_observe_only_is_not_autonomous():
    model = _serialize_remediation_link_summary(
        _link(authority_mode="observe_only", trigger_type="manual")
    )
    assert model.autonomousOrigin.autonomous is False
    assert model.autonomousOrigin.triggerOrigin == "manual"


def test_operator_takeover_available_while_active_and_closed_when_terminal():
    active = _serialize_remediation_link_summary(_link(status="acting"))
    assert active.operatorTakeover.available is True
    resolved = _serialize_remediation_link_summary(_link(status="resolved"))
    assert resolved.operatorTakeover.available is False


def test_operator_takeover_reports_requested_state():
    model = _serialize_remediation_link_summary(
        _link(
            operator_takeover_requested=True,
            operator_takeover_actor="op@example.com",
        )
    )
    assert model.operatorTakeover.requested is True
    assert model.operatorTakeover.actor == "op@example.com"
