# Tasks: Jira Create Browser

**Input**: Design documents from `/specs/161-jira-create-browser/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Required. This is runtime work with explicit TDD expectations.
**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare the Create page and test context for Jira browser work.

- [X] T001 Review current Create page runtime config, state, and template/step instruction surfaces in `frontend/src/entrypoints/task-create.tsx`
- [X] T002 [P] Review existing Create page test fetch mocks and helpers in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T003 [P] Review Jira runtime config source templates and rollout tests in `api_service/api/routers/task_dashboard_view_model.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T004 [P] Review existing Create page layout styles and control patterns in `frontend/src/styles/mission-control.css`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared Jira client-side models, runtime config parsing, and test fixtures used by all stories.

**Critical**: No user story implementation should begin until these tasks are complete.

- [X] T005 [P] Add Jira-enabled boot payload helper and mock Jira endpoint responses in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T006 [P] Add Jira browser client-side model types for integration config, project, board, column, issue summary, issue detail, import target, and replace/append preference in `frontend/src/entrypoints/task-create.tsx`
- [X] T007 Add or verify runtime-config source templates and client parsing for Jira browser capability in `api_service/api/routers/task_dashboard_view_model.py` and `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-001 and DOC-REQ-002
- [X] T008 [P] Add shared Jira browser utility helpers for path interpolation, response item extraction, and target labels in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: Shared Jira types, config parsing, and test fixtures are ready. User story work can begin.

---

## Phase 3: User Story 1 - Open Jira Browser From Preset Instructions (Priority: P1)

**Goal**: User can open one shared Jira browser from the preset `Feature Request / Initial Instructions` field, and the browser identifies that field as the selected target.

**Independent Test**: Enable Jira browser config in the boot payload, open from the preset instructions field, and verify the browser opens with the preset target while existing Create page behavior remains unchanged when disabled.

### Tests for User Story 1

- [X] T009 [P] [US1] Add failing test for hidden Jira browser controls when runtime config disables Jira in `frontend/src/entrypoints/task-create.test.tsx` covering DOC-REQ-001
- [X] T010 [P] [US1] Add failing test for opening `Browse Jira story` from preset instructions and showing the preset target in `frontend/src/entrypoints/task-create.test.tsx` covering DOC-REQ-006 and DOC-REQ-008

### Implementation for User Story 1

- [X] T011 [US1] Add Jira browser open/closed state, current target state, and preset open handler in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-006 and DOC-REQ-008
- [X] T012 [US1] Render the preset `Browse Jira story` entry control only when Jira browser runtime config is enabled in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-001 and DOC-REQ-006
- [X] T013 [US1] Implement the shared browser shell title, close control, and preset target display in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-006 and DOC-REQ-008
- [X] T014 [P] [US1] Add base shared browser and preset entry-point styles in `frontend/src/styles/mission-control.css` covering DOC-REQ-006

### Validation for User Story 1

- [X] T015 [US1] Run `node node_modules/vitest/vitest.mjs run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and verify US1 tests pass in `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Preset target browser entry works independently.

---

## Phase 4: User Story 2 - Open Jira Browser From Step Instructions (Priority: P1)

**Goal**: User can open the same shared Jira browser from any step `Instructions` field, and the browser identifies the selected step as the target.

**Independent Test**: Enable Jira browser config, open from Step 1 instructions, and verify the shared browser opens with `Step 1 Instructions` as the target.

### Tests for User Story 2

- [X] T016 [P] [US2] Add failing test for opening `Browse Jira story` from a step instructions field in `frontend/src/entrypoints/task-create.test.tsx` covering DOC-REQ-006 and DOC-REQ-008

### Implementation for User Story 2

- [X] T017 [US2] Render a Jira browser entry control beside each step instructions field when Jira browser config is enabled in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-006 and DOC-REQ-008
- [X] T018 [US2] Wire step-specific browser open handlers so the shared browser target updates to the selected step in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-006 and DOC-REQ-008
- [X] T019 [P] [US2] Adjust step instruction field layout styles for stable button and textarea placement in `frontend/src/styles/mission-control.css` covering DOC-REQ-006

### Validation for User Story 2

- [X] T020 [US2] Run `node node_modules/vitest/vitest.mjs run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and verify US2 tests pass in `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Preset and step target browser entry points both work and reuse one shared browser.

---

## Phase 5: User Story 3 - Browse Jira Board Stories (Priority: P2)

**Goal**: User can navigate project -> board -> board columns -> issue list -> issue detail through MoonMind-owned Jira browser endpoints without editing task text.

**Independent Test**: Mock MoonMind Jira browser endpoints and verify project/board defaults, ordered columns, column issue switching, and normalized issue preview.

### Tests for User Story 3

- [X] T021 [P] [US3] Add failing test that board columns render in returned order and switching columns updates visible issues in `frontend/src/entrypoints/task-create.test.tsx` covering DOC-REQ-003 and DOC-REQ-004
- [X] T022 [P] [US3] Add failing test that selecting an issue loads normalized preview text without importing into draft fields in `frontend/src/entrypoints/task-create.test.tsx` covering DOC-REQ-005 and DOC-REQ-007

### Implementation for User Story 3

- [X] T023 [US3] Add Jira projects, boards, columns, issues, and issue-detail React Query hooks gated by browser state and selections in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, and DOC-REQ-005
- [X] T024 [US3] Add project and board selection state with configured default selection behavior in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-001 and DOC-REQ-006
- [X] T025 [US3] Add ordered column tabs and active-column state reset behavior in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-003 and DOC-REQ-004
- [X] T026 [US3] Add issue list rendering by active column and selected issue state in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-004 and DOC-REQ-007
- [X] T027 [US3] Add normalized issue preview rendering for issue key, summary, description, acceptance criteria, and non-executing import preference state in `frontend/src/entrypoints/task-create.tsx` covering DOC-REQ-005 and DOC-REQ-007
- [X] T028 [P] [US3] Add browser layout, column tabs, issue list, and preview panel styles in `frontend/src/styles/mission-control.css` covering DOC-REQ-006

### Validation for User Story 3

- [X] T029 [US3] Run `node node_modules/vitest/vitest.mjs run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and verify US3 navigation and preview tests pass in `frontend/src/entrypoints/task-create.test.tsx` covering DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, and DOC-REQ-007

