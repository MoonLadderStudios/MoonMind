# Tasks: Claude Checkpoints Rewind

**Input**: Design documents from `specs/187-claude-checkpoints-rewind/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-346 / STORY-005 user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Tasks reference FR-001 through FR-017, acceptance scenarios 1-8, SC-001 through SC-006, and DESIGN-REQ-016, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-028, DESIGN-REQ-029, and DESIGN-REQ-030 from `spec.md`.

**Test Commands**:

- Unit tests: `pytest tests/unit/schemas/test_claude_checkpoints.py -q`
- Integration tests: `pytest tests/integration/schemas/test_claude_checkpoints_boundary.py -q`
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Hermetic integration verification: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active artifacts and existing schema/test structure.

- [X] T001 Confirm active feature artifacts for MM-346 in specs/187-claude-checkpoints-rewind/spec.md, specs/187-claude-checkpoints-rewind/plan.md, specs/187-claude-checkpoints-rewind/research.md, specs/187-claude-checkpoints-rewind/data-model.md, and specs/187-claude-checkpoints-rewind/contracts/claude-checkpoints-rewind.md
- [X] T002 Confirm existing Claude schema exports and test locations in moonmind/schemas/managed_session_models.py, moonmind/schemas/__init__.py, tests/unit/schemas/, and tests/integration/schemas/

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Identify existing validation helpers and fixture patterns before story work begins.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Review compact metadata validation helpers used by existing Claude context and decision records in moonmind/schemas/managed_session_models.py for FR-007, FR-008, FR-014, and DESIGN-REQ-021
- [X] T004 Review existing Claude schema unit and integration-style fixture patterns in tests/unit/schemas/test_claude_context_snapshots.py, tests/unit/schemas/test_claude_managed_session_models.py, and tests/integration/schemas/test_claude_context_snapshots_boundary.py

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Claude Checkpoints And Rewind

**Summary**: As a user recovering from an unwanted change, I want Claude checkpoints, restore modes, summarize-from-here, and rewind lineage exposed through the session plane so that recovery is visible and provenance-preserving without replacing git history.

**Independent Test**: Drive user-prompt, tracked file-edit, bash-side-effect, and manual-edit checkpoint cases through the checkpoint boundary; list checkpoints; restore code, conversation, both, and summarize-from-here; then assert capture rules, active cursor updates, rewind lineage, event log preservation, and payload pointer behavior.

**Traceability**: FR-001 through FR-017; acceptance scenarios 1-8; SC-001 through SC-006; DESIGN-REQ-016, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-028, DESIGN-REQ-029, DESIGN-REQ-030.

**Test Plan**:

- Unit: trigger validation, capture-rule defaults, checkpoint metadata bounds, rewind mode validation, lineage invariants, summary-from-here guardrails, and work-event validation.
- Integration: representative checkpoint capture and rewind boundary flow with checkpoint index, work items, active cursor changes, preserved event-log reference, and summary artifact behavior.

### Unit Tests (write first)

- [X] T005 [P] Add failing unit tests for documented checkpoint triggers and capture-rule defaults in tests/unit/schemas/test_claude_checkpoints.py covering FR-003, FR-004, FR-005, FR-006, SC-001, and DESIGN-REQ-016
- [X] T006 [P] Add failing unit tests for Checkpoint metadata, CheckpointIndex active cursor validation, and compact payload rejection in tests/unit/schemas/test_claude_checkpoints.py covering FR-001, FR-002, FR-007, FR-008, FR-009, FR-014, SC-003, and DESIGN-REQ-021
- [X] T007 [P] Add failing unit tests for rewind mode validation and result lineage invariants in tests/unit/schemas/test_claude_checkpoints.py covering FR-010, FR-013, FR-015, FR-017, SC-002, and DESIGN-REQ-029
- [X] T008 [P] Add failing unit tests for summarize-from-here summary artifact behavior in tests/unit/schemas/test_claude_checkpoints.py covering FR-016, SC-005, DESIGN-REQ-028, and DESIGN-REQ-030
- [X] T009 [P] Add failing unit tests for checkpoint and rewind work-item event names in tests/unit/schemas/test_claude_checkpoints.py covering FR-011, FR-012, SC-004, DESIGN-REQ-020, and DESIGN-REQ-028
- [X] T010 Run `pytest tests/unit/schemas/test_claude_checkpoints.py -q` and confirm T005-T009 fail for missing checkpoint/rewind contracts before implementation

### Integration Tests (write first)

- [X] T011 [P] Add failing integration-style boundary test for representative user-prompt, file-edit, bash-side-effect, manual-edit, rewind, and summarize-from-here flow in tests/integration/schemas/test_claude_checkpoints_boundary.py covering acceptance scenarios 1-8, FR-001 through FR-017, SC-004, and DESIGN-REQ-016 through DESIGN-REQ-030
- [X] T012 Run `pytest tests/integration/schemas/test_claude_checkpoints_boundary.py -q` and confirm T011 fails for missing checkpoint/rewind contracts before implementation

### Implementation

- [X] T013 Add Claude checkpoint trigger, capture mode, retention state, status, rewind mode, rewind status, and checkpoint work-event literal contracts plus exported documented tuples in moonmind/schemas/managed_session_models.py covering FR-003, FR-010, FR-017, and DESIGN-REQ-016
- [X] T014 Add ClaudeCheckpoint, ClaudeCheckpointCaptureDecision, and claude_checkpoint_capture_decision helper behavior in moonmind/schemas/managed_session_models.py covering FR-001 through FR-006, FR-014, and DESIGN-REQ-016
- [X] T015 Add ClaudeCheckpointIndex validation in moonmind/schemas/managed_session_models.py covering FR-007, FR-008, FR-009, and DESIGN-REQ-021
- [X] T016 Add ClaudeRewindRequest and ClaudeRewindResult validation in moonmind/schemas/managed_session_models.py covering FR-010, FR-013, FR-015, FR-016, FR-017, DESIGN-REQ-028, DESIGN-REQ-029, and DESIGN-REQ-030
- [X] T017 Add create_claude_checkpoint_work_item and create_claude_rewind_work_items helper behavior plus work event validation in moonmind/schemas/managed_session_models.py covering FR-011, FR-012, SC-004, DESIGN-REQ-020, and DESIGN-REQ-028
- [X] T018 Export the new Claude checkpoint contracts from moonmind/schemas/managed_session_models.py and moonmind/schemas/__init__.py covering the public schema contract in specs/187-claude-checkpoints-rewind/contracts/claude-checkpoints-rewind.md
- [X] T019 Run `pytest tests/unit/schemas/test_claude_checkpoints.py tests/integration/schemas/test_claude_checkpoints_boundary.py -q`, fix failures, and verify the MM-346 story passes focused unit and integration-style checks

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T020 [P] Review specs/187-claude-checkpoints-rewind/quickstart.md against implemented commands and update only if command evidence or blockers changed
- [X] T021 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final required unit verification and record the result in this task list
- [X] T022 Run `./tools/test_integration.sh` when Docker is available, or record the exact Docker/socket blocker in this task list
- [X] T023 Run `/moonspec-verify` equivalent read-only verification against specs/187-claude-checkpoints-rewind/spec.md after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on focused story tests passing.

### Within The Story

- Unit tests T005-T009 must be written before implementation tasks T013-T018.
- Integration test T011 must be written before implementation tasks T013-T018.
- Red-first confirmation tasks T010 and T012 must complete before production code tasks.
- Literal contracts and tuples T013 precede model/helper implementation T014-T017.
- Exports T018 follow model/helper implementation.
- Focused validation T019 follows implementation and exports.

### Parallel Opportunities

- T005-T009 can be authored in parallel within the same test file only if edits are coordinated carefully; otherwise keep them sequential to avoid conflicts.
- T011 can be authored in parallel with unit test tasks because it touches a different file.
- T020 can run in parallel with final verification preparation after focused tests pass.

---

## Parallel Example: Story Phase

```bash
# Different files, safe to parallelize if multiple agents are coordinating:
Task: "Add failing unit tests in tests/unit/schemas/test_claude_checkpoints.py"
Task: "Add failing integration boundary test in tests/integration/schemas/test_claude_checkpoints_boundary.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 context checks.
2. Write unit tests and integration-style boundary tests first.
3. Run focused tests and confirm they fail for missing contracts.
4. Implement compact Pydantic contracts and deterministic helpers in the existing schema boundary.
5. Export the new contract surface.
6. Run focused tests until they pass.
7. Run full unit verification and hermetic integration verification when available.
8. Run final `/moonspec-verify` equivalent against the MM-346 spec.

---

## Notes

- This task list covers exactly one story: MM-346 / STORY-005.
- No live Claude provider calls, persistent storage, central checkpoint payload storage, git-history replacement, or provider-specific restore mechanics are in scope.
- Checkpoint payloads must remain pointer-based and compact by default.
- Agent context update blocker: `.specify/scripts/bash/update-agent-context.sh codex` looked for `specs/mm-346-efccaeaf/plan.md` from the managed branch name instead of the active feature directory `specs/187-claude-checkpoints-rewind`.
- Verification evidence: red-first unit test collection failed for missing `CLAUDE_CHECKPOINT_CAPTURE_MODES`; red-first integration test collection failed for missing `ClaudeCheckpoint`; focused tests passed with `pytest tests/unit/schemas/test_claude_checkpoints.py tests/integration/schemas/test_claude_checkpoints_boundary.py -q` (14 passed); full unit verification passed with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` (3349 Python tests passed, 1 xpassed, 16 subtests passed; 222 frontend tests passed); `./tools/test_integration.sh` was blocked by missing Docker socket at `/var/run/docker.sock`.
