# Tasks: Manifest Ingest Runtime

**Input**: Design documents from `/specs/049-manifest-ingest-runtime/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Runtime validation is required by the feature spec (`FR-017`, `DOC-REQ-016`), so each user story includes explicit automated test tasks and final validation runs through `./tools/test_unit.sh`.

**Organization**: Tasks are grouped by user story so each story remains independently implementable and testable while preserving runtime-mode scope.

## Prompt B Scope Controls (Step 7/16)

- Runtime implementation tasks are explicitly present: `T001-T006`, `T011-T014`, `T018-T021`, `T025-T028`.
- Runtime validation tasks are explicitly present: `T007-T010`, `T015-T017`, `T022-T024`, `T029-T033`.
- `DOC-REQ-001` through `DOC-REQ-016` each retain at least one implementation task and one validation task, with persistent source mapping in `specs/049-manifest-ingest-runtime/contracts/requirements-traceability.md`.
- Existing shared `failurePolicy` input compatibility is preserved explicitly in runtime and validation tasks so `fail_fast`, `continue_and_report`, and `best_effort` stay caller-visible and deterministic.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared manifest-ingest runtime primitives, configuration, and worker bootstrap before story work begins.

- [X] T001 Add manifest-ingest runtime schemas for workflow input, status, checkpoint, summary, and run-index payloads in `moonmind/schemas/manifest_ingest_models.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-009, DOC-REQ-012, DOC-REQ-016)
- [X] T002 [P] Add Temporal SDK and manifest-ingest worker bootstrap support in `pyproject.toml`, `services/temporal/scripts/start-worker.sh`, and `moonmind/workflows/temporal/workers.py` (DOC-REQ-001, DOC-REQ-007, DOC-REQ-014, DOC-REQ-016)
- [X] T003 [P] Add manifest-ingest runtime settings for concurrency caps, scheduling batch size, and checkpoint thresholds in `moonmind/config/settings.py` and `docker-compose.yaml` (DOC-REQ-009, DOC-REQ-010, DOC-REQ-016)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the shared runtime, projection, and artifact plumbing that every user story depends on.

**CRITICAL**: Complete this phase before starting user story implementation.

- [X] T004 Implement `MoonMind.ManifestIngest` client start helpers, execution projection metadata, and shared manifest execution persistence in `moonmind/workflows/temporal/client.py`, `moonmind/workflows/temporal/service.py`, and `api_service/db/models.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-012, DOC-REQ-013, DOC-REQ-016)
- [X] T005 [P] Implement manifest artifact IO helpers for plan, checkpoint, summary, and run-index references in `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/activity_runtime.py` (DOC-REQ-003, DOC-REQ-006, DOC-REQ-010, DOC-REQ-014)
- [x] T006 [P] Wire temporal-backed manifest submission and execution serialization in `api_service/services/manifests_service.py`, `api_service/api/routers/manifests.py`, and `api_service/api/schemas.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-013, DOC-REQ-015, DOC-REQ-016)
- [x] T007 Add foundational validation for manifest-ingest submit and execution projection behavior in `tests/unit/services/test_manifests_service.py`, `tests/unit/api/routers/test_manifests.py`, and `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-013, DOC-REQ-016)

**Checkpoint**: Shared runtime startup, artifact references, and execution projection rules are in place; user story work can begin.

---

## Phase 3: User Story 1 - Start and execute a manifest ingest (Priority: P1) 🎯 MVP

**Goal**: Start `MoonMind.ManifestIngest` from a manifest artifact or registry-backed submit path, compile a normalized plan, and launch child `MoonMind.Run` workflows with durable lineage.

**Independent Test**: Start a manifest ingest from an artifact reference, then verify the runtime reads and validates the manifest, persists a compiled plan artifact, launches child runs with bounded concurrency, and writes summary and run-index artifacts at completion.

### Tests for User Story 1

- [X] T008 [P] [US1] Add workflow unit coverage for artifact-read, parse, validate, compile, plan persistence, and child-run lineage in `tests/unit/workflows/temporal/test_manifest_ingest.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-006, DOC-REQ-008, DOC-REQ-014)
- [X] T009 [P] [US1] Add service and router tests for registry-backed and artifact-first manifest-ingest creation in `tests/unit/services/test_manifests_service.py`, `tests/unit/api/routers/test_manifests.py`, and `tests/unit/api/routers/test_executions.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-005, DOC-REQ-013)
- [ ] T010 [P] [US1] Add contract and Temporal integration coverage for `MoonMind.ManifestIngest` startup and child-run orchestration in `tests/contract/test_temporal_execution_api.py` and `tests/integration/temporal/test_manifest_ingest_runtime.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004, DOC-REQ-006, DOC-REQ-016)