**Checkpoint**: Jira browser can browse and preview stories without writing to the Create page draft.

---

## Phase 6: User Story 4 - Keep Manual Task Creation Independent (Priority: P2)

**Goal**: Jira loading failures remain local to the browser and never block manual task authoring or valid non-Jira task submission.

**Independent Test**: Simulate Jira endpoint failures and verify local browser error messaging while manual Create page controls and submission remain usable.

### Tests for User Story 4

- [X] T030 [P] [US4] Add failing test for browser-local Jira project, board, column, issue-list, or issue-detail fetch failure messaging in `frontend/src/entrypoints/task-create.test.tsx` covering FR-014
- [X] T031 [P] [US4] Add failing test that manual task creation remains available after Jira browser failure in `frontend/src/entrypoints/task-create.test.tsx` covering FR-014

### Implementation for User Story 4

- [X] T032 [US4] Add browser-local error messages for failed Jira project, board, board issue, and issue-detail queries in `frontend/src/entrypoints/task-create.tsx` covering FR-014
- [X] T033 [US4] Ensure Jira browser error state does not change submit validation, task objective resolution, step editing, or preset editing in `frontend/src/entrypoints/task-create.tsx` covering FR-014

### Validation for User Story 4

- [X] T034 [US4] Run `node node_modules/vitest/vitest.mjs run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and verify US4 failure isolation tests pass in `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Jira failure is additive and local; manual task creation remains usable.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and traceability checks across all stories.

- [X] T035 [P] Run TypeScript typecheck with `node node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json` for `frontend/src/entrypoints/task-create.tsx`
- [X] T036 [P] Run ESLint with `node node_modules/eslint/bin/eslint.js -c frontend/eslint.config.mjs frontend/src` for `frontend/src/entrypoints/task-create.tsx` and `frontend/src/styles/mission-control.css`
- [X] T037 Run full unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for `frontend/src/entrypoints/task-create.test.tsx` and `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T038 Verify every `DOC-REQ-*` from `specs/161-jira-create-browser/spec.md` appears in at least one implementation task and one validation task in `specs/161-jira-create-browser/tasks.md`
- [X] T039 Update final validation notes in `specs/161-jira-create-browser/quickstart.md` if commands or expected outcomes changed during implementation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1; blocks user story implementation.
- **US1 and US2 (P1)**: Depend on Phase 2. They can be implemented sequentially or in parallel after shared browser state is available, but both touch `task-create.tsx`.
- **US3 (P2)**: Depends on Phase 2 and benefits from US1/US2 browser shell work.
- **US4 (P2)**: Depends on Phase 2 and benefits from US3 data hooks.
- **Polish**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1**: MVP slice; no dependency on other stories after Phase 2.
- **US2**: Independent target-entry slice after Phase 2, but shares browser shell with US1.
- **US3**: Can start after Phase 2 if browser shell scaffolding is available; otherwise begins after US1.
- **US4**: Requires data hooks from US3 to validate loading failure behavior.

### Within Each User Story

- Test tasks must be written before implementation tasks.
- Implementation tasks must pass the story validation task before moving to the next story.
- Final typecheck, lint, and unit-wrapper validation run after all desired stories are complete.

## Parallel Opportunities

- T002, T003, and T004 can run in parallel during setup.
- T005, T006, and T008 can run in parallel during foundation.
- US1 tests T009 and T010 can run in parallel.
- US3 tests T021 and T022 can run in parallel.
- US4 tests T030 and T031 can run in parallel.
- Final typecheck T035 and lint T036 can run in parallel.

## Parallel Example: User Story 3

```bash
Task: "Add failing test that board columns render in returned order and switching columns updates visible issues in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing test that selecting an issue loads normalized preview text without importing into draft fields in frontend/src/entrypoints/task-create.test.tsx"
```

## Implementation Strategy

### MVP First

Complete Phase 1, Phase 2, and User Story 1. This gives a runtime-config-gated Jira browser entry point from preset instructions without altering task submission.

### Incremental Delivery

1. Add shared Jira models and config parsing.
2. Deliver preset browser opening.
3. Add step browser opening.
4. Add project, board, column, issue-list, and issue-detail navigation.
5. Add local failure handling.
6. Run focused frontend tests, typecheck, lint, and full unit verification.

### Traceability Coverage

- DOC-REQ-001: T007, T009, T012, T015, T024
- DOC-REQ-002: T007, T023, T029
- DOC-REQ-003: T021, T023, T025, T029
- DOC-REQ-004: T021, T023, T025, T026, T029
- DOC-REQ-005: T022, T023, T027, T029
- DOC-REQ-006: T010, T011, T012, T013, T014, T016, T017, T018, T028
- DOC-REQ-007: T022, T026, T027, T029
- DOC-REQ-008: T010, T011, T013, T016, T017, T018, T020
