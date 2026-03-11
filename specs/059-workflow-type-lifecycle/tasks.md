# Tasks: Workflow Type Catalog and Lifecycle

**Input**: Design documents from `/specs/046-workflow-type-lifecycle/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Validation tests are required by the feature spec (runtime implementation mode + FR-021), so each user story includes test tasks.

**Organization**: Tasks are grouped by user story so each story remains independently implementable and testable.

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present: `T001-T008`, `T013-T017`, `T021-T026`, `T030-T034`.
- Runtime validation tasks are explicitly present: `T009-T012`, `T018-T020`, `T027-T029`, `T035-T039`.
- `DOC-REQ-*` implementation + validation coverage is enforced by `T039` and the `DOC-REQ Coverage Matrix` in this file, with persistent source mapping in `specs/046-workflow-type-lifecycle/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared lifecycle/catalog primitives and persistence scaffolding.

- [X] T001 Add lifecycle policy settings for Continue-As-New and timeout/retry defaults in moonmind/config/settings.py (DOC-REQ-013, DOC-REQ-014)
- [X] T002 [P] Define canonical workflow type/state/update/signal enums and validators in moonmind/schemas/temporal_models.py (DOC-REQ-004, DOC-REQ-006, DOC-REQ-007, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011)
- [X] T003 [P] Add execution lifecycle projection fields and migration scaffold in api_service/db/models.py and api_service/migrations/versions/202603050001_temporal_execution_lifecycle.py (DOC-REQ-001, DOC-REQ-008, DOC-REQ-018)
- [X] T004 [P] Wire execution endpoint request/response schema usage in api_service/api/routers/executions.py (DOC-REQ-003, DOC-REQ-018)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared runtime invariants required by all user stories.

**⚠️ CRITICAL**: Complete this phase before starting user story implementation.

- [X] T005 Implement safe `mm:<ulid-or-uuid>` workflow ID generation/parsing helpers in moonmind/workflows/temporal/service.py (DOC-REQ-005)
- [X] T006 [P] Implement terminal close-status -> `mm_state` mapping and failure taxonomy helpers in moonmind/workflows/temporal/service.py and moonmind/schemas/temporal_models.py (DOC-REQ-007, DOC-REQ-012, DOC-REQ-015)
- [X] T007 [P] Implement shared visibility/memo projection writers (`mm_owner_id`, `mm_state`, `mm_updated_at`, `title`, `summary`) in moonmind/workflows/temporal/service.py and api_service/db/models.py (DOC-REQ-008)
- [X] T008 Implement owner/admin authorization guard utilities for mutation routes and service boundaries in api_service/api/routers/executions.py and moonmind/workflows/temporal/service.py (DOC-REQ-017)
- [X] T009 Add foundational unit coverage for ID/state/visibility/auth helpers in tests/unit/workflows/temporal/test_temporal_service.py (DOC-REQ-005, DOC-REQ-007, DOC-REQ-008, DOC-REQ-015, DOC-REQ-017)

**Checkpoint**: Core lifecycle invariants are in place; user stories can proceed.

---

## Phase 3: User Story 1 - Track Executions by Workflow Type and Lifecycle (Priority: P1) 🎯 MVP

**Goal**: Ensure each execution row is represented by one Temporal workflow execution with stable workflow type and lifecycle visibility semantics.

**Independent Test**: Start one `MoonMind.Run` and one `MoonMind.ManifestIngest`, then verify lifecycle transitions, list filters, and terminal mappings using visibility + memo fields.

### Tests for User Story 1

- [X] T010 [P] [US1] Add contract tests for create/list/describe execution projection and workflow-type categorization in tests/contract/test_temporal_execution_api.py (DOC-REQ-001, DOC-REQ-004, DOC-REQ-006, DOC-REQ-018)
- [X] T011 [P] [US1] Add contract tests for required visibility/memo fields and state/type filtering behavior in tests/contract/test_temporal_execution_api.py (DOC-REQ-002, DOC-REQ-007, DOC-REQ-008, DOC-REQ-018)
- [X] T012 [P] [US1] Add unit tests for lifecycle state transitions and terminal close-status mapping in tests/unit/workflows/temporal/test_temporal_service.py (DOC-REQ-007, DOC-REQ-016)

