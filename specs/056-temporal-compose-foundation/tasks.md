# Tasks: Temporal Compose Foundation

**Input**: Design documents from `/specs/044-temporal-compose-foundation/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Tests are required for this feature because the specification mandates automated validation coverage for runtime deliverables.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present: `T001-T015`, `T019-T021`, `T027-T033`, `T037-T040`.
- Runtime validation tasks are explicitly present: `T016-T018`, `T022-T026`, `T034-T036`, `T042-T043`.
- `DOC-REQ-*` implementation + validation coverage is enforced by `T041` and the `DOC-REQ Coverage Matrix` in this file, with persistent mapping in `specs/044-temporal-compose-foundation/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare compose and runtime scaffolding for Temporal foundation work.

- [X] T001 Update Temporal service topology and private-network exposure in `docker-compose.yaml` for self-hosted compose foundation and PostgreSQL-backed runtime (DOC-REQ-001, DOC-REQ-002, DOC-REQ-010).
- [X] T002 Add Temporal foundation defaults (`TEMPORAL_NAMESPACE`, `TEMPORAL_RETENTION_MAX_STORAGE_GB`, `TEMPORAL_NUM_HISTORY_SHARDS`, worker versioning knobs) in `.env-template` (DOC-REQ-004, DOC-REQ-007, DOC-REQ-008).
- [X] T003 [P] Align SQL visibility and persistence dynamic config in `services/temporal/dynamicconfig/development-sql.yaml` (DOC-REQ-002).
- [X] T004 [P] Create visibility schema rehearsal automation in `services/temporal/scripts/rehearse-visibility-schema-upgrade.sh` (DOC-REQ-003).
- [X] T005 [P] Harden idempotent namespace reconciliation behavior in `services/temporal/scripts/bootstrap-namespace.sh` for `moonmind` retention governance (DOC-REQ-004).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build Temporal runtime primitives and API wiring required by all user stories.

**⚠️ CRITICAL**: No user story work should begin until this phase is complete.

- [ ] T006 Create Temporal runtime package scaffold in `moonmind/workflows/temporal/__init__.py` for client/service/schedule/worker modules shared by all stories (DOC-REQ-011).
- [ ] T007 Implement Temporal configuration parsing and policy guards in `moonmind/config/settings.py` (address, namespace, shard decision, Auto-Upgrade defaults) (DOC-REQ-007, DOC-REQ-008, DOC-REQ-010).
- [ ] T008 [P] Define API/runtime schema models in `moonmind/schemas/temporal_models.py` and export them in `moonmind/schemas/__init__.py` (DOC-REQ-012).
- [ ] T009 Implement Temporal connectivity and visibility query adapter in `moonmind/workflows/temporal/client.py` (DOC-REQ-005, DOC-REQ-011).
- [ ] T010 Implement task-queue taxonomy and worker-versioning policy module in `moonmind/workflows/temporal/workers.py` (DOC-REQ-006, DOC-REQ-007).
- [ ] T011 Implement core execution orchestration service in `moonmind/workflows/temporal/service.py` for lifecycle commands, artifact references, manifest policy, and callback/polling patterns (DOC-REQ-011, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015).
- [ ] T012 Implement Temporal schedules service in `moonmind/workflows/temporal/schedules.py` and migration hook in `moonmind/workflows/recurring_tasks/scheduler.py` (DOC-REQ-009).
- [ ] T013 Add Temporal execution projection and upgrade readiness persistence models in `api_service/db/models.py` and migration `api_service/migrations/versions/044_temporal_compose_foundation.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-008).
- [ ] T014 Implement execution/foundation router in `api_service/api/routers/executions.py` covering lifecycle and foundation endpoints from the OpenAPI contract (DOC-REQ-012).
- [ ] T015 Register Temporal execution router in `api_service/api/routers/__init__.py` and `api_service/main.py` so foundation and lifecycle endpoints are active (DOC-REQ-001, DOC-REQ-012).

**Checkpoint**: Foundation runtime primitives are complete and user stories can proceed.

---

## Phase 3: User Story 1 - Bring up Temporal platform foundation (Priority: P1) 🎯 MVP

**Goal**: Provision and validate a compose-managed Temporal foundation with persistence, visibility, namespace retention, and upgrade-rehearsal readiness.

**Independent Test**: Bring up Temporal compose services and run foundation integration checks for health, SQL visibility, namespace idempotency, and upgrade rehearsal gates.

### Tests for User Story 1

- [X] T016 [P] [US1] Add compose foundation integration coverage in `tests/integration/temporal/test_compose_foundation.py` for private-network topology, Temporal health, and PostgreSQL persistence/visibility checks (DOC-REQ-001, DOC-REQ-002, DOC-REQ-010).
- [X] T017 [P] [US1] Add namespace retention integration checks in `tests/integration/temporal/test_namespace_retention.py` for idempotent `moonmind` reconciliation and storage-cap policy defaults (DOC-REQ-004).
- [X] T018 [P] [US1] Add upgrade rehearsal integration checks in `tests/integration/temporal/test_upgrade_rehearsal.py` to block rollout on failed/missing visibility schema rehearsal and shard acknowledgment gate (DOC-REQ-003, DOC-REQ-008).

### Implementation for User Story 1

- [ ] T019 [US1] Implement foundation health and namespace reconcile service wiring in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-004, DOC-REQ-010).
- [ ] T020 [US1] Implement upgrade readiness endpoint behavior and readiness record writes in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` (DOC-REQ-003, DOC-REQ-008).
- [X] T021 [US1] Finalize deterministic bootstrap/rehearsal script exit semantics in `services/temporal/scripts/bootstrap-namespace.sh` and `services/temporal/scripts/rehearse-visibility-schema-upgrade.sh` for compose startup automation (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003).

