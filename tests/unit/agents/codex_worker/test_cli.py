"""Unit tests for codex worker CLI preflight and entrypoint behavior."""

from __future__ import annotations

import argparse
import subprocess

import pytest

from moonmind.agents.codex_worker import cli
from moonmind.agents.codex_worker.utils import CliVerificationError


def test_run_preflight_missing_codex_raises(monkeypatch) -> None:
    """Preflight should fail when codex binary is unavailable."""

    def _raise(_name):
        raise CliVerificationError("missing")

    monkeypatch.setattr(cli, "verify_cli_is_executable", _raise)

    with pytest.raises(RuntimeError):
        cli.run_preflight()


def test_run_preflight_login_failure_raises(monkeypatch) -> None:
    """Preflight should fail when `codex login status` exits non-zero."""

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda _name: "/usr/bin/codex",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr="not logged in",
        ),
    )

    with pytest.raises(RuntimeError):
        cli.run_preflight()


def test_run_preflight_with_github_token_runs_gh_auth_commands(monkeypatch) -> None:
    """Token-present startup should run gh auth login/setup/status in order."""

    calls: list[tuple[list[str], str | None]] = []

    def fake_verify(name: str) -> str:
        return f"/usr/bin/{name}"

    def fake_run(command, *args, **kwargs):
        calls.append((list(command), kwargs.get("input")))
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(cli, "verify_cli_is_executable", fake_verify)
    monkeypatch.setattr(subprocess, "run", fake_run)

    cli.run_preflight(env={"GITHUB_TOKEN": "ghp-test-token"})

    assert calls[0] == (["/usr/bin/codex", "login", "status"], None)
    assert calls[1] == (
        [
            "/usr/bin/gh",
            "auth",
            "login",
            "--hostname",
            "github.com",
            "--with-token",
        ],
        "ghp-test-token",
    )
    assert calls[2] == (["/usr/bin/gh", "auth", "setup-git"], None)
    assert calls[3] == (
        ["/usr/bin/gh", "auth", "status", "--hostname", "github.com"],
        None,
    )


def test_run_preflight_without_github_token_skips_gh_auth(monkeypatch) -> None:
    """No token should preserve existing codex-only preflight behavior."""

    verifications: list[str] = []
    calls: list[list[str]] = []

    def fake_verify(name: str) -> str:
        verifications.append(name)
        return f"/usr/bin/{name}"

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(cli, "verify_cli_is_executable", fake_verify)
    monkeypatch.setattr(subprocess, "run", fake_run)

    cli.run_preflight(env={})

    assert verifications == ["codex"]
    assert calls == [["/usr/bin/codex", "login", "status"]]


def test_run_preflight_missing_gh_raises_when_token_present(monkeypatch) -> None:
    """Token-present startup should fail fast when gh is unavailable."""

    def fake_verify(name: str) -> str:
        if name == "gh":
            raise CliVerificationError("missing gh")
        return "/usr/bin/codex"

    monkeypatch.setattr(cli, "verify_cli_is_executable", fake_verify)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="missing gh"):
        cli.run_preflight(env={"GITHUB_TOKEN": "ghp-test-token"})


def test_run_preflight_redacts_token_in_error_output(monkeypatch) -> None:
    """Auth failures should never surface raw token values."""

    token = "ghp-top-secret"

    def fake_verify(name: str) -> str:
        return f"/usr/bin/{name}"

    def fake_run(command, *args, **kwargs):
        if command[:4] == ["/usr/bin/gh", "auth", "login", "--hostname"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout="",
                stderr=f"token rejected: {token}",
            )
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(cli, "verify_cli_is_executable", fake_verify)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        cli.run_preflight(env={"GITHUB_TOKEN": token})

    assert token not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_main_returns_error_when_run_fails(monkeypatch) -> None:
    """CLI main should exit 1 when async runtime fails."""

    async def _fail(_args: argparse.Namespace) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "_run", _fail)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--once"])

    assert exc_info.value.code == 1


def test_main_success_path(monkeypatch) -> None:
    """CLI main should return 0 when runtime succeeds."""

    async def _ok(_args: argparse.Namespace) -> None:
        return None

    monkeypatch.setattr(cli, "_run", _ok)

    assert cli.main(["--once"]) == 0
