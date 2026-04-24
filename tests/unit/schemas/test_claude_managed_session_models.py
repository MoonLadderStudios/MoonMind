"""Unit tests for Claude managed-session core contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.managed_session_models import (
    CLAUDE_DECISION_EVENT_NAMES,
    CLAUDE_DECISION_STAGE_ORDER,
    ClaudeDecisionPoint,
    ClaudeHookAudit,
    ClaudeManagedSession,
    ClaudeManagedTurn,
    ClaudeManagedWorkItem,
    ClaudeSurfaceBinding,
)

NOW = datetime(2026, 4, 16, tzinfo=UTC)

def _session_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sessionId": "claude-session-1",
        "executionOwner": "local_process",
        "state": "active",
        "primarySurface": "terminal",
        "projectionMode": "primary",
        "createdBy": "user",
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    payload.update(overrides)
    return payload

@pytest.mark.parametrize(
    ("execution_owner", "primary_surface", "projection_mode", "created_by"),
    [
        ("local_process", "terminal", "primary", "user"),
        ("anthropic_cloud_vm", "web", "primary", "user"),
        ("anthropic_cloud_vm", "scheduler", "primary", "schedule"),
        ("local_process", "scheduler", "primary", "schedule"),
        ("sdk_host", "sdk", "primary", "sdk"),
    ],
)
def test_claude_managed_session_validates_documented_core_shapes(
    execution_owner: str,
    primary_surface: str,
    projection_mode: str,
    created_by: str,
) -> None:
    session = ClaudeManagedSession(
        **_session_payload(
            executionOwner=execution_owner,
            primarySurface=primary_surface,
            projectionMode=projection_mode,
            createdBy=created_by,
        )
    )

    assert session.runtime_family == "claude_code"
    assert session.session_id == "claude-session-1"
    assert session.execution_owner == execution_owner
    assert session.primary_surface == primary_surface
    assert session.projection_mode == projection_mode
    assert session.created_by == created_by

def test_remote_projection_preserves_session_identity_and_execution_owner() -> None:
    session = ClaudeManagedSession(**_session_payload())
    updated_at = datetime(2026, 4, 16, 0, 1, tzinfo=UTC)

    projected = session.with_remote_projection(
        surface_id="surface-web-1",
        surface_kind="web",
        interactive=True,
        updated_at=updated_at,
    )

    assert projected.session_id == session.session_id
    assert projected.execution_owner == "local_process"
    assert projected.updated_at == updated_at
    assert projected.surface_bindings[-1].surface_id == "surface-web-1"
    assert projected.surface_bindings[-1].surface_kind == "web"
    assert projected.surface_bindings[-1].projection_mode == "remote_projection"
    assert session.surface_bindings == ()

def test_remote_projection_deep_copies_mutable_session_fields() -> None:
    session = ClaudeManagedSession(
        **_session_payload(extensions={"metadata": {"source": "local"}})
    )

    projected = session.with_remote_projection(
        surface_id="surface-web-1",
        surface_kind="web",
        interactive=True,
        updated_at=NOW,
    )
    projected.extensions["metadata"]["source"] = "remote"

    assert session.extensions == {"metadata": {"source": "local"}}
    assert projected.extensions == {"metadata": {"source": "remote"}}

def test_cloud_handoff_creates_distinct_cloud_session_with_lineage() -> None:
    source = ClaudeManagedSession(**_session_payload())

    destination = source.cloud_handoff(
        session_id="claude-cloud-1",
        primary_surface="web",
        created_by="user",
        created_at=NOW,
    )

    assert destination.session_id == "claude-cloud-1"
    assert destination.session_id != source.session_id
    assert destination.execution_owner == "anthropic_cloud_vm"
    assert destination.handoff_from_session_id == source.session_id
    assert source.execution_owner == "local_process"

def test_cloud_handoff_requires_distinct_destination_session() -> None:
    source = ClaudeManagedSession(**_session_payload())

    with pytest.raises(ValueError, match="distinct sessionId"):
        source.cloud_handoff(
            session_id=source.session_id,
            primary_surface="web",
            created_by="user",
            created_at=NOW,
        )

@pytest.mark.parametrize(
    ("model", "payload", "field", "bad_value"),
    [
        (ClaudeManagedSession, _session_payload(), "state", "paused"),
        (
            ClaudeManagedTurn,
            {
                "turnId": "turn-1",
                "sessionId": "claude-session-1",
                "inputOrigin": "human",
                "state": "submitted",
                "startedAt": NOW,
            },
            "state",
            "paused",
        ),
        (
            ClaudeManagedWorkItem,
            {
                "itemId": "item-1",
                "turnId": "turn-1",
                "sessionId": "claude-session-1",
                "kind": "tool_call",
                "status": "queued",
                "payload": {},
                "startedAt": NOW,
            },
            "status",
            "paused",
        ),
        (
            ClaudeSurfaceBinding,
            {
                "surfaceId": "surface-1",
                "surfaceKind": "terminal",
                "projectionMode": "primary",
                "connectionState": "connected",
                "interactive": True,
            },
            "connectionState",
            "paused",
        ),
    ],
)
def test_claude_lifecycle_fields_reject_undocumented_values(
    model: type,
    payload: dict[str, object],
    field: str,
    bad_value: str,
) -> None:
    payload[field] = bad_value

    with pytest.raises(ValidationError):
        model(**payload)

@pytest.mark.parametrize(
    "payload_key",
    ["threadId", "thread_id", "childThread", "child_thread"],
)
def test_claude_session_rejects_codex_thread_aliases(payload_key: str) -> None:
    payload = _session_payload()
    payload[payload_key] = "codex-thread-1"

    with pytest.raises(ValidationError):
        ClaudeManagedSession(**payload)

def test_claude_records_use_session_id_aliases_on_wire() -> None:
    turn = ClaudeManagedTurn(
        turnId="turn-1",
        sessionId="claude-session-1",
        inputOrigin="human",
        state="submitted",
        startedAt=NOW,
    )
    work_item = ClaudeManagedWorkItem(
        itemId="item-1",
        turnId="turn-1",
        sessionId="claude-session-1",
        kind="tool_call",
        status="queued",
        payload={"tool": "read"},
        startedAt=NOW,
    )

    assert turn.model_dump(by_alias=True)["sessionId"] == "claude-session-1"
    assert work_item.model_dump(by_alias=True)["sessionId"] == "claude-session-1"
    assert "threadId" not in turn.model_dump(by_alias=True)
    assert "childThread" not in work_item.model_dump(by_alias=True)

def test_claude_session_lifecycle_datetimes_are_utc_aware() -> None:
    naive_created = datetime(2026, 4, 16, 0, 0)
    naive_updated = datetime(2026, 4, 16, 0, 1)
    naive_ended = datetime(2026, 4, 16, 0, 2)

    session = ClaudeManagedSession(
        **_session_payload(
            createdAt=naive_created,
            updatedAt=naive_updated,
            endedAt=naive_ended,
        )
    )

    assert session.created_at == naive_created.replace(tzinfo=UTC)
    assert session.updated_at == naive_updated.replace(tzinfo=UTC)
    assert session.ended_at == naive_ended.replace(tzinfo=UTC)

def test_claude_turn_lifecycle_datetimes_are_utc_aware() -> None:
    naive_started = datetime(2026, 4, 16, 0, 0)
    naive_completed = datetime(2026, 4, 16, 0, 1)

    turn = ClaudeManagedTurn(
        turnId="turn-1",
        sessionId="claude-session-1",
        inputOrigin="human",
        state="completed",
        startedAt=naive_started,
        completedAt=naive_completed,
    )

    assert turn.started_at == naive_started.replace(tzinfo=UTC)
    assert turn.completed_at == naive_completed.replace(tzinfo=UTC)

def test_claude_work_item_lifecycle_datetimes_are_utc_aware() -> None:
    naive_started = datetime(2026, 4, 16, 0, 0)
    naive_ended = datetime(2026, 4, 16, 0, 1)

    work_item = ClaudeManagedWorkItem(
        itemId="item-1",
        turnId="turn-1",
        sessionId="claude-session-1",
        kind="tool_call",
        status="completed",
        payload={},
        startedAt=naive_started,
        endedAt=naive_ended,
    )

    assert work_item.started_at == naive_started.replace(tzinfo=UTC)
    assert work_item.ended_at == naive_ended.replace(tzinfo=UTC)

def test_claude_work_item_hook_event_names_require_hook_call_kind() -> None:
    hook_work_item = ClaudeManagedWorkItem(
        itemId="item-hook-1",
        turnId="turn-1",
        sessionId="claude-session-1",
        kind="hook_call",
        status="completed",
        eventName="work.hook.completed",
        payload={},
        startedAt=NOW,
    )

    assert hook_work_item.event_name == "work.hook.completed"

    with pytest.raises(ValueError, match="Hook event names require hook_call"):
        ClaudeManagedWorkItem(
            itemId="item-tool-1",
            turnId="turn-1",
            sessionId="claude-session-1",
            kind="tool_call",
            status="completed",
            eventName="work.hook.completed",
            payload={},
            startedAt=NOW,
        )

def _decision_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "decisionId": "decision-1",
        "sessionId": "claude-session-1",
        "turnId": "turn-1",
        "proposalKind": "tool",
        "originStage": "permission_rules",
        "outcome": "allowed",
        "provenanceSource": "policy",
        "eventName": "decision.allowed",
        "metadata": {"ruleId": "allow-read"},
        "createdAt": NOW,
    }
    payload.update(overrides)
    return payload

def test_claude_decision_stage_order_matches_documented_pipeline() -> None:
    assert CLAUDE_DECISION_STAGE_ORDER == (
        "session_state_guard",
        "pretool_hooks",
        "permission_rules",
        "protected_path_guard",
        "permission_mode_baseline",
        "sandbox_substitution",
        "auto_mode_classifier",
        "interactive_prompt_or_headless_resolution",
        "runtime_execution",
        "posttool_hooks",
        "checkpoint_capture",
    )

    decisions = [
        ClaudeDecisionPoint(
            **_decision_payload(
                decisionId=f"decision-{index}",
                originStage=stage,
                outcome="denied" if stage == "protected_path_guard" else "resolved",
                provenanceSource="runtime"
                if stage == "runtime_execution"
                else "protected_path"
                if stage == "protected_path_guard"
                else "policy",
                eventName="decision.denied"
                if stage == "protected_path_guard"
                else "decision.resolved",
            )
        )
        for index, stage in enumerate(CLAUDE_DECISION_STAGE_ORDER, start=1)
    ]

    assert tuple(decision.origin_stage for decision in decisions) == (
        CLAUDE_DECISION_STAGE_ORDER
    )

def test_claude_decision_event_names_match_documented_events() -> None:
    assert CLAUDE_DECISION_EVENT_NAMES == (
        "decision.proposed",
        "decision.mutated",
        "decision.allowed",
        "decision.asked",
        "decision.denied",
        "decision.deferred",
        "decision.canceled",
        "decision.resolved",
    )

    for event_name in CLAUDE_DECISION_EVENT_NAMES:
        decision = ClaudeDecisionPoint(
            **_decision_payload(
                decisionId=f"{event_name.replace('.', '-')}-1",
                eventName=event_name,
                outcome=event_name.removeprefix("decision.")
                if event_name != "decision.asked"
                else "asked",
            )
        )
        assert decision.event_name == event_name

def test_claude_decision_point_uses_canonical_wire_shape() -> None:
    decision = ClaudeDecisionPoint(**_decision_payload(workItemId="item-1"))

    wire = decision.model_dump(by_alias=True)

    assert wire["decisionId"] == "decision-1"
    assert wire["sessionId"] == "claude-session-1"
    assert wire["turnId"] == "turn-1"
    assert wire["workItemId"] == "item-1"
    assert wire["originStage"] == "permission_rules"
    assert "threadId" not in wire
    assert "childThread" not in wire

@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("originStage", "approval_prompt"),
        ("eventName", "decision.approved"),
        ("proposalKind", "database"),
        ("provenanceSource", "unknown"),
        ("outcome", "approved"),
    ],
)
def test_claude_decision_point_rejects_unknown_vocabulary(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        ClaudeDecisionPoint(**_decision_payload(**{field: value}))

def test_policy_decision_records_first_match_provenance() -> None:
    decision = ClaudeDecisionPoint(
        **_decision_payload(
            decisionId="decision-policy-first-match",
            originStage="permission_rules",
            outcome="asked",
            provenanceSource="policy",
            eventName="decision.asked",
            metadata={
                "rulePrecedence": ("deny", "ask", "allow"),
                "winningRuleId": "ask-sensitive-network",
                "firstMatch": True,
            },
        )
    )

    assert decision.provenance_source == "policy"
    assert decision.metadata["rulePrecedence"] == ("deny", "ask", "allow")
    assert decision.metadata["winningRuleId"] == "ask-sensitive-network"
    assert decision.metadata["firstMatch"] is True

def test_protected_path_decision_cannot_be_auto_allowed() -> None:
    decision = ClaudeDecisionPoint.protected_path(
        decision_id="decision-protected",
        session_id="claude-session-1",
        turn_id="turn-1",
        proposal_kind="file",
        outcome="denied",
        metadata={"path": "secrets.env"},
        created_at=NOW,
    )

    assert decision.origin_stage == "protected_path_guard"
    assert decision.provenance_source == "protected_path"
    assert decision.event_name == "decision.denied"

    with pytest.raises(ValueError, match="Protected path decisions"):
        ClaudeDecisionPoint.protected_path(
            decision_id="decision-protected-allow",
            session_id="claude-session-1",
            turn_id="turn-1",
            proposal_kind="file",
            outcome="allowed",
            created_at=NOW,
        )

def test_protected_path_guard_requires_protected_path_provenance() -> None:
    with pytest.raises(ValueError, match="protected_path provenance"):
        ClaudeDecisionPoint(
            **_decision_payload(
                decisionId="decision-protected-policy",
                proposalKind="file",
                originStage="protected_path_guard",
                outcome="denied",
                provenanceSource="policy",
                eventName="decision.denied",
            )
        )

def test_sandbox_substitution_is_distinct_from_explicit_allow_rule() -> None:
    sandboxed = ClaudeDecisionPoint(
        **_decision_payload(
            decisionId="decision-sandbox",
            originStage="sandbox_substitution",
            outcome="resolved",
            provenanceSource="sandbox",
            eventName="decision.resolved",
            metadata={"sandboxProfile": "read-only-bash"},
        )
    )
    explicit_allow = ClaudeDecisionPoint(
        **_decision_payload(
            decisionId="decision-allow-rule",
            originStage="permission_rules",
            outcome="allowed",
            provenanceSource="policy",
            eventName="decision.allowed",
            metadata={"winningRuleId": "allow-read"},
        )
    )

    assert sandboxed.provenance_source == "sandbox"
    assert sandboxed.origin_stage == "sandbox_substitution"
    assert explicit_allow.provenance_source == "policy"
    assert explicit_allow.origin_stage == "permission_rules"
    assert sandboxed.model_dump(by_alias=True) != explicit_allow.model_dump(
        by_alias=True
    )

def test_classifier_decision_is_distinct_from_user_and_policy_outcomes() -> None:
    decision = ClaudeDecisionPoint.classifier(
        decision_id="decision-classifier",
        session_id="claude-session-1",
        turn_id="turn-1",
        proposal_kind="tool",
        outcome="asked",
        metadata={"classifier": "auto-mode"},
        created_at=NOW,
    )

    assert decision.origin_stage == "auto_mode_classifier"
    assert decision.provenance_source == "classifier"
    assert decision.provenance_source not in {"user", "policy"}
    assert decision.event_name == "decision.asked"

def test_headless_resolution_accepts_only_deny_or_defer() -> None:
    denied = ClaudeDecisionPoint.headless_resolution(
        decision_id="decision-headless-deny",
        session_id="claude-session-1",
        turn_id="turn-1",
        proposal_kind="network",
        outcome="denied",
        created_at=NOW,
    )
    deferred = ClaudeDecisionPoint.headless_resolution(
        decision_id="decision-headless-defer",
        session_id="claude-session-1",
        turn_id="turn-1",
        proposal_kind="network",
        outcome="deferred",
        created_at=NOW,
    )

    assert denied.event_name == "decision.denied"
    assert deferred.event_name == "decision.deferred"

    with pytest.raises(ValueError, match="Headless decisions"):
        ClaudeDecisionPoint.headless_resolution(
            decision_id="decision-headless-allow",
            session_id="claude-session-1",
            turn_id="turn-1",
            proposal_kind="network",
            outcome="allowed",
            created_at=NOW,
        )

def test_hook_tightened_decision_records_hook_provenance() -> None:
    decision = ClaudeDecisionPoint.hook_tightened(
        decision_id="decision-hook",
        session_id="claude-session-1",
        turn_id="turn-1",
        proposal_kind="tool",
        origin_stage="pretool_hooks",
        outcome="asked",
        metadata={"hookName": "review-sensitive-command"},
        created_at=NOW,
    )

    assert decision.provenance_source == "hook"
    assert decision.metadata["hookTightened"] is True
    assert decision.event_name == "decision.asked"

    with pytest.raises(ValueError, match="Hook decisions"):
        ClaudeDecisionPoint.hook_tightened(
            decision_id="decision-hook-invalid",
            session_id="claude-session-1",
            turn_id="turn-1",
            proposal_kind="tool",
            origin_stage="permission_rules",
            outcome="asked",
            created_at=NOW,
        )

def test_claude_hook_audit_validates_documented_fields() -> None:
    audit = ClaudeHookAudit(
        auditId="audit-1",
        sessionId="claude-session-1",
        turnId="turn-1",
        decisionId="decision-hook",
        hookName="review-sensitive-command",
        sourceScope="project",
        eventType="PreToolUse",
        matcher="Bash(*)",
        outcome="ask",
        auditData={"reason": "sensitive command"},
        createdAt=NOW,
    )

    wire = audit.model_dump(by_alias=True)

    assert wire["auditId"] == "audit-1"
    assert wire["sourceScope"] == "project"
    assert wire["eventType"] == "PreToolUse"
    assert wire["matcher"] == "Bash(*)"
    assert wire["outcome"] == "ask"
    assert wire["auditData"] == {"reason": "sensitive command"}

@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sourceScope", "workspace"),
        ("outcome", "approved"),
        ("hookName", "   "),
        ("eventType", "   "),
        ("matcher", "   "),
    ],
)
def test_claude_hook_audit_rejects_invalid_values(field: str, value: str) -> None:
    payload: dict[str, object] = {
        "auditId": "audit-1",
        "sessionId": "claude-session-1",
        "turnId": "turn-1",
        "hookName": "review-sensitive-command",
        "sourceScope": "project",
        "eventType": "PreToolUse",
        "matcher": "Bash(*)",
        "outcome": "ask",
        "auditData": {},
        "createdAt": NOW,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        ClaudeHookAudit(**payload)
