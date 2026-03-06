# Tasks: Run History and Rerun Semantics

**Input**: Design documents from `/specs/048-run-history-rerun/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Validation tests are required by the feature spec (`FR-001`, `FR-018`, `DOC-REQ-016`), so each user story includes explicit automated test tasks.

**Organization**: Tasks are grouped by user story so each story remains independently implementable and testable while preserving runtime-mode scope.

## Prompt B Scope Controls (Step 7/16)

- Runtime implementation tasks are explicitly present: `T001-T006`, `T011-T013`, `T017-T020`, `T024-T026`.
- Runtime validation tasks are explicitly present: `T007-T010`, `T014-T016`, `T021-T023`, `T027-T030`.
- `DOC-REQ-001` through `DOC-REQ-016` each appear in at least one implementation task and one validation task, with persistent source mapping in `specs/048-run-history-rerun/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared latest-run schema, projection, and dashboard identity primitives before story work starts.

- [X] T001 Extend latest-view execution schema fields for `workflowId`, `taskId`, `temporalRunId`, `latestRunView`, and `continueAsNewCause` in `moonmind/schemas/temporal_models.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-013, DOC-REQ-016)
- [X] T002 [P] Align latest-run projection invariants and serialization helpers in `api_service/db/models.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-001, DOC-REQ-014)
- [X] T003 [P] Add Temporal dashboard identity plumbing for logical execution routes in `api_service/api/routers/task_dashboard.py` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared runtime behaviors that every user story depends on.

**CRITICAL**: Complete this phase before starting user story implementation.

- [X] T004 Implement canonical `workflowId` and `taskId` latest-view normalization in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004, DOC-REQ-013, DOC-REQ-016)
- [X] T005 [P] Implement latest-run artifact resolution helpers keyed by `workflowId` plus current `temporalRunId` in `moonmind/workflows/temporal/artifacts.py` and `api_service/api/routers/temporal_artifacts.py` (DOC-REQ-006, DOC-REQ-014)
- [X] T006 [P] Normalize Temporal list-row identity and latest-run detail handoff in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-019)
- [X] T007 Add foundational API coverage for latest-run execution and artifact identity rules in `tests/unit/api/routers/test_executions.py` and `tests/unit/api/routers/test_temporal_artifacts.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-006, DOC-REQ-013, DOC-REQ-016)

**Checkpoint**: Shared latest-run identity, projection, and artifact rules are in place; user story work can begin.

---

## Phase 3: User Story 1 - Keep One Stable Detail Route Across Reruns (Priority: P1) 🎯 MVP

**Goal**: Keep task and execution detail anchored on one durable logical execution identity while showing the latest run metadata after rerun or rollover.

**Independent Test**: Start a Temporal-backed execution, capture its `workflowId` and `taskId`, trigger rerun or another Continue-As-New transition, and verify the same detail route still resolves and uses the latest run metadata and artifacts.

### Tests for User Story 1

- [X] T008 [P] [US1] Add execution API tests for `taskId == workflowId`, latest-run detail payloads, and stable detail routing in `tests/unit/api/routers/test_executions.py` (DOC-REQ-002, DOC-REQ-004, DOC-REQ-005, DOC-REQ-013)
- [X] T009 [US1] Add task dashboard router and view-model tests for stable Temporal row identity and latest-run detail handoff in `tests/unit/api/routers/test_task_dashboard.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-019)
- [X] T010 [P] [US1] Add dashboard regression coverage for rerun and rollover row stability in `tests/task_dashboard/test_temporal_run_history.js` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-015)

### Implementation for User Story 1

