"""Workflow Detail boundary coverage for remediation capability discovery."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api_service.api.routers.executions import _serialize_remediation_link_summary


@pytest.mark.parametrize(
    "state,paused,expected",
    [
        ("failed", False, []),
        ("executing", False, ["execution.pause"]),
        ("executing", True, ["execution.resume"]),
    ],
)
def test_control_projection_matches_the_actual_target_state(state, paused, expected):
    now = datetime(2026, 10, 5, tzinfo=UTC)
    link = SimpleNamespace(
        remediation_workflow_id="repair",
        remediation_run_id="repair-run",
        target_workflow_id="target",
        target_run_id="source-run",
        mode="repair",
        authority_mode="approval_gated",
        status="created",
        allowed_actions=["execution.pause", "execution.resume"],
        current_target_state=state,
        target_paused=paused,
        target_runtime="omnigent",
        created_at=now,
        updated_at=now,
    )
    result = _serialize_remediation_link_summary(link)
    assert result.allowedActions == expected
    assert [
        row.actionKind for row in result.actionCapabilities if row.requestable
    ] == expected


def test_remediation_link_publishes_complete_evaluated_capability_matrix() -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    link = SimpleNamespace(
        remediation_workflow_id="remediation-1",
        remediation_run_id="remediation-run-1",
        target_workflow_id="target-1",
        target_run_id="target-run-1",
        mode="repair",
        authority_mode="admin_auto",
        status="acting",
        allowed_actions=["execution.pause"],
        current_target_state="running",
        target_runtime="temporal",
        host_mode="managed",
        evidence_degraded=False,
        unavailable_evidence_classes=[],
        checkpoint_branch_links=[],
        created_at=now,
        updated_at=now,
    )

    result = _serialize_remediation_link_summary(link)

    rows = {row.actionKind: row for row in result.actionCapabilities}
    assert result.allowedActions == ["execution.pause"]
    assert rows["execution.pause"].requestable is True
    assert rows["execution.resume"].requestable is False
    assert "target_policy_denied" in rows["execution.resume"].blockedReasons
    assert rows["session.terminate"].requestable is False
    assert "execution_backend_unavailable" in rows["session.terminate"].blockedReasons


def test_remediation_link_reports_approval_backend_ready_before_request() -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    link = SimpleNamespace(
        remediation_workflow_id="remediation-1",
        remediation_run_id="remediation-run-1",
        target_workflow_id="target-1",
        target_run_id="target-run-1",
        mode="repair",
        authority_mode="approval_gated",
        status="created",
        approval_state=None,
        allowed_actions=["execution.pause"],
        current_target_state="running",
        evidence_degraded=False,
        checkpoint_branch_links=[],
        created_at=now,
        updated_at=now,
    )

    result = _serialize_remediation_link_summary(link)
    pause = next(
        row for row in result.actionCapabilities if row.actionKind == "execution.pause"
    )
    assert pause.requestable is True
    assert pause.approvalBackendReady is True
    assert "approval_backend_unavailable" not in pause.blockedReasons
