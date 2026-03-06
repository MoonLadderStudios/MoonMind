# Tasks: Task Execution Compatibility

**Input**: Design documents from `/specs/047-task-execution-compatibility/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Validation tests are required because the feature specification mandates runtime implementation plus automated verification (`FR-001`, `FR-002`, `SC-006`), with `./tools/test_unit.sh` covering unit/dashboard suites and targeted pytest covering the required contract suites.  
**Organization**: Tasks are grouped by user story so each story remains independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only
- Every task includes concrete file path(s)

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly represented in `T001-T008`, `T013-T017`, `T021-T026`, and `T030-T034`.
- Runtime validation tasks are explicitly represented in `T009-T012`, `T018-T020`, `T027-T029`, and `T036-T038`.
- `DOC-REQ-001` through `DOC-REQ-011` implementation and validation coverage is enforced by `T035`, `T038`, the `DOC-REQ Coverage Matrix` in this file, and `contracts/requirements-traceability.md`.
- Runtime mode remains mandatory for this feature: docs-only completion is non-compliant.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the compatibility API, persistence, and schema scaffolding shared by all stories.

- [X] T001 Add task compatibility router registration and canonical `/api/tasks/*` entrypoints in `api_service/api/routers/task_compatibility.py`, `api_service/api/routers/__init__.py`, and `api_service/main.py` (DOC-REQ-001, DOC-REQ-011)
- [X] T002 [P] Add persisted `task_source_mappings` model and Alembic migration scaffold in `api_service/db/models.py` and `api_service/migrations/versions/202603060001_task_source_mappings.py` (DOC-REQ-004, DOC-REQ-010)
- [X] T003 [P] Add compatibility list/detail/action/count schemas in `moonmind/schemas/task_compatibility_models.py` and `moonmind/schemas/__init__.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-009)
- [X] T004 [P] Add task compatibility and source-mapping service modules in `moonmind/workflows/tasks/compatibility.py`, `moonmind/workflows/tasks/source_mapping.py`, and `moonmind/workflows/tasks/__init__.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-010)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared runtime invariants required before any user story can ship.

**⚠️ CRITICAL**: Complete this phase before starting user story implementation.

- [X] T005 Implement `taskId`-keyed source mapping upsert/backfill/query helpers in `moonmind/workflows/tasks/source_mapping.py` and `api_service/db/models.py` (DOC-REQ-004, DOC-REQ-010)
- [X] T006 [P] Implement allowlisted Memo/Search Attribute projection and task-safe parameter preview helpers in `moonmind/workflows/tasks/compatibility.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-010)
- [X] T007 Implement normalized Temporal identity and status mapping helpers preserving `rawState`, `temporalStatus`, `closeStatus`, and stable `taskId == workflowId` semantics in `moonmind/workflows/tasks/compatibility.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-003, DOC-REQ-007)
- [X] T008 Implement canonical task detail resolution plumbing that consults source mappings before backend-specific fallbacks in `api_service/api/routers/task_compatibility.py` and `api_service/api/routers/task_dashboard.py` (DOC-REQ-004, DOC-REQ-011)
- [X] T009 Add foundational unit coverage for source mapping, metadata bounding, and status normalization in `tests/unit/workflows/tasks/test_task_compatibility_service.py` and `tests/unit/api/routers/test_task_dashboard.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-010)

**Checkpoint**: Shared compatibility primitives are ready and user story work can begin.

---

## Phase 3: User Story 1 - View Temporal Work as Tasks (Priority: P1) 🎯 MVP

**Goal**: List and inspect Temporal-backed executions through the same task-oriented routes and payloads used today.

**Independent Test**: Run Temporal-backed `MoonMind.Run` and `MoonMind.ManifestIngest` executions, then verify `/tasks/list` and `/tasks/{taskId}` return normalized rows/details with canonical source, entry, identity, and metadata fields.

### Tests for User Story 1

- [X] T010 [P] [US1] Add contract tests for `/api/tasks/list` and `/api/tasks/{taskId}` normalized Temporal list/detail payloads in `tests/contract/test_task_compatibility_api.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-005, DOC-REQ-006, DOC-REQ-011)
- [X] T011 [P] [US1] Add router and view-model tests for temporal source filters, manifest entry handling, and canonical detail routing in `tests/unit/api/routers/test_task_compatibility.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-002, DOC-REQ-004, DOC-REQ-011)
- [ ] T012 [P] [US1] Add dashboard runtime tests for `/tasks/list` and `/tasks/{taskId}` loading Temporal-backed rows in `tests/task_dashboard/test_queue_layouts.js` (DOC-REQ-001, DOC-REQ-011)

### Implementation for User Story 1

- [X] T013 [US1] Implement `GET /api/tasks/list` and `GET /api/tasks/{taskId}` compatibility routes in `api_service/api/routers/task_compatibility.py` and `api_service/main.py` (DOC-REQ-001, DOC-REQ-011)
- [X] T014 [US1] Implement normalized Temporal task row/detail builders for `MoonMind.Run` and `MoonMind.ManifestIngest` in `moonmind/workflows/tasks/compatibility.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-002, DOC-REQ-005, DOC-REQ-006)
- [X] T015 [US1] Persist and consult task source mappings for compatibility list/detail resolution and safe legacy backfill in `moonmind/workflows/tasks/source_mapping.py`, `api_service/api/routers/task_compatibility.py`, and `api_service/api/routers/task_dashboard.py` (DOC-REQ-004, DOC-REQ-010)
- [X] T016 [US1] Update unified task dashboard source taxonomy and canonical shell routing for Temporal-backed rows in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-011)
- [X] T017 [US1] Keep queue-backed manifest jobs on `source=queue` while exposing Temporal manifest executions as `source=temporal` plus `entry=manifest` in `moonmind/workflows/tasks/compatibility.py` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-002, DOC-REQ-011)

**Checkpoint**: User Story 1 is independently testable as the MVP compatibility slice.

---

## Phase 4: User Story 2 - Operate Temporal Executions Through Task Controls (Priority: P1)

**Goal**: Preserve task-facing actions and stable task identity while routing behavior through Temporal-native controls.

**Independent Test**: Exercise edit, rename, rerun, approve, pause, resume, callback, and cancel flows for Temporal-backed tasks and verify accepted/applied/message semantics, graceful cancellation, and stable detail routing across Continue-As-New.

### Tests for User Story 2

- [ ] T018 [P] [US2] Add contract tests for task-facing edit, rerun, approval, pause/resume, callback, and cancel compatibility semantics in `tests/contract/test_temporal_execution_api.py` and `tests/contract/test_task_compatibility_api.py` (DOC-REQ-003, DOC-REQ-008)
- [ ] T019 [P] [US2] Add unit tests for action availability, stable task identity across Continue-As-New, and terminal no-op handling in `tests/unit/workflows/tasks/test_task_compatibility_service.py` (DOC-REQ-003, DOC-REQ-007, DOC-REQ-008)
- [ ] T020 [P] [US2] Add dashboard and view-model tests for compatibility action affordances and task-first messaging in `tests/task_dashboard/test_queue_layouts.js` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-006, DOC-REQ-008, DOC-REQ-011)

### Implementation for User Story 2

- [X] T021 [US2] Implement compatibility action availability blocks and task-safe detail action metadata in `moonmind/workflows/tasks/compatibility.py` and `moonmind/schemas/task_compatibility_models.py` (DOC-REQ-006, DOC-REQ-008)
- [X] T022 [US2] Map task-facing rename, edit-inputs, and rerun semantics onto Temporal update handlers with accepted/applied/message responses in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-003, DOC-REQ-008)
- [X] T023 [US2] Implement approval, pause, resume, and external callback routing with explicit authorization checks in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-006, DOC-REQ-008)
- [X] T024 [US2] Implement graceful-by-default cancel behavior, separate force-terminate handling, and explicit terminal unavailability responses in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-007, DOC-REQ-008)
- [X] T025 [US2] Preserve stable task identity across Continue-As-New reruns by updating mapping and detail resolution behavior in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/tasks/source_mapping.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-008, DOC-REQ-010)
- [ ] T026 [US2] Update task dashboard action wiring and copy to stay task-first while calling Temporal-native control endpoints in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-006, DOC-REQ-008, DOC-REQ-011)

**Checkpoint**: User Story 2 controls are independently testable with compatibility semantics preserved.

---

## Phase 5: User Story 3 - Query Mixed Sources Without Losing Meaning (Priority: P2)

**Goal**: Deliver mixed-source task queries with stable sorting, opaque merged cursors, accurate count-mode signaling, and normalized status behavior.

**Independent Test**: Run Temporal-only and mixed-source list queries, then verify normalized statuses, preserved raw lifecycle fields, compatibility-owned cursors, and `countMode` behavior without raw Temporal token leakage.

### Tests for User Story 3

- [ ] T027 [P] [US3] Add contract tests for mixed-source cursor ownership, count-mode behavior, and normalized status preservation in `tests/contract/test_task_compatibility_api.py` (DOC-REQ-007, DOC-REQ-009)
- [ ] T028 [P] [US3] Add unit tests for mixed-source cursor encoding/decoding, sort merging, and raw Temporal token isolation in `tests/unit/workflows/tasks/test_task_compatibility_service.py` (DOC-REQ-009, DOC-REQ-010)
- [ ] T029 [P] [US3] Add dashboard runtime tests for mixed-source pagination, count display, and `awaiting_action` labeling in `tests/task_dashboard/test_queue_layouts.js` (DOC-REQ-007, DOC-REQ-009, DOC-REQ-011)

### Implementation for User Story 3

- [X] T030 [US3] Implement the compatibility-owned mixed-source cursor envelope plus filter and page-size validation in `moonmind/workflows/tasks/compatibility.py` and `moonmind/schemas/task_compatibility_models.py` (DOC-REQ-009, DOC-REQ-010)
- [X] T031 [US3] Implement merged queue, orchestrator, and Temporal task sorting plus `countMode` calculation in `api_service/api/routers/task_compatibility.py` and `moonmind/workflows/tasks/compatibility.py` (DOC-REQ-001, DOC-REQ-007, DOC-REQ-009)
- [ ] T032 [US3] Keep Temporal-only query behavior source-native while preventing raw `nextPageToken` leakage in unified task responses in `api_service/api/routers/task_compatibility.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-009, DOC-REQ-010)
- [X] T033 [US3] Expose normalized `awaiting_action`, `failed`, and `cancelled` presentation while preserving raw lifecycle fields in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-007, DOC-REQ-011)
- [X] T034 [US3] Harden task-safe metadata bounding and reviewed parameter preview output for compatibility list/detail payloads in `moonmind/workflows/tasks/compatibility.py` and `api_service/api/routers/task_compatibility.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-010)

**Checkpoint**: User Story 3 mixed-source query behavior is independently testable and contract-safe.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize compatibility documentation artifacts and run repository-standard validation and scope gates.

- [ ] T035 [P] Sync compatibility contract and traceability artifacts to final runtime behavior in `specs/047-task-execution-compatibility/contracts/task-execution-compatibility.openapi.yaml` and `specs/047-task-execution-compatibility/contracts/requirements-traceability.md` (DOC-REQ-001, DOC-REQ-009, DOC-REQ-011)
- [X] T036 Run repository unit/dashboard validation via `./tools/test_unit.sh` and resolve regressions across `tests/unit/` and `tests/task_dashboard/` for compatibility coverage (DOC-REQ-001 through DOC-REQ-011)
- [ ] T037 [P] Run targeted contract validation for `tests/contract/test_task_compatibility_api.py` and `tests/contract/test_temporal_execution_api.py`, then execute the Temporal-only and mixed-source verification scenarios documented in `specs/047-task-execution-compatibility/quickstart.md` (DOC-REQ-001 through DOC-REQ-011)
- [X] T038 Execute runtime scope gates with `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` (DOC-REQ-001, DOC-REQ-002)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No prerequisites.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all story work.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers the MVP compatibility slice.
- **Phase 4 (US2)**: Depends on Phase 2 and benefits from US1 canonical list/detail surfaces.
- **Phase 5 (US3)**: Depends on Phase 2 and benefits from US1 list/detail normalization being in place.
- **Phase 6 (Polish)**: Depends on completion of the targeted runtime stories.

### User Story Dependencies

- **US1 (P1)**: First deliverable after foundational work; no dependency on later stories.
- **US2 (P1)**: Depends on US1 identity and detail surfaces so task-facing actions attach to the canonical compatibility payload.
- **US3 (P2)**: Depends on US1 normalized list behavior and can land after shared compatibility services exist.

### Within Each User Story

- Add and run the story’s tests first.
- Implement shared schema/service changes before router or dashboard wiring.
- Re-run story-specific validation before moving to the next story.

### Parallel Opportunities

- Phase 1 tasks `T002-T004` can run in parallel after `T001` defines the routing boundary.
- US1 validation tasks `T010-T012` can run in parallel.
- US2 validation tasks `T018-T020` can run in parallel.
- US3 validation tasks `T027-T029` can run in parallel.
- Phase 6 tasks `T035` and `T037` can run in parallel after runtime behavior stabilizes.

---

## Parallel Example: User Story 1

```bash
# Execute US1 validation tracks concurrently:
Task T010: tests/contract/test_task_compatibility_api.py
Task T011: tests/unit/api/routers/test_task_compatibility.py + tests/unit/api/routers/test_task_dashboard_view_model.py
Task T012: tests/task_dashboard/test_queue_layouts.js
```

## Parallel Example: User Story 2

```bash
# Execute US2 validation tracks concurrently:
Task T018: tests/contract/test_temporal_execution_api.py + tests/contract/test_task_compatibility_api.py
Task T019: tests/unit/workflows/tasks/test_task_compatibility_service.py
Task T020: tests/task_dashboard/test_queue_layouts.js + tests/unit/api/routers/test_task_dashboard_view_model.py
```

## Parallel Example: User Story 3

```bash
# Execute US3 validation tracks concurrently:
Task T027: tests/contract/test_task_compatibility_api.py
Task T028: tests/unit/workflows/tasks/test_task_compatibility_service.py
Task T029: tests/task_dashboard/test_queue_layouts.js
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2.
2. Deliver Phase 3 (US1) canonical list/detail compatibility APIs.
3. Validate US1 independently using `T010-T012`.
4. Demo the unified task shell against Temporal-backed executions.

### Incremental Delivery

1. Finish shared compatibility setup and foundational invariants.
2. Ship US1 for canonical list/detail compatibility.
3. Add US2 for task-facing action compatibility and stable rerun identity.
4. Add US3 for mixed-source pagination, count-mode, and status normalization.
5. Finish with Phase 6 validation and runtime scope gates.

### Parallel Team Strategy

1. Pair on Phase 1 and Phase 2 until routing, schemas, and source mapping are stable.
2. Split by story after foundations land:
   - Engineer A: US1 list/detail compatibility
   - Engineer B: US2 action compatibility
   - Engineer C: US3 mixed-source querying
3. Rejoin for Phase 6 validation and scope gates.

---

## Task Summary

- Total tasks: **38**
- Story task count: **US1 = 8**, **US2 = 9**, **US3 = 8**
- Parallelizable tasks (`[P]`): **14**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **all tasks follow `- [ ] T### [P?] [US?] ...` with explicit path references**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T001, T004, T013, T031 | T010, T012, T036, T037, T038 |
| DOC-REQ-002 | T014, T016, T017 | T010, T011 |
| DOC-REQ-003 | T007, T022, T025 | T010, T018, T019 |
| DOC-REQ-004 | T002, T004, T005, T008, T015, T025 | T009, T011, T038 |
| DOC-REQ-005 | T003, T006, T014, T034 | T009, T010 |
| DOC-REQ-006 | T003, T006, T014, T021, T023, T026, T034 | T009, T010, T020 |
| DOC-REQ-007 | T003, T007, T024, T031, T033 | T009, T019, T027, T029 |
| DOC-REQ-008 | T021, T022, T023, T024, T025, T026 | T018, T019, T020 |
| DOC-REQ-009 | T003, T030, T031, T032, T035 | T027, T028, T029, T036, T037 |
| DOC-REQ-010 | T002, T004, T005, T006, T025, T030, T032, T034 | T009, T028 |
| DOC-REQ-011 | T001, T013, T016, T017, T026, T033, T035 | T010, T011, T012, T020, T029 |

Coverage gate rule: every `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.