**Checkpoint**: User Story 1 is independently functional and foundation runtime can be validated end to end.

---

## Phase 4: User Story 2 - Execute and observe Temporal-native lifecycle flows (Priority: P2)

**Goal**: Deliver Temporal-native execution lifecycle APIs with visibility-backed listing, schedule ownership, artifact-reference payload handling, and callback/polling orchestration.

**Independent Test**: Execute start/update/signal/cancel/list/describe flows with visibility-backed pagination and verify manifest policy/callback behavior through contract and integration tests.

### Tests for User Story 2

- [ ] T022 [P] [US2] Add lifecycle contract tests in `tests/contract/test_temporal_execution_api.py` for `POST /api/executions`, `update`, `signal`, `cancel`, `GET list`, and `GET describe` semantics including page tokens (DOC-REQ-005, DOC-REQ-012).
- [ ] T023 [P] [US2] Add lifecycle and visibility integration tests in `tests/integration/temporal/test_visibility_and_lifecycle.py` verifying visibility-sourced filtering/pagination and Temporal-first execution ownership (DOC-REQ-005, DOC-REQ-011, DOC-REQ-012).
- [ ] T024 [P] [US2] Add unit coverage in `tests/unit/workflows/temporal/test_service.py` for artifact reference envelope and manifest failure-policy branching (`fail_fast`, `continue_and_report`, `best_effort`) (DOC-REQ-013, DOC-REQ-014).
- [ ] T025 [P] [US2] Add external monitoring integration tests in `tests/integration/temporal/test_external_monitoring_callbacks.py` for callback signal and timer-based polling fallback behavior (DOC-REQ-015).
- [ ] T026 [P] [US2] Add Temporal schedule integration tests in `tests/integration/temporal/test_schedules.py` for upsert/trigger flows replacing external cron ownership (DOC-REQ-009).

### Implementation for User Story 2