### Implementation for User Story 1

- [X] T013 [US1] Enforce v1 workflow catalog (`MoonMind.Run`, `MoonMind.ManifestIngest`) and entry mapping in moonmind/schemas/temporal_models.py and moonmind/workflows/temporal/service.py (DOC-REQ-004, DOC-REQ-006)
- [X] T014 [US1] Implement execution start flow with `mm:<id>` workflow IDs and initial `mm_state=initializing` behavior in moonmind/workflows/temporal/service.py (DOC-REQ-003, DOC-REQ-005, DOC-REQ-007)
- [X] T015 [US1] Implement list/detail reads backed by visibility and memo projection fields in api_service/api/routers/executions.py and moonmind/workflows/temporal/service.py (DOC-REQ-001, DOC-REQ-002, DOC-REQ-008, DOC-REQ-018)
- [X] T016 [US1] Implement documented lifecycle transitions for `MoonMind.Run` and `MoonMind.ManifestIngest` paths in moonmind/workflows/temporal/service.py (DOC-REQ-016)
- [X] T017 [US1] Enforce artifact-reference-first handling for large input/manifest payloads in moonmind/workflows/temporal/service.py (DOC-REQ-002, DOC-REQ-008)

**Checkpoint**: User Story 1 is independently testable as MVP.

---

## Phase 4: User Story 2 - Control Running Executions via Updates, Signals, and Cancel (Priority: P1)

**Goal**: Provide safe owner/admin mutation controls via update/signal/cancel contracts with invariant enforcement.

**Independent Test**: Execute `UpdateInputs`, `SetTitle`, `RequestRerun`, `ExternalEvent`, `Approve`, and cancel flows for both authorized and unauthorized actors.

### Tests for User Story 2

- [X] T018 [P] [US2] Add unit tests for `UpdateInputs`/`SetTitle`/`RequestRerun` idempotency and apply-mode behavior in tests/unit/workflows/temporal/test_temporal_service.py (DOC-REQ-009, DOC-REQ-010)
- [X] T019 [P] [US2] Add unit tests for signal payload validation, async acceptance, and terminal-state rejection in tests/unit/workflows/temporal/test_temporal_service.py (DOC-REQ-011, DOC-REQ-017)
- [X] T020 [P] [US2] Add contract tests for update/signal/cancel authorization and invariant rejection paths in tests/contract/test_temporal_execution_api.py (DOC-REQ-003, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-017, DOC-REQ-018)

### Implementation for User Story 2

- [X] T021 [US2] Implement `UpdateInputs`, `SetTitle`, and idempotency-key replay response envelope in moonmind/workflows/temporal/service.py and moonmind/schemas/temporal_models.py (DOC-REQ-003, DOC-REQ-009, DOC-REQ-010)
- [X] T022 [US2] Implement `RequestRerun` Continue-As-New path that preserves Workflow ID in moonmind/workflows/temporal/service.py (DOC-REQ-005, DOC-REQ-010, DOC-REQ-013)
- [X] T023 [US2] Implement `ExternalEvent`/`Approve` and optional `Pause`/`Resume` signal handlers in moonmind/workflows/temporal/service.py and moonmind/schemas/temporal_models.py (DOC-REQ-011)
- [X] T024 [US2] Integrate activity-side authenticity verification hooks for external signals in moonmind/workflows/temporal/service.py and moonmind/workflows/skills/tool_dispatcher.py (DOC-REQ-011, DOC-REQ-017)
- [X] T025 [US2] Implement graceful cancel and forced-termination handling with summary reason capture in api_service/api/routers/executions.py and moonmind/workflows/temporal/service.py (DOC-REQ-012)
- [X] T026 [US2] Enforce owner/admin controls at API and runtime boundaries for update/signal/cancel/rerun paths in api_service/api/routers/executions.py and moonmind/workflows/temporal/service.py (DOC-REQ-017, DOC-REQ-018)

**Checkpoint**: User Story 2 controls are independently testable with auth/invariant coverage.

---

## Phase 5: User Story 3 - Ensure Runtime Robustness and History Safety (Priority: P2)

