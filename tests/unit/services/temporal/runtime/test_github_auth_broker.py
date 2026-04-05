from __future__ import annotations

import asyncio
import os
from pathlib import Path
import uuid

import pytest

from moonmind.workflows.temporal.runtime.github_auth_broker import (
    GitHubAuthBrokerManager,
    request_github_token,
    run_gh_wrapper,
)


@pytest.mark.asyncio
async def test_github_auth_broker_serves_token_and_cleans_socket():
    manager = GitHubAuthBrokerManager()
    socket_dir = Path("/tmp") / f"mm-gh-broker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    socket_path = socket_dir / "github-auth.sock"

    await manager.start(
        run_id="run-1",
        token="ghp_testtoken123",
        socket_path=str(socket_path),
    )

    assert (
        await asyncio.to_thread(request_github_token, str(socket_path))
        == "ghp_testtoken123"
    )
    assert Path(socket_path).exists()
    assert (socket_dir.stat().st_mode & 0o777) == 0o700
    assert (socket_path.stat().st_mode & 0o777) == 0o600

    await manager.stop("run-1")

    assert not Path(socket_path).exists()


def test_run_gh_wrapper_clears_ambient_gh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ambient-gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "stale-github-token")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.github_auth_broker.request_github_token",
        lambda _socket_path: "broker-issued-token",
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.github_auth_broker.sys.argv",
        ["gh-wrapper", "auth", "status"],
    )

    captured: dict[str, object] = {}

    def _fake_execvpe(path: str, args: list[str], env: dict[str, str]) -> None:
        captured["path"] = path
        captured["args"] = args
        captured["env"] = env
        raise SystemExit(0)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.github_auth_broker.os.execvpe",
        _fake_execvpe,
    )

    with pytest.raises(SystemExit):
        run_gh_wrapper(socket_path="/tmp/github-auth.sock", real_gh_path="/usr/bin/gh")

    assert captured["path"] == "/usr/bin/gh"
    assert captured["args"] == ["/usr/bin/gh", "auth", "status"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GITHUB_TOKEN"] == "broker-issued-token"
    assert "GH_TOKEN" not in env
