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

    monkeypatch.setattr(cli, "verify_cli_is_executable", lambda _name: "/usr/bin/codex")
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
