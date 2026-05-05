# Tasks: Canonical Task Run List Route

**Input**: Design documents from `specs/298-canonical-task-run-list/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [quickstart.md](./quickstart.md), [contracts/tasks-list-visibility-contract.md](./contracts/tasks-list-visibility-contract.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: One story only: Task-Focused Execution List.

**Source Traceability**: THOR-370 and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-011, acceptance scenarios 1-6, edge cases, SC-001 through SC-006, and DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-008, DESIGN-REQ-015, and DESIGN-REQ-022.

**Requirement Status Summary**: `plan.md` classifies 7 rows as `missing`, 12 as `partial`, 7 as `implemented_unverified`, and 3 as `implemented_verified`. Missing and partial rows require code plus tests. Implemented-unverified rows require verification tests first, with conditional fallback implementation if verification fails. Implemented-verified rows require final regression preservation only.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Targeted Python unit tests: `pytest tests/unit/api/routers/test_task_dashboard.py tests/unit/api/routers/test_executions.py -q`
- Targeted UI unit tests: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and does not depend on incomplete tasks.
- Every task includes an exact file path and requirement, scenario, success, or source IDs where applicable.

## Phase 1: Setup

**Purpose**: Confirm the active feature artifacts and test targets before changing behavior.

- [X] T001 Verify `specs/298-canonical-task-run-list/spec.md`, `specs/298-canonical-task-run-list/plan.md`, `specs/298-canonical-task-run-list/research.md`, `specs/298-canonical-task-run-list/data-model.md`, `specs/298-canonical-task-run-list/quickstart.md`, and `specs/298-canonical-task-run-list/contracts/tasks-list-visibility-contract.md` are present and still describe one THOR-370 story.
- [X] T002 Inspect existing route, list API, and UI behavior in `api_service/api/routers/task_dashboard.py`, `api_service/api/routers/executions.py`, and `frontend/src/entrypoints/tasks-list.tsx` before editing.
- [X] T003 Confirm targeted test commands are available for `tests/unit/api/routers/test_task_dashboard.py`, `tests/unit/api/routers/test_executions.py`, and `frontend/src/entrypoints/tasks-list.test.tsx`.

---

## Phase 2: Foundational

**Purpose**: Establish shared fixtures and expectations that block story test authoring.

**Critical**: No production behavior changes begin until this phase is complete.

- [X] T004 [P] Add or identify reusable boot-payload assertions for page key, initial path, and wide layout in `tests/unit/api/routers/test_task_dashboard.py` covering FR-003, SCN-001, SC-001, and DESIGN-REQ-002.
- [X] T005 [P] Add or identify reusable Tasks List UI render helpers for initial URL, fetch capture, and row payloads in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-004, FR-005, FR-006, and DESIGN-REQ-003.
- [X] T006 [P] Add or identify reusable Temporal list query assertions in `tests/unit/api/routers/test_executions.py` covering FR-005, FR-006, FR-010, and DESIGN-REQ-022.
- [X] T007 [P] Create hermetic integration test scaffolding for ordinary task rows plus system and manifest rows in `tests/integration/api/test_tasks_list_visibility.py` covering SCN-003, SCN-004, SC-002, and SC-003.

**Checkpoint**: Foundation ready; story tests can now be written.

---

## Phase 3: Story - Task-Focused Execution List

**Summary**: As a Mission Control operator, I want `/tasks/list` to always open the task-focused execution list and keep broad workflow browsing out of the ordinary page.

**Independent Test**: Load the canonical and legacy task-list routes with ordinary and broad-workflow query parameters, then verify route behavior, page identity, visible rows, compatibility handling, and diagnostics separation without granting ordinary users access to system workflow rows.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-008, DESIGN-REQ-015, DESIGN-REQ-022.

**Unit Test Plan**: Router boot payload assertions, execution scope query assertions, UI URL normalization, removed broad controls, no direct external fetch targets, and no broad workflow table/card affordances.

**Integration Test Plan**: Hermetic API/UI boundary scenarios for mixed task/system/manifest data and at least four broad compatibility URL cases.

### Unit Tests - Write First

- [X] T008 [P] Add failing router unit tests for `/tasks/list` boot payload page key, initial path, dashboard config, and wide layout in `tests/unit/api/routers/test_task_dashboard.py` covering FR-003, SCN-001, SC-001, and DESIGN-REQ-002.
- [X] T009 [P] Add failing router unit tests preserving `/tasks` and `/tasks/tasks-list` redirects in `tests/unit/api/routers/test_task_dashboard.py` covering FR-001, FR-002, SCN-002, SC-001, and DESIGN-REQ-002.
- [X] T010 [P] Add failing execution API unit tests for task-scope query construction and non-admin owner scoping in `tests/unit/api/routers/test_executions.py` covering FR-005, FR-006, FR-010, SC-002, and DESIGN-REQ-022.
- [X] T011 [P] Add failing Tasks List UI tests proving default list fetch uses `/api/executions` with task-run scope only in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-004, FR-005, SC-005, and DESIGN-REQ-002.
- [X] T012 Add failing Tasks List UI tests for broad compatibility URLs `scope=system`, `scope=all`, system `workflowType`, and `entry=manifest` in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-007, FR-008, FR-010, SCN-004, SCN-005, SC-003, DESIGN-REQ-008, and DESIGN-REQ-015.
- [X] T013 Add failing Tasks List UI tests asserting Scope, Workflow Type, Entry, Kind, Workflow Type, and Entry ordinary browsing affordances are absent from table, controls, cards, and active chips in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-006, FR-009, SCN-006, SC-004, DESIGN-REQ-003, and DESIGN-REQ-004.
- [X] T014 Add failing Tasks List UI tests for manifest-oriented compatibility URLs routing or recoverable messaging without ordinary broad rows in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-007, FR-008, SCN-004, and DESIGN-REQ-015.
- [X] T015 Run `pytest tests/unit/api/routers/test_task_dashboard.py tests/unit/api/routers/test_executions.py -q` and confirm T008-T010 fail for the expected missing behavior before production code changes.
- [X] T016 Run `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` and confirm T011-T014 fail for the expected missing behavior before production code changes.

### Integration Tests - Write First

- [X] T017 [P] Add failing hermetic integration test for mixed ordinary task, system workflow, and manifest workflow data in `tests/integration/api/test_tasks_list_visibility.py` covering SCN-003, SC-002, FR-005, FR-006, DESIGN-REQ-003, and DESIGN-REQ-004.
- [X] T018 Add failing hermetic integration test for four broad compatibility URL cases in `tests/integration/api/test_tasks_list_visibility.py` covering SCN-004, SCN-005, SC-003, FR-007, FR-008, FR-010, DESIGN-REQ-008, DESIGN-REQ-015, and DESIGN-REQ-022.
- [X] T019 Run targeted integration coverage for `tests/integration/api/test_tasks_list_visibility.py` or `./tools/test_integration.sh` and confirm T017-T018 fail for the expected missing behavior before production code changes.

### Red-First Confirmation

- [X] T020 Record the expected red-first failures from T015, T016, and T019 in `artifacts/298-canonical-task-run-list/red-first.md` before editing production code.

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [X] T021 If T008 proves boot payload identity is incomplete, update `/tasks/list` page-key, initial path, dashboard config, or wide layout behavior in `api_service/api/routers/task_dashboard.py` for FR-003, SCN-001, SC-001, and DESIGN-REQ-002.
- [X] T022 If T011 proves browser data access is not MoonMind-owned, update list fetch configuration in `frontend/src/entrypoints/tasks-list.tsx` for FR-004, SC-005, DESIGN-REQ-002, and DESIGN-REQ-022.
- [X] T023 If T009 proves canonical redirects regressed, restore `/tasks` and `/tasks/tasks-list` redirects in `api_service/api/routers/task_dashboard.py` for FR-001, FR-002, SCN-002, and SC-001.

### Implementation

- [X] T024 Implement task-safe compatibility URL parsing in `frontend/src/entrypoints/tasks-list.tsx` for FR-005, FR-007, FR-010, SCN-004, SC-003, DESIGN-REQ-015, and DESIGN-REQ-022.
- [X] T025 Remove ordinary Scope, Workflow Type, and Entry broad-workflow controls from `frontend/src/entrypoints/tasks-list.tsx` while preserving task-safe status, repository, pagination, sorting, and live-update behavior for FR-006, FR-009, SCN-006, SC-004, DESIGN-REQ-003, and DESIGN-REQ-004.
- [X] T026 Remove workflow-type broad browsing metadata from ordinary Tasks List desktop and mobile row rendering in `frontend/src/entrypoints/tasks-list.tsx` for FR-006, FR-009, SCN-006, and DESIGN-REQ-004.
- [X] T027 Implement recoverable message or manifest routing behavior for manifest-oriented compatibility URLs in `frontend/src/entrypoints/tasks-list.tsx` for FR-007, FR-008, SCN-004, and DESIGN-REQ-015.
- [X] T028 Implement diagnostics handoff or recoverable broad-workflow message behavior for system/all compatibility URLs in `frontend/src/entrypoints/tasks-list.tsx` for FR-007, FR-008, FR-010, SCN-005, DESIGN-REQ-008, DESIGN-REQ-015, and DESIGN-REQ-022.
- [X] T029 Preserve or tighten task-scope list query normalization in `api_service/api/routers/executions.py` so ordinary task-run list boundaries remain `MoonMind.Run` plus `mm_entry=run` for FR-005, FR-006, FR-010, SC-002, DESIGN-REQ-003, and DESIGN-REQ-022.
- [X] T030 Update any affected route boot behavior in `api_service/api/routers/task_dashboard.py` to keep `/tasks/list` page identity and runtime dashboard config stable for FR-003, SCN-001, SC-001, and DESIGN-REQ-002.
- [X] T031 Update `frontend/src/entrypoints/tasks-list.test.tsx` expectations that previously asserted raw all-workflows scope or ordinary manifest entry filtering so they now assert fail-safe THOR-370 behavior for FR-007, FR-008, FR-010, SCN-004, and DESIGN-REQ-015.

### Story Validation

- [X] T032 Run `pytest tests/unit/api/routers/test_task_dashboard.py tests/unit/api/routers/test_executions.py -q` and make all THOR-370 router/API unit tests pass.
- [X] T033 Run `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` and make all THOR-370 UI unit tests pass.
- [X] T034 Run targeted integration coverage for `tests/integration/api/test_tasks_list_visibility.py` or `./tools/test_integration.sh` and make all THOR-370 integration tests pass.
- [ ] T035 Verify manually or with browser smoke that `/tasks/list`, `/tasks`, `/tasks/tasks-list`, `scope=system`, `scope=all`, system `workflowType`, and `entry=manifest` satisfy the independent test in `specs/298-canonical-task-run-list/spec.md`.

**Checkpoint**: The Task-Focused Execution List story is independently testable and covered by unit, UI, and integration evidence.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T036 [P] Review `specs/298-canonical-task-run-list/contracts/tasks-list-visibility-contract.md` against final behavior and update only if implementation reveals a contract wording mismatch.
- [X] T037 [P] Review `specs/298-canonical-task-run-list/quickstart.md` against final commands and update only if validation commands changed.
- [X] T038 Run `./tools/test_unit.sh` for full unit verification after targeted tests pass.
- [X] T039 Run `./tools/test_integration.sh` when `tests/integration/api/test_tasks_list_visibility.py` is marked `integration_ci` or API/Temporal visibility boundaries changed, and document the result in `specs/298-canonical-task-run-list/quickstart.md`.
- [ ] T040 Run `/speckit.verify` and preserve THOR-370, the original Jira preset brief, DESIGN-REQ mappings, and test evidence in the final verification output for FR-011 and SC-006.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks story work.
- Phase 3 depends on Phase 2.
- Phase 4 depends on the story passing targeted unit, UI, and integration validation.

### Story Order

- Unit tests T008-T014 must be written before implementation.
- Integration tests T017-T018 must be written before implementation.
- Red-first confirmation T020 must complete before implementation tasks T021-T031.
- Conditional fallback tasks T021-T023 run only if verification tests expose a gap in implemented-unverified behavior.
- Implementation tasks T024-T031 precede story validation T032-T035.
- Full verification T038-T040 runs only after story validation passes.

## Parallel Opportunities

- T004, T005, T006, and T007 can run in parallel because they touch different test scaffolding files.
- T008, T010, T011, and T017 can be authored in parallel because they touch different files.
- T036 and T037 can run in parallel after implementation because they touch different planning artifacts.

## Parallel Example

```bash
# Parallel test authoring after Phase 2:
Task: "T008 Add failing router boot payload tests in tests/unit/api/routers/test_task_dashboard.py"
Task: "T010 Add failing execution API scope tests in tests/unit/api/routers/test_executions.py"
Task: "T011 Add failing Tasks List UI fetch tests in frontend/src/entrypoints/tasks-list.test.tsx"
Task: "T017 Add failing hermetic integration mixed-row test in tests/integration/api/test_tasks_list_visibility.py"
```

## Implementation Strategy

1. Preserve existing verified route behavior and traceability for FR-001, FR-002, SCN-002, and implemented-verified rows.
2. Add verification tests for implemented-unverified rows first; skip conditional fallback implementation if those tests pass.
3. For missing and partial rows, write unit/UI/API/integration tests first and confirm red failures.
4. Implement the smallest task-list UI and API boundary changes needed to make ordinary `/tasks/list` fail safe.
5. Validate the single story with targeted tests, full unit suite, conditional integration suite, and `/speckit.verify`.

## Coverage Matrix

| Coverage Type | IDs Covered |
| --- | --- |
| Code + tests | FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SCN-003, SCN-004, SCN-005, SCN-006, SC-002, SC-003, SC-004, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-008, DESIGN-REQ-015, DESIGN-REQ-022 |
| Verification tests + conditional fallback | FR-003, FR-004, FR-011, SCN-001, SC-001, SC-005, SC-006, DESIGN-REQ-002 |
| Already verified final regression | FR-001, FR-002, SCN-002 |
| Final verification | THOR-370 original brief, FR-011, SC-006, all DESIGN-REQ mappings |

## Notes

- This task list covers exactly one story.
- No application implementation is included in this task-generation step.
- Unit and integration test tasks precede production implementation tasks.
- Red-first confirmation is required before production code changes.
- `/speckit.verify` is the final task after implementation and tests pass.
