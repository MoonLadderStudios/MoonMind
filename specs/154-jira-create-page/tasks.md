# Tasks: Jira Create Page Integration

**Input**: Design documents from `/specs/154-jira-create-page/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
**Tests**: Required by FR-035 and DOC-REQ-014. Write failing tests before implementation in each story phase.
**Organization**: Tasks are grouped by user story so each increment can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or only adds tests for a bounded surface
- **[Story]**: Maps to user stories from `spec.md`
- Every task includes exact file paths and carries relevant `DOC-REQ-*` IDs where applicable

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the runtime integration surfaces and shared contract locations used by later stories.

- [ ] T001 [P] Review existing Jira auth/client/tool policy helpers for reusable browser boundaries in `moonmind/integrations/jira/auth.py`, `moonmind/integrations/jira/client.py`, and `moonmind/integrations/jira/tool.py`
- [ ] T002 [P] Review Create page preset, step update, objective, and submission paths before editing in `frontend/src/entrypoints/task-create.tsx`
- [ ] T003 [P] Review existing Create page and runtime config test helpers in `frontend/src/entrypoints/task-create.test.tsx`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/unit/config/test_settings.py`
- [ ] T004 Create empty runtime implementation modules for the browser path in `moonmind/integrations/jira/browser.py` and `api_service/api/routers/jira_browser.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define shared browser read models, safe errors, and routing seams that all user stories depend on.

**CRITICAL**: No user story implementation should begin until these shared contracts exist.

- [ ] T005 [P] Add Pydantic browser read models and validators for projects, boards, columns, issue summaries, issue details, import recommendations, and list wrappers in `moonmind/integrations/jira/browser.py` (DOC-REQ-007, DOC-REQ-008)
- [ ] T006 [P] Add frontend Jira runtime/config and browser-state TypeScript types in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-006, DOC-REQ-009, DOC-REQ-011)
- [ ] T007 Add safe Jira browser error-to-HTTP mapping helper in `api_service/api/routers/jira_browser.py` using `JiraToolError` codes without exposing credential material (DOC-REQ-002, DOC-REQ-006, DOC-REQ-012)
- [ ] T008 Register the Jira browser router in the API application routing setup in `api_service/main.py` while preserving auth requirements (DOC-REQ-002, DOC-REQ-006)
- [ ] T009 [P] Add shared frontend Jira fetch helpers that consume `sources.jira` templates only when `system.jiraIntegration.enabled` is true in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-002, DOC-REQ-006)

**Checkpoint**: Shared browser models, router seam, and frontend types exist; user story work can begin.

---

## Phase 3: User Story 1 - Discover Jira Capability Safely (Priority: P1)

**Goal**: Operators can expose or hide the Create-page Jira browser independently from trusted Jira tool enablement.

**Independent Test**: Generate Create page runtime config with Jira UI disabled and enabled; verify Jira browser config is absent when disabled and complete when enabled.

### Tests for User Story 1

- [ ] T010 [P] [US1] Add runtime-config tests for disabled omission, enabled Jira source templates, configured defaults, and Jira tool enablement separation in `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-006, DOC-REQ-014)
- [ ] T011 [P] [US1] Add feature-flag settings tests for Create-page Jira enabled/default project/default board/session memory env aliases in `tests/unit/config/test_settings.py` (DOC-REQ-006, DOC-REQ-014)
- [ ] T012 [P] [US1] Add Create-page tests that Jira entry points are hidden when `system.jiraIntegration` is absent or disabled in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-014)

### Implementation for User Story 1

- [ ] T013 [US1] Implement or refine Create-page Jira feature flag settings and normalization in `moonmind/config/settings.py` (DOC-REQ-006)
- [ ] T014 [US1] Implement or refine conditional Jira runtime config publication in `api_service/api/routers/task_dashboard_view_model.py` and operator examples in `api_service/config.template.toml` (DOC-REQ-006)
- [ ] T015 [US1] Add gated Jira entry buttons near preset initial instructions and step instructions in `frontend/src/entrypoints/task-create.tsx` without rendering them when disabled (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-006)

**Checkpoint**: Jira UI discoverability is fully gated and independently testable without requiring browser endpoint implementation.

---

## Phase 4: User Story 2 - Browse Jira Stories From Task Creation (Priority: P1)

**Goal**: Task authors can open one shared Jira browser, navigate project -> board -> column -> issue detail, and preview normalized issue text without mutating the draft.

