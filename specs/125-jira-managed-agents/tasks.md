# Tasks: Jira Tools for Managed Agents

**Input**: Design documents from `/specs/125-jira-managed-agents/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Automated validation is required because runtime implementation mode and `FR-020` require auth, retry, router, and security-regression coverage.

**Organization**: Tasks are grouped by user story so each story remains independently implementable and testable.

## Prompt B Scope Controls

- Runtime implementation tasks are explicitly present in `moonmind/`, `api_service/`, and `.env-template`.
- Runtime validation tasks are explicitly present under `tests/`.
- Every `DOC-REQ-*` has at least one implementation task and one validation task in the coverage matrix below.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add Jira tool configuration and package scaffolding.

- [ ] T001 Extend Jira tool configuration, binding fields, and policy defaults in `moonmind/config/settings.py` and `.env-template` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-006, DOC-REQ-008, DOC-REQ-011)
- [ ] T002 [P] Create the Jira integration package scaffold in `moonmind/integrations/jira/__init__.py`, `moonmind/integrations/jira/errors.py`, and `moonmind/integrations/jira/models.py` (DOC-REQ-004, DOC-REQ-007, DOC-REQ-008)
- [ ] T003 [P] Add Jira tool registry export surfaces in `moonmind/mcp/jira_tool_registry.py` and `moonmind/mcp/__init__.py` (DOC-REQ-004, DOC-REQ-007)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement the trusted auth, transport, and shared helper boundaries used by all Jira actions.

**⚠️ CRITICAL**: Complete this phase before user-story implementation.

- [ ] T004 Implement SecretRef-backed Jira auth resolution and base-URL/header selection in `moonmind/integrations/jira/auth.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-006, DOC-REQ-011)
- [ ] T005 [P] Implement ADF conversion helpers in `moonmind/integrations/jira/adf.py` (DOC-REQ-009)
- [ ] T006 Implement bounded Jira HTTP transport, retry handling, and sanitized error mapping in `moonmind/integrations/jira/client.py` and `moonmind/integrations/jira/errors.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-010)
- [ ] T007 Implement high-level policy enforcement and action orchestration in `moonmind/integrations/jira/tool.py` (DOC-REQ-003, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-011)

**Checkpoint**: Trusted Jira auth, transport, and policy helpers are ready for tool exposure.

---

## Phase 3: User Story 1 - Use Jira Actions Without Exposing Credentials (Priority: P1) 🎯 MVP

**Goal**: Expose core Jira issue/search/edit actions through the managed-agent MCP surface without leaking credentials.

**Independent Test**: Discover Jira tools, call create/get/search/edit through the router, and verify successful sanitized dispatch with trusted credential resolution.

### Tests for User Story 1

- [ ] T008 [P] [US1] Add auth-resolution tests for SecretRef and raw-env local-development bindings in `tests/unit/integrations/test_jira_auth.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-011)
- [ ] T009 [P] [US1] Add client tests for base-URL/header construction and sanitized auth failures in `tests/unit/integrations/test_jira_client.py` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-010)
- [ ] T010 [P] [US1] Add tool-service tests for create/get/search/edit flows and no-secret result guarantees in `tests/unit/integrations/test_jira_tool_service.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-007, DOC-REQ-009, DOC-REQ-010)
- [ ] T011 [P] [US1] Add router tests for Jira tool discovery and call dispatch in `tests/unit/api/test_mcp_tools_router.py` (DOC-REQ-004, DOC-REQ-007)

### Implementation for User Story 1

- [ ] T012 [US1] Implement core Jira issue/search/edit actions in `moonmind/integrations/jira/tool.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-007, DOC-REQ-009)
- [ ] T013 [US1] Implement Jira MCP registry dispatch for core actions in `moonmind/mcp/jira_tool_registry.py` (DOC-REQ-004, DOC-REQ-007, DOC-REQ-008)
- [ ] T014 [US1] Wire Jira registry/service into the MCP router in `api_service/api/routers/mcp_tools.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-007)

**Checkpoint**: User Story 1 delivers the secure managed-agent Jira MVP.

---

## Phase 4: User Story 2 - Safely Drive Transitions and Metadata-Dependent Work (Priority: P1)

**Goal**: Provide metadata helper, comment, sub-task, and transition actions with strict validation and ADF conversion.

**Independent Test**: Use helper and transition tools against mocked Jira responses and verify invalid/stale/disallowed transitions fail before unsafe mutation.

### Tests for User Story 2

- [ ] T015 [P] [US2] Add ADF conversion and transition-validation tests in `tests/unit/integrations/test_jira_tool_service.py` (DOC-REQ-007, DOC-REQ-008, DOC-REQ-009)
- [ ] T016 [P] [US2] Add Jira registry discovery/dispatch tests for metadata, comment, and transition actions in `tests/unit/mcp/test_jira_tool_registry.py` (DOC-REQ-004, DOC-REQ-007, DOC-REQ-008)

### Implementation for User Story 2

- [ ] T017 [US2] Implement metadata helper actions and sub-task/comment flows in `moonmind/integrations/jira/tool.py` and `moonmind/integrations/jira/models.py` (DOC-REQ-007, DOC-REQ-009)
- [ ] T018 [US2] Implement explicit transition lookup and transition action behavior in `moonmind/integrations/jira/tool.py` (DOC-REQ-007, DOC-REQ-008, DOC-REQ-009)
- [ ] T019 [US2] Register metadata, comment, sub-task, and transition actions in `moonmind/mcp/jira_tool_registry.py` (DOC-REQ-004, DOC-REQ-007)

**Checkpoint**: User Story 2 delivers metadata-safe Jira mutation behavior.

---

## Phase 5: User Story 3 - Operate Jira Tools with Policy, Retry, and Redaction Guarantees (Priority: P2)

**Goal**: Enforce allowlists, verify connectivity, and guarantee bounded retry/redaction behavior.

**Independent Test**: Exercise project/action allowlists, `Retry-After` retries, verification calls, and token-echoing Jira failures while confirming sanitized structured outcomes.

### Tests for User Story 3

- [ ] T020 [P] [US3] Add settings parsing tests for Jira tool controls in `tests/config/test_atlassian_settings.py` (DOC-REQ-006, DOC-REQ-008, DOC-REQ-011)
- [ ] T021 [P] [US3] Add retry and rate-limit behavior tests in `tests/unit/integrations/test_jira_client.py` (DOC-REQ-006, DOC-REQ-010)
- [ ] T022 [P] [US3] Add allowlist, verify-connection, and redaction regression tests in `tests/unit/integrations/test_jira_tool_service.py` (DOC-REQ-003, DOC-REQ-008, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012)

### Implementation for User Story 3

- [ ] T023 [US3] Implement Jira policy parsing and allowlist enforcement in `moonmind/config/settings.py` and `moonmind/integrations/jira/tool.py` (DOC-REQ-008, DOC-REQ-011)
- [ ] T024 [US3] Implement `jira.verify_connection` and structured verification results in `moonmind/integrations/jira/tool.py` and `moonmind/mcp/jira_tool_registry.py` (DOC-REQ-011)
- [ ] T025 [US3] Harden sanitized Jira error classification and retry-exhaustion behavior in `moonmind/integrations/jira/client.py` and `moonmind/integrations/jira/errors.py` (DOC-REQ-006, DOC-REQ-010, DOC-REQ-012)

**Checkpoint**: User Story 3 delivers operator-safe policy, retry, and verification behavior.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize traceability and run required validation/scope gates.

- [ ] T026 [P] Update `specs/125-jira-managed-agents/contracts/requirements-traceability.md` with final implementation and validation evidence for `DOC-REQ-001` through `DOC-REQ-012` (DOC-REQ-012)
- [ ] T027 [P] Run focused Jira tests and final repository validation via `./tools/test_unit.sh` (DOC-REQ-012)
- [ ] T028 Run runtime scope gates `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` after implementation changes are present (DOC-REQ-012)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**: No dependencies.
- **Phase 2**: Depends on Phase 1 and blocks all story work.
- **Phase 3**: Depends on Phase 2 and delivers the secure Jira MVP.
- **Phase 4**: Depends on Phase 3 because metadata/transition flows build on the trusted action path.
- **Phase 5**: Depends on Phases 3 and 4 because policy, verification, and retry behavior harden the full tool surface.
- **Phase 6**: Depends on the desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Independent after foundational work and is the suggested MVP.
- **US2 (P1)**: Depends on US1’s trusted tool path and adds metadata/transition correctness.
- **US3 (P2)**: Depends on US1/US2 because it hardens the already-exposed Jira actions.

### Parallel Opportunities

- `T002` and `T003` can run in parallel.
- `T005` and `T006` can run in parallel after `T004` establishes binding resolution requirements.
- All test tasks marked `[P]` within a story can run in parallel.

## Task Summary

- Total tasks: **28**
- User story tasks: **US1 = 7**, **US2 = 5**, **US3 = 6**
- Parallelizable tasks (`[P]`): **12**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **All tasks follow `- [ ] T### [P?] [US?] ...` with explicit file paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T004, T012, T014 | T008, T010, T011 |
| DOC-REQ-002 | T001, T004 | T008, T009 |
| DOC-REQ-003 | T001, T004, T007 | T008, T010, T022 |
| DOC-REQ-004 | T002, T003, T012, T013, T014, T019 | T011, T016 |
| DOC-REQ-005 | T006 | T009, T021 |
| DOC-REQ-006 | T001, T004, T006, T025 | T009, T020, T021 |
| DOC-REQ-007 | T002, T007, T012, T017, T018, T019 | T010, T011, T015, T016 |
| DOC-REQ-008 | T001, T002, T007, T013, T018, T023 | T011, T015, T016, T020, T022 |
| DOC-REQ-009 | T005, T012, T017, T018 | T010, T015 |
| DOC-REQ-010 | T006, T025 | T009, T010, T021, T022 |
| DOC-REQ-011 | T001, T004, T007, T023, T024 | T008, T020, T022 |
| DOC-REQ-012 | T025, T026 | T020, T021, T022, T027, T028 |
| RUNTIME-GUARD | T001-T007, T012-T014, T017-T019, T023-T025 | T027, T028 |

Coverage gate rule: every `DOC-REQ-*` must keep at least one implementation task and at least one validation task before implementation begins and before publish.
