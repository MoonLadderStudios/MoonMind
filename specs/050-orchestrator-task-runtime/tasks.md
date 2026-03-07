# Tasks: Orchestrator Task Runtime Upgrade

**Input**: Design documents from `/specs/042-orchestrator-task-runtime/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Validation coverage is required by the specification (`DOC-REQ-018`, `DOC-REQ-019`), so each user story includes explicit test tasks.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- [ ] format is required for every task
- `T###` is the ordered task id
- `[P]` marks tasks safe to parallelize
- `[US#]` is required for user-story tasks only
- Every task description includes concrete file paths

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared contracts, route hooks, and fixtures before feature implementation.

- [X] T001 [P] Add canonical task-first alias schema fields (`taskId` + transitional `runId`) in `moonmind/schemas/workflow_models.py` and `moonmind/workflows/orchestrator/serializers.py` (DOC-REQ-001, DOC-REQ-005).
- [X] T002 [P] Add unified task route constants and legacy redirect helpers in `api_service/api/routers/task_dashboard.py` and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-003, DOC-REQ-006).
- [X] T003 [P] Add reusable mixed-source fixtures for queue + orchestrator task views in `tests/task_dashboard/__fixtures__/queue_rows.js` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-018).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build runtime foundations that block all user stories.

**⚠️ CRITICAL**: Complete this phase before starting user-story implementation.

- [X] T004 Implement canonical orchestrator task/step migration and model updates in `api_service/migrations/versions/202602260001_orchestrator_task_runtime_steps.py` and `api_service/db/models.py` (DOC-REQ-012, DOC-REQ-013, DOC-REQ-019).
- [X] T005 Implement task/step repository and service adapters for task-first reads/writes with run compatibility in `moonmind/workflows/orchestrator/repositories.py` and `moonmind/workflows/orchestrator/services.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-012, DOC-REQ-013).
- [X] T006 [P] Implement dual job-type normalization for `orchestrator_task` and `orchestrator_run` in `moonmind/workflows/agent_queue/job_types.py` and `moonmind/workflows/orchestrator/queue_dispatch.py` (DOC-REQ-014).
- [X] T007 Implement state-sink interface scaffolding (DB primary + degraded-mode hooks) in `moonmind/workflows/orchestrator/state_sink.py` and `moonmind/workflows/orchestrator/queue_worker.py` (DOC-REQ-004, DOC-REQ-015, DOC-REQ-016).
- [X] T008 [P] Implement shared approval/skill-argument security validators in `moonmind/workflows/orchestrator/policies.py` and `moonmind/workflows/orchestrator/skill_executor.py` (DOC-REQ-017).
- [X] T009 [P] Implement deterministic unified row/status normalization helpers used by list/detail rendering in `api_service/api/routers/task_dashboard_view_model.py` and `moonmind/workflows/orchestrator/serializers.py` (DOC-REQ-007, DOC-REQ-008, DOC-REQ-018).

**Checkpoint**: Core persistence, dispatch, state-sink, security, and normalization infrastructure is ready.

---

## Phase 3: User Story 1 - Operate from One Task Surface (Priority: P1) 🎯 MVP

**Goal**: Queue and orchestrator work appear in one task list/detail experience with task-first naming.

**Independent Test**: `/tasks/list` shows mixed queue/orchestrator rows and `/tasks/:taskId` resolves correct source without using dedicated orchestrator pages.

### Tests for User Story 1

- [X] T010 [P] [US1] Add alias parity contract tests for `/orchestrator/tasks*` and `/orchestrator/runs*` create/list/detail/approval/retry/artifacts flows in `tests/contract/test_orchestrator_api.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-018).
- [X] T011 [P] [US1] Add route redirect and unified list behavior tests for `/tasks/list` in `tests/task_dashboard/test_queue_layouts.js` and `tests/unit/api/routers/test_task_dashboard.py` (DOC-REQ-003, DOC-REQ-006, DOC-REQ-007, DOC-REQ-018).
- [X] T012 [P] [US1] Add source-resolution detail tests (`source` hint + fallback probing) in `tests/unit/api/routers/test_task_dashboard_view_model.py` and `tests/unit/api/routers/test_task_dashboard.py` (DOC-REQ-007, DOC-REQ-008, DOC-REQ-018).

