# Tasks: Normalize Slash-Leading Instructions

**Input**: Design documents from `specs/353-normalize-slash-commands/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/runtime-command-snapshot.md`, `quickstart.md`

**Tests**: Unit tests and integration-shaped task contract tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-684 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: This task list covers FR-001 through FR-011, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-008, DESIGN-REQ-010, and DESIGN-REQ-019 from `spec.md`.

**Test Commands**:

- Unit tests: `pytest tests/unit/workflows/tasks/test_task_contract.py -q`
- Integration tests: `pytest tests/unit/workflows/tasks/test_task_contract.py -q` for the canonical payload plus authoritative snapshot boundary; run `./tools/test_integration.sh` only if implementation expands into API/workflow submission code.
- Final verification: `/speckit.verify specs/353-normalize-slash-commands`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing task contract module and tests are the only required implementation surface.

- [X] T001 Inspect current task contract parsing/snapshot helpers in `moonmind/workflows/tasks/task_contract.py` and record any additional touched helpers in `specs/353-normalize-slash-commands/research.md` if scope changes.
- [X] T002 Inspect existing task contract tests in `tests/unit/workflows/tasks/test_task_contract.py` and place all MM-684 regression tests in that file unless a new fixture file becomes necessary.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the exact contract and red-first test shape before story implementation.

**CRITICAL**: No production implementation work can begin until the failing tests in Phase 3 are written and confirmed.

- [X] T003 Confirm `contracts/runtime-command-snapshot.md` matches the final field names to be asserted by tests for FR-003, FR-004, and DESIGN-REQ-004.
- [X] T004 Confirm no migration, database table, or external service setup is required for MM-684 in `specs/353-normalize-slash-commands/plan.md`.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Authoritative Runtime Command Snapshots

**Summary**: As a MoonMind operator submitting a task, I want slash-leading task and step instructions to remain exactly as authored while MoonMind records authoritative structured runtime command metadata, so managed runtimes can recognize commands without losing audit fidelity.

**Independent Test**: Submit or normalize task inputs containing slash-leading task instructions, slash-leading step instructions, unknown valid commands, escaped slash text, malformed path-like text, and inconsistent frontend-supplied command metadata; verify the resulting authoritative snapshot contains the expected preserved instructions, command metadata, warnings or rejections, and traceable source paths.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-019

**Test Plan**:

- Unit: parser grammar, escaped literals, opaque unknown commands, path-like malformed inputs, unsupported runtime policy, hint status, supplied metadata validation.
- Integration: canonical payload parsing plus `build_authoritative_task_input_snapshot()` boundary for objective and step runtime command metadata.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T005 Add failing unit tests for task-level `/review` metadata preserving raw instructions in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-001, FR-002, FR-003, SC-001, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004.
- [X] T006 Add failing unit tests for step-level `/simplify` metadata with `targetStepId` and `steps[0].instructions` source path in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-004, SC-002, DESIGN-REQ-005.
- [X] T007 Add failing unit tests for unknown valid commands and opaque provider command lines in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-005, FR-006, FR-010, SC-003, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-019.
- [X] T008 Add failing unit tests for escaped slash input and malformed ordinary path-like input in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-007, FR-009, SC-004, SC-005, DESIGN-REQ-008.
- [X] T009 Add failing unit tests for conflicting and malformed frontend-supplied `runtimeCommand` metadata in objective and step payloads in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-008, SC-006, DESIGN-REQ-010.
- [X] T010 Run `pytest tests/unit/workflows/tasks/test_task_contract.py -q` and confirm T005-T009 fail for missing runtime command behavior before production changes.

### Integration Tests (write first)

- [X] T011 Add failing canonical payload boundary tests using `CanonicalTaskPayload.model_validate()` plus `build_authoritative_task_input_snapshot()` in `tests/unit/workflows/tasks/test_task_contract.py` covering objective and step acceptance scenarios SC-001 and SC-002.
- [X] T012 Add failing canonical payload boundary tests for missing frontend metadata, conflicting metadata rejection, unknown command acceptance, and escaped literal metadata in `tests/unit/workflows/tasks/test_task_contract.py` covering SC-003, SC-004, SC-006.
- [X] T013 Run `pytest tests/unit/workflows/tasks/test_task_contract.py -q` and confirm T011-T012 fail for the expected missing boundary behavior.

