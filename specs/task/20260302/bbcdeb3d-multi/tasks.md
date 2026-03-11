# Tasks: Skills Workflow Alignment Refresh

**Input**: Design documents from `/specs/015-skills-workflow/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Validation tests are required by this feature (`DOC-REQ-008`), including unit coverage via `./tools/test_unit.sh`.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Phase 1: Setup (Shared Context)

**Purpose**: Establish traceable implementation scope and source-of-truth artifacts before code changes.

- [X] T001 Reconcile requirement mappings in `specs/015-skills-workflow/spec.md` and `specs/015-skills-workflow/contracts/requirements-traceability.md` for `DOC-REQ-001` through `DOC-REQ-008`.
- [X] T002 Align planning assumptions in `specs/015-skills-workflow/plan.md` and `specs/015-skills-workflow/research.md` with runtime implementation mode (`DOC-REQ-007`) and `./tools/test_unit.sh` validation (`DOC-REQ-008`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared metadata and stage-contract foundations required by all user stories.

**CRITICAL**: No user story implementation starts until this phase is complete.

- [X] T003 Normalize canonical runtime stage constants in `moonmind/workflows/speckit_celery/tasks.py` and `moonmind/workflows/speckit_celery/models.py` for `discover_next_phase`, `submit_codex_job`, and `apply_and_publish` (`DOC-REQ-001`, `DOC-REQ-007`).
- [X] T004 [P] Add foundational phase-state schema fields for `selected_skill`, `adapter_id`, and `execution_path` in `moonmind/schemas/workflow_models.py` (`DOC-REQ-004`, `DOC-REQ-005`).
- [X] T005 [P] Create reusable metadata fixture coverage for explicit and legacy payloads in `tests/unit/workflows/test_spec_automation_env.py` (`DOC-REQ-006`, `DOC-REQ-007`).

**Checkpoint**: Canonical stage + metadata foundations are in place for story-level implementation.

---

## Phase 3: User Story 1 - Canonical Stage Contract and Phase Metadata (Priority: P1) 🎯 MVP

**Goal**: Surface canonical stage metadata and adapter observability in runtime/API payloads.

**Independent Test**: `tests/unit/workflows/test_spec_automation_env.py` and `tests/unit/api/test_spec_automation.py` prove explicit metadata and legacy defaults expose `selected_skill`, `adapter_id`, and `execution_path`.

### Tests for User Story 1

- [X] T006 [P] [US1] Add normalization regression tests for explicit metadata and Speckit legacy defaults in `tests/unit/workflows/test_spec_automation_env.py` (`DOC-REQ-005`, `DOC-REQ-006`).
- [X] T007 [P] [US1] Add API serialization tests for `selected_skill`, `adapter_id`, and `execution_path` in `tests/unit/api/test_spec_automation.py` (`DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`).

### Implementation for User Story 1

- [X] T008 [US1] Implement backward-compatible stage metadata normalization (`selectedSkill`, `adapterId`, `executionPath`) in `moonmind/workflows/speckit_celery/models.py` (`DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`).
- [X] T009 [US1] Project adapter-aware phase fields through API schema and serializer code in `moonmind/schemas/workflow_models.py` and `api_service/api/routers/spec_automation.py` (`DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-007`).
- [X] T010 [US1] Align stage/metadata documentation contracts in `specs/015-skills-workflow/contracts/skills-stage-contract.md` and `specs/015-skills-workflow/contracts/spec-automation-api.openapi.yaml` (`DOC-REQ-001`, `DOC-REQ-005`).

**Checkpoint**: User Story 1 is independently testable with canonical runtime stage metadata and adapter observability.

---

## Phase 4: User Story 2 - Shared Skills Runtime and Fast-Path Documentation Parity (Priority: P1)

**Goal**: Ensure `015` artifacts match the shared-skills workspace strategy and current worker startup/auth flow.

**Independent Test**: Contracts and quickstart reflect one shared `skills_active` workspace, required auth scripts, and conditional Speckit verification behavior.

### Tests for User Story 2

- [X] T011 [P] [US2] Add stage-routing and conditional Speckit verification tests in `tests/unit/workflows/test_tasks.py` (`DOC-REQ-001`, `DOC-REQ-004`).

### Implementation for User Story 2

- [X] T012 [US2] Implement conditional Speckit verification based on configured stage skills in `moonmind/workflows/skills/registry.py` and `moonmind/workflows/speckit_celery/tasks.py` (`DOC-REQ-004`, `DOC-REQ-007`).
- [X] T013 [US2] Update shared-skills workspace contract details in `specs/015-skills-workflow/contracts/compose-fast-path.md` and `specs/015-skills-workflow/contracts/skills-stage-contract.md` (`DOC-REQ-002`).
- [X] T014 [US2] Update worker auth/startup fast-path steps in `specs/015-skills-workflow/quickstart.md` and `specs/015-skills-workflow/contracts/compose-fast-path.md` to use `./tools/auth-codex-volume.sh` and `./tools/auth-gemini-volume.sh` (`DOC-REQ-003`).
- [X] T015 [US2] Validate docs-to-runtime parity and traceability entries in `specs/015-skills-workflow/contracts/requirements-traceability.md` for shared skills and conditional checks (`DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`).

**Checkpoint**: User Story 2 documentation and runtime behavior are aligned with the current MoonMind strategy.

---

## Phase 5: User Story 3 - Runtime Validation and Backward-Compatible Safety (Priority: P2)

**Goal**: Enforce runtime implementation scope and verify compatibility behavior through required validation gates.

**Independent Test**: Runtime-focused regression tests pass and validation commands prove this feature is not docs-only.

### Tests for User Story 3

- [X] T016 [P] [US3] Expand compatibility regression coverage for missing metadata keys in `tests/unit/workflows/test_spec_automation_env.py` and `tests/unit/api/test_spec_automation.py` (`DOC-REQ-006`, `DOC-REQ-007`).

### Implementation for User Story 3

- [X] T017 [US3] Harden legacy-safe phase projection behavior in `moonmind/workflows/speckit_celery/models.py` and `api_service/api/routers/spec_automation.py` for absent skill metadata keys (`DOC-REQ-006`, `DOC-REQ-007`).

### Validation for User Story 3

- [X] T018 [US3] Run `./tools/test_unit.sh` validating updates in `tests/unit/workflows/test_spec_automation_env.py`, `tests/unit/workflows/test_tasks.py`, and `tests/unit/api/test_spec_automation.py` (`DOC-REQ-008`, `DOC-REQ-007`).
- [X] T019 [US3] Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime` against runtime files in `moonmind/workflows/speckit_celery/models.py` and `api_service/api/routers/spec_automation.py` (`DOC-REQ-007`).