### Implementation for User Story 1

- [X] T013 [US1] Implement `/orchestrator/tasks` create/list/detail/approval/retry/artifacts aliases with transitional IDs in `api_service/api/routers/orchestrator.py` and `moonmind/workflows/orchestrator/serializers.py` (DOC-REQ-001, DOC-REQ-005).
- [X] T014 [US1] Consolidate dashboard navigation to `/tasks/list` and `/tasks/:taskId` with legacy redirects in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard.py` (DOC-REQ-003, DOC-REQ-006).
- [X] T015 [P] [US1] Implement unified queue/orchestrator row mapping for list responses in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-007).
- [X] T016 [US1] Implement deterministic shared detail loading for queue/orchestrator sources in `api_service/api/routers/task_dashboard.py` and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-008).
- [X] T017 [US1] Replace run-first UI labels and detail metadata copy with task-first text in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-001, DOC-REQ-003).

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Author Orchestrator Tasks with Steps and Skills (Priority: P2)

**Goal**: Runtime=orchestrator authoring supports explicit ordered steps + skills while preserving orchestrator controls and compatibility behavior.

**Independent Test**: Orchestrator task creation works with and without `steps[]`, grouped skills render by runtime domain, and explicit steps execute in declared order.

### Tests for User Story 2

- [X] T018 [P] [US2] Add submit-form runtime tests for orchestrator step editing and queue capability field preservation in `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-002, DOC-REQ-009, DOC-REQ-018).
- [X] T019 [P] [US2] Add API tests for grouped skills response and create-with/without-steps behavior in `tests/contract/test_orchestrator_api.py` and `tests/unit/api/routers/test_task_dashboard.py` (DOC-REQ-010, DOC-REQ-011, DOC-REQ-018).
- [X] T020 [P] [US2] Add persistence tests for arbitrary step counts/order/status/attempts and run-to-task migration compatibility in `tests/unit/workflows/test_orchestrator_repository.py` and `tests/unit/workflows/orchestrator/test_tasks.py` (DOC-REQ-012, DOC-REQ-013, DOC-REQ-018).
- [X] T021 [P] [US2] Add worker compatibility tests for `orchestrator_task` and `orchestrator_run` payloads in `tests/unit/workflows/orchestrator/test_queue_worker.py` and `tests/unit/workflows/orchestrator/test_tasks.py` (DOC-REQ-014, DOC-REQ-018).

### Implementation for User Story 2

- [X] T022 [US2] Implement orchestrator create schema support for optional ordered `steps[]` with stable IDs and explicit skill IDs in `moonmind/schemas/workflow_models.py` and `api_service/api/routers/orchestrator.py` (DOC-REQ-002, DOC-REQ-011).
- [X] T023 [US2] Implement runtime=orchestrator submit UX with step editor, per-step skill args, and orchestrator controls while keeping queue capability fields available in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-002, DOC-REQ-009).
- [X] T024 [P] [US2] Implement grouped skill discovery payloads (`worker`, `orchestrator`) with compatibility list output in `api_service/api/routers/task_dashboard.py` and `moonmind/workflows/orchestrator/skill_executor.py` (DOC-REQ-010).
- [X] T025 [US2] Implement canonical `OrchestratorTaskStep` persistence for arbitrary N-step executions in `moonmind/workflows/orchestrator/repositories.py` and `api_service/db/models.py` (DOC-REQ-012).
- [X] T026 [US2] Complete run-to-task migration/read compatibility flow in `api_service/migrations/versions/202602260001_orchestrator_task_runtime_steps.py` and `moonmind/workflows/orchestrator/repositories.py` (DOC-REQ-013).
- [X] T027 [US2] Implement runtime dispatch/service handling for both `orchestrator_task` and legacy `orchestrator_run` envelopes in `moonmind/workflows/orchestrator/queue_dispatch.py` and `moonmind/workflows/orchestrator/services.py` (DOC-REQ-014).
- [X] T028 [US2] Enforce approval-token and skill-argument command-injection guardrails for explicit steps in `moonmind/workflows/orchestrator/policies.py` and `moonmind/workflows/orchestrator/skill_executor.py` (DOC-REQ-017).

**Checkpoint**: User Story 2 is independently functional and testable.

---

## Phase 5: User Story 3 - Finish Tasks During Mid-Run DB Outages (Priority: P3)

