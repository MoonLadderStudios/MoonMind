# Tasks: Generic Task Container Runner

**Input**: Design documents from `specs/020-generic-container-runner/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/task-container-contract.md`

## Phase 1: Setup

**Purpose**: Establish feature scaffolding and test targets before implementation.

- [X] T001 Create feature task fixtures for container payload samples in `tests/unit/workflows/agent_queue/test_task_contract.py`
- [X] T002 Add worker container execution test scaffolding utilities in `tests/unit/agents/codex_worker/test_worker.py`

---

## Phase 2: Foundational

**Purpose**: Add canonical payload support that all user stories depend on.

- [X] T003 Add `task.container` normalization and validation helpers in `moonmind/workflows/agent_queue/task_contract.py`
- [X] T004 Update canonical required capability derivation so `task.container.enabled=true` requires `docker` in `moonmind/workflows/agent_queue/task_contract.py`
- [X] T005 Add/extend unit tests for container payload normalization and capability behavior in `tests/unit/workflows/agent_queue/test_task_contract.py`

**Checkpoint**: Canonical task payloads can express validated container execution requests.

---

## Phase 3: User Story 1 - Run Arbitrary Task Containers (Priority: P1)

**Goal**: Execute arbitrary image + command in worker execute stage when `task.container.enabled=true`.

**Independent Test**: Submit a canonical task payload with `task.container` and verify worker runs container path, produces artifacts, and reports terminal status from container exit.

- [X] T006 [US1] Add container execution config fields (`docker host/workspace mount strategy`) to `moonmind/agents/codex_worker/worker.py`
- [X] T007 [US1] Implement container payload extraction/validation inside execute stage in `moonmind/agents/codex_worker/worker.py`
- [X] T008 [US1] Implement `docker run` command construction (image, arbitrary command, workdir, env, labels, mounts) in `moonmind/agents/codex_worker/worker.py`
- [X] T009 [US1] Implement container execution result metadata artifact generation (`container/metadata/run.json`) in `moonmind/agents/codex_worker/worker.py`
- [X] T010 [US1] Add unit tests for successful and failed container execution path in `tests/unit/agents/codex_worker/test_worker.py`

---

## Phase 4: User Story 2 - Switch Toolchains Per Repository and Per Task (Priority: P2)

**Goal**: Ensure image/command are task-driven and not runtime hardcoded.

**Independent Test**: Validate two tasks with different images/commands produce distinct docker invocation payloads through same worker code path.

- [X] T011 [US2] Implement task-driven image/command selection and optional pull behavior in `moonmind/agents/codex_worker/worker.py`
- [X] T012 [US2] Add unit tests covering multiple image/command combinations in `tests/unit/agents/codex_worker/test_worker.py`
- [X] T013 [US2] Wire worker Docker runtime defaults (`DOCKER_HOST`, `docker` capability baseline) in `docker-compose.yaml`

---

## Phase 5: User Story 3 - Preserve Queue Lifecycle Guarantees (Priority: P3)

**Goal**: Ensure events, timeout handling, cleanup, and artifact upload remain reliable.

**Independent Test**: Simulate timeout and verify container stop attempt, failed terminal status, and stage events/artifacts are emitted.

- [X] T014 [US3] Emit container lifecycle events (`moonmind.task.container.started`/`finished`) in `moonmind/agents/codex_worker/worker.py`
- [X] T015 [US3] Implement timeout + best-effort stop cleanup for running container tasks in `moonmind/agents/codex_worker/worker.py`
- [X] T016 [US3] Add unit tests for timeout cleanup and lifecycle event payloads in `tests/unit/agents/codex_worker/test_worker.py`

---

## Phase 6: Polish & Validation

**Purpose**: Final consistency checks, regression coverage, and scope validation.

- [X] T017 Run/adjust unit tests for worker + task contract changes in `tests/unit/agents/codex_worker/test_worker.py` and `tests/unit/workflows/agent_queue/test_task_contract.py`
- [X] T018 Run project unit suite via `./tools/test_unit.sh`
- [X] T019 Validate runtime scope gates using `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

---

## Dependencies & Order

1. Setup (Phase 1) before Foundational (Phase 2).
2. Foundational tasks (T003-T005) block all user stories.
3. US1 (Phase 3) should complete before US2/US3 to establish container runtime path.
4. US2 and US3 can proceed after US1 core execution path exists.
5. Polish tasks run last.

## Parallel Opportunities

- T005 can run in parallel with late-stage implementation once normalization APIs are stable.
- T012 and T016 can be implemented in parallel after their target runtime behaviors are coded.
- T017 can run in parallel with residual cleanup once test files settle.

## Implementation Strategy

1. Deliver MVP by completing Phase 2 + Phase 3 (validated container execution path).
2. Add repo/task-switching and compose runtime wiring in Phase 4.
3. Add timeout/event robustness in Phase 5.
4. Complete full regression and scope gates in Phase 6.
