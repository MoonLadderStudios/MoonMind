# Tasks: Temporal Task Editing Entry Points

**Input**: Design documents from `/specs/157-temporal-task-editing/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required. This feature is runtime-scoped and explicitly requires production runtime code changes plus validation tests.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Phase 1: Setup (Shared Test and Contract Scaffolding)

**Purpose**: Establish the test-first harness and shared contract files used by all user stories.

- [ ] T001 [P] Add failing dashboard runtime-config flag coverage for `temporalTaskEditing` in `tests/unit/api/routers/test_task_dashboard_view_model.py` (Validation: DOC-REQ-011)
- [ ] T002 [P] Add failing execution detail read-contract and capability coverage in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-001, DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-009)
- [ ] T003 [P] Add failing task-detail route helper and visibility coverage in `frontend/src/entrypoints/task-detail.test.tsx` (Validation: DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-012)
- [ ] T004 [P] Add contract-fixture assertions for update names and artifact-safe payload shape in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-008, DOC-REQ-010)
- [ ] T005 Verify the OpenAPI planning contract parses cleanly in `specs/157-temporal-task-editing/contracts/temporal-task-editing.openapi.yaml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared runtime settings, read-model fields, and route helpers that all user stories rely on.

**CRITICAL**: No user story implementation should begin until this phase is complete.

- [ ] T006 Add `TEMPORAL_TASK_EDITING_ENABLED` runtime setting in `moonmind/config/settings.py` (Implementation: DOC-REQ-011)
- [ ] T007 Surface `features.temporalDashboard.temporalTaskEditing` in `api_service/api/routers/task_dashboard_view_model.py` (Implementation: DOC-REQ-011)
- [ ] T008 Extend `ExecutionModel` with `inputParameters` and `inputArtifactRef` in `moonmind/schemas/temporal_models.py` (Implementation: DOC-REQ-001, DOC-REQ-007)
- [ ] T009 Populate `inputParameters`, `inputArtifactRef`, and run `planArtifactRef` in `api_service/api/routers/executions.py` (Implementation: DOC-REQ-001, DOC-REQ-007)
- [ ] T010 Create typed Temporal task editing route and contract helpers in `frontend/src/lib/temporalTaskEditing.ts` (Implementation: DOC-REQ-003, DOC-REQ-011)

**Checkpoint**: Runtime flag, execution read fields, and canonical route helpers exist for user-story work.

---

## Phase 3: User Story 1 - Align Temporal Editing Contracts (Priority: P1) 🎯 MVP

**Goal**: Supported and unsupported execution detail responses expose enough state for safe edit/rerun decisions without queue fallback.

**Independent Test**: Read supported and unsupported execution details and verify workflow identity, workflow type, input state, artifact refs, configuration state, capability flags, and disabled reasons.

### Tests for User Story 1

- [ ] T011 [P] [US1] Assert supported active `MoonMind.Run` execution detail includes `workflowId`, `workflowType`, `inputParameters`, `inputArtifactRef`, `planArtifactRef`, runtime/model/repository state, and `canUpdateInputs` in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-001, DOC-REQ-007)
- [ ] T012 [P] [US1] Assert supported terminal `MoonMind.Run` execution detail exposes `canRerun` and does not expose in-place edit semantics in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-005, DOC-REQ-006)
- [ ] T013 [P] [US1] Assert unsupported workflow types and feature-disabled states return false edit/rerun capabilities with explicit reasons in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-002, DOC-REQ-009, DOC-REQ-011)
- [ ] T014 [P] [US1] Assert `UpdateInputs` and `RequestRerun` update-name contract remains recognized in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-008)

### Implementation for User Story 1

- [ ] T015 [US1] Gate `canUpdateInputs` and `canRerun` by `MoonMind.Run`, lifecycle state, actions enabled, and `temporalTaskEditing` in `api_service/api/routers/executions.py` (Implementation: DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-009, DOC-REQ-011)
- [ ] T016 [US1] Add disabled reason values for feature-disabled, unsupported workflow type, and state-ineligible edit/rerun actions in `api_service/api/routers/executions.py` (Implementation: DOC-REQ-002, DOC-REQ-009, DOC-REQ-011)
- [ ] T017 [US1] Preserve `UpdateInputs` and `RequestRerun` request-envelope compatibility in `moonmind/schemas/temporal_models.py` and `moonmind/workflows/temporal/service.py` (Implementation: DOC-REQ-008, DOC-REQ-010)

**Checkpoint**: User Story 1 is independently functional: execution detail carries the safe read contract and authoritative capabilities.

