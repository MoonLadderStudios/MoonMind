"""Cursor CLI rules and context injection.

Generates ``.cursor/rules/moonmind-task.mdc`` files that provide task
instructions and skill context to Cursor CLI runs.  The ``.mdc`` format is
Markdown with YAML frontmatter, automatically loaded by Cursor CLI as
project-level rules.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_task_rule_content(
    instruction: str,
    skill_context: str | None = None,
) -> str:
    """Produce a ``.mdc`` string from a task instruction and optional skill context.

    The output follows Cursor's rule format with YAML frontmatter.

    Parameters
    ----------
    instruction:
        The primary task instruction text.
    skill_context:
        Optional additional skill/context information to inject.

    Returns
    -------
    str
        A complete ``.mdc`` document string.
    """
    lines: list[str] = [
        "---",
        "description: MoonMind task instructions",
        'globs: "**/*"',
        "alwaysApply: true",
        "---",
        "",
        "# Task Instructions",
        "",
        instruction.strip(),
    ]

    if skill_context and skill_context.strip():
        lines.extend([
            "",
            "# Skill Context",
            "",
            skill_context.strip(),
        ])

    lines.append("")  # Trailing newline.
    return "\n".join(lines)


def write_task_rule_file(
    workspace_path: Path,
    instruction: str,
    skill_context: str | None = None,
) -> Path:
    """Generate and write ``.cursor/rules/moonmind-task.mdc`` in the workspace.

    Creates the ``.cursor/rules`` directory if it does not exist.

    Returns the path to the written file.
    """
    content = generate_task_rule_content(instruction, skill_context)
    rules_dir = workspace_path / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    mdc_path = rules_dir / "moonmind-task.mdc"
    mdc_path.write_text(content, encoding="utf-8")
    logger.info("Wrote Cursor task rule to %s", mdc_path)
    return mdc_path