**Independent Test**: With Jira UI enabled and mocked browser responses, open the browser from preset and step targets, navigate to issue detail, and verify no draft field changes until import is confirmed.

### Tests for User Story 2

- [ ] T016 [P] [US2] Add browser service tests for connection verification, allowed project listing, project board listing, and policy-denied project access in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-007, DOC-REQ-014)
- [ ] T017 [P] [US2] Add browser service tests for board column normalization, board order, empty columns, issue grouping, and unmapped statuses in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-007, DOC-REQ-014)
- [ ] T018 [P] [US2] Add browser service tests for issue detail text normalization and target-specific recommended imports in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-008, DOC-REQ-014)
- [ ] T019 [P] [US2] Add Jira browser router tests for `/api/jira/connections/verify`, `/api/jira/projects`, `/api/jira/projects/{projectKey}/boards`, `/api/jira/boards/{boardId}/columns`, `/api/jira/boards/{boardId}/issues`, and `/api/jira/issues/{issueKey}` in `tests/unit/api/routers/test_jira_browser.py` (DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-014)
- [ ] T020 [P] [US2] Add Create-page browser tests for opening from preset target, opening from step target, column switching, issue selection, issue preview, and no draft mutation on selection in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-014)

### Implementation for User Story 2

- [ ] T021 [US2] Implement `JiraBrowserService.verify_connection()` and `JiraBrowserService.list_projects()` in `moonmind/integrations/jira/browser.py` using existing auth, client, and policy boundaries (DOC-REQ-002, DOC-REQ-006, DOC-REQ-007)
- [ ] T022 [US2] Implement board listing, column normalization, status-to-column mapping, and issue grouping in `moonmind/integrations/jira/browser.py` (DOC-REQ-007)
- [ ] T023 [US2] Implement issue detail normalization, rich-text extraction, acceptance criteria extraction, and recommended import generation in `moonmind/integrations/jira/browser.py` (DOC-REQ-008)
- [ ] T024 [US2] Implement Jira browser REST endpoints and auth dependency wiring in `api_service/api/routers/jira_browser.py` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-014)
- [ ] T025 [US2] Implement shared Jira browser open/close state, target preselection, project/board/column/issue loading, and issue preview rendering in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-014)
- [ ] T026 [US2] Add explicit empty project, board, column, issue, and issue-detail states inside the Jira browser in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-007, DOC-REQ-012)

**Checkpoint**: Jira browsing and preview work independently; selecting an issue never changes task draft text.

---

## Phase 5: User Story 3 - Import Jira Text Into the Right Authoring Target (Priority: P1)

**Goal**: Task authors can import normalized Jira text into preset initial instructions or one selected step using replace or append and target-aware import modes.

**Independent Test**: Import a mocked Jira issue into preset and step targets with replace and append; verify only the selected target changes and objective/template semantics remain correct.

### Tests for User Story 3

- [ ] T027 [P] [US3] Add Create-page tests for replace and append imports into preset initial instructions, including separator behavior and objective precedence in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-010, DOC-REQ-014)
- [ ] T028 [P] [US3] Add Create-page tests for replace and append imports into primary and secondary step instructions in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-001, DOC-REQ-010, DOC-REQ-014)
- [ ] T029 [P] [US3] Add Create-page tests for all import modes: preset brief, execution brief, description only, and acceptance criteria only, including an SC-003 responsiveness assertion that import controls are usable immediately after issue detail load, in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-010, DOC-REQ-014)
- [ ] T030 [P] [US3] Add Create-page tests for template-bound step warning and template instruction identity detachment on Jira import in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-004, DOC-REQ-014)
- [ ] T031 [P] [US3] Add Create-page tests for preset reapply-needed messaging after importing Jira into preset instructions after preset apply in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-005, DOC-REQ-014)
- [ ] T032 [P] [US3] Add Create-page tests proving Jira issue selection alone does not import and that target switching preserves selected issue detail in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-009, DOC-REQ-014)

### Implementation for User Story 3

- [ ] T033 [US3] Implement import mode selection, live import preview, and replace/append action state in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-009, DOC-REQ-010)
- [ ] T034 [US3] Implement preset-target import by writing through `setTemplateFeatureRequest()` and preserving objective precedence in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-010, DOC-REQ-014)
- [ ] T035 [US3] Implement step-target import by writing through `updateStep()` so template-bound identity detaches when instructions diverge in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-010, DOC-REQ-014)
- [ ] T036 [US3] Implement preset reapply-needed message state without rewriting already-expanded preset steps in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-005)
- [ ] T037 [US3] Implement local Jira import provenance state and `Jira: {issueKey}` field chips in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-002, DOC-REQ-011)
- [ ] T038 [US3] Implement optional session-only last project/board memory helpers in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-011)

