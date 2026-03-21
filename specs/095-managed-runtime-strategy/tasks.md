# Tasks: Managed Runtime Strategy Pattern — Phase 1

**Input**: Design documents from `/specs/095-managed-runtime-strategy/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, contracts/requirements-traceability.md, quickstart.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Create package structure for strategies

- [x] T001 Create `moonmind/workflows/temporal/runtime/strategies/__init__.py` with `RUNTIME_STRATEGIES` dict and `get_strategy()` helper (DOC-REQ-002 / FR-004)
- [x] T002 [P] Create `tests/unit/workflows/temporal/runtime/strategies/__init__.py` as empty test package

---

## Phase 2: Foundational — ABC Definition

**Purpose**: Define the strategy contract that all runtimes implement

**⚠️ CRITICAL**: No strategy implementations can begin until this phase is complete

- [x] T003 Create `ManagedRuntimeStrategy` ABC in `moonmind/workflows/temporal/runtime/strategies/base.py` with abstract `runtime_id`, `default_command_template`, `build_command()` and concrete defaults for `default_auth_mode`, `shape_environment()`, `prepare_workspace()`, `classify_exit()`, `create_output_parser()` (DOC-REQ-001 / FR-001, FR-002, FR-003)
- [x] T004 [P] Create unit tests for ABC in `tests/unit/workflows/temporal/runtime/strategies/test_base.py` — verify ABC cannot be instantiated; verify concrete defaults return expected values (DOC-REQ-001 validation)

**Checkpoint**: ABC is defined and tested. Strategy implementations can now begin.

---

## Phase 3: User Story 1 — Strategy Registration & Launcher Delegation (Priority: P1) 🎯 MVP

**Goal**: Register `GeminiCliStrategy` and have the launcher delegate to it for `gemini_cli` runtimes.

**Independent Test**: Call `build_command()` through the launcher with a `gemini_cli` profile and verify it produces the same CLI arguments as the existing `if/elif` path.

### Implementation for User Story 1

- [x] T005 [US1] Implement `GeminiCliStrategy` in `moonmind/workflows/temporal/runtime/strategies/gemini_cli.py` — extract command construction from `launcher.py:342-351`, set `runtime_id="gemini_cli"`, `default_command_template=["gemini"]`, `default_auth_mode="api_key"` (DOC-REQ-003 / FR-005)
- [x] T006 [US1] Register `GeminiCliStrategy` in `RUNTIME_STRATEGIES` dict in `moonmind/workflows/temporal/runtime/strategies/__init__.py` (DOC-REQ-002 / FR-004)
- [x] T007 [US1] Modify `ManagedRuntimeLauncher.build_command()` in `moonmind/workflows/temporal/runtime/launcher.py` to check `RUNTIME_STRATEGIES` before `if/elif` block — delegate if strategy found, fallthrough otherwise (DOC-REQ-004 / FR-007)
- [x] T008 [P] [US1] Create unit tests for `GeminiCliStrategy` in `tests/unit/workflows/temporal/runtime/strategies/test_gemini_cli.py` — verify `build_command()` output matches existing launcher output for various input combinations (DOC-REQ-003 validation)
- [x] T009 [US1] Create unit test in `tests/unit/workflows/temporal/runtime/strategies/test_gemini_cli.py` verifying launcher delegation for registered runtime and fallthrough for unregistered runtime (DOC-REQ-004 validation)

**Checkpoint**: Gemini CLI commands are built by the strategy. Other runtimes still use `if/elif`.

---

## Phase 4: User Story 2 — Strategy-Driven Adapter Defaults (Priority: P1)

**Goal**: `ManagedAgentAdapter.start()` reads `default_command_template` and `default_auth_mode` from the strategy registry.

**Independent Test**: Call `start()` with a `gemini_cli` request and verify defaults come from the strategy.

### Implementation for User Story 2

- [x] T010 [US2] Modify `ManagedAgentAdapter.start()` in `moonmind/workflows/adapters/managed_agent_adapter.py` to check `RUNTIME_STRATEGIES` for `command_template` defaults before the `if/elif` at L286-293 (DOC-REQ-005 / FR-008)
- [x] T011 [US2] Modify `ManagedAgentAdapter.start()` in `moonmind/workflows/adapters/managed_agent_adapter.py` to check `RUNTIME_STRATEGIES` for `auth_mode` defaults before the branching at L236-238 (DOC-REQ-005 / FR-008)
- [x] T012 [P] [US2] Add unit tests in `tests/unit/workflows/adapters/test_managed_agent_adapter.py` verifying adapter reads defaults from strategy when registered (DOC-REQ-005 validation)

**Checkpoint**: Both launcher and adapter delegate to the strategy for Gemini CLI.

---

## Phase 5: User Story 3 — Environment Shaping via Strategy (Priority: P2)

**Goal**: `GeminiCliStrategy.shape_environment()` handles Gemini-specific env passthrough.

**Independent Test**: Invoke `shape_environment()` and verify `GEMINI_HOME` and `GEMINI_CLI_HOME` are passed through.

### Implementation for User Story 3

- [x] T013 [US3] Implement `GeminiCliStrategy.shape_environment()` in `moonmind/workflows/temporal/runtime/strategies/gemini_cli.py` to pass through `HOME`, `GEMINI_HOME`, `GEMINI_CLI_HOME` from the worker environment (DOC-REQ-006 / FR-006)
- [x] T014 [P] [US3] Add unit tests in `tests/unit/workflows/temporal/runtime/strategies/test_gemini_cli.py` verifying `shape_environment()` passes through expected keys and excludes unrelated keys (DOC-REQ-006 validation)

**Checkpoint**: Gemini-specific env shaping is strategy-driven.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Regression verification and cleanup

- [x] T015 Run `./tools/test_unit.sh` and verify all existing tests pass without modification (DOC-REQ-007 / FR-009)
- [x] T016 Verify no changes were made to `moonmind/workflows/temporal/runtime/supervisor.py` (DOC-REQ-007 / FR-010)
- [x] T017 [P] Run quickstart.md validation to verify strategy registration and launcher delegation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (Phase 2)
- **User Story 2 (Phase 4)**: Depends on Phase 3 (strategy must be registered before adapter reads it)
- **User Story 3 (Phase 5)**: Can start after Phase 2, independent of Phases 3-4
- **Polish (Phase 6)**: Depends on all user stories

### Parallel Opportunities

- T001 and T002 can run in parallel (setup)
- T003 and T004 can run in parallel (ABC + test)
- T005 and T008 can run in parallel (implementation + test)
- T013 and T014 can run in parallel (env shaping + test)
- T015, T016, T017 can all run in parallel (verification)

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational ABC
3. Complete Phase 3: User Story 1 (launcher delegation)
4. **STOP and VALIDATE**: Run strategy tests + regression suite
5. Ship if ready

### Incremental Delivery

1. Setup + Foundational → ABC tested ✓
2. Add User Story 1 → Launcher delegates to strategy ✓
3. Add User Story 2 → Adapter delegates to strategy ✓
4. Add User Story 3 → Env shaping via strategy ✓
5. Each story adds value without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each phase
- Stop at any checkpoint to validate
- All DOC-REQ IDs are tagged for traceability