---

## Phase 4: User Story 2 - Navigate From Detail to Edit or Rerun (Priority: P2)

**Goal**: Operators can navigate from task detail to the canonical shared submit routes only when the execution supports the action.

**Independent Test**: Render active, terminal, unsupported, and flag-disabled fixtures and verify Edit/Rerun visibility plus route targets.

### Tests for User Story 2

- [ ] T018 [P] [US2] Assert `taskEditHref` and `taskRerunHref` produce canonical `/tasks/new` targets and URL-encode workflow IDs in `frontend/src/entrypoints/task-detail.test.tsx` (Validation: DOC-REQ-003, DOC-REQ-004)
- [ ] T019 [P] [US2] Assert Edit renders only when `temporalTaskEditing` and `actions.canUpdateInputs` are true in `frontend/src/entrypoints/task-detail.test.tsx` (Validation: DOC-REQ-005, DOC-REQ-006, DOC-REQ-012)
- [ ] T020 [P] [US2] Assert Rerun renders only when `temporalTaskEditing` and `actions.canRerun` are true in `frontend/src/entrypoints/task-detail.test.tsx` (Validation: DOC-REQ-005, DOC-REQ-006, DOC-REQ-012)
- [ ] T021 [P] [US2] Assert unsupported actions are omitted and no queue-era route or `editJobId` target is generated in `frontend/src/entrypoints/task-detail.test.tsx` (Validation: DOC-REQ-004, DOC-REQ-009, DOC-REQ-012)

### Implementation for User Story 2

- [ ] T022 [US2] Extend `ExecutionDetailSchema` to parse `actions.canUpdateInputs` in `frontend/src/entrypoints/task-detail.tsx` (Implementation: DOC-REQ-005, DOC-REQ-012)
- [ ] T023 [US2] Read `features.temporalDashboard.temporalTaskEditing` in `frontend/src/entrypoints/task-detail.tsx` (Implementation: DOC-REQ-011, DOC-REQ-012)
- [ ] T024 [US2] Replace direct detail-page rerun mutation with canonical Edit and Rerun links in `frontend/src/entrypoints/task-detail.tsx` (Implementation: DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-012)
- [ ] T025 [US2] Ensure terminal executions only expose rerun, not in-place edit messaging, in `frontend/src/entrypoints/task-detail.tsx` (Implementation: DOC-REQ-006)

**Checkpoint**: User Story 2 is independently functional: valid detail pages navigate to canonical edit/rerun routes and invalid actions are hidden.

---

## Phase 5: User Story 3 - Establish Rollout Fixtures and Operator Copy (Priority: P3)

**Goal**: Local and CI fixtures make supported, unsupported, active, terminal, and flag-disabled states repeatable for rollout and later phases.

**Independent Test**: Run fixture-backed backend and frontend tests and verify each unsupported state has explicit, non-queue behavior.

### Tests for User Story 3

- [ ] T026 [P] [US3] Add supported active, supported terminal, unsupported workflow, and feature-disabled backend fixtures in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-009, DOC-REQ-011, DOC-REQ-012)
- [ ] T027 [P] [US3] Add supported active, supported terminal, unsupported workflow, and feature-disabled frontend fixtures in `frontend/src/entrypoints/task-detail.test.tsx` (Validation: DOC-REQ-009, DOC-REQ-011, DOC-REQ-012)
- [ ] T028 [P] [US3] Assert unsupported-state copy and disabled reasons distinguish unsupported workflow type, feature-disabled, state-ineligible, malformed input, and unavailable artifact cases in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-009, DOC-REQ-011)
- [ ] T029 [P] [US3] Assert artifact-backed edit/rerun contracts use new artifact references rather than historical mutation in `tests/unit/api/routers/test_executions.py` (Validation: DOC-REQ-010)

### Implementation for User Story 3

- [ ] T030 [US3] Normalize disabled reason labels and unsupported-state copy constants for task editing states in `api_service/api/routers/executions.py` and `frontend/src/entrypoints/task-detail.tsx` (Implementation: DOC-REQ-009, DOC-REQ-011)
- [ ] T031 [US3] Keep first-slice prefill fields represented in frontend/backend typed contracts in `frontend/src/lib/temporalTaskEditing.ts` and `moonmind/schemas/temporal_models.py` (Implementation: DOC-REQ-007, DOC-REQ-011)
- [ ] T032 [US3] Verify no primary flow introduces `editJobId`, `/tasks/queue/new`, queue update routes, or queue resubmit language in `frontend/src/lib/temporalTaskEditing.ts` and `frontend/src/entrypoints/task-detail.tsx` (Implementation: DOC-REQ-004)

