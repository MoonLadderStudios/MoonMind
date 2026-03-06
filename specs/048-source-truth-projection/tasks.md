# Tasks: Temporal Source of Truth and Projection Model

**Input**: Design documents from `/specs/048-source-truth-projection/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `quickstart.md`, `contracts/`

**Tests**: Validation is required for this runtime-mode feature. Include unit, contract, integration, and runtime scope-gate tasks.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies).
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only.
- Every task includes exact file paths and carries `DOC-REQ-*` tags for traceability.

## Runtime Scope Controls

- Runtime implementation tasks are explicit in `T001-T029`.
- Runtime validation tasks are explicit in `T030-T032`.
- Every `DOC-REQ-001` through `DOC-REQ-019` appears in at least one implementation task and one validation task.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Align dependencies, configuration, and test scaffolding before the Temporal-authoritative runtime work starts.

- [ ] T001 Add the `temporalio` runtime dependency and Temporal source-of-truth configuration flags in `pyproject.toml` and `moonmind/config/settings.py` for `DOC-REQ-001`, `DOC-REQ-004`, and `DOC-REQ-018`.
- [ ] T002 [P] Export Temporal authority adapter entrypoints in `moonmind/workflows/temporal/__init__.py` and reserve module surfaces in `moonmind/workflows/temporal/client.py`, `moonmind/workflows/temporal/visibility.py`, and `moonmind/workflows/temporal/projection_repair.py` for `DOC-REQ-004`, `DOC-REQ-016`, and `DOC-REQ-018`.
- [ ] T003 [P] Add shared control-plane, Visibility, and degraded-mode fixture scaffolding in `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/contract/test_temporal_execution_api.py`, and `tests/integration/temporal/test_source_truth_projection.py` for `DOC-REQ-010`, `DOC-REQ-013`, and `DOC-REQ-018`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the shared adapters, persistence constraints, and snapshot/projection primitives that every story depends on.

**⚠️ CRITICAL**: No user-story work should start until this phase is complete.

- [ ] T004 Implement the Temporal control-plane adapter for start, describe, update, signal, cancel, and terminate operations in `moonmind/workflows/temporal/client.py` for `DOC-REQ-001`, `DOC-REQ-004`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-018`, and `DOC-REQ-019`.
- [ ] T005 [P] Implement the Visibility query/count adapter and truthful source-kind pagination contract in `moonmind/workflows/temporal/visibility.py` and `moonmind/schemas/temporal_models.py` for `DOC-REQ-001`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, and `DOC-REQ-018`.
- [ ] T006 [P] Add Workflow-ID keyed source/projection constraints, sync metadata defaults, and migration/backfill plumbing in `api_service/db/models.py` and `api_service/migrations/versions/202603060003_temporal_execution_source_truth_runtime.py` for `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-009`, and `DOC-REQ-019`.
- [ ] T007 Implement authoritative snapshot mapping, source metadata models, and projection upsert helpers in `moonmind/workflows/temporal/service.py` and `moonmind/schemas/temporal_models.py` for `DOC-REQ-002`, `DOC-REQ-004`, `DOC-REQ-006`, `DOC-REQ-009`, and `DOC-REQ-015`.

**Checkpoint**: Temporal adapters, projection invariants, and authoritative snapshot plumbing are ready for independent story work.

---

## Phase 3: User Story 1 - Use Temporal as the Execution Authority (Priority: P1) 🎯 MVP

**Goal**: Ensure start, mutate, and detail flows treat Temporal as authoritative and never invent accepted execution state from projection-only writes.

**Independent Test**: Start an execution, issue update, signal, and cancel requests, then verify the API and projection state reflect Temporal-accepted outcomes with no ghost-row success path.

### Tests for User Story 1

