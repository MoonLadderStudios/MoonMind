# Tasks: Temporal Task Draft Reconstruction

**Input**: Design documents from `/specs/161-temporal-task-drafts/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. The feature request requires test-driven development plus production runtime code changes and validation tests.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Phase 1: Setup (Shared Test and Contract Scaffolding)

**Purpose**: Establish shared fixtures and contract expectations before production code changes.

- [ ] T001 [P] Add shared edit/rerun execution fixtures with inline and artifact-backed instructions in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T002 [P] Add route mode precedence tests for create, edit, rerun, and both identifiers in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T003 [P] Add draft reconstruction helper tests for first-slice fields and missing instructions in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T004 [P] Add contract shape assertions for execution detail and artifact-backed draft inputs in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T005 Verify the OpenAPI planning contract parses cleanly in `specs/161-temporal-task-drafts/contracts/temporal-task-drafts.openapi.yaml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared route, mode, and draft contracts that every user story depends on.

**CRITICAL**: No user story implementation should begin until this phase is complete.

- [ ] T006 Create `TaskSubmitPageModeResolution` and `TemporalSubmissionDraft` types in `frontend/src/lib/temporalTaskEditing.ts`
- [ ] T007 Implement canonical `resolveTaskSubmitPageMode` mode precedence helper in `frontend/src/lib/temporalTaskEditing.ts`
- [ ] T008 Extend `TemporalTaskEditingExecutionContract` with all Phase 2 draft source fields in `frontend/src/lib/temporalTaskEditing.ts`
- [ ] T009 Add `buildTemporalSubmissionDraftFromExecution` skeleton with explicit incomplete-draft failure in `frontend/src/lib/temporalTaskEditing.ts`
- [ ] T010 Read `features.temporalDashboard.temporalTaskEditing` in `frontend/src/entrypoints/task-create.tsx`
- [ ] T011 [P] Add execution detail read-contract regression coverage for Phase 2 draft source fields in `tests/unit/api/routers/test_executions.py`
- [ ] T012 Ensure execution detail serialization continues exposing Phase 2 draft source fields in `api_service/api/routers/executions.py`

**Checkpoint**: Shared mode and draft contracts exist; user-story implementation can proceed.

---

## Phase 3: User Story 1 - Resolve Submit Page Mode (Priority: P1) 🎯 MVP

**Goal**: The shared task submit page reliably resolves create, edit, and rerun modes without queue-era fallback.

**Independent Test**: Open `/tasks/new`, `/tasks/new?editExecutionId=<workflowId>`, `/tasks/new?rerunExecutionId=<workflowId>`, and a URL with both identifiers; verify resolved mode, selected execution, feature-flag refusal, and no execution read in create mode.

### Tests for User Story 1

- [ ] T013 [P] [US1] Add failing create-mode no-execution-load test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T014 [P] [US1] Add failing edit-mode route parsing and title/CTA test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T015 [P] [US1] Add failing rerun-mode precedence and title/CTA test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T016 [P] [US1] Add failing feature-flag-disabled edit/rerun error test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T017 [P] [US1] Add failing no queue-era route or `editJobId` assertion in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 1

- [ ] T018 [US1] Wire `resolveTaskSubmitPageMode` into `TaskCreatePage` in `frontend/src/entrypoints/task-create.tsx`
- [ ] T019 [US1] Render mode-specific page title and primary CTA in `frontend/src/entrypoints/task-create.tsx`
- [ ] T020 [US1] Show feature-disabled edit/rerun error state without loading execution detail in `frontend/src/entrypoints/task-create.tsx`
- [ ] T021 [US1] Prevent edit/rerun modes from submitting through create semantics in `frontend/src/entrypoints/task-create.tsx`
- [ ] T022 [US1] Ensure create mode keeps existing task creation behavior unchanged in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: User Story 1 is independently functional: mode resolution works and create mode remains unchanged.

---

## Phase 4: User Story 2 - Reconstruct a Reviewable Draft (Priority: P2)

**Goal**: Supported `MoonMind.Run` edit and rerun modes load execution detail and prefill a trustworthy shared task draft.

**Independent Test**: Use supported active and terminal `MoonMind.Run` fixtures with inline and artifact-backed instructions; verify all available first-slice fields are prefilled into the shared form.

### Tests for User Story 2

- [ ] T023 [P] [US2] Add failing inline-instruction draft reconstruction test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T024 [P] [US2] Add failing artifact-backed instruction reconstruction test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T025 [P] [US2] Add failing edit-mode form prefill test for runtime/profile/model/effort/repository/branches/publish/skill in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T026 [P] [US2] Add failing rerun-mode form prefill test with artifact download in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T027 [P] [US2] Add failing applied template state reconstruction test in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 2

- [ ] T028 [US2] Complete `buildTemporalSubmissionDraftFromExecution` field mapping in `frontend/src/lib/temporalTaskEditing.ts`
- [ ] T029 [US2] Add immutable input artifact read helper for artifact-backed instructions in `frontend/src/entrypoints/task-create.tsx`
- [ ] T030 [US2] Load `/api/executions/{workflowId}?source=temporal` for edit and rerun modes in `frontend/src/entrypoints/task-create.tsx`
- [ ] T031 [US2] Apply reconstructed draft state to the shared form fields in `frontend/src/entrypoints/task-create.tsx`
- [ ] T032 [US2] Preserve provider profile/model defaults without overwriting reconstructed draft values in `frontend/src/entrypoints/task-create.tsx`
- [ ] T033 [US2] Restore applied template state from reconstructed draft data in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: User Story 2 is independently functional: supported executions open with reviewable prefilled drafts.

