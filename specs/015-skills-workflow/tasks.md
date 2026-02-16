# Tasks: Skills-First Workflow Umbrella

**Input**: Design documents from `/specs/015-skills-workflow/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish migration scaffolding for skills-first workflow work.

- [ ] T001 Verify feature artifacts exist and are internally consistent in `specs/015-skills-workflow/`.
- [ ] T002 Create `moonmind/workflows/skills/__init__.py` package scaffold for skills adapters.
- [ ] T003 [P] Create skeleton modules `moonmind/workflows/skills/contracts.py`, `moonmind/workflows/skills/registry.py`, `moonmind/workflows/skills/runner.py`, and `moonmind/workflows/skills/speckit_adapter.py`.
- [ ] T004 [P] Add baseline unit test scaffold in `tests/unit/workflows/test_skills_runner.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add configuration and orchestration primitives used by all stories.

- [ ] T005 Extend workflow settings with skills-first flags and per-stage override fields in `moonmind/config/settings.py`.
- [ ] T006 Implement skill registry allowlist and stage mapping logic in `moonmind/workflows/skills/registry.py`.
- [ ] T007 [P] Implement stage contract dataclasses/validation in `moonmind/workflows/skills/contracts.py`.
- [ ] T008 [P] Implement skills runner execution and fallback orchestration in `moonmind/workflows/skills/runner.py`.
- [ ] T009 Integrate skills runner entrypoints into workflow orchestration path in `moonmind/workflows/speckit_celery/tasks.py`.
- [ ] T010 Preserve backward-compatible orchestration sequencing while recording execution-path metadata in `moonmind/workflows/speckit_celery/orchestrator.py` and `moonmind/workflows/speckit_celery/models.py`.

**Checkpoint**: Skills-first execution primitives and compatibility wiring are in place.

---

## Phase 3: User Story 1 - Fast Worker Launch with Authenticated Codex + Gemini Embeddings (Priority: P1) 🎯 MVP

**Goal**: Operators can launch workers quickly with persistent Codex auth and Google Gemini embedding defaults.

**Independent Test**: Run quickstart from clean env and verify startup preflight and embedding defaults.

### Tests for User Story 1

- [ ] T011 [P] [US1] Add worker entrypoint startup validation tests for Speckit presence, Codex preflight enforcement, and actionable failures in `tests/unit/workflows/test_worker_entrypoints.py`.
- [ ] T012 [P] [US1] Add runtime config tests for embedding provider/model resolution and missing credential failures in `tests/unit/workflows/test_spec_automation_env.py`.

### Implementation for User Story 1

- [ ] T013 [US1] Extend worker startup checks for always-on Speckit capability and preflight diagnostics in `celery_worker/speckit_worker.py` and `celery_worker/gemini_worker.py`.
- [ ] T014 [US1] Align compose environment defaults and queue commands for Codex + Gemini fast path in `docker-compose.yaml`.
- [ ] T015 [US1] Update quickstart/runtime guidance in `README.md` for Codex auth volume setup and Gemini embedding defaults.
- [ ] T016 [US1] Update worker architecture/operator docs in `docs/CodexCliWorkers.md` and `docs/SpecKitAutomationInstructions.md`.

**Checkpoint**: Fast-path startup and operator guidance are deterministic and test-covered.

---

## Phase 4: User Story 2 - Skills-First Execution with Speckit Always Available (Priority: P1)

**Goal**: Stages execute through skills-first contract while preserving Speckit defaults and fallback behavior.

**Independent Test**: Execute stage flows with default and overridden skills; verify fallback and metadata behavior.

### Tests for User Story 2

- [ ] T017 [P] [US2] Add unit tests for stage-to-skill resolution rules and allowlist enforcement in `tests/unit/workflows/test_skills_runner.py`.
- [ ] T018 [P] [US2] Add unit tests covering skill failure -> direct fallback transitions in `tests/unit/workflows/test_tasks.py`.
- [ ] T019 [P] [US2] Add API compatibility regression tests for existing workflow routes in `tests/contract/test_workflow_api.py`.

