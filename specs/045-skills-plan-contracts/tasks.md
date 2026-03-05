# Tasks: Skills and Plans Runtime Contracts

**Input**: Design documents from `/specs/045-skills-plan-contracts/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `quickstart.md`, `contracts/`

**Tests**: Tests are required for this feature because the specification mandates validation coverage for runtime code deliverables.

**Organization**: Tasks are grouped by user story so each story can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only
- Include exact file paths in every task description

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present: `T001`, `T004-T013`, `T017-T020`, `T025-T030`, `T034-T037`.
- Runtime validation tasks are explicitly present: `T014-T016`, `T021-T024`, `T031-T033`, `T039-T041`.
- `DOC-REQ-*` implementation + validation coverage is enforced by `T038` and the `DOC-REQ Coverage Matrix` in this file, with persistent mapping in `specs/045-skills-plan-contracts/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Align runtime contract surfaces and test harness scaffolding before core behavior changes.

- [X] T001 Align skill/plan contract surface exports for runtime consumers in `moonmind/workflows/skills/contracts.py` and `moonmind/workflows/skills/__init__.py` (DOC-REQ-002, DOC-REQ-015).
- [X] T002 [P] Add shared registry/plan fixture builders for contract runtime tests in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-016).
- [X] T003 [P] Add/align unit test module scaffolding for registry and runner integration touchpoints in `tests/unit/workflows/test_skills_registry.py` and `tests/unit/workflows/test_skills_runner.py` (DOC-REQ-016).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement core runtime primitives required by all user stories.

**⚠️ CRITICAL**: No user-story work should begin until this phase is complete.

- [X] T004 Harden canonical contract dataclasses/parsers for skills, plans, results, and failures in `moonmind/workflows/skills/skill_plan_contracts.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-005, DOC-REQ-008, DOC-REQ-016).
- [X] T005 Implement immutable content-addressed artifact semantics and metadata validation in `moonmind/workflows/skills/artifact_store.py` (DOC-REQ-004, DOC-REQ-007).
- [X] T006 [P] Implement registry loading/validation for required fields, uniqueness, and policy bounds in `moonmind/workflows/skills/skill_registry.py` (DOC-REQ-005, DOC-REQ-009, DOC-REQ-016).
- [X] T007 [P] Implement deterministic registry snapshot digest creation and artifact-backed snapshot loading in `moonmind/workflows/skills/skill_registry.py` and `moonmind/workflows/skills/registry.py` (DOC-REQ-009, DOC-REQ-013, DOC-REQ-016).
- [X] T008 Implement structural plan validation for supported version, DAG edges, and unsupported semantics rejection in `moonmind/workflows/skills/plan_validation.py` (DOC-REQ-003, DOC-REQ-010).
- [X] T009 Implement deep validation activity flow that persists validated plans and blocks execution on validation errors in `moonmind/workflows/skills/skill_dispatcher.py` (DOC-REQ-012, DOC-REQ-016).
- [X] T010 [P] Implement dispatcher activity registration and strict declared-binding enforcement in `moonmind/workflows/skills/skill_dispatcher.py` (DOC-REQ-014, DOC-REQ-016).
- [X] T011 Implement deterministic ready-node scheduler with max-concurrency control in `moonmind/workflows/skills/plan_interpreter.py` (DOC-REQ-001, DOC-REQ-010, DOC-REQ-016).
- [X] T012 Implement failure-mode policy engine (`FAIL_FAST` vs `CONTINUE`) and skipped/cancelled bookkeeping in `moonmind/workflows/skills/plan_interpreter.py` (DOC-REQ-010).
- [X] T013 Implement deterministic cross-node input reference resolution and dependency-safe ref checks in `moonmind/workflows/skills/plan_validation.py` and `moonmind/workflows/skills/plan_interpreter.py` (DOC-REQ-011).

**Checkpoint**: Core registry, validation, dispatch, and interpreter foundations are ready for story slices.

---

## Phase 3: User Story 1 - Execute Contract-Compliant Plans (Priority: P1) 🎯 MVP

**Goal**: Execute pinned-snapshot DAG plans with deterministic scheduling, dependency correctness, concurrency caps, and policy-based failure handling.

**Independent Test**: Execute a validated multi-node plan and verify dependency ordering, configured concurrency, and policy-specific terminal behavior.

### Tests for User Story 1

- [X] T014 [P] [US1] Add deterministic ordering and concurrency-cap tests in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-010).
- [X] T015 [P] [US1] Add `FAIL_FAST` cancellation and dependent-node skip tests in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-010).
- [X] T016 [P] [US1] Add `CONTINUE` branch-completion and mixed-outcome summary tests in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-010, DOC-REQ-015).

### Implementation for User Story 1

- [X] T017 [US1] Wire validated-plan + pinned-snapshot execution entrypoints in `moonmind/workflows/skills/skill_dispatcher.py` and `moonmind/workflows/skills/plan_interpreter.py` (DOC-REQ-012, DOC-REQ-013).
- [X] T018 [US1] Enforce dependency-success scheduling and deterministic execution order in `moonmind/workflows/skills/plan_interpreter.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-010).
- [X] T019 [US1] Implement policy-correct branch cancellation/continuation transitions in `moonmind/workflows/skills/plan_interpreter.py` (DOC-REQ-010).
- [X] T020 [US1] Implement per-node `SkillResult` aggregation and terminal status shaping in `moonmind/workflows/skills/plan_interpreter.py` and `moonmind/workflows/skills/skill_plan_contracts.py` (DOC-REQ-007, DOC-REQ-015).