---

## Phase 5: User Story 3 - Refuse Unsafe or Incomplete Drafts (Priority: P3)

**Goal**: Unsupported or incomplete Temporal edit/rerun states fail explicitly and never render misleading partial submit state.

**Independent Test**: Use unsupported workflow, missing capability, unreadable artifact, malformed artifact, and incomplete draft fixtures; verify each produces an explicit error and disables submit-ready behavior.

### Tests for User Story 3

- [ ] T034 [P] [US3] Add failing unsupported workflow type error test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T035 [P] [US3] Add failing missing edit capability error test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T036 [P] [US3] Add failing missing rerun capability error test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T037 [P] [US3] Add failing unreadable input artifact error test in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T038 [P] [US3] Add failing malformed artifact and incomplete draft error tests in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T039 [P] [US3] Add failing schedule-controls-hidden test for edit and rerun modes in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [ ] T040 [US3] Validate `MoonMind.Run` workflow type before draft application in `frontend/src/entrypoints/task-create.tsx`
- [ ] T041 [US3] Validate `actions.canUpdateInputs` for edit mode and `actions.canRerun` for rerun mode in `frontend/src/entrypoints/task-create.tsx`
- [ ] T042 [US3] Convert artifact read failures and JSON parse failures into operator-readable errors in `frontend/src/entrypoints/task-create.tsx`
- [ ] T043 [US3] Prevent incomplete drafts from being applied to submit-ready form state in `frontend/src/entrypoints/task-create.tsx`
- [ ] T044 [US3] Hide recurring schedule controls outside create mode in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: All user stories are independently functional and unsafe states fail closed.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup across all stories.

- [ ] T045 [P] Run targeted task-create Vitest coverage with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`
- [ ] T046 [P] Run existing task-detail Vitest coverage with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx`
- [ ] T047 [P] Run frontend TypeScript typecheck with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- [ ] T048 Run full unit suite with `./tools/test_unit.sh`
- [ ] T049 Run runtime scope validation with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [ ] T050 Verify no task editing code path introduces `editJobId`, `/tasks/queue/new`, queue update routes, or queue resubmit wording in `frontend/src/lib/temporalTaskEditing.ts` and `frontend/src/entrypoints/task-create.tsx`
- [ ] T051 Update `specs/161-temporal-task-drafts/quickstart.md` if validation commands change during implementation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies; establishes failing tests and fixtures.
- **Phase 2 (Foundational)**: Depends on Phase 1; blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2; delivers the MVP mode-resolution slice.
- **Phase 4 (US2)**: Depends on Phase 2 and uses US1 page mode plumbing.
- **Phase 5 (US3)**: Depends on Phase 2 and can proceed after error-state fixture shapes stabilize.
- **Phase 6 (Polish)**: Depends on all selected user stories.

### User Story Dependencies

- **User Story 1 (P1)**: Starts after Foundational; no dependency on US2 or US3.
- **User Story 2 (P2)**: Starts after Foundational; consumes the mode model from US1 but remains independently testable with direct route fixtures.
- **User Story 3 (P3)**: Starts after Foundational; validates failure behavior around the same load/reconstruction path used by US2.

### Within Each User Story

- Write tests first and confirm they fail before implementing.
- Shared helper types precede page integration.
- Mode resolution precedes execution loading.
- Execution capability validation precedes draft application.
- Draft reconstruction precedes form prefill assertions.
- Error handling precedes final validation.

### Parallel Opportunities

- T001-T004 can run in parallel because they add independent test fixtures/assertions.
- T006-T012 can run in parallel except when editing the same helper/page/API file; coordinate file ownership.
- US1 test tasks T013-T017 can run in parallel.
- US2 test tasks T023-T027 can run in parallel.
- US3 test tasks T034-T039 can run in parallel.
- Final validation tasks T045-T047 can run in parallel before the full unit suite.

---

## Parallel Example: User Story 1

```text
Task: "T013 [US1] create-mode no-execution-load test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T014 [US1] edit-mode route parsing and title/CTA test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T015 [US1] rerun-mode precedence and title/CTA test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T016 [US1] feature-flag-disabled edit/rerun error test in frontend/src/entrypoints/task-create.test.tsx"
```

## Parallel Example: User Story 2

```text
Task: "T023 [US2] inline-instruction draft reconstruction test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T024 [US2] artifact-backed instruction reconstruction test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T025 [US2] edit-mode form prefill test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T026 [US2] rerun-mode form prefill test in frontend/src/entrypoints/task-create.test.tsx"
```

## Parallel Example: User Story 3

```text
Task: "T034 [US3] unsupported workflow type error test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T035 [US3] missing edit capability error test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T037 [US3] unreadable input artifact error test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T039 [US3] schedule-controls-hidden test in frontend/src/entrypoints/task-create.test.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup tests and fixtures.
2. Complete Phase 2 shared mode/draft contracts.
3. Complete Phase 3 mode-resolution page behavior.
4. Validate with targeted task-create Vitest coverage.

### Incremental Delivery

1. US1: Mode-aware shared submit page with no queue fallback.
2. US2: Supported execution loading and trustworthy draft prefill.
3. US3: Explicit refusal for unsupported and incomplete states.
4. Phase 6: Typecheck, targeted tests, full unit suite, and runtime scope validation.

### Runtime Scope Guard

- Production runtime implementation tasks are T006-T010, T012, T018-T022, T028-T033, and T040-T044.
- Validation tasks are T001-T005, T011, T013-T017, T023-T027, T034-T039, and T045-T050.
- The feature is incomplete unless at least one production runtime code task and one validation task are completed.
