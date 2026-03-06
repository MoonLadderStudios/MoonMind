# Tasks: Tasks Image Attachments Phase 1 (Runtime Alignment)

**Input**: Design documents from `/specs/037-tasks-image-phase1/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Mode**: Runtime (`DOC-REQ-010`)  
**Tests**: Validation tests are required and must run through `./tools/test_unit.sh` (`DOC-REQ-011`).

## Format: `[ID] [P?] [Story] Description`

- [X] Tasks use sequential `T###` IDs in dependency order.
- [X] `[P]` marks tasks that are parallelizable (different files, no unmet dependencies).
- [X] `[US#]` labels are used only in user-story phases.
- [X] Every task includes concrete file path(s).

## Prompt B Scope Controls (Step 12/16)

- Runtime production implementation tasks are explicitly present: `T004-T008`, `T011-T013`, `T017-T020`, `T023-T025`.
- Runtime validation tasks are explicitly present: `T003`, `T009`, `T010`, `T014-T016`, `T021`, `T022`, `T028`.
- `DOC-REQ-*` implementation + validation coverage is enforced through `T002` and `T026`, with per-requirement mappings persisted in `specs/037-tasks-image-phase1/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Lock runtime scope, traceability, and deterministic fixtures before implementation updates.

- [X] T001 Reconcile runtime Phase 1 scope and implementation surfaces in `specs/037-tasks-image-phase1/plan.md` and `specs/037-tasks-image-phase1/spec.md` (`DOC-REQ-010`).
- [X] T002 [P] Seed full `DOC-REQ-001` to `DOC-REQ-011` task mapping skeleton in `specs/037-tasks-image-phase1/contracts/requirements-traceability.md`.
- [X] T003 [P] Refresh attachment fixtures for PNG/JPEG/WebP and invalid payload coverage in `tests/fixtures/attachments/` (`DOC-REQ-002`, `DOC-REQ-011`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Complete blocking API/service/view-model prerequisites needed by all user stories.

- [X] T004 Reconcile multipart create contract and explicit caption fail-fast handling in `api_service/api/routers/agent_queue.py` and `moonmind/workflows/agent_queue/task_contract.py` (`DOC-REQ-001`, `DOC-REQ-009`).
- [X] T005 [P] Reconcile attachment type/signature/count/size validation and persistence gating in `moonmind/workflows/agent_queue/service.py` (`DOC-REQ-001`, `DOC-REQ-002`).
- [X] T006 [P] Reconcile reserved `inputs/` namespace enforcement and worker-upload rejection in `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/storage.py` (`DOC-REQ-003`).
- [X] T007 [P] Reconcile owner and active-claim authorization guards for attachment list/download endpoints in `api_service/api/routers/agent_queue.py` and `moonmind/workflows/agent_queue/service.py` (`DOC-REQ-004`).
- [X] T008 Reconcile dashboard runtime attachment config exposure in `api_service/api/routers/task_dashboard_view_model.py` (`DOC-REQ-008`, `DOC-REQ-010`).

**Checkpoint**: Foundational runtime prerequisites are complete; user-story work can proceed.

---

## Phase 3: User Story 1 - Submit Task With Image Attachments (Priority: P1) 🎯 MVP

**Goal**: Ensure attachment-enabled queue creation is validated, persisted, and only claimable after persistence.

**Independent Test**: Submit valid and invalid attachment payloads and verify validation + claimability gating behavior in automated tests.

### Tests for User Story 1

- [X] T009 [P] [US1] Add router tests for create-with-attachments atomic visibility and validation failures in `tests/unit/api/routers/test_agent_queue.py` (`DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-011`).
- [X] T010 [P] [US1] Add artifact/service tests for reserved `inputs/` namespace rules and caption fail-fast behavior in `tests/unit/workflows/agent_queue/test_artifact_storage.py` and `tests/unit/api/routers/test_agent_queue_artifacts.py` (`DOC-REQ-003`, `DOC-REQ-009`, `DOC-REQ-011`).

### Implementation for User Story 1

- [X] T011 [US1] Implement create-with-attachments persist-before-claim flow in `moonmind/workflows/agent_queue/service.py` (`DOC-REQ-001`, `DOC-REQ-010`).
- [X] T012 [US1] Implement deterministic attachment metadata normalization and reserved-path persistence in `moonmind/workflows/agent_queue/service.py` (`DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-010`).
- [X] T013 [US1] Implement user/worker attachment endpoint response shaping and authorization error handling in `api_service/api/routers/agent_queue.py` (`DOC-REQ-004`, `DOC-REQ-010`).

**Checkpoint**: User Story 1 supports validated attachment submission with persistence gating.

---

## Phase 4: User Story 2 - Worker Consumes Attachments During Prepare (Priority: P1)

**Goal**: Ensure prepare-stage runtime deterministically downloads attachments, generates artifacts, and injects prompt context in required order.

**Independent Test**: Run worker prepare for attachment-enabled jobs and verify downloads, manifest/context outputs, lifecycle events, and prompt ordering.

### Tests for User Story 2

- [X] T014 [P] [US2] Add worker prepare tests for attachment download lifecycle events, manifest emission, and `task_context.json` attachment summary in `tests/unit/agents/codex_worker/test_worker.py` (`DOC-REQ-005`, `DOC-REQ-011`).
- [X] T015 [P] [US2] Add worker tests for vision context enabled/disabled paths and deterministic `.moonmind/vision/image_context.md` generation in `tests/unit/agents/codex_worker/test_worker.py` (`DOC-REQ-007`, `DOC-REQ-011`).
- [X] T016 [P] [US2] Add worker tests asserting `INPUT ATTACHMENTS` is injected before `WORKSPACE` in runtime instructions in `tests/unit/agents/codex_worker/test_worker.py` (`DOC-REQ-006`, `DOC-REQ-011`).

### Implementation for User Story 2

- [X] T017 [US2] Implement worker attachment list/download client helpers and prepare-stage `.moonmind/inputs` download pipeline in `moonmind/agents/codex_worker/worker.py` (`DOC-REQ-005`, `DOC-REQ-010`).
- [X] T018 [US2] Implement `.moonmind/attachments_manifest.json` generation and `artifacts/task_context.json` attachment summary wiring in `moonmind/agents/codex_worker/worker.py` (`DOC-REQ-005`, `DOC-REQ-011`, `DOC-REQ-010`).
- [X] T019 [US2] Implement toggleable attachment vision context rendering in `moonmind/agents/codex_worker/worker.py` and `moonmind/vision/service.py` (`DOC-REQ-007`, `DOC-REQ-010`).
- [X] T020 [US2] Implement runtime instruction composition that injects `INPUT ATTACHMENTS` before `WORKSPACE` in `moonmind/agents/codex_worker/worker.py` (`DOC-REQ-006`, `DOC-REQ-010`).

**Checkpoint**: User Story 2 deterministically prepares and injects attachment context for runtime execution.

---

## Phase 5: User Story 3 - Review Attachments in Queue Detail (Priority: P2)

**Goal**: Ensure dashboard users can upload, preview, and download authorized attachments from queue create/detail flows.

**Independent Test**: Validate dashboard view-model config and queue detail attachment behavior for authorized and unauthorized contexts.

### Tests for User Story 3

- [X] T021 [P] [US3] Add view-model tests for attachment runtime config and endpoint exposure in `tests/unit/api/routers/test_task_dashboard_view_model.py` (`DOC-REQ-008`, `DOC-REQ-011`).
- [X] T022 [P] [US3] Extend queue router tests for owner/unauthorized attachment list/download access in `tests/unit/api/routers/test_agent_queue.py` (`DOC-REQ-004`, `DOC-REQ-008`, `DOC-REQ-011`).

### Implementation for User Story 3

- [X] T023 [US3] Implement queue create multipart attachment upload wiring and limit messaging in `api_service/static/task_dashboard/dashboard.js` (`DOC-REQ-008`, `DOC-REQ-010`).
- [X] T024 [US3] Implement queue detail attachment preview and download actions in `api_service/static/task_dashboard/dashboard.js` (`DOC-REQ-008`, `DOC-REQ-010`).
- [X] T025 [US3] Implement dashboard attachment config consumption and API endpoint mapping in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard_view_model.py` (`DOC-REQ-008`, `DOC-REQ-011`, `DOC-REQ-010`).

