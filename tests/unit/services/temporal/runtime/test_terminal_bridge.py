"""Unit tests for OAuth terminal bridge frame handling."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime import terminal_bridge
from moonmind.workflows.temporal.runtime.terminal_bridge import (
    InMemoryPtyAdapter,
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


@pytest.mark.asyncio
async def test_terminal_bridge_forwards_input_and_resize_to_pty_adapter() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_pty",
        terminal_bridge_id="br_oas_terminal_pty",
        owner_user_id="user-1",
    )
    pty = InMemoryPtyAdapter(output_chunks=[b"Open https://example.test/login\r\n"])

    await pty.connect()
    assert await bridge.handle_frame_for_pty(
        {"type": "input", "data": "codex login\n"}, pty
    ) == {"type": "input_ack", "bytes": 12}
    assert await bridge.handle_frame_for_pty(
        {"type": "resize", "cols": 132, "rows": 40}, pty
    ) == {"type": "resize_ack", "cols": 132, "rows": 40}
    assert await bridge.handle_frame_for_pty({"type": "heartbeat"}, pty) == {
        "type": "heartbeat_ack"
    }

    assert pty.written == [b"codex login\n"]
    assert pty.resizes == [(132, 40)]
    assert bridge.input_event_count == 1
    assert bridge.output_event_count == 0
    assert bridge.heartbeat_count == 1
    assert list(bridge.input_events) == ["codex login\n"]


@pytest.mark.asyncio
async def test_terminal_bridge_streams_output_without_persisting_raw_output() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_output",
        terminal_bridge_id="br_oas_terminal_output",
        owner_user_id="user-1",
    )
    pty = InMemoryPtyAdapter(
        output_chunks=[
            b"Paste code from browser\r\n",
            b"credential material should not be metadata\r\n",
        ]
    )
    sent: list[bytes] = []

    await pty.connect()
    await bridge.stream_pty_output(pty, sent.append)

    assert sent == [
        b"Paste code from browser\r\n",
        b"credential material should not be metadata\r\n",
    ]
    assert bridge.output_event_count == 2
    assert list(bridge.output_events) == []


@pytest.mark.asyncio
async def test_terminal_bridge_streams_output_to_async_callback() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminal_output_async",
        terminal_bridge_id="br_oas_terminal_output_async",
        owner_user_id="user-1",
    )
    pty = InMemoryPtyAdapter(output_chunks=[b"first", b"second"])
    sent: list[bytes] = []

    async def send_output(chunk: bytes) -> None:
        sent.append(chunk)

    await pty.connect()
    await bridge.stream_pty_output(pty, send_output)

    assert sent == [b"first", b"second"]
    assert bridge.output_event_count == 2


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
        bootstrap_command=("codex", "login", "--device-auth"),
    )

    assert result["container_name"] == "moonmind_auth_oas_terminal_runner"
    assert "-v" in observed
    assert "codex_auth_volume:/home/app/.codex" in observed
    assert "--user" in observed
    assert "1000:1000" in observed
    assert "-e" in observed
    assert "CODEX_HOME=/home/app/.codex" in observed
    assert observed[-3:-1] == ["/bin/sh", "-lc"]
    assert "command -v codex" in observed[-1]
    assert "sleep 1800" in observed[-1]
    assert observed[-1].count("codex") == 2
    assert observed[-1] != "codex login --device-auth"


@pytest.mark.asyncio
async def test_start_terminal_bridge_container_uses_claude_home_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    async def fake_create_subprocess_exec(*args, **_kwargs):
        observed.extend(args)
        return _FakeProcess()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic")
    monkeypatch.setenv("CLAUDE_API_KEY", "ambient-claude")
    monkeypatch.setattr(
        terminal_bridge.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    await start_terminal_bridge_container(
        session_id="oas_terminal_runner_claude",
        runtime_id="claude_code",
        volume_ref="claude_auth_volume",
        volume_mount_path="/home/app/.claude",
        session_ttl=1800,
        bootstrap_command=("claude", "login"),
    )

    assert "claude_auth_volume:/home/app/.claude" in observed
    assert "HOME=/home/app" in observed
    assert "CLAUDE_HOME=/home/app/.claude" in observed
    assert "CLAUDE_VOLUME_PATH=/home/app/.claude" in observed
    assert "ANTHROPIC_API_KEY=" in observed
    assert "CLAUDE_API_KEY=" in observed
    assert "ANTHROPIC_API_KEY=ambient-anthropic" not in observed
    assert "CLAUDE_API_KEY=ambient-claude" not in observed
    assert "CODEX_HOME=/home/app/.claude" not in observed
    assert "command -v claude" in observed[-1]
    assert observed[-1] != "claude login"


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
            bootstrap_command=("codex", "login", "--device-auth"),
        )

    message = str(exc_info.value)
    assert "token=" not in message
    assert "password=" not in message
    assert "super-secret" not in message
    assert "hunter2" not in message
    assert "failed" in message


@pytest.mark.asyncio
async def test_start_terminal_bridge_container_uses_configured_runner_user(
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
    monkeypatch.setenv("MOONMIND_OAUTH_RUNNER_USER", "2001:2001")

    await start_terminal_bridge_container(
        session_id="oas_terminal_runner_custom_user",
        runtime_id="codex_cli",
        volume_ref="codex_auth_volume",
        volume_mount_path="/home/app/.codex",
        session_ttl=1800,
        bootstrap_command=("codex", "login", "--device-auth"),
    )

    assert "--user" in observed
    assert "2001:2001" in observed
