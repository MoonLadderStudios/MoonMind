"""Unit tests for codex worker CLI preflight and entrypoint behavior."""

from __future__ import annotations

import argparse
import os
import subprocess

import pytest

from moonmind.agents.codex_worker import cli
from moonmind.agents.codex_worker.utils import CliVerificationError


@pytest.fixture(autouse=True)
def _disable_rag_preflight_checks(monkeypatch) -> None:
    """Prevent preflight tests from performing slow external RAG readiness checks."""

    monkeypatch.setattr(cli, "ensure_rag_ready", lambda _settings: None)


def test_run_preflight_missing_codex_raises(monkeypatch) -> None:
    """Preflight should fail when codex binary is unavailable."""

    def _raise(_name):
        raise CliVerificationError("missing")

    monkeypatch.setattr(cli, "verify_cli_is_executable", _raise)

    with pytest.raises(RuntimeError):
        cli.run_preflight()


def test_run_preflight_missing_rg_raises_clear_diagnostic(monkeypatch) -> None:
    """Codex-capable startup should fail fast with one rg diagnostic."""

    calls: list[list[str]] = []

    def fake_verify(name: str) -> str:
        if name == "rg":
            raise CliVerificationError("The 'rg' CLI is not available on PATH.")
        return f"/usr/bin/{name}"

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(cli, "verify_cli_is_executable", fake_verify)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Codex runtime requires ripgrep"):
        cli.run_preflight(env={"DEFAULT_EMBEDDING_PROVIDER": "ollama"})

    assert calls == []


def test_run_preflight_login_failure_raises(monkeypatch) -> None:
    """Preflight should fail when `codex login status` exits non-zero."""

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(command, *args, **kwargs):
        if command == ["/usr/bin/rg", "--version"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="ripgrep 14.0.0",
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

    assert calls[0] == (["/usr/bin/rg", "--version"], None, None)
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

    assert verifications == ["codex", "rg"]
    assert calls == [
        ["/usr/bin/rg", "--version"],
        ["/usr/bin/codex", "login", "status"],
    ]


def test_run_preflight_skips_non_matching_stage_skills(monkeypatch) -> None:
    """Non-matching stage skill configuration should not require Workflow preflight."""

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

    cli.run_preflight(
        env={
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            "WORKFLOW_DEFAULT_SKILL": "custom-skill",
            "WORKFLOW_DISCOVER_SKILL": "custom-skill",
            "WORKFLOW_SUBMIT_SKILL": "custom-skill",
            "WORKFLOW_PUBLISH_SKILL": "custom-skill",
        }
    )

    assert verifications == ["codex", "rg"]
    assert calls == [
        ["/usr/bin/rg", "--version"],
        ["/usr/bin/codex", "login", "status"],
    ]


def test_run_preflight_uses_workflow_skill_aliases(monkeypatch) -> None:
    """Canonical WORKFLOW_* aliases should drive speckit dependency checks."""

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

    cli.run_preflight(
        env={
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            "WORKFLOW_DEFAULT_SKILL": "custom-skill",
            "WORKFLOW_DISCOVER_SKILL": "custom-skill",
            "WORKFLOW_SUBMIT_SKILL": "custom-skill",
            "WORKFLOW_PUBLISH_SKILL": "custom-skill",
        }
    )

    assert verifications == ["codex", "rg"]
    assert calls == [
        ["/usr/bin/rg", "--version"],
        ["/usr/bin/codex", "login", "status"],
    ]


def test_run_preflight_respects_workflow_use_skills_alias(monkeypatch) -> None:
    """WORKFLOW_USE_SKILLS=false should skip Workflow checks."""

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

    cli.run_preflight(
        env={
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            "WORKFLOW_USE_SKILLS": "false",
            "WORKFLOW_DEFAULT_SKILL": "speckit",
        }
    )

    assert verifications == ["codex", "rg"]
    assert calls == [
        ["/usr/bin/rg", "--version"],
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


def test_run_checked_command_error_message_includes_return_code_and_tail(
    monkeypatch,
) -> None:
    """Failed command diagnostics should expose return code and last stderr line."""

    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=2,
            stdout="ignored stdout\n",
            stderr="warn: step 1\nfatal: bad request\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(
        RuntimeError, match=r"command failed \(2\): /usr/bin/codex login"
    ) as exc_info:
        cli._run_checked_command(["/usr/bin/codex", "login", "status"])

    message = str(exc_info.value)
    assert "warn: step 1" not in message
    assert "fatal: bad request" in message


def test_run_checked_command_truncates_after_redaction(monkeypatch) -> None:
    """Tokenized output should be redacted before truncating diagnostic text."""

    token = "ghp-redact-boundary-token-012345"
    detail = "x" * 980 + token + "tail"

    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=99,
            stdout="",
            stderr=detail,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        cli._run_checked_command(["codex", "login"], redaction_values=(token,))

    message = str(exc_info.value)
    assert token[:16] not in message
    assert "[REDACTED]" in message
    assert len(message) <= 1024


def test_run_checked_command_error_message_without_detail_uses_compact_hint(
    monkeypatch,
) -> None:
    """Failure without command output should still include compact command hint."""

    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=7,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(
        RuntimeError,
        match=r"command failed \(7\): codex exec",
    ) as exc_info:
        cli._run_checked_command(["codex", "exec", "run now"])

    message = str(exc_info.value)
    assert "run now" not in message




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
                "GOOGLE_EMBEDDING_MODEL": "gemini-embedding-2-preview",
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
            "MOONMIND_WORKER_RUNTIME": "gemini_cli",
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
        }
    )

    assert verifications == ["gemini_cli"]
    assert calls == [
        ["/usr/bin/gemini_cli", "--version"],
    ]


