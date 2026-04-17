# Tasks: Preset Application and Reapply State

**Input**: Design documents from `specs/196-preset-application-reapply-state/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and UI/request-shape integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code.

**Test Commands**:

- Focused UI tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, DESIGN-REQ-011: preset controls and objective image target.
- FR-002, SC-001, DESIGN-REQ-012: selecting a preset is non-mutating.
- FR-003, SC-002, DESIGN-REQ-012: Apply replaces initial empty step and appends to authored drafts.
- FR-004, SC-003, DESIGN-REQ-022: preset objective text drives objective and title.
- FR-005, FR-009, SC-004, DESIGN-REQ-012, DESIGN-REQ-025: changed objective inputs mark explicit reapply and do not overwrite steps.
- FR-006, FR-007, FR-008, SC-005, SC-006, DESIGN-REQ-010, DESIGN-REQ-025: template-bound instruction and attachment detachment.
- FR-010, SC-007: MM-378 remains visible in artifacts and verification.

## Phase 1: Setup

- [X] T001 Confirm MM-378 source input and single-story traceability in `docs/tmp/jira-orchestration-inputs/MM-378-moonspec-orchestration-input.md` and `specs/196-preset-application-reapply-state/spec.md`.
- [X] T002 Confirm existing Create page preset, Jira import, attachment, and submission surfaces in `frontend/src/entrypoints/task-create.tsx`.

## Phase 2: Foundational

- [X] T003 Confirm existing focused UI test harness covers Create page preset and request-shape behavior in `frontend/src/entrypoints/task-create.test.tsx`.

## Phase 3: Story - Preset Application and Reapply State

**Summary**: As a task author, I want reusable task presets to apply only when I explicitly choose Apply or Reapply so that edited preset objective inputs and manually customized expanded steps remain under my control.

**Independent Test**: Render the Create page, select and apply a preset, modify preset objective inputs and template-bound step inputs, then submit. The story passes when selecting a preset alone does not mutate steps, applying replaces only the initial empty step or appends to authored steps, preset objective text drives task objective and title, changed preset objective text or objective-scoped attachments mark the preset as needing explicit reapply, and edited template-bound instructions or attachments detach template identity.

**Traceability**: FR-001 through FR-010, SC-001 through SC-007, DESIGN-REQ-010 through DESIGN-REQ-012, DESIGN-REQ-022, DESIGN-REQ-025, MM-378.

### Unit Tests

- [X] T004 Add focused UI test proving selecting a preset does not mutate existing step draft state in `frontend/src/entrypoints/task-create.test.tsx` (FR-002, SC-001, DESIGN-REQ-012).
- [X] T005 Add focused UI tests proving Apply replaces an initial empty step and appends to authored steps in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, SC-002, DESIGN-REQ-012).
- [X] T006 Add focused UI test proving preset objective text controls objective and title resolution in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, SC-003, DESIGN-REQ-022).
- [X] T007 Add focused UI tests proving manual preset objective text and objective-scoped attachment changes mark Reapply preset without mutating expanded steps in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-005, FR-009, SC-004, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-025).
- [X] T008 Add focused UI tests proving template-bound step instruction and attachment edits detach template identity in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, FR-007, FR-008, SC-005, SC-006, DESIGN-REQ-010, DESIGN-REQ-025).

### Red-First Confirmation

- [X] T009 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm new tests fail for missing objective attachment target, dirty-state trigger, or template attachment detachment before production edits.

### Implementation

- [X] T010 Add objective-scoped preset attachment draft state, UI, validation, upload, task-level payload submission, and reapply dirty-state tracking in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-005, FR-009, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-025).
- [X] T011 Add template attachment snapshots and attachment-set detachment logic for preset-expanded steps in `frontend/src/entrypoints/task-create.tsx` (FR-006, FR-007, FR-008, DESIGN-REQ-010, DESIGN-REQ-025).
- [X] T012 Preserve existing preset Apply replacement/append behavior, non-mutating selection, and objective/title resolution; make only test-driven fixes in `frontend/src/entrypoints/task-create.tsx` (FR-002, FR-003, FR-004, DESIGN-REQ-012, DESIGN-REQ-022).
- [X] T013 Run focused UI tests and fix failures in `frontend/src/entrypoints/task-create.tsx` or `frontend/src/entrypoints/task-create.test.tsx` only as needed (FR-001 through FR-009).

## Phase 4: Polish And Verification

- [X] T014 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T015 Run `/moonspec-verify` and record the result in `specs/196-preset-application-reapply-state/verification.md` (FR-010, SC-007).

## Dependencies & Execution Order

- T001-T003 must complete before story tests.
- T004-T008 must be written before T010-T012.
- T009 confirms red-first behavior before implementation.
- T010-T013 complete the story.
- T014-T015 run after focused tests pass.

## Parallel Opportunities

- T004 through T008 can be drafted in the same focused UI test file but must be validated as one red-first batch.
- T010 and T011 touch the same implementation file and must run sequentially.

## Notes

- This task list covers exactly one story: MM-378.