**Checkpoint**: Runtime mode scope and backward-compatible behavior are validated.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final cross-artifact verification and task-quality gates.

- [X] T020 [P] Run `SPECIFY_FEATURE=015-skills-workflow .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` for `specs/015-skills-workflow/tasks.md` (`DOC-REQ-007`).
- [X] T021 [P] Verify each `DOC-REQ-*` appears in at least one implementation and one validation task in `specs/015-skills-workflow/tasks.md` and `specs/015-skills-workflow/contracts/requirements-traceability.md` (`DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 (Setup) has no prerequisites.
- Phase 2 (Foundational) depends on Phase 1 and blocks all user stories.
- Phase 3 (US1), Phase 4 (US2), and Phase 5 (US3) depend on Phase 2 completion.
- Phase 6 (Polish) depends on completion of targeted user stories.

### User Story Dependencies

- US1 (P1) depends on foundational stage and schema updates from Phase 2.
- US2 (P1) depends on foundational stage contract readiness; can proceed in parallel with US1 once Phase 2 is complete.
- US3 (P2) depends on US1 runtime metadata behavior and US2 conditional verification behavior.

### Parallel Opportunities

- T004 and T005 can run in parallel during Phase 2.
- T006 and T007 can run in parallel during US1 test authoring.
- T011 can run in parallel with T013 and T014 once T012 is underway.
- T016 can run in parallel with T017 during US3 stabilization.
- T020 and T021 can run in parallel in the Polish phase.

---

## Parallel Example: User Story 1

```bash
# Parallel US1 validation authoring:
Task: "T006 [US1] Add normalization regression tests in tests/unit/workflows/test_spec_automation_env.py"
Task: "T007 [US1] Add API serialization tests in tests/unit/api/test_spec_automation.py"
```

## Parallel Example: User Story 2

```bash
# Parallel US2 docs parity updates after conditional runtime behavior is implemented:
Task: "T013 [US2] Update shared-skills workspace contract docs in specs/015-skills-workflow/contracts/compose-fast-path.md and specs/015-skills-workflow/contracts/skills-stage-contract.md"
Task: "T014 [US2] Update auth/startup quickstart steps in specs/015-skills-workflow/quickstart.md and specs/015-skills-workflow/contracts/compose-fast-path.md"
```

## Parallel Example: User Story 3

```bash
# Parallel US3 compatibility hardening and test expansion:
Task: "T016 [US3] Expand compatibility regressions in tests/unit/workflows/test_spec_automation_env.py and tests/unit/api/test_spec_automation.py"
Task: "T017 [US3] Harden legacy-safe phase projection in moonmind/workflows/speckit_celery/models.py and api_service/api/routers/spec_automation.py"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2 foundations.
2. Deliver US1 metadata normalization + API projection (T006-T010).
3. Validate US1 independently before expanding scope.

### Incremental Delivery

1. Deliver US1 (metadata observability).
2. Deliver US2 (shared-skills runtime parity and conditional Speckit behavior).
3. Deliver US3 (runtime validation and compatibility hardening).
4. Finish with cross-cutting scope + traceability gates.

### Runtime Scope Commitments

- Runtime implementation surfaces include `moonmind/workflows/`, `moonmind/schemas/`, and `api_service/api/routers/`.
- Validation coverage includes `tests/unit/workflows/` and `tests/unit/api/`.
- Required unit gate executes via `./tools/test_unit.sh`.
