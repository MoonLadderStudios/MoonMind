---
description: "Task list template for feature implementation"
---

# Tasks: [FEATURE NAME]

**Input**: Design documents from `/specs/[###-feature-name]/`
**Prerequisites**: `plan.md` (required), `spec.md` (required for user stories), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**:
- For runtime work, automated tests are **REQUIRED by default**.
- Use the appropriate test level for the change: unit/regression tests for logic and bug fixes, contract tests for interface/API changes, integration or end-to-end tests for multi-component journeys.
- Prefer TDD whenever behavior can be specified before implementation; write the test first and confirm it fails for the expected reason before changing production code.
- Manual or `quickstart.md` validation is supplemental unless automated tests are genuinely impossible or not meaningful.
- If automated tests cannot be added, create explicit `Test Exception` tasks that document the rationale in `specs/[###-feature-name]/plan.md` and deterministic fallback validation in `specs/[###-feature-name]/quickstart.md`.

**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.
**Usage**: Keep only the sections and tasks that apply to the feature. Remove unused examples and renumber sequentially.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (for example `US1`, `US2`, `US3`)
- Include exact file paths in descriptions
- If `spec.md` contains traceability IDs (for example `DOC-REQ-*`), carry them into the relevant implementation and validation tasks

## Test-First Rules

- For runtime stories, place automated test tasks before implementation tasks.
- Default to TDD for bug fixes, parsers, validators, services, adapters, API contracts, and other behavior-first changes.
- For bug fixes, start with a failing regression test that reproduces the defect.
- For API/schema changes, add or update contract tests before implementation.
- For cross-component flows, add or update integration or end-to-end tests that prove the user journey.
- For explicit docs-only work, replace runtime automated test tasks with deterministic documentation validation tasks.
- At least one automated test task is required for each runtime story unless a `Test Exception` subsection is used.
- A `Test Exception` subsection is allowed only when automated tests are impossible or not meaningful. It must document:
  - why automated tests are not feasible,
  - what was attempted or considered,
  - the deterministic fallback validation steps,
  - the residual risks and follow-up recommendations.

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root
- **Web app**: `backend/src/`, `backend/tests/`, `frontend/src/`, `frontend/tests/`
- **Mobile**: `api/src/`, `api/tests/`, `ios/`, `ios/Tests/` or `android/`, `androidTest/`
- **Co-located tests**: acceptable when standard for the stack (for example `src/**/*.spec.ts`, `src/**/*.test.ts`)
- Paths shown below assume a single project; adjust based on `plan.md`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and tooling needed across stories

- [ ] T001 Create project structure per implementation plan in `src/` and `tests/`
- [ ] T002 Initialize [language] project with [framework] dependencies in `[build-file-path]`
- [ ] T003 [P] Configure linting and formatting in `[lint-config-path]` and `[format-config-path]`
- [ ] T004 [P] Configure test runner and coverage reporting in `[test-config-path]`
- [ ] T005 [P] Create or update local/CI validation entrypoints in `[local-validation-script-path]` and `[ci-workflow-path]`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

Examples of foundational tasks (adjust based on your project):

- [ ] T006 Setup database schema and migration framework in `[migration-path]`
- [ ] T007 [P] Implement authentication/authorization foundation in `src/[auth-path].py`
- [ ] T008 [P] Setup API routing and middleware foundation in `src/[routing-path].py`
- [ ] T009 Create base models/entities shared across stories in `src/models/[shared-entity].py`
- [ ] T010 Configure error handling and logging infrastructure in `src/[infrastructure-path].py`
- [ ] T011 Setup environment and configuration management in `src/[config-path].py` and `[env-example-path]`

**Checkpoint**: Foundation ready. User story implementation can now begin in parallel.

---

## Phase 3: User Story 1 - [Title] (Priority: P1)

**Goal**: [Brief description of what this story delivers]
**Independent Test**: [How to verify this story works on its own]

### Tests for User Story 1 (REQUIRED unless impossible)

> **NOTE:** Write the applicable tests FIRST. Confirm they fail for the expected reason before implementing production code.

- [ ] T012 [P] [US1] Add or update contract test for [endpoint/interface] in `tests/contract/test_[name].py`
- [ ] T013 [P] [US1] Add or update integration test for [user journey] in `tests/integration/test_[name].py`
- [ ] T014 [P] [US1] Add or update unit/regression test for [rule, service, or bug] in `tests/unit/test_[name].py`

### Test Exception for User Story 1 (ONLY if automated tests are impossible or not meaningful)

- [ ] T015 [US1] Document why automated tests are not feasible, what was attempted, and residual risk in `specs/[###-feature-name]/plan.md`
- [ ] T016 [US1] Add deterministic fallback validation steps for [user journey] in `specs/[###-feature-name]/quickstart.md`

