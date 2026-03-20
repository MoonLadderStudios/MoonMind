"""Unit tests for Cursor CLI rules and context injection."""

from moonmind.agents.base.cursor_rules import (
    generate_task_rule_content,
    write_task_rule_file,
)


def test_generate_basic_instruction():
    """Generate MDC with instruction only."""
    content = generate_task_rule_content("Implement the login feature")
    assert "---" in content
    assert "description: MoonMind task instructions" in content
    assert 'globs: "**/*"' in content
    assert "alwaysApply: true" in content
    assert "# Task Instructions" in content
    assert "Implement the login feature" in content
    # Should NOT have skill context section
    assert "# Skill Context" not in content


def test_generate_with_skill_context():
    """Generate MDC with instruction and skill context."""
    content = generate_task_rule_content(
        instruction="Fix the bug",
        skill_context="Use the debug skill to trace the issue",
    )
    assert "# Task Instructions" in content
    assert "Fix the bug" in content
    assert "# Skill Context" in content
    assert "Use the debug skill to trace the issue" in content


def test_generate_empty_skill_context_omitted():
    """Empty/whitespace skill context is omitted."""
    content = generate_task_rule_content("Do the task", skill_context="   ")
    assert "# Skill Context" not in content


def test_generate_none_skill_context_omitted():
    """None skill context is omitted."""
    content = generate_task_rule_content("Do the task", skill_context=None)
    assert "# Skill Context" not in content


def test_frontmatter_format():
    """MDC has proper YAML frontmatter delimiters."""
    content = generate_task_rule_content("Test")
    lines = content.split("\n")
    assert lines[0] == "---"
    # Find the closing frontmatter delimiter
    closing = [i for i, line in enumerate(lines) if line == "---" and i > 0]
    assert len(closing) == 1
    assert closing[0] == 4  # line index 4 (5th line)


def test_instruction_whitespace_stripped():
    """Leading/trailing whitespace in instruction is stripped."""
    content = generate_task_rule_content("  hello world  ")
    assert "hello world" in content
    assert "  hello world  " not in content


def test_write_task_rule_file(tmp_path):
    """Write MDC to .cursor/rules/moonmind-task.mdc."""
    result_path = write_task_rule_file(
        tmp_path,
        instruction="Build the feature",
        skill_context="Follow TDD",
    )

    assert result_path.exists()
    assert result_path.name == "moonmind-task.mdc"
    assert result_path.parent.name == "rules"
    assert result_path.parent.parent.name == ".cursor"

    content = result_path.read_text()
    assert "Build the feature" in content
    assert "Follow TDD" in content


def test_write_task_rule_file_creates_dirs(tmp_path):
    """Write creates .cursor/rules directory automatically."""
    workspace = tmp_path / "deep" / "workspace"
    workspace.mkdir(parents=True)

    result_path = write_task_rule_file(workspace, instruction="Test task")
    assert result_path.exists()
    assert (workspace / ".cursor" / "rules" / "moonmind-task.mdc").exists()
