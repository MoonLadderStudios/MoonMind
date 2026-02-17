# Tasks: Task Steps System

**Input**: Design documents from `/specs/022-task-steps-system/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required validation coverage across task contract, worker execution/cancellation, and dashboard submission behavior.

**Organization**: Tasks grouped by user story with explicit `DOC-REQ-*` coverage and independent testability.

## Format: `[ID] [P?] [Story] Description`

- `[P]`: Can run in parallel (different files, no dependencies)
- `[Story]`: User story label (`[US1]`, `[US2]`, `[US3]`)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Scaffold feature contracts and planning traceability.

- [X] T001 Add/refresh task-step contract docs in `specs/022-task-steps-system/contracts/task-steps.openapi.yaml` and `specs/022-task-steps-system/contracts/requirements-traceability.md` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-015)
- [X] T002 [P] Add quickstart validation commands and smoke payload examples in `specs/022-task-steps-system/quickstart.md` (DOC-REQ-015)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement canonical payload + worker primitives used by all stories.

- [X] T003 Extend task contract models for `task.steps` and strict step schema validation in `moonmind/workflows/agent_queue/task_contract.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)
- [X] T004 [P] Extend task capability derivation to include task-level and step-level skill requirements in `moonmind/workflows/agent_queue/task_contract.py` (DOC-REQ-013)
- [X] T005 Implement first-rollout explicit rejection for `task.steps` with `task.container.enabled=true` in `moonmind/workflows/agent_queue/task_contract.py` and worker guards in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-014)
- [X] T006 Add worker step-resolution and effective skill precedence helpers in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-006)

**Checkpoint**: Canonical payload and worker primitives support validated step metadata and policy guards.

---

## Phase 3: User Story 1 - Execute multi-step tasks in one job (Priority: P1) 🎯 MVP

**Goal**: One queue task executes N ordered step invocations in one claim/workspace with one publish decision.

**Independent Test**: Submit canonical task with multiple steps and assert ordered runtime invocations + publish once at end.

### Tests for User Story 1

- [X] T007 [P] [US1] Add task-contract tests for `task.steps` normalization, implicit single-step fallback, forbidden step-level overrides, and explicit container+steps rejection in `tests/unit/workflows/agent_queue/test_task_contract.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-014, DOC-REQ-015)
- [X] T008 [P] [US1] Add worker tests validating stage-contract preservation, sequential step execution count/order, effective skill precedence, first-failure short-circuit, and single publish gating in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-006, DOC-REQ-008, DOC-REQ-009, DOC-REQ-015)

### Implementation for User Story 1

- [X] T009 [US1] Implement execute-stage step loop and stage-contract preservation in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-008)
- [X] T010 [US1] Ensure publish stage only runs once after all steps succeed in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-009)
- [X] T011 [US1] Preserve objective-required semantics and per-step prompt composition (`objective + step instructions`) in `moonmind/agents/codex_worker/worker.py` and `moonmind/workflows/agent_queue/task_contract.py` (DOC-REQ-005, DOC-REQ-006)

**Checkpoint**: P1 behavior complete and independently testable.

---

## Phase 4: User Story 2 - Observe and cancel step execution safely (Priority: P2)

**Goal**: Operators get step lifecycle observability and cancellation remains cooperative and deterministic.

**Independent Test**: Multi-step run emits required step events/artifacts and cancellation acknowledgement stops remaining steps without terminal success/failure.

### Tests for User Story 2

- [X] T012 [P] [US2] Add worker tests for `task.steps.plan`, per-step lifecycle events (`started`, `finished`, `failed`) payloads, and multi-skill materialization selection coverage in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-007, DOC-REQ-011, DOC-REQ-015)
- [X] T013 [P] [US2] Add worker tests for cancellation during step execution and boundary checks before next step in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-010, DOC-REQ-015)
- [X] T014 [P] [US2] Add worker tests for per-step artifact path emission (`logs/steps`, `patches/steps`) in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-011, DOC-REQ-015)

### Implementation for User Story 2

- [X] T015 [US2] Emit step plan/start/finish/fail events and persist step-level artifacts in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-011)
- [X] T016 [US2] Preserve cancellation semantics during step loop and cancel-ack behavior in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-010)
- [X] T017 [US2] Materialize union of referenced non-auto skills across task-level and step-level selections in prepare stage of `moonmind/agents/codex_worker/worker.py` (DOC-REQ-007)

**Checkpoint**: Operators can observe and safely cancel multi-step execution.

---

## Phase 5: User Story 3 - Author steps in queue UI (Priority: P3)

**Goal**: Queue submit form can add/edit/reorder steps and emit canonical payload preserving defaults.

**Independent Test**: Submit UI-authored task with steps and verify payload includes ordered `task.steps` and default publish mode `pr`.

### Tests for User Story 3

- [X] T018 [P] [US3] Add dashboard-related validation for runtime config/publish defaults in `tests/unit/api/routers/test_task_dashboard_view_model.py` and payload-focused assertions in worker/contract tests where applicable (DOC-REQ-012, DOC-REQ-015)
- [X] T019 [P] [US3] Add contract tests for step-skill required capability unions impacting `requiredCapabilities` in `tests/unit/workflows/agent_queue/test_task_contract.py` (DOC-REQ-013, DOC-REQ-015)

### Implementation for User Story 3

- [X] T020 [US3] Add queue new form steps editor (add/remove/reorder) and canonical payload emission in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-012)
- [X] T021 [US3] Keep queue new publish default as `pr` and derive required capabilities with step-skill requirements in `api_service/static/task_dashboard/dashboard.js` plus backend canonical normalization in `moonmind/workflows/agent_queue/task_contract.py` (DOC-REQ-012, DOC-REQ-013)

**Checkpoint**: Queue UI supports practical step authoring with canonical payload behavior.

---

## Phase 6: Polish & Cross-Cutting Validation

**Purpose**: Run full validation and ensure scope/traceability gates pass.

- [X] T022 Run targeted and full unit regressions using `./tools/test_unit.sh` and capture results in `specs/022-task-steps-system/quickstart.md` (DOC-REQ-015)
- [X] T023 Run scope gates via `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` (DOC-REQ-015)
- [X] T024 Verify each `DOC-REQ-001..015` is represented in completed tasks and final implementation notes in `specs/022-task-steps-system/tasks.md` and `specs/022-task-steps-system/contracts/requirements-traceability.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015)

---

## Dependencies & Execution Order

- Phase 1 -> Phase 2 required before user stories.
- US1 depends on foundational task contract + step loop primitives.
- US2 depends on US1 execute loop for event/cancellation integration.
- US3 depends on contract semantics from Phase 2 and can proceed mostly in parallel with US2 once payload schema stabilizes.
- Polish tasks run after all implementation/test changes are complete.

## Parallel Opportunities

- T002 can run parallel to T001.
- T004 can run parallel to T003 once step schema exists.
- T007 and T008 can run in parallel with T009/T010 implementation iteration.
- T012/T013/T014 are parallel once step-loop scaffold is in place.
- T018 and T019 are parallel validation tasks while T020/T021 progress.

## Implementation Strategy

1. Land canonical `task.steps` contract + capability derivation and unsupported container guardrails.
2. Implement worker step resolution + execute loop + event/artifact emission + cancellation boundaries.
3. Extend queue submit UI for steps authoring and canonical payload emission.
4. Run full validation gates and mark task completion with DOC-REQ traceability.