- [X] T008 [P] [US1] Extend authoritative start, update, signal, cancel, and accepted-write-failure unit coverage in `tests/unit/workflows/temporal/test_temporal_service.py` for `DOC-REQ-003`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, and `DOC-REQ-018`.
- [ ] T009 [P] [US1] Add Temporal control-plane adapter unit coverage in `tests/unit/workflows/temporal/test_temporal_client.py` for `DOC-REQ-001`, `DOC-REQ-010`, `DOC-REQ-011`, and `DOC-REQ-018`.
- [ ] T010 [P] [US1] Extend lifecycle router contract coverage for Temporal-backed create, mutate, and detail behavior in `tests/contract/test_temporal_execution_api.py` for `DOC-REQ-003`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, and `DOC-REQ-018`.
- [ ] T011 [P] [US1] Add authoritative write/detail integration coverage in `tests/integration/temporal/test_source_truth_projection.py` for `DOC-REQ-001`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, and `DOC-REQ-018`.

### Implementation for User Story 1

- [ ] T012 [US1] Rework execution start to authenticate first, call Temporal, and mirror accepted starts without leaving ghost rows in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/temporal/client.py` for `DOC-REQ-003`, `DOC-REQ-010`, `DOC-REQ-012`, and `DOC-REQ-018`.
- [ ] T013 [US1] Rework update, signal, cancel, and terminate flows to use authoritative Temporal outcomes and idempotent result reuse in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/temporal/client.py` for `DOC-REQ-011`, `DOC-REQ-012`, and `DOC-REQ-018`.
- [ ] T014 [US1] Move execution detail reads and response shaping to authoritative Temporal snapshots in `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` for `DOC-REQ-001`, `DOC-REQ-004`, `DOC-REQ-013`, and `DOC-REQ-015`.
- [X] T015 [US1] Persist repair-pending acceptance and authoritative projection sync markers after successful writes in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` for `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-016`, and `DOC-REQ-017`.

**Checkpoint**: Temporal-backed write and detail semantics are independently functional and no projection-only lifecycle success path remains.

---

## Phase 4: User Story 2 - Preserve Compatibility Without Hiding Temporal Backing (Priority: P1)

**Goal**: Keep `/api/executions` and task-oriented compatibility surfaces usable during migration while exposing truthful source metadata, identifier bridging, and count semantics.

**Independent Test**: Query execution and compatibility list/detail routes for Temporal-backed work and verify `taskId == workflowId`, truthful `countMode`, and explicit source labeling for direct, joined, and fallback payloads.

### Tests for User Story 2

- [ ] T016 [P] [US2] Add Visibility list/count and truthful `countMode` unit coverage in `tests/unit/workflows/temporal/test_visibility_adapter.py` and `tests/unit/workflows/temporal/test_temporal_service.py` for `DOC-REQ-013`, `DOC-REQ-014`, and `DOC-REQ-018`.
- [ ] T017 [P] [US2] Extend execution API contract coverage for Visibility-backed pagination, filtering, and count semantics in `tests/contract/test_temporal_execution_api.py` for `DOC-REQ-013`, `DOC-REQ-014`, and `DOC-REQ-015`.
- [ ] T018 [P] [US2] Add compatibility/dashboard source metadata and identifier-bridge coverage in `tests/unit/api/routers/test_task_dashboard.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/task_dashboard/test_submit_runtime.js` for `DOC-REQ-007`, `DOC-REQ-014`, `DOC-REQ-015`, and `DOC-REQ-018`.

### Implementation for User Story 2

- [ ] T019 [US2] Implement Visibility-backed execution listing, filtering, and truthful count reporting in `moonmind/workflows/temporal/visibility.py`, `moonmind/workflows/temporal/service.py`, and `api_service/api/routers/executions.py` for `DOC-REQ-013`, `DOC-REQ-014`, and `DOC-REQ-018`.
- [ ] T020 [US2] Extend execution list/detail schemas with source metadata, source kind, and honest sort/count fields in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` for `DOC-REQ-014` and `DOC-REQ-015`.
- [ ] T021 [US2] Preserve `taskId == workflowId`, canonical identifiers, and non-queue-order compatibility labeling in `api_service/api/routers/task_dashboard.py` and `api_service/api/routers/task_dashboard_view_model.py` for `DOC-REQ-007`, `DOC-REQ-008`, and `DOC-REQ-015`.
- [ ] T022 [US2] Route compatibility detail/list enrichment through explicit direct, join, and fallback source modes in `moonmind/workflows/temporal/service.py`, `api_service/api/routers/task_dashboard.py`, and `api_service/api/routers/task_dashboard_view_model.py` for `DOC-REQ-002`, `DOC-REQ-007`, `DOC-REQ-008`, and `DOC-REQ-014`.