### Implementation for User Story 1

- [X] T011 [US1] Create the `MoonMind.ManifestIngest` workflow, lifecycle state machine, and bounded status snapshot logic in `moonmind/workflows/temporal/manifest_ingest.py` and `moonmind/schemas/manifest_ingest_models.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006)
- [X] T012 [US1] Implement manifest read, parse, validate, compile, and plan-persist activities in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-003, DOC-REQ-006, DOC-REQ-008, DOC-REQ-014)
- [X] T013 [US1] Implement child `MoonMind.Run` scheduling with immutable ingest lineage and request-cancel parent-close behavior in `moonmind/workflows/temporal/client.py` and `moonmind/workflows/temporal/manifest_ingest.py` (DOC-REQ-002, DOC-REQ-009, DOC-REQ-011)
- [X] T014 [US1] Expose temporal-backed manifest submission and shared execution detail fields for manifest ingest in `api_service/services/manifests_service.py`, `api_service/api/routers/manifests.py`, `api_service/api/routers/executions.py`, and `api_service/api/schemas.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-013, DOC-REQ-015)

**Checkpoint**: User Story 1 delivers a real Temporal-native manifest ingest path that starts, compiles, schedules child runs, and returns authoritative execution detail.

---

## Phase 4: User Story 2 - Edit and inspect an active ingest safely (Priority: P2)

**Goal**: Allow operators to update future manifest work and inspect active ingest progress through validated workflow Updates and bounded query surfaces.

**Independent Test**: Start a running ingest, invoke updates for manifest replacement or append, concurrency changes, pause or resume, cancel, and retry, then query status and node listings to confirm accepted changes and deterministic rejections.

### Tests for User Story 2

- [X] T015 [P] [US2] Add workflow tests for `UpdateManifest`, `SetConcurrency`, `Pause`, `Resume`, `CancelNodes`, and `RetryNodes` validation or acknowledgement behavior in `tests/unit/workflows/temporal/test_manifest_ingest.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-009, DOC-REQ-010)
- [X] T016 [P] [US2] Add API and contract tests for manifest update requests, bounded status queries, and node-page responses in `tests/unit/api/routers/test_executions.py` and `tests/contract/test_temporal_execution_api.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-012, DOC-REQ-015)
- [X] T017 [P] [US2] Add task-dashboard and view-model tests for manifest status snapshots and run-index-backed lineage pagination in `tests/unit/api/routers/test_task_dashboard_view_model.py` and `tests/task_dashboard/test_temporal_run_history.js` (DOC-REQ-005, DOC-REQ-012, DOC-REQ-015)

### Implementation for User Story 2

- [X] T018 [US2] Implement manifest-specific workflow Update handlers plus bounded query responses in `moonmind/workflows/temporal/manifest_ingest.py` and `moonmind/schemas/manifest_ingest_models.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-009, DOC-REQ-010)
- [X] T019 [US2] Persist manifest status, plan or summary or run-index references, and node counters in `moonmind/workflows/temporal/service.py`, `api_service/db/models.py`, and `api_service/migrations/versions/202603060001_manifest_temporal_runtime_metadata.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-012, DOC-REQ-015)
- [X] T020 [US2] Expose manifest update, status, and node-list API contracts in `api_service/api/schemas.py`, `api_service/api/routers/executions.py`, and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-013, DOC-REQ-015)
- [X] T021 [US2] Register manifest-ingest workflows and keep runtime selection at activity or task-queue routing boundaries in `moonmind/workflows/temporal/workers.py` and `moonmind/workflows/temporal/activity_catalog.py` (DOC-REQ-007, DOC-REQ-014)

**Checkpoint**: User Story 2 delivers acknowledged edit operations and bounded inspection surfaces without DB-side mutation becoming the source of truth.

---

## Phase 5: User Story 3 - Run large ingests with secure, accurate lineage (Priority: P3)

**Goal**: Keep manifest ingest within Temporal limits, preserve secure authorization lineage, and publish accurate shared-visibility and run-index outputs for large fan-out executions.

**Independent Test**: Exercise manifests large enough to require batching and checkpointing, verify Continue-As-New and concurrency enforcement behavior, and confirm visibility, memo, and run-index outputs preserve lineage without leaking secrets.

### Tests for User Story 3

