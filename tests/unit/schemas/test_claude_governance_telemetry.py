"""Unit tests for Claude governance telemetry contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas import (
    CLAUDE_EVENT_FAMILIES,
    CLAUDE_TELEMETRY_METRIC_NAMES,
    CLAUDE_TELEMETRY_SPAN_NAMES,
    ClaudeComplianceExportView,
    ClaudeEventEnvelope,
    ClaudeEventSubscription,
    ClaudeGovernanceEvidence,
    ClaudeHookAudit,
    ClaudeProviderDashboardSummary,
    ClaudeRetentionClass,
    ClaudeRetentionEvidence,
    ClaudeStorageEvidence,
    ClaudeTelemetryEvidence,
    ClaudeTelemetryMetric,
    ClaudeTelemetrySpan,
    ClaudeUsageRollup,
)

NOW = datetime(2026, 4, 16, tzinfo=UTC)

def _required_retention_classes() -> tuple[ClaudeRetentionClass, ...]:
    return (
        ClaudeRetentionClass(
            className="hot_session_metadata",
            retentionValue="30d",
            policyControlled=True,
        ),
        ClaudeRetentionClass(
            className="hot_event_log",
            retentionValue="30d",
            policyControlled=True,
        ),
        ClaudeRetentionClass(
            className="usage_rollups",
            retentionValue="90d",
            policyControlled=True,
        ),
        ClaudeRetentionClass(
            className="audit_event_metadata",
            retentionValue="org_policy",
            policyControlled=True,
        ),
        ClaudeRetentionClass(
            className="checkpoint_payloads",
            retentionValue="runtime_local_default",
            policyControlled=True,
        ),
    )

def _hook_audit() -> ClaudeHookAudit:
    return ClaudeHookAudit(
        auditId="hook-audit-1",
        sessionId="session-1",
        turnId="turn-1",
        hookName="audit-write",
        sourceScope="managed",
        eventType="PreToolUse",
        matcher="Write",
        outcome="allow",
        createdAt=NOW,
    )

def test_event_subscription_and_envelope_validate_closed_families() -> None:
    subscription = ClaudeEventSubscription(
        subscriptionId="sub-session-1",
        subscriptionType="session",
        scopeId="session-1",
        eventFamilies=CLAUDE_EVENT_FAMILIES,
        createdAt=NOW,
    )

    assert subscription.event_families == CLAUDE_EVENT_FAMILIES
    assert "session" in subscription.model_dump(by_alias=True)["eventFamilies"]

    envelope = ClaudeEventEnvelope(
        eventId="event-surface-attached",
        eventFamily="surface",
        eventName="surface.attached",
        sessionId="session-1",
        surfaceId="surface-terminal",
        occurredAt=NOW,
    )

    assert envelope.event_name == "surface.attached"
    assert envelope.session_id == "session-1"

    with pytest.raises(ValidationError):
        ClaudeEventSubscription(
            subscriptionId="sub-bad",
            subscriptionType="session",
            scopeId="session-1",
            eventFamilies=("unknown",),
            createdAt=NOW,
        )

    with pytest.raises(ValidationError, match="eventName"):
        ClaudeEventEnvelope(
            eventId="event-mismatch",
            eventFamily="surface",
            eventName="policy.compiled",
            sessionId="session-1",
            occurredAt=NOW,
        )

def test_event_envelope_rejects_missing_required_identity() -> None:
    with pytest.raises(ValidationError, match="sessionId"):
        ClaudeEventEnvelope(
            eventId="event-session-started",
            eventFamily="session",
            eventName="session.started",
            occurredAt=NOW,
        )

    with pytest.raises(ValidationError, match="sessionGroupId"):
        ClaudeEventEnvelope(
            eventId="event-team-created",
            eventFamily="child_work",
            eventName="team.group.created",
            sessionId="session-1",
            occurredAt=NOW,
        )

def test_event_envelope_allows_non_session_scoped_identities() -> None:
    policy_event = ClaudeEventEnvelope(
        eventId="event-policy-compiled",
        eventFamily="policy",
        eventName="policy.compiled",
        policyEnvelopeId="policy-1",
        occurredAt=NOW,
    )
    group_event = ClaudeEventEnvelope(
        eventId="event-team-created",
        eventFamily="child_work",
        eventName="team.group.created",
        sessionGroupId="group-1",
        occurredAt=NOW,
    )

    assert policy_event.session_id is None
    assert policy_event.policy_envelope_id == "policy-1"
    assert group_event.session_id is None
    assert group_event.session_group_id == "group-1"

def test_event_envelope_metadata_is_payload_light() -> None:
    with pytest.raises(ValidationError, match="payload-light"):
        ClaudeEventEnvelope(
            eventId="event-session-started",
            eventFamily="session",
            eventName="session.started",
            sessionId="session-1",
            occurredAt=NOW,
            metadata={"checkpointPayload": {"raw": "runtime-local payload"}},
        )

def test_storage_evidence_is_payload_light_by_default() -> None:
    evidence = ClaudeStorageEvidence(
        evidenceId="storage-1",
        sessionId="session-1",
        centralStore="event_log",
        storedKinds=("event_envelope", "artifact_pointer"),
        artifactRefs=("artifact://audit/event-log",),
        runtimeLocalPayloadKinds=("transcript", "checkpoint_payload"),
        payloadLight=True,
        createdAt=NOW,
        metadata={"summaryRef": "artifact://audit/summary"},
    )

    assert evidence.payload_light is True
    assert evidence.artifact_refs == ("artifact://audit/event-log",)

    with pytest.raises(ValidationError, match="payload-light"):
        ClaudeStorageEvidence(
            evidenceId="storage-bad",
            sessionId="session-1",
            centralStore="event_log",
            storedKinds=("event_envelope",),
            runtimeLocalPayloadKinds=("transcript",),
            payloadLight=True,
            createdAt=NOW,
            metadata={"transcript": "full transcript should not be here"},
        )

def test_retention_evidence_requires_policy_controlled_complete_classes() -> None:
    evidence = ClaudeRetentionEvidence(
        retentionId="retention-1",
        sessionId="session-1",
        classes=_required_retention_classes(),
        policyRef="policy://retention/default",
        createdAt=NOW,
    )

    assert {item.class_name for item in evidence.classes} == {
        "hot_session_metadata",
        "hot_event_log",
        "usage_rollups",
        "audit_event_metadata",
        "checkpoint_payloads",
    }

    with pytest.raises(ValidationError, match="required retention classes"):
        ClaudeRetentionEvidence(
            retentionId="retention-missing",
            sessionId="session-1",
            classes=_required_retention_classes()[:-1],
            policyRef="policy://retention/default",
            createdAt=NOW,
        )

    with pytest.raises(ValidationError, match="missing required retention classes"):
        ClaudeRetentionEvidence(
            retentionId="retention-missing-detail",
            sessionId="session-1",
            classes=_required_retention_classes()[:-1],
            policyRef="policy://retention/default",
            createdAt=NOW,
        )

    with pytest.raises(ValidationError, match="policy-controlled"):
        ClaudeRetentionClass(
            className="hot_session_metadata",
            retentionValue="30d",
            policyControlled=False,
        )

def test_telemetry_evidence_accepts_supported_metric_and_span_names() -> None:
    assert "managed_sessions_active" in CLAUDE_TELEMETRY_METRIC_NAMES
    assert "session.bootstrap" in CLAUDE_TELEMETRY_SPAN_NAMES

    metric = ClaudeTelemetryMetric(
        metricName="managed_sessions_active",
        value=1,
        dimensions={"runtimeKind": "claude_code"},
    )
    span = ClaudeTelemetrySpan(
        spanName="session.bootstrap",
        durationMs=42,
        attributes={"provider": "anthropic_api"},
    )
    evidence = ClaudeTelemetryEvidence(
        telemetryId="telemetry-1",
        sessionId="session-1",
        metrics=(metric,),
        eventEnvelopes=(
            ClaudeEventEnvelope(
                eventId="event-session-active",
                eventFamily="session",
                eventName="session.active",
                sessionId="session-1",
                occurredAt=NOW,
            ),
        ),
        traceSpans=(span,),
        createdAt=NOW,
    )

    assert evidence.metrics[0].metric_name == "managed_sessions_active"
    assert evidence.trace_spans[0].span_name == "session.bootstrap"

    with pytest.raises(ValidationError):
        ClaudeTelemetryMetric(metricName="unknown_metric", value=1)

    with pytest.raises(ValidationError):
        ClaudeTelemetrySpan(spanName="unknown.span", durationMs=1)

def test_usage_rollup_rejects_double_counting_shapes() -> None:
    rollup = ClaudeUsageRollup(
        usageRollupId="usage-1",
        sessionId="session-1",
        sessionGroupId="group-1",
        userId="user-1",
        workspaceId="workspace-1",
        runtimeFamily="claude_code",
        providerMode="anthropic_api",
        tokenDirection="input",
        tokenCount=20,
        childContextId="child-1",
        includedInParentRollup=True,
        createdAt=NOW,
    )

    assert rollup.child_context_id == "child-1"
    assert rollup.included_in_parent_rollup is True

    with pytest.raises(ValidationError, match="cannot both be set"):
        ClaudeUsageRollup(
            usageRollupId="usage-bad",
            sessionId="session-1",
            userId="user-1",
            workspaceId="workspace-1",
            runtimeFamily="claude_code",
            providerMode="anthropic_api",
            tokenDirection="input",
            tokenCount=20,
            childContextId="child-1",
            teamMemberSessionId="session-team-1",
            createdAt=NOW,
        )

    with pytest.raises(ValidationError, match="parent rollup"):
        ClaudeUsageRollup(
            usageRollupId="usage-parent-bad",
            sessionId="session-1",
            userId="user-1",
            workspaceId="workspace-1",
            runtimeFamily="claude_code",
            providerMode="anthropic_api",
            tokenDirection="total",
            tokenCount=20,
            includedInParentRollup=True,
            createdAt=NOW,
        )

def test_governance_compliance_and_dashboard_evidence_are_bounded() -> None:
    governance = ClaudeGovernanceEvidence(
        governanceId="governance-1",
        sessionId="session-1",
        policyTrustLevel="endpoint_enforced",
        providerMode="anthropic_api",
        executionSecurityMode="remote_control_projection",
        controlLayers=("protected_paths", "hooks", "runtime_isolation"),
        protectedPathPolicy="protected paths require explicit operator approval",
        hookAudits=(_hook_audit(),),
        storageEvidenceRefs=("storage-1",),
        retentionEvidenceRefs=("retention-1",),
        telemetryEvidenceRefs=("telemetry-1",),
        usageRollupRefs=("usage-1",),
        createdAt=NOW,
    )

    compliance = ClaudeComplianceExportView(
        exportId="export-1",
        governance=governance,
        storageSummaryRefs=("storage-1",),
        retentionSummaryRefs=("retention-1",),
        telemetrySummaryRefs=("telemetry-1",),
        usageSummaryRefs=("usage-1",),
        createdAt=NOW,
    )
    dashboard = ClaudeProviderDashboardSummary(
        dashboardId="dashboard-1",
        providerMode="anthropic_api",
        policyTrustLevels=("endpoint_enforced",),
        executionSecurityModes=("remote_control_projection",),
        telemetryEvidenceRefs=("telemetry-1",),
        usageRollupRefs=("usage-1",),
        governanceEvidenceRefs=("governance-1",),
        createdAt=NOW,
    )

    assert compliance.governance.policy_trust_level == "endpoint_enforced"
    assert dashboard.provider_mode == "anthropic_api"

    with pytest.raises(ValidationError, match="protectedPathPolicy"):
        ClaudeGovernanceEvidence(
            governanceId="governance-bad",
            sessionId="session-1",
            policyTrustLevel="endpoint_enforced",
            providerMode="anthropic_api",
            executionSecurityMode="local_execution",
            controlLayers=("protected_paths",),
            createdAt=NOW,
        )
