# Tasks: Jira UI Test Coverage

**Input**: Design documents from `/specs/170-jira-ui-test-coverage/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Required. This runtime feature is explicitly test-driven and must include production runtime code fixes where new validation exposes behavior gaps.

**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare the active feature context and identify the existing runtime/test surfaces before story work begins.

- [ ] T001 Confirm active branch and current feature artifacts in `specs/170-jira-ui-test-coverage/spec.md`, `specs/170-jira-ui-test-coverage/plan.md`, and `specs/170-jira-ui-test-coverage/contracts/requirements-traceability.md`
- [ ] T002 [P] Review current frontend Jira fixtures and helper coverage in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T003 [P] Review current backend Jira browser fixtures in `tests/unit/api/routers/test_jira_browser.py` and `tests/unit/integrations/test_jira_browser_service.py`
- [ ] T004 [P] Review current runtime config fixture coverage in `tests/unit/api/routers/test_task_dashboard_view_model.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish reusable fixture coverage and traceability scaffolding that all story phases rely on.

**Critical**: No user story work should begin until these tasks are complete.

- [ ] T005 Add or normalize shared Jira runtime config fixture helpers in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-001, DOC-REQ-005, DOC-REQ-011
- [ ] T006 [P] Add or normalize reusable Jira service response fixtures in `tests/unit/integrations/test_jira_browser_service.py` for DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-010
- [ ] T007 [P] Add or normalize reusable Jira router fake-service behavior in `tests/unit/api/routers/test_jira_browser.py` for DOC-REQ-001, DOC-REQ-004, DOC-REQ-010
- [ ] T008 Verify each DOC-REQ row in `specs/170-jira-ui-test-coverage/contracts/requirements-traceability.md` has planned implementation and validation coverage before starting story tasks

**Checkpoint**: Shared fixtures and traceability are ready. User story work can proceed in priority order or in parallel where marked.

---

## Phase 3: User Story 1 - Protect Jira Rollout Boundaries (Priority: P1)

**Goal**: Prove Jira controls and browser endpoint discovery are exposed only through explicit Create-page runtime config.
**Independent Test**: Runtime config and Create page tests pass with Jira disabled, enabled, trusted tooling enabled separately, incomplete endpoint templates, and configured defaults.

### Tests for User Story 1

- [ ] T009 [P] [US1] Add or strengthen runtime config tests for disabled Jira UI, enabled Jira UI, trusted tooling separation, MoonMind-owned endpoints, and defaults in `tests/unit/api/routers/test_task_dashboard_view_model.py` for DOC-REQ-001
- [ ] T010 [P] [US1] Add or strengthen Create page tests for hidden Jira controls when disabled and disabled browser when endpoint templates are incomplete in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-001, DOC-REQ-011

### Implementation for User Story 1

- [ ] T011 [US1] Patch Jira runtime config gating and endpoint template exposure in `api_service/api/routers/task_dashboard_view_model.py` if T009 exposes gaps for DOC-REQ-001
- [ ] T012 [US1] Patch Create page Jira integration gating and endpoint-template validation in `frontend/src/entrypoints/task-create.tsx` if T010 exposes gaps for DOC-REQ-001, DOC-REQ-011

### Validation for User Story 1

- [ ] T013 [US1] Run `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_task_dashboard_view_model.py -q` and confirm DOC-REQ-001 coverage
- [ ] T014 [US1] Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and confirm Jira disabled-control assertions for DOC-REQ-001, DOC-REQ-011

**Checkpoint**: Jira rollout exposure is independently protected.

---

## Phase 4: User Story 2 - Validate Jira Browsing Behavior (Priority: P1)

**Goal**: Prove the shared Jira browser opens from preset or step targets, renders ordered board columns, switches visible issues by column, and loads normalized preview content without mutating draft fields.
**Independent Test**: Create page and service tests pass for preset/step browser opening, board column order, column switching, issue preview, and no mutation on issue selection.

### Tests for User Story 2

