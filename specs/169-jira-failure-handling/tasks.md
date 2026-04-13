# Tasks: Jira Failure Handling

**Input**: Design documents from `/specs/169-jira-failure-handling/`
**Prerequisites**: `plan.md` (required), `spec.md` (required for user stories), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Required. The feature request explicitly requires test-driven development and validation tests for runtime code changes.
**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files and does not depend on incomplete tasks.
- **[Story]**: User story label for story-phase tasks only.
- Each task includes an exact repository file path.
- `DOC-REQ-*` identifiers are carried into both implementation and validation tasks for traceability.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm current Jira browser surfaces and runtime test entrypoints before story work begins.

- [ ] T001 Review existing Jira browser router failure mapping surfaces in `api_service/api/routers/jira_browser.py`
- [ ] T002 [P] Review existing Jira browser service empty-state normalization surfaces in `moonmind/integrations/jira/browser.py`
- [ ] T003 [P] Review existing Create page Jira query and browser-panel surfaces in `frontend/src/entrypoints/task-create.tsx`
- [ ] T004 [P] Review focused validation locations in `tests/unit/api/routers/test_jira_browser.py`, `tests/unit/integrations/test_jira_browser_service.py`, and `frontend/src/entrypoints/task-create.test.tsx`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared failure/empty-state expectations before individual user stories are implemented.

**CRITICAL**: No user story work should begin until the existing endpoint, UI, and validation surfaces are understood.

- [ ] T005 Define the Jira browser failure detail fields from `contracts/jira-browser-failure.yaml` in `api_service/api/routers/jira_browser.py`
- [ ] T006 [P] Confirm existing Jira list/detail response models can represent empty states for DOC-REQ-001 and DOC-REQ-002 in `moonmind/integrations/jira/browser.py`
- [ ] T007 [P] Identify current frontend query error rendering paths for DOC-REQ-001 and DOC-REQ-002 in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: Failure envelope, empty-state model, and frontend error locations are identified. User story implementation can start.

---

## Phase 3: User Story 1 - Contain Jira Backend Failures (Priority: P1)

**Goal**: Jira browser endpoints return safe structured MoonMind errors for known and unexpected backend failures.
**Independent Test**: Exercise router failures and verify structured details include safe code/message/source/action fields with no leaked secret-like content.

### Tests for User Story 1

- [ ] T008 [P] [US1] Add failing router test for known JiraToolError detail including `source` and `action` for DOC-REQ-001 in `tests/unit/api/routers/test_jira_browser.py`
- [ ] T009 [P] [US1] Add failing router regression test for secret-like Jira error sanitization for DOC-REQ-002 in `tests/unit/api/routers/test_jira_browser.py`
- [ ] T010 [P] [US1] Add failing router test for unexpected Jira browser exceptions returning safe structured errors for DOC-REQ-001 in `tests/unit/api/routers/test_jira_browser.py`
- [ ] T011 [P] [US1] Add or confirm service empty-state regression coverage for no projects, boards, columns, or issues for DOC-REQ-001 and DOC-REQ-002 in `tests/unit/integrations/test_jira_browser_service.py`

### Implementation for User Story 1

- [ ] T012 [US1] Implement structured JiraToolError mapping with `code`, `message`, `source`, and safe `action` for DOC-REQ-001 in `api_service/api/routers/jira_browser.py`
- [ ] T013 [US1] Implement secret-like message sanitization for browser-facing Jira failures for DOC-REQ-002 in `api_service/api/routers/jira_browser.py`
- [ ] T014 [US1] Implement safe unexpected-exception mapping for Jira browser endpoints for DOC-REQ-001 in `api_service/api/routers/jira_browser.py`
- [ ] T015 [US1] Preserve or adjust successful empty list/model responses for DOC-REQ-001 and DOC-REQ-002 in `moonmind/integrations/jira/browser.py`

### Validation for User Story 1