**Checkpoint**: Direct and compatibility views remain usable while truthfully exposing Temporal-backed semantics and source posture.

---

## Phase 5: User Story 3 - Repair Drift and Degrade Honestly (Priority: P2)

**Goal**: Repair missing, stale, or orphaned projections deterministically and reject production write fallbacks when Temporal is unavailable.

**Independent Test**: Simulate projection drift, Continue-As-New, and subsystem outages, then verify repair restores canonical state or surfaces honest degraded behavior without duplicate primary rows.

### Tests for User Story 3

- [ ] T023 [P] [US3] Add unit coverage for post-write refresh ordering, periodic sweep, startup/backfill, stale/missing/orphan repair cases, Continue-As-New updates, and sync-state transitions in `tests/unit/workflows/temporal/test_projection_repair.py` and `tests/unit/workflows/temporal/test_temporal_service.py` for `DOC-REQ-006`, `DOC-REQ-009`, `DOC-REQ-016`, `DOC-REQ-017`, and `DOC-REQ-019`.
- [ ] T024 [P] [US3] Add degraded-mode, repair-on-read, periodic sweep, and startup/backfill integration coverage in `tests/integration/temporal/test_source_truth_projection.py` for `DOC-REQ-003`, `DOC-REQ-009`, `DOC-REQ-016`, `DOC-REQ-017`, `DOC-REQ-018`, and `DOC-REQ-019`.
- [ ] T025 [P] [US3] Extend router and dashboard fallback-honesty coverage in `tests/contract/test_temporal_execution_api.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` for `DOC-REQ-014`, `DOC-REQ-015`, and `DOC-REQ-018`.

### Implementation for User Story 3

- [ ] T026 [US3] Implement deterministic post-write, read-repair, periodic sweep, and startup/backfill orchestration in `moonmind/workflows/temporal/projection_repair.py` and `moonmind/workflows/temporal/service.py` for `DOC-REQ-016` and `DOC-REQ-017`.
- [ ] T027 [US3] Implement Workflow-ID in-place projection refresh, orphan quarantine, and stale run/state overwrite rules in `moonmind/workflows/temporal/projection_repair.py`, `moonmind/workflows/temporal/service.py`, and `api_service/db/models.py` for `DOC-REQ-003`, `DOC-REQ-006`, `DOC-REQ-009`, and `DOC-REQ-019`.
- [ ] T028 [US3] Enforce production degraded-mode write rejection and explicit non-production fallback guards in `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/client.py`, and `moonmind/config/settings.py` for `DOC-REQ-003` and `DOC-REQ-018`.
- [ ] T029 [US3] Surface degraded read decisions, stale/partial source metadata, and mixed-source outage reporting in `moonmind/schemas/temporal_models.py`, `api_service/api/routers/executions.py`, and `api_service/api/routers/task_dashboard_view_model.py` for `DOC-REQ-014` and `DOC-REQ-018`.

**Checkpoint**: Repair, Continue-As-New handling, and degraded-mode honesty are independently verifiable and operationally safe.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Lock traceability, run repository-standard validation, and prove the runtime scope gates pass.

