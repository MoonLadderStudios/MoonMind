# Tasks: Temporal Visibility Query Model

**Input**: Design documents from `/specs/047-temporal-visibility-query/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Validation tests are required because the feature stays in runtime implementation mode and `FR-022` requires automated coverage.  
**Organization**: Tasks are grouped by user story so each slice stays independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only
- Every task includes concrete file path(s)

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly represented in `T001-T008`, `T013-T017`, `T021-T025`, and `T029-T032`.
- Runtime validation tasks are explicitly represented in `T009-T012`, `T018-T020`, `T026-T028`, and `T035-T038`.
- `DOC-REQ-001` through `DOC-REQ-018` implementation and validation coverage is enforced by per-task tags plus the `DOC-REQ Coverage Matrix` in this file, with persistent mapping in `specs/047-temporal-visibility-query/contracts/requirements-traceability.md`.
- Scope-gate commands use `SPECIFY_FEATURE=047-temporal-visibility-query` so the repository scripts resolve the correct feature directory from the MoonMind task branch.
- This delivery chooses the compatibility-adapter UI path; required freshness/degraded-read behavior still lands in the current operator-facing task surfaces, while a separately branded first-class `temporal` dashboard source remains a follow-up.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the shared schema, router, and projection scaffolding for the Temporal-backed compatibility contract.

- [X] T001 Update canonical execution query/list schemas to expose the planned top-level fields and pagination/count envelopes in `moonmind/schemas/temporal_models.py` (DOC-REQ-004, DOC-REQ-013, DOC-REQ-015).
- [X] T002 [P] Add compatibility-adapter identifier and status scaffolding in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` (DOC-REQ-016, DOC-REQ-018).
- [X] T003 [P] Extend projection/search-attribute persistence fields and add migration scaffold in `api_service/db/models.py` and `api_service/migrations/versions/202603060001_temporal_visibility_query_contract.py` (DOC-REQ-005, DOC-REQ-017, DOC-REQ-018).
- [X] T004 [P] Align execution router query parameters and response shaping with the canonical filter contract in `api_service/api/routers/executions.py` (DOC-REQ-001, DOC-REQ-010, DOC-REQ-013).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared runtime invariants that every user story depends on.

**⚠️ CRITICAL**: Shared query invariants from this phase must be finished before final runtime closeout. Story slices may land incrementally once the needed primitives are in place, but `T005` and `T008` remain blocking hardening work until they are done.

- [X] T005 Implement legacy-alias canonicalization on top of the current `workflowId` ownership and filter-normalization helpers in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-008, DOC-REQ-010).
- [X] T006 [P] Implement Search Attribute and Memo normalization helpers for `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry`, `title`, and `summary` in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-009).
- [X] T007 [P] Implement shared status and wait-metadata mapping helpers for exact state, Temporal status, `dashboardStatus`, `waitingReason`, and `attentionRequired` in `moonmind/workflows/temporal/service.py`, `moonmind/schemas/temporal_models.py`, and `api_service/api/routers/executions.py` (DOC-REQ-004, DOC-REQ-012).
- [X] T008 Implement projection drift-repair and Temporal-truth reconciliation helpers in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` (DOC-REQ-001, DOC-REQ-017).
- [X] T009 Add foundational unit coverage for filter normalization, state mapping, Search Attribute bounds, and owner handling in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-012).

**Checkpoint**: Core query primitives are in place; remaining alias and reconciliation hardening must finish before final closeout.

---

## Phase 3: User Story 1 - Query Temporal Executions Consistently (Priority: P1) 🎯 MVP

**Goal**: Make Temporal Visibility the canonical list/detail contract for execution querying, filtering, and ordering.

**Independent Test**: Seed executions with different owners, entries, workflow types, states, and update timestamps, then verify canonical list/detail fields, supported filters, and deterministic ordering through the execution API.

### Tests for User Story 1

- [X] T010 [P] [US1] Add contract tests for canonical list/detail fields, `workflowId` durability, `runId` debug-only placement, and detail-only `artifactRefs[]` behavior in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-002, DOC-REQ-004, DOC-REQ-015).
- [X] T011 [P] [US1] Add contract tests for the supported exact-match filters (`workflowType`, `ownerType`, `ownerId`, `state`, `entry`) plus invalid token and forbidden-owner rejection in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-006, DOC-REQ-010).
- [X] T012 [P] [US1] Add unit tests for meaningful `mm_updated_at` mutations, Search Attribute and Memo initialization, and scope-bound pagination behavior in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-009, DOC-REQ-011, DOC-REQ-017).

### Implementation for User Story 1

- [X] T013 [US1] Enforce required v1 Search Attributes plus owner identity invariants in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008).
- [X] T014 [US1] Implement canonical Memo persistence and top-level list/detail serialization for display-safe `title` and `summary` fields in `moonmind/workflows/temporal/service.py`, `moonmind/schemas/temporal_models.py`, and `api_service/api/routers/executions.py` (DOC-REQ-004, DOC-REQ-009, DOC-REQ-015).
- [X] T015 [US1] Implement canonical exact-filter handling for `workflowType`, `ownerType`, `ownerId`, `state`, and `entry` in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-001, DOC-REQ-010).
- [X] T016 [US1] Implement default `mm_updated_at DESC` then `workflowId DESC` ordering plus opaque `nextPageToken` and `countMode` response shaping in `moonmind/workflows/temporal/service.py`, `moonmind/schemas/temporal_models.py`, and `api_service/api/routers/executions.py` (DOC-REQ-011, DOC-REQ-013).
- [X] T017 [US1] Repair projection/cache drift in favor of Temporal-backed canonical rows before list/detail serialization in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` (DOC-REQ-001, DOC-REQ-017).