- [ ] T015 [P] [US2] Add or strengthen Create page tests for opening Jira browser from preset and step targets in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-005, DOC-REQ-011
- [ ] T016 [P] [US2] Add or strengthen Create page tests for ordered columns, column switching, issue preview, and no mutation on issue selection in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-011
- [ ] T017 [P] [US2] Add or strengthen service tests for board column ordering, empty columns, mapped issue buckets, unmapped statuses, and issue-detail normalized text in `tests/unit/integrations/test_jira_browser_service.py` for DOC-REQ-002, DOC-REQ-003, DOC-REQ-004

### Implementation for User Story 2

- [ ] T018 [US2] Patch Jira browser state, target labeling, column selection, issue selection, or preview rendering in `frontend/src/entrypoints/task-create.tsx` if T015 or T016 exposes gaps for DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-011
- [ ] T019 [US2] Patch board column, issue grouping, empty-state, unmapped-status, or issue-detail normalization in `moonmind/integrations/jira/browser.py` if T017 exposes gaps for DOC-REQ-002, DOC-REQ-003, DOC-REQ-004

### Validation for User Story 2

- [ ] T020 [US2] Run `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/integrations/test_jira_browser_service.py -q` and confirm DOC-REQ-002, DOC-REQ-003, DOC-REQ-004 coverage
- [ ] T021 [US2] Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and confirm browsing behavior for DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-011

**Checkpoint**: Jira browsing and issue preview are independently protected.

---

## Phase 5: User Story 3 - Validate Jira Import Semantics (Priority: P1)

**Goal**: Prove explicit Jira import updates only the selected preset or step target and preserves preset reapply, template detachment, provenance, and submission invariants.
**Independent Test**: Create page tests pass for replace and append imports, import modes, selected target isolation, template-bound step detachment, preset reapply signaling, advisory provenance, and unchanged submission payload shape.

### Tests for User Story 3

- [ ] T022 [P] [US3] Add or strengthen Create page tests for selecting an issue without import, preset replace import, preset append import, and import-mode text selection in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-006, DOC-REQ-011
- [ ] T023 [P] [US3] Add or strengthen Create page tests for selected-step import isolation and missing or empty import target safety in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-005, DOC-REQ-006, DOC-REQ-011
- [ ] T024 [P] [US3] Add or strengthen Create page tests for template-bound step detachment and warning behavior in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-007, DOC-REQ-011
- [ ] T025 [P] [US3] Add or strengthen Create page tests for preset reapply-needed signaling after applied preset import in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-008, DOC-REQ-011
- [ ] T026 [P] [US3] Add or strengthen Create page tests proving Jira provenance remains advisory and task submission payload shape remains unchanged in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-009, DOC-REQ-011

### Implementation for User Story 3

- [ ] T027 [US3] Patch Jira import preview, import-mode, replace, and append behavior in `frontend/src/entrypoints/task-create.tsx` if T022 exposes gaps for DOC-REQ-006, DOC-REQ-011
- [ ] T028 [US3] Patch selected step import targeting and target-missing safety in `frontend/src/entrypoints/task-create.tsx` if T023 exposes gaps for DOC-REQ-005, DOC-REQ-006, DOC-REQ-011
- [ ] T029 [US3] Patch template-bound step manual customization and warning behavior in `frontend/src/entrypoints/task-create.tsx` if T024 exposes gaps for DOC-REQ-007, DOC-REQ-011
- [ ] T030 [US3] Patch preset reapply-needed signaling and expanded-step preservation in `frontend/src/entrypoints/task-create.tsx` if T025 exposes gaps for DOC-REQ-008, DOC-REQ-011
- [ ] T031 [US3] Patch Jira provenance handling or submission assembly in `frontend/src/entrypoints/task-create.tsx` if T026 exposes gaps for DOC-REQ-009, DOC-REQ-011

### Validation for User Story 3

- [ ] T032 [US3] Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and confirm import semantics for DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-011