- [ ] T030 [P] Reconcile final `DOC-REQ-001` through `DOC-REQ-019` implementation and validation coverage in `specs/048-source-truth-projection/contracts/requirements-traceability.md` and `tests/unit/specs/test_doc_req_traceability.py`.
- [ ] T031 [P] Run repository-standard unit and dashboard validation with `./tools/test_unit.sh` covering `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/workflows/temporal/test_temporal_client.py`, `tests/unit/workflows/temporal/test_visibility_adapter.py`, `tests/unit/workflows/temporal/test_projection_repair.py`, `tests/unit/api/routers/test_task_dashboard.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/task_dashboard/test_submit_runtime.js` for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, `DOC-REQ-016`, `DOC-REQ-017`, and `DOC-REQ-018`.
- [ ] T032 [P] Run compose-backed contract validation with `docker compose -f docker-compose.test.yaml run --rm -e TEST_TYPE=contract pytest` covering `tests/contract/test_temporal_execution_api.py` for `DOC-REQ-001`, `DOC-REQ-003`, `DOC-REQ-006`, `DOC-REQ-008`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, and `DOC-REQ-018`.
- [ ] T033 [P] Run compose-backed Temporal integration validation with `docker compose -f docker-compose.test.yaml run --rm -e TEST_TYPE=integration/temporal pytest` covering `tests/integration/temporal/test_source_truth_projection.py` for `DOC-REQ-001`, `DOC-REQ-003`, `DOC-REQ-006`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-016`, `DOC-REQ-017`, `DOC-REQ-018`, and `DOC-REQ-019`.
- [ ] T034 Run the runtime quickstart and scope gates from `specs/048-source-truth-projection/quickstart.md` with `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, `DOC-REQ-016`, `DOC-REQ-017`, `DOC-REQ-018`, and `DOC-REQ-019`.
- [X] T035 [P] Record the step-16 `DOC-REQ-*` completed-task audit in `specs/048-source-truth-projection/tasks.md` and run `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime --base-ref origin/main` plus `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, `DOC-REQ-016`, `DOC-REQ-017`, `DOC-REQ-018`, and `DOC-REQ-019`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)** starts immediately.
- **Phase 2 (Foundational)** depends on Phase 1 and blocks all story work.
- **Phase 3 (US1)** depends on Phase 2 and delivers the MVP write/detail authority shift.
- **Phase 4 (US2)** depends on Phase 2 and can proceed in parallel with US1 once the shared adapters and snapshot models exist.
- **Phase 5 (US3)** depends on US1 and US2 because repair and degraded-mode rules rely on the authoritative write/read paths already being in place.
- **Phase 6 (Polish)** depends on the targeted story work being complete.

### User Story Dependencies

- **US1 (P1)** starts after Foundational and delivers the critical Temporal-first lifecycle authority.
- **US2 (P1)** starts after Foundational and builds on the same adapters, but does not need US1 to finish before compatibility work begins.
- **US3 (P2)** starts after US1 and US2 because repair ordering and degraded-mode honesty depend on the authoritative control-plane and Visibility read paths already existing.

### Within Each User Story

- Add the story tests first and confirm they fail before implementation changes.
- Adapter or schema changes precede router and serializer wiring.
- Authoritative write/read behavior lands before repair or degraded-mode hardening built on top of it.
- Finish each story with its own independent validation before moving to the next priority.

## Parallel Opportunities

- `T002` and `T003` can run in parallel after `T001`.
- `T005` and `T006` can run in parallel after `T004`.
- `T008-T011` can run in parallel within US1.
- `T016-T018` can run in parallel within US2.
- `T023-T025` can run in parallel within US3.
- `T030`, `T031`, and `T032` can run in parallel before Temporal integration validation and the final quickstart/scope-gate run.

---

## Parallel Example: User Story 1

```bash
Task T008: tests/unit/workflows/temporal/test_temporal_service.py
Task T009: tests/unit/workflows/temporal/test_temporal_client.py
Task T010: tests/contract/test_temporal_execution_api.py
Task T011: tests/integration/temporal/test_source_truth_projection.py
```

## Parallel Example: User Story 2

```bash
Task T016: tests/unit/workflows/temporal/test_visibility_adapter.py + tests/unit/workflows/temporal/test_temporal_service.py
Task T017: tests/contract/test_temporal_execution_api.py
Task T018: tests/unit/api/routers/test_task_dashboard.py + tests/unit/api/routers/test_task_dashboard_view_model.py + tests/task_dashboard/test_submit_runtime.js
```

## Parallel Example: User Story 3

```bash
Task T023: tests/unit/workflows/temporal/test_projection_repair.py + tests/unit/workflows/temporal/test_temporal_service.py
Task T024: tests/integration/temporal/test_source_truth_projection.py
Task T025: tests/contract/test_temporal_execution_api.py + tests/unit/api/routers/test_task_dashboard_view_model.py
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1).
3. Validate the authoritative write/detail behavior independently before expanding compatibility and repair work.