def test_run_preflight_claude_runtime_requires_api_key(monkeypatch) -> None:
    """Claude runtime should fail fast when ANTHROPIC_API_KEY is missing."""

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "claude",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


def test_run_preflight_claude_runtime_verifies_version_with_key(monkeypatch) -> None:
    """Claude runtime should validate CLI version when API key is configured."""

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
            "ANTHROPIC_API_KEY": "test-key",
        }
    )

    assert verifications == ["claude"]
    assert calls == [
        ["/usr/bin/claude", "--version"],
    ]


def test_run_preflight_jules_runtime_requires_configuration(monkeypatch) -> None:
    """Jules runtime should fail fast when Jules API settings are missing."""

    with pytest.raises(RuntimeError, match="targetRuntime=jules requires"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "jules",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


def test_run_preflight_jules_runtime_succeeds_with_configuration(monkeypatch) -> None:
    """Jules runtime should pass preflight when Jules API settings are present."""

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
            "MOONMIND_WORKER_RUNTIME": "jules",
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            "JULES_ENABLED": "true",
            "JULES_API_URL": "https://jules.example.test",
            "JULES_API_KEY": "test-key",
        }
    )

    assert verifications == []
    assert calls == []


def test_run_preflight_universal_without_claude_capability_skips_checks(
    monkeypatch,
) -> None:
    """Universal runtime without claude capability should skip Claude CLI validation."""

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
            "MOONMIND_WORKER_CAPABILITIES": "codex,gemini_cli",        }
    )

    assert verifications == ["codex", "gemini_cli", "rg"]
    assert calls == [
        ["/usr/bin/rg", "--version"],
        ["/usr/bin/codex", "login", "status"],
        ["/usr/bin/gemini_cli", "--version"],
    ]


def test_run_preflight_universal_with_claude_capability_requires_key(
    monkeypatch,
) -> None:
    """Universal runtime with claude capability should require API key."""

    monkeypatch.setattr(
        cli, "verify_cli_is_executable", lambda name: f"/usr/bin/{name}"
    )

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "universal",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
                "MOONMIND_WORKER_CAPABILITIES": "codex,claude,gemini_cli",
            }
        )


def test_run_preflight_universal_without_capabilities_requires_claude_key(
    monkeypatch,
) -> None:
    """Universal runtime defaults to Claude-capable when capabilities are unset."""

    monkeypatch.setattr(
        cli, "verify_cli_is_executable", lambda name: f"/usr/bin/{name}"
    )

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "universal",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


def test_run_preflight_universal_with_claude_capability_runs_checks(
    monkeypatch,
) -> None:
    """Universal runtime with claude capability should verify all CLIs when key exists."""

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
            "MOONMIND_WORKER_CAPABILITIES": "codex,claude,gemini_cli",
            "ANTHROPIC_API_KEY": "secret",
        }
    )

    assert verifications == ["codex", "gemini_cli", "claude", "rg"]
    assert calls == [
        ["/usr/bin/rg", "--version"],
        ["/usr/bin/codex", "login", "status"],
        ["/usr/bin/gemini_cli", "--version"],
        ["/usr/bin/claude", "--version"],
    ]


