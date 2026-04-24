# Tasks: Temporal Editing Hardening

**Input**: Design documents from `/specs/170-temporal-editing-hardening/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Required. This is runtime work and must use test-driven development with failing automated tests before production runtime changes.
**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes plus validation tests.
**Traceability**: No document requirement identifiers are present in `spec.md`; `contracts/requirements-traceability.md` is not required for this feature.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete tasks.
- **[Story]**: User story label for story phases only.
- Every task includes an exact file path or validation script path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing Temporal task editing surfaces and test entrypoints before story work begins.

- [X] T001 Review existing Temporal task editing helper exports and route helpers in `frontend/src/lib/temporalTaskEditing.ts`
- [X] T002 [P] Review current detail-page edit/rerun action handling in `frontend/src/entrypoints/task-detail.tsx`
- [X] T003 [P] Review current shared task form edit/rerun mode handling in `frontend/src/entrypoints/task-create.tsx`
- [X] T004 [P] Review current execution update endpoint behavior in `api_service/api/routers/executions.py`
- [X] T005 [P] Review runtime feature flag exposure for `temporalTaskEditing` in `moonmind/config/settings.py` and `api_service/api/routers/task_dashboard_view_model.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared telemetry contracts and bounded failure reason vocabulary used by all stories.

**CRITICAL**: No user story implementation should begin until this phase is complete.

### Tests for Shared Foundations

- [X] T006 [P] Add failing unit coverage for bounded client telemetry event names and failure reasons in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T007 [P] Add failing unit coverage for server-side update telemetry dimensions and raw-payload exclusion in `tests/unit/api/routers/test_executions.py`
- [X] T008 [P] Add failing unit coverage for `temporalTaskEditing` runtime flag exposure in `tests/unit/api/routers/test_task_dashboard_view_model.py`

### Implementation for Shared Foundations

- [X] T009 Implement bounded Temporal task editing telemetry types, event names, and failure reason normalization in `frontend/src/lib/temporalTaskEditing.ts`
- [X] T010 Implement best-effort server telemetry helpers for task editing update attempts/results in `api_service/api/routers/executions.py`
- [X] T011 Ensure `temporalTaskEditing` remains runtime-visible and configurable for local/staging readiness in `moonmind/config/settings.py` and `api_service/api/routers/task_dashboard_view_model.py`

### Validation for Shared Foundations

