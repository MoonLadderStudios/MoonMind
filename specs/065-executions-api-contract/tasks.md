# Tasks: Executions API Contract Runtime Delivery

**Input**: Design documents from `/specs/048-executions-api-contract/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required. This feature explicitly requires automated validation coverage for contract, router, service, compatibility, and DOC-REQ traceability behavior, plus repository-standard execution via `./tools/test_unit.sh`.

**Organization**: Tasks are grouped by user story so each slice can be implemented and validated independently while preserving runtime-mode scope.

## Format: `[ID] [P?] [Story] Description`

- [ ]: Required checkbox prefix
- `T###`: Sequential task ID
- `[P]`: Task can run in parallel (different files, no dependency on incomplete tasks)
- `[US#]`: Required for user-story phase tasks only
- Each task description includes one or more concrete file paths

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Reconfirm the runtime-mode implementation boundary, validation entrypoints, and contract artifacts before code changes begin.

- [X] T001 Reconfirm runtime-mode delivery scope, implementation surfaces, and validation commands in `specs/048-executions-api-contract/plan.md` and `specs/048-executions-api-contract/quickstart.md`.
- [X] T002 [P] Reconcile the six-route execution contract surface, schema names, and status-code expectations in `specs/048-executions-api-contract/contracts/executions-api-contract.openapi.yaml` and `specs/048-executions-api-contract/spec.md`.
- [X] T003 [P] Refresh `DOC-REQ-001` through `DOC-REQ-015` implementation and validation mappings in `specs/048-executions-api-contract/contracts/requirements-traceability.md`, `specs/048-executions-api-contract/quickstart.md`, and `tests/unit/specs/test_doc_req_traceability.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared persistence, schema, router, and service invariants that block all execution API stories.

**⚠️ CRITICAL**: No user story work should start until this phase is complete.

- [X] T004 Reconcile execution lifecycle persistence, idempotency, and metadata columns/indexes in `api_service/migrations/versions/202603050001_temporal_execution_lifecycle.py` and `api_service/db/models.py` (DOC-REQ-004, DOC-REQ-006, DOC-REQ-007, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-015).
- [X] T005 [P] Harden shared execution request/response schemas, camelCase aliases, and domain error envelopes in `moonmind/schemas/temporal_models.py` (DOC-REQ-002, DOC-REQ-005, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013).
- [X] T006 [P] Implement shared router ownership, auth, and domain-error helper behavior for `/api/executions` in `api_service/api/routers/executions.py` and `api_service/main.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-013).
- [X] T007 Implement shared execution service serialization, baseline metadata, opaque pagination-token handling, and adapter-boundary helpers in `moonmind/workflows/temporal/service.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-008, DOC-REQ-014, DOC-REQ-015).

**Checkpoint**: Foundation complete. User stories can now proceed in priority order.

---

## Phase 3: User Story 1 - Start and Inspect Owned Executions (Priority: P1) 🎯 MVP

**Goal**: Deliver create, list, and describe behavior with stable execution identity, owner scoping, and contract-compliant payloads.

**Independent Test**: Create both supported workflow types, then verify create/list/describe responses expose the documented fields, initialize lifecycle metadata, enforce owner scoping, and keep `workflowId` as the durable identifier.

### Tests for User Story 1

- [X] T008 [P] [US1] Extend create/list/describe contract coverage for supported workflow types, payload fields, pagination, and count semantics in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-015).
- [X] T009 [P] [US1] Extend router tests for owner-scope enforcement, hidden describe behavior, malformed `nextPageToken`, `pageSize` bounds, and domain validation envelopes in `tests/unit/api/routers/test_executions.py` (DOC-REQ-003, DOC-REQ-008, DOC-REQ-009, DOC-REQ-013, DOC-REQ-015).
- [X] T010 [P] [US1] Extend service tests for create validation, owner/type/idempotency deduplication, baseline metadata initialization, and list ordering in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-004, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-015).

### Implementation for User Story 1

- [X] T011 [US1] Implement create-request validation, owner-aware idempotent create flow, and initialization defaults in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` (DOC-REQ-006, DOC-REQ-007, DOC-REQ-015).
- [X] T012 [US1] Implement create/list/describe router behavior with authenticated ownership enforcement and non-disclosing `execution_not_found` responses in `api_service/api/routers/executions.py` and `api_service/main.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-009, DOC-REQ-013).
- [X] T013 [US1] Implement list filtering, deterministic ordering, opaque `nextPageToken`, and `count`/`countMode` handling in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-005, DOC-REQ-008).
- [X] T014 [US1] Align create/list/describe serialization to canonical `workflowId`, non-durable `runId`, camelCase JSON, and no direct `taskId` exposure in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` (DOC-REQ-002, DOC-REQ-005).