- [X] T011 [US1] Return canonical logical execution detail and list fields from `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-005, DOC-REQ-013)
- [X] T012 [US1] Keep Temporal-backed `/tasks/{taskId}` routes anchored on `workflowId` in `api_service/api/routers/task_dashboard.py` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-002, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-019)
- [X] T013 [US1] Resolve detail-page artifacts from current execution detail metadata in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/temporal_artifacts.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-013, DOC-REQ-015, DOC-REQ-019)

**Checkpoint**: The dashboard and execution APIs now expose one stable logical route and latest-run detail behavior for Temporal-backed work.

---

## Phase 4: User Story 2 - Rerun the Same Logical Execution Predictably (Priority: P1)

**Goal**: Make `RequestRerun` a deterministic same-execution Continue-As-New flow with stable logical identity, explicit response semantics, and terminal-state safeguards.

**Independent Test**: Submit `RequestRerun` updates against active and terminal executions, including input or plan replacements and idempotency keys, and verify the resulting execution state, response contract, and latest-run artifact behavior.

### Tests for User Story 2

- [X] T014 [P] [US2] Add Temporal service tests for `RequestRerun` state reset, workflow-type restart targets, and terminal rejection in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-007, DOC-REQ-009, DOC-REQ-010, DOC-REQ-015)
- [X] T015 [P] [US2] Add execution contract tests for accepted rerun response shape, `continue_as_new`, and fresh-workflow distinction in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-007, DOC-REQ-010, DOC-REQ-012, DOC-REQ-013, DOC-REQ-015)
- [X] T016 [US2] Add API and artifact tests for rerun-time input, plan, and parameter updates plus latest-run artifact fetches in `tests/unit/api/routers/test_executions.py` and `tests/unit/api/routers/test_temporal_artifacts.py` (DOC-REQ-006, DOC-REQ-008, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015)

### Implementation for User Story 2

- [X] T017 [US2] Implement `RequestRerun` Continue-As-New reset semantics and rerun memo or summary updates in `moonmind/workflows/temporal/service.py` (DOC-REQ-007, DOC-REQ-009, DOC-REQ-010, DOC-REQ-013, DOC-REQ-015)
- [X] T018 [US2] Preserve rerun-time `input_ref`, `plan_ref`, `parameters_patch`, and idempotency handling in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-008, DOC-REQ-014)
- [X] T019 [US2] Surface accepted reruns as latest-view execution updates in `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-007, DOC-REQ-009, DOC-REQ-013)
- [X] T020 [US2] Keep the latest-run projection row mutable per `workflowId` during rerun in `api_service/db/models.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-001, DOC-REQ-012, DOC-REQ-014)

**Checkpoint**: Active executions rerun predictably on the same logical execution, while terminal executions remain explicitly non-rerunnable in v1.

---

## Phase 5: User Story 3 - Distinguish Manual Rerun from Other Lifecycle Rollover (Priority: P2)

**Goal**: Preserve same-execution Continue-As-New behavior while clearly separating manual rerun from automatic lifecycle rollover and fresh logical execution creation.

**Independent Test**: Exercise threshold-driven Continue-As-New, explicit rerun, and a brand-new execution start, then verify identifiers, labels, and route semantics remain correct for each case.

### Tests for User Story 3

- [X] T021 [P] [US3] Add service tests for lifecycle-threshold and major-reconfiguration Continue-As-New causes in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-011, DOC-REQ-015)
- [X] T022 [US3] Add API tests for manual rerun versus automatic rollover labeling and new logical execution creation in `tests/unit/api/routers/test_executions.py` and `tests/contract/test_temporal_execution_api.py` (DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-015)
- [X] T023 [P] [US3] Add dashboard JS coverage for logical-versus-run identity labeling in `tests/task_dashboard/test_temporal_run_history.js` (DOC-REQ-003, DOC-REQ-011, DOC-REQ-015, DOC-REQ-019)

### Implementation for User Story 3

- [X] T024 [US3] Record and expose `continue_as_new_cause` without inferring user intent from `rerun_count` in `moonmind/workflows/temporal/service.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-011, DOC-REQ-013, DOC-REQ-015)
- [X] T025 [US3] Keep new logical execution creation on fresh `workflowId` paths distinct from `RequestRerun` in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-012, DOC-REQ-013)
- [X] T026 [US3] Align Temporal dashboard metadata labels and run-instance naming in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-003, DOC-REQ-011, DOC-REQ-019)

**Checkpoint**: Manual rerun, automatic rollover, and fresh logical execution creation remain distinguishable without breaking stable logical identity.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finish validation, traceability, and runtime-mode acceptance across all stories.

- [X] T027 Add cross-story regression coverage for latest-run projection boundaries in `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/api/routers/test_temporal_artifacts.py` (DOC-REQ-006, DOC-REQ-014, DOC-REQ-015)
- [X] T028 Verify `DOC-REQ-001` through `DOC-REQ-016` implementation and validation mapping in `specs/048-run-history-rerun/contracts/requirements-traceability.md` and `specs/048-run-history-rerun/quickstart.md` (DOC-REQ-015, DOC-REQ-016)
- [X] T029 Run `./tools/test_unit.sh` and confirm runtime validation steps in `specs/048-run-history-rerun/quickstart.md` (DOC-REQ-015, DOC-REQ-016)
- [X] T030 Run `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` and record runtime-scope completion notes in `specs/048-run-history-rerun/quickstart.md` (DOC-REQ-016)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; start immediately.
- **Foundational (Phase 2)**: Depends on Setup; blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational.
- **User Story 2 (Phase 4)**: Depends on Foundational and can proceed in parallel with US1 after shared latest-run primitives exist.
- **User Story 3 (Phase 5)**: Depends on Foundational and benefits from US1 and US2 runtime semantics being in place.
- **Polish (Phase 6)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: First deliverable for stable logical routing and latest-run detail semantics.
- **US2 (P1)**: Parallel P1 story for explicit rerun behavior; required to harden the rerun path exercised by US1.
- **US3 (P2)**: Builds on the identity and rerun semantics from US1 and US2 to distinguish operator-visible causes and fresh execution creation.

