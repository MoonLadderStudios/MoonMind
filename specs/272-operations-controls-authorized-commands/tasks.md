# Tasks: Operations Controls Exposed as Authorized Commands

**Input**: `specs/272-operations-controls-authorized-commands/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/system-worker-pause-api.md`, `quickstart.md`

**Prerequisites**: Constitution and README reviewed; spec and plan gates pass; setup helper scripts are absent in this checkout, so artifacts were created directly from templates.

**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

**Integration Test Command**: `./tools/test_integration.sh`

**Source Traceability**: `MM-542`; FR-001 through FR-010; SC-001 through SC-006; DESIGN-REQ-002, DESIGN-REQ-013, DESIGN-REQ-014.

## Phase 1: Setup

- [X] T001 Confirm `MM-542`, DESIGN-REQ-002, DESIGN-REQ-013, and DESIGN-REQ-014 are preserved in `specs/272-operations-controls-authorized-commands/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/system-worker-pause-api.md`, and `quickstart.md`.
- [X] T002 Create backend route/service test files `tests/unit/api/routers/test_system_operations.py` and `tests/unit/services/test_system_operations.py` for MM-542.
- [X] T003 Create integration test file `tests/integration/temporal/test_system_operations_api.py` for the `/api/system/worker-pause` route contract.

## Phase 2: Foundational

- [X] T004 Add shared worker operation request/result model expectations to `tests/unit/services/test_system_operations.py` covering FR-004, FR-008, DESIGN-REQ-013.
- [X] T005 Add route fixture setup in `tests/unit/api/routers/test_system_operations.py` with admin, non-admin, session, and fake Temporal execution service dependencies covering FR-005 and FR-006.
- [X] T006 Add integration fixture setup in `tests/integration/temporal/test_system_operations_api.py` that mounts the system operations router with fake subsystem dependencies covering SC-005 and DESIGN-REQ-014.

## Phase 3: Invoke Authorized Operations Commands

**Story Summary**: As an operator, I can invoke operational controls from Settings with current state, impact, confirmation, authorization, audit trail, and rollback or resume feedback instead of editing ordinary preferences.

**Independent Test**: Open Settings -> Operations, load worker state, submit Pause Workers with confirmation and reason, verify backend authorization/audit/subsystem invocation, then submit Resume Workers and verify running state and recent action feedback. Repeat as an unauthorized direct caller and verify no subsystem side effect.

**Traceability IDs**: FR-001 through FR-010; SC-001 through SC-006; DESIGN-REQ-002, DESIGN-REQ-013, DESIGN-REQ-014.

### Unit Test Plan

- Backend service tests cover validation, confirmation, idempotency, sanitized audit, and Temporal signal delegation.
- Backend route tests cover GET snapshot, POST pause/resume, unauthorized rejection, invalid command rejection, and subsystem failure mapping.
- UI tests cover worker control metadata, confirmation payload, audit rendering, and route errors.

### Integration Test Plan

- Hermetic API integration test verifies the configured `/api/system/worker-pause` route shape and that Settings runtime config points to the implemented route.

### Unit Tests First

- [X] T007 [P] Add failing service test for initial worker snapshot and sanitized latest audit projection in `tests/unit/services/test_system_operations.py` covering FR-001, FR-002, FR-009, SC-001.
- [X] T008 [P] Add failing service test for pause command validation requiring mode, reason, confirmation, and idempotency key in `tests/unit/services/test_system_operations.py` covering FR-003, FR-004, SC-002, DESIGN-REQ-013.
- [X] T009 [P] Add failing service test proving quiesce pause and resume delegate to fake Temporal signal methods while drain records state without direct signal broadcast in `tests/unit/services/test_system_operations.py` covering FR-007, SC-005, DESIGN-REQ-002.
- [X] T010 [P] Add failing service test for non-secret audit event persistence metadata in `tests/unit/services/test_system_operations.py` covering FR-004, FR-009, SC-004.
- [X] T011 [P] Add failing API test for GET `/api/system/worker-pause` returning system, metrics, audit, and signal status in `tests/unit/api/routers/test_system_operations.py` covering FR-001, FR-002, SC-001.
- [X] T012 [P] Add failing API test for POST pause and resume returning updated snapshots and calling subsystem methods in `tests/unit/api/routers/test_system_operations.py` covering FR-004, FR-007, DESIGN-REQ-014.
- [X] T013 [P] Add failing API test for non-admin POST rejection with no subsystem invocation in `tests/unit/api/routers/test_system_operations.py` covering FR-005, FR-006, SC-003.
- [X] T014 [P] Add failing API test for missing confirmation and invalid command values in `tests/unit/api/routers/test_system_operations.py` covering FR-003, FR-008.
- [X] T015 [P] Add failing UI test for worker control expected impact, confirmation submission fields, actor/audit rendering, and error feedback in `frontend/src/components/settings/OperationsSettingsSection.test.tsx` covering FR-002, FR-003, FR-009.

