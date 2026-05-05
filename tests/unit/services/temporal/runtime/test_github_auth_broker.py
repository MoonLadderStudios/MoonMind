from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path

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

@pytest.mark.asyncio
async def test_github_auth_broker_keeps_shared_mm_gh_root_traversable_only():
    manager = GitHubAuthBrokerManager()
    # AF_UNIX socket paths are short on Linux; keep this test's path under /tmp
    # so xdist's long per-worker tmp_path prefix does not exceed that limit.
    short_base = Path("/tmp") / f"mm-gh-root-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    shared_root = short_base / "mm-gh"
    socket_dir = shared_root / "0123456789abcdef"
    socket_path = socket_dir / "github.sock"

    try:
        await manager.start(
            run_id="run-1",
            token="ghp_testtoken123",
            socket_path=str(socket_path),
        )

        assert (shared_root.stat().st_mode & 0o777) == 0o711
        assert (socket_dir.stat().st_mode & 0o777) == 0o700
        assert (socket_path.stat().st_mode & 0o777) == 0o600

        await manager.stop("run-1")
    finally:
        await manager.stop("run-1")
        shutil.rmtree(short_base, ignore_errors=True)

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

def test_run_gh_wrapper_resolves_real_gh_after_wrapper_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper_dir = tmp_path / "wrapper-bin"
    real_dir = tmp_path / "real-bin"
    wrapper_dir.mkdir()
    real_dir.mkdir()
    wrapper_path = wrapper_dir / "gh"
    real_gh_path = real_dir / "gh"
    wrapper_path.write_text("#!/bin/sh\n", encoding="utf-8")
    real_gh_path.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper_path.chmod(0o700)
    real_gh_path.chmod(0o700)
    monkeypatch.setenv("PATH", f"{wrapper_dir}{os.pathsep}{real_dir}")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.github_auth_broker.request_github_token",
        lambda _socket_path: "broker-issued-token",
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.github_auth_broker.sys.argv",
        [str(wrapper_path), "auth", "status"],
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
        run_gh_wrapper(socket_path="/tmp/github-auth.sock")

    assert captured["path"] == str(real_gh_path)
    assert captured["args"] == [str(real_gh_path), "auth", "status"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GITHUB_TOKEN"] == "broker-issued-token"
