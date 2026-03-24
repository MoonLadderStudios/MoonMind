"""Unit tests for managed agents environment adapter logic."""


from moonmind.agents.base.adapter import (
    resolve_volume_mount_env,
    shape_agent_environment,
)


def test_shape_agent_environment_oauth():
    """Test that API keys are explicitly scrubbed when auth_mode is oauth."""
    base_env = {
        "ANTHROPIC_API_KEY": "secret1",
        "ANTHROPIC_AUTH_TOKEN": "secret1b",
        "CLAUDE_API_KEY": "secret2",
        "OPENAI_API_KEY": "secret3",
        "GEMINI_API_KEY": "secret4",
        "GOOGLE_API_KEY": "secret5",
        "OTHER_VAR": "keepme",
    }
    
    shaped = shape_agent_environment(base_env, "oauth")
    
    # Assert API keys are set to empty string
    assert shaped["ANTHROPIC_API_KEY"] == ""
    assert shaped["ANTHROPIC_AUTH_TOKEN"] == ""
    assert shaped["CLAUDE_API_KEY"] == ""
    assert shaped["OPENAI_API_KEY"] == ""
    assert shaped["GEMINI_API_KEY"] == ""
    assert shaped["GOOGLE_API_KEY"] == ""
    
    # Assert other variables are untouched
    assert shaped["OTHER_VAR"] == "keepme"
    
def test_shape_agent_environment_api_key():
    """Test that API keys are preserved when auth_mode is not oauth."""
    base_env = {
        "GEMINI_API_KEY": "secret4",
        "OTHER_VAR": "keepme",
    }
    
    shaped = shape_agent_environment(base_env, "api_key")
    
    # Assert API keys remain
    assert shaped["GEMINI_API_KEY"] == "secret4"
    assert shaped["OTHER_VAR"] == "keepme"


def test_resolve_volume_mount_env_gemini():
    """Test volume mount resolution for Gemini CLI."""
    base_env = {"EXISTING": "var"}
    shaped = resolve_volume_mount_env(base_env, "gemini_cli", "/custom/mount/gemini")
    
    assert shaped["EXISTING"] == "var"
    assert shaped["GEMINI_HOME"] == "/custom/mount/gemini"
    assert shaped["GEMINI_CLI_HOME"] == "/custom/mount/gemini"


def test_resolve_volume_mount_env_claude():
    """Test volume mount resolution for Claude."""
    base_env = {"EXISTING": "var"}
    shaped = resolve_volume_mount_env(base_env, "claude_code", "/custom/mount/claude")
    
    assert shaped["EXISTING"] == "var"
    assert shaped["CLAUDE_HOME"] == "/custom/mount/claude"
    assert "GEMINI_HOME" not in shaped


def test_resolve_volume_mount_env_codex():
    """Test volume mount resolution for Codex CLI."""
    base_env = {"EXISTING": "var"}
    shaped = resolve_volume_mount_env(base_env, "codex_cli", "/custom/mount/codex")
    
    assert shaped["EXISTING"] == "var"
    assert shaped["CODEX_HOME"] == "/custom/mount/codex"


def test_resolve_volume_mount_env_empty():
    """Test volume mount is ignored if path is empty."""
    base_env = {"EXISTING": "var"}
    shaped = resolve_volume_mount_env(base_env, "gemini_cli", "")
    
    assert shaped == base_env
    assert "GEMINI_HOME" not in shaped


def test_resolve_volume_mount_env_cursor():
    """Test volume mount resolution for Cursor CLI."""
    base_env = {"EXISTING": "var"}
    shaped = resolve_volume_mount_env(base_env, "cursor_cli", "/home/app/.cursor")

    assert shaped["EXISTING"] == "var"
    assert shaped["CURSOR_CONFIG_DIR"] == "/home/app/.cursor"
    assert "GEMINI_HOME" not in shaped
    assert "CLAUDE_HOME" not in shaped
    assert "CODEX_HOME" not in shaped


def test_shape_agent_environment_oauth_includes_cursor_key():
    """Test that CURSOR_API_KEY is scrubbed when auth_mode is oauth."""
    base_env = {
        "CURSOR_API_KEY": "cursor-secret",
        "ANTHROPIC_API_KEY": "anth-secret",
        "OTHER_VAR": "keepme",
    }

    shaped = shape_agent_environment(base_env, "oauth")

    assert shaped["CURSOR_API_KEY"] == ""
    assert shaped["ANTHROPIC_API_KEY"] == ""
    assert shaped["OTHER_VAR"] == "keepme"


def test_shape_agent_environment_api_key_preserves_cursor_key():
    """Test that CURSOR_API_KEY is preserved when auth_mode is api_key."""
    base_env = {
        "CURSOR_API_KEY": "cursor-secret",
        "OTHER_VAR": "keepme",
    }

    shaped = shape_agent_environment(base_env, "api_key")

    assert shaped["CURSOR_API_KEY"] == "cursor-secret"
    assert shaped["OTHER_VAR"] == "keepme"