- [X] T022 [P] [US3] Add workflow and activity tests for concurrency caps, `FAIL_FAST`, `continue_and_report`, and `BEST_EFFORT` behavior, checkpoint artifacts, Continue-As-New, and request-cancel child shutdown in `tests/unit/workflows/temporal/test_manifest_ingest.py` and `tests/unit/workflows/temporal/test_activity_runtime.py` (DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-014)
- [X] T023 [P] [US3] Add service and router tests for unauthorized artifact access, authorization lineage propagation, and secret-safe manifest responses in `tests/unit/services/test_manifests_service.py` and `tests/unit/api/routers/test_manifests.py` (DOC-REQ-013, DOC-REQ-016)
- [ ] T024 [P] [US3] Add integration, API, and dashboard coverage for run-index pagination, shared visibility totals, and large-ingest rollover behavior in `tests/integration/temporal/test_manifest_ingest_runtime.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/task_dashboard/test_temporal_run_history.js` (DOC-REQ-010, DOC-REQ-012, DOC-REQ-015, DOC-REQ-016)

### Implementation for User Story 3

- [X] T025 [US3] Implement policy-aware scheduling for `FAIL_FAST`, `continue_and_report`, and `BEST_EFFORT`, bounded concurrency, and Continue-As-New checkpoint recovery in `moonmind/workflows/temporal/manifest_ingest.py`, `moonmind/workflows/temporal/artifacts.py`, and `moonmind/config/settings.py` (DOC-REQ-009, DOC-REQ-010)
- [X] T026 [US3] Publish canonical summary and run-index artifacts plus bounded shared visibility metadata in `moonmind/workflows/temporal/manifest_ingest.py`, `moonmind/workflows/temporal/service.py`, and `api_service/db/models.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-012, DOC-REQ-015)
- [X] T027 [US3] Enforce immutable authorization lineage and secrecy guardrails across manifest ingest and child runs in `moonmind/workflows/temporal/manifest_ingest.py`, `moonmind/workflows/temporal/client.py`, `moonmind/workflows/temporal/artifacts.py`, and `api_service/services/manifests_service.py` (DOC-REQ-013, DOC-REQ-014)
- [X] T028 [US3] Align manifest-ingest detail and lineage pagination consumers with run-index-backed totals in `api_service/api/routers/executions.py`, `api_service/api/routers/task_dashboard_view_model.py`, and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-012, DOC-REQ-015)

**Checkpoint**: User Story 3 hardens the runtime for large, secure manifest execution with authoritative lineage and bounded Temporal state.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finish cross-story regression coverage, traceability, and runtime-scope validation.

- [X] T029 [P] Add cross-story regression coverage for worker registration, activity routing, artifact serialization, and requirements traceability in `tests/unit/workflows/temporal/test_temporal_workers.py`, `tests/unit/workflows/temporal/test_activity_catalog.py`, `tests/unit/workflows/temporal/test_artifacts.py`, and `tests/unit/specs/test_doc_req_traceability.py` (DOC-REQ-007, DOC-REQ-012, DOC-REQ-014, DOC-REQ-016)
- [X] T030 Verify `DOC-REQ-001` through `DOC-REQ-016` implementation and validation mappings in `specs/049-manifest-ingest-runtime/contracts/requirements-traceability.md` and `specs/049-manifest-ingest-runtime/quickstart.md` (DOC-REQ-016)
- [x] T031 Run `./tools/test_unit.sh` and record manifest-ingest runtime validation evidence in `specs/049-manifest-ingest-runtime/quickstart.md` (DOC-REQ-016)
- [x] T032 Run `SPECIFY_FEATURE=049-manifest-ingest-runtime ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and record the result in `specs/049-manifest-ingest-runtime/quickstart.md` (DOC-REQ-016)
- [x] T033 Run `SPECIFY_FEATURE=049-manifest-ingest-runtime ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` and record the result in `specs/049-manifest-ingest-runtime/quickstart.md` (DOC-REQ-016)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; start immediately.
- **Foundational (Phase 2)**: Depends on Setup and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational and is the MVP runtime increment.
- **User Story 2 (Phase 4)**: Depends on Foundational and should land after the US1 execution path is in place.
- **User Story 3 (Phase 5)**: Depends on Foundational and builds on the runtime, query, and lineage surfaces introduced by US1 and US2.
- **Polish (Phase 6)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: First deliverable for real Temporal-native manifest execution.
- **US2 (P2)**: Builds on US1 execution state and artifact references to add safe edit and inspect behavior.
- **US3 (P3)**: Builds on US1 and US2 to harden scaling, lineage, and security behavior under load.

### Within Each User Story

- Write the listed automated tests before finalizing implementation.
- Keep workflow or schema changes ahead of router or dashboard consumers.
- Preserve artifact-reference-first behavior whenever a task touches execution detail or lineage payloads.

