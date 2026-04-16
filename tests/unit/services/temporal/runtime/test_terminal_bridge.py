"""Unit tests for OAuth terminal bridge frame handling."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime.terminal_bridge import (
    TerminalBridgeConnection,
    TerminalBridgeFrameError,
)


def test_terminal_bridge_handles_resize_input_output_and_heartbeat() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_frames",
        terminal_bridge_id="br_oas_terminal_frames",
        owner_user_id="user-1",
    )

    assert bridge.handle_frame({"type": "resize", "cols": 120, "rows": 36}) == {
        "type": "resize_ack",
        "cols": 120,
        "rows": 36,
    }
    assert bridge.handle_frame({"type": "input", "data": "codex login\n"}) == {
        "type": "input_ack",
        "bytes": 12,
    }
    assert bridge.handle_frame({"type": "output", "data": "Open browser\n"}) == {
        "type": "output_ack",
        "bytes": 13,
    }
    assert bridge.handle_frame({"type": "heartbeat"}) == {"type": "heartbeat_ack"}
    assert list(bridge.resize_events) == [(120, 36)]
    assert list(bridge.input_events) == ["codex login\n"]
    assert list(bridge.output_events) == ["Open browser\n"]
    assert bridge.heartbeat_count == 1


def test_terminal_bridge_rejects_malformed_resize_dimensions() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_bad_resize",
        terminal_bridge_id="br_oas_terminal_bad_resize",
        owner_user_id="user-1",
    )

    with pytest.raises(TerminalBridgeFrameError, match="resize dimensions must be integers"):
        bridge.handle_frame({"type": "resize", "cols": "wide", "rows": 36})


def test_terminal_bridge_keeps_bounded_event_metadata() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_bounded",
        terminal_bridge_id="br_oas_terminal_bounded",
        owner_user_id="user-1",
    )

    for index in range(300):
        bridge.handle_frame({"type": "input", "data": f"line-{index}\n"})

    assert len(bridge.input_events) == 256
    assert bridge.input_events[0] == "line-44\n"
    assert bridge.input_events[-1] == "line-299\n"


def test_terminal_bridge_rejects_generic_exec_frames() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_exec",
        terminal_bridge_id="br_oas_terminal_exec",
        owner_user_id="user-1",
    )

    with pytest.raises(TerminalBridgeFrameError, match="generic Docker exec"):
        bridge.handle_frame({"type": "docker_exec", "command": "sh"})
