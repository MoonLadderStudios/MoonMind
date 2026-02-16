# Tasks: Unified CLI Single Queue Worker Runtime

**Input**: Design documents from `/specs/018-unified-cli-queue/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature artifacts and establish baseline test/runtime scaffolding.

- [X] T001 Verify design artifacts exist and are internally consistent in `specs/018-unified-cli-queue/` (DOC-REQ-012).
- [X] T002 Create/update queue/runtime regression test scaffolding in `tests/unit/config/test_settings.py` and `tests/unit/workflows/test_celeryconfig.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-010, DOC-REQ-012).
- [X] T003 [P] Update runtime contract references in `specs/018-unified-cli-queue/contracts/runtime-job-contract.md` and `specs/018-unified-cli-queue/contracts/worker-runtime-contract.md` before implementation (DOC-REQ-005, DOC-REQ-006, DOC-REQ-011).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared queue defaults and runtime mode guardrails used by all user stories.

- [X] T004 Implement single-queue defaults and compatibility fallback in `moonmind/config/settings.py` (DOC-REQ-003, DOC-REQ-010).
- [X] T005 [P] Align Spec Kit Celery queue routing defaults in `moonmind/workflows/speckit_celery/celeryconfig.py` and `moonmind/workflows/speckit_celery/__init__.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-010).
- [X] T006 [P] Implement runtime mode validation and queue/runtime startup logging in `celery_worker/speckit_worker.py` and `celery_worker/gemini_worker.py` (DOC-REQ-004, DOC-REQ-011).
- [X] T007 Update Gemini task queue binding to single-queue compatibility behavior in `celery_worker/gemini_tasks.py` (DOC-REQ-003, DOC-REQ-010).

**Checkpoint**: Settings and worker bootstrap logic enforce one queue defaults and runtime mode validation.

---

## Phase 3: User Story 1 - Operate a Single Queue Runtime Fleet (Priority: P1) 🎯 MVP

**Goal**: Ensure workers can process runtime-neutral jobs from one queue with mode selected by environment.

**Independent Test**: Validate default queue resolution and runtime-mode acceptance through unit tests.

### Tests for User Story 1

- [X] T008 [P] [US1] Add settings tests validating `moonmind.jobs` default queue behavior and codex queue fallback in `tests/unit/config/test_settings.py` (DOC-REQ-003, DOC-REQ-010).
- [X] T009 [P] [US1] Add Celery router tests for fixed single-queue behavior in `tests/unit/workflows/test_celeryconfig.py` (DOC-REQ-003, DOC-REQ-005).

### Implementation for User Story 1

- [X] T010 [US1] Update worker queue configuration logging to reflect one effective queue in `celery_worker/speckit_worker.py` and `celery_worker/gemini_worker.py` (DOC-REQ-003, DOC-REQ-011).
- [X] T011 [US1] Add runtime-mode enum validation (`codex|gemini|claude|universal`) in worker entrypoints (`celery_worker/speckit_worker.py`, `celery_worker/gemini_worker.py`) (DOC-REQ-004, DOC-REQ-006).

**Checkpoint**: Workers bootstrap with validated runtime modes and one effective queue contract.

---

## Phase 4: User Story 2 - Deploy Homogeneous or Mixed Runtime Services (Priority: P2)

**Goal**: Keep one image and configure runtime mode per service/container.

**Independent Test**: Validate Dockerfile and compose/runtime configuration surfaces expose all required CLIs and runtime env semantics.

### Tests for User Story 2

- [X] T012 [P] [US2] Add assertions for supported runtime values helper behavior in `tests/unit/workflows/test_worker_entrypoints.py` (DOC-REQ-004, DOC-REQ-006).

### Implementation for User Story 2

- [X] T013 [US2] Extend shared image CLI install/copy/verify paths to include Claude while preserving `speckit` in same Dockerfile in `api_service/Dockerfile` (DOC-REQ-001, DOC-REQ-002).
- [X] T014 [US2] Update compose worker env/queue defaults for single-queue runtime mode deployment in `docker-compose.yaml` (DOC-REQ-003, DOC-REQ-007, DOC-REQ-008, DOC-REQ-010).

**Checkpoint**: Shared image and worker service configuration support homogeneous/mixed runtime deployments from one queue.

---

## Phase 5: User Story 3 - Enforce Safe Runtime and Tooling Contracts (Priority: P3)

**Goal**: Ensure startup health checks and observability alignment across runtimes.

**Independent Test**: Confirm startup health checks include required CLIs and unit tests verify queue/runtime contracts.

### Tests for User Story 3

- [X] T015 [P] [US3] Add worker startup tests for runtime/queue metadata behavior in `tests/unit/workflows/test_worker_entrypoints.py` and `tests/unit/workflows/test_celeryconfig.py` (DOC-REQ-009, DOC-REQ-011).
- [X] T016 [P] [US3] Add regression test that legacy queue envs still resolve during migration compatibility in `tests/unit/config/test_settings.py` (DOC-REQ-010).

### Implementation for User Story 3

- [X] T017 [US3] Enforce startup CLI checks for `codex`, `gemini`, `claude`, and `speckit` in worker entrypoints (`celery_worker/speckit_worker.py`, `celery_worker/gemini_worker.py`) (DOC-REQ-001, DOC-REQ-009).
- [X] T018 [US3] Ensure runtime-neutral queue/task contract docs remain aligned with implementation in `specs/018-unified-cli-queue/contracts/requirements-traceability.md` and `specs/018-unified-cli-queue/quickstart.md` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-011, DOC-REQ-012).

**Checkpoint**: Startup health and observability expectations are codified and validated.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, traceability closure, and scope proof.

- [X] T019 [P] Reconcile `DOC-REQ-*` coverage against final implementation and validation tasks in `specs/018-unified-cli-queue/contracts/requirements-traceability.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012).
- [X] T020 Run unit validation via `./tools/test_unit.sh` (fails in local environment: `python3 -m pytest` missing) (DOC-REQ-009, DOC-REQ-012).
- [X] T021 Run manual implementation scope validation for tasks and diff (repository lacks `.specify/scripts/bash/validate-implementation-scope.sh`) and record outcome in implementation report (manual gate PASS with noted unit-test environment blocker) (DOC-REQ-012).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phases 3/4/5 -> Phase 6.
- User stories depend on foundational tasks T004-T007.

### User Story Dependencies

- US1 provides the single-queue runtime baseline and should ship first.
- US2 depends on US1 queue/runtime baseline for deploy topology alignment.
- US3 depends on US1/US2 startup/runtime behavior for health and observability hardening.

### Parallel Opportunities

- T003 can run in parallel with T002 after setup starts.
- T005, T006, and T007 are parallelizable foundational tasks once settings changes begin.
- T008 and T009 are parallelizable US1 tests.
- T012 is parallelizable with T013/T014 once runtime-mode helpers are in place.
- T015 and T016 are parallelizable US3 validation tasks.

---

## Implementation Strategy

### MVP First (US1)

1. Complete foundational queue/runtime settings and worker boot validations.
2. Validate one effective queue and runtime mode enforcement with tests.
3. Confirm runtime-neutral contract behavior before expanding image/compose topology.

### Incremental Delivery

1. Add Claude CLI to shared image while preserving Speckit co-location.
2. Align compose/runtime env for mixed or homogeneous fleets.
3. Finalize startup health checks and telemetry-facing runtime metadata.

### Runtime Scope Commitments

- Production runtime files must change in `api_service/`, `moonmind/`, `celery_worker/`, and `docker-compose.yaml`.
- Validation includes new/updated unit tests and execution via `./tools/test_unit.sh`.
