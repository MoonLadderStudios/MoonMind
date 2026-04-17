# Tasks: Preserve Attachment Bindings in Snapshots and Reruns

**Input**: Design documents from `/specs/196-preserve-attachment-bindings/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-369 user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: FR-001 through FR-011; acceptance scenarios 1-5; SC-001 through SC-005; DESIGN-REQ-007, DESIGN-REQ-015, DESIGN-REQ-018.

**Test Commands**:

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and `pytest tests/unit/api/routers/test_executions.py -q`
- Integration tests: `pytest tests/contract/test_temporal_execution_api.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing task editing and attachment surfaces before adding tests.

- [X] T001 Inspect current task snapshot persistence in `api_service/api/routers/executions.py` for FR-001, FR-002, DESIGN-REQ-007
- [X] T002 Inspect current frontend draft reconstruction in `frontend/src/lib/temporalTaskEditing.ts` and Create-page attachment state in `frontend/src/entrypoints/task-create.tsx` for FR-003 through FR-010

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the binding model used by tests and implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Identify the existing attachment ref fields and draft reconstruction assumptions in `frontend/src/lib/temporalTaskEditing.ts` for objective and step draft reconstruction covering FR-001, FR-003, FR-004, DESIGN-REQ-018
- [X] T004 Identify the existing Create-page state and submission assumptions for persisted versus new attachments in `frontend/src/entrypoints/task-create.tsx` covering FR-006, FR-008

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Preserve Attachment Bindings in Snapshots and Reruns

**Summary**: As a user editing or rerunning a task, I need MoonMind to reconstruct attachments from the authoritative task input snapshot so unchanged bindings survive and changes are always explicit.

**Independent Test**: Create a draft from an artifact-backed task input snapshot with objective and step attachments, load it into edit/rerun Create-page state, submit without changes, then verify unchanged refs remain in the outgoing payload; removing a persisted ref or adding a new local file changes only that target.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011; acceptance scenarios 1-5; SC-001-SC-005; DESIGN-REQ-007, DESIGN-REQ-015, DESIGN-REQ-018

**Test Plan**:

- Unit: frontend draft transformation and Create-page state/submission behavior; backend snapshot descriptor/action guard behavior.
- Integration: contract test for persisted task input snapshot shape containing objective and step `inputAttachments`.

### Unit Tests (write first)

- [X] T005 [P] Add failing frontend unit test for `buildTemporalSubmissionDraftFromExecution` preserving objective and step `inputAttachments` from the authoritative snapshot in `frontend/src/entrypoints/task-create.test.tsx` covering FR-003, FR-004, SC-001, SC-002, DESIGN-REQ-007
- [X] T006 [P] Add failing frontend unit test for Create-page edit/rerun submission retaining unchanged persisted attachment refs in `frontend/src/entrypoints/task-create.test.tsx` covering FR-006, FR-008, SC-003, DESIGN-REQ-018
- [X] T007 [P] Add failing frontend unit test for explicit reconstruction failure when snapshot attachment refs cannot be bound from `draft.task` in `frontend/src/entrypoints/task-create.test.tsx` covering FR-009, FR-010, SC-004, SC-005
- [X] T008 [P] Add or extend backend unit coverage for snapshot descriptor/action availability with original snapshot requirements in `tests/unit/api/routers/test_executions.py` covering FR-009, DESIGN-REQ-018
- [X] T009 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and `pytest tests/unit/api/routers/test_executions.py -q` to confirm T005-T008 fail for the expected missing behavior

### Integration Tests (write first)

- [X] T010 Add failing contract test or extend existing task-shaped execution contract coverage in `tests/contract/test_temporal_execution_api.py` to assert snapshot task body preserves objective and step `inputAttachments` and compact refs do not replace the task body covering FR-001, FR-002, DESIGN-REQ-007
- [X] T011 Run `pytest tests/contract/test_temporal_execution_api.py -q` to confirm T010 fails for the expected missing or incomplete behavior

### Implementation

- [X] T012 Implement persisted attachment ref types, normalization, and binding validation in `frontend/src/lib/temporalTaskEditing.ts` covering FR-003, FR-004, FR-009, FR-010
- [X] T013 Implement persisted attachment ref state fields and hydration from Temporal drafts in `frontend/src/entrypoints/task-create.tsx` covering FR-005, FR-006, DESIGN-REQ-015
- [X] T014 Implement unchanged persisted ref submission and explicit add/remove behavior in `frontend/src/entrypoints/task-create.tsx` covering FR-006, FR-008, DESIGN-REQ-018
- [X] T015 Backend snapshot payload/descriptor changes were not needed because `tests/unit/api/routers/test_executions.py` and `tests/contract/test_temporal_execution_api.py` already verify backend binding evidence for FR-001, FR-002, FR-009
- [X] T016 Run focused unit commands `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and `pytest tests/unit/api/routers/test_executions.py -q`, then fix failures
- [X] T017 Run focused contract command `pytest tests/contract/test_temporal_execution_api.py -q`, then fix failures
- [X] T018 Validate the single MM-369 story against acceptance scenarios 1-5 using `frontend/src/entrypoints/task-create.test.tsx` and `tests/contract/test_temporal_execution_api.py`

**Checkpoint**: The story is fully functional, covered by unit and contract tests, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Validate the completed story without adding hidden scope.

- [X] T019 [P] Update `specs/196-preserve-attachment-bindings/quickstart.md` if final commands or blockers differ from the planned validation
- [X] T020 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification
- [X] T021 Run `./tools/test_integration.sh` when Docker is available, or record the exact Docker socket blocker
- [X] T022 Run `/moonspec-verify` and record verification evidence in the final response

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work
- **Story (Phase 3)**: Depends on Foundational completion
- **Polish (Phase 4)**: Depends on focused unit and contract tests passing or documented blockers

### Within The Story

- Unit tests T005-T008 must be written before implementation.
- Integration/contract test T010 must be written before implementation.
- Red-first confirmation T009 and T011 must complete before T012-T015.
- Frontend draft model work T012 must precede Create-page state/submission work T013-T014.
- Backend changes T015 are conditional and should be skipped if existing backend evidence already satisfies the contract.
- Story validation T018 must follow focused unit and contract evidence.

### Parallel Opportunities

- T001 and T002 can run in parallel.
- T005, T006, T007, T008 can be authored in parallel because they cover distinct assertions.
- T019 can run after implementation while final verification commands are queued.

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational tasks.
2. Write failing frontend and backend tests.
3. Confirm the focused tests fail for the expected missing attachment preservation behavior.
4. Implement draft model and Create-page state/submission changes.
5. Update backend only if contract tests prove snapshot persistence is incomplete.
6. Run focused unit and contract tests.
7. Run final unit and feasible integration verification.
8. Run `/moonspec-verify`.
