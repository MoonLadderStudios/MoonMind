# Tasks: Jira Create-Page Rollout Hardening

**Input**: Design documents from `/specs/171-jira-rollout-hardening/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Required. The feature request explicitly requires test-driven development, production runtime code changes, and validation tests.

**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or depends only on completed earlier phases.
- **[Story]**: User story label for story-phase tasks only.
- Every task includes an exact repository file path or validation command.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish rollout configuration and validation scaffolding used by all stories.

- [X] T001 Confirm Jira Create-page settings names, defaults, and config-template entries in `moonmind/config/settings.py` and `api_service/config.template.toml`
- [X] T002 [P] Confirm Jira browser router registration remains wired through `api_service/main.py`
- [X] T003 [P] Confirm OpenAPI generated types include Jira browser schemas in `frontend/src/generated/openapi.ts`
- [X] T004 [P] Confirm quickstart validation commands are current in `specs/171-jira-rollout-hardening/quickstart.md`
- [X] T005 Run baseline focused validation before implementation using `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/api/routers/test_jira_browser.py tests/unit/integrations/test_jira_browser_service.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Complete shared contracts and model boundaries before story implementation.

**CRITICAL**: No user-story implementation should begin until this phase is complete.

- [X] T006 Add or update runtime-config contract tests for Jira disabled/enabled/default values in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T007 Add or update settings normalization tests for Jira Create-page flags and defaults in `tests/unit/config/test_settings.py`
- [X] T008 Add or update backend browser contract tests for all Jira browser routes in `tests/unit/api/routers/test_jira_browser.py`
- [X] T009 Add or update Jira browser service model/normalization tests in `tests/unit/integrations/test_jira_browser_service.py`
- [X] T010 Implement or update Jira Create-page runtime config source and system blocks in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T011 Implement or update Jira Create-page feature flag/default settings in `moonmind/config/settings.py`
- [X] T012 Implement or update Jira browser response models and service boundaries in `moonmind/integrations/jira/browser.py`
- [X] T013 Implement or update MoonMind-owned Jira browser routes and safe error mapping in `api_service/api/routers/jira_browser.py`
- [X] T014 Verify foundational backend/runtime work using `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py tests/unit/api/routers/test_jira_browser.py tests/unit/integrations/test_jira_browser_service.py`

**Checkpoint**: Runtime config, trusted Jira browser routes, service models, and safe errors are ready for UI story work.

---

## Phase 3: User Story 1 - Safely Browse Jira While Creating a Task (Priority: P1)

**Goal**: Operators can open a gated Jira browser from Create page fields, browse project to board to column to issue preview, and continue manual authoring if Jira is unavailable.

**Independent Test**: Enable Jira in the boot payload, open the browser, navigate project/board/column/story, then simulate disabled and failing Jira states and verify manual editing remains available.

### Tests for User Story 1

- [X] T015 [P] [US1] Add or update frontend test for hidden Jira controls when disabled in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T016 [P] [US1] Add or update frontend tests for opening Jira browser from preset and step targets in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T017 [P] [US1] Add or update frontend tests for project, board, column ordering, column switching, and issue preview loading in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T018 [P] [US1] Add or update frontend tests for Jira load failures and empty states remaining local to the browser in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T019 [P] [US1] Add or update frontend tests for session-only last project/board restoration when `rememberLastBoardInSession` is enabled, disabled, and browser storage is unavailable in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 1

- [X] T020 [US1] Implement or update Jira integration config parsing and endpoint-template validation in `frontend/src/entrypoints/task-create.tsx`
- [X] T021 [US1] Implement or update Jira browser state for project, board, active column, issue list, selected issue, target, loading, and error state in `frontend/src/entrypoints/task-create.tsx`
- [X] T022 [US1] Implement or update sessionStorage-backed last project/board persistence gated by `rememberLastBoardInSession`, with safe no-op behavior when browser storage is unavailable, in `frontend/src/entrypoints/task-create.tsx`
- [X] T023 [US1] Implement or update TanStack Query fetchers for Jira projects, boards, columns, issues, and issue detail in `frontend/src/entrypoints/task-create.tsx`
- [X] T024 [US1] Implement or update one shared Jira browser dialog with project/board selectors, column tabs, issue list, preview panel, and close control in `frontend/src/entrypoints/task-create.tsx`
- [X] T025 [US1] Implement or update local Jira failure and empty-state copy so manual task creation remains available in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 1

- [X] T026 [US1] Verify User Story 1 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Jira browser can be safely opened and navigated without mutating draft fields or blocking manual task creation.

---

## Phase 4: User Story 2 - Import Jira Text Into the Correct Task Field (Priority: P1)

**Goal**: Operators can explicitly import selected Jira text into the preset objective or a selected step using replace or append semantics.

