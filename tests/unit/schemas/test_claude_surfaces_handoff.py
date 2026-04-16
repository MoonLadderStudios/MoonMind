"""Unit tests for Claude surface projection and handoff contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas import (
    CLAUDE_SURFACE_LIFECYCLE_EVENT_NAMES,
    ClaudeManagedSession,
    ClaudeSurfaceBinding,
    ClaudeSurfaceLifecycleEvent,
    classify_claude_execution_security_mode,
)


NOW = datetime(2026, 4, 16, tzinfo=UTC)


def _session(**overrides: object) -> ClaudeManagedSession:
    payload: dict[str, object] = {
        "sessionId": "claude-session-local",
        "executionOwner": "local_process",
        "state": "active",
        "primarySurface": "terminal",
        "projectionMode": "primary",
        "createdBy": "user",
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    payload.update(overrides)
    return ClaudeManagedSession(**payload)


def test_session_allows_one_primary_surface_and_multiple_projections() -> None:
    session = _session().with_surface_binding(
        surface_id="surface-terminal",
        surface_kind="terminal",
        projection_mode="primary",
        interactive=True,
        updated_at=NOW,
        capabilities=("approvals", "diff_review"),
    )

    projected = session.with_remote_projection(
        surface_id="surface-web",
        surface_kind="web",
        interactive=True,
        updated_at=NOW,
    ).with_remote_projection(
        surface_id="surface-mobile",
        surface_kind="mobile",
        interactive=True,
        updated_at=NOW,
    )

    assert projected.session_id == session.session_id
    assert projected.execution_owner == "local_process"
    assert (
        [b.projection_mode for b in projected.surface_bindings].count("primary") == 1
    )
    assert [b.projection_mode for b in projected.surface_bindings].count(
        "remote_projection"
    ) == 2
    assert projected.surface_bindings[0].capabilities == ("approvals", "diff_review")

    with pytest.raises(ValueError, match="one primary surface"):
        projected.with_surface_binding(
            surface_id="surface-desktop",
            surface_kind="desktop",
            projection_mode="primary",
            interactive=True,
            updated_at=NOW,
        )


def test_surface_connection_updates_do_not_fail_session() -> None:
    session = _session().with_remote_projection(
        surface_id="surface-web",
        surface_kind="web",
        interactive=True,
        updated_at=NOW,
    )

    disconnected = session.with_surface_connection_state(
        surface_id="surface-web",
        connection_state="disconnected",
        updated_at=NOW,
    )
    reconnecting = disconnected.with_surface_connection_state(
        surface_id="surface-web",
        connection_state="reconnecting",
        updated_at=NOW,
    )
    detached = reconnecting.with_surface_connection_state(
        surface_id="surface-web",
        connection_state="detached",
        updated_at=NOW,
    )

    assert disconnected.state == "active"
    assert reconnecting.state == "active"
    assert detached.state == "active"
    assert detached.surface_bindings[-1].connection_state == "detached"

    with pytest.raises(ValueError, match="surfaceId"):
        detached.with_surface_connection_state(
            surface_id="unknown-surface",
            connection_state="connected",
            updated_at=NOW,
        )


def test_resume_on_different_surface_preserves_identity_without_handoff() -> None:
    session = _session().with_surface_binding(
        surface_id="surface-terminal",
        surface_kind="terminal",
        projection_mode="primary",
        interactive=True,
        updated_at=NOW,
    )

    resumed = session.resume_on_surface(
        surface_id="surface-desktop",
        surface_kind="desktop",
        interactive=True,
        updated_at=NOW,
    )

    assert resumed.session_id == session.session_id
    assert resumed.execution_owner == session.execution_owner
    assert resumed.primary_surface == "desktop"
    assert resumed.handoff_from_session_id is None
    assert (
        [b.projection_mode for b in resumed.surface_bindings].count("primary") == 1
    )
    assert resumed.surface_bindings[-1].surface_id == "surface-desktop"


def test_cloud_handoff_carries_bounded_seed_refs_and_security_mode() -> None:
    source = _session()

    destination = source.cloud_handoff(
        session_id="claude-session-cloud",
        primary_surface="web",
        created_by="user",
        created_at=NOW,
        seed_artifact_refs=(
            "artifact://handoff/summary-v1",
            "artifact://handoff/diff-v1",
        ),
    )

    assert destination.session_id != source.session_id
    assert destination.execution_owner == "anthropic_cloud_vm"
    assert destination.projection_mode == "handoff"
    assert destination.handoff_from_session_id == source.session_id
    assert destination.handoff_seed_artifact_refs == (
        "artifact://handoff/summary-v1",
        "artifact://handoff/diff-v1",
    )
    assert classify_claude_execution_security_mode(source) == "local_execution"
    assert (
        classify_claude_execution_security_mode(
            source.with_remote_projection(
                surface_id="surface-web",
                surface_kind="web",
                interactive=True,
                updated_at=NOW,
            )
        )
        == "remote_control_projection"
    )
    assert classify_claude_execution_security_mode(destination) == "cloud_execution"

    with pytest.raises(ValidationError):
        source.cloud_handoff(
            session_id="claude-session-cloud-2",
            primary_surface="web",
            created_by="user",
            created_at=NOW,
            seed_artifact_refs=("   ",),
        )


def test_surface_binding_rejects_unsupported_values() -> None:
    with pytest.raises(ValidationError):
        ClaudeSurfaceBinding(
            surfaceId="surface-web",
            surfaceKind="web",
            projectionMode="handoff",
            connectionState="connected",
            interactive=True,
        )


@pytest.mark.parametrize(
    "event_name",
    [
        "surface.attached",
        "surface.connected",
        "surface.disconnected",
        "surface.reconnecting",
        "surface.detached",
        "surface.resumed",
        "surface.handoff.created",
    ],
)
def test_surface_lifecycle_event_names_are_documented(event_name: str) -> None:
    assert event_name in CLAUDE_SURFACE_LIFECYCLE_EVENT_NAMES


def test_surface_lifecycle_event_validates_surface_and_handoff_shapes() -> None:
    attached = ClaudeSurfaceLifecycleEvent(
        eventId="event-surface-attached",
        sessionId="claude-session-local",
        surfaceId="surface-web",
        eventName="surface.attached",
        occurredAt=NOW,
        metadata={"projectionMode": "remote_projection"},
    )
    handoff = ClaudeSurfaceLifecycleEvent(
        eventId="event-handoff",
        sessionId="claude-session-cloud",
        eventName="surface.handoff.created",
        sourceSessionId="claude-session-local",
        destinationSessionId="claude-session-cloud",
        handoffSeedArtifactRefs=("artifact://handoff/summary-v1",),
        occurredAt=NOW,
    )

    assert attached.surface_id == "surface-web"
    assert handoff.source_session_id == "claude-session-local"
    assert handoff.destination_session_id == "claude-session-cloud"

    with pytest.raises(ValidationError, match="surfaceId"):
        ClaudeSurfaceLifecycleEvent(
            eventId="event-missing-surface",
            sessionId="claude-session-local",
            eventName="surface.disconnected",
            occurredAt=NOW,
        )

    with pytest.raises(ValidationError, match="sourceSessionId"):
        ClaudeSurfaceLifecycleEvent(
            eventId="event-bad-handoff",
            sessionId="claude-session-cloud",
            eventName="surface.handoff.created",
            destinationSessionId="claude-session-cloud",
            occurredAt=NOW,
        )

    with pytest.raises(ValidationError):
        ClaudeSurfaceLifecycleEvent(
            eventId="event-large",
            sessionId="claude-session-cloud",
            eventName="surface.handoff.created",
            sourceSessionId="claude-session-local",
            destinationSessionId="claude-session-cloud",
            occurredAt=NOW,
            metadata={"summary": "x" * 100_000},
        )