### Implementation for User Story 2

- [ ] T020 [US2] Implement Speckit adapter bindings for `specify|plan|tasks|analyze|implement` stages in `moonmind/workflows/skills/speckit_adapter.py`.
- [ ] T021 [US2] Wire skills-first stage dispatch into `submit_codex_job`/stage orchestration path in `moonmind/workflows/speckit_celery/tasks.py`.
- [ ] T022 [US2] Surface selected skill and execution path metadata in workflow serialization/routes in `moonmind/workflows/speckit_celery/serializers.py` and `api_service/api/routers/workflows.py`.

**Checkpoint**: Skills-first execution is functional with fallback and route compatibility.

---

## Phase 5: User Story 3 - Progressive Rollout with Parity and Drift Controls (Priority: P2)

**Goal**: Rollout is controllable and parity-protected.

**Independent Test**: Enable shadow/canary flags and run parity fixtures without breaking baseline behavior.

### Tests for User Story 3

- [ ] T023 [P] [US3] Add tests for shadow/canary gating decisions in `tests/unit/workflows/test_skills_runner.py`.
- [ ] T024 [P] [US3] Add parity fixture comparison tests for skills vs direct outputs in `tests/unit/workflows/test_tasks.py`.

### Implementation for User Story 3

- [ ] T025 [US3] Implement rollout flag evaluation (global/per-stage, shadow, canary) in `moonmind/workflows/skills/registry.py` and `moonmind/config/settings.py`.
- [ ] T026 [US3] Emit structured metrics/log fields for stage, skill id, execution path, and duration in `moonmind/workflows/speckit_celery/tasks.py`.
- [ ] T027 [US3] Add parity drift reporting hooks and run-scope summaries in `moonmind/workflows/speckit_celery/services.py`.

**Checkpoint**: Rollout controls and drift checks are operational.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final consistency, docs alignment, and validation.

- [ ] T028 [P] Reconcile docs/spec drift and ensure fast-path wording is consistent across `README.md`, `docs/CodexCliWorkers.md`, and `specs/015-skills-workflow/quickstart.md`.
- [ ] T029 [P] Verify contracts remain aligned with implementation in `specs/015-skills-workflow/contracts/skills-stage-contract.md` and `specs/015-skills-workflow/contracts/compose-fast-path.md`.
- [ ] T030 Run unit validation via `./tools/test_unit.sh`.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phases 3/4/5 -> Phase 6.
- User stories require foundational tasks T005-T010.

### User Story Dependencies

- US1 can start after foundational completion and is the MVP operator outcome.
- US2 depends on foundational skills runner and settings wiring.
- US3 depends on US2 execution metadata and stage dispatch behavior.

### Parallel Opportunities

- T003 and T004 can run in parallel.
- T007 and T008 can run in parallel after T006 starts.
- US1 tests (T011/T012), US2 tests (T017/T018/T019), and US3 tests (T023/T024) are parallelizable.
- T028 and T029 can run in parallel during polish.

---

## Implementation Strategy

### MVP First (US1)

1. Complete setup and foundational wiring.
2. Deliver deterministic fast-path startup/auth/embedding operator flow.
3. Validate with targeted tests before expanding orchestration behavior.

### Incremental Delivery

1. Introduce skills-first stage dispatch with Speckit defaults and fallback (US2).
2. Add rollout controls and parity/drift observability (US3).
3. Finalize docs and run unit validation.

### Runtime Scope Commitments

- Production runtime files will be modified under `moonmind/workflows/`, `moonmind/config/`, `celery_worker/`, and `api_service/api/routers/`.
- Compose/docs surfaces will be updated in `docker-compose.yaml`, `README.md`, and `docs/`.
- Validation will include `./tools/test_unit.sh` and compatibility-focused workflow tests.
