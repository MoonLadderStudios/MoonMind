"""Integration-style boundary tests for Claude surfaces and handoff."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas import (
    build_claude_surface_handoff_fixture_flow,
    classify_claude_execution_security_mode,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


NOW = datetime(2026, 4, 16, tzinfo=UTC)


def test_claude_surface_handoff_boundary_preserves_execution_semantics() -> None:
    flow = build_claude_surface_handoff_fixture_flow(
        source_session_id="claude-session-local",
        terminal_surface_id="surface-terminal",
        web_surface_id="surface-web",
        resumed_surface_id="surface-desktop",
        cloud_session_id="claude-session-cloud",
        created_at=NOW,
        seed_artifact_refs=(
            "artifact://handoff/summary-v1",
            "artifact://handoff/diff-v1",
        ),
    )

    assert flow.source_session.session_id == "claude-session-local"
    assert flow.projected_session.session_id == flow.source_session.session_id
    assert flow.projected_session.execution_owner == "local_process"
    assert flow.disconnected_session.state != "failed"
    assert flow.reconnected_session.state != "failed"
    assert flow.resumed_session.session_id == flow.source_session.session_id
    assert flow.resumed_session.primary_surface == "desktop"
    assert flow.resumed_session.handoff_from_session_id is None

    assert flow.cloud_session.session_id == "claude-session-cloud"
    assert flow.cloud_session.session_id != flow.source_session.session_id
    assert flow.cloud_session.execution_owner == "anthropic_cloud_vm"
    assert flow.cloud_session.projection_mode == "handoff"
    assert flow.cloud_session.handoff_from_session_id == flow.source_session.session_id
    assert flow.cloud_session.handoff_seed_artifact_refs == (
        "artifact://handoff/summary-v1",
        "artifact://handoff/diff-v1",
    )

    assert (
        classify_claude_execution_security_mode(flow.source_session)
        == "local_execution"
    )
    assert (
        classify_claude_execution_security_mode(flow.projected_session)
        == "remote_control_projection"
    )
    assert (
        classify_claude_execution_security_mode(flow.cloud_session)
        == "cloud_execution"
    )

    assert tuple(event.event_name for event in flow.events) == (
        "surface.attached",
        "surface.attached",
        "surface.disconnected",
        "surface.reconnecting",
        "surface.connected",
        "surface.resumed",
        "surface.handoff.created",
    )
    assert [
        event.surface_id
        for event in flow.events
        if event.event_name.startswith("surface.")
        and event.event_name != "surface.handoff.created"
    ] == [
        "surface-terminal",
        "surface-web",
        "surface-web",
        "surface-web",
        "surface-web",
        "surface-desktop",
    ]
    handoff_events = [
        event
        for event in flow.events
        if event.event_name == "surface.handoff.created"
    ]
    assert len(handoff_events) == 1
    assert handoff_events[0].source_session_id == flow.source_session.session_id
    assert handoff_events[0].destination_session_id == flow.cloud_session.session_id
    assert handoff_events[0].handoff_seed_artifact_refs == (
        "artifact://handoff/summary-v1",
        "artifact://handoff/diff-v1",
    )

    for record in (
        flow.source_session,
        flow.projected_session,
        flow.disconnected_session,
        flow.reconnected_session,
        flow.resumed_session,
        flow.cloud_session,
        *flow.events,
    ):
        wire = record.model_dump(by_alias=True)
        assert "threadId" not in wire
        assert "summary" not in wire
