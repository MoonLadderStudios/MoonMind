"""Unit tests for Claude CLI auth-mode helpers in runtime_mode."""

from __future__ import annotations



from celery_worker.runtime_mode import (
    format_invalid_claude_cli_auth_mode_error,
    inspect_claude_home_for_auth_mode,
    is_invalid_claude_cli_auth_mode,
    resolve_claude_cli_auth_mode,
)


def test_resolve_claude_cli_auth_mode_defaults_to_api_key() -> None:
    """Default mode should be api_key when env var is absent."""

    mode, raw = resolve_claude_cli_auth_mode(env={})
    assert mode == "api_key"
    assert raw == "api_key"


def test_resolve_claude_cli_auth_mode_reads_env_var() -> None:
    """Mode should be resolved from MOONMIND_CLAUDE_CLI_AUTH_MODE."""

    mode, raw = resolve_claude_cli_auth_mode(
        env={"MOONMIND_CLAUDE_CLI_AUTH_MODE": "oauth"}
    )
    assert mode == "oauth"
    assert raw == "oauth"


def test_resolve_claude_cli_auth_mode_api_key_explicit() -> None:
    """api_key mode should be returned when set explicitly."""

    mode, raw = resolve_claude_cli_auth_mode(
        env={"MOONMIND_CLAUDE_CLI_AUTH_MODE": "api_key"}
    )
    assert mode == "api_key"
    assert raw == "api_key"


def test_resolve_claude_cli_auth_mode_invalid_returns_default() -> None:
    """Invalid mode should return (default_mode, raw_value) without raising."""

    mode, raw = resolve_claude_cli_auth_mode(
        env={"MOONMIND_CLAUDE_CLI_AUTH_MODE": "some-invalid-value"}
    )
    assert mode == "api_key"
    assert raw == "some-invalid-value"


def test_is_invalid_claude_cli_auth_mode_returns_false_for_api_key() -> None:
    """api_key is a valid mode and should not be flagged as invalid."""

    assert is_invalid_claude_cli_auth_mode("api_key") is False


def test_is_invalid_claude_cli_auth_mode_returns_false_for_oauth() -> None:
    """oauth is a valid mode and should not be flagged as invalid."""

    assert is_invalid_claude_cli_auth_mode("oauth") is False


def test_is_invalid_claude_cli_auth_mode_returns_true_for_unknown() -> None:
    """Unknown mode values should be flagged as invalid."""

    assert is_invalid_claude_cli_auth_mode("ftp") is True
    assert is_invalid_claude_cli_auth_mode("token") is True
    assert is_invalid_claude_cli_auth_mode("AIza-secret") is True


def test_is_invalid_claude_cli_auth_mode_returns_false_for_empty() -> None:
    """Empty string should not be flagged as invalid (treated as missing)."""

    assert is_invalid_claude_cli_auth_mode("") is False


def test_format_invalid_claude_cli_auth_mode_error_redacts_value() -> None:
    """Error message should include redacted summary, not the raw value."""

    raw = "AIza-very-secret-key-abc123"
    msg = format_invalid_claude_cli_auth_mode_error(raw)
    assert raw not in msg
    assert "MOONMIND_CLAUDE_CLI_AUTH_MODE" in msg
    assert "api_key" in msg
    assert "oauth" in msg
    assert "<redacted:" in msg


def test_inspect_claude_home_missing_for_oauth_mode() -> None:
    """oauth mode with no CLAUDE_HOME should return missing_for_oauth issue."""

    home, issue = inspect_claude_home_for_auth_mode(auth_mode="oauth", claude_home=None)
    assert home is None
    assert issue == "missing_for_oauth"


def test_inspect_claude_home_missing_for_api_key_mode_is_ok() -> None:
    """api_key mode with no CLAUDE_HOME should not raise any issue."""

    home, issue = inspect_claude_home_for_auth_mode(
        auth_mode="api_key", claude_home=None
    )
    assert home is None
    assert issue is None


def test_inspect_claude_home_non_directory_raises_issue() -> None:
    """Non-existent path should return not_directory issue for any mode."""

    home, issue = inspect_claude_home_for_auth_mode(
        auth_mode="oauth",
        claude_home="/tmp/no-such-dir-xyzzy",
        isdir=lambda _: False,
        access=lambda _p, _m: True,
    )
    assert home == "/tmp/no-such-dir-xyzzy"
    assert issue == "not_directory"


def test_inspect_claude_home_not_writable_for_oauth() -> None:
    """oauth mode with a non-writable directory should return not_writable_for_oauth."""

    home, issue = inspect_claude_home_for_auth_mode(
        auth_mode="oauth",
        claude_home="/tmp/read-only-dir",
        isdir=lambda path: path == "/tmp/read-only-dir",
        access=lambda _p, _m: False,
    )
    assert home == "/tmp/read-only-dir"
    assert issue == "not_writable_for_oauth"


def test_inspect_claude_home_valid_oauth_directory() -> None:
    """Valid writable directory in oauth mode should return no issue."""

    home, issue = inspect_claude_home_for_auth_mode(
        auth_mode="oauth",
        claude_home="/tmp/claude-auth",
        isdir=lambda path: path == "/tmp/claude-auth",
        access=lambda _p, _m: True,
    )
    assert home == "/tmp/claude-auth"
    assert issue is None


def test_inspect_claude_home_api_key_mode_skips_writability_check() -> None:
    """api_key mode should not require a writable home directory."""

    home, issue = inspect_claude_home_for_auth_mode(
        auth_mode="api_key",
        claude_home="/tmp/read-only-dir",
        isdir=lambda path: path == "/tmp/read-only-dir",
        access=lambda _p, _m: False,
    )
    assert home == "/tmp/read-only-dir"
    assert issue is None
