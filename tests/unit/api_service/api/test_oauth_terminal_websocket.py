"""Unit tests for OAuth terminal WebSocket helper behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from api_service.api import websockets
from api_service.db.models import ManagedAgentOAuthSession, OAuthSessionStatus


def _session(
    *,
    status: OAuthSessionStatus = OAuthSessionStatus.AWAITING_USER,
    expires_at: datetime | None = None,
    container_name: str | None = "moonmind_auth_oas_ws1",
) -> ManagedAgentOAuthSession:
    return ManagedAgentOAuthSession(
        session_id="oas_ws1",
        runtime_id="codex_cli",
        profile_id="codex-ws",
        status=status,
        requested_by_user_id="user-1",
        container_name=container_name,
        expires_at=expires_at,
    )


def test_terminal_close_reason_rejects_inactive_session() -> None:
    reason = websockets._terminal_close_reason(
        _session(status=OAuthSessionStatus.SUCCEEDED)
    )
    assert reason == "Session is not attachable in succeeded state"


def test_terminal_close_reason_rejects_expired_session() -> None:
    reason = websockets._terminal_close_reason(
        _session(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    assert reason == "Session has expired"


def test_terminal_close_reason_rejects_missing_runner_container() -> None:
    reason = websockets._terminal_close_reason(_session(container_name=None))
    assert reason == "Session terminal is not ready"


def test_terminal_close_reason_accepts_active_unexpired_session() -> None:
    reason = websockets._terminal_close_reason(
        _session(expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    )
    assert reason is None


def test_provider_bootstrap_command_uses_registry_command() -> None:
    assert websockets._provider_bootstrap_command("codex_cli") == ["true"]
    assert websockets._command_for_docker_exec("codex_cli") == "true"


def test_provider_bootstrap_command_rejects_unknown_runtime() -> None:
    with pytest.raises(ValueError, match="Unsupported OAuth runtime"):
        websockets._provider_bootstrap_command("unknown_runtime")


def test_json_frame_from_text_treats_json_scalars_as_terminal_input() -> None:
    assert websockets._json_frame_from_text('"1"') is None
    assert websockets._json_frame_from_text("true") is None
    assert websockets._json_frame_from_text("null") is None


def test_json_frame_from_text_accepts_mapping_frames() -> None:
    assert websockets._json_frame_from_text('{"type": "heartbeat"}') == {
        "type": "heartbeat"
    }


def test_resize_dimensions_rejects_malformed_values() -> None:
    assert websockets._resize_dimensions({"type": "resize", "cols": "bad"}) is None
    assert websockets._resize_dimensions({"type": "resize", "rows": object()}) is None


def test_resize_dimensions_parses_valid_values() -> None:
    assert websockets._resize_dimensions(
        {"type": "resize", "cols": "100", "rows": "40"}
    ) == (100, 40)
