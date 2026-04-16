# Tasks: Edit Task Shows All Steps

**Input**: Design documents from `/specs/192-edit-task-all-steps/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/speckit.verify`

## Phase 1: Setup

- [X] T001 Confirm active feature artifacts exist under `specs/192-edit-task-all-steps/` for MM-340 traceability.
- [X] T002 Confirm implementation files and tests are localized to `frontend/src/lib/temporalTaskEditing.ts`, `frontend/src/entrypoints/task-create.tsx`, and `frontend/src/entrypoints/task-create.test.tsx`.

## Phase 2: Foundational

- [X] T003 Verify existing frontend test harness and mocked execution-detail responses in `frontend/src/entrypoints/task-create.test.tsx`.

## Phase 3: Story - Edit Multi-Step Task

**Summary**: As a MoonMind operator, I want the Edit Task form to load every step from a multi-step task so that I can review and update the complete task plan.

**Independent Test**: Load the Edit Task page with a supported `MoonMind.Run` execution whose input contains multiple task steps, then verify each step appears in order with its instructions and saving the unchanged draft preserves the later steps.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, MM-340

### Unit Tests

- [X] T004 Add failing draft reconstruction test for ordered multi-step output in `frontend/src/entrypoints/task-create.test.tsx` covering FR-001, FR-004, FR-005, SC-001.
- [X] T005 Add failing edit-page rendering test for all multi-step sections in `frontend/src/entrypoints/task-create.test.tsx` covering FR-002, FR-003, SC-002, SC-004.
- [X] T006 Add failing save-payload preservation test for unchanged later steps in `frontend/src/entrypoints/task-create.test.tsx` covering FR-006, SC-003.
- [X] T007 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and confirm T004-T006 fail for the expected truncation reason.

### Implementation

- [X] T008 Extend `TemporalSubmissionDraft` and reconstruction helpers in `frontend/src/lib/temporalTaskEditing.ts` to expose ordered editable steps for FR-001, FR-004, FR-005.
- [X] T009 Update edit/rerun draft application in `frontend/src/entrypoints/task-create.tsx` to initialize `StepState[]` from reconstructed steps for FR-002 and FR-003.
- [X] T010 Preserve unchanged later steps in submitted edit/rerun payloads through the existing `StepState[]` serialization in `frontend/src/entrypoints/task-create.tsx` for FR-006.
- [X] T011 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and confirm the focused tests pass.

## Phase 4: Polish And Verification

- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for required unit verification.
- [X] T013 Run `/speckit.verify` against `specs/192-edit-task-all-steps/spec.md` and record the verdict: FULLY_IMPLEMENTED.

## Dependencies & Execution Order

- T004-T006 must be written before T008-T010.
- T007 must run before production implementation.
- T011 must pass before T012.
- T013 is final after tests pass.

## Implementation Strategy

Keep the change narrow: preserve step records during reconstruction, apply those records to form state, and rely on the existing submit serialization to preserve later steps.
