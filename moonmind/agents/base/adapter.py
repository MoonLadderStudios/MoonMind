"""Adapter logic for formatting the execution environment of managed CLI agents.

This module implements environment shaping (clearing API keys when in OAuth mode
to prevent fallback behavior) and dynamic volume mount path resolution as required
by the MoonMind Managed Agents Authentication spec.
"""

from typing import Dict, Optional


def shape_agent_environment(
    base_env: Dict[str, str],
    auth_mode: str,
) -> Dict[str, str]:
    """Shape the agent's environment dictionary based on the authentication mode.

    If auth_mode is 'oauth', explicitly clear API keys to prevent the CLI from
    falling back to key-based auth (DOC-REQ-001). We set them to empty strings
    rather than deleting them so they override exported host variables in subprocesses.
    
    Args:
        base_env: The starting environment dictionary.
        auth_mode: The authentication mode (e.g., 'oauth' or 'api_key').
        
    Returns:
        A new dictionary with the shaped environment variables.
    """
    shaped_env = dict(base_env)
    
    if auth_mode.lower() == "oauth":
        oauth_scrubbable_keys = [
            "ANTHROPIC_API_KEY",
            "CLAUDE_API_KEY",     # Claude Code aliases
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "CURSOR_API_KEY",
        ]
        
        for key in oauth_scrubbable_keys:
            shaped_env[key] = ""
            
    return shaped_env


def resolve_volume_mount_env(
    base_env: Dict[str, str],
    runtime_id: str,
    volume_mount_path: Optional[str],
) -> Dict[str, str]:
    """Inject the dynamically resolved volume mount path into the environment.

    (DOC-REQ-003, DOC-REQ-009) For a given managed runtime, it expects its
    authentication configuration to live in a specific directory defined by an
    environment variable (e.g., GEMINI_HOME).

    Args:
        base_env: The starting environment dictionary.
        runtime_id: The ID of the runtime family (e.g., 'gemini_cli').
        volume_mount_path: The absolute path where the auth profile is mounted.
        
    Returns:
        A new dictionary with the volume mount environment variables injected.
    """
    if not volume_mount_path:
        return base_env
        
    shaped_env = dict(base_env)
    
    if runtime_id == "gemini_cli":
        shaped_env["GEMINI_HOME"] = volume_mount_path
        shaped_env["GEMINI_CLI_HOME"] = volume_mount_path
    elif runtime_id == "claude_code":
        shaped_env["CLAUDE_HOME"] = volume_mount_path
    elif runtime_id == "codex_cli":
        shaped_env["CODEX_HOME"] = volume_mount_path
    elif runtime_id == "cursor_cli":
        shaped_env["CURSOR_CONFIG_DIR"] = volume_mount_path
        
    return shaped_env
