# Tasks: Worker Self-Heal System

**Input**: Design documents from `/specs/034-worker-self-heal/`
**Prerequisites**: plan.md, spec.md, data-model.md, research.md, quickstart.md, docs/WorkerSelfHealSystem.md
**Tests**: Run `./tools/test_unit.sh` (optionally `-k self_heal`) and `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` before publish.
**Organization**: Tasks are grouped by user story with Setup → Foundational prerequisites, followed by a Polish phase.

## Format: `[ID] [P?] [Story] Description`

- `[P]` indicates the task can run in parallel (independent files/deps).
- `[Story]` is `[US1]`, `[US2]`, or `[US3]` for story-scoped work.
- Always include concrete file paths or scripts.

## Path Conventions

- Worker runtime: `moonmind/agents/codex_worker/`
- Queue services: `moonmind/workflows/agent_queue/`
- API + dashboard: `api_service/`
- Tests: `tests/unit/...`
- Docs/specs: `docs/` and `specs/034-worker-self-heal/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Wire configuration + scaffolding so later phases can focus on behavior.

- [X] T001 Declare STEP_MAX_ATTEMPTS, STEP_TIMEOUT_SECONDS, STEP_IDLE_TIMEOUT_SECONDS, STEP_NO_PROGRESS_LIMIT, and JOB_SELF_HEAL_MAX_RESETS in `celery_worker/speckit_worker.py` and `config.toml` so SelfHealConfig enforces DOC-REQ-002 defaults.
- [X] T002 [P] Scaffold `moonmind/agents/codex_worker/self_heal.py` and update `moonmind/agents/codex_worker/__init__.py` to export SelfHealController dataclasses aligned with DOC-REQ-001 and DOC-REQ-002.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core helpers (signatures, metrics, artifact plumbing) that every story needs.

- [X] T003 Implement failure signature normalization + SecretRedactor hooks in `moonmind/agents/codex_worker/self_heal.py` so DOC-REQ-001, DOC-REQ-003, and DOC-REQ-012 data stays scrubbed.
- [X] T004 [P] Create `moonmind/agents/codex_worker/metrics.py` that wraps `moonmind/workflows/speckit_celery/metrics._MetricsEmitter` for task.self_heal counters/timers (DOC-REQ-011).
- [X] T005 [P] Extend `moonmind/workflows/agent_queue/storage.py` and `moonmind/agents/codex_worker/worker.py` to create/upload `state/self_heal/` + `state/steps/` artifacts next to patches per DOC-REQ-007.

---

## Phase 3: User Story 1 - Automatic Stuck-Step Recovery (Priority: P1) 🎯 MVP

**Goal**: Workers autonomously detect stuck steps, classify failures, and recover via bounded soft resets.

**Independent Test**: Inject a transient CLI hang and verify idle timeout → soft reset recovery without operator input (see spec).

### Implementation for User Story 1

- [ ] T006 [US1] Implement wall-clock (asyncio) + idle watchdogs and no-progress counters wired to `OutputChunkCallback` inside `moonmind/agents/codex_worker/self_heal.py` per DOC-REQ-001.
- [ ] T007 [P] [US1] Encode SelfHealStrategy selection (soft reset vs hard reset vs queue retry) with attempt budget decrements + failure-class heuristics inside `moonmind/agents/codex_worker/self_heal.py`, satisfying DOC-REQ-002 and DOC-REQ-003.
- [ ] T008 [US1] Wrap the step execution loop in `moonmind/agents/codex_worker/worker.py` with the controller so soft resets restart runtimes, cancel/pause/takeover checks run before each attempt, and exhausted retries surface as queue retries (DOC-REQ-004, DOC-REQ-008).
- [ ] T009 [P] [US1] Extend `_compose_step_instruction_for_runtime` and artifact writers in `moonmind/agents/codex_worker/worker.py` to emit the minimal retry context envelope plus `state/self_heal/attempt-*.json` metadata (DOC-REQ-006, DOC-REQ-010).
- [ ] T010 [P] [US1] Emit `task.step.attempt.*`/`task.self_heal.*` events and StatsD counters/timers via the new metrics adapter inside `moonmind/agents/codex_worker/worker.py` (DOC-REQ-010, DOC-REQ-011).
- [ ] T011 [P] [US1] Ensure failure signatures, retry prompts, and stored artifacts route through `SecretRedactor` before persistence in `moonmind/agents/codex_worker/self_heal.py` and `moonmind/agents/codex_worker/worker.py`, covering DOC-REQ-012.

### Validation for User Story 1

- [ ] T012 [US1] Expand `tests/unit/agents/codex_worker/test_worker.py` to simulate wall/idle/no-progress detections, budget exhaustion, and cancel/pause/takeover gating that verifies DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, and DOC-REQ-008.
- [ ] T013 [P] [US1] Add worker tests asserting soft reset retries, minimal retry context payloads, StatsD/event emission, and scrubbed artifacts in `tests/unit/agents/codex_worker/test_worker.py`, validating DOC-REQ-004, DOC-REQ-006, DOC-REQ-010, DOC-REQ-011, and DOC-REQ-012.

---

## Phase 4: User Story 2 - Deterministic Checkpoint Resume (Priority: P2)

**Goal**: Persist per-step checkpoints and replay them deterministically for hard resets or operator resumes.

**Independent Test**: Finish steps 1-3, force step 4 to fail repeatedly, trigger hard reset, and confirm replayed checkpoints rebuild workspace before resuming (see spec).

### Implementation for User Story 2

- [ ] T014 [US2] Persist `state/steps/step-XXXX.json` metadata next to each patch in `moonmind/agents/codex_worker/worker.py` and register uploads through `moonmind/workflows/agent_queue/storage.py`, satisfying DOC-REQ-007.
- [ ] T015 [P] [US2] Implement a HardResetWorkspaceBuilder in `moonmind/agents/codex_worker/self_heal.py` that reclones the baseline repo, replays recorded patches with hash validation, and reruns the failing step per DOC-REQ-005 and DOC-REQ-007.
- [ ] T016 [US2] Emit `task.resume.from_step` events with checkpoint metadata/diff hashes when hard reset or resume completes inside `moonmind/agents/codex_worker/worker.py`, covering DOC-REQ-005 and DOC-REQ-010.

### Validation for User Story 2

- [ ] T017 [US2] Add deterministic replay tests in `tests/unit/agents/codex_worker/test_worker.py` and `tests/unit/workflows/agent_queue/test_repositories.py` that cover StepCheckpointState writes, hash verification, and failure modes (DOC-REQ-005, DOC-REQ-007).
- [ ] T018 [P] [US2] Extend worker tests to simulate `resume_from_step` hard resets and assert `task.resume.from_step` events/artifacts in `tests/unit/agents/codex_worker/test_worker.py`, validating DOC-REQ-005 and DOC-REQ-010.

---

## Phase 5: User Story 3 - Operator-Controlled Recovery Actions (Priority: P3)

**Goal**: Allow operators to issue retry/hard-reset/resume commands via API/dashboard and have workers honor them.

**Independent Test**: Pause a task mid-retry, send `hard_reset_step` via control API, and confirm the worker resumes using the requested strategy once unpaused (see spec).

### Implementation for User Story 3

- [ ] T019 [US3] Update `api_service/api/routers/task_runs.py` (and related schemas) to accept `retry_step`, `hard_reset_step`, and `resume_from_step` actions with validation + audit logging per DOC-REQ-009.
- [ ] T020 [P] [US3] Extend `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/repositories.py` to persist `liveControl.recovery`, guard invalid stepIds, and emit control events per DOC-REQ-009.
- [ ] T021 [P] [US3] Update `moonmind/agents/codex_worker/worker.py` to parse `payload.liveControl.recovery`, inject operator overrides into the controller, and emit `task.control.recovery.*` acknowledgements satisfying DOC-REQ-009 and DOC-REQ-010.
- [ ] T022 [US3] Enhance `api_service/static/task_dashboard/dashboard.js` so dashboard users can issue Retry/Hard Reset/Resume commands and see pending recovery state (DOC-REQ-009).

### Validation for User Story 3

- [ ] T023 [US3] Add `tests/unit/api/routers/test_task_runs.py` coverage for the new recovery actions (validation errors, success payloads, audit logging) to validate DOC-REQ-009.
- [ ] T024 [P] [US3] Expand `tests/unit/workflows/agent_queue/test_service_hardening.py` and `tests/unit/workflows/agent_queue/test_repositories.py` to verify `liveControl.recovery` persistence plus `task.control.recovery.*` payloads per DOC-REQ-009 and DOC-REQ-010.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Align docs/runbooks and verify the full suite before shipping.

- [ ] T025 Update `docs/WorkerSelfHealSystem.md`, `specs/034-worker-self-heal/quickstart.md`, and `specs/034-worker-self-heal/contracts/requirements-traceability.md` with the implemented controller APIs, metrics, and smoke-test steps.
- [ ] T026 [P] Run `./tools/test_unit.sh` plus `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` and walk through the quickstart scenarios in `specs/034-worker-self-heal/quickstart.md` to capture release notes + log evidence.

---

## Dependencies & Execution Order

- Setup (T001-T002) must finish before foundational helpers can compile.
- Foundational (T003-T005) blocks every user story because timers, metrics, and artifact plumbing are required everywhere.
- User Story 1 (T006-T013) delivers the MVP; User Story 2 (T014-T018) depends on Setup/Foundational + US1 controller hooks; User Story 3 (T019-T024) depends on US1 controller APIs and US2 checkpoint metadata for resume actions.
- Polish (T025-T026) runs last after all user stories pass their validation tasks.

**Parallel Opportunities**
- T002/T004/T005 can run alongside documentation audits once T001 is done.
- Within US1, T007, T009, and T010 can run in parallel after T006 lands because they touch distinct helpers.
- US2 replay logic (T015) and event wiring (T016) can progress while US1 tests (T012-T013) run, as long as T005 is complete.
- US3 UI work (T022) can proceed in parallel with API/repository changes once the request schema in T019 stabilizes.

## Parallel Example: User Story 1

```bash
# Run watchers + strategy logic in parallel once T006 is merged
Task T007: Strategy selection in moonmind/agents/codex_worker/self_heal.py
Task T009: Minimal retry context + attempt artifacts in moonmind/agents/codex_worker/worker.py
Task T010: StatsD + event emission in moonmind/agents/codex_worker/worker.py
```

## Implementation Strategy

### MVP First (User Story 1 Only)
1. Complete Setup + Foundational phases.
2. Deliver US1 (T006-T013) to unlock bounded self-heal with soft resets.
3. Validate via the idle-timeout independent test before moving on.

### Incremental Delivery
1. After MVP, implement US2 (T014-T018) to add deterministic hard resets/resume.
2. Finish US3 (T019-T024) for operator control surfaces once replay data exists.
3. Close with Polish (T025-T026) and rerun the full test/integration suites.

### Parallel Team Strategy
- Team A: Worker controller + tests (US1).
- Team B: Checkpoint replay + storage (US2).
- Team C: API/service/dashboard controls (US3).
- Coordinate via StatsD/event schemas defined in T004/T010 to avoid drift.