**Checkpoint**: Jira import writes only selected existing Create-page fields, with no submission contract change.

---

## Phase 6: User Story 4 - Preserve Preset and Failure Safety (Priority: P2)

**Goal**: Jira failures, template-bound edits, preset reapply states, and accessibility concerns are safe and understandable without blocking manual task creation.

**Independent Test**: Simulate Jira failures and accessibility paths; verify manual task creation remains available and controls expose clear target/focus behavior.

### Tests for User Story 4

- [ ] T039 [P] [US4] Add router tests for safe Jira error normalization, unavailable Jira, policy denial, not found, and redaction-safe responses in `tests/unit/api/routers/test_jira_browser.py` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-012, DOC-REQ-014)
- [ ] T040 [P] [US4] Add service redaction regression tests that simulated Jira errors do not expose API tokens, authorization headers, or basic auth material in `tests/unit/integrations/test_jira_browser_service.py` (DOC-REQ-002, DOC-REQ-012, DOC-REQ-014)
- [ ] T041 [P] [US4] Add Create-page tests for Jira fetch failure isolation, browser close behavior, and manual Create submission after Jira failure in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-002, DOC-REQ-012, DOC-REQ-014)
- [ ] T042 [P] [US4] Add Create-page accessibility tests for labeled browser controls, keyboard-reachable open/close/target/import actions, active column state, target context, and post-import focus or success notice in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-013, DOC-REQ-014)
- [ ] T043 [P] [US4] Add submission regression tests proving Jira provenance is not required in task payloads and existing artifact fallback still works in `frontend/src/entrypoints/task-create.test.tsx` (DOC-REQ-002, DOC-REQ-011, DOC-REQ-012, DOC-REQ-014)

### Implementation for User Story 4

- [ ] T044 [US4] Implement safe local Jira browser error rendering and close-without-draft-loss behavior in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-012)
- [ ] T045 [US4] Implement Jira browser loading guards so only in-flight import actions are blocked and the main Create action remains available on browser failures in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-012)
- [ ] T046 [US4] Implement accessibility labels, active column semantics, target context text, and predictable post-import focus or success notice in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-013)
- [ ] T047 [US4] Ensure Jira browser router errors use structured safe details and never include raw Jira response bodies or credential values in `api_service/api/routers/jira_browser.py` (DOC-REQ-002, DOC-REQ-012)
- [ ] T048 [US4] Ensure Create-page submit payload construction remains unchanged and excludes required Jira provenance fields in `frontend/src/entrypoints/task-create.tsx` (DOC-REQ-002, DOC-REQ-011, DOC-REQ-012)

**Checkpoint**: Jira remains additive and non-blocking; accessibility and submission invariants are covered.

---

## Phase 7: Polish & Cross-Cutting Verification

**Purpose**: Finalize coverage, traceability, and whole-feature verification.

- [ ] T049 [P] Update `specs/154-jira-create-page/contracts/requirements-traceability.md` with final implementation file paths and test file names if implementation deviates from the plan (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014)
- [ ] T050 [P] Run focused backend verification `pytest tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py tests/unit/integrations/test_jira_browser_service.py tests/unit/api/routers/test_jira_browser.py -q` and record results in `specs/154-jira-create-page/quickstart.md` if any command needs adjustment (DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-012, DOC-REQ-014)
- [ ] T051 [P] Run focused frontend verification `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`, confirming the SC-003 import responsiveness coverage executes, and record results in `specs/154-jira-create-page/quickstart.md` if any command needs adjustment (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014)
- [ ] T052 Run standard unit verification `./tools/test_unit.sh` and document any unrelated pre-existing failures in the final implementation notes (DOC-REQ-014)
- [ ] T053 Run runtime scope validation `SPECIFY_FEATURE=154-jira-create-page ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` in the repository root and remediate any failures in `specs/154-jira-create-page/tasks.md` (DOC-REQ-014)
- [ ] T054 Run runtime diff validation `SPECIFY_FEATURE=154-jira-create-page ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` in the repository root after implementation and remediate any production/runtime coverage failures (DOC-REQ-014)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 US1**: Depends on Phase 2. This is the MVP rollout contract and can ship before browser data endpoints are complete.
- **Phase 4 US2**: Depends on Phase 2 and benefits from US1 config gating. It can be implemented in parallel with US3 only after backend browser contracts are stable.
- **Phase 5 US3**: Depends on US2 issue detail preview and browser state.
- **Phase 6 US4**: Depends on US2 and US3 behavior existing, but tests can be drafted earlier.
- **Phase 7 Polish**: Depends on selected user story phases being complete.

