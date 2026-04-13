# Tasks: Jira Runtime Config Tests

**Input**: Design documents from `/specs/159-jira-runtime-config-tests/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/
**Tests**: TDD is required by the feature request. Write failing tests before implementation tasks for each story.
**Runtime Mode**: Required deliverables include production runtime code changes plus validation tests.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or has no dependency on incomplete tasks
- **[Story]**: User story label for story phases only
- Every task includes exact file paths

## Phase 1: Setup

**Purpose**: Confirm current runtime config surfaces and test locations before changing behavior.

- [X] T001 Review existing runtime config assembly and Jira rollout hooks in api_service/api/routers/task_dashboard_view_model.py and moonmind/config/settings.py
- [X] T002 [P] Review existing runtime config unit-test patterns in tests/unit/api/routers/test_task_dashboard_view_model.py
- [X] T003 [P] Review operator default examples in api_service/config.template.toml

---

## Phase 2: Foundational

**Purpose**: Establish shared constants and settings shape that all stories depend on.

- [X] T004 Add or confirm safe-by-default Jira Create-page feature flag fields and normalization in moonmind/config/settings.py for DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005
- [X] T005 Add or confirm repository config template defaults in api_service/config.template.toml for DOC-REQ-004
- [X] T006 Add or confirm a single Jira source-template constant in api_service/api/routers/task_dashboard_view_model.py for DOC-REQ-002, DOC-REQ-004, DOC-REQ-005

**Checkpoint**: Shared settings and endpoint-template surfaces are ready for story implementation.

---

## Phase 3: User Story 1 - Keep Jira Hidden When Disabled (Priority: P1)

**Goal**: The Create page boot payload omits Jira browser capability when the Jira UI rollout is disabled, even if trusted backend Jira tooling is enabled.

**Independent Test**: Generate runtime config with Jira UI disabled and assert `sources.jira` and `system.jiraIntegration` are absent while existing non-Jira Create-page config remains present.

### Tests for User Story 1

- [X] T007 [P] [US1] Add failing disabled-rollout omission test in tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-001 and DOC-REQ-003
- [X] T008 [P] [US1] Add failing backend-tooling separation test in tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-001, DOC-REQ-003, DOC-REQ-005
- [X] T009 [US1] Run focused failing tests with ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-001, DOC-REQ-003, DOC-REQ-005

### Implementation for User Story 1

- [X] T010 [US1] Implement disabled-state omission in api_service/api/routers/task_dashboard_view_model.py for DOC-REQ-001 and DOC-REQ-003
- [X] T011 [US1] Preserve existing non-Jira runtime config shape in api_service/api/routers/task_dashboard_view_model.py for DOC-REQ-001 and DOC-REQ-002
- [X] T012 [US1] Run focused passing tests with ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-005

**Checkpoint**: User Story 1 is fully functional and testable independently.

---

## Phase 4: User Story 2 - Publish Jira Discovery Contract When Enabled (Priority: P1)

**Goal**: The enabled Create page boot payload includes Jira browser endpoint templates and an affirmative Jira integration enabled setting.

**Independent Test**: Generate runtime config with Jira UI enabled and assert all six Jira source entries and the enabled integration setting are present and use MoonMind API paths only.

### Tests for User Story 2

- [X] T013 [P] [US2] Add failing enabled endpoint-template test in tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-002, DOC-REQ-004, DOC-REQ-005
- [X] T014 [P] [US2] Add failing MoonMind-API-path safety assertions in tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-001 and DOC-REQ-005
- [X] T015 [US2] Run focused failing tests with ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-002, DOC-REQ-004, DOC-REQ-005

### Implementation for User Story 2

- [X] T016 [US2] Implement enabled Jira source and system block merge in api_service/api/routers/task_dashboard_view_model.py for DOC-REQ-002, DOC-REQ-004, DOC-REQ-005
- [X] T017 [US2] Ensure endpoint templates in api_service/api/routers/task_dashboard_view_model.py expose only MoonMind API paths and no Jira credentials or Jira domains for DOC-REQ-001 and DOC-REQ-005
- [X] T018 [US2] Run focused passing tests with ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-001, DOC-REQ-002, DOC-REQ-004, DOC-REQ-005

**Checkpoint**: User Story 2 is fully functional and testable independently.

---

## Phase 5: User Story 3 - Surface Operator Defaults (Priority: P2)

**Goal**: Configured default Jira project, default Jira board, and session board memory values appear in the enabled boot payload.

**Independent Test**: Generate runtime config with Jira UI enabled and configured defaults, then assert the Jira integration settings reflect those values.

### Tests for User Story 3

- [X] T019 [P] [US3] Add failing configured-defaults runtime config test in tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-004 and DOC-REQ-005
- [X] T020 [P] [US3] Add failing default-normalization coverage in tests/unit/config/test_settings.py for DOC-REQ-004
- [X] T021 [US3] Run focused failing tests with ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py for DOC-REQ-004 and DOC-REQ-005

### Implementation for User Story 3

- [X] T022 [US3] Surface default project key, board ID, and session-memory values in api_service/api/routers/task_dashboard_view_model.py for DOC-REQ-004 and DOC-REQ-005
- [X] T023 [US3] Ensure settings normalization for default project key and board ID in moonmind/config/settings.py for DOC-REQ-004
- [X] T024 [US3] Run focused passing tests with ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py for DOC-REQ-004 and DOC-REQ-005

**Checkpoint**: User Story 3 is fully functional and testable independently.

---

## Phase 6: Polish & Cross-Cutting Validation

**Purpose**: Confirm runtime scope, traceability, and full-suite behavior before completion.

- [X] T025 [P] Verify all DOC-REQ mappings remain complete in specs/159-jira-runtime-config-tests/contracts/requirements-traceability.md
- [X] T026 Run runtime scope validation with ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
- [X] T027 Run full unit verification with ./tools/test_unit.sh
- [X] T028 Review final git diff for unrelated changes in api_service/api/routers/task_dashboard_view_model.py, moonmind/config/settings.py, api_service/config.template.toml, and tests/unit/api/routers/test_task_dashboard_view_model.py

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational completion and is the MVP rollout guard.
- **User Story 2 (Phase 4)**: Depends on Foundational completion; can proceed independently after Phase 2 but is typically sequenced after US1 for safer rollout.
- **User Story 3 (Phase 5)**: Depends on Foundational completion and can proceed after US2 source/system block shape exists.
- **Polish (Phase 6)**: Depends on all selected user stories.

### User Story Dependencies

- **US1 Keep Jira Hidden When Disabled**: No dependency on other user stories after Foundational.
- **US2 Publish Jira Discovery Contract When Enabled**: No dependency on US1 implementation after Foundational, but shares the same runtime config builder.
- **US3 Surface Operator Defaults**: Depends on enabled Jira integration settings existing from US2.

### Within Each User Story

- Test tasks must be written and observed failing before implementation.
- Runtime implementation tasks follow the failing tests.
- Focused passing verification completes the story.

## Parallel Opportunities

- T002 and T003 can run in parallel with T001.
- T007 and T008 can run in parallel because they add separate test cases.
- T013 and T014 can run in parallel because they validate separate enabled-contract concerns.
- T019 and T020 can run in parallel if one targets router config and the other targets settings normalization.
- US1 and US2 can be implemented in parallel after Foundational if developers coordinate edits to api_service/api/routers/task_dashboard_view_model.py.

## Parallel Example: User Story 2

```text
Task: "T013 Add failing enabled endpoint-template test in tests/unit/api/routers/test_task_dashboard_view_model.py"
Task: "T014 Add failing MoonMind-API-path safety assertions in tests/unit/api/routers/test_task_dashboard_view_model.py"
```

## Implementation Strategy

### MVP First

1. Complete Setup and Foundational tasks.
2. Complete US1 tests and implementation.
3. Stop and validate that Jira UI config is absent when disabled and independent from backend Jira tooling.

### Incremental Delivery

1. Deliver US1 to protect disabled behavior.
2. Deliver US2 to expose the enabled discovery contract.
3. Deliver US3 to surface operator defaults.
4. Run cross-cutting validation and full unit tests.

### Traceability Coverage

- `DOC-REQ-001`: implementation T004, T010, T011, T017; validation T007, T008, T012, T014, T018.
- `DOC-REQ-002`: implementation T006, T011, T016; validation T012, T013, T015, T018.
- `DOC-REQ-003`: implementation T004, T010; validation T007, T008, T009, T012.
- `DOC-REQ-004`: implementation T004, T005, T006, T016, T022, T023; validation T013, T015, T018, T019, T020, T021, T024.
- `DOC-REQ-005`: implementation T004, T006, T016, T017, T022; validation T008, T009, T013, T014, T015, T018, T019, T021, T024.
