# Tasks: Jira Provenance Polish

**Input**: Design documents from `/specs/167-jira-provenance-polish/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/create-page-jira-provenance.yaml`, `quickstart.md`  
**Tests**: Automated frontend regression tests are required for each runtime story. Follow TDD: add or update the relevant failing test first, confirm it fails for the expected reason, then implement production behavior.

**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing Create page and Jira browser surfaces before adding story-specific changes.

- [X] T001 Review current Jira import target, issue detail, import mode, and text write helpers in `frontend/src/entrypoints/task-create.tsx`
- [X] T002 [P] Review existing Jira browser, preset import, step import, reapply, and submission tests in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T003 [P] Review existing Jira browser and Create page styling hooks in `frontend/src/styles/mission-control.css`
- [X] T004 [P] Review the UI state contract in `specs/167-jira-provenance-polish/contracts/create-page-jira-provenance.yaml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared local-state primitives needed by all provenance and session-memory stories.

**CRITICAL**: No user story implementation should begin until these shared primitives are in place.

- [X] T005 Add shared Jira import provenance and session-memory type definitions near existing Jira types in `frontend/src/entrypoints/task-create.tsx`
- [X] T006 Add safe browser session storage read/write helpers that ignore storage access failures in `frontend/src/entrypoints/task-create.tsx`
- [X] T007 Add a reusable provenance-chip rendering helper or component with accessible labeling in `frontend/src/entrypoints/task-create.tsx`
- [X] T008 Add base provenance chip styling with stable sizing and existing Create page visual conventions in `frontend/src/styles/mission-control.css`

**Checkpoint**: Shared primitives are available for story-specific tests and implementation.

---

## Phase 3: User Story 1 - See Jira Import Origin (Priority: P1)

**Goal**: Operators can see which Jira issue supplied imported preset or step instruction text.
**Independent Test**: Import one Jira issue into preset instructions and one Jira issue into a step; verify each target shows `Jira: <issue key>` and unrelated targets remain unmarked.

### Tests for User Story 1

- [X] T009 [US1] Add a failing regression test for showing a preset-level Jira provenance chip after preset-target import in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T010 [US1] Add a failing regression test for showing a step-level Jira provenance chip only on the imported step after step-target import in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T011 [US1] Add a failing regression test that manually editing preset or step instructions clears the corresponding stale Jira provenance chip in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T012 [US1] Add a failing regression test that importing an issue without an issue key does not render an empty Jira provenance chip in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 1

- [X] T013 [US1] Store preset-target Jira import provenance after successful preset import only when the selected issue has a non-empty issue key in `frontend/src/entrypoints/task-create.tsx`
- [X] T014 [US1] Store step-target Jira import provenance by step local id after successful step import only when the selected issue has a non-empty issue key in `frontend/src/entrypoints/task-create.tsx`
- [X] T015 [US1] Render the preset provenance chip near Feature Request / Initial Instructions when preset provenance exists in `frontend/src/entrypoints/task-create.tsx`
- [X] T016 [US1] Render step provenance chips near each affected step Instructions field when step provenance exists in `frontend/src/entrypoints/task-create.tsx`
- [X] T017 [US1] Clear preset provenance when the preset instructions field is manually edited in `frontend/src/entrypoints/task-create.tsx`
- [X] T018 [US1] Clear step provenance when the corresponding step instructions field is manually edited or the step is removed in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 1

- [X] T019 [US1] Verify User Story 1 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` for `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Story 1 is fully functional and independently verifiable.

---

## Phase 4: User Story 2 - Remember Last Jira Board in Session (Priority: P2)

**Goal**: Operators keep the last selected Jira project and board during the current browser session when runtime config enables session memory.
**Independent Test**: Enable session memory, select project/board, remount or refresh the page in the same session, and verify project/board are restored; disable session memory and verify they are not restored.

### Tests for User Story 2

- [X] T020 [US2] Add a failing regression test that restores the last selected Jira project and board from browser session storage when `rememberLastBoardInSession` is enabled in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T021 [US2] Add a failing regression test that does not write or restore Jira project and board session memory when `rememberLastBoardInSession` is disabled in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T022 [US2] Add a failing regression test that manually clearing Jira project or board selection clears the corresponding remembered session value in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T023 [US2] Add a failing regression test that session storage read/write failures do not block Jira browsing or manual task creation in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 2

- [X] T024 [US2] Initialize Jira project and board selection from session storage when the Jira browser opens and session memory is enabled in `frontend/src/entrypoints/task-create.tsx`
- [X] T025 [US2] Persist selected Jira project and board to session storage only when session memory is enabled in `frontend/src/entrypoints/task-create.tsx`
- [X] T026 [US2] Clear remembered Jira board when project selection changes or clears in `frontend/src/entrypoints/task-create.tsx`
- [X] T027 [US2] Clear remembered Jira board when board selection is manually cleared in `frontend/src/entrypoints/task-create.tsx`
- [X] T028 [US2] Keep existing configured default project/board fallback behavior when no session memory exists or storage access fails in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 2

- [X] T029 [US2] Verify User Story 2 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` for `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Stories 1 and 2 both work independently.

---

## Phase 5: User Story 3 - Keep Task Submission Unchanged (Priority: P3)

**Goal**: Jira provenance remains local Create-page metadata and does not alter submitted task payload shape.
**Independent Test**: Import Jira text, submit the task, and verify the payload includes the edited instruction text but does not include separate Jira provenance fields.

### Tests for User Story 3

- [X] T030 [US3] Add a failing regression test that task submission after Jira import excludes provenance fields such as issue key, board id, import mode, target type, or a Jira provenance object in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T031 [US3] Add or extend a regression test proving imported instruction text still participates in existing objective/title resolution without adding Jira metadata to the payload in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T032 [US3] Add or extend a regression test proving Jira-disabled Create page behavior and manual task submission remain unchanged in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [X] T033 [US3] Keep Jira provenance state out of Create page submission assembly while preserving imported instruction text in `frontend/src/entrypoints/task-create.tsx`
- [X] T034 [US3] Audit Create page submission payload assembly to ensure no session-memory or provenance state is included in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 3

- [X] T035 [US3] Verify User Story 3 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` for `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: All user stories are independently functional and verifiable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup across the Create page Jira provenance feature.

- [X] T036 Run TypeScript typecheck with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` for `frontend/src/entrypoints/task-create.tsx`
- [X] T037 Run focused Create page tests with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` for `frontend/src/entrypoints/task-create.test.tsx`
- [X] T038 Run the required unit wrapper with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for `frontend/src/entrypoints/task-create.test.tsx`
- [X] T039 Confirm runtime scope validation passes with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` for `specs/167-jira-provenance-polish/tasks.md`
- [X] T040 Update final manual smoke notes if implementation changes validation flow in `specs/167-jira-provenance-polish/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies. Can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational completion.
- **User Story 2 (Phase 4)**: Depends on Foundational completion; can run in parallel with User Story 1 after shared helpers exist, but both stories touch `frontend/src/entrypoints/task-create.tsx` and should coordinate carefully.
- **User Story 3 (Phase 5)**: Depends on User Story 1 provenance state existing and can start after the import provenance write path exists.
- **Polish (Phase 6)**: Depends on all selected user stories.

### User Story Dependencies

- **US1 - See Jira Import Origin (P1)**: MVP. Delivers visible provenance for imports.
- **US2 - Remember Last Jira Board in Session (P2)**: Independent after shared storage helpers, but uses the same Jira browser state area.
- **US3 - Keep Task Submission Unchanged (P3)**: Depends on provenance state being introduced by US1 so the payload boundary can be validated.

### Within Each User Story

- Write and run the failing tests before production changes.
- Implement production Create page state updates after the tests exist.
- Run focused validation before moving to the next story.
- Preserve existing Jira browser, preset, step import, reapply, and template-detachment behavior.

---

## Parallel Opportunities

- T002, T003, and T004 can run in parallel during setup because they inspect different files/artifacts.
- T009-T012 can be drafted in parallel only if coordinated in one owner branch because they touch the same test file.
- T020-T023 can be drafted in parallel only if coordinated in one owner branch because they touch the same test file.
- CSS styling task T008 can run in parallel with TypeScript helper tasks T005-T007.
- Final validation tasks T036 and T037 can run independently after implementation; T038 should run after focused validation is green.

---

## Parallel Example: User Story 1

```bash
Task: "Add a failing regression test for showing a preset-level Jira provenance chip after preset-target import in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add a failing regression test for showing a step-level Jira provenance chip only on the imported step after step-target import in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add base provenance chip styling with stable sizing and existing Create page visual conventions in frontend/src/styles/mission-control.css"
```

---

## Parallel Example: User Story 2

```bash
Task: "Add a failing regression test that restores the last selected Jira project and board from browser session storage when rememberLastBoardInSession is enabled in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add a failing regression test that does not write or restore Jira project and board session memory when rememberLastBoardInSession is disabled in frontend/src/entrypoints/task-create.test.tsx"
```

---

## Implementation Strategy

### MVP First

Complete Phase 1, Phase 2, and User Story 1. This delivers the visible Jira provenance chip for imported preset and step instruction text.

### Incremental Delivery

1. Deliver US1 with focused Create page tests.
2. Add US2 session-memory behavior and storage-failure fallback.
3. Add US3 payload-boundary assertions.
4. Run TypeScript, focused frontend tests, and the full unit wrapper.

### Runtime Scope Check

This task list includes production runtime changes in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/styles/mission-control.css`, plus validation changes in `frontend/src/entrypoints/task-create.test.tsx` and final command verification. This satisfies the selected runtime orchestration mode.
