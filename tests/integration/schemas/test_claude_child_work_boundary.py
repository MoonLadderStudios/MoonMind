"""Integration-style boundary tests for Claude child work."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas import build_claude_child_work_fixture_flow

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


NOW = datetime(2026, 4, 16, tzinfo=UTC)


def test_claude_child_work_boundary_keeps_subagents_and_teams_distinct() -> None:
    flow = build_claude_child_work_fixture_flow(
        parent_session_id="claude-session-parent",
        parent_turn_id="turn-1",
        child_context_id="child-subagent-1",
        session_group_id="team-group-1",
        lead_session_id="claude-session-lead",
        teammate_session_id="claude-session-teammate",
        message_id="message-1",
        created_at=NOW,
    )

    assert flow.parent_session.session_id == "claude-session-parent"
    assert flow.child_context.child_context_id == "child-subagent-1"
    assert flow.child_context.parent_session_id == flow.parent_session.session_id
    assert flow.child_context.parent_turn_id == "turn-1"
    assert flow.child_context.context_window == "isolated"
    assert flow.child_context.communication == "caller_only"
    assert flow.child_context.lifecycle_owner == "parent_turn"
    assert flow.child_context.status == "completed"
    assert flow.child_context.usage is not None
    assert flow.child_context.usage.metadata["rollupTarget"] == "parent_session"

    member_ids = {member.session_id for member in flow.team_members}
    assert flow.session_group.session_group_id == "team-group-1"
    assert flow.session_group.lead_session_id == "claude-session-lead"
    assert member_ids == {"claude-session-lead", "claude-session-teammate"}
    assert flow.child_context.child_context_id not in member_ids
    assert all(
        member.session_group_id == flow.session_group.session_group_id
        for member in flow.team_members
    )

    assert flow.team_message.sender_session_id == "claude-session-lead"
    assert flow.team_message.peer_session_id == "claude-session-teammate"
    assert flow.team_message.session_group_id == flow.session_group.session_group_id

    assert flow.session_group.usage is not None
    assert flow.session_group.usage.metadata["rollupTarget"] == "session_group"
    assert all(member.usage is not None for member in flow.team_members)

    assert tuple(event.event_name for event in flow.events) == (
        "child.subagent.started",
        "child.subagent.completed",
        "team.group.created",
        "team.member.started",
        "team.member.started",
        "team.message.sent",
        "team.member.completed",
        "team.member.completed",
        "team.group.completed",
    )
    assert all(
        event.child_context_id == flow.child_context.child_context_id
        for event in flow.events
        if event.event_name.startswith("child.")
    )
    assert all(
        event.session_group_id == flow.session_group.session_group_id
        for event in flow.events
        if event.event_name.startswith("team.")
    )
    assert [
        event.peer_session_id
        for event in flow.events
        if event.event_name == "team.message.sent"
    ] == ["claude-session-teammate"]

    for record in (
        flow.parent_session,
        flow.child_context,
        flow.session_group,
        flow.team_message,
        *flow.team_members,
        *flow.events,
    ):
        wire = record.model_dump(by_alias=True)
        assert "promotedSessionId" not in wire
        assert "threadId" not in wire