**Checkpoint**: User Story 3 is independently functional: rollout states are reproducible and unsupported behavior fails closed.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, traceability, and readiness gates across all stories.

- [ ] T033 [P] Run targeted backend tests with `pytest tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/api/routers/test_executions.py -q` (Validation: DOC-REQ-001, DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012)
- [ ] T034 [P] Run targeted frontend tests with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx` (Validation: DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-009, DOC-REQ-011, DOC-REQ-012)
- [ ] T035 [P] Run frontend typecheck with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- [ ] T036 Run full unit suite with `./tools/test_unit.sh` (Validation: DOC-REQ-011, DOC-REQ-012)
- [ ] T037 Run runtime scope validation with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [ ] T038 Verify every `DOC-REQ-*` appears in at least one implementation task and one validation task in `specs/157-temporal-task-editing/tasks.md`
- [ ] T039 Update `specs/157-temporal-task-editing/contracts/requirements-traceability.md` if implementation file paths or validation commands change during execution

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies; establishes failing tests and contract checks.
- **Phase 2 (Foundational)**: Depends on Phase 1 test scaffolding; blocks user-story implementation.
- **Phase 3 (US1)**: Depends on Phase 2; delivers the MVP read contract and backend capability gates.
- **Phase 4 (US2)**: Depends on Phase 2 and benefits from US1 backend capabilities; restores detail-page navigation.
- **Phase 5 (US3)**: Depends on Phase 2 and may proceed in parallel with US1/US2 after fixture shapes stabilize.
- **Phase 6 (Polish)**: Depends on all selected user stories.

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2; no dependency on US2 or US3.
- **US2 (P2)**: Starts after Phase 2; requires the route helper from T010 and uses capability shapes from US1 tests.
- **US3 (P3)**: Starts after Phase 2; expands fixture/copy coverage and can proceed alongside US1/US2 if files are coordinated.

### Within Each User Story

- Write and run tests first; they should fail before corresponding implementation.
- Backend model/config changes precede API serialization and capability changes.
- Route helpers precede task-detail navigation changes.
- Story checkpoints must pass before treating that story as complete.

### Parallel Opportunities

- T001-T004 can run in parallel because they touch different tests or contract checks.
- T006-T010 can run in parallel after setup, except downstream tasks must respect file ownership.
- US1 tests T011-T014 can run in parallel.
- US2 tests T018-T021 can run in parallel.
- US3 tests T026-T029 can run in parallel.
- Final validation T033-T035 can run in parallel before full `./tools/test_unit.sh`.

---

## Parallel Example: User Story 1

```text
Task: "T011 backend read contract test in tests/unit/api/routers/test_executions.py"
Task: "T012 terminal rerun capability test in tests/unit/api/routers/test_executions.py"
Task: "T013 unsupported workflow and feature-disabled capability test in tests/unit/api/routers/test_executions.py"
Task: "T014 update-name contract test in tests/unit/api/routers/test_executions.py"
```

## Parallel Example: User Story 2

```text
Task: "T018 route helper canonical target test in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T019 Edit visibility test in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T020 Rerun visibility test in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T021 queue-era route omission test in frontend/src/entrypoints/task-detail.test.tsx"
```

## Parallel Example: User Story 3

```text
Task: "T026 backend fixture coverage in tests/unit/api/routers/test_executions.py"
Task: "T027 frontend fixture coverage in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T028 unsupported-state copy assertions in tests/unit/api/routers/test_executions.py"
Task: "T029 artifact immutability contract assertions in tests/unit/api/routers/test_executions.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup tests.
2. Complete Phase 2 shared runtime contract scaffolding.
3. Complete Phase 3 backend read contract and capability gating.
4. Validate with T033 targeted backend tests.

### Incremental Delivery

1. US1: Backend read contract and capability flags.
2. US2: Detail-page Edit/Rerun navigation through canonical `/tasks/new` routes.
3. US3: Rollout fixtures, unsupported-state copy, and artifact-safety guardrails.
4. Phase 6: Full validation and traceability gates.

### Runtime Scope Guard

- Runtime production implementation tasks are T006-T010, T015-T017, T022-T025, and T030-T032.
- Validation tasks are T001-T004, T011-T014, T018-T021, T026-T029, and T033-T038.
- Every `DOC-REQ-*` appears in at least one implementation task and one validation task.