### Parallel Opportunities

- `T002` and `T003` can run in parallel after `T001`.
- `T005` and `T006` can run in parallel after `T004`.
- `T008`, `T009`, and `T010` can run in parallel for US1 because they touch different test layers.
- `T015`, `T016`, and `T017` can run in parallel for US2 because they touch different test files.
- `T022`, `T023`, and `T024` can run in parallel for US3 because they touch different validation layers.
- `T029` can run in parallel with `T030` after user stories are complete.

---

## Parallel Example: User Story 1

```bash
# Parallel validation work for US1
Task: T008 tests/unit/workflows/temporal/test_manifest_ingest.py
Task: T009 tests/unit/services/test_manifests_service.py + tests/unit/api/routers/test_manifests.py + tests/unit/api/routers/test_executions.py
Task: T010 tests/contract/test_temporal_execution_api.py + tests/integration/temporal/test_manifest_ingest_runtime.py
```

## Parallel Example: User Story 2

```bash
# Parallel validation work for US2
Task: T015 tests/unit/workflows/temporal/test_manifest_ingest.py
Task: T016 tests/unit/api/routers/test_executions.py + tests/contract/test_temporal_execution_api.py
Task: T017 tests/unit/api/routers/test_task_dashboard_view_model.py + tests/task_dashboard/test_temporal_run_history.js
```

## Parallel Example: User Story 3

```bash
# Parallel validation work for US3
Task: T022 tests/unit/workflows/temporal/test_manifest_ingest.py + tests/unit/workflows/temporal/test_activity_runtime.py
Task: T023 tests/unit/services/test_manifests_service.py + tests/unit/api/routers/test_manifests.py
Task: T024 tests/integration/temporal/test_manifest_ingest_runtime.py + tests/unit/api/routers/test_task_dashboard_view_model.py + tests/task_dashboard/test_temporal_run_history.js
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Deliver Phase 3 so manifest ingest starts real Temporal workflows, compiles plans, and schedules child runs.
3. Validate the US1 independent test before layering edit or large-scale behavior.

### Incremental Delivery

1. Setup plus Foundational establishes runtime startup, artifact refs, and execution projection primitives.
2. Add US1 for Temporal-native manifest execution.
3. Add US2 for safe workflow Updates, status, and lineage inspection.
4. Add US3 for checkpointing, scale limits, and security or lineage hardening.
5. Finish with cross-story regression and runtime-scope gates.

### Parallel Team Strategy

1. Pair on Phase 1 and Phase 2 to lock shared runtime and projection primitives.
2. After foundation is complete:
   - Engineer A: US1 workflow and submission path
   - Engineer B: US2 update and query surfaces
   - Engineer C: US3 checkpointing, lineage, and security hardening
3. Rejoin for Phase 6 validation and scope-gate execution.

---

## Task Summary

- Total tasks: **33**
- User story tasks: **US1 = 7**, **US2 = 7**, **US3 = 7**
- Parallelizable tasks (`[P]`): **14**
- Suggested MVP scope: **through Phase 3 (User Story 1)**
- Checklist format validation: **All tasks follow `- [ ] T### [P?] [US?] ...` with explicit file paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T002, T004, T006, T011, T014 | T007, T009, T010 |
| DOC-REQ-002 | T004, T013 | T008, T010 |
| DOC-REQ-003 | T001, T005, T006, T012, T019, T026 | T007, T008, T009 |
| DOC-REQ-004 | T011, T018, T020 | T010, T015, T016 |
| DOC-REQ-005 | T001, T011, T014, T018, T019, T020, T026, T028 | T009, T015, T016, T017, T024 |
| DOC-REQ-006 | T005, T011, T012 | T008, T010 |
| DOC-REQ-007 | T002, T021 | T029 |
| DOC-REQ-008 | T012 | T008 |
| DOC-REQ-009 | T001, T003, T013, T018, T025 | T015, T022 |
| DOC-REQ-010 | T005, T018, T025 | T015, T022, T024 |
| DOC-REQ-011 | T013 | T022 |
| DOC-REQ-012 | T001, T004, T019, T026, T028 | T016, T017, T024, T029 |
| DOC-REQ-013 | T004, T006, T014, T020, T027 | T007, T009, T023 |
| DOC-REQ-014 | T002, T005, T012, T021, T027 | T008, T022, T029 |
| DOC-REQ-015 | T006, T014, T019, T020, T026, T028 | T016, T017, T024 |
| DOC-REQ-016 | T001, T002, T003, T004, T006 | T007, T010, T023, T024, T029, T030, T031, T032, T033 |

Coverage gate rule: each `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.