**Checkpoint**: Jira import behavior is independently protected.

---

## Phase 6: User Story 4 - Validate Trusted Backend Browser Path (Priority: P2)

**Goal**: Prove backend browser operations use the trusted MoonMind Jira boundary, normalize Create-page read models, enforce policy, and sanitize browser-facing failures.
**Independent Test**: Router and service tests pass for all browser endpoints, policy denial, request validation, normalization, empty states, unexpected errors, and secret-safe failures.

### Tests for User Story 4

- [ ] T033 [P] [US4] Add or strengthen router tests for connection verification, projects, boards, columns, board issues, and issue detail in `tests/unit/api/routers/test_jira_browser.py` for DOC-REQ-001, DOC-REQ-004, DOC-REQ-010
- [ ] T034 [P] [US4] Add or strengthen router tests for structured known errors, unexpected errors, trace-like messages, and secret-like message sanitization in `tests/unit/api/routers/test_jira_browser.py` for DOC-REQ-010
- [ ] T035 [P] [US4] Add or strengthen service tests for project allowlists, connection verification policy, invalid input rejection, allowed project fetching, board grouping, issue detail, and no secret leakage in `tests/unit/integrations/test_jira_browser_service.py` for DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-010

### Implementation for User Story 4

- [ ] T036 [US4] Patch browser endpoint routing, response models, error mapping, or sanitization in `api_service/api/routers/jira_browser.py` if T033 or T034 exposes gaps for DOC-REQ-001, DOC-REQ-004, DOC-REQ-010
- [ ] T037 [US4] Patch Jira browser service policy checks, request validation, normalization, or error safety in `moonmind/integrations/jira/browser.py` if T035 exposes gaps for DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-010

### Validation for User Story 4

- [ ] T038 [US4] Run `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_jira_browser.py tests/unit/integrations/test_jira_browser_service.py -q` and confirm backend trusted-boundary coverage for DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-010

**Checkpoint**: Backend Jira browser trust boundary is independently protected.

---

## Phase 7: User Story 5 - Keep Jira Failure Additive (Priority: P2)

**Goal**: Prove Jira loading failures stay local to the browser and never block manual task editing or valid task creation.
**Independent Test**: Frontend and backend tests pass for endpoint failures, empty states, failed issue detail, manual editing after failure, and unchanged manual submission payload.

### Tests for User Story 5

- [ ] T039 [P] [US5] Add or strengthen Create page tests for project, board, column, issue-list, and issue-detail failures remaining inside the Jira browser in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-010, DOC-REQ-011
- [ ] T040 [P] [US5] Add or strengthen Create page tests proving manual preset editing, step editing, and valid task creation still work after Jira failure in `frontend/src/entrypoints/task-create.test.tsx` for DOC-REQ-009, DOC-REQ-010, DOC-REQ-011
- [ ] T041 [P] [US5] Add or strengthen router/service tests for empty browser models and safe structured failure responses in `tests/unit/api/routers/test_jira_browser.py` and `tests/unit/integrations/test_jira_browser_service.py` for DOC-REQ-010

### Implementation for User Story 5

- [ ] T042 [US5] Patch local Jira error and empty-state handling in `frontend/src/entrypoints/task-create.tsx` if T039 or T040 exposes gaps for DOC-REQ-010, DOC-REQ-011
- [ ] T043 [US5] Patch browser API empty-state response handling or safe error shaping in `api_service/api/routers/jira_browser.py` and `moonmind/integrations/jira/browser.py` if T041 exposes gaps for DOC-REQ-010

### Validation for User Story 5

