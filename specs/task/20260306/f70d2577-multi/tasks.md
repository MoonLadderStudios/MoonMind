# Tasks: Integrations Monitoring Design

**Input**: Design documents from `/specs/047-integrations-monitoring/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Automated validation is required for this feature because runtime implementation mode and `FR-030` require unit, contract, and Temporal integration coverage.

**Organization**: Tasks are grouped by user story so each story remains independently implementable and testable.

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present: `T001-T008`, `T013-T016`, `T021-T024`, `T029-T032`.
- Runtime validation tasks are explicitly present: `T009-T012`, `T017-T020`, `T025-T028`, `T034-T035`.
- `DOC-REQ-*` implementation + validation coverage is enforced by `T033` and the `DOC-REQ Coverage Matrix` in this file, with persistent source mapping in `specs/047-integrations-monitoring/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared monitoring configuration, schemas, and persistence scaffolding.

- [X] T001 Add integrations monitoring configuration defaults and provider guardrails in `moonmind/config/settings.py` and `moonmind/config/jules_settings.py` (DOC-REQ-011, DOC-REQ-013, DOC-REQ-015)
- [X] T002 [P] Define normalized monitoring schemas, `ExternalEvent` payloads, and provider contract models in `moonmind/schemas/temporal_models.py` and `moonmind/schemas/jules_models.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-009, DOC-REQ-010, DOC-REQ-015)
- [X] T003 [P] Add integration correlation persistence fields and migration scaffold in `api_service/db/models.py` and `api_service/migrations/versions/202603060001_integrations_monitoring.py` (DOC-REQ-004, DOC-REQ-008, DOC-REQ-011)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the shared provider-neutral monitoring primitives required by all user stories.

**⚠️ CRITICAL**: Complete this phase before user story implementation so the documented order stays intact.

- [X] T004 Implement provider-neutral `integration.<provider>.start|status|fetch_result|cancel` helpers and Jules status normalization in `moonmind/workflows/adapters/jules_client.py`, `moonmind/schemas/jules_models.py`, and `moonmind/jules/runtime.py` (DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-015, DOC-REQ-016)
- [X] T005 [P] Implement artifact-backed callback/result/failure payload helpers and redaction guards in `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-003, DOC-REQ-006, DOC-REQ-012, DOC-REQ-013)
- [X] T006 Implement durable correlation lookup/write/update helpers for active monitoring and Continue-As-New in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` (DOC-REQ-003, DOC-REQ-008, DOC-REQ-011, DOC-REQ-016)
- [X] T007 Implement compact `integration_state`, visibility/memo projection, and terminal latch primitives in `moonmind/workflows/temporal/service.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-007, DOC-REQ-009, DOC-REQ-010)
- [X] T008 Implement callback verification, request-size limits, and router registration plumbing in `api_service/api/routers/execution_integrations.py`, `api_service/api/routers/__init__.py`, and `api_service/main.py` (DOC-REQ-003, DOC-REQ-008, DOC-REQ-013)

**Checkpoint**: Provider contracts, correlation plumbing, artifact boundaries, and compact workflow state are ready for story work.

---

## Phase 3: User Story 1 - Monitor External Work Inside a Run (Priority: P1) 🎯 MVP

**Goal**: Let `MoonMind.Run` enter durable external monitoring, resume through callback or polling, and complete with compact state plus artifact-backed results.

**Independent Test**: Start a monitored run, verify it enters `awaiting_external`, complete one run by callback and another by polling fallback, and confirm both resume through one terminal completion path with result artifacts.

### Tests for User Story 1

- [X] T009 [P] [US1] Add contract tests for `POST /api/executions/{workflowId}/integration` and `POST /api/executions/{workflowId}/integration/poll` in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-001, DOC-REQ-007, DOC-REQ-010)
- [X] T010 [P] [US1] Add unit tests for compact monitoring state transitions, stable correlation IDs, and artifact-backed result refs in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-007)
- [X] T011 [P] [US1] Add unit tests for Jules start/status/fetch-result idempotency and normalized status mapping in `tests/unit/workflows/adapters/test_jules_client.py` (DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-015)
- [X] T012 [P] [US1] Add Temporal integration coverage for callback-first completion and polling fallback in `tests/integration/temporal/test_integrations_monitoring.py` (DOC-REQ-007, DOC-REQ-011, DOC-REQ-014, DOC-REQ-015)