- [ ] T016 [US1] Run backend Jira browser router tests for DOC-REQ-001 and DOC-REQ-002 with `pytest tests/unit/api/routers/test_jira_browser.py -q`
- [ ] T017 [US1] Run Jira browser service tests for DOC-REQ-001 and DOC-REQ-002 with `pytest tests/unit/integrations/test_jira_browser_service.py -q`

**Checkpoint**: Backend Jira browser failures are structured, safe, and independently verified.

---

## Phase 4: User Story 2 - Keep Jira UI Failures Local (Priority: P1)

**Goal**: Jira project, board, column, issue-list, and issue-detail failures or empty states render only inside the Jira browser panel and do not mutate the draft.
**Independent Test**: Simulate failed and empty Jira browser endpoints in the Create page and verify inline browser-panel messages plus editable step and preset fields.

### Tests for User Story 2

- [ ] T018 [P] [US2] Add failing Create page test for project load failure rendering local manual-continuation copy for DOC-REQ-001 and DOC-REQ-002 in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T019 [P] [US2] Add failing Create page test for board, column, or issue-list failure and empty project, board, column, or issue-list states staying local to the browser panel for DOC-REQ-001 and DOC-REQ-002 in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T020 [P] [US2] Add failing Create page test for issue-detail failure not importing or mutating preset/step draft content for DOC-REQ-001 and DOC-REQ-003 in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 2

- [ ] T021 [US2] Implement shared local Jira error and empty-state message formatting with manual-continuation guidance for DOC-REQ-002 in `frontend/src/entrypoints/task-create.tsx`
- [ ] T022 [US2] Render project and board query failures and empty states inside the Jira browser panel only for DOC-REQ-001 and DOC-REQ-002 in `frontend/src/entrypoints/task-create.tsx`
- [ ] T023 [US2] Render column, issue-list, and issue-detail query failures and empty issue-list states inside the Jira browser panel only for DOC-REQ-001 and DOC-REQ-002 in `frontend/src/entrypoints/task-create.tsx`
- [ ] T024 [US2] Ensure issue-detail failure leaves import preview/actions unavailable without mutating draft fields for DOC-REQ-001 and DOC-REQ-003 in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 2

- [ ] T025 [US2] Run focused Create page Jira failure and empty-state tests for DOC-REQ-001, DOC-REQ-002, and DOC-REQ-003 with `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Jira UI failures and empty states are local to the browser panel and independently verified.

---

## Phase 5: User Story 3 - Preserve Manual Creation and Submission (Priority: P1)

**Goal**: Manual task editing and the existing Create submission path remain usable after Jira failures.
**Independent Test**: Fail a Jira browser request, close or ignore the browser, manually enter valid instructions, and submit through the existing Create flow.

### Tests for User Story 3

- [ ] T026 [P] [US3] Add failing Create page test proving manual step editing remains available after Jira project failure for DOC-REQ-001 and DOC-REQ-003 in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T027 [P] [US3] Add failing Create page test proving manual task creation uses the existing submission path after Jira failure for DOC-REQ-003 and DOC-REQ-004 in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T028 [P] [US3] Add failing Create page regression test proving Jira failure does not alter submission payload shape or objective precedence for DOC-REQ-003 and DOC-REQ-004 in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [ ] T029 [US3] Ensure Jira query error state is not included in Create button disabled logic for DOC-REQ-001 and DOC-REQ-003 in `frontend/src/entrypoints/task-create.tsx`
- [ ] T030 [US3] Ensure manual preset and step editing handlers remain independent of Jira failure state for DOC-REQ-001 and DOC-REQ-003 in `frontend/src/entrypoints/task-create.tsx`
- [ ] T031 [US3] Ensure task submission assembly excludes Jira failure/provenance metadata and preserves existing payload shape for DOC-REQ-003 and DOC-REQ-004 in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 3

- [ ] T032 [US3] Run focused Create page manual submission tests for DOC-REQ-003 and DOC-REQ-004 with `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Manual Create page editing and submission remain available after Jira failure.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability, security, and repository verification across all stories.