- [ ] T044 [US5] Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` and confirm local failure behavior for DOC-REQ-009, DOC-REQ-010, DOC-REQ-011
- [ ] T045 [US5] Run `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_jira_browser.py tests/unit/integrations/test_jira_browser_service.py -q` and confirm backend failure safety for DOC-REQ-010

**Checkpoint**: Jira failure behavior is independently protected and additive.

---

## Phase 8: Polish & Cross-Cutting Validation

**Purpose**: Confirm all stories satisfy traceability, runtime scope, and final unit validation requirements.

- [ ] T046 Verify every DOC-REQ ID appears in at least one implementation task and one validation task in `specs/170-jira-ui-test-coverage/tasks.md`
- [ ] T047 Run `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_jira_browser.py tests/unit/integrations/test_jira_browser_service.py tests/unit/api/routers/test_task_dashboard_view_model.py -q` for focused backend Phase 9 validation
- [ ] T048 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` for supported Create page validation
- [ ] T049 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final full unit-suite verification
- [ ] T050 Update verification notes in `specs/170-jira-ui-test-coverage/quickstart.md` if command names, expected outputs, or prerequisite behavior changed during implementation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies. Can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion. Blocks all user story work.
- **User Story 1 (Phase 3)**: Depends on Foundational. MVP validation for safe rollout gating.
- **User Story 2 (Phase 4)**: Depends on Foundational. Can run after or in parallel with User Story 1 once fixtures are ready.
- **User Story 3 (Phase 5)**: Depends on User Story 2 because import tests require browser preview and target behavior to be stable.
- **User Story 4 (Phase 6)**: Depends on Foundational. Can run in parallel with frontend story work because it touches backend tests and services.
- **User Story 5 (Phase 7)**: Depends on User Stories 1, 2, and 4 because failure assertions rely on runtime gating, browser flows, and backend error shaping.
- **Polish (Phase 8)**: Depends on all selected user stories.

### User Story Dependencies

- **US1 Protect Jira Rollout Boundaries**: Independent MVP after Foundational.
- **US2 Validate Jira Browsing Behavior**: Independent after Foundational; required before import semantics.
- **US3 Validate Jira Import Semantics**: Depends on US2 browser/preview behavior.
- **US4 Validate Trusted Backend Browser Path**: Independent after Foundational; can run in parallel with US1/US2/US3.
- **US5 Keep Jira Failure Additive**: Depends on browser, backend error, and submission assertions from earlier stories.

## Parallel Execution Examples

### US1

```text
Task A: T009 in tests/unit/api/routers/test_task_dashboard_view_model.py
Task B: T010 in frontend/src/entrypoints/task-create.test.tsx
```

### US2

```text
Task A: T015 and T016 in frontend/src/entrypoints/task-create.test.tsx
Task B: T017 in tests/unit/integrations/test_jira_browser_service.py
```

### US3

```text
Task A: T022 and T023 in frontend/src/entrypoints/task-create.test.tsx
Task B: T024 and T025 in frontend/src/entrypoints/task-create.test.tsx after Task A establishes common helpers
Task C: T026 in frontend/src/entrypoints/task-create.test.tsx
```

### US4

```text
Task A: T033 and T034 in tests/unit/api/routers/test_jira_browser.py
Task B: T035 in tests/unit/integrations/test_jira_browser_service.py
```

### US5

```text
Task A: T039 and T040 in frontend/src/entrypoints/task-create.test.tsx
Task B: T041 in tests/unit/api/routers/test_jira_browser.py and tests/unit/integrations/test_jira_browser_service.py
```

## Implementation Strategy

### MVP First

Complete Phases 1-3 first. This proves the Jira browser cannot accidentally appear without enabled runtime config and gives operators the safest rollout boundary.

### Incremental Delivery

1. Finish Setup and Foundational fixture tasks.
2. Deliver US1 rollout-boundary coverage.
3. Deliver US2 browsing coverage.
4. Deliver US3 import semantics coverage.
5. Deliver US4 backend trusted-boundary coverage.
6. Deliver US5 additive failure coverage.
7. Run cross-cutting validation and final unit suite.

### Traceability Rule

Each task that validates or patches behavior carries the relevant `DOC-REQ-*` IDs. Implementation is incomplete if any DOC-REQ ID lacks both a runtime implementation task and a validation task.