def test_run_preflight_gemini_oauth_requires_gemini_home(monkeypatch) -> None:
    """Gemini oauth mode should fail fast when GEMINI_HOME is unset."""

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, *args, **kwargs: subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    with pytest.raises(
        RuntimeError,
        match=r"GEMINI_CLI_HOME \(or GEMINI_HOME fallback\) is required",
    ):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "gemini_cli",
                "MOONMIND_GEMINI_CLI_AUTH_MODE": "oauth",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


def test_run_preflight_gemini_invalid_auth_mode_redacts_value(monkeypatch) -> None:
    """Invalid auth mode should fail with a redacted diagnostic."""

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, *args, **kwargs: subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match=r"received <redacted:\d+ chars>"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "gemini_cli",
                "MOONMIND_GEMINI_CLI_AUTH_MODE": "AIza-secret-like-value",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


def test_run_preflight_gemini_oauth_requires_writable_gemini_home(monkeypatch) -> None:
    """Gemini oauth mode should enforce writable GEMINI_HOME directories."""

    monkeypatch.setattr(
        cli,
        "verify_cli_is_executable",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, *args, **kwargs: subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    monkeypatch.setattr(os.path, "isdir", lambda path: path == "/tmp/gemini-auth")
    monkeypatch.setattr(
        os,
        "access",
        lambda _path, _mode: False,
    )

    with pytest.raises(
        RuntimeError,
        match=r"GEMINI_CLI_HOME \(or GEMINI_HOME fallback\) must be writable",
    ):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "gemini_cli",
                "MOONMIND_GEMINI_CLI_AUTH_MODE": "oauth",
                "GEMINI_HOME": "/tmp/gemini-auth",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


def test_run_preflight_claude_oauth_with_valid_home_succeeds(monkeypatch) -> None:
    """Claude oauth mode with a valid writable CLAUDE_HOME should pass preflight."""

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
    monkeypatch.setattr(os.path, "isdir", lambda path: path == "/tmp/claude-auth")
    monkeypatch.setattr(os, "access", lambda _p, _m: True)

    cli.run_preflight(
        env={
            "MOONMIND_WORKER_RUNTIME": "claude",
            "MOONMIND_CLAUDE_CLI_AUTH_MODE": "oauth",
            "CLAUDE_HOME": "/tmp/claude-auth",
            "DEFAULT_EMBEDDING_PROVIDER": "ollama",
        }
    )

    assert "claude" in verifications


def test_run_preflight_claude_oauth_missing_home_raises(monkeypatch) -> None:
    """Claude oauth mode with no CLAUDE_HOME should fail with a clear error."""

    monkeypatch.setattr(
        cli, "verify_cli_is_executable", lambda name: f"/usr/bin/{name}"
    )

    with pytest.raises(RuntimeError, match="CLAUDE_HOME is required"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "claude",
                "MOONMIND_CLAUDE_CLI_AUTH_MODE": "oauth",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


def test_run_preflight_claude_oauth_non_directory_home_raises(monkeypatch) -> None:
    """Claude oauth mode with a non-existent CLAUDE_HOME directory should fail."""

    monkeypatch.setattr(
        cli, "verify_cli_is_executable", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(os.path, "isdir", lambda path: False)

    with pytest.raises(RuntimeError, match="must point to an existing directory"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "claude",
                "MOONMIND_CLAUDE_CLI_AUTH_MODE": "oauth",
                "CLAUDE_HOME": "/tmp/no-such-dir",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


def test_run_preflight_claude_invalid_auth_mode_redacts_value(monkeypatch) -> None:
    """Invalid Claude auth mode should fail with a redacted diagnostic."""

    monkeypatch.setattr(
        cli, "verify_cli_is_executable", lambda name: f"/usr/bin/{name}"
    )

    with pytest.raises(RuntimeError, match=r"received <redacted:\d+ chars>"):
        cli.run_preflight(
            env={
                "MOONMIND_WORKER_RUNTIME": "claude",
                "MOONMIND_CLAUDE_CLI_AUTH_MODE": "AIza-secret-like-value",
                "DEFAULT_EMBEDDING_PROVIDER": "ollama",
            }
        )


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
