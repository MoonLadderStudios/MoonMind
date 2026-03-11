# Tasks: Activity Catalog and Worker Topology

**Input**: Design documents from `/specs/047-activity-worker-topology/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Validation is required for this runtime-mode feature. Include unit, contract, and compose-backed Temporal verification tasks.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: Which user story this task belongs to (`[US1]`, `[US2]`, `[US3]`)
- Every task includes exact file paths and carries `DOC-REQ-*` tags for traceability

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare shared runtime configuration and worker entrypoints before catalog and story work begins.

- [X] T001 Add worker-fleet environment defaults and concurrency settings in `moonmind/config/settings.py` for `DOC-REQ-010`, `DOC-REQ-011`, and `DOC-REQ-018`
- [X] T002 [P] Create the fleet-aware Temporal worker launcher in `services/temporal/scripts/start-worker.sh` for `DOC-REQ-011`, `DOC-REQ-013`, and `DOC-REQ-018`
- [X] T003 [P] Export planned worker-topology surfaces from `moonmind/workflows/temporal/__init__.py` for `DOC-REQ-002`, `DOC-REQ-010`, and `DOC-REQ-018`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the shared catalog, envelope, and worker bootstrap primitives that all user stories depend on.

**⚠️ CRITICAL**: No user story work should start until this phase is complete.

- [X] T004 Implement the complete canonical activity catalog, family policy profiles, and fail-closed route validation in `moonmind/workflows/temporal/activity_catalog.py` for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-012`, `DOC-REQ-016`, and `DOC-REQ-017`
- [X] T005 [P] Add shared invocation envelope, compact result, and runtime-context helpers in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/skills/tool_plan_contracts.py` for `DOC-REQ-003`, `DOC-REQ-005`, `DOC-REQ-012`, and `DOC-REQ-017`
- [X] T006 [P] Create dedicated worker fleet bootstrap and registration helpers in `moonmind/workflows/temporal/workers.py` for `DOC-REQ-001`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-014`, `DOC-REQ-015`, and `DOC-REQ-016`
- [X] T007 Wire the workflow/artifacts/llm/sandbox/integrations services into `docker-compose.yaml` using the shared worker bootstrap from `services/temporal/scripts/start-worker.sh` for `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-013`, `DOC-REQ-015`, `DOC-REQ-016`, and `DOC-REQ-018`

**Checkpoint**: Catalog, envelope, and worker-fleet foundations are ready; user story work can proceed.

---

## Phase 3: User Story 1 - Route Canonical Activities to the Correct Worker Fleet (Priority: P1) 🎯 MVP

**Goal**: Deliver canonical queue routing, capability-based binding, and dedicated worker fleet registration for every v1 activity family.

**Independent Test**: Start the Temporal worker topology, invoke one activity from each canonical family, and verify each one lands on the documented queue and fleet with mismatched capability bindings rejected.

### Tests for User Story 1

- [X] T008 [P] [US1] Extend queue, fleet, timeout, and binding rejection coverage in `tests/unit/workflows/temporal/test_activity_catalog.py` for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-012`, `DOC-REQ-016`, and `DOC-REQ-017`
- [X] T009 [P] [US1] Add worker registration and least-privilege topology coverage in `tests/unit/workflows/temporal/test_temporal_workers.py` for `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-014`, and `DOC-REQ-015`
- [X] T010 [P] [US1] Add canonical queue-set, shared-envelope contract, and compose-backed routing assertions in `tests/contract/test_temporal_activity_topology.py` and `tests/integration/temporal/test_activity_worker_topology.py` for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-015`, `DOC-REQ-016`, and `DOC-REQ-018`

### Implementation for User Story 1