**Goal**: Guarantee predictable long-run behavior via Continue-As-New thresholds, timeout/retry policies, and normalized error outcomes.

**Independent Test**: Drive long-running and failure scenarios to verify policy-triggered Continue-As-New, bounded monitoring behavior, and error category summaries.

### Tests for User Story 3

- [X] T027 [P] [US3] Add unit tests for Continue-As-New threshold triggers preserving workflow identity and references in tests/unit/workflows/temporal/test_temporal_service.py (DOC-REQ-013)
- [X] T028 [P] [US3] Add unit tests for error-category mapping and failed-summary memo output in tests/unit/workflows/temporal/test_temporal_service.py (DOC-REQ-015)
- [X] T029 [P] [US3] Add integration tests for timeout/retry defaults and callback-first bounded polling fallback in tests/integration/temporal/test_compose_foundation.py (DOC-REQ-014)

### Implementation for User Story 3

- [X] T030 [US3] Implement policy-driven Continue-As-New thresholds and trigger checks in moonmind/workflows/temporal/service.py and moonmind/config/settings.py (DOC-REQ-013, DOC-REQ-014)
- [X] T031 [US3] Implement explicit workflow/activity timeout-retry defaults and bounded polling backoff behavior in moonmind/workflows/temporal/service.py (DOC-REQ-014)
- [X] T032 [US3] Persist normalized failure taxonomy (`user_error|integration_error|execution_error|system_error`) into lifecycle summaries in moonmind/workflows/temporal/service.py and moonmind/schemas/temporal_models.py (DOC-REQ-015)
- [X] T033 [US3] Keep large external/input payloads as artifact references during long-running execution paths in moonmind/workflows/temporal/service.py and moonmind/workflows/skills/runner.py (DOC-REQ-002, DOC-REQ-013, DOC-REQ-014)
- [X] T034 [US3] Harden run/manifest lifecycle guards around `awaiting_external` and `finalizing` transitions in moonmind/workflows/temporal/service.py (DOC-REQ-016)

**Checkpoint**: User Story 3 robustness behaviors are independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cross-story hardening.

- [X] T035 [P] Add projection/migration compatibility checks for lifecycle fields in tests/contract/test_temporal_execution_api.py and api_service/migrations/versions/202603050001_temporal_execution_lifecycle.py (DOC-REQ-001, DOC-REQ-008, DOC-REQ-018)
- [X] T036 [P] Run repository unit suite via ./tools/test_unit.sh and address regressions in tests/unit/workflows/temporal/test_temporal_service.py (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017, DOC-REQ-018)
- [X] T037 [P] Run contract API validation for lifecycle endpoints in tests/contract/test_temporal_execution_api.py (DOC-REQ-001, DOC-REQ-003, DOC-REQ-006, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-017, DOC-REQ-018)
- [X] T038 [P] Run temporal integration lifecycle validation in tests/integration/temporal/test_compose_foundation.py (DOC-REQ-013, DOC-REQ-014, DOC-REQ-016)
- [X] T039 Run runtime diff scope gate command from .specify/scripts/bash/validate-implementation-scope.sh (DOC-REQ-018)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies; start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2; delivers MVP lifecycle catalog/list behavior.
- **Phase 4 (US2)**: Depends on Phase 2 and should follow US1 for safer contract extension.
- **Phase 5 (US3)**: Depends on Phases 2-4 because robustness policies rely on established lifecycle/update flows.
- **Phase 6 (Polish)**: Depends on completion of desired user stories.

### User Story Dependencies

- **US1 (P1)**: No dependency on other stories after foundational work.
- **US2 (P1)**: Depends on US1 lifecycle surfaces (`workflowId`, `mm_state`, visibility/memo projection).
- **US3 (P2)**: Depends on US1/US2 runtime flows to validate long-run behavior and failure semantics.

### Within Each User Story

- Tests first (where listed), then implementation.
- Schema/model changes before router wiring.
- Service logic before integration validations.

### Parallel Opportunities

- Phase 1 tasks marked `[P]` can run in parallel.
- Phase 2 tasks `T006` and `T007` can run in parallel after `T005` starts.
- In each user story, tasks marked `[P]` can run in parallel.
- Contract and unit test authoring can proceed in parallel with non-overlapping files.

