"""Integration-style boundary tests for Claude managed-session core shapes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas.managed_session_models import ClaudeManagedSession

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


NOW = datetime(2026, 4, 16, tzinfo=UTC)


def _session(**overrides: object) -> ClaudeManagedSession:
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
    return ClaudeManagedSession(**payload)


def test_claude_session_boundary_supports_documented_story_shapes() -> None:
    local = _session(sessionId="local-1", primarySurface="terminal")
    remote = local.with_remote_projection(
        surface_id="remote-web-1",
        surface_kind="web",
        interactive=True,
        updated_at=NOW,
    )
    cloud = _session(
        sessionId="cloud-1",
        executionOwner="anthropic_cloud_vm",
        primarySurface="web",
    )
    cloud_scheduled = _session(
        sessionId="cloud-schedule-1",
        executionOwner="anthropic_cloud_vm",
        primarySurface="scheduler",
        createdBy="schedule",
    )
    desktop_scheduled = _session(
        sessionId="desktop-schedule-1",
        executionOwner="local_process",
        primarySurface="scheduler",
        createdBy="schedule",
    )
    sdk = _session(
        sessionId="sdk-1",
        executionOwner="sdk_host",
        primarySurface="sdk",
        createdBy="sdk",
    )
    handoff = local.cloud_handoff(
        session_id="cloud-handoff-1",
        primary_surface="web",
        created_by="user",
        created_at=NOW,
    )

    assert local.runtime_family == "claude_code"
    assert remote.execution_owner == local.execution_owner
    assert remote.surface_bindings[-1].projection_mode == "remote_projection"
    assert cloud.execution_owner == "anthropic_cloud_vm"
    assert cloud_scheduled.created_by == "schedule"
    assert desktop_scheduled.execution_owner == "local_process"
    assert sdk.execution_owner == "sdk_host"
    assert handoff.session_id != local.session_id
    assert handoff.handoff_from_session_id == local.session_id

    for session in (
        local,
        remote,
        cloud,
        cloud_scheduled,
        desktop_scheduled,
        sdk,
        handoff,
    ):
        wire = session.model_dump(by_alias=True)
        assert wire["sessionId"] == session.session_id
        assert "threadId" not in wire
        assert "childThread" not in wire
