# Spec: Cursor CLI Phase 4 — Permission and Context Integration

**Source Document**: [CursorCli.md](file:///Users/nsticco/MoonMind/docs/ManagedAgents/CursorCli.md)
**Phase**: 4 of 5

---

## Document Requirement Identifiers

| ID | Source Section | Requirement |
|----|---------------|-------------|
| DOC-REQ-P4-001 | §6 Permission Mapping | Generate `.cursor/cli.json` with `allow`/`deny` rule lists from `approval_policy` |
| DOC-REQ-P4-002 | §7 Rules and Context | Generate `.cursor/rules/moonmind-task.mdc` with task instructions and skill constraints |
| DOC-REQ-P4-003 | §8 MCP Integration | Wire MCP configuration for MoonMind context servers (optional, deferred) |

---

## User Stories

### US1: Permission Config Generation
**As a** MoonMind managed runtime launcher  
**I want** the agent's `approval_policy` converted to a `.cursor/cli.json` permission file  
**So that** Cursor CLI enforces the correct Shell/Read/Write/WebFetch/Mcp permissions

### US2: Skill Rule Injection
**As a** MoonMind task orchestrator  
**I want** task instructions and skill context written to `.cursor/rules/moonmind-task.mdc`  
**So that** Cursor CLI loads them as project rules before executing the agent prompt

---

## Functional Requirements

### Permission Config

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-001 | `generate_cursor_cli_json()` accepts `approval_policy` dict and returns a dict with `permissions.allow` and `permissions.deny` lists | DOC-REQ-P4-001 | Unit test |
| FR-002 | Default `full_autonomy` policy generates permissive allow rules (`Read(**)`, `Write(**)`, `Shell(**)`) | DOC-REQ-P4-001 | Unit test |
| FR-003 | `supervised` policy generates `Read(**)`, `Write(**)` with specific `Shell` allow rules | DOC-REQ-P4-001 | Unit test |
| FR-004 | `restricted` policy generates minimal permissions per policy content | DOC-REQ-P4-001 | Unit test |
| FR-005 | `write_cursor_cli_json()` writes the config dict to `.cursor/cli.json` in the workspace | DOC-REQ-P4-001 | Unit test |

### Rules Injection

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-006 | `generate_task_rule_content()` produces a `.mdc` string from task instruction and optional skill context | DOC-REQ-P4-002 | Unit test |
| FR-007 | Generated MDC content includes frontmatter with `description` and `globs` fields | DOC-REQ-P4-002 | Unit test |
| FR-008 | `write_task_rule_file()` writes the MDC content to `.cursor/rules/moonmind-task.mdc` in the workspace | DOC-REQ-P4-002 | Unit test |

---

## Success Criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| SC-1 | `generate_cursor_cli_json({"level": "full_autonomy"})` returns permissive config | Unit test |
| SC-2 | `generate_cursor_cli_json({"level": "restricted", ...})` returns restrictive config | Unit test |
| SC-3 | `write_cursor_cli_json()` creates `.cursor/cli.json` file with valid JSON | Unit test |
| SC-4 | `generate_task_rule_content("prompt", "skill_context")` returns valid MDC string | Unit test |
| SC-5 | `write_task_rule_file()` creates `.cursor/rules/moonmind-task.mdc` | Unit test |
| SC-6 | All existing unit tests continue to pass (`./tools/test_unit.sh`) | CLI verification |
