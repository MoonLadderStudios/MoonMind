# Tasks: Manifest Task System Phase 1 (Worker Readiness)

**Input**: Design documents from `/specs/030-manifest-phase1/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Automated tests are required by the spec; run via `./tools/test_unit.sh`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Align shared API contracts and schema surfaces for Phase 1 worker-readiness scope.

- [ ] T001 Reconcile manifest worker request/response schemas with `specs/030-manifest-phase1/contracts/manifest-phase1.openapi.yaml` in `api_service/api/schemas.py` for `ManifestSecretResolutionRequest`, `ManifestSecretResolutionResponse`, and `ManifestStateUpdateRequest` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Ensure core router/service guardrails are in place before story-specific validation.

- [ ] T002 Implement/normalize manifest secret reference extraction and unresolved-key fail-fast handling in `api_service/api/routers/agent_queue.py` for malformed `manifestSecretRefs` payload safety (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004)
- [ ] T003 Implement/normalize `update_manifest_state(...)` persistence semantics in `api_service/services/manifests_service.py` for `state_json`, `state_updated_at`, and optional `last_run_*` updates (DOC-REQ-003, DOC-REQ-004)

**Checkpoint**: Foundational runtime behavior is ready for user-story specific endpoint completion and tests.

---

## Phase 3: User Story 1 - Resolve Manifest Secrets at Runtime (Priority: P1) 🎯 MVP

**Goal**: A manifest-capable owning worker can resolve profile/env-backed refs and receive vault metadata for running manifest jobs only.

**Independent Test**: Call `POST /api/queue/jobs/{jobId}/manifest/secrets` for a running claimed manifest job and verify resolved profile values + vault pass-through refs; verify unresolved keys return validation errors.

### Implementation for User Story 1

- [ ] T004 [US1] Finalize `POST /api/queue/jobs/{job_id}/manifest/secrets` in `api_service/api/routers/agent_queue.py` with worker-token auth, `manifest` capability checks, running-state checks, ownership checks, profile resolution, and vault pass-through shaping (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004)

### Validation for User Story 1

- [ ] T005 [US1] Extend secret-resolution coverage in `tests/unit/api/routers/test_agent_queue.py` for happy-path profile/vault resolution plus unresolved-profile and malformed-ref error behavior (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004)

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Persist Manifest Checkpoint State (Priority: P2)

**Goal**: Manifest workers can persist checkpoint state and run metadata through the registry callback endpoint.

**Independent Test**: Call `POST /api/manifests/{name}/state` with `stateJson` and `lastRun*` fields, then verify persisted updates via manifest detail fetch.

### Implementation for User Story 2

- [ ] T006 [US2] Implement/normalize `POST /api/manifests/{name}/state` route wiring and not-found handling in `api_service/api/routers/manifests.py` using `ManifestsService.update_manifest_state(...)` (DOC-REQ-003, DOC-REQ-004)
- [ ] T007 [US2] Ensure manifest state callback field mapping and timestamp update behavior in `api_service/services/manifests_service.py` aligns with `specs/030-manifest-phase1/data-model.md` (DOC-REQ-003, DOC-REQ-004)

### Validation for User Story 2

- [ ] T008 [P] [US2] Add/update service persistence tests in `tests/unit/services/test_manifests_service.py` for `state_json`, `state_updated_at`, and optional `last_run_*` updates (DOC-REQ-003, DOC-REQ-004)
- [ ] T009 [P] [US2] Add/update route tests in `tests/unit/api/routers/test_manifests.py` for successful callback persistence and 404 behavior on missing manifests (DOC-REQ-003, DOC-REQ-004)

**Checkpoint**: User Story 2 is independently functional and testable.

---

## Phase 5: User Story 3 - Enforce Worker-Only Secret Access (Priority: P3)

**Goal**: Secret resolution is inaccessible to workers without manifest capability or non-owning workers.

**Independent Test**: Attempt manifest secret resolution with missing `manifest` capability and wrong `claimed_by` ownership; both requests must fail with no secret value disclosure.

### Implementation for User Story 3

- [ ] T010 [US3] Harden denied-path responses for capability and ownership violations in `api_service/api/routers/agent_queue.py` so unauthorized callers never receive resolved secret values (DOC-REQ-001, DOC-REQ-004)

### Validation for User Story 3

- [ ] T011 [US3] Add explicit authorization regression tests in `tests/unit/api/routers/test_agent_queue.py` for missing-manifest-capability and wrong-owner secret resolution attempts (DOC-REQ-001, DOC-REQ-004)

**Checkpoint**: User Story 3 authorization guarantees are independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Execute full runtime validation and confirm scope guard compliance.

- [ ] T012 Run full unit suite via `./tools/test_unit.sh` and resolve failures in `tests/unit/api/routers/test_agent_queue.py`, `tests/unit/api/routers/test_manifests.py`, and `tests/unit/services/test_manifests_service.py` (DOC-REQ-004)
- [ ] T013 Run runtime scope gate `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime` and address any missing runtime/test changes in `api_service/` or `tests/` before handoff (DOC-REQ-004)

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 (Setup) has no prerequisites.
- Phase 2 (Foundational) depends on T001 and blocks all user story work.
- User story phases depend on Phase 2 completion.
- Phase 6 (Polish) runs after targeted story implementation and validation tasks.

### User Story Dependencies

- User Story 1 (P1) depends on T001-T003 and delivers MVP value.
- User Story 2 (P2) depends on T001 and T003.
- User Story 3 (P3) depends on User Story 1 endpoint/guard behavior (T004).

### Task-Level Order

1. T001 -> T002, T003
2. T002 -> T004 -> T005
3. T003 -> T006 -> T007
4. T007 -> T008 and T009
5. T004 -> T010 -> T011
6. T005, T008, T009, and T011 -> T012 -> T013

---

## Parallel Opportunities

- T008 and T009 can run in parallel after T007 (different test files).
- T005 and T008/T009 can run in parallel after their implementation dependencies are complete.

## Parallel Example: User Story 2

```bash
Task: "T008 [US2] Add/update service persistence tests in tests/unit/services/test_manifests_service.py"
Task: "T009 [US2] Add/update route tests in tests/unit/api/routers/test_manifests.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2 (T001-T003).
2. Complete User Story 1 (T004-T005).
3. Validate with targeted tests and proceed to full suite in T012 when ready.

### Incremental Delivery

1. Deliver US1 secret resolution flow (MVP).
2. Add US2 checkpoint persistence callback.
3. Add US3 authorization hardening.
4. Execute full regression and runtime scope gate (T012-T013).

### Traceability Coverage

- DOC-REQ-001: T002, T004, T005, T010, T011
- DOC-REQ-002: T001, T002, T004, T005
- DOC-REQ-003: T001, T003, T006, T007, T008, T009
- DOC-REQ-004: T001-T013
