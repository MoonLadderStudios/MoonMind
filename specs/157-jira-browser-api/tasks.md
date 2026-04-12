# Tasks: Jira Browser API

**Input**: Design documents from `/specs/157-jira-browser-api/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
**Tests**: Required. The feature request explicitly calls for test-driven development and validation tests.
**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or only adds tests for a bounded surface
- **[Story]**: Maps to user stories from `spec.md`
- Each task includes exact file paths and carries relevant `DOC-REQ-*` IDs where applicable

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the existing Jira boundaries and feature artifacts before changing runtime code.

- [ ] T001 Review trusted Jira auth/client/tool boundaries in `moonmind/integrations/jira/auth.py`, `moonmind/integrations/jira/client.py`, `moonmind/integrations/jira/tool.py`, and `docs/Tools/JiraIntegration.md`
- [ ] T002 Review Create-page runtime config gating in `api_service/api/routers/task_dashboard_view_model.py` and tests in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [ ] T003 [P] Review planned response models and API contract in `specs/157-jira-browser-api/data-model.md` and `specs/157-jira-browser-api/contracts/jira-browser-openapi.yaml`
- [ ] T004 [P] Review existing Jira regression tests in `tests/unit/integrations/test_jira_auth.py`, `tests/unit/integrations/test_jira_client.py`, and `tests/unit/integrations/test_jira_tool_service.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create shared browser service/router seams that all user stories depend on.

**CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T005 [P] Create browser response model skeletons for connection, project, board, column, issue summary, issue detail, recommended imports, and safe errors in `moonmind/integrations/jira/browser.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007)
- [ ] T006 [P] Create the Jira browser router skeleton with dependency wiring in `api_service/api/routers/jira_browser.py` (DOC-REQ-003, DOC-REQ-007)
- [ ] T007 Register the Jira browser router in `api_service/main.py` without changing existing Create-page routes or submission behavior (DOC-REQ-001, DOC-REQ-003)
- [ ] T008 Add shared test fixtures for stubbed Jira browser service calls in `tests/unit/integrations/test_jira_browser_service.py` and `tests/unit/api/routers/test_jira_browser.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-007)

**Checkpoint**: Browser read-model and router seams exist; user-story work can proceed.

---

## Phase 3: User Story 1 - Verify Trusted Jira Availability (Priority: P1) MVP

**Goal**: A task author or operator can verify trusted Jira availability without exposing browser-held Jira credentials.

**Independent Test**: Configure a trusted Jira binding in tests, request connection verification, and confirm safe metadata or structured safe failure.

### Tests for User Story 1

- [ ] T009 [P] [US1] Add service tests for account-scoped and project-scoped connection verification in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-002)
- [ ] T010 [P] [US1] Add service tests for disabled browser rollout and missing Jira configuration failures in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-003, DOC-REQ-007)
- [ ] T011 [P] [US1] Add router tests for `GET /api/jira/connections/verify` success and safe Jira error mapping in `tests/unit/api/routers/test_jira_browser.py` (DOC-REQ-003, DOC-REQ-007)
- [ ] T012 [P] [US1] Add redaction regression tests proving verification failures do not expose credential-like values in `tests/unit/api/routers/test_jira_browser.py` (DOC-REQ-003, DOC-REQ-007)

### Implementation for User Story 1

- [ ] T013 [US1] Implement `JiraBrowserService.verify_connection()` using the trusted Jira auth/client path in `moonmind/integrations/jira/browser.py` (DOC-REQ-002, DOC-REQ-003)
- [ ] T014 [US1] Implement browser rollout and project-policy checks for verification in `moonmind/integrations/jira/browser.py` (DOC-REQ-003, DOC-REQ-007)
- [ ] T015 [US1] Implement `GET /api/jira/connections/verify` and safe `JiraToolError` HTTP mapping in `api_service/api/routers/jira_browser.py` (DOC-REQ-003, DOC-REQ-007)

**Checkpoint**: User Story 1 can be verified independently through the connection verification endpoint.

---

## Phase 4: User Story 2 - Browse Projects, Boards, And Columns (Priority: P1)

**Goal**: A task author can browse allowed Jira projects, select a project board, and receive stable board columns in Jira order.

**Independent Test**: With an allowed project and stubbed Jira board configuration, request projects, boards, and columns; verify denied projects fail before data is returned.

### Tests for User Story 2