**Checkpoint**: User Story 1 is independently testable as the Temporal query-model MVP.

---

## Phase 4: User Story 2 - Preserve Task Compatibility Without Breaking Temporal Semantics (Priority: P1)

**Goal**: Keep task-oriented dashboard and compatibility surfaces working while preserving canonical identifiers, exact state semantics, and owner visibility rules.

**Independent Test**: Request Temporal-backed rows through compatibility adapters and verify `taskId == workflowId`, exact state plus compatibility status, `awaiting_external` wait metadata, and user-vs-operator visibility boundaries.

### Tests for User Story 2

- [X] T018 [P] [US2] Add contract tests for `taskId == workflowId` and non-admin owner scoping in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-003, DOC-REQ-008).
- [X] T019 [P] [US2] Add unit and contract tests for exact-state to `dashboardStatus` mapping plus `awaiting_external` wait-metadata behavior in `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/contract/test_temporal_execution_api.py` (DOC-REQ-012).
- [X] T020 [P] [US2] Add validation for the current task-dashboard compatibility path in `tests/unit/api/routers/test_task_dashboard_view_model.py` and `tests/unit/api/routers/test_task_dashboard.py`, proving Temporal-backed compatibility payloads do not require a first-class `temporal` runtime source (DOC-REQ-016, DOC-REQ-018).

### Implementation for User Story 2

- [X] T021 [US2] Enforce canonical identifier bridging so Temporal-backed compatibility surfaces return `taskId == workflowId` in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` (DOC-REQ-003, DOC-REQ-015).
- [X] T022 [US2] Implement exact state, close-status, `dashboardStatus`, `waitingReason`, and `attentionRequired` serialization rules for compatibility adapters in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-004, DOC-REQ-012).
- [X] T023 [US2] Enforce user-versus-operator visibility semantics for `mm_owner_type` and `mm_owner_id` across execution list/detail paths in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-008).
- [X] T024 [US2] Lock the compatibility-adapter UI path into the execution payload contract and keep `temporal` out of worker runtime selection in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` (DOC-REQ-016, DOC-REQ-018).
- [X] T025 [US2] Keep list responses small and server-owned by omitting `artifactRefs[]` from list payloads while preserving canonical detail metadata in `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-012, DOC-REQ-015, DOC-REQ-016).

**Checkpoint**: User Story 2 preserves task compatibility while keeping Temporal semantics authoritative.

---

## Phase 5: User Story 3 - Handle Pagination, Counts, and Refresh During Migration (Priority: P2)

**Goal**: Keep pagination, count semantics, and post-action compatibility behavior safe while Temporal-backed rows coexist with migration-era task surfaces.

**Independent Test**: Exercise paginated list flows, change filters mid-session, mutate one execution via action responses, and verify scope-bound tokens, exact count behavior, and stable post-action payload semantics.

### Tests for User Story 3

- [X] T026 [P] [US3] Add contract tests for opaque `nextPageToken`, scope invalidation after filter changes, and `count`/`countMode` behavior in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-013).
- [X] T027 [P] [US3] Add unit tests for no-op recency preservation and post-action state transitions in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-014).
- [X] T028 [P] [US3] Add contract tests for stable post-action compatibility payloads across signal and cancel flows in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-014, DOC-REQ-016).

### Implementation for User Story 3

- [X] T029 [US3] Bind page tokens to a scope fingerprint and expose exact count semantics in `moonmind/workflows/temporal/service.py`, `moonmind/schemas/temporal_models.py`, and `api_service/api/routers/executions.py` (DOC-REQ-013).
- [X] T030 [US3] Patch acted-on Temporal rows from successful update responses and expose a uniform row-refresh envelope for compatibility consumers that synchronously patch list/detail state in `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-014, DOC-REQ-016).
- [ ] T031 [US3] Add background refetch and stale-state affordances for mixed-source operator views in `api_service/api/routers/task_dashboard.py` and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-014, DOC-REQ-018).
- [X] T032 [US3] Surface degraded freshness and degraded-count metadata for operator-facing compatibility views in `moonmind/schemas/temporal_models.py` and follow-on UI adapters when exact totals or fresh row state cannot be maintained end-to-end (DOC-REQ-013, DOC-REQ-014, DOC-REQ-016).