### Implementation for User Story 1

- [ ] T017 [P] [US1] Create [Entity1] model in `src/models/[entity1].py`
- [ ] T018 [P] [US1] Create [Entity2] model in `src/models/[entity2].py`
- [ ] T019 [US1] Implement [Service] in `src/services/[service].py` (depends on T017, T018)
- [ ] T020 [US1] Implement [endpoint/feature] in `src/[location]/[file].py`
- [ ] T021 [US1] Add validation and error handling in `src/[location]/[file].py`
- [ ] T022 [US1] Add observability/logging for User Story 1 in `src/[location]/[file].py`

### Validation for User Story 1

- [ ] T023 [US1] Verify User Story 1 behavior via `tests/contract/test_[name].py` and `tests/integration/test_[name].py`
- [ ] T024 [US1] Update usage and demo steps for User Story 1 in `specs/[###-feature-name]/quickstart.md`

**Checkpoint**: At this point, User Story 1 should be fully functional and independently verifiable.

---

## Phase 4: User Story 2 - [Title] (Priority: P2)

**Goal**: [Brief description of what this story delivers]
**Independent Test**: [How to verify this story works on its own]

### Tests for User Story 2 (REQUIRED unless impossible)

> **NOTE:** Write the applicable tests FIRST. Confirm they fail for the expected reason before implementing production code.

- [ ] T025 [P] [US2] Add or update contract test for [endpoint/interface] in `tests/contract/test_[name].py`
- [ ] T026 [P] [US2] Add or update integration test for [user journey] in `tests/integration/test_[name].py`
- [ ] T027 [P] [US2] Add or update unit/regression test for [rule, service, or bug] in `tests/unit/test_[name].py`

### Test Exception for User Story 2 (ONLY if automated tests are impossible or not meaningful)

- [ ] T028 [US2] Document why automated tests are not feasible, what was attempted, and residual risk in `specs/[###-feature-name]/plan.md`
- [ ] T029 [US2] Add deterministic fallback validation steps for [user journey] in `specs/[###-feature-name]/quickstart.md`

### Implementation for User Story 2

- [ ] T030 [P] [US2] Create [Entity] model in `src/models/[entity].py`
- [ ] T031 [US2] Implement [Service] in `src/services/[service].py`
- [ ] T032 [US2] Implement [endpoint/feature] in `src/[location]/[file].py`
- [ ] T033 [US2] Integrate with shared components from earlier phases in `src/[location]/[file].py`
- [ ] T034 [US2] Add validation, error handling, and logging in `src/[location]/[file].py`

### Validation for User Story 2

- [ ] T035 [US2] Verify User Story 2 behavior via `tests/contract/test_[name].py` and `tests/integration/test_[name].py`
- [ ] T036 [US2] Update usage and demo steps for User Story 2 in `specs/[###-feature-name]/quickstart.md`

**Checkpoint**: At this point, User Stories 1 and 2 should both work independently.

---

## Phase 5: User Story 3 - [Title] (Priority: P3)

**Goal**: [Brief description of what this story delivers]
**Independent Test**: [How to verify this story works on its own]

### Tests for User Story 3 (REQUIRED unless impossible)

> **NOTE:** Write the applicable tests FIRST. Confirm they fail for the expected reason before implementing production code.

- [ ] T037 [P] [US3] Add or update contract test for [endpoint/interface] in `tests/contract/test_[name].py`
- [ ] T038 [P] [US3] Add or update integration test for [user journey] in `tests/integration/test_[name].py`
- [ ] T039 [P] [US3] Add or update unit/regression test for [rule, service, or bug] in `tests/unit/test_[name].py`

### Test Exception for User Story 3 (ONLY if automated tests are impossible or not meaningful)

- [ ] T040 [US3] Document why automated tests are not feasible, what was attempted, and residual risk in `specs/[###-feature-name]/plan.md`
- [ ] T041 [US3] Add deterministic fallback validation steps for [user journey] in `specs/[###-feature-name]/quickstart.md`

### Implementation for User Story 3

- [ ] T042 [P] [US3] Create [Entity] model in `src/models/[entity].py`
- [ ] T043 [US3] Implement [Service] in `src/services/[service].py`
- [ ] T044 [US3] Implement [endpoint/feature] in `src/[location]/[file].py`
- [ ] T045 [US3] Integrate with shared components from earlier phases in `src/[location]/[file].py`
- [ ] T046 [US3] Add validation, error handling, and logging in `src/[location]/[file].py`

### Validation for User Story 3

