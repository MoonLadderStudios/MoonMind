# Tasks: Temporal Dashboard Integration

**Input**: Design documents from `/specs/048-temporal-dashboard-integration/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Tests are required because the specification mandates runtime validation coverage for list, detail, actions, submit routing, and artifacts.  
**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only
- Every task includes concrete runtime or validation paths and carries `DOC-REQ-*` tags for traceability

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present in `T001-T009`, `T014-T018`, `T022-T025`, and `T029-T031`.
- Runtime validation tasks are explicitly present in `T010-T013`, `T019-T021`, `T026-T028`, and `T033-T035`.
- `DOC-REQ-001` through `DOC-REQ-019` implementation and validation coverage is enforced by the per-task tags and the `DOC-REQ Coverage Matrix` in this file, with persistent mapping in `specs/048-temporal-dashboard-integration/contracts/requirements-traceability.md`.
- Validation execution remains explicit: `./tools/test_unit.sh` covers unit + dashboard JS suites, while targeted `pytest` commands cover Temporal contract and browser/e2e suites that are outside the current `test_unit.sh` scope.
- Runtime-mode completion requires production code changes plus automated validation; docs-only task sets are invalid for this feature.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared Temporal dashboard configuration and shell scaffolding before story work begins.

- [X] T001 Add env-backed Temporal dashboard rollout settings in `moonmind/config/settings.py` for list/detail/actions/submit/debug flags and source endpoint defaults (DOC-REQ-004, DOC-REQ-006, DOC-REQ-018).
- [X] T002 [P] Export the authoritative Temporal runtime-config contract in `api_service/api/routers/task_dashboard_view_model.py` for `sources.temporal`, `statusMaps.temporal`, and `system.taskSourceResolver` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007).
- [X] T003 [P] Extend canonical dashboard route allowlists and Temporal-safe task-id handling in `api_service/api/routers/task_dashboard.py` for `/tasks/list`, `/tasks/new`, and `/tasks/{taskId}` compatibility (DOC-REQ-007, DOC-REQ-017).
- [X] T004 [P] Add normalized Temporal row/detail/action model fields in `moonmind/schemas/temporal_models.py` for `rawState`, `temporalStatus`, `closeStatus`, `waitingReason`, `attentionRequired`, latest-run identity semantics, and reserved legacy `runId` separation (DOC-REQ-005, DOC-REQ-011, DOC-REQ-013, DOC-REQ-017).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared API and dashboard primitives required by every user story.

**⚠️ CRITICAL**: No user story work should start until this phase is complete.

- [X] T005 Implement Temporal execution query/filter normalization, owner-policy enforcement, authoritative `repo`/`integration` and `countMode` passthrough, and list/detail payload expansion in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-005, DOC-REQ-008, DOC-REQ-010, DOC-REQ-011, DOC-REQ-017).
- [X] T006 [P] Implement canonical task-source resolution and Temporal ownership-aware fallback behavior in `api_service/api/routers/task_dashboard.py` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-002, DOC-REQ-007, DOC-REQ-017).
- [X] T007 [P] Implement shared Temporal artifact presentation plus MoonMind-managed create/upload/download metadata helpers in `api_service/api/routers/temporal_artifacts.py` and `api_service/api/routers/executions.py` (DOC-REQ-002, DOC-REQ-012, DOC-REQ-016, DOC-REQ-019).
- [X] T008 [P] Add shared Temporal dashboard client helpers in `api_service/static/task_dashboard/dashboard.js` for query parsing, `repo`/`integration` filters, status normalization, identifier handling, and Temporal-aware sort keys (DOC-REQ-001, DOC-REQ-008, DOC-REQ-011, DOC-REQ-017).
- [X] T009 Add feature-gated Temporal list/detail UI anchors and submit/detail shell containers in `api_service/templates/task_dashboard.html` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-015, DOC-REQ-018).

**Checkpoint**: Shared routing, config, data, and artifact primitives are ready and user stories can proceed.

---

## Phase 3: User Story 1 - Operators Can See Temporal Tasks in the Existing Dashboard (Priority: P1) 🎯 MVP

**Goal**: Make Temporal-backed work visible inside the existing `/tasks` list and detail routes with canonical route resolution and authoritative Temporal-only list semantics.

**Independent Test**: Enable Temporal read flags, load mixed-source and `source=temporal` list views, open `/tasks/{taskId}` for a Temporal-backed item, and confirm normalized status, latest-run metadata, and artifacts render without any direct Temporal browser calls.

### Tests for User Story 1

- [X] T010 [P] [US1] Add runtime-config and source-resolution unit coverage in `tests/unit/api/routers/test_task_dashboard_view_model.py` and `tests/unit/api/routers/test_task_dashboard.py` for Temporal endpoints, feature flags, canonical routes, and source resolution responses (DOC-REQ-004, DOC-REQ-006, DOC-REQ-007, DOC-REQ-017).
- [X] T011 [P] [US1] Add Temporal execution list/detail contract coverage in `tests/contract/test_temporal_execution_api.py` for query filters, `repo`/`integration` passthrough, owner gating, pinned-source pagination tokens, `count`/`countMode`, and normalized list/detail payload fields (DOC-REQ-005, DOC-REQ-008, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012).
- [X] T012 [P] [US1] Add Temporal dashboard list/detail client coverage in `tests/task_dashboard/test_temporal_dashboard.js` for mixed-source merge behavior, authoritative Temporal-only `countMode` semantics, deterministic sorting, task-oriented labels, and reserved `runId` separation without raw-history browsing, and wire that suite into `./tools/test_unit.sh` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-013, DOC-REQ-017, DOC-REQ-019).
- [X] T013 [P] [US1] Extend canonical route browser coverage in `tests/e2e/test_task_create_submit_browser.py` for `/tasks/{taskId}` Temporal detail resolution and latest-run artifact fetch sequencing (DOC-REQ-007, DOC-REQ-012, DOC-REQ-016).

### Implementation for User Story 1

- [X] T014 [US1] Consume the settings-backed Temporal runtime-config contract from `T001-T002` inside `moonmind/config/settings.py` and `api_service/api/routers/task_dashboard_view_model.py` so read-path feature behavior is exported from configuration rather than hardcoded (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006).
- [X] T015 [US1] Connect the canonical source-resolution primitives from `T003` and `T006` into Temporal-safe detail routing in `api_service/api/routers/task_dashboard.py` and `api_service/api/routers/executions.py` while keeping `taskId == workflowId` as the durable route identity (DOC-REQ-007, DOC-REQ-012, DOC-REQ-017).
- [X] T016 [US1] Wire the Temporal query/filter primitives from `T005` through the dashboard-facing `source=temporal` behavior in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py`, including operator-only owner filters plus authoritative `repo`/`integration`/pagination/`countMode` passthrough (DOC-REQ-008, DOC-REQ-009, DOC-REQ-010).
- [X] T017 [US1] Implement Temporal row normalization, mixed-source bounded merge logic, informational total semantics, and Temporal-aware sorting precedence in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011).
- [X] T018 [US1] Consume the artifact helpers from `T007` in execution-first detail loading, latest-run artifact lookup, and normalized detail header rendering in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/temporal_artifacts.py` without exposing raw Temporal event history (DOC-REQ-012, DOC-REQ-013, DOC-REQ-016, DOC-REQ-019).

**Checkpoint**: User Story 1 is independently functional and provides the MVP read experience.

---

## Phase 4: User Story 2 - Operators Can Understand State, Artifacts, and Allowed Actions (Priority: P2)

**Goal**: Expose normalized Temporal state, latest-run artifacts, blocked-state cues, and only the actions that are valid for the current execution state.

**Independent Test**: Open Temporal-backed tasks in multiple states, verify normalized badges plus optional debug metadata, confirm latest-run artifacts load from detail-derived run IDs, and exercise only the allowed action set for each state.

### Tests for User Story 2

- [X] T019 [P] [US2] Add action-capability and debug-field unit coverage in `tests/unit/api/routers/test_executions.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` for state-aware actions and phased feature-flag behavior (DOC-REQ-005, DOC-REQ-006, DOC-REQ-014).
- [X] T020 [P] [US2] Add latest-run artifact, upload-helper, and download-policy contract coverage in `tests/contract/test_temporal_artifact_api.py` for execution-scoped artifact lists, placeholder/create-upload-complete flows, preview metadata, and MoonMind-managed raw access behavior (DOC-REQ-012, DOC-REQ-016, DOC-REQ-019).
- [X] T021 [P] [US2] Extend Temporal detail client coverage in `tests/task_dashboard/test_temporal_dashboard.js` for blocked-state cues, synthesized timeline rendering, task-oriented action copy, and action visibility matrices across lifecycle states (DOC-REQ-001, DOC-REQ-005, DOC-REQ-013, DOC-REQ-014).

### Implementation for User Story 2

- [X] T022 [US2] Implement Temporal detail state normalization, debug metadata shaping, and action-capability payload generation in `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-005, DOC-REQ-013, DOC-REQ-014).
- [X] T023 [US2] Implement latest-run artifact presentation, create/upload/download policy metadata, and authorized artifact access in `api_service/api/routers/temporal_artifacts.py` and `api_service/api/routers/executions.py` (DOC-REQ-012, DOC-REQ-016, DOC-REQ-019).
- [X] T024 [US2] Implement task-oriented Temporal detail UI, synthesized timeline sections, blocked-state cues, and artifact presentation in `api_service/static/task_dashboard/dashboard.js` and `api_service/templates/task_dashboard.html` (DOC-REQ-001, DOC-REQ-013, DOC-REQ-014, DOC-REQ-016).
- [X] T025 [US2] Implement state-aware Temporal action handlers and post-action detail refresh flows in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/executions.py` (DOC-REQ-006, DOC-REQ-014, DOC-REQ-018).

**Checkpoint**: User Story 2 is independently functional with safe operator state and action handling.

---

## Phase 5: User Story 3 - Users Can Submit Task-Shaped Requests Without a Temporal Runtime Picker (Priority: P3)

**Goal**: Keep the existing task submission UX intact while allowing backend-routed Temporal starts and canonical redirects for eligible task-shaped submissions.

**Independent Test**: Submit supported task-shaped flows from `/tasks/new`, confirm the runtime picker never exposes `temporal`, and verify successful Temporal-backed creates redirect to `/tasks/{taskId}?source=temporal` with artifact-first handling for large inputs.

### Tests for User Story 3

- [X] T026 [P] [US3] Extend submit-runtime and runtime-config coverage in `tests/task_dashboard/test_submit_runtime.js` and `tests/unit/api/routers/test_task_dashboard_view_model.py` to keep Temporal out of picker values while exposing backend-routed submit flags (DOC-REQ-003, DOC-REQ-006, DOC-REQ-015).
- [X] T027 [P] [US3] Extend Temporal create contract coverage in `tests/contract/test_temporal_execution_api.py` for backend-routed task-shaped creates, artifact-ref inputs, artifact helper handoff, and canonical Temporal identity/redirect fields without legacy `runId` reuse (DOC-REQ-003, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017).
- [X] T028 [P] [US3] Extend submit browser coverage in `tests/e2e/test_task_create_submit_browser.py` for Temporal-backed create redirects to `/tasks/{taskId}?source=temporal` and large-input artifact-first submit flows while preserving the existing submit UX (DOC-REQ-003, DOC-REQ-007, DOC-REQ-015).

### Implementation for User Story 3

- [X] T029 [US3] Keep the runtime picker worker-only and gate Temporal submit rollout in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/static/task_dashboard/dashboard.js` so engine routing stays hidden from users (DOC-REQ-003, DOC-REQ-006, DOC-REQ-015).
- [X] T030 [US3] Implement backend-routed Temporal create behavior and task-shaped submit payload handling in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py`, including artifact-first large-input placeholder/upload/complete paths (DOC-REQ-002, DOC-REQ-003, DOC-REQ-015, DOC-REQ-016).
- [X] T031 [US3] Implement submit success redirect handling, source-aware detail hydration, artifact-ref persistence cues, and Temporal identity display that keeps legacy `runId` separate from `temporalRunId` in `api_service/static/task_dashboard/dashboard.js` and `api_service/templates/task_dashboard.html` (DOC-REQ-001, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017).

**Checkpoint**: User Story 3 is independently functional and completes the end-to-end task-shaped Temporal submit path.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize traceability, run the required validation commands, and record final rollout verification guidance.

- [X] T032 [P] Update final implementation and validation evidence in `specs/048-temporal-dashboard-integration/contracts/requirements-traceability.md` for `DOC-REQ-001` through `DOC-REQ-019` (DOC-REQ-018).
- [X] T033 Run the required regression suite by executing `./tools/test_unit.sh` for `tests/unit/api/routers/test_task_dashboard_view_model.py`, `tests/unit/api/routers/test_task_dashboard.py`, `tests/unit/api/routers/test_executions.py`, `tests/task_dashboard/test_temporal_dashboard.js`, and `tests/task_dashboard/test_submit_runtime.js`, then execute targeted `pytest` coverage for `tests/contract/test_temporal_execution_api.py`, `tests/contract/test_temporal_artifact_api.py`, and `tests/e2e/test_task_create_submit_browser.py` (DOC-REQ-001 through DOC-REQ-019).
- [X] T034 Run runtime scope gates with `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` using `specs/048-temporal-dashboard-integration/tasks.md` (DOC-REQ-018).
- [X] T035 [P] Record final rollout and operator validation steps in `specs/048-temporal-dashboard-integration/quickstart.md` for flags, mixed-source list behavior, Temporal-only pagination, detail/actions, submit redirects, and artifact handling (DOC-REQ-018).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No prerequisites.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all story work.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers the MVP read experience.
- **Phase 4 (US2)**: Depends on Phase 2 and builds on the read surfaces from US1 for actions and artifacts.
- **Phase 5 (US3)**: Depends on Phase 2 and reuses read/detail foundations; full validation benefits from US1 and US2 behavior being in place.
- **Phase 6 (Polish)**: Depends on completion of the targeted story phases.

### User Story Dependencies

- **US1 (P1)**: First independent slice after foundations; no dependency on later stories.
- **US2 (P2)**: Depends on the Temporal read/detail surfaces from US1.
- **US3 (P3)**: Depends on foundational config/routing plus the Temporal source/detail behavior already implemented for US1.

### Within Each User Story

- Add or update validation tasks first and verify they fail before implementation.
- Finish API/schema behavior before the dashboard client work that consumes it.
- Complete story-specific implementation before re-running that story's tests.

### Parallel Opportunities

- Setup tasks `T002-T004` can run in parallel after `T001`.
- Foundational tasks `T006-T008` can run in parallel after `T005`.
- US1 validation tasks `T010-T013` can run in parallel.
- US2 validation tasks `T019-T021` can run in parallel.
- US3 validation tasks `T026-T028` can run in parallel.
- Polish tasks `T032` and `T035` can run in parallel after validation completes.

---

## Parallel Example: User Story 1

```bash
Task T010: tests/unit/api/routers/test_task_dashboard_view_model.py + tests/unit/api/routers/test_task_dashboard.py
Task T011: tests/contract/test_temporal_execution_api.py
Task T012: tests/task_dashboard/test_temporal_dashboard.js
Task T013: tests/e2e/test_task_create_submit_browser.py
```

## Parallel Example: User Story 2

```bash
Task T019: tests/unit/api/routers/test_executions.py + tests/unit/api/routers/test_task_dashboard_view_model.py
Task T020: tests/contract/test_temporal_artifact_api.py
Task T021: tests/task_dashboard/test_temporal_dashboard.js
```

## Parallel Example: User Story 3

```bash
Task T026: tests/task_dashboard/test_submit_runtime.js + tests/unit/api/routers/test_task_dashboard_view_model.py
Task T027: tests/contract/test_temporal_execution_api.py
Task T028: tests/e2e/test_task_create_submit_browser.py
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational.
3. Complete Phase 3: User Story 1.
4. Validate US1 independently before enabling later rollout phases.