**Checkpoint**: User Story 1 is independently executable and policy-deterministic.

---

## Phase 4: User Story 2 - Validate and Dispatch Skills Safely (Priority: P2)

**Goal**: Reject invalid registries/plans early and dispatch valid invocations only through declared activity bindings with normalized failures.

**Independent Test**: Run mixed valid/invalid registry+plan payloads and verify invalid artifacts fail before execution while valid invocations route through declared bindings.

### Tests for User Story 2

- [X] T021 [P] [US2] Add registry contract validation tests for required fields, uniqueness, and snapshot immutability in `tests/unit/workflows/test_skills_registry.py` and `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-005, DOC-REQ-009).
- [X] T022 [P] [US2] Add deep plan validation tests for missing pinned skills, bad schemas, invalid overrides, and pre-execution rejection in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-006, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013).
- [X] T023 [P] [US2] Add dispatcher routing tests for declared `executor.activity_type` and missing-handler failures in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-014).
- [X] T024 [P] [US2] Add standardized `SkillFailure` code/retryability tests for validation and dispatch errors in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-008).

### Implementation for User Story 2

- [X] T025 [US2] Enforce skill definition required schema/executor/policy/capability/security constraints in `moonmind/workflows/skills/skill_plan_contracts.py` and `moonmind/workflows/skills/skill_registry.py` (DOC-REQ-005).
- [X] T026 [US2] Enforce unique node IDs, schema-validated inputs, bounded overrides, and valid ref topology in `moonmind/workflows/skills/plan_validation.py` (DOC-REQ-006, DOC-REQ-011).
- [X] T027 [US2] Enforce pinned snapshot digest/artifact consistency and snapshot-only lookup behavior in `moonmind/workflows/skills/skill_registry.py` and `moonmind/workflows/skills/plan_validation.py` (DOC-REQ-009, DOC-REQ-013).
- [X] T028 [US2] Implement strict dispatch routing by declared activity binding without inference fallback in `moonmind/workflows/skills/skill_dispatcher.py` (DOC-REQ-014, DOC-REQ-016).
- [X] T029 [US2] Normalize runtime failures to standard `SkillFailure` envelopes with policy-driven retry semantics in `moonmind/workflows/skills/skill_dispatcher.py` and `moonmind/workflows/skills/skill_plan_contracts.py` (DOC-REQ-008).
- [X] T030 [US2] Ensure deep plan validation is exposed as explicit `plan.validate` invocation behavior in `moonmind/workflows/skills/skill_dispatcher.py` and `moonmind/workflows/skills/contracts.py` (DOC-REQ-002, DOC-REQ-012).

**Checkpoint**: User Story 2 is independently safe, fail-fast, and binding-deterministic.

---

## Phase 5: User Story 3 - Observe Progress and Artifacts (Priority: P3)

**Goal**: Provide structured in-flight progress plus durable progress/summary artifact references for auditing and debugging.

**Independent Test**: Run a multi-node plan and verify progress snapshots, large-output artifact offload behavior, and terminal summary artifact references.

### Tests for User Story 3

- [X] T031 [P] [US3] Add progress snapshot consistency tests for counts, `last_event`, and `updated_at` in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-001, DOC-REQ-015).
- [X] T032 [P] [US3] Add large output offload tests ensuring inline payloads stay small and artifact refs carry large data in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-004, DOC-REQ-007).
- [X] T033 [P] [US3] Add terminal summary tests for result/failure/skipped maps plus summary/progress artifact refs in `tests/unit/workflows/test_skill_plan_runtime.py` (DOC-REQ-007, DOC-REQ-015).

### Implementation for User Story 3

- [X] T034 [US3] Implement structured execution progress state model updates in `moonmind/workflows/skills/plan_interpreter.py` and `moonmind/workflows/skills/skill_plan_contracts.py` (DOC-REQ-001, DOC-REQ-015, DOC-REQ-016).
- [X] T035 [US3] Implement optional durable `progress.json` artifact publication flow in `moonmind/workflows/skills/plan_interpreter.py` and `moonmind/workflows/skills/artifact_store.py` (DOC-REQ-004, DOC-REQ-015).
- [X] T036 [US3] Implement terminal summary artifact publication with per-node outcomes in `moonmind/workflows/skills/plan_interpreter.py` and `moonmind/workflows/skills/artifact_store.py` (DOC-REQ-007, DOC-REQ-015).
- [X] T037 [US3] Expose progress and summary contract helpers for runtime consumers in `moonmind/workflows/skills/contracts.py` and `moonmind/workflows/skills/__init__.py` (DOC-REQ-015).

**Checkpoint**: User Story 3 is independently observable and audit-friendly.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability, regression validation, and runtime scope checks.

- [X] T038 [P] Reconcile final implementation/validation evidence for DOC-REQ-001 through DOC-REQ-016 in `specs/045-skills-plan-contracts/contracts/requirements-traceability.md`.
- [X] T039 Run runtime contract validation via `./tools/test_unit.sh` and confirm workflow-suite coverage for `tests/unit/workflows/test_skill_plan_runtime.py`, `tests/unit/workflows/test_skills_registry.py`, and `tests/unit/workflows/test_skills_runner.py` (DOC-REQ-016).
- [X] T040 Run full unit regression via `./tools/test_unit.sh` and confirm skill/plan runtime coverage remains green (DOC-REQ-016).
- [X] T041 Run runtime scope gates via `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Starts immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks user stories.
- **Phase 3 (US1)**: Depends on Phase 2 and provides MVP runtime behavior.
- **Phase 4 (US2)**: Depends on Phase 2 and can proceed after core interpreter path stabilizes.
- **Phase 5 (US3)**: Depends on Phase 2 and builds on interpreter/result structures from US1.
- **Phase 6 (Polish)**: Depends on completion of targeted story work.

