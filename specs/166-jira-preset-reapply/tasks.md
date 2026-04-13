# Tasks: Jira Preset Reapply Signaling

**Input**: Design documents from `/specs/166-jira-preset-reapply/`
**Prerequisites**: `plan.md` (required), `spec.md` (required for user stories), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Required. The feature request explicitly requires runtime production code changes plus validation tests and the spec requires TDD coverage.
**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other marked tasks in the same phase because it touches different files or only reads context.
- **[Story]**: User story label for story-scoped tasks only.
- Every task includes an exact file path.

---

## Phase 1: Setup (Shared Context)

**Purpose**: Confirm the existing Create page surfaces and test fixture before adding story-specific tests.

- [ ] T001 Review current Jira import, preset message, applied template, and step update state in `frontend/src/entrypoints/task-create.tsx`
- [ ] T002 [P] Review existing Create page Jira and preset regression fixtures in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T003 [P] Review UI action expectations in `specs/166-jira-preset-reapply/contracts/create-page-jira-preset-reapply.yaml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared helper boundaries used by all three stories.

**Critical**: Complete this phase before story implementation so all stories share the same reapply and template-bound definitions.

- [ ] T004 Define the exact reapply-needed message constant location and reuse strategy in `frontend/src/entrypoints/task-create.tsx`
- [ ] T005 Define the template-bound instruction identity predicate contract near existing step helpers in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: Shared behavior definitions are ready. User story tests can be written in priority order.

---

## Phase 3: User Story 1 - Understand Preset Reapply Need (Priority: P1)

**Goal**: Importing Jira text into preset instructions after a preset was applied shows the required message and preserves already-expanded steps.
**Independent Test**: Apply a preset, import Jira text into preset instructions, confirm the exact message appears, confirm existing expanded steps remain unchanged, and confirm restoring the last applied instructions clears the message.

### Tests for User Story 1 (write first)

- [ ] T006 [P] [US1] Add a failing regression test for applied preset Jira import showing the exact reapply-needed message in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T007 [P] [US1] Add a failing regression assertion that already-expanded preset steps remain unchanged after Jira preset import in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T008 [P] [US1] Add a failing regression assertion that restoring preset instructions to the last applied value clears the reapply-needed message in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T009 [P] [US1] Add a failing regression test that Jira import producing unchanged preset instructions does not show reapply-needed state in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 1

- [ ] T010 [US1] Update Jira preset import handling to set reapply-needed state only when applied templates exist and imported text changes the field in `frontend/src/entrypoints/task-create.tsx`
- [ ] T011 [US1] Preserve current expanded step state during Jira preset import by avoiding preset expansion calls in `frontend/src/entrypoints/task-create.tsx`
- [ ] T012 [US1] Update preset instructions change handling to clear reapply-needed state when text matches the last applied preset instructions in `frontend/src/entrypoints/task-create.tsx`
- [ ] T013 [US1] Keep unchanged Jira preset imports as no-ops for reapply-needed state in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 1

- [ ] T014 [US1] Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and confirm User Story 1 assertions pass for `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Story 1 is independently functional and verifiable.

---

## Phase 4: User Story 2 - Reapply Explicitly (Priority: P2)

**Goal**: While preset instructions are dirty after Jira import, the preset action is clearly presented as reapply and no steps change until the operator explicitly uses it.
**Independent Test**: Change preset instructions through Jira import after applying a preset and confirm the preset action label communicates reapply while expanded steps remain unchanged before clicking it.

### Tests for User Story 2 (write first)

- [ ] T015 [P] [US2] Add a failing regression assertion that the preset action is labeled `Reapply preset` while reapply-needed state is visible in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T016 [P] [US2] Add a failing regression assertion that the preset action returns to `Apply` after reapply-needed state clears in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 2

- [ ] T017 [US2] Update the preset action label to render `Reapply preset` only while the reapply-needed message is active in `frontend/src/entrypoints/task-create.tsx`
- [ ] T018 [US2] Preserve the existing preset application handler so the explicit reapply action uses the same validated Apply flow in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 2

