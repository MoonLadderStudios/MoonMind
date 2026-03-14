# Tasks: Agent Runtime Phase 1 Contracts

**Input**: Design documents from `/specs/072-agent-run-contracts/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Tests are required because this feature mandates runtime contract validation before completion.  
**Organization**: Tasks are grouped by user story with explicit `DOC-REQ-*` traceability.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only
- Every task includes a concrete file path or command

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present in `T001-T009`, `T011`, `T015-T016`, and `T018`.
- Runtime validation tasks are explicitly present in `T010`, `T013-T014`, `T017`, and `T020-T022`.
- Every `DOC-REQ-001` through `DOC-REQ-011` has at least one implementation task and one validation task in this file.
- Runtime-mode completion requires production runtime file changes plus automated validation execution.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish feature module scaffolding and package exports for contract implementation.

- [X] T001 Create contract schema module scaffold in `moonmind/schemas/agent_runtime_models.py` with canonical type declarations and `__all__` exports (DOC-REQ-001, DOC-REQ-011).
- [X] T002 [P] Create shared adapter interface scaffold in `moonmind/workflows/adapters/agent_adapter.py` with async method signatures for start/status/fetch_result/cancel (DOC-REQ-001, DOC-REQ-007, DOC-REQ-011).
- [X] T003 [P] Create external adapter scaffold in `moonmind/workflows/adapters/jules_agent_adapter.py` with Jules client dependency injection hooks (DOC-REQ-001, DOC-REQ-007, DOC-REQ-011).
- [X] T004 [P] Wire module exports in `moonmind/workflows/adapters/__init__.py` and `moonmind/schemas/__init__.py` for new contract surfaces (DOC-REQ-001, DOC-REQ-011).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement canonical contracts and adapter runtime integration before user-story-specific refinements.

**⚠️ CRITICAL**: No user story implementation should proceed before this phase completes.

- [X] T005 Implement `AgentExecutionRequest`, `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` validation logic in `moonmind/schemas/agent_runtime_models.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-010).
- [X] T006 [P] Implement `ManagedAgentAuthProfile` validation, per-profile concurrency/cooldown constraints, and credential-safe field rules in `moonmind/schemas/agent_runtime_models.py` (DOC-REQ-008, DOC-REQ-009).
- [X] T007 [P] Implement concrete `AgentAdapter` protocol semantics and typing contracts in `moonmind/workflows/adapters/agent_adapter.py` (DOC-REQ-007).
- [X] T008 [P] Implement `JulesAgentAdapter` mapping from Jules provider payloads into canonical handle/status/result contracts with start idempotency reuse (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-010).
- [X] T009 Integrate `TemporalJulesActivities` to reuse `JulesAgentAdapter` internally in `moonmind/workflows/temporal/activity_runtime.py` while preserving current activity outputs (DOC-REQ-001, DOC-REQ-007, DOC-REQ-010, DOC-REQ-011).

**Checkpoint**: Core contracts and shared adapter pathway are available for story-level validation.

---

## Phase 3: User Story 1 - Platform Defines Unified Agent-Run Contracts (Priority: P1) 🎯 MVP

**Goal**: Deliver validated canonical request/handle/status/result contract behavior.

**Independent Test**: Contract model tests pass for valid payloads, reject invalid payloads, and enforce terminal status semantics.

### Tests for User Story 1

- [X] T010 [P] [US1] Add contract model tests in `tests/unit/schemas/test_agent_runtime_models.py` covering required request fields, canonical status vocabulary, terminal-state semantics, and artifact-reference discipline (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-010, DOC-REQ-011).

### Implementation for User Story 1

- [X] T011 [US1] Refine canonical contract validators and serialization aliases in `moonmind/schemas/agent_runtime_models.py` to satisfy `T010` failures deterministically (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-010).

**Checkpoint**: User Story 1 is independently complete with passing schema tests.

---

## Phase 4: User Story 2 - Runtime Integrations Use One Adapter Interface (Priority: P1)

**Goal**: Deliver shared adapter behavior with a concrete Jules external adapter implementation.

**Independent Test**: Adapter tests pass for start/status/fetch_result/cancel mapping and idempotent start reuse.

### Tests for User Story 2

- [X] T012 [P] [US2] Add adapter behavior tests in `tests/unit/workflows/adapters/test_jules_agent_adapter.py` for canonical start/status/result/cancel mappings and provider metadata normalization (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-007, DOC-REQ-010, DOC-REQ-011).
- [X] T013 [P] [US2] Extend Jules activity-runtime integration tests in `tests/unit/workflows/temporal/test_activity_runtime.py` to verify canonical adapter-backed integration behavior remains compatible (DOC-REQ-001, DOC-REQ-007, DOC-REQ-011).

### Implementation for User Story 2

- [X] T014 [US2] Finalize shared adapter compatibility behavior and exports in `moonmind/workflows/adapters/agent_adapter.py` and `moonmind/workflows/adapters/jules_agent_adapter.py` based on failing tests from `T012` (DOC-REQ-007).
- [X] T015 [US2] Finalize Temporal Jules activity integration wiring and fallback compatibility in `moonmind/workflows/temporal/activity_runtime.py` based on failing tests from `T013` (DOC-REQ-001, DOC-REQ-007, DOC-REQ-010).

**Checkpoint**: User Story 2 is independently complete with adapter conformance validated.

---

## Phase 5: User Story 3 - Managed Auth Policy Is Contracted Safely (Priority: P2)

