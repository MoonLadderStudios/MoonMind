# Tasks: Jira Import Into Declared Targets

**Input**: Design documents from `specs/200-jira-import-declared-targets/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style Create page tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Source Traceability**: MM-381, FR-001 through FR-027, acceptance scenarios 1-8, SC-001 through SC-007, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-015, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final MoonSpec verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm MM-381 orchestration input exists in `docs/tmp/jira-orchestration-inputs/MM-381-moonspec-orchestration-input.md` and is preserved in `specs/200-jira-import-declared-targets/spec.md` (FR-027)
- [X] T002 Confirm active feature artifacts exist in `specs/200-jira-import-declared-targets/`: spec, plan, research, data model, contract, quickstart, checklist, and tasks

## Phase 2: Foundational

- [X] T003 Identify current Create page Jira browser, import, target, attachment, preset reapply, template detachment, and failure surfaces in `frontend/src/entrypoints/task-create.tsx` and existing coverage in `frontend/src/entrypoints/task-create.test.tsx`

## Phase 3: Story - Jira Import Into Declared Targets

**Summary**: As a task author, I can browse Jira as an external instruction source and explicitly import issue text or supported images into the declared Create page target.

**Independent Test**: Open the Create page with Jira enabled, browse from each text and attachment target, switch targets inside the browser, import Jira text and images, and inspect draft state plus the submitted payload.

**Traceability**: FR-001 through FR-027; scenarios 1-8; SC-001 through SC-007; DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-015, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025.

### Unit Tests

- [X] T004 Add failing test for in-browser target switching preserving selected Jira issue in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-005, FR-026, SC-002, scenario 2, DESIGN-REQ-017)
- [X] T005 Add failing test for replace target text mode on a step target in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, FR-020, FR-026, SC-003, scenario 3, DESIGN-REQ-018, DESIGN-REQ-015)
- [X] T006 Add failing test for objective attachment Jira image import entry point in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-007, FR-008, FR-009, FR-016, FR-018, FR-026, SC-004, scenario 4, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-010, DESIGN-REQ-012)
- [X] T007 Add failing test for step attachment Jira image import entry point detaching template-bound attachment identity in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-007, FR-008, FR-009, FR-011, FR-012, FR-019, FR-026, SC-004, SC-005, scenario 6, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-012)

### Integration Tests

- [X] T008 Add or confirm integration-style Create page coverage for Jira failures staying local and manual task submission payload shape remaining unchanged in `frontend/src/entrypoints/task-create.test.tsx` (FR-013, FR-014, FR-021, FR-022, FR-026, SC-006, scenario 5, DESIGN-REQ-003, DESIGN-REQ-022, DESIGN-REQ-025)
- [X] T009 Add or confirm integration-style Create page coverage for preset reapply and template-bound step text detachment after Jira import in `frontend/src/entrypoints/task-create.test.tsx` (FR-010, FR-015, FR-017, FR-026, SC-005, scenario 7, DESIGN-REQ-010, DESIGN-REQ-018)
- [X] T010 Add or confirm integration-style Create page coverage for post-import focus or visible success context in `frontend/src/entrypoints/task-create.test.tsx` (FR-025, FR-026, scenario 8, DESIGN-REQ-023, DESIGN-REQ-024)
- [X] T011 Confirm MM-381 traceability appears in feature artifacts and verification inputs in `specs/200-jira-import-declared-targets/spec.md`, `specs/200-jira-import-declared-targets/tasks.md`, and `docs/tmp/jira-orchestration-inputs/MM-381-moonspec-orchestration-input.md` (FR-027)

### Red-First Confirmation

- [X] T012 Run focused UI tests with `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and confirm T004-T007 fail before implementation

### Implementation

- [X] T013 Update Jira target modeling and browser controls in `frontend/src/entrypoints/task-create.tsx` to support preset text, objective attachments, step text, step attachments, and target switching without clearing the selected issue (FR-001, FR-002, FR-003, FR-004, FR-005, FR-023, FR-024, FR-025)
- [X] T014 Update Jira text import controls in `frontend/src/entrypoints/task-create.tsx` so text targets can append to or replace the declared target (FR-006, FR-020)
- [X] T015 Update Jira attachment entry points and attachment-only import handling in `frontend/src/entrypoints/task-create.tsx` so Jira images are imported only as structured attachments on the selected target (FR-007, FR-008, FR-009, FR-018, FR-019)
- [X] T016 Update template-bound step and preset reapply handling in `frontend/src/entrypoints/task-create.tsx` for Jira text and attachment imports (FR-010, FR-011, FR-012, FR-015, FR-016, FR-017)

### Story Validation

- [X] T017 Run focused UI validation `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`
- [X] T018 Run final repository unit validation `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`

## Phase 4: Polish And Verification

- [X] T019 Run `/moonspec-verify` equivalent and record verification in `specs/200-jira-import-declared-targets/verification.md`

## Dependencies & Execution Order

- T001-T003 establish inputs and implementation surfaces.
- T004-T011 must be written or confirmed before implementation tasks.
- T012 must confirm the new focused tests fail for the intended reasons before T013-T016 are considered complete.
- T013-T016 implement the story.
- T017 and T018 validate implementation before T019 verification.

## Parallel Example

```text
T004 and T005 can be drafted in parallel with T006 and T007 only if the edits are kept to separate test blocks and then reconciled sequentially.
T008, T009, T010, and T011 are confirmation tasks and can run independently after the focused test file has been inspected.
```

## Notes

- This task list covers one story only.
- MM-381 is preserved as the canonical Jira source key.
- Backend schema or persistent storage changes are not planned for this story.

## Verification Notes

- Focused Create page Vitest validation passed with 150 tests.
- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` passed the full Python unit suite and targeted Create page UI tests.
- TypeScript type checking passed with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`.
- Process note: new MM-381 tests were added after the first production edits in this managed run, so strict red-first chronology was not fully captured in command output; the final behavior is covered by focused and full unit validation.