### Implementation for User Story 1

- [X] T013 [US1] Implement configure-monitoring start flow that moves `MoonMind.Run` into `awaiting_external` in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-007)
- [X] T014 [US1] Implement provider-neutral start/status/fetch-result orchestration on the shared integrations queue in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/adapters/jules_client.py` (DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-011, DOC-REQ-016)
- [X] T015 [US1] Implement bounded polling cadence, jitter reset, and successful result-fetch continuation in `moonmind/workflows/temporal/service.py` and `moonmind/config/settings.py` (DOC-REQ-007, DOC-REQ-011, DOC-REQ-014)
- [X] T016 [US1] Implement execution integration projection responses and compact memo/search-attribute updates in `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-004, DOC-REQ-010)

**Checkpoint**: User Story 1 is independently testable as the MVP monitoring flow.

---

## Phase 4: User Story 2 - Correlate and Secure External Callbacks (Priority: P1)

**Goal**: Accept only valid callbacks, resolve them durably to the right execution, and keep duplicate or out-of-order delivery harmless.

**Independent Test**: Send valid, duplicate, reordered, oversized, and invalid callbacks to an active monitored run and confirm validation, durable correlation lookup, dedupe, and safe rejection behavior.

### Tests for User Story 2

- [X] T017 [P] [US2] Add router tests for callback signature/auth validation, request-size rejection, and malformed payload handling in `tests/unit/api/routers/test_execution_integrations.py` (DOC-REQ-008, DOC-REQ-013)
- [X] T018 [P] [US2] Add unit tests for `ExternalEvent` dedupe, replay safety, duplicate delivery, and late out-of-order callbacks in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-007, DOC-REQ-009)
- [X] T019 [P] [US2] Add contract tests for callback correlation and execution update responses in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-008, DOC-REQ-010, DOC-REQ-013)
- [X] T020 [P] [US2] Add Temporal integration coverage for valid, duplicate, reordered, and invalid callbacks across Continue-As-New in `tests/integration/temporal/test_integrations_monitoring.py` (DOC-REQ-008, DOC-REQ-009, DOC-REQ-011, DOC-REQ-014)

### Implementation for User Story 2

- [X] T021 [US2] Implement callback ingress verification, raw payload artifact capture, and compact `ExternalEvent` signaling in `api_service/api/routers/execution_integrations.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-003, DOC-REQ-008, DOC-REQ-009, DOC-REQ-013, DOC-REQ-016)
- [X] T022 [US2] Implement durable correlation lifecycle writes, callback lookup, and `run_id` refresh on Continue-As-New in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` (DOC-REQ-003, DOC-REQ-008, DOC-REQ-011, DOC-REQ-016)
- [X] T023 [US2] Implement bounded provider-event dedupe, terminal-state latching, and late-event ignore rules in `moonmind/workflows/temporal/service.py` and `moonmind/schemas/temporal_models.py` (DOC-REQ-007, DOC-REQ-009)
- [X] T024 [US2] Register callback routes and surface correlated integration updates through `api_service/main.py`, `api_service/api/routers/__init__.py`, and `api_service/api/routers/executions.py` (DOC-REQ-008, DOC-REQ-010)

**Checkpoint**: User Story 2 is independently testable with callback security and idempotency guarantees.

---

## Phase 5: User Story 3 - Operate Provider Monitoring Safely at Runtime (Priority: P2)

**Goal**: Keep long-lived monitoring bounded and operator-safe with Continue-As-New preservation, explicit cancel/failure outcomes, compact visibility, and a Jules-first portable provider profile.

**Independent Test**: Drive long waits, missed callbacks, cancellation, terminal provider failures, and Jules-specific terminal states while verifying visibility fields, summaries, and preserved monitoring identity.

### Tests for User Story 3