### Within Each User Story

- Write the listed automated tests before finalizing implementation.
- Update schema and service logic before wiring router or dashboard consumers.
- Keep artifact resolution changes aligned with latest-run detail payload semantics.

### Parallel Opportunities

- `T002` and `T003` can run in parallel after `T001`.
- `T005` and `T006` can run in parallel after `T004`.
- `T008` and `T010` can run in parallel for US1 because they touch different test files.
- `T014` and `T015` can run in parallel for US2 because they touch different test files.
- `T021` and `T023` can run in parallel for US3 because they touch different test files.

---

## Parallel Example: User Story 1

```bash
# Parallel test work for US1
Task: T008 tests/unit/api/routers/test_executions.py
Task: T010 tests/task_dashboard/test_temporal_run_history.js

# Parallel implementation work for US1 after foundational tasks land
Task: T012 api_service/api/routers/task_dashboard.py + api_service/api/routers/task_dashboard_view_model.py
Task: T013 api_service/static/task_dashboard/dashboard.js + api_service/api/routers/temporal_artifacts.py
```

## Parallel Example: User Story 2

```bash
# Parallel test work for US2
Task: T014 tests/unit/workflows/temporal/test_temporal_service.py
Task: T015 tests/contract/test_temporal_execution_api.py

# Parallel implementation slices for US2 once service invariants are understood
Task: T018 moonmind/workflows/temporal/service.py + moonmind/workflows/temporal/artifacts.py
Task: T019 api_service/api/routers/executions.py + moonmind/schemas/temporal_models.py
```

## Parallel Example: User Story 3

```bash
# Parallel test work for US3
Task: T021 tests/unit/workflows/temporal/test_temporal_service.py
Task: T023 tests/task_dashboard/test_temporal_run_history.js

# Parallel implementation work for US3
Task: T024 moonmind/workflows/temporal/service.py + moonmind/schemas/temporal_models.py
Task: T026 api_service/static/task_dashboard/dashboard.js + api_service/api/routers/task_dashboard_view_model.py
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2.
2. Deliver Phase 3 to lock stable logical routing and latest-run detail semantics.
3. Validate the US1 independent test using rerun or rollover against the same route.
4. Follow immediately with Phase 4 to harden the explicit rerun contract that powers the MVP path.

### Incremental Delivery

1. Setup plus Foundational establishes shared identity and artifact rules.
2. Add US1 for stable task or execution detail routing.
3. Add US2 for deterministic rerun behavior and terminal-state safeguards.
4. Add US3 for operator-facing lifecycle distinction and fresh-execution boundaries.
5. Finish with cross-story regression and runtime-mode acceptance checks.

### Parallel Team Strategy

1. Pair on Phase 1 and Phase 2 to lock shared latest-run invariants.
2. After foundation is complete:
   - Engineer A: US1 API and dashboard routing
   - Engineer B: US2 rerun lifecycle service and contract work
   - Engineer C: US3 cause-labeling and dashboard metadata
3. Rejoin for Phase 6 validation and scope-gate execution.

---

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T002, T004, T020 | T007, T027 |
| DOC-REQ-002 | T001, T004, T011, T012 | T007, T008 |
| DOC-REQ-003 | T001, T011, T026 | T010, T023 |
| DOC-REQ-004 | T003, T004, T012 | T008, T009, T010 |
| DOC-REQ-005 | T003, T006, T011, T012, T013 | T008, T009, T010 |
| DOC-REQ-006 | T003, T005, T006, T012, T013 | T007, T009, T010, T016, T027 |
| DOC-REQ-007 | T017, T019 | T014, T015 |
| DOC-REQ-008 | T018 | T016 |
| DOC-REQ-009 | T017, T019 | T014 |
| DOC-REQ-010 | T017 | T014, T015 |
| DOC-REQ-011 | T024, T026 | T021, T022, T023 |
| DOC-REQ-012 | T020, T025 | T015, T022 |
| DOC-REQ-013 | T001, T004, T011, T017, T019, T024, T025 | T007, T008, T015, T016, T022 |
| DOC-REQ-014 | T002, T005, T018, T020 | T016, T027 |
| DOC-REQ-015 | T013, T017, T024 | T010, T014, T015, T016, T021, T022, T023, T027, T029 |
| DOC-REQ-016 | T001, T004 | T007, T028, T029, T030 |

Coverage gate rule: each `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.

---

## Notes

- All tasks use the required checklist format: `- [ ] T### [P?] [US?] Description with file path`.
- `[US#]` labels appear only in user story phases.
- Runtime-mode scope is explicit in the task ranges, validation tasks, and `DOC-REQ-016` coverage.
- The plan intentionally excludes a v1 per-run history browser, arbitrary historical-run routes, or an immutable per-run projection model.