### User Story Dependencies

- **US1 Discover Jira Capability Safely**: Independent MVP after foundational setup.
- **US2 Browse Jira Stories From Task Creation**: Requires foundational service/router/frontend browser types and runtime config availability.
- **US3 Import Jira Text Into the Right Authoring Target**: Requires US2 browser issue detail preview.
- **US4 Preserve Preset and Failure Safety**: Cross-cuts US2 and US3 behavior but remains independently verifiable through failure/accessibility tests.

### DOC-REQ Coverage Gate

- **DOC-REQ-001**: Implementation T015, T034, T035; validation T020, T027, T028, T051.
- **DOC-REQ-002**: Implementation T007, T008, T037, T047, T048; validation T012, T039, T040, T041, T043, T051.
- **DOC-REQ-003**: Implementation T015, T025; validation T020, T051.
- **DOC-REQ-004**: Implementation T035; validation T030, T051.
- **DOC-REQ-005**: Implementation T034, T036; validation T027, T031, T051.
- **DOC-REQ-006**: Implementation T007, T008, T013, T014, T021, T024; validation T010, T011, T012, T019, T039, T050.
- **DOC-REQ-007**: Implementation T005, T021, T022, T024, T026; validation T016, T017, T019, T020, T050.
- **DOC-REQ-008**: Implementation T005, T023, T024, T025; validation T018, T019, T020, T050.
- **DOC-REQ-009**: Implementation T006, T025, T033; validation T020, T032, T051.
- **DOC-REQ-010**: Implementation T033, T034, T035; validation T027, T028, T029, T051.
- **DOC-REQ-011**: Implementation T037, T038, T048; validation T043, T051.
- **DOC-REQ-012**: Implementation T007, T026, T044, T045, T047, T048; validation T039, T040, T041, T043, T050, T051.
- **DOC-REQ-013**: Implementation T046; validation T042, T051.
- **DOC-REQ-014**: Implementation T024, T025, T034, T035, T049; validation T010, T011, T012, T016, T017, T018, T019, T020, T027, T028, T029, T030, T031, T032, T039, T040, T041, T042, T043, T050, T051, T052, T053, T054.

---

## Parallel Execution Examples

### User Story 1

```bash
Task: "T010 runtime-config tests in tests/unit/api/routers/test_task_dashboard_view_model.py"
Task: "T011 feature-flag settings tests in tests/unit/config/test_settings.py"
Task: "T012 hidden-entry frontend tests in frontend/src/entrypoints/task-create.test.tsx"
```

### User Story 2

```bash
Task: "T016 connection/project service tests in tests/unit/integrations/test_jira_browser_service.py"
Task: "T019 router tests in tests/unit/api/routers/test_jira_browser.py"
Task: "T020 browser navigation tests in frontend/src/entrypoints/task-create.test.tsx"
```

### User Story 3

```bash
Task: "T027 preset import tests in frontend/src/entrypoints/task-create.test.tsx"
Task: "T028 step import tests in frontend/src/entrypoints/task-create.test.tsx"
Task: "T029 import mode tests in frontend/src/entrypoints/task-create.test.tsx"
```

### User Story 4

```bash
Task: "T039 router error tests in tests/unit/api/routers/test_jira_browser.py"
Task: "T041 frontend failure isolation tests in frontend/src/entrypoints/task-create.test.tsx"
Task: "T042 accessibility tests in frontend/src/entrypoints/task-create.test.tsx"
```

---

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1) so Jira UI is safely gated by runtime config.
3. Stop and validate runtime config plus hidden-entry tests.

### Incremental Delivery

1. Add US2 browser data endpoints and preview UI.
2. Add US3 import write semantics.
3. Add US4 failure/accessibility hardening.
4. Run Phase 7 verification after each user-visible slice.

### TDD Discipline

1. Write story-specific tests first.
2. Confirm the focused tests fail for missing behavior.
3. Implement the smallest runtime change that passes the focused tests.
4. Run the story checkpoint before moving to the next phase.