**Independent Test**: Import one Jira story into preset objective and a secondary step, verify only the target changes, and verify task submission payload shape remains unchanged.

### Tests for User Story 2

- [X] T027 [P] [US2] Add or update frontend tests for replace import into preset objective and resolved objective precedence in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T028 [P] [US2] Add or update frontend tests for append import into preset objective with separator preservation in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T029 [P] [US2] Add or update frontend tests for replace import into a selected step without changing other steps in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T030 [P] [US2] Add or update frontend tests for import modes including preset brief, execution brief, description only, and acceptance criteria only in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T031 [P] [US2] Add or update frontend tests proving Jira import does not add Jira provenance or issue metadata to task submission payloads in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 2

- [X] T032 [US2] Implement or update target-aware import-mode selection and preview text derivation in `frontend/src/entrypoints/task-create.tsx`
- [X] T033 [US2] Implement or update explicit replace and append import actions for preset and step targets in `frontend/src/entrypoints/task-create.tsx`
- [X] T034 [US2] Implement or update preset objective import behavior so objective resolution prefers imported preset text in `frontend/src/entrypoints/task-create.tsx`
- [X] T035 [US2] Implement or update step import behavior through the existing step update path so only the selected step changes in `frontend/src/entrypoints/task-create.tsx`
- [X] T036 [US2] Implement or update task submission construction to keep Jira provenance out of the request payload in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 2

- [X] T037 [US2] Verify User Story 2 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Jira imports update exactly one selected field and do not alter the Create-page submission contract.

---

## Phase 5: User Story 3 - Understand Preset Reapply and Import Provenance (Priority: P2)

**Goal**: Operators see preset reapply guidance after Jira changes applied preset inputs, and imported fields show lightweight Jira provenance.

**Independent Test**: Apply a preset, import Jira into the preset objective, verify reapply-needed messaging without step rewrites, and verify provenance appears, restores context, and clears on manual edits/removal.

### Tests for User Story 3

- [X] T038 [P] [US3] Add or update frontend tests for preset reapply-needed messaging after Jira import changes an applied preset in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T039 [P] [US3] Add or update frontend tests proving Jira import into template-derived steps detaches template instruction identity in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T040 [P] [US3] Add or update frontend tests for provenance chips after preset and step imports in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T041 [P] [US3] Add or update frontend tests for reopening Jira from an imported field with prior issue context selected in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T042 [P] [US3] Add or update frontend tests for clearing provenance when imported text is manually edited or a step is removed in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation for User Story 3

- [X] T043 [US3] Implement or update preset reapply-needed state and message when Jira changes applied preset objective text in `frontend/src/entrypoints/task-create.tsx`
- [X] T044 [US3] Implement or update template-derived step customization warning and identity detachment on Jira import in `frontend/src/entrypoints/task-create.tsx`
- [X] T045 [US3] Implement or update local Jira provenance state and field-level provenance chips in `frontend/src/entrypoints/task-create.tsx`
- [X] T046 [US3] Implement or update Jira browser reopen behavior to prefer prior issue, board, column, and import mode from provenance in `frontend/src/entrypoints/task-create.tsx`
- [X] T047 [US3] Implement or update provenance clearing for manual field edits and removed steps in `frontend/src/entrypoints/task-create.tsx`

### Validation for User Story 3

- [X] T048 [US3] Verify User Story 3 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`

**Checkpoint**: Preset reapply and provenance behavior are visible, explicit, and reversible without hidden task draft rewrites.

---

## Phase 6: User Story 4 - Roll Out Jira UI Without Weakening Trusted Boundaries (Priority: P2)

**Goal**: Operators can roll out Jira UI separately from backend Jira tooling, with policy enforcement and secret-safe errors preserved server-side.

**Independent Test**: Enable backend Jira tooling while UI rollout is disabled, verify no UI config appears, then enable UI rollout and verify endpoint templates, policy denial, and safe error behavior.

### Tests for User Story 4

- [X] T049 [P] [US4] Add or update runtime config tests proving Jira UI remains separate from backend Jira tooling in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T050 [P] [US4] Add or update Jira browser service tests for project allowlist denial before provider requests in `tests/unit/integrations/test_jira_browser_service.py`
- [X] T051 [P] [US4] Add or update Jira browser router tests for structured safe errors and secret-like message redaction in `tests/unit/api/routers/test_jira_browser.py`
- [X] T052 [P] [US4] Add or update Jira client tests for Agile REST path resolution and sanitized request failures in `tests/unit/integrations/test_jira_client.py`

### Implementation for User Story 4

- [X] T053 [US4] Implement or update Jira Create-page rollout gate so backend Jira tool enablement does not expose UI config in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T054 [US4] Implement or update project allowlist checks and validation fail-fast behavior in `moonmind/integrations/jira/browser.py`
- [X] T055 [US4] Implement or update router-level safe error shaping and secret-like message sanitization in `api_service/api/routers/jira_browser.py`
- [X] T056 [US4] Implement or update Jira client Agile path routing and redacted logging support in `moonmind/integrations/jira/client.py`
- [X] T057 [US4] Update generated OpenAPI types after route/schema changes using `npm run generate` and verify `frontend/src/generated/openapi.ts`

### Validation for User Story 4

- [X] T058 [US4] Verify User Story 4 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/integrations/test_jira_browser_service.py tests/unit/api/routers/test_jira_browser.py tests/unit/integrations/test_jira_client.py`

