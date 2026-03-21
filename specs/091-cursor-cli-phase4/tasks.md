# Tasks: Cursor CLI Phase 4 — Permission and Context Integration

**Input**: Design documents from `/specs/091-cursor-cli-phase4/`
**Prerequisites**: plan.md (required), spec.md (required), research.md

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Create feature branch `091-cursor-cli-phase4` (already done)

---

## Phase 2: Tests First (TDD)

- [ ] T002 [P] [US1] Create `tests/unit/agents/base/test_cursor_config.py` with tests for permission config generation: `full_autonomy`, `supervised`, `restricted` levels, custom allow/deny rules, and file writing (DOC-REQ-P4-001)
- [ ] T003 [P] [US2] Create `tests/unit/agents/base/test_cursor_rules.py` with tests for MDC rule generation: basic instruction, with skill context, frontmatter format, and file writing (DOC-REQ-P4-002)

---

## Phase 3: Core Implementation

- [ ] T004 [P] [US1] Create `moonmind/agents/base/cursor_config.py` with `generate_cursor_cli_json()` and `write_cursor_cli_json()` (DOC-REQ-P4-001)
- [ ] T005 [P] [US2] Create `moonmind/agents/base/cursor_rules.py` with `generate_task_rule_content()` and `write_task_rule_file()` (DOC-REQ-P4-002)

---

## Phase 4: Documentation

- [ ] T006 Document MCP config deferral to Phase 5 (DOC-REQ-P4-003)

---

## Phase 5: Validation

- [ ] T007 Run `./tools/test_unit.sh` to verify all tests pass
- [ ] T008 Verify all DOC-REQ-P4-* identifiers have implementation/documentation coverage