**Checkpoint**: US1 is complete and independently testable as the MVP slice.

---

## Phase 4: User Story 2 - Control a Running Execution Safely (Priority: P1)

**Goal**: Deliver contract-compliant update, signal, rerun, and cancel behavior with predictable lifecycle transitions and terminal-state handling.

**Independent Test**: Against a running execution, exercise `UpdateInputs`, `SetTitle`, `RequestRerun`, `ExternalEvent`, `Approve`, `Pause`, `Resume`, graceful cancel, and forced termination; verify accepted/rejected responses, lifecycle effects, terminal behavior, and ownership enforcement.

### Tests for User Story 2

- [X] T015 [P] [US2] Extend router tests for update/signal/cancel authorization, hidden control behavior, invalid update names, and invalid signal payloads in `tests/unit/api/routers/test_executions.py` (DOC-REQ-003, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-015).
- [X] T016 [P] [US2] Extend service tests for `UpdateInputs`, `SetTitle`, `RequestRerun`, latest-key idempotency replay, signal lifecycle effects, and graceful versus forced cancel behavior in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-004, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-015).
- [X] T017 [P] [US2] Extend contract tests for update/signal/cancel success statuses, terminal responses, and lifecycle response bodies in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-015).

### Implementation for User Story 2

- [X] T018 [US2] Implement `UpdateInputs`, `SetTitle`, and `RequestRerun` request handling, `accepted`/`applied`/`message` responses, and most-recent idempotency replay in `moonmind/workflows/temporal/service.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-010).
- [X] T019 [US2] Implement `ExternalEvent`, `Approve`, `Pause`, and `Resume` validation plus lifecycle transitions in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-011, DOC-REQ-013).
- [X] T020 [US2] Implement graceful cancel, forced termination, and unchanged terminal cancel returns in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-012, DOC-REQ-013).
- [X] T021 [US2] Preserve continue-as-new/rerun invariants by keeping `workflowId` stable, rotating `runId`, and mapping `closeStatus` to `temporalStatus` in `moonmind/workflows/temporal/service.py`, `api_service/db/models.py`, and `moonmind/schemas/temporal_models.py` (DOC-REQ-002, DOC-REQ-004, DOC-REQ-010).

**Checkpoint**: US2 is complete with lifecycle mutation behavior that remains predictable and testable.

---

## Phase 5: User Story 3 - Integrate During Migration Without Contract Drift (Priority: P2)

**Goal**: Preserve a stable execution-shaped API while compatibility adapters keep task-oriented surfaces functional during migration.

**Independent Test**: Verify response shapes and identifiers remain stable for execution API consumers while compatibility adapters continue mapping execution records into task-oriented views using `taskId == workflowId`.

### Tests for User Story 3

- [X] T022 [P] [US3] Extend compatibility-adapter tests for execution-backed task view models, status mapping, and `taskId == workflowId` bridging in `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-002, DOC-REQ-014, DOC-REQ-015).
- [X] T023 [P] [US3] Extend contract coverage for stable execution JSON and future-compatible non-authoritative `countMode` handling in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-008, DOC-REQ-014, DOC-REQ-015).

### Implementation for User Story 3

- [X] T024 [US3] Update task/dashboard execution adapters to preserve `taskId == workflowId` outside `/api/executions` in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-002, DOC-REQ-014).
- [X] T025 [US3] Harden the `/api/executions` adapter boundary so execution responses remain stable across backing-read changes in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-001, DOC-REQ-014).
- [X] T026 [US3] Align migration-validation guidance and compatibility notes with implemented adapter behavior in `specs/048-executions-api-contract/quickstart.md` and `specs/048-executions-api-contract/contracts/requirements-traceability.md` (DOC-REQ-014, DOC-REQ-015).

**Checkpoint**: US3 is complete with migration-safe execution responses and explicit compatibility guarantees.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize traceability, run required validation, and enforce runtime implementation gates.

