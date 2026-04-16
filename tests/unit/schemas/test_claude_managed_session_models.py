"""Unit tests for Claude managed-session core contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.managed_session_models import (
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

    projected = session.with_remote_projection(
        surface_id="surface-web-1",
        surface_kind="web",
        interactive=True,
    )

    assert projected.session_id == session.session_id
    assert projected.execution_owner == "local_process"
    assert projected.surface_bindings[-1].surface_id == "surface-web-1"
    assert projected.surface_bindings[-1].surface_kind == "web"
    assert projected.surface_bindings[-1].projection_mode == "remote_projection"
    assert session.surface_bindings == ()


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
