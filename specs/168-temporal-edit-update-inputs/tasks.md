# Tasks: Temporal Edit UpdateInputs

**Input**: Design documents from `/specs/168-temporal-edit-update-inputs/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required. This is runtime implementation work and must include production code changes plus validation tests.

**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Phase 1: Setup (Shared Test and Contract Scaffolding)

**Purpose**: Establish shared validation targets and confirm the existing contract artifacts are ready for implementation.

- [ ] T001 [P] Verify the edit update OpenAPI contract parses and contains `/api/executions/{workflowId}/update` in `specs/168-temporal-edit-update-inputs/contracts/temporal-edit-update-inputs.openapi.yaml`
- [ ] T002 [P] Review focused validation commands for frontend tests, typecheck, and unit wrapper in `specs/168-temporal-edit-update-inputs/quickstart.md`
- [ ] T003 [P] Confirm no `DOC-REQ-*` coverage gate is required for this feature in `specs/168-temporal-edit-update-inputs/spec.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared edit-submit helpers and endpoint configuration used by all user stories.

**CRITICAL**: No user story implementation should begin until this phase is complete.

### Tests for Foundational Helpers

- [ ] T004 [P] Add failing payload-builder coverage for `UpdateInputs`, `parametersPatch`, and optional `inputArtifactRef` in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T005 [P] Add failing configured-update-endpoint coverage using `sources.temporal.update` defaults and workflow ID encoding in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for Foundational Helpers

- [ ] T006 Implement `TemporalTaskEditUpdateName`, `TemporalArtifactEditUpdatePayload`, and `buildTemporalArtifactEditUpdatePayload` in `frontend/src/lib/temporalTaskEditing.ts`
- [ ] T007 Add `sources.temporal.update` typing and configured update URL interpolation support in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: Shared payload and endpoint helpers exist; user story implementation can begin.

---

## Phase 3: User Story 1 - Save Active Execution Edits (Priority: P1) 🎯 MVP

**Goal**: Operators can edit supported fields for an active `MoonMind.Run` execution and save changes back to the same Temporal execution with `UpdateInputs`.

**Independent Test**: Open `/tasks/new?editExecutionId=<workflowId>`, change a supported field, submit, assert the request uses `UpdateInputs` for the same workflow, and assert navigation returns to the Temporal detail route.

### Tests for User Story 1

- [ ] T008 [P] [US1] Add failing active edit submit test for `UpdateInputs` request shape in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T009 [P] [US1] Add failing active edit redirect and one-time success notice test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T010 [P] [US1] Add failing Temporal detail success notice display/removal coverage in `frontend/src/entrypoints/task-detail.test.tsx`

### Implementation for User Story 1

- [ ] T011 [US1] Remove the Phase 2 edit-mode non-submitting guard while keeping rerun blocked in `frontend/src/entrypoints/task-create.tsx`
- [ ] T012 [US1] Submit edit mode through `POST /api/executions/{workflowId}/update` with `updateName: "UpdateInputs"` and `parametersPatch` in `frontend/src/entrypoints/task-create.tsx`
- [ ] T013 [US1] Interpret immediate, safe-point, and continue-as-new accepted outcomes into operator-readable success copy in `frontend/src/entrypoints/task-create.tsx`
- [ ] T014 [US1] Redirect successful edits to the Temporal detail route for the relevant workflow in `frontend/src/entrypoints/task-create.tsx`
- [ ] T015 [US1] Display and clear a one-time Temporal task editing success notice after detail navigation in `frontend/src/entrypoints/task-detail.tsx`

### Validation for User Story 1