---

## Parallel Example: User Story 1

```bash
# Parallel test authoring for US1
Task: T010 tests/contract/test_temporal_execution_api.py
Task: T011 tests/contract/test_temporal_execution_api.py
Task: T012 tests/unit/workflows/temporal/test_temporal_service.py

# Parallel implementation slices for US1
Task: T013 moonmind/schemas/temporal_models.py + moonmind/workflows/temporal/service.py
Task: T015 api_service/api/routers/executions.py + moonmind/workflows/temporal/service.py
```

## Parallel Example: User Story 2

```bash
# Parallel tests for US2
Task: T018 tests/unit/workflows/temporal/test_temporal_service.py
Task: T019 tests/unit/workflows/temporal/test_temporal_service.py
Task: T020 tests/contract/test_temporal_execution_api.py

# Parallel implementation slices for US2
Task: T023 moonmind/workflows/temporal/service.py + moonmind/schemas/temporal_models.py
Task: T025 api_service/api/routers/executions.py + moonmind/workflows/temporal/service.py
```

## Parallel Example: User Story 3

```bash
# Parallel tests for US3
Task: T027 tests/unit/workflows/temporal/test_temporal_service.py
Task: T028 tests/unit/workflows/temporal/test_temporal_service.py
Task: T029 tests/integration/temporal/test_compose_foundation.py

# Parallel implementation slices for US3
Task: T030 moonmind/workflows/temporal/service.py + moonmind/config/settings.py
Task: T032 moonmind/workflows/temporal/service.py + moonmind/schemas/temporal_models.py
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1).
3. Validate create/list/describe lifecycle behavior and terminal mappings.
4. Demo/deploy MVP execution visibility catalog behavior.

### Incremental Delivery

1. Deliver US1 for foundational execution visibility.
2. Add US2 control-plane updates/signals/cancel semantics.
3. Add US3 robustness and history-safety controls.
4. Run full unit/contract/integration validation before merge.

### Parallel Team Strategy

1. Pair on Phase 1-2 to lock shared invariants.
2. Split by story after foundational completion:
   - Engineer A: US1
   - Engineer B: US2
   - Engineer C: US3
3. Rejoin for Phase 6 hardening and full validation pass.

---

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T003, T015 | T010, T035, T037 |
| DOC-REQ-002 | T015, T017, T033 | T011, T036 |
| DOC-REQ-003 | T004, T014, T021 | T020, T037 |
| DOC-REQ-004 | T002, T013 | T010, T036 |
| DOC-REQ-005 | T005, T014, T022 | T009, T036 |
| DOC-REQ-006 | T002, T013 | T010, T037 |
| DOC-REQ-007 | T002, T006, T014 | T009, T011, T012 |
| DOC-REQ-008 | T003, T007, T015, T017 | T009, T011, T035 |
| DOC-REQ-009 | T002, T021 | T018, T020, T037 |
| DOC-REQ-010 | T002, T021, T022 | T018, T020, T037 |
| DOC-REQ-011 | T002, T023, T024 | T019, T020, T037 |
| DOC-REQ-012 | T006, T025 | T020, T037 |
| DOC-REQ-013 | T001, T022, T030, T033 | T027, T036, T038 |
| DOC-REQ-014 | T001, T030, T031, T033 | T029, T036, T038 |
| DOC-REQ-015 | T006, T032 | T009, T028, T036 |
| DOC-REQ-016 | T016, T034 | T012, T038 |
| DOC-REQ-017 | T008, T024, T026 | T009, T019, T020, T037 |
| DOC-REQ-018 | T003, T004, T015, T026 | T010, T011, T020, T035, T037, T039 |

Coverage gate rule: each `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.

---

## Notes

- All tasks use strict checklist format: `- [ ] T### [P?] [US?] Description with file path`.
- `[US#]` labels appear only in user-story phases.
- Runtime-mode guard is satisfied via production runtime file tasks and explicit validation tasks.
- `DOC-REQ-001` through `DOC-REQ-018` are carried into implementation and validation tasks for traceability coverage.
