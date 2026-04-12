# Tasks: Jira UI Runtime Config

**Input**: Design documents from `/specs/156-jira-ui-runtime-config/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/
**Tests**: Required. The feature request explicitly requires test-driven development and validation tests.
**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches a different file or is not blocked by incomplete work.
- **[Story]**: Maps the task to a user story from `spec.md`.
- Every implementation or validation task that covers source-document requirements includes the relevant `DOC-REQ-*` IDs.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the feature branch context and planned contract before runtime changes.

- [X] T001 Review the runtime-config contract in `specs/156-jira-ui-runtime-config/contracts/runtime-config-jira.schema.json`
- [X] T002 Review the source traceability matrix in `specs/156-jira-ui-runtime-config/contracts/requirements-traceability.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the shared operator settings and defaults that all user stories depend on.

**CRITICAL**: No user story runtime behavior should be implemented before the feature flag and default-setting shape exists.

- [X] T003 [P] Add Create-page Jira rollout setting fields and safe defaults for DOC-REQ-004 and DOC-REQ-005 in `moonmind/config/settings.py`
- [X] T004 [P] Add disabled-by-default Jira Create-page config template entries for DOC-REQ-004 and DOC-REQ-005 in `api_service/config.template.toml`

**Checkpoint**: Runtime settings can represent disabled state, optional defaults, and session-memory preference.

---

## Phase 3: User Story 1 - Hide Jira UI When Disabled (Priority: P1) MVP

**Goal**: Omit Jira browser capability from runtime config unless the Jira Create-page rollout is enabled.

**Independent Test**: Build runtime config with the Jira Create-page rollout disabled and verify no `sources.jira` or `system.jiraIntegration` block exists while existing Create page config remains present.

### Tests for User Story 1

> Write these tests first and confirm they fail before implementation when the runtime behavior is missing.

- [X] T005 [P] [US1] Add disabled-state runtime config tests for DOC-REQ-001 and DOC-REQ-004 in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T006 [P] [US1] Add backend-tool-independence regression tests for DOC-REQ-003 in `tests/unit/api/routers/test_task_dashboard_view_model.py`

### Implementation for User Story 1

- [X] T007 [US1] Implement disabled-state omission of `sources.jira` and `system.jiraIntegration` for DOC-REQ-001 and DOC-REQ-004 in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T008 [US1] Ensure Jira UI rollout logic does not read backend Jira tool enablement for DOC-REQ-003 in `api_service/api/routers/task_dashboard_view_model.py`

**Checkpoint**: User Story 1 is independently complete when disabled runtime config omits Jira UI blocks and preserves existing Create page config.

---

## Phase 4: User Story 2 - Expose Jira Browser Contract When Enabled (Priority: P1)

**Goal**: Publish MoonMind-owned Jira browser endpoint templates and the enabled integration gate when rollout is enabled.

**Independent Test**: Build runtime config with the Jira Create-page rollout enabled and verify the six source templates plus `system.jiraIntegration.enabled` match the contract.

### Tests for User Story 2

> Write these tests first and confirm they fail before implementation when the enabled contract is missing.

- [X] T009 [P] [US2] Add enabled endpoint-template tests for DOC-REQ-002, DOC-REQ-005, and DOC-REQ-006 in `tests/unit/api/routers/test_task_dashboard_view_model.py`

### Implementation for User Story 2

- [X] T010 [US2] Implement the Jira source and integration system blocks for DOC-REQ-002, DOC-REQ-005, and DOC-REQ-006 in `api_service/api/routers/task_dashboard_view_model.py`

**Checkpoint**: User Story 2 is independently complete when enabled runtime config matches `contracts/runtime-config-jira.schema.json`.

---

## Phase 5: User Story 3 - Surface Operator Defaults (Priority: P2)

**Goal**: Surface configured default Jira project, board, and session-memory values under the enabled Jira integration block.

**Independent Test**: Build runtime config with configured defaults and verify those defaults appear only when the Jira Create-page rollout is enabled.

### Tests for User Story 3

> Write these tests first and confirm they fail before implementation when defaults are not surfaced or normalized.