- [ ] T033 [P] Verify every DOC-REQ-001 through DOC-REQ-004 has at least one implementation task and one validation task in `specs/169-jira-failure-handling/tasks.md`
- [ ] T034 [P] Confirm failure contract examples align with implemented error shape in `specs/169-jira-failure-handling/contracts/jira-browser-failure.yaml`
- [ ] T035 [P] Update quickstart verification commands if final test commands differ in `specs/169-jira-failure-handling/quickstart.md`
- [ ] T036 Run final unit verification for DOC-REQ-004 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **User Story 1 (Phase 3)**: Depends on Foundational; can ship backend structured failures independently.
- **User Story 2 (Phase 4)**: Depends on Foundational; can proceed after or in parallel with User Story 1 once the frontend consumes existing error messages, but final behavior benefits from User Story 1's structured backend envelope.
- **User Story 3 (Phase 5)**: Depends on Foundational; can proceed after User Story 2 tests identify local browser failure state, but remains independently verifiable.
- **Polish (Phase 6)**: Depends on selected story phases being complete.

### User Story Dependencies

- **US1**: No dependency on other user stories after Phase 2.
- **US2**: No hard dependency on US1 because frontend can test failed responses with mocked fetch, but final end-to-end behavior should align with US1's structured errors.
- **US3**: No hard dependency on US1; depends only on Create page controls remaining independent from Jira failure state.

### Within Each User Story

- Write failing automated tests before production changes.
- Backend router tests precede router failure mapping changes.
- Frontend Create page tests precede UI error rendering and submit-state changes.
- Story validation runs before moving to cross-cutting verification.

---

## Parallel Opportunities

- T002, T003, and T004 can run in parallel during setup.
- T006 and T007 can run in parallel during foundational analysis.
- T008, T009, T010, and T011 can be authored in parallel because they target independent backend cases.
- T018, T019, and T020 can be authored in parallel because they target separate frontend failure and empty-state cases.
- T026, T027, and T028 can be authored in parallel because they target separate manual-creation assertions.
- T033, T034, and T035 can run in parallel during polish.

## Parallel Example: User Story 1

```bash
Task: "Add failing router test for known JiraToolError detail including source and action in tests/unit/api/routers/test_jira_browser.py"
Task: "Add failing router regression test for secret-like Jira error sanitization in tests/unit/api/routers/test_jira_browser.py"
Task: "Add failing router test for unexpected Jira browser exceptions in tests/unit/api/routers/test_jira_browser.py"
Task: "Add or confirm service empty-state regression coverage in tests/unit/integrations/test_jira_browser_service.py"
```

## Parallel Example: User Story 2

```bash
Task: "Add failing Create page test for project load failure rendering local manual-continuation copy in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing Create page test for board, column, or issue-list failure staying local to the browser panel in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing Create page test for issue-detail failure not mutating preset/step draft content in frontend/src/entrypoints/task-create.test.tsx"
```

## Parallel Example: User Story 3

```bash
Task: "Add failing Create page test proving manual step editing remains available after Jira project failure in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing Create page test proving manual task creation uses the existing submission path after Jira failure in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing Create page regression test proving Jira failure does not alter submission payload shape in frontend/src/entrypoints/task-create.test.tsx"
```

---

## Implementation Strategy

### MVP First

Complete Phase 1, Phase 2, and User Story 1 first. This delivers the backend safety boundary for all Jira browser endpoints and prevents raw or inconsistent failures from reaching the browser.

### Incremental Delivery

1. Deliver US1 backend structured failures and secret-safe responses.
2. Deliver US2 local frontend browser-panel error rendering.
3. Deliver US3 manual creation and unchanged submission safeguards.
4. Run polish traceability and full unit verification.

### Validation Strategy

Use test-first implementation for each runtime story. Focused tests must fail before production changes and pass after implementation. Final verification must include backend router/service tests, Create page Vitest coverage, and `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