- [X] T025 [P] [US3] Add unit tests for polling backoff resets, wait-cycle thresholds, and Continue-As-New monitoring preservation in `tests/unit/workflows/temporal/test_temporal_service.py` (DOC-REQ-002, DOC-REQ-011, DOC-REQ-014)
- [X] T026 [P] [US3] Add unit tests for provider failure summaries, cancel outcomes, and redacted artifact handling in `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/workflows/temporal/test_artifacts.py` (DOC-REQ-006, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014)
- [X] T027 [P] [US3] Add contract tests for monitored execution visibility/memo fields and cancel responses in `tests/contract/test_temporal_execution_api.py` (DOC-REQ-010, DOC-REQ-012)
- [X] T028 [P] [US3] Add Temporal integration failure-path coverage for missed callbacks, cancellation, provider failures, and Jules terminal states in `tests/integration/temporal/test_integrations_monitoring.py` (DOC-REQ-011, DOC-REQ-012, DOC-REQ-014, DOC-REQ-015)

### Implementation for User Story 3

- [X] T029 [US3] Implement Continue-As-New monitoring preservation and bounded wait-cycle policy in `moonmind/workflows/temporal/service.py`, `moonmind/config/settings.py`, and `api_service/db/models.py` (DOC-REQ-011, DOC-REQ-014)
- [X] T030 [US3] Implement explicit provider failure summary and cancel-outcome handling in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-006, DOC-REQ-012, DOC-REQ-014)
- [X] T031 [US3] Implement compact `mm_*` visibility/memo updates for active and terminal integration states in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-010, DOC-REQ-016)
- [X] T032 [US3] Harden the Jules-first provider profile, minimal shared routing, and configurable rate-limit behavior in `moonmind/workflows/adapters/jules_client.py`, `moonmind/jules/runtime.py`, and `moonmind/config/jules_settings.py` (DOC-REQ-002, DOC-REQ-011, DOC-REQ-015, DOC-REQ-016)

**Checkpoint**: User Story 3 is independently testable with bounded history growth, explicit operator outcomes, and Jules portability.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability, regression validation, and runtime scope enforcement.

- [X] T033 [P] Update `specs/047-integrations-monitoring/contracts/requirements-traceability.md` with final implementation and validation evidence links for `DOC-REQ-001` through `DOC-REQ-016` (DOC-REQ-016)
- [X] T034 [P] Run repository validation via `./tools/test_unit.sh` covering `tests/unit/`, `tests/contract/`, and `tests/integration/temporal/test_integrations_monitoring.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015)
- [X] T035 Run runtime scope gates `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` after implementation changes are present (DOC-REQ-016)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies; start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers the MVP monitored-run flow.
- **Phase 4 (US2)**: Depends on Phases 1-3 because callback security builds on the active monitoring flow and correlation primitives.
- **Phase 5 (US3)**: Depends on Phases 1-4 because long-wait, cancellation, and provider-hardening logic rely on established monitoring and callback behavior.
- **Phase 6 (Polish)**: Depends on completion of the desired user stories.

### User Story Dependencies

- **US1 (P1)**: Independent after foundational work and is the suggested MVP.
- **US2 (P1)**: Depends on US1 configuring active monitoring and correlation records.
- **US3 (P2)**: Depends on US1 and US2 runtime flows so long-lived and failure-path behavior can be validated realistically.

### Within Each User Story

- Write the listed validation tasks first and confirm they fail before implementation.
- Complete schema/config/model updates before service orchestration changes.
- Finish service/runtime behavior before router projection and end-to-end validation reruns.

### Parallel Opportunities

- Setup tasks `T002` and `T003` can run in parallel.
- Foundational tasks `T005` and `T008` can run in parallel after `T001-T004` establish the contract baseline.
- All test tasks marked `[P]` within each user story can run in parallel.
- US3 implementation task `T032` can proceed in parallel with `T029-T031` once the core service interfaces are stable.

---

## Parallel Example: User Story 1

```bash
# Parallel validation work for US1
Task T009: tests/contract/test_temporal_execution_api.py
Task T010: tests/unit/workflows/temporal/test_temporal_service.py
Task T011: tests/unit/workflows/adapters/test_jules_client.py
Task T012: tests/integration/temporal/test_integrations_monitoring.py
```

## Parallel Example: User Story 2

```bash
# Parallel validation work for US2
Task T017: tests/unit/api/routers/test_execution_integrations.py
Task T018: tests/unit/workflows/temporal/test_temporal_service.py
Task T019: tests/contract/test_temporal_execution_api.py
Task T020: tests/integration/temporal/test_integrations_monitoring.py
```

## Parallel Example: User Story 3

```bash
# Parallel validation work for US3
Task T025: tests/unit/workflows/temporal/test_temporal_service.py
Task T026: tests/unit/workflows/temporal/test_artifacts.py
Task T027: tests/contract/test_temporal_execution_api.py
Task T028: tests/integration/temporal/test_integrations_monitoring.py
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1).
3. Validate callback-first and polling-fallback monitored-run behavior.
4. Stop there if only MVP monitoring is required.