- [X] T027 [P] Reconcile final implementation and validation evidence for `DOC-REQ-001` through `DOC-REQ-015` in `specs/048-executions-api-contract/contracts/requirements-traceability.md`, `specs/048-executions-api-contract/quickstart.md`, and `tests/unit/specs/test_doc_req_traceability.py`.
- [X] T028 [P] Run `./tools/test_unit.sh` and record the expected validation command/results in `specs/048-executions-api-contract/quickstart.md` (DOC-REQ-015).
- [X] T029 [P] Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` and capture runtime-scope verification notes in `specs/048-executions-api-contract/quickstart.md` and `specs/048-executions-api-contract/contracts/requirements-traceability.md` (DOC-REQ-015).
- [X] T030 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and keep `specs/048-executions-api-contract/tasks.md` aligned with runtime-mode coverage requirements (DOC-REQ-015).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no prerequisites.
- Phase 2 depends on Phase 1 and blocks all user-story work.
- Phase 3 (US1) depends on Phase 2.
- Phase 4 (US2) depends on Phase 2 and should start after US1 create/list/describe foundations are stable.
- Phase 5 (US3) depends on Phase 3 and benefits from Phase 4 lifecycle serialization stability.
- Phase 6 depends on all story phases.

### User Story Dependencies

- **US1 (P1)**: Depends only on foundational execution persistence, schema, router, and service invariants.
- **US2 (P1)**: Depends on US1 identity/serialization behavior and shared ownership enforcement.
- **US3 (P2)**: Depends on US1 execution response stability and should verify compatibility after US2 lifecycle semantics are settled.

### Within Each User Story

- Add or extend tests first and confirm the missing behavior is not yet covered.
- Service and schema changes should land before router compatibility cleanup.
- Finish the story’s runtime behavior before updating traceability or quickstart notes that describe the final behavior.

## Parallel Opportunities

- Phase 1 tasks `T002` and `T003` can run in parallel.
- In Phase 2, `T005` and `T006` can run in parallel after persistence scope in `T004` is understood.
- In US1, tests `T008` through `T010` can run in parallel; `T013` and `T014` can overlap once `T011` establishes create/list behavior.
- In US2, tests `T015` through `T017` can run in parallel; `T019` and `T020` can proceed in parallel after `T018` establishes update/control scaffolding.
- In US3, `T022` and `T023` can run in parallel; `T024` and `T025` can proceed together once compatibility boundaries are agreed.
- In Phase 6, `T027` through `T029` can run in parallel after implementation is complete; `T030` is the final gate.

## Parallel Example: User Story 1

```bash
Task: "T008 [US1] Extend create/list/describe contract coverage in tests/contract/test_temporal_execution_api.py"
Task: "T009 [US1] Extend router tests in tests/unit/api/routers/test_executions.py"
Task: "T010 [US1] Extend service tests in tests/unit/workflows/temporal/test_temporal_service.py"
```

## Parallel Example: User Story 2

```bash
Task: "T015 [US2] Extend router control-route tests in tests/unit/api/routers/test_executions.py"
Task: "T016 [US2] Extend service lifecycle tests in tests/unit/workflows/temporal/test_temporal_service.py"
Task: "T017 [US2] Extend contract control-route coverage in tests/contract/test_temporal_execution_api.py"
```

## Parallel Example: User Story 3

```bash
Task: "T022 [US3] Extend compatibility-adapter tests in tests/unit/api/routers/test_task_dashboard_view_model.py"
Task: "T024 [US3] Update task/dashboard adapters in api_service/api/routers/task_dashboard_view_model.py and api_service/static/task_dashboard/dashboard.js"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete US1 (`T008` through `T014`).
3. Validate create/list/describe behavior independently.
4. Stop and confirm the execution identity, metadata, and owner-scope contract is stable before moving to controls.

### Incremental Delivery

1. Deliver US1 to establish the stable execution API read/create surface.
2. Deliver US2 to add update, signal, rerun, and cancel lifecycle control behavior.
3. Deliver US3 to preserve migration compatibility and contract stability for task-oriented consumers.
4. Finish with Phase 6 validation and runtime-scope gates.

## Task Summary & Validation

- Total tasks: **30**
- User story task counts:
  - **US1**: 7 tasks (`T008` through `T014`)
  - **US2**: 7 tasks (`T015` through `T021`)
  - **US3**: 5 tasks (`T022` through `T026`)
- Parallel opportunities: Present in every phase and called out above.
- Independent test criteria: Defined per user story and aligned to the feature spec.
- Suggested MVP scope: Phase 1 + Phase 2 + US1.
- Checklist format validation: All tasks follow `- [ ] T### [P?] [US?] Description with file path` format.
