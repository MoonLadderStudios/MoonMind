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

    def fake_run(command, *args, **kwargs):
        if command == ["/usr/bin/speckit", "--version"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="speckit 0.4.0",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="not logged in",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        cli.run_preflight(env={"DEFAULT_EMBEDDING_PROVIDER": "ollama"})


def test_run_preflight_with_github_token_runs_gh_auth_commands(monkeypatch) -> None:
    """Token-present startup should run gh auth login/setup/status in order."""

    calls: list[tuple[list[str], str | None, dict[str, str] | None]] = []
    monkeypatch.setenv("MOONMIND_TEST_ENV", "preserved")

    def fake_verify(name: str) -> str:
        return f"/usr/bin/{name}"

    def fake_run(command, *args, **kwargs):
        calls.append((list(command), kwargs.get("input"), kwargs.get("env")))
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(cli, "verify_cli_is_executable", fake_verify)
    monkeypatch.setattr(subprocess, "run", fake_run)

    cli.run_preflight(
        env={
            "GITHUB_TOKEN": "ghp-test-token",
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
        }
    )

    assert calls[0] == (["/usr/bin/speckit", "--version"], None, None)
    assert calls[1] == (["/usr/bin/codex", "login", "status"], None, None)
    assert calls[2][0] == [
        "/usr/bin/gh",
        "auth",
        "login",
        "--hostname",
        "github.com",
        "--with-token",
    ]
    assert calls[2][1] == "ghp-test-token"
    assert calls[2][2] is not None
    assert calls[2][2].get("MOONMIND_TEST_ENV") == "preserved"
    assert "GITHUB_TOKEN" not in calls[2][2]
    assert "GH_TOKEN" not in calls[2][2]
    assert calls[3][0] == ["/usr/bin/gh", "auth", "setup-git"]
    assert calls[3][1] is None
    assert calls[3][2] is not None
    assert calls[3][2].get("MOONMIND_TEST_ENV") == "preserved"
    assert "GITHUB_TOKEN" not in calls[3][2]
    assert "GH_TOKEN" not in calls[3][2]
    assert calls[4][0] == ["/usr/bin/gh", "auth", "status", "--hostname", "github.com"]
    assert calls[4][1] is None
    assert calls[4][2] is not None
    assert calls[4][2].get("MOONMIND_TEST_ENV") == "preserved"
    assert "GITHUB_TOKEN" not in calls[4][2]
    assert "GH_TOKEN" not in calls[4][2]

    for idx in (2, 3, 4):
        env = calls[idx][2]
        assert env is not None
        assert "PATH" in env
        assert "GITHUB_TOKEN" not in env
        assert "GH_TOKEN" not in env


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

    cli.run_preflight(env={"DEFAULT_EMBEDDING_PROVIDER": "ollama"})

    assert verifications == ["codex", "speckit"]
    assert calls == [
        ["/usr/bin/speckit", "--version"],
        ["/usr/bin/codex", "login", "status"],
    ]


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
        cli.run_preflight(
            env={
                "GITHUB_TOKEN": "ghp-test-token",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


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
        cli.run_preflight(
            env={
                "GITHUB_TOKEN": token,
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )

    assert token not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_run_checked_command_merges_environment_overrides(monkeypatch) -> None:
    """Subprocess env overrides should preserve base vars while removing keys."""

    observed_env: dict[str, str] | None = None

    def fake_run(command, *args, **kwargs):
        nonlocal observed_env
        observed_env = kwargs.get("env")
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("MM_KEEP_VAR", "present")
    monkeypatch.setenv("MM_REMOVE_VAR", "remove-me")

    cli._run_checked_command(
        ["/usr/bin/echo", "ok"],
        env_overrides={"MM_KEEP_VAR": "overridden", "MM_NEW_VAR": "new"},
        unset_env_keys=("MM_REMOVE_VAR",),
    )

    assert observed_env is not None
    assert observed_env["MM_KEEP_VAR"] == "overridden"
    assert "MM_REMOVE_VAR" not in observed_env
    assert observed_env["MM_NEW_VAR"] == "new"


def test_run_preflight_missing_speckit_raises(monkeypatch) -> None:
    """Preflight should fail when speckit binary is unavailable."""

    def fake_verify(name: str) -> str:
        if name == "speckit":
            raise CliVerificationError("missing speckit")
        return f"/usr/bin/{name}"

    monkeypatch.setattr(cli, "verify_cli_is_executable", fake_verify)

    with pytest.raises(RuntimeError, match="missing speckit"):
        cli.run_preflight(env={"DEFAULT_EMBEDDING_PROVIDER": "ollama"})


def test_run_preflight_speckit_version_fallback_to_help(monkeypatch) -> None:
    """Preflight should accept speckit shims that only support --help."""

    calls: list[list[str]] = []

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        if command == ["/usr/bin/speckit", "--version"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=2,
                stdout="Usage: specify",
                stderr="No such option: --version",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="ok",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    cli.run_preflight(env={"DEFAULT_EMBEDDING_PROVIDER": "ollama"})

    assert calls[:3] == [
        ["/usr/bin/speckit", "--version"],
        ["/usr/bin/speckit", "--help"],
        ["/usr/bin/codex", "login", "status"],
    ]


def test_run_preflight_speckit_non_version_error_raises(monkeypatch) -> None:
    """Fallback should not mask unrelated Speckit execution failures."""

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(command, *args, **kwargs):
        if command == ["/usr/bin/speckit", "--version"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout="",
                stderr="permission denied",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="permission denied"):
        cli.run_preflight(env={"DEFAULT_EMBEDDING_PROVIDER": "ollama"})


def test_run_preflight_google_embedding_requires_credential(monkeypatch) -> None:
    """Google embedding profiles should fail fast when key material is absent."""

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )

    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY or GEMINI_API_KEY"):
        cli.run_preflight(
            env={
                "DEFAULT_EMBEDDING_PROVIDER": "google",
                "GOOGLE_EMBEDDING_MODEL": "gemini-embedding-001",
            }
        )


def test_run_preflight_gemini_runtime_verifies_gemini_not_codex(monkeypatch) -> None:
    """Gemini worker runtime should check Gemini CLI without Codex login."""

    calls: list[list[str]] = []
    verifications: list[str] = []

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

    cli.run_preflight(
        env={
            "MOONMIND_WORKER_RUNTIME": "gemini",
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
        }
    )

    assert verifications == ["gemini", "speckit"]
    assert calls == [
        ["/usr/bin/speckit", "--version"],
        ["/usr/bin/gemini", "--version"],
    ]


def test_run_preflight_claude_runtime_checks_claude_auth(monkeypatch) -> None:
    """Claude runtime should validate Claude CLI version + auth status."""

    calls: list[list[str]] = []
    verifications: list[str] = []

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

    cli.run_preflight(
        env={
            "MOONMIND_WORKER_RUNTIME": "claude",
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
        }
    )

    assert verifications == ["claude", "speckit"]
    assert calls == [
        ["/usr/bin/speckit", "--version"],
        ["/usr/bin/claude", "--version"],
        ["/usr/bin/claude", "auth", "status"],
    ]


def test_run_preflight_claude_runtime_falls_back_to_login_status(monkeypatch) -> None:
    """If `claude auth status` is unsupported, preflight should try login status."""

    calls: list[list[str]] = []

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        if command == ["/usr/bin/claude", "auth", "status"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=2,
                stdout="",
                stderr="unknown command 'auth'",
            )
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    cli.run_preflight(
        env={
            "MOONMIND_WORKER_RUNTIME": "claude",
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
        }
    )

    assert calls == [
        ["/usr/bin/speckit", "--version"],
        ["/usr/bin/claude", "--version"],
        ["/usr/bin/claude", "auth", "status"],
        ["/usr/bin/claude", "login", "status"],
    ]


def test_run_preflight_claude_runtime_does_not_fallback_on_auth_error_with_usage_hint(
    monkeypatch,
) -> None:
    """Auth failures with usage hints should still fail preflight without fallback."""

    calls: list[list[str]] = []

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        if command == ["/usr/bin/claude", "auth", "status"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout="",
                stderr=(
                    "Error: not authenticated\n\n"
                    "Usage: claude auth status [flags]"
                ),
            )
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="not authenticated"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "claude",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )

    assert calls == [
        ["/usr/bin/speckit", "--version"],
        ["/usr/bin/claude", "--version"],
        ["/usr/bin/claude", "auth", "status"],
    ]


def test_run_preflight_universal_checks_codex_and_claude_auth(monkeypatch) -> None:
    """Universal runtime should validate both Codex and Claude auth states."""

    calls: list[list[str]] = []
    verifications: list[str] = []

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

    cli.run_preflight(
        env={
            "MOONMIND_WORKER_RUNTIME": "universal",
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
        }
    )

    assert verifications == ["codex", "gemini", "claude", "speckit"]
    assert calls == [
        ["/usr/bin/speckit", "--version"],
        ["/usr/bin/codex", "login", "status"],
        ["/usr/bin/gemini", "--version"],
        ["/usr/bin/claude", "--version"],
        ["/usr/bin/claude", "auth", "status"],
    ]


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