- [X] T012 Run foundation tests with `pytest tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_dashboard_view_model.py -q` and `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Shared telemetry and rollout foundations are ready. User story implementation can now begin in priority order or parallel by separate owners.

---

## Phase 3: User Story 1 - Observe Editing and Rerun Outcomes (Priority: P1)

**Goal**: Operators and maintainers can observe edit/rerun clicks, reconstruction outcomes, submit attempts, submit results, and bounded failure reasons.
**Independent Test**: Exercise supported and failing edit/rerun paths and verify bounded client/server telemetry is emitted without blocking runtime behavior.

### Tests for User Story 1

> Write these tests first and confirm they fail for the expected reason before implementation.

- [X] T013 [P] [US1] Add failing detail-page telemetry tests for Edit and Rerun click events in `frontend/src/entrypoints/task-detail.test.tsx`
- [X] T014 [P] [US1] Add failing draft reconstruction telemetry tests for success and failure in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T015 [P] [US1] Add failing submit telemetry tests for `UpdateInputs` and `RequestRerun` attempts/results in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T016 [P] [US1] Add failing backend telemetry tests for `UpdateInputs` accepted, rejected, and validation-failure outcomes in `tests/unit/api/routers/test_executions.py`
- [X] T017 [P] [US1] Add failing backend telemetry tests for `RequestRerun` accepted, rejected, and validation-failure outcomes in `tests/unit/api/routers/test_executions.py`

### Implementation for User Story 1

- [X] T018 [US1] Wire non-blocking client telemetry emission and bounded dimension sanitization in `frontend/src/lib/temporalTaskEditing.ts`
- [X] T019 [US1] Record detail-page Edit and Rerun click telemetry only for allowed navigation in `frontend/src/entrypoints/task-detail.tsx`
- [X] T020 [US1] Record draft reconstruction success/failure telemetry with mode and bounded reason in `frontend/src/entrypoints/task-create.tsx`
- [X] T021 [US1] Record edit/rerun submit attempt and result telemetry with update name, outcome, applied state, and reason in `frontend/src/entrypoints/task-create.tsx`
- [X] T022 [US1] Emit bounded server metrics/logs for `UpdateInputs` and `RequestRerun` attempts/results in `api_service/api/routers/executions.py`
- [X] T023 [US1] Ensure telemetry helpers never include task instructions, artifact contents, credentials, or full update payloads in `api_service/api/routers/executions.py` and `frontend/src/lib/temporalTaskEditing.ts`

### Validation for User Story 1

- [X] T024 [US1] Verify User Story 1 with `pytest tests/unit/api/routers/test_executions.py -q`
- [X] T025 [US1] Verify User Story 1 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: User Story 1 is independently observable and ready for MVP validation.

---

## Phase 4: User Story 2 - Prove Regression Safety for Key Flows (Priority: P2)

**Goal**: Automated regression coverage proves supported active edit, terminal rerun, and explicit failure scenarios keep working.
**Independent Test**: Run the task editing regression tests and confirm all Phase 5 scenarios are covered with correct mode, payload, failure, telemetry, and redirect behavior.

### Tests for User Story 2

> Write these tests first and confirm they fail for the expected reason before implementation.

- [X] T026 [P] [US2] Add failing route parsing, mode resolution, and rerun-over-edit precedence tests in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T027 [P] [US2] Add failing artifact-safe payload building tests for edit and rerun submissions in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T028 [P] [US2] Add failing active `MoonMind.Run` edit happy-path test covering prefill, `UpdateInputs`, and Temporal detail return in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T029 [P] [US2] Add failing terminal `MoonMind.Run` rerun happy-path test covering prefill, `RequestRerun`, and Temporal detail return in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T030 [P] [US2] Add failing unsupported workflow type, missing capability, missing artifact, and malformed artifact tests in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T031 [P] [US2] Add failing stale-state, validation-error, and artifact externalization failure tests in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T032 [P] [US2] Add failing backend stale-state and validation rejection tests for execution update handling in `tests/unit/api/routers/test_executions.py`

### Implementation for User Story 2

- [X] T033 [US2] Harden canonical route parsing and mode resolution so rerun wins over edit in `frontend/src/lib/temporalTaskEditing.ts`
- [X] T034 [US2] Harden artifact-safe edit/rerun payload construction without mutating historical artifacts in `frontend/src/lib/temporalTaskEditing.ts`
- [X] T035 [US2] Harden draft reconstruction error handling for unsupported workflow type, missing capability, missing artifact, and malformed artifact in `frontend/src/entrypoints/task-create.tsx`
- [X] T036 [US2] Harden edit submit handling for stale state, backend validation failure, artifact preparation failure, `UpdateInputs`, and Temporal detail return in `frontend/src/entrypoints/task-create.tsx`
- [X] T037 [US2] Harden rerun submit handling for stale state, backend validation failure, artifact preparation failure, `RequestRerun`, and Temporal detail return in `frontend/src/entrypoints/task-create.tsx`
- [X] T038 [US2] Preserve server-side rejection semantics and bounded failure responses for changed workflow state in `api_service/api/routers/executions.py`
- [X] T039 [US2] Update shared Temporal update response typing for applied/deferred/continued outcomes in `moonmind/schemas/temporal_models.py`

### Validation for User Story 2

- [X] T040 [US2] Verify User Story 2 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`
- [X] T041 [US2] Verify User Story 2 with `pytest tests/unit/api/routers/test_executions.py -q`

**Checkpoint**: User Story 2 proves all required edit/rerun happy paths and failure modes with automated coverage.

---

## Phase 5: User Story 3 - Remove Queue-Era Primary Flow Leakage (Priority: P3)

**Goal**: Primary Temporal task editing routes, redirects, and operator copy no longer use queue-era edit or resubmit semantics.
**Independent Test**: Primary runtime UI tests and source scans prove edit/rerun flows use Temporal-native routes and copy only.