- [X] T011 [US1] Finish per-invocation capability routing, explicit binding rejection, and Appendix A catalog coverage in `moonmind/workflows/temporal/activity_catalog.py` for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-006`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-012`, `DOC-REQ-016`, and `DOC-REQ-017`
- [X] T012 [US1] Implement workflow and activity worker construction, queue registration, and fleet profile enforcement in `moonmind/workflows/temporal/workers.py` and `moonmind/workflows/temporal/__init__.py` for `DOC-REQ-001`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-014`, and `DOC-REQ-016`
- [X] T013 [US1] Materialize dedicated workflow, artifacts, llm, sandbox, and integrations services in `docker-compose.yaml` and `services/temporal/scripts/start-worker.sh` for `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-013`, `DOC-REQ-015`, `DOC-REQ-016`, and `DOC-REQ-018`

**Checkpoint**: Canonical activity routing works end-to-end and the dedicated worker topology can be started and verified independently.

---

## Phase 4: User Story 2 - Execute Stable Activity Contracts with Artifact-Backed Payloads (Priority: P1)

**Goal**: Deliver stable request/response contracts for artifact, plan, skill, sandbox, and integration activities with artifact references replacing large inline payloads.

**Independent Test**: Exercise each canonical activity family with side-effecting requests and confirm envelopes require correlation/idempotency data, large inputs and outputs flow through artifacts, and visibility mutations remain in workflow code.

### Tests for User Story 2

- [X] T014 [P] [US2] Extend envelope, artifact-ref, idempotency, visibility-boundary, and sandbox/integration request-envelope coverage in `tests/unit/workflows/temporal/test_activity_runtime.py` for `DOC-REQ-003`, `DOC-REQ-005`, `DOC-REQ-012`, `DOC-REQ-017`, and `DOC-REQ-018`
- [X] T015 [P] [US2] Extend artifact lifecycle, preview, link, pin, and retention coverage in `tests/unit/workflows/temporal/test_artifacts.py` and `tests/unit/workflows/temporal/test_artifact_lifecycle.py` for `DOC-REQ-004`, `DOC-REQ-012`, and `DOC-REQ-016`
- [X] T016 [P] [US2] Extend plan-validation, skill-binding, and sandbox/integration shared-envelope rule coverage in `tests/unit/workflows/test_skill_plan_runtime.py`, `tests/unit/workflows/test_skills_registry.py`, and `tests/contract/test_temporal_activity_topology.py` for `DOC-REQ-003`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-012`, `DOC-REQ-015`, `DOC-REQ-016`, and `DOC-REQ-018`

### Implementation for User Story 2

- [X] T017 [US2] Implement shared request/result envelope parsing, runtime-context metadata, and workflow-owned visibility boundaries in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/service.py` for `DOC-REQ-003`, `DOC-REQ-005`, `DOC-REQ-012`, and `DOC-REQ-017`
- [X] T018 [US2] Complete `artifact.write_complete`, `artifact.list_for_execution`, `artifact.compute_preview`, link, pin, unpin, and lifecycle-sweep behavior in `moonmind/workflows/temporal/artifacts.py` and `api_service/api/routers/temporal_artifacts.py` for `DOC-REQ-004`, `DOC-REQ-013`, and `DOC-REQ-016`
- [X] T019 [US2] Implement artifact-backed `plan.generate` and authoritative `plan.validate` flows in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/skills/tool_plan_contracts.py` for `DOC-REQ-003`, `DOC-REQ-005`, and `DOC-REQ-016`
- [X] T020 [US2] Enforce the hybrid skill execution model and explicit binding reason validation in `moonmind/workflows/skills/tool_registry.py` and `moonmind/workflows/skills/tool_dispatcher.py` for `DOC-REQ-006`, `DOC-REQ-009`, `DOC-REQ-012`, and `DOC-REQ-016`
- [X] T021 [US2] Add canonical sandbox and integration activity contracts, including checkout/apply-patch/run-tests request shapes and compact result envelopes, in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/adapters/jules_client.py` for `DOC-REQ-007`, `DOC-REQ-008`, and `DOC-REQ-016`

**Checkpoint**: Canonical activity contracts are stable, artifact-backed, and independently verifiable without relying on inline workflow-history blobs.

---

## Phase 5: User Story 3 - Operate the Fleet Securely and Recover from Failures (Priority: P2)

**Goal**: Deliver heartbeat-aware sandbox execution, idempotent integration recovery, least-privilege secret scoping, and structured observability across the worker fleets.

**Independent Test**: Inject long-running sandbox work, retried provider calls, and restricted artifact access flows, then verify heartbeat data, idempotent recovery, redaction, and fleet privilege boundaries.

### Tests for User Story 3

- [X] T022 [P] [US3] Add security, redaction, and observability coverage in `tests/unit/workflows/temporal/test_artifact_authorization.py` and `tests/unit/workflows/temporal/test_activity_runtime.py` for `DOC-REQ-013`, `DOC-REQ-014`, and `DOC-REQ-017`
- [X] T023 [P] [US3] Add integration retry, callback/polling fallback, and worker policy coverage in `tests/unit/workflows/adapters/test_jules_client.py` and `tests/unit/workflows/temporal/test_temporal_workers.py` for `DOC-REQ-008`, `DOC-REQ-011`, `DOC-REQ-012`, and `DOC-REQ-014`
- [X] T024 [P] [US3] Add compose-backed heartbeat, failure-injection, and restricted-preview regression coverage in `tests/integration/temporal/test_activity_worker_topology.py` and `tests/integration/temporal/test_temporal_artifact_auth_preview.py` for `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, and `DOC-REQ-018`