- [ ] T027 [US2] Implement lifecycle request/response handling in `api_service/api/routers/executions.py` using `moonmind/schemas/temporal_models.py` (DOC-REQ-012).
- [ ] T028 [US2] Implement visibility-backed list/count/filter pagination in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/temporal/client.py` (DOC-REQ-005).
- [ ] T029 [US2] Enforce Temporal-first execution ownership and activity side-effect boundaries in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/agentkit_celery/tasks.py` (DOC-REQ-011).
- [ ] T030 [US2] Implement artifact-reference payload persistence flow in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/agent_queue/storage.py` to keep large payloads out of history (DOC-REQ-013).
- [ ] T031 [US2] Implement manifest ingestion failure-policy runtime handling in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/manifests.py` (DOC-REQ-014).
- [ ] T032 [US2] Implement callback signal and timer polling fallback orchestration in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` (DOC-REQ-015).
- [ ] T033 [US2] Migrate recurring task orchestration endpoints to Temporal schedules in `api_service/api/routers/recurring_tasks.py` and `moonmind/workflows/temporal/schedules.py` (DOC-REQ-009).

**Checkpoint**: User Story 2 is independently functional with contract and integration validation.

---

## Phase 5: User Story 3 - Operate safely and upgrade predictably (Priority: P3)

**Goal**: Enforce operational guardrails for queue semantics, worker versioning defaults, shard decision gates, and observability signals.

**Independent Test**: Execute unit/integration guardrail tests covering Auto-Upgrade defaults, routing-only queues, shard-ack gating, and observability behavior under failure conditions.

### Tests for User Story 3

- [ ] T034 [P] [US3] Add worker versioning default tests in `tests/unit/workflows/temporal/test_workers.py` validating Auto-Upgrade baseline and explicit exception governance (DOC-REQ-007).
- [ ] T035 [P] [US3] Add settings/queue semantics tests in `tests/unit/workflows/temporal/test_settings_and_queue_semantics.py` for routing-only queue behavior and shard decision gating (DOC-REQ-006, DOC-REQ-008).
- [ ] T036 [P] [US3] Add observability integration tests in `tests/integration/temporal/test_observability_guardrails.py` for no-poller, retry-storm, and visibility-failure signals (DOC-REQ-010).

### Implementation for User Story 3

- [ ] T037 [US3] Enforce routing-only queue semantics in `moonmind/workflows/temporal/workers.py` and `api_service/api/routers/executions.py` without exposing queue-order product guarantees (DOC-REQ-006).
- [ ] T038 [US3] Enforce worker versioning Auto-Upgrade defaults and exception policy validation in `moonmind/workflows/temporal/workers.py` and `moonmind/config/settings.py` (DOC-REQ-007).
- [ ] T039 [US3] Implement shard decision acknowledgment gate and upgrade readiness persistence enforcement in `moonmind/workflows/temporal/service.py` and `api_service/db/models.py` (DOC-REQ-008).
- [ ] T040 [US3] Implement observability counters/log hooks for key Temporal failure modes in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/temporal/workers.py` (DOC-REQ-010).

**Checkpoint**: User Story 3 is independently functional with operational guardrails and verification.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability, regression validation, and release-readiness checks across all stories.

- [ ] T041 [P] Update traceability matrix in `specs/044-temporal-compose-foundation/contracts/requirements-traceability.md` with final implementation and validation evidence links for DOC-REQ-001 through DOC-REQ-015.
- [X] T042 Run comprehensive runtime regression via `./tools/test_unit.sh` and ensure Temporal foundation/lifecycle coverage in `tests/contract/` and `tests/integration/temporal/` is included in CI acceptance.
- [X] T043 Run runtime scope gate `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` after implementation to verify production runtime + test changes are both present.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user story implementation.
- **User Story 1 (Phase 3)**: Depends on Foundational completion.
- **User Story 2 (Phase 4)**: Depends on Foundational completion; can begin after US1 foundations are stable, but remains independently testable.
- **User Story 3 (Phase 5)**: Depends on Foundational completion and uses lifecycle/schedule behaviors delivered in US2 for full operations validation.
- **Polish (Phase 6)**: Depends on completion of all targeted user stories.

### User Story Dependencies

- **US1 (P1)**: Independent after Foundational phase; delivers MVP foundation runtime.
- **US2 (P2)**: Independent after Foundational phase, but benefits from US1 compose/foundation readiness for integration execution.
- **US3 (P3)**: Independent guardrail implementation after Foundational phase, with strongest validation once US2 lifecycle paths exist.