**Checkpoint**: User Story 3 makes migration-era pagination and refresh semantics independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize traceability, run required validation, and enforce runtime scope gates.

- [X] T033 [P] Sync the requirement evidence to final runtime behavior in `specs/047-temporal-visibility-query/contracts/requirements-traceability.md` (DOC-REQ-018).
- [X] T034 [P] Record final runtime verification steps, filter examples, and compatibility-adapter expectations in `specs/047-temporal-visibility-query/quickstart.md` (DOC-REQ-018).
- [X] T035 [P] Run focused execution contract tests plus the repository unit validation entrypoint via `python -m pytest -q tests/contract/test_temporal_execution_api.py` and `./tools/test_unit.sh` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017, DOC-REQ-018).
- [X] T036 [P] Run full repository validation via `./tools/test_unit.sh` and resolve regressions across `tests/unit/` and dashboard runtime smoke tests included in that entrypoint (DOC-REQ-018).
- [X] T037 Execute runtime scope gates with `SPECIFY_FEATURE=047-temporal-visibility-query .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `SPECIFY_FEATURE=047-temporal-visibility-query .specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` (DOC-REQ-018).
- [X] T038 [P] Verify every `DOC-REQ-*` retains at least one implementation task and one validation task in `specs/047-temporal-visibility-query/tasks.md` and `specs/047-temporal-visibility-query/contracts/requirements-traceability.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017, DOC-REQ-018).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No prerequisites.
- **Phase 2 (Foundational)**: Depends on Phase 1 and defines the shared invariants that must be complete before final runtime closeout.
- **Phase 3 (US1)**: Builds on the shared Phase 2 query primitives and delivers the MVP canonical query contract.
- **Phase 4 (US2)**: Builds on the shared Phase 2 query primitives and the US1 query contract to preserve compatibility semantics safely.
- **Phase 5 (US3)**: Builds on the earlier query and compatibility slices because pagination and refresh handling rely on canonical payloads and compatibility-surface integration.
- **Phase 6 (Polish)**: Depends on the targeted story phases and remaining foundational hardening work being complete enough for final validation and scope-gate closure.

### User Story Dependencies

- **US1 (P1)**: First runtime slice after the shared schema/filter/status primitives are in place; remaining foundational hardening still has to close before final validation.
- **US2 (P1)**: Depends on US1 canonical identifiers and payload fields so compatibility layers reuse the same truth.
- **US3 (P2)**: Depends on US1 query semantics and US2 compatibility-adapter wiring to implement migration-safe pagination and refresh behavior.

### Within Each User Story

- Add or update story-specific tests first, verify they fail, then implement.
- Keep service and schema changes ahead of router compatibility wiring.
- Re-run the story-specific validation before moving to the next phase.

### Parallel Opportunities

- Setup tasks `T002-T004` can run in parallel after the schema direction in `T001` is clear.
- Foundational tasks `T006` and `T007` can run in parallel once `T005` defines the canonical parsing and scope rules.
- US1 validation tasks `T010-T012` can run in parallel.
- US2 validation tasks `T018-T020` can run in parallel.
- US3 validation tasks `T026-T028` can run in parallel.
- Polish tasks `T033-T036` and `T038` can run in parallel after story work stabilizes.

