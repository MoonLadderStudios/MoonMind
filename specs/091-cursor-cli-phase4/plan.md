# Implementation Plan: Cursor CLI Phase 4 — Permission and Context Integration

## Technical Context

Phase 4 creates pure-function utility modules for generating Cursor CLI workspace configuration files before agent launch. These are stateless functions that convert MoonMind's `approval_policy` dict and task instructions into Cursor-specific config files.

## Constitution Check

- ✅ No credentials committed
- ✅ No compatibility transforms
- ✅ Fail-fast for unsupported values

## Proposed Changes

### Cursor Config Generator

#### [NEW] [cursor_config.py](file:///Users/nsticco/MoonMind/moonmind/agents/base/cursor_config.py)

Pure-function module for generating `.cursor/cli.json` permission configs:

- `generate_cursor_cli_json(approval_policy: dict) -> dict` — converts approval policy to Cursor permission format
- `write_cursor_cli_json(workspace_path: Path, approval_policy: dict) -> Path` — writes config to file

Policy level mapping:
| MoonMind Level | Cursor Behavior |
|---|---|
| `full_autonomy` | `allow: [Read(**), Write(**), Shell(**)]`, empty deny |
| `supervised` | `allow: [Read(**), Write(**)]`, specific shell commands from policy |
| `restricted` | Only explicitly listed permissions from policy |

### Cursor Rules Generator

#### [NEW] [cursor_rules.py](file:///Users/nsticco/MoonMind/moonmind/agents/base/cursor_rules.py)

Pure-function module for generating `.cursor/rules/moonmind-task.mdc` files:

- `generate_task_rule_content(instruction: str, skill_context: str | None = None) -> str` — produces MDC frontmatter + body
- `write_task_rule_file(workspace_path: Path, instruction: str, skill_context: str | None = None) -> Path` — writes `.mdc` file

MDC format:
```
---
description: MoonMind task instructions
globs: "**/*"
---
# Task Instructions
{instruction}

# Skill Context
{skill_context}
```

### Tests

#### [NEW] [test_cursor_config.py](file:///Users/nsticco/MoonMind/tests/unit/agents/base/test_cursor_config.py)

Tests for permission config generation (all 3 policy levels, file writing).

#### [NEW] [test_cursor_rules.py](file:///Users/nsticco/MoonMind/tests/unit/agents/base/test_cursor_rules.py)

Tests for rules MDC generation (basic instruction, with skill context, file writing).

---

## Verification Plan

### Automated Tests

```bash
./tools/test_unit.sh
```

All tests run via the standard unit test script. New tests validate:
1. Config generation for each policy level
2. Custom permission rules
3. File I/O operations (using `tmp_path` fixtures)
4. MDC frontmatter format
5. Instruction + skill context concatenation

### Manual Verification

None required — pure functions with unit test coverage.