**Goal**: In-flight orchestrator tasks continue through DB outages with artifact-backed state and reconcile once connectivity returns.

**Independent Test**: With DB interruption mid-run, execution continues, snapshots are written, and restored DB state/terminal queue state reconcile correctly.

### Tests for User Story 3

- [X] T029 [P] [US3] Add DB-sink failure fallback tests for artifact snapshot writes in `tests/unit/workflows/orchestrator/test_queue_worker.py` and `tests/unit/workflows/orchestrator/test_storage.py` (DOC-REQ-004, DOC-REQ-015, DOC-REQ-018).
- [X] T030 [P] [US3] Add reconciliation/idempotency tests for snapshot replay after DB recovery in `tests/unit/workflows/orchestrator/test_queue_worker.py` and `tests/unit/workflows/test_orchestrator_repository.py` (DOC-REQ-015, DOC-REQ-018).
- [X] T031 [P] [US3] Add queue heartbeat/lease failure continuation + terminal status retry tests in `tests/unit/workflows/orchestrator/test_queue_worker.py` and `tests/unit/workflows/agent_queue/test_repositories.py` (DOC-REQ-016, DOC-REQ-018).
- [X] T032 [US3] Add auth + approval regression tests for orchestrator task aliases in `tests/contract/test_orchestrator_api.py` and `tests/unit/api/routers/test_task_dashboard.py` (DOC-REQ-017, DOC-REQ-018).

### Implementation for User Story 3

- [X] T033 [US3] Implement DB-first plus artifact-fallback state recording in `moonmind/workflows/orchestrator/state_sink.py` and `moonmind/workflows/orchestrator/queue_worker.py` (DOC-REQ-004, DOC-REQ-015).
- [X] T034 [US3] Implement snapshot reconciliation replay into canonical task/step persistence in `moonmind/workflows/orchestrator/state_sink.py` and `moonmind/workflows/orchestrator/repositories.py` (DOC-REQ-015).
- [X] T035 [US3] Implement best-effort execution continuation through queue heartbeat/lease failures with delayed terminal sync in `moonmind/workflows/orchestrator/queue_worker.py` and `moonmind/workflows/orchestrator/services.py` (DOC-REQ-004, DOC-REQ-016).
- [X] T036 [US3] Implement source-aware degraded-mode detail serialization for unified task detail timelines in `moonmind/workflows/orchestrator/serializers.py` and `api_service/api/routers/task_dashboard.py` (DOC-REQ-008, DOC-REQ-016).

**Checkpoint**: User Story 3 is independently functional and testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final runtime validation and scope-gate evidence.