- [ ] T016 [P] [US2] Add service tests for allowed project listing with and without configured project allowlists in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004)
- [ ] T017 [P] [US2] Add service tests proving denied project board listing is rejected before Jira data is returned in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-003, DOC-REQ-004)
- [ ] T018 [P] [US2] Add service tests for board listing and board metadata normalization in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-002, DOC-REQ-004)
- [ ] T019 [P] [US2] Add service tests for column order, stable column IDs, empty column lists, and status ID mappings in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-004)
- [ ] T020 [P] [US2] Add router tests for `GET /api/jira/projects`, `GET /api/jira/projects/{projectKey}/boards`, and `GET /api/jira/boards/{boardId}/columns` in `tests/unit/api/routers/test_jira_browser.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004)

### Implementation for User Story 2

- [ ] T021 [US2] Implement `JiraBrowserService.list_projects()` with allowed-project filtering in `moonmind/integrations/jira/browser.py` (DOC-REQ-002, DOC-REQ-003)
- [ ] T022 [US2] Implement `JiraBrowserService.list_project_boards()` with project-policy enforcement in `moonmind/integrations/jira/browser.py` (DOC-REQ-002, DOC-REQ-004)
- [ ] T023 [US2] Implement board metadata normalization and Jira board configuration loading in `moonmind/integrations/jira/browser.py` (DOC-REQ-004)
- [ ] T024 [US2] Implement stable ordered column normalization with status ID mappings in `moonmind/integrations/jira/browser.py` (DOC-REQ-004)
- [ ] T025 [US2] Implement project, board, and column routes in `api_service/api/routers/jira_browser.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004)

**Checkpoint**: User Story 2 can be verified independently through project, board, and column browsing.

---

## Phase 5: User Story 3 - Browse Issues By Board Column (Priority: P1)

**Goal**: A task author can view Jira issues grouped into mapped columns, empty columns, and an explicit unmapped bucket.

**Independent Test**: With board columns and issues in mapped and unmapped statuses, request board issues and verify grouping, counts, filtering, and empty-column preservation.

### Tests for User Story 3

- [ ] T026 [P] [US3] Add service tests for grouping issues by status-to-column mapping in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-002, DOC-REQ-005)
- [ ] T027 [P] [US3] Add service tests for empty columns and computed column counts in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-005)
- [ ] T028 [P] [US3] Add service tests for unmapped status handling in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-005)
- [ ] T029 [P] [US3] Add service tests for optional issue key or summary filtering in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-005)
- [ ] T030 [P] [US3] Add router tests for `GET /api/jira/boards/{boardId}/issues` including query filtering in `tests/unit/api/routers/test_jira_browser.py` (DOC-REQ-002, DOC-REQ-005)

### Implementation for User Story 3

- [ ] T031 [US3] Implement `JiraBrowserService.list_board_issues()` using board status mappings in `moonmind/integrations/jira/browser.py` (DOC-REQ-002, DOC-REQ-005)
- [ ] T032 [US3] Implement normalized issue summary creation with safe display fields in `moonmind/integrations/jira/browser.py` (DOC-REQ-005)
- [ ] T033 [US3] Implement explicit `unmappedItems`, empty column arrays, column counts, and optional filtering in `moonmind/integrations/jira/browser.py` (DOC-REQ-005)
- [ ] T034 [US3] Implement the board issues route in `api_service/api/routers/jira_browser.py` (DOC-REQ-002, DOC-REQ-005)

**Checkpoint**: User Story 3 can be verified independently through the board issue listing endpoint.

---

## Phase 6: User Story 4 - Preview Normalized Issue Detail (Priority: P1)

**Goal**: A task author can select a Jira issue and preview normalized text ready for preset or step import.

**Independent Test**: With stubbed Jira rich-text issue detail, request issue detail and verify normalized description text, acceptance criteria text, and recommended import strings.

### Tests for User Story 4

- [ ] T035 [P] [US4] Add service tests for plain-text normalization of Jira rich-text descriptions in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-006)
- [ ] T036 [P] [US4] Add service tests for acceptance criteria extraction and missing-field empty states in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-006)
- [ ] T037 [P] [US4] Add service tests for recommended preset-instruction and step-instruction import text in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-006)
- [ ] T038 [P] [US4] Add service tests for issue project-policy denial before detail is returned in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-003, DOC-REQ-007)
- [ ] T039 [P] [US4] Add router tests for `GET /api/jira/issues/{issueKey}` success, invalid issue keys, and safe error mapping in `tests/unit/api/routers/test_jira_browser.py` (DOC-REQ-003, DOC-REQ-006, DOC-REQ-007)

### Implementation for User Story 4

- [ ] T040 [US4] Implement `JiraBrowserService.get_issue_detail()` with issue project-policy enforcement in `moonmind/integrations/jira/browser.py` (DOC-REQ-003, DOC-REQ-006, DOC-REQ-007)
- [ ] T041 [US4] Implement Jira rich-text-to-plain-text normalization helpers in `moonmind/integrations/jira/browser.py` (DOC-REQ-006)
- [ ] T042 [US4] Implement acceptance criteria extraction and missing-field empty-state handling in `moonmind/integrations/jira/browser.py` (DOC-REQ-006)
- [ ] T043 [US4] Implement target-specific recommended import generation in `moonmind/integrations/jira/browser.py` (DOC-REQ-006)
- [ ] T044 [US4] Implement the issue detail route in `api_service/api/routers/jira_browser.py` (DOC-REQ-003, DOC-REQ-006, DOC-REQ-007)

**Checkpoint**: User Story 4 can be verified independently through the issue detail endpoint.

---

## Phase 7: Polish & Cross-Cutting Verification

**Purpose**: Ensure cross-story safety, existing behavior preservation, and standard verification.