### Tests for User Story 3

> Write these tests first and confirm they fail for the expected reason before implementation.

- [X] T042 [P] [US3] Add failing tests that detail-page Edit and Rerun navigation never targets `/tasks/queue/new` or `editJobId` in `frontend/src/entrypoints/task-detail.test.tsx`
- [X] T043 [P] [US3] Add failing tests that edit/rerun success redirects return to Temporal detail context and not queue/list pages in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T044 [P] [US3] Add failing tests that operator-facing Temporal edit/rerun copy avoids queue resubmit terminology in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [X] T045 [US3] Remove queue-era route, `editJobId`, and resubmit copy from primary detail-page task editing navigation in `frontend/src/entrypoints/task-detail.tsx`
- [X] T046 [US3] Remove queue-era submit, redirect, and resubmit copy from shared edit/rerun form behavior in `frontend/src/entrypoints/task-create.tsx`
- [X] T047 [US3] Remove or replace queue-era helper exports from primary Temporal task editing helpers in `frontend/src/lib/temporalTaskEditing.ts`
- [X] T048 [US3] Inspect and update current Temporal-native route/copy guidance where needed in `specs/170-temporal-editing-hardening/quickstart.md`, `docs/Tasks/TaskEditingSystem.md`, `docs/UI/CreatePage.md`, and `docs/Tasks/TaskEditingSystem.md`

### Validation for User Story 3

- [X] T049 [US3] Verify User Story 3 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- [X] T050 [US3] Search primary runtime surfaces and current docs/internal references for queue-era references with `rg -n "(/tasks/queue/new|editJobId|queue resubmit|resubmit)" frontend/src api_service moonmind docs/Tasks/TaskEditingSystem.md docs/UI/CreatePage.md local-only handoffs specs/170-temporal-editing-hardening/quickstart.md`

**Checkpoint**: User Story 3 has removed queue-era primary-flow leakage from current runtime surfaces.

---

## Phase 6: User Story 4 - Support Controlled Rollout (Priority: P4)

**Goal**: Maintainers can enable, dogfood, limit, expand, and disable Temporal task editing through runtime-visible rollout controls and health gates.
**Independent Test**: Runtime config tests and quickstart checks prove the flag path and rollout gates exist without queue fallback behavior.

### Tests for User Story 4

> Write these tests first and confirm they fail for the expected reason before implementation.

- [X] T051 [P] [US4] Add failing dashboard view-model tests for local/staging `temporalTaskEditing` exposure in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T052 [P] [US4] Add failing settings tests for runtime flag parsing and disabled-state behavior in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T053 [P] [US4] Add failing frontend tests that disabled rollout hides edit/rerun entry points without queue fallback in `frontend/src/entrypoints/task-detail.test.tsx`

### Implementation for User Story 4

- [X] T054 [US4] Ensure `TEMPORAL_TASK_EDITING_ENABLED` or equivalent runtime setting controls `temporalTaskEditing` in `moonmind/config/settings.py`
- [X] T055 [US4] Ensure dashboard boot/config payload exposes `temporalTaskEditing` consistently for local and staging validation in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T056 [US4] Ensure disabled rollout state hides Temporal edit/rerun entry points without queue fallback in `frontend/src/entrypoints/task-detail.tsx`
- [X] T057 [US4] Document rollout gates for dogfood, limited production, and all-operator expansion in `specs/170-temporal-editing-hardening/quickstart.md`

### Validation for User Story 4

