"""Unit tests for OAuth terminal bridge frame handling."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime import terminal_bridge
from moonmind.workflows.temporal.runtime.terminal_bridge import (
    TerminalBridgeConnection,
    TerminalBridgeFrameError,
    start_terminal_bridge_container,
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

    with pytest.raises(TerminalBridgeFrameError, match="generic Docker exec"):
        bridge.handle_frame({"type": "task_terminal", "session": "managed"})


class _FakeProcess:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"container-id\n", self._stderr

    def kill(self) -> None:
        self.killed = True


@pytest.mark.asyncio
async def test_start_terminal_bridge_container_uses_provider_bootstrap_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    async def fake_create_subprocess_exec(*args, **_kwargs):
        observed.extend(args)
        return _FakeProcess()

    monkeypatch.setattr(
        terminal_bridge.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await start_terminal_bridge_container(
        session_id="oas_terminal_runner",
        runtime_id="codex_cli",
        volume_ref="codex_auth_volume",
        volume_mount_path="/home/app/.codex",
        session_ttl=1800,
        bootstrap_command=("codex", "login"),
    )

    assert result["container_name"] == "moonmind_auth_oas_terminal_runner"
    assert "-v" in observed
    assert "codex_auth_volume:/home/app/.codex" in observed
    assert observed[-2:] == ["codex", "login"]
    assert "sleep" not in observed


@pytest.mark.asyncio
async def test_start_terminal_bridge_container_redacts_startup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess(
            returncode=1,
            stderr=b"failed token=super-secret password=hunter2",
        )

    monkeypatch.setattr(
        terminal_bridge.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await start_terminal_bridge_container(
            session_id="oas_terminal_runner_failed",
            runtime_id="codex_cli",
            volume_ref="codex_auth_volume",
            volume_mount_path="/home/app/.codex",
            session_ttl=1800,
            bootstrap_command=("codex", "login"),
        )

    message = str(exc_info.value)
    assert "token=" not in message
    assert "password=" not in message
    assert "super-secret" not in message
    assert "hunter2" not in message
    assert "failed" in message
    assert "exit code 1" in message