**Goal**: Deliver managed auth-profile contracts with per-profile policy safety and credential hygiene constraints.

**Independent Test**: Managed auth profile tests pass for valid payloads and fail for unsafe/invalid policy values.

### Tests for User Story 3

- [X] T016 [P] [US3] Extend managed auth-profile test coverage in `tests/unit/schemas/test_agent_runtime_models.py` for per-profile concurrency/cooldown rules and credential-field rejection (DOC-REQ-008, DOC-REQ-009, DOC-REQ-011).

### Implementation for User Story 3

- [X] T017 [US3] Finalize `ManagedAgentAuthProfile` validators and policy field normalization in `moonmind/schemas/agent_runtime_models.py` to satisfy `T016` failures (DOC-REQ-008, DOC-REQ-009).

**Checkpoint**: User Story 3 is independently complete with managed auth-policy contracts validated.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Complete traceability, run required validation commands, and enforce scope gates.

- [X] T018 [P] Update final implementation/validation mapping in `specs/072-agent-run-contracts/contracts/requirements-traceability.md` for `DOC-REQ-001` through `DOC-REQ-011`.
- [X] T019 Run focused validation suites with `./tools/test_unit.sh tests/unit/schemas/test_agent_runtime_models.py`, `./tools/test_unit.sh tests/unit/workflows/adapters/test_jules_agent_adapter.py`, and `./tools/test_unit.sh tests/unit/workflows/temporal/test_activity_runtime.py` (DOC-REQ-011).
- [X] T020 Run full regression validation with `./tools/test_unit.sh` (DOC-REQ-011).
- [X] T021 Run runtime scope tasks gate with `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` (DOC-REQ-011).
- [X] T022 Run runtime scope diff gate with `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` (DOC-REQ-011).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No prerequisites.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers MVP contracts.
- **Phase 4 (US2)**: Depends on Phase 2 and reuses US1 contract surfaces.
- **Phase 5 (US3)**: Depends on Phase 2 and can run alongside US2.
- **Phase 6 (Polish)**: Depends on completion of selected stories.

### User Story Dependencies

- **US1 (P1)**: First implementation slice after foundations.
- **US2 (P1)**: Depends on core contract entities from US1.
- **US3 (P2)**: Depends on schema infrastructure from US1 foundations, independent of US2 adapter behavior.

### Parallel Opportunities

- Setup tasks `T002-T004` can run in parallel.
- Foundational tasks `T006-T008` can run in parallel after `T005`.
- Story test tasks `T010`, `T012`, `T013`, and `T016` can run in parallel per story.
- Polish tasks `T018` and `T019` can run in parallel after implementation.

---

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 setup.
2. Complete Phase 2 foundational contracts/adapters.
3. Complete Phase 3 and pass `T010`.
4. Validate MVP contract behavior before adding adapter/auth refinements.

### Incremental Delivery

1. Land canonical schema contracts (US1).
2. Land shared adapter + Jules conformance (US2).
3. Land managed auth profile safety rules (US3).
4. Run cross-cutting validation and scope gates.

### Parallel Team Strategy

1. One engineer owns schema contracts (`T005-T011`, `T016-T017`).
2. One engineer owns adapter and temporal integration (`T007-T009`, `T012-T015`).
3. Rejoin for final validation and scope gates (`T018-T022`).

---

## Quality Gates

1. Runtime tasks gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime diff gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
3. Required validation command: `./tools/test_unit.sh`
4. Traceability gate: every `DOC-REQ-001` through `DOC-REQ-011` has implementation + validation tasks

## Task Summary

- Total tasks: **22**
- Story task count: **US1 = 2**, **US2 = 4**, **US3 = 2**
- Parallelizable tasks (`[P]`): **11**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **all tasks follow the required markdown checklist format**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T001, T002, T003, T004, T009, T015 | T013 |
| DOC-REQ-002 | T005, T011 | T010 |
| DOC-REQ-003 | T005, T008, T011 | T010, T012 |
| DOC-REQ-004 | T005, T008, T011 | T010, T012 |
| DOC-REQ-005 | T005, T008, T011 | T010, T012 |
| DOC-REQ-006 | T005, T008, T011 | T010, T012 |
| DOC-REQ-007 | T002, T003, T007, T008, T009, T014, T015 | T012, T013 |
| DOC-REQ-008 | T006, T017 | T016 |
| DOC-REQ-009 | T006, T017 | T016 |
| DOC-REQ-010 | T005, T008, T009, T011, T015 | T010, T012 |
| DOC-REQ-011 | T001, T002, T003, T004, T009 | T010, T012, T013, T016, T019, T020, T021, T022 |

## FR Coverage Matrix

| FR | Implementing Task(s) | Validation Task(s) |
| --- | --- | --- |
| FR-001 | T001, T002, T003, T004, T009, T015 | T013, T019, T020 |
| FR-002 | T005, T011 | T010, T019 |
| FR-003 | T005, T011 | T010, T019 |
| FR-004 | T005, T011 | T010, T019 |
| FR-005 | T005, T011 | T010, T019 |
| FR-006 | T005, T008, T011 | T010, T012, T019 |
| FR-007 | T002, T007, T014 | T012, T013, T019 |
| FR-008 | T003, T008, T014, T015 | T012, T013, T019 |
| FR-009 | T006, T017 | T016, T019 |
| FR-010 | T005, T008, T011, T015 | T010, T012, T019 |
| FR-011 | T006, T017 | T016, T019 |