- [X] T011 [P] [US3] Add runtime config default-value tests for DOC-REQ-005 in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T012 [P] [US3] Add settings normalization tests for DOC-REQ-005 in `tests/config/test_atlassian_settings.py`

### Implementation for User Story 3

- [X] T013 [US3] Surface configured Jira default project, board, and session-memory values for DOC-REQ-005 in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T014 [US3] Normalize Jira Create-page default project and board settings for DOC-REQ-005 in `moonmind/config/settings.py`

**Checkpoint**: User Story 3 is independently complete when configured defaults are reflected in enabled runtime config and omitted when disabled.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify the complete runtime contract and preserve existing behavior.

- [X] T015 [P] Run focused validation for DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, and DOC-REQ-006 with `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py tests/config/test_atlassian_settings.py`
- [X] T016 Run final unit validation for runtime scope with `./tools/test_unit.sh`
- [X] T017 [P] Confirm no Jira credentials, raw Jira domains, or browser-side Jira auth hints are exposed in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T018 [P] Confirm implementation remains aligned with `specs/156-jira-ui-runtime-config/contracts/requirements-traceability.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational; delivers the MVP disabled-state safety boundary.
- **User Story 2 (Phase 4)**: Depends on Foundational; can be implemented after or alongside US1 but must not weaken US1 disabled behavior.
- **User Story 3 (Phase 5)**: Depends on Foundational and the enabled system block from US2.
- **Polish (Phase 6)**: Depends on the desired story set being complete.

### User Story Dependencies

- **US1 Hide Jira UI When Disabled**: No dependency on other stories after Foundational.
- **US2 Expose Jira Browser Contract When Enabled**: No dependency on US1 implementation, but final verification must cover both enabled and disabled states together.
- **US3 Surface Operator Defaults**: Depends on the Jira integration system block from US2.

### Within Each User Story

- Tests must be written before implementation.
- Settings shape must exist before runtime config consumes defaults.
- Runtime config implementation must precede final validation commands.

---

## Parallel Opportunities

- T003 and T004 can run in parallel after setup.
- T005 and T006 can run in parallel because they add distinct disabled-state assertions.
- T011 and T012 can run in parallel because they cover runtime output and settings normalization separately.
- T015, T017, and T018 can run in parallel after implementation is complete.

## Parallel Example: User Story 1

```text
Task: "T005 [US1] Add disabled-state runtime config tests in tests/unit/api/routers/test_task_dashboard_view_model.py"
Task: "T006 [US1] Add backend-tool-independence regression tests in tests/unit/api/routers/test_task_dashboard_view_model.py"
```

## Parallel Example: User Story 3

```text
Task: "T011 [US3] Add runtime config default-value tests in tests/unit/api/routers/test_task_dashboard_view_model.py"
Task: "T012 [US3] Add settings normalization tests in tests/config/test_atlassian_settings.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete US1 tests and implementation.
3. Validate that Jira UI config is absent when disabled and existing Create page config remains available.

### Incremental Delivery

1. Deliver US1 to protect disabled behavior.
2. Deliver US2 to expose the enabled browser contract.
3. Deliver US3 to surface operator defaults.
4. Run focused validation and final unit validation.

### Traceability Coverage

- **DOC-REQ-001**: Implementation T007; validation T005 and T015.
- **DOC-REQ-002**: Implementation T010; validation T009 and T015.
- **DOC-REQ-003**: Implementation T008; validation T006 and T015.
- **DOC-REQ-004**: Implementation T003 and T007; validation T005 and T015.
- **DOC-REQ-005**: Implementation T003, T010, T013, and T014; validation T009, T011, T012, and T015.
- **DOC-REQ-006**: Implementation T010; validation T009 and T015.

## Notes

- Phase 1 intentionally excludes Jira browser API endpoints and frontend Jira browsing UI.
- Keep Jira UI rollout separate from trusted backend Jira tool enablement.
- Do not introduce raw Jira credentials, Jira domains, or browser-side Jira auth paths.
- Commit after each completed story or tightly related task group.