- [ ] T019 [US2] Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and confirm User Story 1 and User Story 2 assertions pass for `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Stories 1 and 2 are independently functional and verifiable.

---

## Phase 5: User Story 3 - Understand Template-Bound Step Customization (Priority: P3)

**Goal**: Opening Jira import for a template-bound step warns that the step will become manually customized, while still allowing import and detaching template instruction identity on edit.
**Independent Test**: Apply a preset, open Jira import from a still-template-bound step, confirm the warning appears, import Jira text, confirm only the targeted step changes, and confirm the submitted draft no longer carries template-bound instruction identity for that edited step.

### Tests for User Story 3 (write first)

- [ ] T020 [P] [US3] Add a failing regression test that opening Jira browser from a template-bound step shows the customization warning in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T021 [P] [US3] Add a failing regression assertion that the customization warning disappears after Jira import detaches the step from template-bound instruction identity in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T022 [P] [US3] Add or extend a regression assertion that Jira import into a template-bound step updates only the targeted step in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T023 [P] [US3] Add or extend a regression assertion that submitting after Jira import omits template-bound step id for the customized step in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [ ] T024 [US3] Implement a reusable template-bound instruction identity predicate for step drafts in `frontend/src/entrypoints/task-create.tsx`
- [ ] T025 [US3] Derive the current Jira step target and template-bound warning state from the active import target in `frontend/src/entrypoints/task-create.tsx`
- [ ] T026 [US3] Render the exact customization warning inside the Jira browser header when the active step target is still template-bound in `frontend/src/entrypoints/task-create.tsx`
- [ ] T027 [US3] Route Jira step imports through the existing step update path so instruction divergence detaches template identity in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 3

- [ ] T028 [US3] Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and confirm all User Story 3 assertions pass for `frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: All user stories are independently functional and verifiable.

---

## Phase 6: Polish & Cross-Cutting Validation

**Purpose**: Verify runtime scope, type safety, and full unit-suite compatibility after all stories pass.

- [ ] T029 [P] Run `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` and address any type errors in `frontend/src/entrypoints/task-create.tsx`
- [ ] T030 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm targeted dashboard validation passes for `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T031 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --no-xdist` and confirm the full unit suite passes from repository root `tools/test_unit.sh`
- [ ] T032 Confirm no task submission payload schema changes were introduced by inspecting Create page submission assembly in `frontend/src/entrypoints/task-create.tsx`
- [ ] T033 Confirm quickstart validation steps still match the delivered runtime behavior in `specs/166-jira-preset-reapply/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1 context review.
- **Phase 3 User Story 1**: Depends on Phase 2 because it uses the shared message and reapply definitions.
- **Phase 4 User Story 2**: Depends on User Story 1 reapply-needed state.
- **Phase 5 User Story 3**: Depends on Phase 2 helper definitions but can be implemented after User Story 1 if capacity is constrained.
- **Phase 6 Polish**: Depends on all selected user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: MVP. Delivers the critical non-destructive reapply-needed behavior.
- **User Story 2 (P2)**: Depends on User Story 1 dirty state; adds explicit reapply action clarity.
- **User Story 3 (P3)**: Depends on foundational template-bound identity definition; independent from preset reapply button labeling.

### Within Each User Story

- Test tasks must be completed before implementation tasks.
- Production code tasks in `frontend/src/entrypoints/task-create.tsx` should be completed serially to avoid same-file conflicts.
- Validation tasks run after the related implementation tasks.

---

## Parallel Opportunities

- T002 and T003 can run in parallel after T001 starts because they read different files.
- T006, T007, T008, and T009 can be drafted in parallel in the same test file only if one owner coordinates final ordering; otherwise run serially to avoid conflicts.
- T015 and T016 can be drafted together because they extend the same User Story 2 flow in the test file.
- T020, T021, T022, and T023 can be drafted together if one owner coordinates the final test flow; otherwise run serially to avoid conflicts.
- T029 can run in parallel with quickstart review T033 after implementation is complete.

## Parallel Example: User Story 1

```bash
Task: "Add failing reapply-needed message regression test in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing expanded-step preservation assertion in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing unchanged-import no-op regression test in frontend/src/entrypoints/task-create.test.tsx"
```

## Parallel Example: User Story 3

```bash
Task: "Add failing template-bound warning regression test in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing targeted-step-only import assertion in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing customized-step submission assertion in frontend/src/entrypoints/task-create.test.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete User Story 1 tests first and confirm they fail for the expected reason.
3. Implement User Story 1 in `frontend/src/entrypoints/task-create.tsx`.
4. Run the targeted Create page test command.
5. Stop and validate the operator can see reapply-needed messaging without hidden step rewrites.

### Incremental Delivery

1. Deliver User Story 1 for non-destructive preset import signaling.
2. Add User Story 2 to make the reapply action explicit.
3. Add User Story 3 for template-bound step conflict signaling and detachment validation.
4. Finish with typecheck, dashboard-targeted validation, and full unit validation.

### Runtime Scope Gate

- At least one production runtime implementation task is present: T010-T013, T017-T018, and T024-T027 update `frontend/src/entrypoints/task-create.tsx`.
- Validation tasks are present for each user story: T014, T019, T028, T029, T030, and T031.
- No `DOC-REQ-*` identifiers exist in `spec.md`, so no DOC-REQ task traceability is required.