**Checkpoint**: User Story 3 provides attachment upload + preview/download UX with authorization guarantees.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability, validation evidence, and delivery hardening.

- [X] T026 [P] Update `DOC-REQ-001` to `DOC-REQ-011` implementation/validation task mappings in `specs/037-tasks-image-phase1/contracts/requirements-traceability.md`.
- [X] T027 [P] Update runtime validation walkthrough and attachment scenarios in `specs/037-tasks-image-phase1/quickstart.md`, including `./tools/test_unit.sh` commands (`DOC-REQ-010`, `DOC-REQ-011`).
- [X] T028 Execute `./tools/test_unit.sh tests/unit/api/routers/test_agent_queue.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/agents/codex_worker/test_worker.py` and record results in `specs/037-tasks-image-phase1/quickstart.md` (`DOC-REQ-010`, `DOC-REQ-011`).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks all user-story work.
- Phases 3, 4, and 5 depend on Phase 2.
- Phase 6 depends on completion of targeted user stories.

### User Story Dependencies

- **US1 (P1)** starts after Phase 2 and is the MVP for attachment ingestion + gating.
- **US2 (P1)** starts after Phase 2 and depends on persisted attachment behavior from US1.
- **US3 (P2)** starts after Phase 2 and depends on attachment API/config stability from US1.

### Within Each User Story

- Tests are defined and completed before final sign-off.
- Core runtime implementation tasks complete before cross-cutting polish.
- Each story must satisfy its independent test criteria before moving on.

## Parallel Opportunities

- T002 and T003 can run in parallel during setup.
- T005, T006, and T007 can run in parallel after T004 begins.
- US1 tests T009 and T010 can run in parallel.
- US2 tests T014, T015, and T016 can run in parallel.
- US3 tests T021 and T022 can run in parallel.
- T026 and T027 can run in parallel before T028.

## Parallel Example: User Story 2

```bash
# Parallel worker validation tasks
Task: T014 tests/unit/agents/codex_worker/test_worker.py (prepare lifecycle + manifest assertions)
Task: T015 tests/unit/agents/codex_worker/test_worker.py (vision context enabled/disabled assertions)
Task: T016 tests/unit/agents/codex_worker/test_worker.py (prompt block ordering assertions)
```

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1).
3. Validate US1 independently before proceeding.

### Incremental Delivery

1. Deliver US1 (attachment ingestion + gating).
2. Deliver US2 (worker prepare + prompt injection).
3. Deliver US3 (dashboard create/detail UX).
4. Run Phase 6 final traceability + wrapper test validation.

## Task Summary & Validation

- Total tasks: **28**
- Task count by user story: **US1: 5**, **US2: 7**, **US3: 5**
- Non-story tasks (Setup/Foundational/Polish): **11**
- Parallel opportunities: **Yes** (Setup, Foundational, and per-story tests)
- Independent test criteria: Defined in each story phase
- Suggested MVP scope: **Phase 1 + Phase 2 + Phase 3 (US1)**
- Format validation: All tasks follow `- [X] T### [P?] [US?] Description with file path`
