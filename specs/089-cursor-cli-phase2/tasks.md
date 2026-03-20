# Tasks: Cursor CLI Phase 2 — Adapter Wiring

**Input**: Design documents from `/specs/089-cursor-cli-phase2/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Include exact file paths in descriptions

---

## Phase 1: Setup

- [ ] T001 Create feature tracking branch `089-cursor-cli-phase2` (already done)

---

## Phase 2: Tests First (TDD)

**Purpose**: Write tests before implementation to validate expected behavior.

- [ ] T002 [P] [US1] Add unit test `test_resolve_volume_mount_env_cursor` in `tests/unit/agents/base/test_adapter.py` — verify `cursor_cli` runtime maps to `CURSOR_CONFIG_DIR` env var (DOC-REQ-P2-001)
- [ ] T003 [P] [US2] Add unit test `test_shape_agent_environment_oauth_includes_cursor_key` in `tests/unit/agents/base/test_adapter.py` — verify `CURSOR_API_KEY` is scrubbed in OAuth mode (DOC-REQ-P2-002)
- [ ] T004 [P] [US3] Add unit test `test_build_command_cursor_cli` in `tests/unit/services/temporal/runtime/test_launcher.py` — verify cursor-specific flags `-p`, `--output-format stream-json`, `--force` (DOC-REQ-P2-003)
- [ ] T005 [P] [US3] Add unit test `test_build_command_cursor_cli_with_sandbox` in `tests/unit/services/temporal/runtime/test_launcher.py` — verify `--sandbox` flag from request parameters (DOC-REQ-P2-003)
- [ ] T006 [P] [US4] Create new test file `tests/unit/agents/base/test_ndjson_parser.py` with tests for NDJSON event parsing (DOC-REQ-P2-004)

---

## Phase 3: Core Implementation

**Purpose**: Implement production code changes to pass the tests.

### User Story 1 — Volume Mount Resolution

- [ ] T007 [US1] Add `cursor_cli` branch to `resolve_volume_mount_env()` in `moonmind/agents/base/adapter.py` — set `CURSOR_CONFIG_DIR` env var (DOC-REQ-P2-001)

### User Story 2 — API Key Scrubbing

- [ ] T008 [US2] Add `CURSOR_API_KEY` to `oauth_scrubbable_keys` list in `shape_agent_environment()` in `moonmind/agents/base/adapter.py` (DOC-REQ-P2-002)

### User Story 3 — Command Construction

- [ ] T009 [US3] Add `cursor_cli` branch to `build_command()` in `moonmind/workflows/temporal/runtime/launcher.py` — handle `-p`, `--output-format stream-json`, `--force`, `--model`, `--sandbox` flags (DOC-REQ-P2-003)

### User Story 4 — NDJSON Output Parser

- [ ] T010 [US4] Create `moonmind/agents/base/ndjson_parser.py` with `CursorStreamEvent` dataclass, `parse_ndjson_line()`, and `parse_ndjson_stream()` functions (DOC-REQ-P2-004)

---

## Phase 4: Registration & Documentation

### User Story 5 — Worker Fleet Registration

- [ ] T011 [US5] Document that `cursor_cli` requires no changes to `activity_catalog.py` — dispatches via existing `agent_runtime` fleet profile system (DOC-REQ-P2-005)

---

## Phase 5: Validation & Polish

- [ ] T012 Run `./tools/test_unit.sh` to verify all existing and new tests pass
- [ ] T013 Verify all DOC-REQ-P2-* identifiers have at least one [X] implementation task and one [X] validation task

---

## Dependencies & Execution Order

### Task Dependencies

- T002–T006 (tests) can all run in parallel — different files
- T007–T008 (adapter changes) are sequential within one file, but can run parallel with T009–T010
- T009 (launcher) and T010 (parser) are independent files — can run in parallel
- T011 (documentation) is independent
- T012–T013 (validation) depend on all implementation tasks

### Parallel Opportunities

```bash
# These can be worked on simultaneously:
Task: "T002–T003 [Adapter tests]"
Task: "T004–T005 [Launcher tests]"
Task: "T006 [Parser tests]"
```

```bash
# And these:
Task: "T007–T008 [Adapter implementation]"
Task: "T009 [Launcher implementation]"
Task: "T010 [Parser implementation]"
```