**Checkpoint**: Jira UI rollout remains explicit, trusted, policy-enforced, and secret-safe.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final hardening, validation, and documentation alignment across all user stories.

- [X] T059 [P] Review Create-page accessibility labels, modal semantics, button labels, and keyboard-safe close behavior in `frontend/src/entrypoints/task-create.tsx`
- [X] T060 [P] Review Jira browser copy against desired-state wording in `docs/UI/CreatePage.md`
- [X] T061 [P] Review contract drift between planned OpenAPI and runtime models in `specs/171-jira-rollout-hardening/contracts/jira-browser.openapi.yaml` and `moonmind/integrations/jira/browser.py`
- [X] T062 Run full focused Python validation from `specs/171-jira-rollout-hardening/quickstart.md`
- [X] T063 Run targeted Create-page dashboard validation from `specs/171-jira-rollout-hardening/quickstart.md`
- [X] T064 Run frontend typecheck with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- [X] T065 Run frontend lint with `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src`
- [X] T066 Run final repository status check with `git status --short`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1; blocks all user stories.
- **Phase 3 US1**: Depends on Phase 2.
- **Phase 4 US2**: Depends on Phase 2 and can start once Jira browser target/preview scaffolding from US1 is available.
- **Phase 5 US3**: Depends on US2 import semantics.
- **Phase 6 US4**: Depends on Phase 2 and can run in parallel with US1-US3 after shared backend boundaries exist.
- **Phase 7 Polish**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 (P1)**: MVP. Required before user-facing imports can be demonstrated.
- **US2 (P1)**: Depends on US1 browser preview/target scaffolding.
- **US3 (P2)**: Depends on US2 import actions and existing preset/template state.
- **US4 (P2)**: Can proceed in parallel after Phase 2 because it hardens runtime gating and trusted backend boundaries.

### Parallel Opportunities

- T002, T003, and T004 can run in parallel after T001.
- T006 through T009 can run in parallel because they are test additions in different files.
- T015 through T019 can run in parallel for US1 test coverage.
- T027 through T031 can run in parallel for US2 test coverage.
- T038 through T042 can run in parallel for US3 test coverage.
- T049 through T052 can run in parallel for US4 backend/runtime test coverage.
- T059 through T061 can run in parallel during polish.

## Parallel Execution Examples

### US1

```text
Agent A: T015 and T016 in frontend/src/entrypoints/task-create.test.tsx
Agent B: T017 through T019 in frontend/src/entrypoints/task-create.test.tsx
Agent C: T020 through T026 in frontend/src/entrypoints/task-create.tsx after tests are in place
```

### US2

```text
Agent A: T027 and T028 in frontend/src/entrypoints/task-create.test.tsx
Agent B: T029 through T031 in frontend/src/entrypoints/task-create.test.tsx
Agent C: T032 through T036 in frontend/src/entrypoints/task-create.tsx after tests are in place
```

### US3

```text
Agent A: T038 and T039 in frontend/src/entrypoints/task-create.test.tsx
Agent B: T040 through T042 in frontend/src/entrypoints/task-create.test.tsx
Agent C: T043 through T047 in frontend/src/entrypoints/task-create.tsx after tests are in place
```

### US4

```text
Agent A: T049 and T051 in tests/unit/api/routers/
Agent B: T050 and T052 in tests/unit/integrations/
Agent C: T053 through T056 in api_service/ and moonmind/integrations/jira/
```

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1 so the Jira browser can be safely opened and navigated without changing task drafts.
3. Validate US1 independently.

### Incremental Delivery

1. Add US2 import behavior after US1 browser and preview behavior are stable.
2. Add US3 preset reapply/provenance once import semantics are stable.
3. Complete US4 hardening in parallel where possible, then rerun backend and frontend validation.

### Final Validation

1. Run all quickstart automated validation commands.
2. Confirm no Jira provenance enters task submission payloads.
3. Confirm disabled Jira UI leaves existing Create-page behavior unchanged.
4. Confirm working tree status before final handoff.