- [ ] T045 [P] Add or update regression coverage proving Create-page runtime config remains gated independently from trusted Jira tooling in `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-001, DOC-REQ-003)
- [ ] T046 [P] Run existing Jira boundary regression tests: `pytest tests/unit/integrations/test_jira_auth.py tests/unit/integrations/test_jira_client.py tests/unit/integrations/test_jira_tool_service.py tests/unit/api/test_mcp_tools_router.py -q` (DOC-REQ-003, DOC-REQ-007)
- [ ] T047 Run focused Jira browser tests: `pytest tests/unit/integrations/test_jira_browser_service.py tests/unit/api/routers/test_jira_browser.py -q` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007)
- [ ] T048 Run runtime config tests: `pytest tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py -q` (DOC-REQ-001, DOC-REQ-003)
- [ ] T049 Run standard unit verification with `./tools/test_unit.sh` (DOC-REQ-001)
- [ ] T050 Run runtime scope validation with `SPECIFY_FEATURE=157-jira-browser-api ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` (DOC-REQ-001)
- [ ] T051 Run runtime diff validation with `SPECIFY_FEATURE=157-jira-browser-api ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` (DOC-REQ-001)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 US1**: Depends on Phase 2 and provides the MVP verification path.
- **Phase 4 US2**: Depends on Phase 2; can start after shared service/router seams exist.
- **Phase 5 US3**: Depends on Phase 4 column normalization because issue grouping needs board status mappings.
- **Phase 6 US4**: Depends on Phase 2; board-context column mapping benefits from Phase 4 but issue detail remains independently testable without board context.
- **Phase 7 Polish**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 Verify Trusted Jira Availability**: First MVP slice after foundational setup.
- **US2 Browse Projects, Boards, And Columns**: Required before board issue grouping can be complete.
- **US3 Browse Issues By Board Column**: Requires column normalization from US2.
- **US4 Preview Normalized Issue Detail**: Can be implemented after foundational setup; does not require US3.

### DOC-REQ Coverage Gate

- **DOC-REQ-001**: Implementation T007; validation T045, T047, T048, T049, T050, T051.
- **DOC-REQ-002**: Implementation T013, T021, T022, T031, T034; validation T009, T016, T018, T026, T030, T047.
- **DOC-REQ-003**: Implementation T014, T015, T021, T025, T040, T044; validation T010, T011, T012, T017, T020, T038, T039, T045, T046, T047, T048.
- **DOC-REQ-004**: Implementation T022, T023, T024, T025; validation T016, T017, T018, T019, T020, T047.
- **DOC-REQ-005**: Implementation T031, T032, T033, T034; validation T026, T027, T028, T029, T030, T047.
- **DOC-REQ-006**: Implementation T040, T041, T042, T043, T044; validation T035, T036, T037, T039, T047.
- **DOC-REQ-007**: Implementation T014, T015, T040, T044; validation T010, T011, T012, T038, T039, T046, T047.

---

## Parallel Execution Examples

### User Story 1

```bash
Task: "T009 service verification tests in tests/unit/integrations/test_jira_browser_service.py"
Task: "T011 router verification tests in tests/unit/api/routers/test_jira_browser.py"
Task: "T012 redaction regression tests in tests/unit/api/routers/test_jira_browser.py"
```

### User Story 2

```bash
Task: "T016 project listing service tests in tests/unit/integrations/test_jira_browser_service.py"
Task: "T019 column normalization service tests in tests/unit/integrations/test_jira_browser_service.py"
Task: "T020 project/board/column router tests in tests/unit/api/routers/test_jira_browser.py"
```

### User Story 3

```bash
Task: "T026 issue grouping service tests in tests/unit/integrations/test_jira_browser_service.py"
Task: "T028 unmapped status service tests in tests/unit/integrations/test_jira_browser_service.py"
Task: "T030 board issues router tests in tests/unit/api/routers/test_jira_browser.py"
```

### User Story 4

```bash
Task: "T035 rich-text normalization tests in tests/unit/integrations/test_jira_browser_service.py"
Task: "T037 recommended import tests in tests/unit/integrations/test_jira_browser_service.py"
Task: "T039 issue detail router tests in tests/unit/api/routers/test_jira_browser.py"
```

---

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1 connection verification).
3. Run US1 tests and verify safe error behavior.
4. Stop and validate that browser exposure remains gated and no browser credential path exists.

### Incremental Delivery

1. US1: connection verification and safe failure mapping.
2. US2: allowed project, board, and column browsing.
3. US3: board issue grouping by normalized column.
4. US4: issue detail normalization and recommended import text.
5. Cross-story verification and standard unit wrapper.

### Parallel Team Strategy

- Foundational service/router skeletons should be completed first.
- Service normalization tests and router tests can be written in parallel because they target separate files.
- US4 issue detail work can proceed alongside US2/US3 after foundational setup, but any board-context column mapping should wait for US2 column normalization.

## Notes

- Tests are required and should be written before implementation for each story.
- Runtime implementation tasks intentionally target `moonmind/` and `api_service/`.
- Validation tasks intentionally target `tests/`, `./tools/test_unit.sh`, and the runtime scope validator.
- Do not add Jira mutation behavior, Create-page import UI behavior, or task submission payload changes in this backend phase.