---

## Parallel Example: User Story 1

```bash
# Execute US1 validation tracks concurrently:
Task T010: tests/contract/test_temporal_execution_api.py
Task T011: tests/contract/test_temporal_execution_api.py
Task T012: tests/unit/workflows/temporal/test_temporal_service.py
```

## Parallel Example: User Story 2

```bash
# Execute US2 validation tracks concurrently:
Task T018: tests/contract/test_temporal_execution_api.py
Task T019: tests/unit/workflows/temporal/test_temporal_service.py + tests/contract/test_temporal_execution_api.py
Task T020: tests/unit/api/routers/test_task_dashboard_view_model.py + tests/unit/api/routers/test_task_dashboard.py
```

## Parallel Example: User Story 3

```bash
# Execute US3 validation tracks concurrently:
Task T026: tests/contract/test_temporal_execution_api.py
Task T027: tests/unit/workflows/temporal/test_temporal_service.py
Task T028: tests/contract/test_temporal_execution_api.py
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phases 1 and 2 to lock canonical query primitives.
2. Deliver Phase 3 (US1) list/detail/filter/order behavior.
3. Validate US1 independently with `T010-T012`.
4. Stop and confirm the Temporal-backed query model is stable before expanding compatibility and migration concerns.

### Incremental Delivery

1. Foundation (Phases 1-2) establishes canonical identifiers, metadata bounds, and serializer helpers.
2. Add US1 for the core Temporal Visibility query contract.
3. Add US2 to preserve task-oriented compatibility without semantic drift.
4. Add US3 for migration-safe pagination, counts, and refresh behavior.
5. Finish with Phase 6 validation, traceability, and scope gates.

### Parallel Team Strategy

1. Pair on Phases 1-2 to settle shared schemas and service invariants.
2. Split by story after foundational work:
   - Engineer A: US1 query semantics
  - Engineer B: US2 compatibility adapter hardening
  - Engineer C: US3 pagination and refresh behavior
3. Rejoin for Phase 6 validation and traceability review.

---

## Task Summary

- Total tasks: **38**
- Story task count: **US1 = 8**, **US2 = 8**, **US3 = 7**
- Parallelizable tasks (`[P]`): **19**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation target: **all tasks follow `- [ ] T### [P?] [US?] ...` with explicit file paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T004, T008, T015, T017 | T011, T012, T035 |
| DOC-REQ-002 | T005 | T010, T035 |
| DOC-REQ-003 | T005, T021 | T009, T018, T035 |
| DOC-REQ-004 | T001, T007, T014, T022 | T010, T019, T035 |
| DOC-REQ-005 | T003, T006, T013 | T009, T012, T035 |
| DOC-REQ-006 | T006, T013 | T009, T011, T035 |
| DOC-REQ-007 | T006, T013 | T009, T035 |
| DOC-REQ-008 | T005, T013, T023 | T009, T018, T035 |
| DOC-REQ-009 | T006, T014 | T012, T035 |
| DOC-REQ-010 | T004, T005, T015 | T011, T035 |
| DOC-REQ-011 | T016 | T012, T035 |
| DOC-REQ-012 | T007, T022, T025 | T009, T019, T035 |
| DOC-REQ-013 | T001, T004, T016, T029, T032 | T026, T035 |
| DOC-REQ-014 | T030, T031, T032 | T027, T028, T035 |
| DOC-REQ-015 | T001, T014, T021, T025 | T010, T035 |
| DOC-REQ-016 | T002, T024, T025, T030, T032 | T020, T028, T035 |
| DOC-REQ-017 | T003, T008, T017 | T012, T035 |
| DOC-REQ-018 | T002, T003, T024, T031 | T020, T035, T036, T037, T038 |

Coverage gate rule: each `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.

---

## Notes

- All tasks use strict checklist format: `- [ ] T### [P?] [US?] Description with file path`.
- `[US#]` labels appear only in user-story phases.
- Runtime-mode guard is satisfied via production runtime file tasks and explicit validation tasks.
- `SPECIFY_FEATURE=047-temporal-visibility-query` is required when running the repository scope scripts from the MoonMind task branch.