- [X] T037 Run the required regression suite with `./tools/test_unit.sh` and fix any failures in `tests/contract/test_orchestrator_api.py`, `tests/task_dashboard/test_submit_runtime.js`, and `tests/unit/workflows/orchestrator/test_queue_worker.py` (DOC-REQ-018, DOC-REQ-019).
- [X] T038 [P] Run runtime task-scope validation via `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and capture the result in `specs/042-orchestrator-task-runtime/quickstart.md` (DOC-REQ-019).
- [X] T039 [P] Update execution evidence and requirement links in `specs/042-orchestrator-task-runtime/contracts/requirements-traceability.md` and `specs/042-orchestrator-task-runtime/quickstart.md`, and reconcile against the `DOC-REQ` coverage matrix in this `tasks.md` file (DOC-REQ-018).
- [X] T040 [P] Run orchestrator resilience integration flow with `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` and record outcomes in `specs/042-orchestrator-task-runtime/quickstart.md` (DOC-REQ-004, DOC-REQ-014, DOC-REQ-018, DOC-REQ-019).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies; can start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1; blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2 completion.
- **Phase 4 (US2)**: Depends on Phase 2 completion; can proceed after US1 API/list primitives are stable.
- **Phase 5 (US3)**: Depends on Phase 2 completion; recommended after US1/US2 because it extends shared worker/detail behavior.
- **Phase 6 (Polish)**: Depends on all targeted story phases completing.

### User Story Dependencies

- **US1 (P1)**: No user-story dependency once foundational work is complete.
- **US2 (P2)**: Depends on foundational persistence/dispatch/security tasks (`T004`-`T009`) and can run independently from US3.
- **US3 (P3)**: Depends on foundational state-sink scaffolding (`T007`) and benefits from US1/US2 completion for full end-to-end validation.

### Within Each User Story

- Validation tasks should fail first, then implementation tasks should make them pass.
- Schema/model/repository changes should land before router/UI wiring that depends on them.
- Worker execution changes should land before degraded-mode serialization/polish.

## Parallel Opportunities

- Setup tasks `T001`-`T003` are parallelizable.
- Foundational tasks `T006`, `T008`, and `T009` can run in parallel after `T004` begins.
- In US1, tests `T010`-`T012` and implementation task `T015` are parallelizable.
- In US2, test tasks `T018`-`T021` and implementation tasks `T024` and `T028` are parallelizable once schemas are in place.
- In US3, test tasks `T029`-`T031` can run in parallel with reconciliation implementation `T034` after sink scaffolding stabilizes.

## Parallel Example: User Story 1

```bash
Task: T010 in tests/contract/test_orchestrator_api.py
Task: T011 in tests/task_dashboard/test_queue_layouts.js + tests/unit/api/routers/test_task_dashboard.py
Task: T015 in api_service/api/routers/task_dashboard_view_model.py + api_service/static/task_dashboard/dashboard.js
```

## Parallel Example: User Story 2

```bash
Task: T018 in tests/task_dashboard/test_submit_runtime.js
Task: T019 in tests/contract/test_orchestrator_api.py + tests/unit/api/routers/test_task_dashboard.py
Task: T024 in api_service/api/routers/task_dashboard.py + moonmind/workflows/orchestrator/skill_executor.py
Task: T028 in moonmind/workflows/orchestrator/policies.py + moonmind/workflows/orchestrator/skill_executor.py
```

## Parallel Example: User Story 3

```bash
Task: T029 in tests/unit/workflows/orchestrator/test_queue_worker.py + tests/unit/workflows/orchestrator/test_storage.py
Task: T031 in tests/unit/workflows/orchestrator/test_queue_worker.py + tests/unit/workflows/agent_queue/test_repositories.py
Task: T034 in moonmind/workflows/orchestrator/state_sink.py + moonmind/workflows/orchestrator/repositories.py
```

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2.
2. Complete US1 tasks (`T010`-`T017`).
3. Validate unified list/detail + alias parity before expanding scope.

### Incremental Delivery

1. Deliver US1 for unified operator visibility and task-first terminology.
2. Deliver US2 for orchestrator step/skill authoring parity.
3. Deliver US3 for degraded-mode resilience and reconciliation.
4. Finish with Phase 6 cross-cutting validation and runtime scope gates.

### Task Summary

- Total tasks: **40**
- User story tasks: **US1 = 8**, **US2 = 11**, **US3 = 8**
- Parallelizable tasks (`[P]`): **17**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **All tasks follow `- [ ] T### [P?] [US?] ...` with explicit paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T001, T005, T013, T017 | T010 |
| DOC-REQ-002 | T022, T023 | T018 |
| DOC-REQ-003 | T002, T014, T017 | T011 |
| DOC-REQ-004 | T007, T033, T035, T040 | T029, T040 |
| DOC-REQ-005 | T001, T005, T013 | T010 |
| DOC-REQ-006 | T002, T014 | T011 |
| DOC-REQ-007 | T009, T015 | T011, T012 |
| DOC-REQ-008 | T009, T016, T036 | T012 |
| DOC-REQ-009 | T023 | T018 |
| DOC-REQ-010 | T024 | T019 |
| DOC-REQ-011 | T022 | T019 |
| DOC-REQ-012 | T004, T005, T025 | T020 |
| DOC-REQ-013 | T004, T005, T026 | T020 |
| DOC-REQ-014 | T006, T027, T040 | T021, T040 |
| DOC-REQ-015 | T007, T033, T034 | T029, T030 |
| DOC-REQ-016 | T007, T035, T036 | T031 |
| DOC-REQ-017 | T008, T028 | T032 |
| DOC-REQ-018 | T009, T039, T040 | T003, T010, T011, T012, T018, T019, T020, T021, T029, T030, T031, T032, T037, T040 |
| DOC-REQ-019 | T004, T040 | T037, T038, T040 |

Coverage gate rule: each `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.
