"""Unit tests for Claude child-work contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas import (
    CLAUDE_CHILD_WORK_EVENT_NAMES,
    ClaudeChildContext,
    ClaudeChildWorkEvent,
    ClaudeChildWorkUsage,
    ClaudeSessionGroup,
    ClaudeTeamMemberSession,
    ClaudeTeamMessage,
    validate_claude_team_message_membership,
)

NOW = datetime(2026, 4, 16, tzinfo=UTC)

def test_subagent_child_context_is_parent_owned_not_peer_session() -> None:
    usage = ClaudeChildWorkUsage(
        inputTokens=1000,
        outputTokens=400,
        totalTokens=1400,
        metadata={"rollup": "parent"},
    )

    child = ClaudeChildContext(
        childContextId="child-subagent-1",
        parentSessionId="claude-session-parent",
        parentTurnId="turn-1",
        profile="researcher",
        returnShape="summary_plus_metadata",
        status="completed",
        usage=usage,
        startedAt=NOW,
        completedAt=NOW,
        metadata={"background": True},
    )

    assert child.context_window == "isolated"
    assert child.communication == "caller_only"
    assert child.lifecycle_owner == "parent_turn"
    assert child.usage is not None
    assert child.usage.total_tokens == 1400
    wire = child.model_dump(by_alias=True)
    assert "sessionId" not in wire
    assert wire["childContextId"] == "child-subagent-1"

def test_subagent_rejects_peer_session_collapse_and_promotion_metadata() -> None:
    with pytest.raises(ValidationError):
        ClaudeChildContext(
            childContextId="child-subagent-1",
            parentSessionId="claude-session-parent",
            parentTurnId="turn-1",
            profile="researcher",
            returnShape="summary",
            status="running",
            startedAt=NOW,
            sessionId="claude-peer-session",
        )

    with pytest.raises(ValidationError, match="promotion"):
        ClaudeChildContext(
            childContextId="child-subagent-1",
            parentSessionId="claude-session-parent",
            parentTurnId="turn-1",
            profile="researcher",
            returnShape="summary",
            status="running",
            startedAt=NOW,
            metadata={"promotedSessionId": "claude-peer-session"},
        )

def test_child_work_usage_rejects_underreported_total_tokens() -> None:
    with pytest.raises(ValidationError, match="totalTokens"):
        ClaudeChildWorkUsage(
            inputTokens=100,
            outputTokens=50,
            totalTokens=149,
        )

def test_team_group_members_and_message_keep_distinct_session_identities() -> None:
    group = ClaudeSessionGroup(
        sessionGroupId="team-group-1",
        leadSessionId="claude-session-lead",
        status="completed",
        usage=ClaudeChildWorkUsage(
            inputTokens=1200,
            outputTokens=800,
            totalTokens=2000,
        ),
        createdAt=NOW,
        completedAt=NOW,
    )
    lead = ClaudeTeamMemberSession(
        sessionId="claude-session-lead",
        sessionGroupId=group.session_group_id,
        role="lead",
        status="completed",
    )
    teammate = ClaudeTeamMemberSession(
        sessionId="claude-session-teammate",
        sessionGroupId=group.session_group_id,
        role="teammate",
        status="completed",
    )
    message = ClaudeTeamMessage(
        messageId="message-1",
        sessionGroupId=group.session_group_id,
        senderSessionId=lead.session_id,
        peerSessionId=teammate.session_id,
        sentAt=NOW,
        metadata={"messageRef": "artifact://messages/message-1"},
    )

    assert lead.session_id != teammate.session_id
    assert lead.session_group_id == teammate.session_group_id
    assert group.usage is not None
    assert group.usage.total_tokens == 2000
    validate_claude_team_message_membership(
        message=message,
        members=(lead, teammate),
    )

def test_team_message_rejects_self_and_cross_group_messages() -> None:
    with pytest.raises(ValidationError, match="peerSessionId"):
        ClaudeTeamMessage(
            messageId="message-self",
            sessionGroupId="team-group-1",
            senderSessionId="claude-session-lead",
            peerSessionId="claude-session-lead",
            sentAt=NOW,
        )

    lead = ClaudeTeamMemberSession(
        sessionId="claude-session-lead",
        sessionGroupId="team-group-1",
        role="lead",
        status="running",
    )
    outsider = ClaudeTeamMemberSession(
        sessionId="claude-session-outsider",
        sessionGroupId="team-group-2",
        role="teammate",
        status="running",
    )
    message = ClaudeTeamMessage(
        messageId="message-cross-group",
        sessionGroupId="team-group-1",
        senderSessionId=lead.session_id,
        peerSessionId=outsider.session_id,
        sentAt=NOW,
    )

    with pytest.raises(ValueError, match="same session group"):
        validate_claude_team_message_membership(
            message=message,
            members=(lead, outsider),
        )

@pytest.mark.parametrize(
    "event_name",
    [
        "child.subagent.started",
        "child.subagent.completed",
        "team.group.created",
        "team.member.started",
        "team.message.sent",
        "team.member.completed",
        "team.group.completed",
    ],
)
def test_child_work_event_names_are_documented_and_valid(event_name: str) -> None:
    assert event_name in CLAUDE_CHILD_WORK_EVENT_NAMES

    event = ClaudeChildWorkEvent(
        eventId=f"event-{event_name}",
        sessionId="claude-session-parent",
        turnId="turn-1",
        childContextId="child-subagent-1"
        if event_name.startswith("child.")
        else None,
        sessionGroupId="team-group-1" if event_name.startswith("team.") else None,
        peerSessionId="claude-session-teammate"
        if event_name == "team.message.sent"
        else None,
        eventName=event_name,
        occurredAt=NOW,
    )

    assert event.event_name == event_name

def test_child_work_event_requires_shape_specific_identifiers() -> None:
    with pytest.raises(ValidationError, match="childContextId"):
        ClaudeChildWorkEvent(
            eventId="event-child-started",
            sessionId="claude-session-parent",
            eventName="child.subagent.started",
            occurredAt=NOW,
        )

    with pytest.raises(ValidationError, match="turnId"):
        ClaudeChildWorkEvent(
            eventId="event-child-started",
            sessionId="claude-session-parent",
            childContextId="child-subagent-1",
            eventName="child.subagent.started",
            occurredAt=NOW,
        )

    with pytest.raises(ValidationError, match="sessionGroupId"):
        ClaudeChildWorkEvent(
            eventId="event-team-started",
            sessionId="claude-session-lead",
            eventName="team.member.started",
            occurredAt=NOW,
        )

    with pytest.raises(ValidationError, match="peerSessionId"):
        ClaudeChildWorkEvent(
            eventId="event-team-message",
            sessionId="claude-session-lead",
            sessionGroupId="team-group-1",
            eventName="team.message.sent",
            occurredAt=NOW,
        )

def test_child_work_rejects_large_payload_metadata() -> None:
    with pytest.raises(ValidationError):
        ClaudeChildWorkEvent(
            eventId="event-large",
            sessionId="claude-session-parent",
            childContextId="child-subagent-1",
            eventName="child.subagent.completed",
            occurredAt=NOW,
            metadata={"content": "x" * 100_000},
        )