### User Story Dependencies

- **US1 (P1)**: Independent after foundational completion.
- **US2 (P2)**: Independent after foundational completion; validates and hardens dispatch/validation contracts.
- **US3 (P3)**: Independent after foundational completion; depends on interpreter lifecycle data structures for progress/summary publication.

### Within Each User Story

- Add tests for the story first and verify they fail before implementation changes.
- Complete validation/contract checks before interpreter/dispatch behavior changes.
- Complete implementation tasks before story-level full validation reruns.

## Parallel Opportunities

- Setup tasks `T002` and `T003` can run in parallel after `T001`.
- Foundational tasks `T006`, `T007`, and `T010` can run in parallel after `T004` and `T005`.
- US1 test tasks `T014`-`T016` can run in parallel.
- US2 test tasks `T021`-`T024` can run in parallel.
- US3 test tasks `T031`-`T033` can run in parallel.
- Polish tasks `T038` and `T039` can run in parallel before final full regression and scope gates.

---

## Parallel Example: User Story 1

```bash
Task T014: tests/unit/workflows/test_skill_plan_runtime.py
Task T015: tests/unit/workflows/test_skill_plan_runtime.py
Task T016: tests/unit/workflows/test_skill_plan_runtime.py
```

## Parallel Example: User Story 2