### Implementation

- [X] T014 Add runtime command constants, supported slash-capable runtime classification, known hint identifiers, and default policy values in `moonmind/workflows/tasks/task_contract.py` covering FR-006, FR-009, FR-010, DESIGN-REQ-007, DESIGN-REQ-019.
- [X] T015 Implement an internal runtime command parser in `moonmind/workflows/tasks/task_contract.py` covering grammar parsing, arguments, instruction body, opaque command lines, escaped literals, leading whitespace, empty instructions, and path-like malformed lines for FR-005, FR-007, DESIGN-REQ-006, DESIGN-REQ-008.
- [X] T016 Implement runtime command metadata construction for objective instructions in `moonmind/workflows/tasks/task_contract.py` covering FR-001, FR-002, FR-003, SC-001, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-004.
- [X] T017 Implement runtime command metadata construction for step instructions in `moonmind/workflows/tasks/task_contract.py` covering FR-004, SC-002, DESIGN-REQ-005.
- [X] T018 Implement backend validation for supplied objective and step `runtimeCommand` metadata in `moonmind/workflows/tasks/task_contract.py` covering FR-008, SC-006, DESIGN-REQ-010.
- [X] T019 Wire objective and step `runtimeCommand` output into `build_authoritative_task_input_snapshot()` in `moonmind/workflows/tasks/task_contract.py` without rewriting existing `instructions`, `inputAttachments`, dependency, provenance, or traceability fields for FR-001 through FR-010.
- [X] T020 Run `pytest tests/unit/workflows/tasks/test_task_contract.py -q`, fix failures, and verify all MM-684 task contract tests pass.
- [X] T021 Run `./tools/test_unit.sh` for final required unit verification and record the result in `specs/353-normalize-slash-commands/verification.md` or final response.

**Checkpoint**: The story is fully functional, covered by unit and integration-shaped task contract tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T022 Review `moonmind/workflows/tasks/task_contract.py` for dead helper names, compatibility aliases, or stale comments introduced during implementation and remove them for Constitution XIII.
- [X] T023 Review `specs/353-normalize-slash-commands/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-command-snapshot.md`, and `quickstart.md` for MM-684 traceability and update only if implementation reality changed.
- [X] T024 Run quickstart validation from `specs/353-normalize-slash-commands/quickstart.md`.
- [X] T025 Run `/speckit.verify specs/353-normalize-slash-commands` after implementation and tests pass.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing.

### Within The Story

- T005-T009 unit tests and T011-T012 boundary tests must be written before implementation.
- T010 and T013 red-first confirmation must complete before T014-T019.
- T014-T015 parser/policy work precedes T016-T019 snapshot wiring.
- T020 targeted tests precede T021 full unit verification.
- T025 final verification runs only after implementation and tests pass.

### Parallel Opportunities

- T001 and T002 can be done in parallel.
- T005-T009 can be authored together in the same file only with careful merge ordering; otherwise do them sequentially to avoid conflicts.
- T014 and T015 are tightly coupled and should be implemented together.
- T023 documentation review can run after T020 while full unit verification T021 is running.

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational confirmation.
2. Write failing unit and boundary tests in `tests/unit/workflows/tasks/test_task_contract.py`.
3. Confirm tests fail for missing runtime command metadata/validation.
4. Implement parser, metadata construction, supplied metadata validation, and snapshot wiring in `moonmind/workflows/tasks/task_contract.py`.
5. Run targeted tests until green.
6. Run `./tools/test_unit.sh`.
7. Run final `/speckit.verify` and preserve MM-684 traceability in verification evidence.

## Notes

- This task list covers one story only: backend authoritative runtime command snapshots for slash-leading instructions.
- Provider-neutral Create page previews remain out of scope for MM-684 and are tracked by related issue MM-685.
- Runtime-specific command rendering remains out of scope except for metadata required by later adapter-owned rendering.