- [X] T058 [US4] Verify User Story 4 with `pytest tests/unit/api/routers/test_task_dashboard_view_model.py -q`
- [X] T059 [US4] Verify User Story 4 with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx`

**Checkpoint**: User Story 4 supports controlled rollout with runtime flags and explicit rollout health gates.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final runtime validation, type/lint checks, and cross-artifact consistency.

- [X] T060 [P] Run backend targeted validation from quickstart with `pytest tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_dashboard_view_model.py -q`
- [X] T061 [P] Run frontend targeted validation from quickstart with `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- [X] T062 [P] Run frontend typecheck with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- [X] T063 [P] Run frontend lint for edited files with `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src/lib/temporalTaskEditing.ts frontend/src/entrypoints/task-detail.tsx frontend/src/entrypoints/task-create.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- [X] T064 Run full required unit validation with `./tools/test_unit.sh`
- [X] T065 Run Spec Kit runtime scope validation with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T066 Confirm no unresolved placeholders or traceability gaps remain in `specs/170-temporal-editing-hardening/tasks.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies. Can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Stories (Phases 3-6)**: Depend on Foundational completion.
- **Polish (Phase 7)**: Depends on the desired user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational. Delivers the MVP observability slice.
- **User Story 2 (P2)**: Can start after Foundational, but should reuse telemetry helpers from User Story 1 if US1 has already landed.
- **User Story 3 (P3)**: Can start after Foundational, but final cleanup should be rechecked after User Story 2 redirects and copy are implemented.
- **User Story 4 (P4)**: Can start after Foundational and can run in parallel with User Stories 1-3 if ownership is separate.

### Within Each User Story

- Automated tests must be written first and fail for the expected reason before implementation.
- Helper/type changes should land before entrypoint wiring when both are required.
- Story validation tasks must pass before treating that story as independently complete.
- Quickstart validation supplements automated tests and does not replace them.

## Parallel Opportunities

- Setup review tasks T002-T005 can run in parallel.
- Foundational test tasks T006-T008 can run in parallel.
- User Story 1 test tasks T013-T017 can run in parallel before implementation.
- User Story 2 test tasks T026-T032 can run in parallel because they target independent scenarios.
- User Story 3 test tasks T042-T044 can run in parallel.
- User Story 4 test tasks T051-T053 can run in parallel.
- Final validation tasks T060-T063 can run in parallel after implementation is complete.

## Parallel Example: User Story 1

```bash
# Write failing telemetry tests in parallel:
Task: "T013 [P] [US1] Add failing detail-page telemetry tests for Edit and Rerun click events in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T014 [P] [US1] Add failing draft reconstruction telemetry tests for success and failure in frontend/src/entrypoints/task-create.test.tsx"
Task: "T016 [P] [US1] Add failing backend telemetry tests for UpdateInputs accepted, rejected, and validation-failure outcomes in tests/unit/api/routers/test_executions.py"
```

## Parallel Example: User Story 2

```bash
# Write failing regression tests for independent scenarios in parallel:
Task: "T026 [P] [US2] Add failing route parsing, mode resolution, and rerun-over-edit precedence tests in frontend/src/entrypoints/task-create.test.tsx"
Task: "T028 [P] [US2] Add failing active MoonMind.Run edit happy-path test in frontend/src/entrypoints/task-create.test.tsx"
Task: "T032 [P] [US2] Add failing backend stale-state and validation rejection tests in tests/unit/api/routers/test_executions.py"
```

## Parallel Example: User Story 4

```bash
# Validate rollout controls on backend and frontend separately:
Task: "T051 [P] [US4] Add failing dashboard view-model tests for local/staging temporalTaskEditing exposure in tests/unit/api/routers/test_task_dashboard_view_model.py"
Task: "T053 [P] [US4] Add failing frontend tests that disabled rollout hides edit/rerun entry points without queue fallback in frontend/src/entrypoints/task-detail.test.tsx"
```

## Implementation Strategy

### MVP First

Complete Phase 1, Phase 2, and User Story 1. This creates the minimum production-ready observability slice: detail clicks, draft reconstruction outcomes, submit attempts/results, bounded failure reasons, and non-blocking telemetry behavior.

### Incremental Delivery

1. Deliver User Story 1 and validate telemetry independently.
2. Deliver User Story 2 to lock down edit/rerun happy paths and failure regressions.
3. Deliver User Story 3 to remove queue-era leakage from primary runtime flows.
4. Deliver User Story 4 to finalize runtime rollout controls and health gates.
5. Run Phase 7 validation before implementation completion.

### Task Counts

- Setup: 5 tasks
- Foundational: 7 tasks
- User Story 1: 13 tasks
- User Story 2: 16 tasks
- User Story 3: 9 tasks
- User Story 4: 9 tasks
- Polish: 7 tasks
- Total: 66 tasks