- [ ] T016 [US1] Verify User Story 1 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx`

**Checkpoint**: Active edit submit is functional end-to-end for inline edited input.

---

## Phase 4: User Story 2 - Preserve Artifact Auditability (Priority: P2)

**Goal**: Artifact-backed or oversized edited task input creates a new artifact reference and never mutates or reuses historical input artifacts.

**Independent Test**: Edit an execution with an existing `inputArtifactRef`, submit, assert a new artifact is created/uploaded, and assert the update request references the new artifact rather than the historical one.

### Tests for User Story 2

- [ ] T017 [P] [US2] Add failing artifact-backed edit submit test that asserts new artifact creation and upload content in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T018 [P] [US2] Add failing oversized edited input externalization coverage in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T019 [P] [US2] Add failing assertion that historical `inputArtifactRef` is not reused as the edited input reference in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 2

- [ ] T020 [US2] Detect historical artifact-backed edit drafts and force creation of a fresh edited input artifact in `frontend/src/entrypoints/task-create.tsx`
- [ ] T021 [US2] Reuse existing artifact creation/upload/completion helpers for edited input externalization in `frontend/src/entrypoints/task-create.tsx`
- [ ] T022 [US2] Include the new `inputArtifactRef` in the `UpdateInputs` payload and edited `parametersPatch` when externalized in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 2

- [ ] T023 [US2] Verify User Story 2 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Artifact-backed and oversized edits preserve artifact immutability.

---

## Phase 5: User Story 3 - Explain Rejected Edits (Priority: P3)

**Goal**: Rejected or failed edit submissions show explicit messages and do not redirect as success.

**Independent Test**: Submit from edit mode after simulating stale terminal-state rejection, validation rejection, capability mismatch, and artifact preparation failure; verify each case shows a message and stays on the edit page.

### Tests for User Story 3

- [ ] T024 [P] [US3] Add failing stale terminal-state rejection test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T025 [P] [US3] Add failing backend validation/capability rejection message test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T026 [P] [US3] Add failing artifact creation/upload/completion failure test for edit submit in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [ ] T027 [US3] Surface backend update rejection messages without success redirect in `frontend/src/entrypoints/task-create.tsx`
- [ ] T028 [US3] Surface artifact preparation failures before submitting `UpdateInputs` in `frontend/src/entrypoints/task-create.tsx`
- [ ] T029 [US3] Ensure failed edit saves keep the operator in edit mode and do not set a success notice in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 3

- [ ] T030 [US3] Verify User Story 3 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Rejected edit outcomes are explicit and never masquerade as successful updates.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final regression coverage and runtime scope validation.

- [ ] T031 [P] Run TypeScript typecheck with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- [ ] T032 [P] Run focused frontend regression tests with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx`
- [ ] T033 [P] Verify no queue-era fallback appears in primary edit surfaces with `rg -n "editJobId|/tasks/queue/new|queue resubmit|queue update" frontend/src/entrypoints/task-create.tsx frontend/src/entrypoints/task-detail.tsx frontend/src/lib/temporalTaskEditing.ts`
- [ ] T034 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T035 Run runtime scope validation with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies; validates planning artifacts and commands.
- **Phase 2 (Foundational)**: Depends on Phase 1; blocks all user stories because shared payload and endpoint helpers are required.
- **Phase 3 (US1)**: Depends on Phase 2; delivers the MVP active edit submit.
- **Phase 4 (US2)**: Depends on Phase 2 and can begin after helper contracts stabilize, but full artifact-submit behavior integrates most directly after US1.
- **Phase 5 (US3)**: Depends on Phase 2 and can proceed alongside US1/US2 tests if file edits are coordinated.
- **Phase 6 (Polish)**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2 and is the recommended MVP scope.
- **US2 (P2)**: Uses the same submit path as US1 and adds artifact safety; can be implemented after or alongside US1 once helper contracts exist.
- **US3 (P3)**: Uses the same submit path as US1 and adds failure handling; can be implemented after or alongside US1 once helper contracts exist.

### Within Each User Story

- Write and run failing tests first.
- Implement shared helper changes before page-level submit wiring.
- Implement submit behavior before success/failure UX assertions are finalized.
- Run story-specific validation before moving to the next story or final polish.

### Parallel Opportunities

- T001-T003 can run in parallel because they inspect independent planning artifacts.
- T004 and T005 can run in parallel because they cover separate helper behaviors.
- T008-T010 can run in parallel before US1 implementation because they touch test assertions in two files.
- T017-T019 can run in parallel because they are independent artifact-safety test cases in the same test file if coordinated.
- T024-T026 can run in parallel because they are independent failure-mode test cases in the same test file if coordinated.
- T031-T033 can run in parallel before the full unit wrapper.

---

## Parallel Example: User Story 1

```text
Task: "T008 active edit submit request-shape test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T009 active edit redirect and success notice test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T010 detail success notice display/removal test in frontend/src/entrypoints/task-detail.test.tsx"
```

## Parallel Example: User Story 2

```text
Task: "T017 artifact-backed edit submit test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T018 oversized edited input externalization test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T019 historical artifact ref non-reuse assertion in frontend/src/entrypoints/task-create.test.tsx"
```

## Parallel Example: User Story 3

```text
Task: "T024 stale terminal-state rejection test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T025 backend validation/capability rejection message test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T026 artifact preparation failure test in frontend/src/entrypoints/task-create.test.tsx"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup checks.
2. Complete Phase 2 shared helper work.
3. Complete Phase 3 active inline edit submit.
4. Validate with focused frontend tests and typecheck.

### Incremental Delivery

1. US1: Active edit submit to `UpdateInputs` with success redirect.
2. US2: Artifact-safe edited input externalization.
3. US3: Explicit rejected/failed submit handling.
4. Polish: queue fallback scan, full unit suite, runtime scope validation.

### Runtime Scope Guard

- Production runtime implementation tasks: T006, T007, T011-T015, T020-T022, T027-T029.
- Validation tasks: T001-T005, T008-T010, T016-T019, T023-T026, T030-T035.
- The task list includes production frontend runtime changes and automated validation; docs-only work is insufficient.