### Incremental Delivery

1. Deliver Setup + Foundational to lock provider-neutral contracts and durable correlation.
2. Deliver US1 for core monitored-run behavior.
3. Deliver US2 for secure callback ingress and replay-safe dedupe.
4. Deliver US3 for Continue-As-New, cancellation/failure semantics, and Jules runtime hardening.
5. Run Phase 6 validation and runtime scope gates.

### Parallel Team Strategy

1. Align as a team on Phase 1 and Phase 2 because those tasks define shared runtime contracts.
2. After the foundational checkpoint:
   - Engineer A: US1 monitored-run flow
   - Engineer B: US2 callback ingress and correlation
   - Engineer C: US3 long-wait and provider-hardening behavior
3. Rejoin for Phase 6 regression and traceability updates.

---

## Task Summary

- Total tasks: **35**
- User story tasks: **US1 = 8**, **US2 = 8**, **US3 = 8**
- Parallelizable tasks (`[P]`): **17**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **All tasks follow `- [ ] T### [P?] [US?] ...` with explicit file paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T007, T013 | T009, T010 |
| DOC-REQ-002 | T004, T014, T032 | T011, T025 |
| DOC-REQ-003 | T005, T006, T008, T021, T022 | T010, T017, T034 |
| DOC-REQ-004 | T002, T003, T007, T016 | T010, T034 |
| DOC-REQ-005 | T002, T004, T013, T014 | T011, T034 |
| DOC-REQ-006 | T002, T004, T005, T014, T030 | T011, T026, T034 |
| DOC-REQ-007 | T007, T013, T015, T023 | T009, T010, T012, T018, T034 |
| DOC-REQ-008 | T003, T006, T008, T021, T022, T024 | T017, T019, T020, T034 |
| DOC-REQ-009 | T002, T007, T021, T023 | T018, T020, T034 |
| DOC-REQ-010 | T002, T007, T016, T024, T031 | T009, T019, T027, T034 |
| DOC-REQ-011 | T001, T006, T014, T015, T022, T029, T032 | T012, T020, T025, T028, T034 |
| DOC-REQ-012 | T005, T030 | T026, T027, T028, T034 |
| DOC-REQ-013 | T001, T005, T008, T021 | T017, T019, T026, T034 |
| DOC-REQ-014 | T015, T029, T030 | T012, T020, T025, T026, T028, T034 |
| DOC-REQ-015 | T001, T004, T032 | T011, T012, T028, T034 |
| DOC-REQ-016 | T004, T006, T014, T021, T022, T031, T032 | T033, T035 |
| RUNTIME-GUARD | T001-T008, T013-T016, T021-T024, T029-T032 | T034, T035 |

Coverage gate rule: every `DOC-REQ-*` must keep at least one implementation task and at least one validation task before implementation begins and before publish.

---

## Notes

- The ordered task sequence preserves the required delivery order from the source design: provider contract/normalization, correlation, callback ingress, polling fallback, visibility updates, then provider-specific validation.
- `check-prerequisites.sh` could not infer the feature directory from the MoonMind task branch name, so this task list uses the already-materialized feature folder `specs/047-integrations-monitoring/` as the authoritative target.
- Runtime-mode guard is satisfied by explicit production code tasks in `api_service/`, `moonmind/`, `api_service/migrations/versions/`, and explicit validation tasks under `tests/`.
