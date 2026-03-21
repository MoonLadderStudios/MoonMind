"""Cursor CLI permission config generator.

Produces ``.cursor/cli.json`` permission files from MoonMind's
``approval_policy`` dict.  The five Cursor permission types are:

- ``Shell(cmd)``  — allowed shell commands
- ``Read(path)``  — file read access
- ``Write(path)`` — file write access
- ``WebFetch(domain)`` — HTTP access control
- ``Mcp(server:tool)`` — MCP tool access

Policy levels:

- **full_autonomy** — unrestricted (``Read(**)``, ``Write(**)``, ``Shell(**)``).
- **supervised** — read/write allowed, only listed shell commands permitted.
- **restricted** — only explicitly listed permissions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default permission sets per policy level.
_FULL_AUTONOMY_ALLOW: list[str] = [
    "Read(**)",
    "Write(**)",
    "Shell(**)",
]

_SUPERVISED_ALLOW: list[str] = [
    "Read(**)",
    "Write(**)",
]


def generate_cursor_cli_json(approval_policy: dict[str, Any]) -> dict[str, Any]:
    """Convert a MoonMind ``approval_policy`` dict to a Cursor CLI config dict.

    The returned dict can be serialised to ``.cursor/cli.json``.

    Parameters
    ----------
    approval_policy:
        MoonMind approval policy.  Expected keys:

        - ``level`` (str): ``full_autonomy`` | ``supervised`` | ``restricted``
        - ``allow_shell`` (list[str], optional): shell commands to allow
          (``supervised`` mode).
        - ``allow_read`` (list[str], optional): read paths to allow
          (``restricted`` mode).
        - ``allow_write`` (list[str], optional): write paths to allow
          (``restricted`` mode).
        - ``deny`` (list[str], optional): explicit deny rules.

    Returns
    -------
    dict
        A dict with ``{"permissions": {"allow": [...], "deny": [...]}}``.
    """
    level: str = str(approval_policy.get("level", "full_autonomy")).strip().lower()
    explicit_deny: list[str] = list(approval_policy.get("deny", []))

    if level == "full_autonomy":
        allow_rules = list(_FULL_AUTONOMY_ALLOW)
    elif level == "supervised":
        allow_rules = list(_SUPERVISED_ALLOW)
        for cmd in approval_policy.get("allow_shell", []):
            allow_rules.append(f"Shell({cmd})")
    elif level == "restricted":
        allow_rules = []
        for path in approval_policy.get("allow_read", []):
            allow_rules.append(f"Read({path})")
        for path in approval_policy.get("allow_write", []):
            allow_rules.append(f"Write({path})")
        for cmd in approval_policy.get("allow_shell", []):
            allow_rules.append(f"Shell({cmd})")
    else:
        raise ValueError(
            f"Unknown approval_policy level {level!r}; "
            f"expected one of: full_autonomy, supervised, restricted"
        )

    return {
        "permissions": {
            "allow": allow_rules,
            "deny": explicit_deny,
        }
    }


def write_cursor_cli_json(
    workspace_path: Path,
    approval_policy: dict[str, Any],
) -> Path:
    """Generate and write ``.cursor/cli.json`` in the workspace.

    Creates the ``.cursor`` directory if it does not exist.

    Returns the path to the written file.
    """
    config = generate_cursor_cli_json(approval_policy)
    cursor_dir = workspace_path / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    cli_json_path = cursor_dir / "cli.json"
    cli_json_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote Cursor CLI config to %s", cli_json_path)
    return cli_json_path