### Within Each User Story

- Write tests for the story first and confirm they fail before implementation.
- Complete service/runtime module changes before API/router integration changes.
- Finish implementation tasks before final story-level validation reruns.

### Parallel Opportunities

- Setup tasks `T003`-`T005` can run in parallel.
- Foundational tasks `T008`, `T009`, and `T010` can run in parallel after `T006`-`T007`.
- US1 test tasks `T016`-`T018` can run in parallel.
- US2 test tasks `T022`-`T026` can run in parallel.
- US3 test tasks `T034`-`T036` can run in parallel.

---

## Parallel Example: User Story 1

```bash
# Run US1 validation tracks in parallel:
Task T016: tests/integration/temporal/test_compose_foundation.py
Task T017: tests/integration/temporal/test_namespace_retention.py
Task T018: tests/integration/temporal/test_upgrade_rehearsal.py
```

## Parallel Example: User Story 2

```bash
# Run US2 validation tracks in parallel:
Task T022: tests/contract/test_temporal_execution_api.py
Task T023: tests/integration/temporal/test_visibility_and_lifecycle.py
Task T026: tests/integration/temporal/test_schedules.py
```

## Parallel Example: User Story 3

```bash
# Run US3 validation tracks in parallel:
Task T034: tests/unit/workflows/temporal/test_workers.py
Task T035: tests/unit/workflows/temporal/test_settings_and_queue_semantics.py
Task T036: tests/integration/temporal/test_observability_guardrails.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup).
2. Complete Phase 2 (Foundational).
3. Complete Phase 3 (US1 foundation runtime).
4. Validate US1 independently via `tests/integration/temporal/test_compose_foundation.py`, `test_namespace_retention.py`, and `test_upgrade_rehearsal.py`.

### Incremental Delivery

1. Deliver Setup + Foundational baseline.
2. Deliver US1 and validate compose foundation readiness.
3. Deliver US2 and validate execution lifecycle APIs/behavior.
4. Deliver US3 and validate operational guardrails.
5. Run cross-cutting regression and runtime scope gates.

### Parallel Team Strategy

1. Team aligns on Setup + Foundational tasks.
2. After Foundational checkpoint:
   - Engineer A drives US1 foundation validation.
   - Engineer B drives US2 lifecycle and schedule flows.
   - Engineer C drives US3 guardrails and observability.
3. Rejoin for Phase 6 regression and release checks.

---

## Quality Gates

1. Runtime scope gate (tasks): `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime scope gate (diff): `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
3. Unit suite gate: `./tools/test_unit.sh`
4. Traceability gate: each `DOC-REQ-001` through `DOC-REQ-015` remains represented by at least one implementation task and at least one validation task.

## Task Summary

- Total tasks: **43**
- User story tasks: **US1 = 6**, **US2 = 12**, **US3 = 7**
- Parallelizable tasks (`[P]`): **16**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **All tasks follow `- [ ] T### [P?] [US?] ...` with explicit paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T001, T015, T021 | T016 |
| DOC-REQ-002 | T001, T003, T021 | T016 |
| DOC-REQ-003 | T004, T013, T020, T021 | T018 |
| DOC-REQ-004 | T002, T005, T019 | T017 |
| DOC-REQ-005 | T009, T013, T028 | T022, T023 |
| DOC-REQ-006 | T010, T037 | T035 |
| DOC-REQ-007 | T002, T007, T010, T038 | T034 |
| DOC-REQ-008 | T002, T007, T013, T020, T039 | T018, T035 |
| DOC-REQ-009 | T012, T033 | T026 |
| DOC-REQ-010 | T001, T007, T019, T040 | T016, T036 |
| DOC-REQ-011 | T006, T009, T011, T029 | T023 |
| DOC-REQ-012 | T008, T014, T015, T027 | T022, T023 |
| DOC-REQ-013 | T011, T030 | T024 |
| DOC-REQ-014 | T011, T031 | T024 |
| DOC-REQ-015 | T011, T032 | T025 |

Coverage gate rule: each `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.
