# Tasks: Temporal Rerun Submit

**Input**: Design documents from `specs/169-temporal-rerun-submit/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Automated runtime regression tests are required. Use TDD: add or update the failing tests before production code changes, then run focused and final validation.

**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing Temporal task editing surfaces before story work begins.

- [X] T001 Review the current Temporal task form mode helpers and payload builder in `frontend/src/lib/temporalTaskEditing.ts`
- [X] T002 Review the current shared task form submit path, artifact preparation, and redirect behavior in `frontend/src/entrypoints/task-create.tsx`
- [X] T003 [P] Review the execution update request schema and router forwarding for `RequestRerun` in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py`
- [X] T004 [P] Review existing terminal rerun service behavior in `moonmind/workflows/temporal/service.py` and `tests/unit/workflows/temporal/test_temporal_service.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared test fixtures and helper coverage that all rerun stories depend on.

**CRITICAL**: No user story implementation should begin until these foundation tasks are complete.

- [X] T005 [P] Add or update shared terminal `MoonMind.Run` rerun execution fixtures in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T006 [P] Add or update stale rerun rejection and artifact-backed rerun fixture responses in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T007 Add a failing route precedence regression test proving `rerunExecutionId` wins over `editExecutionId` in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T008 Add a failing payload-builder regression test for `RequestRerun`, `parametersPatch`, and replacement `inputArtifactRef` in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T009 Ensure `resolveTaskSubmitPageMode` preserves rerun-first precedence and update-name types represent both edit and rerun in `frontend/src/lib/temporalTaskEditing.ts`

**Checkpoint**: Shared fixtures, route precedence, and helper contract coverage are ready. User story work can proceed.

---

## Phase 3: User Story 1 - Rerun a Terminal Task (Priority: P1)

**Goal**: Operators can submit a supported terminal `MoonMind.Run` rerun from the shared task form with `RequestRerun`, without create or queue fallback.

**Independent Test**: Open `/tasks/new?rerunExecutionId=<workflowId>`, submit a reconstructed draft, and verify the request uses the source execution update path with `updateName: "RequestRerun"`.

### Tests for User Story 1

- [X] T010 [US1] Add a failing rerun submit test that asserts `/api/executions/{workflowId}/update` receives `updateName: "RequestRerun"` in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T011 [US1] Add a failing rerun submit test that asserts the normal task create endpoint is not called from rerun mode in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T012 [P] [US1] Add or update backend contract coverage that `RequestRerun` remains an accepted update name in `tests/unit/api/routers/test_executions.py`

### Implementation for User Story 1

- [X] T013 [US1] Replace the rerun milestone block with shared Temporal update submission handling in `frontend/src/entrypoints/task-create.tsx`
- [X] T014 [US1] Select `RequestRerun` for rerun mode and preserve `UpdateInputs` for edit mode in `frontend/src/entrypoints/task-create.tsx`
- [X] T015 [US1] Keep rerun submit routing on `configuredTemporalUpdateUrl(...)` for the source workflow in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 1

- [X] T016 [US1] Run focused UI coverage for terminal rerun submit with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Story 1 is independently functional and rerun submission uses the Temporal-native update path.

---

## Phase 4: User Story 2 - Preserve Rerun Lineage (Priority: P2)

**Goal**: Rerun requests preserve source execution context, replacement input artifact references, backend application mode, and latest-run success behavior.

**Independent Test**: Submit a modified artifact-backed rerun draft and verify source workflow id, replacement artifact ref, backend applied mode, and returned workflow context remain visible in request/response handling.

### Tests for User Story 2

- [X] T017 [US2] Add a failing artifact-backed rerun test that asserts a new input artifact is created before `RequestRerun` in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T018 [US2] Add a failing lineage source test that asserts replacement artifact creation carries `sourceWorkflowId` or an equivalent source execution lineage field in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T019 [US2] Add a failing lineage success test that asserts accepted reruns preserve backend `applied` mode and redirect to `/tasks/{workflowId}?source=temporal` or the returned workflow context in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T020 [P] [US2] Add or update service-level coverage for rerun input and parameter overrides in `tests/unit/workflows/temporal/test_temporal_service.py`

### Implementation for User Story 2

- [X] T021 [US2] Reuse edit-mode artifact preparation for rerun mode and assign replacement `inputArtifactRef` in `frontend/src/entrypoints/task-create.tsx`
- [X] T022 [US2] Include source execution lineage when creating rerun replacement input artifacts in `frontend/src/entrypoints/task-create.tsx`
- [X] T023 [US2] Store rerun-specific accepted-state copy based on backend `applied` mode in `moonmind.temporalTaskEditing.notice` without reusing edit success language in `frontend/src/entrypoints/task-create.tsx`
- [X] T024 [US2] Redirect accepted rerun submissions to the returned execution workflow id when present, otherwise the source workflow id, in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 2

- [X] T025 [US2] Verify artifact-backed rerun lineage behavior with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Stories 1 and 2 preserve rerun submission semantics, explicit source lineage, and latest-run success context.

---

## Phase 5: User Story 3 - Block Unsupported or Stale Reruns (Priority: P3)

**Goal**: Unsupported rerun attempts and stale backend lifecycle rejections show explicit errors and never redirect as success.

**Independent Test**: Load unsupported or stale rerun states and verify the form blocks or surfaces errors without calling queue/create paths or redirecting.

### Tests for User Story 3

- [X] T026 [US3] Add a failing backend stale-state rerun rejection test that asserts the error is shown and `navigateTo` is not called in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T027 [US3] Add or update reconstruction failure tests for missing and malformed input artifacts in rerun mode in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [X] T028 [US3] Surface rerun-specific backend rejection fallback messages without redirecting in `frontend/src/entrypoints/task-create.tsx`
- [X] T029 [US3] Ensure rerun mode still requires `actions.canRerun` and supported workflow type before submit in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 3

- [X] T030 [US3] Verify stale, unsupported, and malformed-artifact rerun behavior with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: All user stories have explicit negative-path behavior and no queue-era fallback.

---

## Phase 6: Polish & Cross-Cutting Validation

**Purpose**: Confirm the complete runtime scope and update validation guidance.

- [X] T031 [P] Update final rerun validation notes in `specs/169-temporal-rerun-submit/quickstart.md`
- [X] T032 Run TypeScript validation with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- [X] T033 Run focused lint validation with `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src/entrypoints/task-create.tsx frontend/src/entrypoints/task-create.test.tsx`
- [X] T034 Run final unit verification with `./tools/test_unit.sh`
- [X] T035 Run runtime task scope validation with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1; blocks user story work.
- **Phase 3 / US1**: Depends on Phase 2; delivers the MVP rerun submit path.
- **Phase 4 / US2**: Depends on US1 submit plumbing.
- **Phase 5 / US3**: Depends on US1 submit plumbing and can run in parallel with most US2 work after T013-T015 are complete.
- **Phase 6 Polish**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 (P1)**: MVP. Required before lineage and stale rejection submit behavior.
- **US2 (P2)**: Depends on US1 because artifact lineage and success redirect use the rerun submit path.
- **US3 (P3)**: Depends on US1 because stale rejection handling is part of the shared rerun submit path.

### Within Each User Story

- Test tasks must be completed first and fail for the expected reason before implementation tasks.
- Implementation tasks must be completed before the story validation task.
- Backend/service validation tasks marked `[P]` can run alongside frontend tests when they touch different files.

## Parallel Opportunities

- T003 and T004 can run in parallel during setup.
- T005 and T006 can run in parallel because they add independent fixture cases.
- T012 can run in parallel with T010-T011 if backend contract coverage is needed.
- T020 can run in parallel with T017-T019 because service tests are separate from frontend UI tests.
- T031 can run in parallel with T032-T033 after implementation is complete.

## Parallel Example: User Story 1

```bash
# Add independent tests first:
Task: "T010 Add rerun submit RequestRerun coverage in frontend/src/entrypoints/task-create.test.tsx"
Task: "T011 Add no-create-endpoint rerun coverage in frontend/src/entrypoints/task-create.test.tsx"
Task: "T012 Add backend update-name contract coverage in tests/unit/api/routers/test_executions.py"

# Then implement shared rerun submission:
Task: "T013 Replace rerun milestone block in frontend/src/entrypoints/task-create.tsx"
Task: "T014 Select RequestRerun for rerun and UpdateInputs for edit in frontend/src/entrypoints/task-create.tsx"
Task: "T015 Keep rerun submit on configuredTemporalUpdateUrl in frontend/src/entrypoints/task-create.tsx"
```

## Implementation Strategy

### MVP First

Complete Phases 1-3 to deliver terminal rerun submission through `RequestRerun` without create or queue fallback.

### Incremental Delivery

1. Finish US1 and validate the shared form submits the correct update name.
2. Add US2 artifact replacement and lineage success behavior.
3. Add US3 stale/unsupported rejection handling.
4. Run Phase 6 validation before marking the feature ready for implementation completion.

### Runtime Scope

This task set includes production runtime changes under `frontend/src/` and validation tasks under `frontend/src/*.test.tsx`, `tests/unit/`, and explicit validation commands. Docs-only completion is not sufficient.
