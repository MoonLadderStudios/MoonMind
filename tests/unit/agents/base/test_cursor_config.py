"""Unit tests for Cursor CLI permission config generation."""

import json

from moonmind.agents.base.cursor_config import (
    generate_cursor_cli_json,
    write_cursor_cli_json,
)


def test_generate_full_autonomy_default():
    """Empty policy defaults to full_autonomy."""
    config = generate_cursor_cli_json({})
    perms = config["permissions"]
    assert "Read(**)" in perms["allow"]
    assert "Write(**)" in perms["allow"]
    assert "Shell(**)" in perms["allow"]
    assert perms["deny"] == []


def test_generate_full_autonomy_explicit():
    """Explicit full_autonomy level."""
    config = generate_cursor_cli_json({"level": "full_autonomy"})
    perms = config["permissions"]
    assert len(perms["allow"]) == 3
    assert "Read(**)" in perms["allow"]
    assert "Write(**)" in perms["allow"]
    assert "Shell(**)" in perms["allow"]


def test_generate_supervised():
    """Supervised level allows read/write with specific shell commands."""
    config = generate_cursor_cli_json({
        "level": "supervised",
        "allow_shell": ["npm test", "npm run build"],
    })
    perms = config["permissions"]
    assert "Read(**)" in perms["allow"]
    assert "Write(**)" in perms["allow"]
    assert "Shell(npm test)" in perms["allow"]
    assert "Shell(npm run build)" in perms["allow"]
    # Should NOT have Shell(**)
    assert "Shell(**)" not in perms["allow"]


def test_generate_supervised_no_shell():
    """Supervised level with no shell commands."""
    config = generate_cursor_cli_json({"level": "supervised"})
    perms = config["permissions"]
    assert "Read(**)" in perms["allow"]
    assert "Write(**)" in perms["allow"]
    assert len(perms["allow"]) == 2  # Only Read and Write


def test_generate_restricted():
    """Restricted level only allows explicitly listed permissions."""
    config = generate_cursor_cli_json({
        "level": "restricted",
        "allow_read": ["src/**"],
        "allow_write": ["src/**"],
        "allow_shell": ["npm test"],
    })
    perms = config["permissions"]
    assert "Read(src/**)" in perms["allow"]
    assert "Write(src/**)" in perms["allow"]
    assert "Shell(npm test)" in perms["allow"]
    assert len(perms["allow"]) == 3
    # Should NOT have global wildcards
    assert "Read(**)" not in perms["allow"]
    assert "Write(**)" not in perms["allow"]


def test_generate_restricted_empty():
    """Restricted level with no explicit permissions."""
    config = generate_cursor_cli_json({"level": "restricted"})
    perms = config["permissions"]
    assert perms["allow"] == []
    assert perms["deny"] == []


def test_explicit_deny_rules():
    """Deny rules are passed through from policy."""
    config = generate_cursor_cli_json({
        "level": "full_autonomy",
        "deny": ["Shell(rm -rf *)", "WebFetch(*)"],
    })
    perms = config["permissions"]
    assert "Shell(rm -rf *)" in perms["deny"]
    assert "WebFetch(*)" in perms["deny"]


def test_unknown_level_defaults_to_full_autonomy():
    """Unknown policy level defaults to full_autonomy with warning."""
    config = generate_cursor_cli_json({"level": "unknown_mode"})
    perms = config["permissions"]
    assert "Read(**)" in perms["allow"]
    assert "Write(**)" in perms["allow"]
    assert "Shell(**)" in perms["allow"]


def test_write_cursor_cli_json(tmp_path):
    """Write config to .cursor/cli.json file."""
    policy = {"level": "supervised", "allow_shell": ["git status"]}
    result_path = write_cursor_cli_json(tmp_path, policy)

    assert result_path.exists()
    assert result_path.name == "cli.json"
    assert result_path.parent.name == ".cursor"

    content = json.loads(result_path.read_text())
    assert "permissions" in content
    assert "Shell(git status)" in content["permissions"]["allow"]


def test_write_cursor_cli_json_creates_dirs(tmp_path):
    """Write creates .cursor directory automatically."""
    workspace = tmp_path / "workspace" / "nested"
    workspace.mkdir(parents=True)

    result_path = write_cursor_cli_json(workspace, {})
    assert result_path.exists()
    assert (workspace / ".cursor" / "cli.json").exists()