### Implementation for User Story 3

- [X] T025 [US3] Implement heartbeat-aware sandbox checkout, patch, command, and test execution with idempotent workspace refs and redacted diagnostics in `moonmind/workflows/temporal/activity_runtime.py` for `DOC-REQ-007`, `DOC-REQ-012`, `DOC-REQ-013`, `DOC-REQ-014`, and `DOC-REQ-016`
- [X] T026 [US3] Implement integration idempotency, callback-first tracking, and bounded polling fallback in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/adapters/jules_client.py` for `DOC-REQ-008`, `DOC-REQ-012`, `DOC-REQ-014`, and `DOC-REQ-016`
- [X] T027 [US3] Enforce least-privilege secret scope and private local object-storage posture across fleets in `moonmind/workflows/temporal/workers.py`, `moonmind/config/settings.py`, and `docker-compose.yaml` for `DOC-REQ-011`, `DOC-REQ-013`, and `DOC-REQ-018`
- [X] T028 [US3] Add structured activity summaries, metrics hooks, and artifact-backed diagnostics emission in `moonmind/workflows/temporal/telemetry.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/utils/logging.py` for `DOC-REQ-014`, `DOC-REQ-015`, and `DOC-REQ-018`

**Checkpoint**: Reliability, observability, and least-privilege security requirements are implemented and independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Close runtime acceptance, execute the required validation commands, and prove the feature meets the runtime-mode scope gate.

- [X] T029 [P] Reconcile final Temporal exports and service wiring in `moonmind/workflows/temporal/__init__.py` and `moonmind/workflows/temporal/service.py` for `DOC-REQ-001`, `DOC-REQ-015`, and `DOC-REQ-018`
- [X] T030 Run repository-standard unit acceptance with `./tools/test_unit.sh`, including `tests/unit/specs/test_doc_req_traceability.py`, for `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-012`, `DOC-REQ-015`, `DOC-REQ-016`, and `DOC-REQ-018`
- [X] T031 Run compose-backed topology verification against `tests/integration/temporal/test_activity_worker_topology.py`, `tests/integration/temporal/test_temporal_artifact_lifecycle.py`, and `tests/integration/temporal/test_temporal_artifact_auth_preview.py` for `DOC-REQ-001`, `DOC-REQ-004`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, and `DOC-REQ-018`
- [X] T032 Run the runtime scope gate with `.specify/scripts/bash/validate-implementation-scope.sh` using `specs/047-activity-worker-topology/tasks.md` for `DOC-REQ-018`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1: Setup** has no dependencies and starts immediately.
- **Phase 2: Foundational** depends on Phase 1 and blocks all story work.
- **Phase 3: User Story 1** depends on Phase 2.
- **Phase 4: User Story 2** depends on Phase 2 and can proceed in parallel with User Story 1 once the foundational catalog and envelope work is in place.
- **Phase 5: User Story 3** depends on the runtime surfaces from User Story 1 and User Story 2 because it hardens those families for retries, observability, and least-privilege operation.
- **Phase 6: Polish** depends on the desired user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: Starts after Phase 2 and establishes the routing/topology MVP.
- **User Story 2 (P1)**: Starts after Phase 2 and shares the same foundations as User Story 1, but does not require User Story 1 to finish first.
- **User Story 3 (P2)**: Starts after User Story 1 and User Story 2 because heartbeat, recovery, and security work depend on the canonical families being in place.

### Within Each User Story

- Validation tasks should be implemented before or alongside the runtime changes they prove, and each story's independent test must live in that story's task set.
- Routing/catalog changes precede fleet bootstrap wiring.
- Envelope and artifact contract changes precede higher-level sandbox and integration contract work.
- Reliability and observability changes follow the existence of the canonical runtime handlers they instrument.

### Parallel Opportunities

- `T002` and `T003` can run in parallel after `T001`.
- `T005` and `T006` can run in parallel after `T004`.
- `T008`, `T009`, and `T010` can run in parallel within User Story 1.
- `T014`, `T015`, and `T016` can run in parallel within User Story 2.
- `T022`, `T023`, and `T024` can run in parallel within User Story 3.
- `T030` and `T031` can run in parallel once implementation is complete.

---

## Parallel Example: User Story 1

```bash
Task: "Extend queue, fleet, timeout, and binding rejection coverage in tests/unit/workflows/temporal/test_activity_catalog.py"
Task: "Add worker registration and least-privilege topology coverage in tests/unit/workflows/temporal/test_temporal_workers.py"
Task: "Add canonical queue-set, shared-envelope contract, and compose-backed routing assertions in tests/contract/test_temporal_activity_topology.py and tests/integration/temporal/test_activity_worker_topology.py"
```

## Parallel Example: User Story 2

```bash
Task: "Extend envelope, artifact-ref, idempotency, and visibility-boundary coverage in tests/unit/workflows/temporal/test_activity_runtime.py"
Task: "Extend artifact lifecycle, preview, link, pin, and retention coverage in tests/unit/workflows/temporal/test_artifacts.py and tests/unit/workflows/temporal/test_artifact_lifecycle.py"
Task: "Extend plan-validation, skill-binding, and shared-envelope rule coverage in tests/unit/workflows/test_skill_plan_runtime.py, tests/unit/workflows/test_skills_registry.py, and tests/contract/test_temporal_activity_topology.py"
```

## Parallel Example: User Story 3

```bash
Task: "Add security, redaction, and observability coverage in tests/unit/workflows/temporal/test_artifact_authorization.py and tests/unit/workflows/temporal/test_activity_runtime.py"
Task: "Add integration retry, callback/polling fallback, and worker policy coverage in tests/unit/workflows/adapters/test_jules_client.py and tests/unit/workflows/temporal/test_temporal_workers.py"
Task: "Add compose-backed heartbeat, failure-injection, and restricted-preview regression coverage in tests/integration/temporal/test_activity_worker_topology.py and tests/integration/temporal/test_temporal_artifact_auth_preview.py"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational.
3. Complete Phase 3: User Story 1.
4. Validate routing and dedicated worker-fleet startup before expanding contract coverage.

### Incremental Delivery

1. Finish Setup + Foundational to lock the canonical catalog, envelopes, and worker bootstrap.
2. Deliver User Story 1 to prove the queue topology and routing contract.
3. Deliver User Story 2 to finish the stable activity envelopes and artifact-backed payload discipline.
4. Deliver User Story 3 to harden security, retries, heartbeats, and observability.
5. Finish with Phase 6 validation and scope-gate execution.

### Parallel Team Strategy

1. One engineer completes Phase 1 and Phase 2.
2. After the foundation is stable:
   Developer A can own User Story 1 routing/topology work.
   Developer B can own User Story 2 contract/envelope work.
3. User Story 3 begins once those runtime surfaces exist and can be hardened together.

---

## Notes

- All tasks follow the required checklist format with sequential IDs.
- Runtime implementation mode is preserved: production file changes and automated validation are both explicit.
- Every `DOC-REQ-001` through `DOC-REQ-018` appears in implementation and validation tasks for traceability.
- The User Story 1 task set now owns the first compose-backed routing proof instead of deferring it to later hardening.
- Suggested MVP scope: User Story 1.
