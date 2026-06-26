"""Integration-style boundary tests for Claude governance telemetry."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas import build_claude_governance_telemetry_fixture_flow

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

NOW = datetime(2026, 4, 16, tzinfo=UTC)

def test_claude_governance_telemetry_boundary_is_payload_light() -> None:
    flow = build_claude_governance_telemetry_fixture_flow(
        session_id="session-1",
        session_group_id="group-1",
        user_id="user-1",
        workspace_id="workspace-1",
        policy_envelope_id="policy-1",
        created_at=NOW,
    )

    assert flow.subscription.subscription_type == "session"
    assert {event.event_family for event in flow.events} == {
        "session",
        "surface",
        "policy",
        "turn",
        "work",
        "decision",
        "child_work",
    }
    assert all(event.event_id for event in flow.events)

    assert flow.storage_evidence.payload_light is True
    assert flow.storage_evidence.artifact_refs == ("artifact://session-1/audit",)
    assert set(flow.storage_evidence.runtime_local_payload_kinds) == {
        "transcript",
        "full_file_read",
        "checkpoint_payload",
        "local_cache",
    }

    assert {item.class_name for item in flow.retention_evidence.classes} == {
        "hot_session_metadata",
        "hot_event_log",
        "usage_rollups",
        "audit_event_metadata",
        "checkpoint_payloads",
    }
    assert all(item.policy_controlled for item in flow.retention_evidence.classes)

    assert [metric.metric_name for metric in flow.telemetry_evidence.metrics] == [
        "managed_sessions_active",
        "managed_usage_tokens",
    ]
    assert [span.span_name for span in flow.telemetry_evidence.trace_spans] == [
        "session.bootstrap",
        "turn.process",
    ]

    assert {rollup.token_direction for rollup in flow.usage_rollups} == {
        "input",
        "output",
        "total",
    }
    assert any(rollup.child_context_id for rollup in flow.usage_rollups)
    assert any(rollup.team_member_session_id for rollup in flow.usage_rollups)

    assert flow.governance_evidence.policy_trust_level == "endpoint_enforced"
    assert flow.governance_evidence.provider_mode == "anthropic_api"
    assert flow.governance_evidence.execution_security_mode == (
        "remote_control_projection"
    )
    assert flow.governance_evidence.protected_path_policy
    assert flow.governance_evidence.hook_audits[0].hook_name == "audit-write"

    assert flow.compliance_export.governance.session_id == "session-1"
    assert flow.dashboard_summary.provider_mode == "anthropic_api"
    assert flow.dashboard_summary.governance_evidence_refs == ("governance-1",)

    forbidden_keys = {
        "sourceCode",
        "transcript",
        "fullFileRead",
        "checkpointPayload",
        "localCache",
    }
    for record in (
        flow.storage_evidence,
        flow.retention_evidence,
        flow.telemetry_evidence,
        flow.governance_evidence,
        flow.compliance_export,
        flow.dashboard_summary,
    ):
        wire = record.model_dump(by_alias=True)
        assert forbidden_keys.isdisjoint(wire)
