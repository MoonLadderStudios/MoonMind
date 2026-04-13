# Tasks: Jira Import Actions

**Input**: Design documents from `/specs/164-jira-import-actions/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/create-page-jira-import.yaml`, `quickstart.md`
**Tests**: Required. The feature request explicitly calls for test-driven development and runtime validation.
**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or depends only on completed earlier tasks.
- **[Story]**: Maps task to a user story from `spec.md`.
- Every task includes a concrete file path or validation command path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the Phase 4 Jira browser/runtime-config base is present before adding import behavior.

- [ ] T001 Review existing Jira browser types, target state, issue detail query, and placeholder import UI in `frontend/src/entrypoints/task-create.tsx`
- [ ] T002 [P] Review existing Create page Jira browser, preset, submission, and template-detachment tests in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T003 [P] Review Jira Create page runtime config exposure in `api_service/api/routers/task_dashboard_view_model.py`
- [ ] T004 [P] Review existing Jira runtime config tests in `tests/unit/api/routers/test_task_dashboard_view_model.py`

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared import-mode/text helpers and preserve runtime rollout wiring used by all stories.

**CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T005 Add shared Jira import mode and write-action types in `frontend/src/entrypoints/task-create.tsx`
- [ ] T006 Add shared import text derivation helpers for preset brief, execution brief, description only, and acceptance criteria only in `frontend/src/entrypoints/task-create.tsx`
- [ ] T007 Add shared append/replace text write helper with empty-target handling in `frontend/src/entrypoints/task-create.tsx`
- [ ] T008 Preserve or adjust Jira Create page runtime config gating so Jira import controls remain disabled unless configured in `api_service/api/routers/task_dashboard_view_model.py`
- [ ] T009 [P] Add or update runtime config regression coverage for Jira UI gating if T008 changes behavior in `tests/unit/api/routers/test_task_dashboard_view_model.py`

**Checkpoint**: Import helpers and runtime-config gating are ready. User story implementation can proceed.

## Phase 3: User Story 1 - Import Jira Into Preset Objective (Priority: P1)

**Goal**: Operators can explicitly replace or append Jira text into Feature Request / Initial Instructions.
**Independent Test**: Open the Jira browser from the preset target, select an issue, choose import modes/actions, and verify only the preset objective field changes.

### Tests for User Story 1

- [ ] T010 [P] [US1] Add a failing test for replacing preset instructions from selected Jira issue text in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T011 [P] [US1] Add a failing test for appending Jira issue text to existing preset instructions with a clear separator in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T012 [P] [US1] Add a failing test that changing import mode changes copied preset text in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T013 [P] [US1] Add a failing test that selecting an issue preview without pressing Replace or Append does not mutate preset or step fields in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 1

- [ ] T014 [US1] Add target-aware default import mode state when opening Jira browser from the preset target in `frontend/src/entrypoints/task-create.tsx`
- [ ] T015 [US1] Replace the Phase 4 placeholder import copy with Import mode, Import preview, Replace target text, and Append to target text controls in `frontend/src/entrypoints/task-create.tsx`
- [ ] T016 [US1] Implement preset-target Replace behavior that writes selected import text only to `templateFeatureRequest` in `frontend/src/entrypoints/task-create.tsx`
- [ ] T017 [US1] Implement preset-target Append behavior that preserves existing `templateFeatureRequest` text and adds imported Jira text after a separator in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 1

- [ ] T018 [US1] Verify preset import tests with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Story 1 is independently functional and verifiable.

## Phase 4: User Story 2 - Import Jira Into a Selected Step (Priority: P1)

**Goal**: Operators can import Jira text into exactly one selected step's Instructions field.
**Independent Test**: Create multiple steps, open Jira from one step, import an issue, and verify only that step changes.

### Tests for User Story 2

- [ ] T019 [P] [US2] Add a failing test for replacing only the selected secondary step instructions from Jira import in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T020 [P] [US2] Add a failing test that step-target Jira import leaves the preset objective and non-target steps unchanged in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T021 [P] [US2] Add a failing test that opening Jira from a step defaults to Execution brief mode in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 2

- [ ] T022 [US2] Add step-target import handling that finds the selected step by local step identity in `frontend/src/entrypoints/task-create.tsx`
- [ ] T023 [US2] Route step-target Replace and Append writes through the existing `updateStep()` path in `frontend/src/entrypoints/task-create.tsx`
- [ ] T024 [US2] Guard step-target import so a missing target step does not mutate any other step in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 2

- [ ] T025 [US2] Verify step import tests with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Stories 1 and 2 both work independently.

## Phase 5: User Story 3 - Preserve Preset Reapply Semantics (Priority: P2)

**Goal**: Jira import into preset objective after preset application signals reapply is needed without silently rewriting expanded steps.
**Independent Test**: Apply a preset, import Jira into preset objective, and verify existing expanded steps remain unchanged while reapply messaging appears.

### Tests for User Story 3

