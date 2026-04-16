"""Integration-style boundary tests for Claude decision provenance."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas.managed_session_models import (
    CLAUDE_DECISION_STAGE_ORDER,
    ClaudeDecisionPoint,
    ClaudeHookAudit,
    ClaudeManagedWorkItem,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


NOW = datetime(2026, 4, 16, tzinfo=UTC)


def test_claude_decision_boundary_preserves_stage_order_and_hook_audit() -> None:
    decisions = [
        ClaudeDecisionPoint(
            decisionId=f"decision-{index}",
            sessionId="claude-session-1",
            turnId="turn-1",
            proposalKind="tool",
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
            metadata={"stageIndex": index},
            createdAt=NOW,
        )
        for index, stage in enumerate(CLAUDE_DECISION_STAGE_ORDER, start=1)
    ]
    protected = ClaudeDecisionPoint.protected_path(
        decision_id="decision-protected",
        session_id="claude-session-1",
        turn_id="turn-1",
        proposal_kind="file",
        outcome="denied",
        created_at=NOW,
    )
    classifier = ClaudeDecisionPoint.classifier(
        decision_id="decision-classifier",
        session_id="claude-session-1",
        turn_id="turn-1",
        proposal_kind="tool",
        outcome="asked",
        created_at=NOW,
    )
    hook = ClaudeHookAudit(
        auditId="audit-1",
        sessionId="claude-session-1",
        turnId="turn-1",
        decisionId="decision-2",
        hookName="review-sensitive-command",
        sourceScope="project",
        eventType="PreToolUse",
        matcher="Bash(*)",
        outcome="ask",
        auditData={"reason": "requires review"},
        createdAt=NOW,
    )
    hook_work = ClaudeManagedWorkItem(
        itemId="work-hook-1",
        sessionId="claude-session-1",
        turnId="turn-1",
        kind="hook_call",
        status="completed",
        eventName="work.hook.completed",
        payload={"auditId": hook.audit_id},
        startedAt=NOW,
        endedAt=NOW,
    )

    assert tuple(decision.origin_stage for decision in decisions) == (
        CLAUDE_DECISION_STAGE_ORDER
    )
    assert protected.provenance_source == "protected_path"
    assert protected.outcome == "denied"
    assert classifier.provenance_source == "classifier"
    assert classifier.provenance_source not in {"user", "policy"}
    assert hook.source_scope == "project"
    assert hook.outcome == "ask"
    assert hook_work.event_name == "work.hook.completed"

    for record in (*decisions, protected, classifier, hook, hook_work):
        wire = record.model_dump(by_alias=True)
        assert "threadId" not in wire
        assert "childThread" not in wire