- [ ] T047 [US3] Verify User Story 3 behavior via `tests/contract/test_[name].py` and `tests/integration/test_[name].py`
- [ ] T048 [US3] Update usage and demo steps for User Story 3 in `specs/[###-feature-name]/quickstart.md`

**Checkpoint**: All user stories should now be independently functional and verifiable.

---

[Add more user story phases as needed, following the same pattern.]

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T049 [P] Update operator and developer documentation in `docs/[doc-path].md`
- [ ] T050 Refactor shared code and remove obsolete paths in `src/[shared-path].py`
- [ ] T051 [P] Add or update cross-story regression coverage in `tests/integration/test_[name].py`
- [ ] T052 [P] Optimize performance-critical paths in `src/[performance-path].py`
- [ ] T053 [P] Harden security-sensitive paths in `src/[security-path].py`
- [ ] T054 Update final end-to-end validation steps in `specs/[###-feature-name]/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies. Can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion. Blocks all user stories.
- **User Stories (Phase 3+)**: Depend on Foundational completion.
  - User stories can then proceed in parallel if staffing allows.
  - Or sequentially in priority order (`P1 → P2 → P3`).
- **Polish (Final Phase)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2). No dependencies on other stories.
- **User Story 2 (P2)**: Can start after Foundational (Phase 2). It may integrate shared components but should remain independently testable.
- **User Story 3 (P3)**: Can start after Foundational (Phase 2). It may integrate shared components but should remain independently testable.
- If a story truly depends on another story, document that dependency explicitly and keep the dependency as narrow as possible.

### Within Each User Story

- Automated tests MUST be written first and fail for the expected reason before implementation, unless a documented `Test Exception` is used.
- `Test Exception` tasks MUST be completed before implementation when automated tests are omitted.
- Models/entities before services.
- Services before endpoints/UI wiring.
- Core implementation before cross-story integration.
- Story validation before moving to the next priority or release candidate.
- `quickstart.md` validation supplements automated tests; it does not replace them unless a `Test Exception` is documented.

### Parallel Opportunities

- All Setup tasks marked `[P]` can run in parallel.
- All Foundational tasks marked `[P]` can run in parallel within Phase 2.
- Once Foundational is complete, multiple user stories can start in parallel if team capacity allows.
- Applicable tests within a story marked `[P]` can run in parallel.
- Models within a story marked `[P]` can run in parallel.
- Different user stories can be worked on in parallel by different team members when they do not touch the same files.

---

## Parallel Example: User Story 1

```bash
# Launch the initial failing tests for User Story 1 together:
Task: "Add or update contract test for [endpoint/interface] in tests/contract/test_[name].py"
Task: "Add or update integration test for [user journey] in tests/integration/test_[name].py"
Task: "Add or update unit/regression test for [rule, service, or bug] in tests/unit/test_[name].py"

# After the tests exist, launch independent model work together:
Task: "Create [Entity1] model in src/models/[entity1].py"
Task: "Create [Entity2] model in src/models/[entity2].py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational.
3. Complete Phase 3: User Story 1 tests first.
4. Implement User Story 1.
5. **STOP and VALIDATE**: Run automated validation for User Story 1, or the approved fallback validation if a `Test Exception` exists.
6. Deploy/demo if ready.

### Incremental Delivery

1. Complete Setup + Foundational → foundation ready.
2. Add User Story 1 → validate independently → deploy/demo.
3. Add User Story 2 → validate independently → deploy/demo.
4. Add User Story 3 → validate independently → deploy/demo.
5. Each story should add value without breaking previously delivered behavior or test coverage.

### Bug-Fix Strategy

1. Add a failing regression test that reproduces the bug.
2. Implement the minimal fix required in production code.
3. Re-run the affected automated tests and relevant integrations.
4. Update `quickstart.md` if operator-visible validation steps changed.

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together.
2. Once Foundational is complete:
   - Developer A: User Story 1 tests + implementation
   - Developer B: User Story 2 tests + implementation
   - Developer C: User Story 3 tests + implementation
3. Merge only when each story’s tests pass, or an approved `Test Exception` is documented and validated.

---

## Notes

- `[P]` tasks = different files, no dependencies.
- `[Story]` labels map each task to a specific user story for traceability.
- Each user story should be independently completable and independently verifiable.
- Prefer small, testable increments over large cross-story changes.
- Keep test tasks close to the behavior they prove.
- Reference `DOC-REQ-*` IDs in relevant tasks when present.
- Commit after each task or logical group.
- Stop at any checkpoint to validate the story independently.
- Avoid: vague tasks, same-file conflicts across parallel tasks, hidden cross-story dependencies, and manual-only validation without a documented `Test Exception`.