### Incremental Delivery

1. Lock shared config, routing, artifact, and dashboard primitives in Phases 1-2.
2. Deliver US1 for list/detail visibility.
3. Deliver US2 for state, artifacts, and action safety.
4. Deliver US3 for task-shaped submit routing.
5. Finish with Phase 6 validation and scope gates.

### Parallel Team Strategy

1. Align on Setup and Foundational tasks first.
2. After Phase 2:
   Engineer A can drive US1 list/detail work.
   Engineer B can prepare US2 state/action/artifact validation.
   Engineer C can prepare US3 submit routing validation.
3. Rejoin for final regression, scope gates, and rollout verification.

---

## Quality Gates

1. Runtime tasks gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime diff gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`
3. Repository-standard validation: `./tools/test_unit.sh` plus targeted Temporal contract/e2e `pytest` suites
4. Traceability gate: every `DOC-REQ-001` through `DOC-REQ-019` has at least one implementation task and one validation task.

## Task Summary

- Total tasks: **35**
- Story task count: **US1 = 9**, **US2 = 7**, **US3 = 6**
- Parallelizable tasks (`[P]`): **17**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **all tasks follow `- [ ] T### [P?] [US?] ...` with explicit paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T008, T017, T018, T024, T031 | T012, T021, T033 |
| DOC-REQ-002 | T006, T007, T009, T017, T030 | T012, T033 |
| DOC-REQ-003 | T029, T030 | T026, T027, T028 |
| DOC-REQ-004 | T001, T002, T014 | T010, T033 |
| DOC-REQ-005 | T002, T004, T005, T014, T022 | T011, T019, T021, T033 |
| DOC-REQ-006 | T001, T002, T009, T014, T025, T029 | T010, T019, T026, T033 |
| DOC-REQ-007 | T002, T003, T006, T015 | T010, T013, T028, T033 |
| DOC-REQ-008 | T005, T008, T016 | T011, T033 |
| DOC-REQ-009 | T016, T017 | T012, T033 |
| DOC-REQ-010 | T005, T016, T017 | T011, T012, T033 |
| DOC-REQ-011 | T004, T005, T008, T017 | T011, T012, T033 |
| DOC-REQ-012 | T007, T015, T018, T023 | T011, T013, T020, T033 |
| DOC-REQ-013 | T004, T018, T022, T024 | T012, T021, T033 |
| DOC-REQ-014 | T022, T024, T025 | T019, T021, T033 |
| DOC-REQ-015 | T009, T025, T029, T030, T031 | T026, T027, T028, T033 |
| DOC-REQ-016 | T007, T018, T023, T024, T030, T031 | T013, T020, T027, T033 |
| DOC-REQ-017 | T003, T004, T005, T006, T008, T015, T031 | T010, T012, T027, T033 |
| DOC-REQ-018 | T001, T009, T025, T032, T035 | T033, T034 |
| DOC-REQ-019 | T007, T018, T023 | T012, T020, T033 |