### Integration Tests First

- [X] T016 [P] Add failing `integration_ci` API contract test for configured Settings worker-pause route shape in `tests/integration/temporal/test_system_operations_api.py` covering SC-001, SC-004, DESIGN-REQ-014.

### Red-First Confirmation

- [X] T017 Run targeted backend tests and confirm they fail for missing system operations implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_system_operations.py tests/unit/api/routers/test_system_operations.py`.
- [X] T018 Run targeted UI test and confirm it fails for missing confirmation/audit expectations: `./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx`.
- [X] T019 Run targeted integration test and confirm it fails for missing route contract: `pytest tests/integration/temporal/test_system_operations_api.py -m integration_ci -q --tb=short`.

### Implementation

- [X] T020 Add system operations service models and validation in `api_service/services/system_operations.py` covering FR-003, FR-004, FR-008.
- [X] T021 Implement worker-pause snapshot state, metrics defaults, sanitized audit projection, and non-secret audit persistence in `api_service/services/system_operations.py` covering FR-001, FR-002, FR-009.
- [X] T022 Implement Temporal subsystem delegation for quiesce pause and resume in `api_service/services/system_operations.py` covering FR-007, DESIGN-REQ-002.
- [X] T023 Add FastAPI router `api_service/api/routers/system_operations.py` for GET/POST `/api/system/worker-pause` with backend authorization and sanitized errors covering FR-005, FR-006, FR-008, DESIGN-REQ-014.
- [X] T024 Register the system operations router in `api_service/main.py` covering FR-001.
- [X] T025 Update `frontend/src/components/settings/OperationsSettingsSection.tsx` to send confirmation metadata, render available actor/audit metadata, and preserve deployment operation controls covering FR-002, FR-003, FR-009.
- [X] T026 Update `api_service/api/schemas.py` only if shared worker-pause response schemas need additional optional fields while preserving existing aliases covering FR-002 and FR-008.

### Story Validation

- [X] T027 Run targeted backend tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_system_operations.py tests/unit/api/routers/test_system_operations.py`.
- [X] T028 Run targeted UI tests: `./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx`.
- [X] T029 Run targeted integration test or document environment blocker: `pytest tests/integration/temporal/test_system_operations_api.py -m integration_ci -q --tb=short`.

## Final Phase: Polish And Verification

- [X] T030 Run traceability check: `rg -n "MM-542|DESIGN-REQ-002|DESIGN-REQ-013|DESIGN-REQ-014" specs/272-operations-controls-authorized-commands`.
- [X] T031 Run full required unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T032 Run `/speckit.verify` equivalent with `moonspec-verify` against `specs/272-operations-controls-authorized-commands/spec.md`.

## Dependencies And Execution Order

1. T001-T006 establish traceability and test fixtures.
2. T007-T016 write failing tests before production code.
3. T017-T019 confirm red state.
4. T020-T026 implement service, route, registration, and UI.
5. T027-T032 validate and verify.

## Parallel Examples

- T007-T010 can run in parallel with T011-T014 because service tests and route tests are separate files.
- T015 can run in parallel with backend API tests after fixtures are understood.
- T016 can run in parallel with unit tests because it uses a separate integration file.

## Implementation Strategy

Complete the missing backend worker-pause command route using the existing deployment operations router/service pattern and existing `SettingsAuditEvent` persistence. Keep Settings as a command presentation surface, delegate quiesce/resume semantics to Temporal service methods, and preserve MM-542 traceability through final verification.