```bash
Task T021: tests/unit/workflows/test_skills_registry.py + tests/unit/workflows/test_skill_plan_runtime.py
Task T023: tests/unit/workflows/test_skill_plan_runtime.py
Task T024: tests/unit/workflows/test_skill_plan_runtime.py
```

## Parallel Example: User Story 3

```bash
Task T031: tests/unit/workflows/test_skill_plan_runtime.py
Task T032: tests/unit/workflows/test_skill_plan_runtime.py
Task T033: tests/unit/workflows/test_skill_plan_runtime.py
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1).
3. Validate US1 independently using tasks `T014`-`T016`.

### Incremental Delivery

1. Deliver foundational contracts, registry, validation, and interpreter.
2. Deliver US1 deterministic execution behavior.
3. Deliver US2 validation + dispatch hardening.
4. Deliver US3 observability and artifact publication.
5. Run polish gates (`T038`-`T041`).

### Parallel Team Strategy

1. Team aligns on Phase 1-2 interfaces.
2. After foundational checkpoint:
   - Engineer A: US1 interpreter policy behavior.
   - Engineer B: US2 registry/validation/dispatch hardening.
   - Engineer C: US3 progress + artifact publication.
3. Rejoin for final validation and traceability gates.

---

## Quality Gates

1. Runtime scope gate (tasks): `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime scope gate (diff): `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
3. Unit test gate: `./tools/test_unit.sh`
4. DOC-REQ gate: every `DOC-REQ-001` through `DOC-REQ-016` has at least one implementation task and one validation task.
5. Prompt B runtime gate: runtime implementation + validation task coverage remains explicit and traceable before implementation handoff.

## Task Summary

- Total tasks: **41**
- User story tasks: **US1 = 7**, **US2 = 10**, **US3 = 7**
- Parallelizable tasks (`[P]`): **15**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **All tasks follow `- [ ] T### [P?] [US?] ...` with explicit file paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T011, T018, T034 | T014, T031 |
| DOC-REQ-002 | T001, T004, T030 | T022 |
| DOC-REQ-003 | T004, T008, T018 | T014, T022 |
| DOC-REQ-004 | T005, T035 | T032 |
| DOC-REQ-005 | T004, T006, T025 | T021 |
| DOC-REQ-006 | T026 | T022 |
| DOC-REQ-007 | T005, T020, T036 | T032, T033 |
| DOC-REQ-008 | T004, T029 | T024 |
| DOC-REQ-009 | T006, T007, T027 | T021 |
| DOC-REQ-010 | T008, T011, T012, T018, T019 | T014, T015, T016 |
| DOC-REQ-011 | T013, T026 | T022 |
| DOC-REQ-012 | T009, T017, T030 | T022 |
| DOC-REQ-013 | T007, T017, T027 | T022 |
| DOC-REQ-014 | T010, T028 | T023 |
| DOC-REQ-015 | T001, T020, T034, T035, T036, T037 | T016, T031, T033 |
| DOC-REQ-016 | T004, T006, T007, T009, T010, T011, T028, T034 | T039, T040 |

Coverage rule: each `DOC-REQ-*` remains represented by at least one implementation task and one validation task before implementation and publish.