### Incremental Delivery

1. Finish Setup + Foundational to lock Temporal adapters, projection constraints, and shared schemas.
2. Deliver US1 to make Temporal authoritative for write and detail flows.
3. Deliver US2 to make list/count/compatibility semantics truthful without hiding Temporal backing.
4. Deliver US3 to add deterministic repair and honest degraded-mode behavior.
5. Finish with traceability, unit validation, contract validation, Temporal integration validation, quickstart validation, and runtime scope gates.

### Parallel Team Strategy

1. One engineer completes Phase 1 and Phase 2.
2. After the foundational checkpoint:
   Engineer A can own US1 control-plane write/detail authority.
   Engineer B can own US2 Visibility and compatibility serialization.
3. US3 starts once those runtime surfaces exist and can be hardened together.

---

## DOC-REQ Coverage Matrix

Validation references include targeted story-level tests where available, plus the cross-cutting repository validation and scope-gate tasks in `T031` through `T034`.

| DOC-REQ | Implementation Tasks | Validation Tasks |
|---|---|---|
| DOC-REQ-001 | T001, T004, T005, T014 | T009, T011, T031, T032, T033, T034 |
| DOC-REQ-002 | T007, T022 | T031, T034 |
| DOC-REQ-003 | T012, T027, T028 | T008, T010, T024, T031, T032, T033, T034 |
| DOC-REQ-004 | T001, T002, T004, T007, T014 | T031, T034 |
| DOC-REQ-005 | T006 | T031, T034 |
| DOC-REQ-006 | T006, T007, T027 | T023, T031, T032, T033, T034 |
| DOC-REQ-007 | T021, T022 | T018, T031, T034 |
| DOC-REQ-008 | T021, T022 | T031, T032, T034 |
| DOC-REQ-009 | T006, T007, T015, T027 | T023, T024, T031, T032, T033, T034 |
| DOC-REQ-010 | T004, T012, T015 | T008, T009, T010, T011, T031, T032, T033, T034 |
| DOC-REQ-011 | T004, T013 | T008, T009, T010, T011, T031, T032, T033, T034 |
| DOC-REQ-012 | T012, T013 | T008, T010, T011, T031, T032, T033, T034 |
| DOC-REQ-013 | T005, T014, T019 | T016, T017, T031, T032, T033, T034 |
| DOC-REQ-014 | T005, T019, T020, T022, T029 | T016, T017, T018, T025, T031, T032, T033, T034 |
| DOC-REQ-015 | T005, T007, T014, T020, T021 | T017, T018, T025, T031, T032, T034 |
| DOC-REQ-016 | T002, T015, T026 | T023, T024, T031, T033, T034 |
| DOC-REQ-017 | T015, T026 | T023, T024, T031, T033, T034 |
| DOC-REQ-018 | T001, T002, T004, T005, T012, T013, T019, T028, T029 | T008, T009, T010, T011, T016, T018, T024, T025, T031, T032, T033, T034 |
| DOC-REQ-019 | T004, T006, T027 | T023, T024, T031, T033, T034 |

---

## Notes

- All tasks follow the required checklist format with sequential IDs.
- Runtime implementation mode is preserved: production file changes and validation tasks are both explicit.
- Suggested MVP scope: User Story 1.