- [ ] T026 [P] [US3] Add a failing test for reapply-needed message after Jira import changes an already-applied preset objective in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T027 [P] [US3] Add a failing test that expanded preset-derived steps are not rewritten by preset-target Jira import in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [ ] T028 [US3] Set the non-blocking preset reapply-needed message when preset-target Jira import occurs after a preset has been applied in `frontend/src/entrypoints/task-create.tsx`
- [ ] T029 [US3] Ensure preset-target Jira import updates only preset objective state and does not call preset expansion or mutate expanded step state in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 3

- [ ] T030 [US3] Verify preset reapply tests with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Preset reapply semantics remain explicit and visible.

## Phase 6: User Story 4 - Treat Jira Import as a Manual Template-Step Edit (Priority: P2)

**Goal**: Importing Jira text into a template-bound step detaches template identity when instructions diverge.
**Independent Test**: Apply a preset, import Jira into a template-bound step, submit, and verify the customized step no longer carries the original template-step identity.

### Tests for User Story 4

- [ ] T031 [P] [US4] Add a failing test that Jira import into a template-bound step detaches template-step identity before submission in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T032 [P] [US4] Add or update a submission regression assertion that unchanged template-bound steps keep identity while the customized imported step does not in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 4

- [ ] T033 [US4] Confirm Jira step import reuses `updateStep()` instruction editing semantics for template identity detachment in `frontend/src/entrypoints/task-create.tsx`
- [ ] T034 [US4] Remove any direct Jira step-state mutation that bypasses template-detachment behavior in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 4

- [ ] T035 [US4] Verify template-detachment tests with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Template-bound step identity remains accurate after Jira import.

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final safety checks across all user stories.

- [ ] T036 [P] Format and lint changed frontend files with `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src/entrypoints/task-create.tsx frontend/src/entrypoints/task-create.test.tsx`
- [ ] T037 [P] Typecheck the frontend with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- [ ] T038 Run the focused Create page UI suite with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`
- [ ] T039 Run repo unit validation with local test mode via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- [ ] T040 Run runtime scope validation for completed work with `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime`
- [ ] T041 Update quickstart validation notes if final commands or manual smoke steps change in `specs/164-jira-import-actions/quickstart.md`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational completion.
- **User Story 2 (Phase 4)**: Depends on Foundational completion; can run in parallel with US1 only if edits to `frontend/src/entrypoints/task-create.tsx` are coordinated.
- **User Story 3 (Phase 5)**: Depends on US1 because it extends preset-target import behavior.
- **User Story 4 (Phase 6)**: Depends on US2 because it extends step-target import behavior.
- **Polish (Phase 7)**: Depends on all selected user stories being complete.

### User Story Dependencies

- **US1 Import Jira Into Preset Objective**: MVP scope. No dependency on other stories after foundation.
- **US2 Import Jira Into a Selected Step**: No product dependency on US1, but shares implementation files.
- **US3 Preserve Preset Reapply Semantics**: Depends on US1 preset write behavior.
- **US4 Treat Jira Import as a Manual Template-Step Edit**: Depends on US2 step write behavior.

### Parallel Opportunities

- T002, T003, and T004 can run in parallel after T001 starts.
- T009 can run in parallel with frontend foundational helper tasks if T008 changes runtime config behavior.
- T010, T011, T012, and T013 can be written in parallel in `frontend/src/entrypoints/task-create.test.tsx` only if coordinated to avoid same-file conflicts.
- T019, T020, and T021 can be written in parallel in `frontend/src/entrypoints/task-create.test.tsx` only if coordinated to avoid same-file conflicts.
- T026 and T027 can be drafted together because they cover distinct assertions for preset reapply behavior.
- T031 and T032 can be drafted together because they cover related submission assertions.
- T036 and T037 can run in parallel after implementation is complete.

## Parallel Example: User Story 1

```bash
Task: "Add failing preset replace import test in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing preset append import test in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing import mode selection test in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing no-mutation-on-preview test in frontend/src/entrypoints/task-create.test.tsx"
```

## Parallel Example: User Story 2

```bash
Task: "Add failing selected-step replace test in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing non-target fields unchanged test in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing step default import mode test in frontend/src/entrypoints/task-create.test.tsx"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Setup and Foundational tasks T001-T009.
2. Add US1 failing tests T010-T013.
3. Implement US1 production behavior T014-T017.
4. Validate with T018.
5. Stop and demo explicit preset objective Replace/Append before adding step import behavior.

### Incremental Delivery

1. Deliver US1 preset import.
2. Deliver US2 step import.
3. Deliver US3 preset reapply messaging.
4. Deliver US4 template-step detachment.
5. Run final polish and validation tasks T036-T041.

### TDD Strategy

1. Add failing behavior tests for each story before production edits.
2. Implement the minimum production behavior to pass that story.
3. Re-run focused Create page tests after each story.
4. Run typecheck, lint, unit wrapper, and runtime scope validation before completion.
