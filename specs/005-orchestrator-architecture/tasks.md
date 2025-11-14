# Tasks: MoonMind Orchestrator Implementation

**Input**: Design documents from `/specs/005-orchestrator-architecture/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md, contracts/

**Tests**: Compose-based integration tests, Celery step unit tests, and orchestrator API contract tests are required to confirm patch/build/restart automation, artifact retention, and approval enforcement per the spec.

**Organization**: Tasks are grouped by user story so each increment is independently testable.

## Format: `[ID] [P?] [Story] Description`
- `[P]` indicates the task can run in parallel with others in the same phase.
- `[US#]` labels tasks that belong to a specific user story.
- All tasks cite concrete file paths derived from the implementation plan.

---

## Phase 1: Setup (Shared Infrastructure)
**Purpose**: Establish configuration scaffolding and container build context for the orchestrator service before wiring application code.

- [ ] T001 Add mm-orchestrator environment defaults (queue names, StatsD host/port, artifact root) to `.env-template`, `.env.vllm-template`, and `config.toml` so services share the same knobs.
- [ ] T002 Update `docker-compose.yaml` and `docker-compose.job.yaml` to define the `orchestrator` service with `/workspace` and `/var/run/docker.sock` mounts, broker/result backend env vars, and dependency ordering with rabbitmq/api.
- [ ] T003 Create the container context in `services/orchestrator/Dockerfile`, `services/orchestrator/requirements.txt`, and `services/orchestrator/entrypoint.sh` to install Python 3.11, Celery 5.4, compose CLI, and bootstrap the dedicated worker process.

---

## Phase 2: Foundational (Blocking Prerequisites)
**Purpose**: Provide database schema, serialization, persistence utilities, and Celery configuration that all user stories rely on. ⚠️ Do not start story work until these tasks finish.

- [ ] T004 Create an Alembic migration `api_service/migrations/versions/<timestamp>_add_orchestrator_tables.py` that adds `orchestrator_runs`, `orchestrator_action_plans`, `orchestrator_run_artifacts`, `approval_gates`, and links into `spec_workflow_task_states` per `data-model.md`.
- [ ] T005 Extend SQLAlchemy models and enums in `api_service/db/models.py` (and related `moonmind/workflows/speckit_celery/models.py` if reused) to represent OrchestratorRun, ActionPlan, RunArtifact, ApprovalGate, and approval/status fields consumed by Celery state tracking.
- [ ] T006 Add orchestrator request/response schemas in `moonmind/schemas/workflow_models.py` plus API response plumbing in `api_service/api/schemas.py` so routers and workers share typed payloads.
- [ ] T007 Implement persistence helpers and artifact storage utilities in `moonmind/workflows/orchestrator/repositories.py` and `moonmind/workflows/orchestrator/storage.py` to create runs, snapshot plan steps, and manage `var/artifacts/spec_workflows/<run_id>/`.
- [ ] T008 Configure the Celery app + instrumentation baseline by adding `moonmind/workflows/orchestrator/celeryconfig.py` and `moonmind/workflows/orchestrator/metrics.py`, then updating `services/orchestrator/entrypoint.sh` to load the config and StatsD hooks.

**Checkpoint**: Database, schemas, repositories, and worker scaffolding are ready for user stories.

---

## Phase 3: User Story 1 – Autonomous Fix Run (Priority: P1) 🎯 MVP
**Goal**: Accept an instruction, plan patch/build/restart steps for a target compose service, and execute the chain while storing artifacts.
**Independent Test**: Trigger a failing service locally, POST `/orchestrator/runs`, and verify the run produces a diff, rebuilds only the target service, restarts it, and stores verify logs/artifacts end-to-end.

### Tests for User Story 1
- [ ] T014 [P] [US1] Add a compose-based integration test `tests/integration/orchestrator/test_autonomous_run.py` that simulates a dependency fix, asserts `analyze→patch→build→restart→verify` log entries, and checks artifacts under `var/artifacts/spec_workflows/<run_id>/`.

### Implementation for User Story 1
- [ ] T009 [P] [US1] Define service metadata (allow-listed files, compose service names, health URLs) in `moonmind/workflows/orchestrator/service_profiles.py` so instructions can be validated up front.
- [ ] T010 [US1] Implement ActionPlan generation logic in `moonmind/workflows/orchestrator/action_plan.py` that expands instructions into analyze/patch/build/restart/verify steps with parameters.
- [ ] T011 [US1] Build the patch/build/restart command runner in `moonmind/workflows/orchestrator/command_runner.py`, including allow-list enforcement, compose invocations, and artifact writers for diffs/build logs.
- [ ] T012 [US1] Implement Celery step tasks and chaining in `moonmind/workflows/orchestrator/tasks.py` to execute the ActionPlan sequentially, update run status, and emit repository events.
- [ ] T013 [US1] Add the `POST /orchestrator/runs` endpoint plus router registration inside `api_service/api/routers/orchestrator.py` (and `api_service/main.py`) to validate requests, create OrchestratorRun rows, and enqueue the Celery chain.

**Checkpoint**: Operators can queue autonomous fix runs that patch/build/restart services and persist artifacts without manual Docker commands.

---

## Phase 4: User Story 2 – Run Visibility & Audit (Priority: P2)
**Goal**: Provide APIs and instrumentation so operators can inspect run state, artifacts, and metrics while the chain executes.
**Independent Test**: Start a run, poll the listing/detail endpoints, and confirm each plan step exposes timestamps, messages, artifact links, and StatsD counters while failures show actionable errors.

### Tests for User Story 2
- [ ] T020 [P] [US2] Add orchestrator API/serializer tests in `tests/contract/test_orchestrator_api.py` and repository unit tests in `tests/unit/workflows/test_orchestrator_repository.py` covering run list/detail/artifact responses and filtering.

### Implementation for User Story 2
- [ ] T015 [P] [US2] Implement run listing/detail queries plus serializer helpers inside `moonmind/workflows/orchestrator/repositories.py` and `moonmind/workflows/orchestrator/serializers.py`, including pagination/filter options.
- [ ] T016 [US2] Add `GET /orchestrator/runs` and `GET /orchestrator/runs/{run_id}` handlers in `api_service/api/routers/orchestrator.py` that call the new repository methods and enforce auth/query validation.
- [ ] T017 [P] [US2] Implement `GET /orchestrator/runs/{run_id}/artifacts` in the same router to expose `RunArtifact` metadata and signed paths to files under `var/artifacts/spec_workflows/<run_id>/`.
- [ ] T018 [US2] Enhance `moonmind/workflows/orchestrator/tasks.py` so each Celery step writes `spec_workflow_task_states` rows with timestamps, Celery IDs, messages, and artifact references per `data-model.md`.
- [ ] T019 [US2] Emit StatsD counters/timers for run lifecycle events inside `moonmind/workflows/orchestrator/metrics.py` and hook the calls into step transitions so monitoring reflects throughput and latency.

**Checkpoint**: Operators and observability tooling can watch orchestrator runs progress with full artifact and metric trails.

---

## Phase 5: User Story 3 – Policy-Governed Rollback & Approvals (Priority: P3)
**Goal**: Enforce approval gates before riskier edits, perform rollbacks on failed verifications, and expose retry/approval APIs.
**Independent Test**: Configure a protected service, start a run without approval (expect `awaiting_approval`), provide approval to resume, then induce a verify failure to confirm rollback, artifact capture, and retry support.

### Tests for User Story 3
- [ ] T025 [P] [US3] Add integration coverage in `tests/integration/orchestrator/test_policy_and_rollback.py` proving approval enforcement, rollback artifacts, and retry flows, plus contract assertions for approval/retry endpoints.

### Implementation for User Story 3
- [ ] T021 [US3] Implement approval policy resolution and validation helpers in `moonmind/workflows/orchestrator/policies.py`, and enforce them in run creation so protected services block before patching.
- [ ] T022 [US3] Add the `POST /orchestrator/runs/{run_id}/approvals` handler in `api_service/api/routers/orchestrator.py` plus repository updates to persist approval tokens, approver metadata, and unblock pending runs.
- [ ] T023 [US3] Extend `moonmind/workflows/orchestrator/command_runner.py` and `moonmind/workflows/orchestrator/tasks.py` with rollback execution (git revert/file restore, compose restart, rollback.log artifact) triggered on verify failure.
- [ ] T024 [US3] Implement `POST /orchestrator/runs/{run_id}/retry` in the router and Celery entry points so operators can resume from a stored plan step or queue a fresh chain that reuses artifacts.

**Checkpoint**: Approval-sensitive services are protected, failures roll back automatically, and operators can approve or retry runs confidently.

---

## Phase 6: Polish & Cross-Cutting Concerns
**Purpose**: Finalize documentation, CI, and hardening across user stories.

- [ ] T026 [P] Document orchestrator operations, approval flows, and StatsD dashboards in `docs/OrchestratorArchitecture.md`, `docs/SpecKitAutomation.md`, and refresh `specs/005-orchestrator-architecture/quickstart.md`.
- [ ] T027 Wire orchestrator integration jobs into CI by updating `docker-compose.test.yaml` and `.github/workflows/pytest-unit-tests.yml` so the new tests run in automation.
- [ ] T028 [P] Add log redaction + secret scrubbing hooks for orchestrator outputs inside `moonmind/workflows/orchestrator/command_runner.py` and `moonmind/utils/logging.py` to keep artifacts audit-safe.

---

## Dependencies & Execution Order
- **Setup (Phase 1)** must complete before schema or worker code can compile.
- **Foundational (Phase 2)** depends on Setup settings and blocks all user story phases.
- **User Story 1 (Phase 3)** starts after foundational work; it delivers the MVP experience.
- **User Story 2 (Phase 4)** depends on US1 because it surfaces states/artifacts generated there.
- **User Story 3 (Phase 5)** depends on US1/US2 for base run execution and visibility before layering approvals/rollback.
- **Polish (Phase 6)** runs last once desired stories are complete.

### User Story Dependency Graph
`US1 (Autonomous Fix Run) ──▶ US2 (Run Visibility & Audit) ──▶ US3 (Policy-Governed Rollback & Approvals)`

---

## Parallel Execution Examples

### User Story 1 – Autonomous Fix Run
```
Task: T009 [P] [US1] service_profiles.py metadata
Task: T011 [US1] command_runner.py compose executor
Task: T014 [P] [US1] integration test in tests/integration/orchestrator/test_autonomous_run.py
```

### User Story 2 – Run Visibility & Audit
```
Task: T015 [P] [US2] repositories/serializers for run listings
Task: T017 [P] [US2] artifacts endpoint in api_service/api/routers/orchestrator.py
Task: T020 [P] [US2] contract + unit tests for visibility APIs
```

### User Story 3 – Policy-Governed Rollback & Approvals
```
Task: T021 [US3] policies.py approval enforcement
Task: T023 [US3] rollback logic in command_runner.py and tasks.py
Task: T025 [P] [US3] integration tests covering approval + rollback
```

---

## Implementation Strategy
- **MVP (US1)**: Complete Phases 1–3 to queue and execute autonomous fix runs with artifact capture; validate via the US1 integration test before moving on.
- **Incremental Delivery**: After MVP, layer in US2 visibility endpoints/tests so audit data is available without affecting run execution, then add US3 approval/rollback to protect sensitive services.
- **Parallelization**: Once Foundational tasks land, separate contributors can tackle US1 planning/command code, US2 API visibility, and US3 policy logic concurrently using the parallel task examples while coordinating on shared files.
- **Verification**: After each phase, run the quickstart Compose stack (`docker compose up rabbitmq celery-worker api orchestrator`) and targeted tests to confirm the feature slice is independently shippable.
