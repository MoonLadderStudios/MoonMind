# Tasks: Finalize OAuth from Provider Terminal

**Input**: Design documents from `/work/agent_jobs/mm:d8243ed5-4171-40ec-9a44-b9251ba3d631/repo/specs/306-finalize-oauth-terminal/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/oauth-terminal-finalization.md](./contracts/oauth-terminal-finalization.md), [quickstart.md](./quickstart.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around one independently testable story: provider terminal OAuth finalization.

**Source Traceability**: Tasks reference `FR-*`, `SCN-*`, `SC-*`, `DESIGN-REQ-*`, and edge cases from [spec.md](./spec.md). `SCN-001` through `SCN-009` map to acceptance scenarios 1 through 9 in the spec.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Focused frontend tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/oauth-terminal.test.tsx frontend/src/components/settings/ProviderProfilesManager.test.tsx`
- Focused API tests: `./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py`
- Workflow boundary tests: `./tools/test_unit.sh tests/integration/temporal/test_oauth_session.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify` (`/moonspec-verify` managed workflow equivalent)

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when files do not overlap and no dependency exists.
- Every task includes a concrete file path.
- This task list covers exactly one story.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing tool and file structure before writing failing tests.

- [ ] T001 Verify the active feature artifacts and source traceability in specs/306-finalize-oauth-terminal/spec.md, specs/306-finalize-oauth-terminal/plan.md, specs/306-finalize-oauth-terminal/research.md, specs/306-finalize-oauth-terminal/data-model.md, specs/306-finalize-oauth-terminal/contracts/oauth-terminal-finalization.md, and specs/306-finalize-oauth-terminal/quickstart.md
- [ ] T002 [P] Confirm frontend focused test tooling supports oauth-terminal and Settings targets by dry-listing commands documented in specs/306-finalize-oauth-terminal/quickstart.md
- [ ] T003 [P] Confirm Python OAuth API and Temporal workflow test targets from specs/306-finalize-oauth-terminal/quickstart.md are addressable through ./tools/test_unit.sh

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare shared fixtures and helpers needed by the red-first tests.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T004 Add reusable OAuth terminal test response builders for session projection, profile summary, attach metadata, and safe failure payloads in frontend/src/entrypoints/oauth-terminal.test.tsx covering FR-001, FR-002, FR-006, FR-012
- [ ] T005 [P] Add reusable OAuth API test helpers for creating sessions, existing Provider Profiles, fake verifier outcomes, and mutation assertions in tests/unit/api_service/api/routers/test_oauth_sessions.py covering FR-005, FR-008, FR-009, FR-010, FR-012
- [ ] T006 [P] Add or identify Settings-side test hooks for provider-profile query invalidation and terminal-originated refresh notification in frontend/src/components/settings/ProviderProfilesManager.test.tsx covering FR-007 and SC-005
- [ ] T007 [P] Identify the existing OAuth workflow boundary tests that must remain compatible with `api_finalize_succeeded` in tests/integration/temporal/test_oauth_session.py covering DESIGN-REQ-002 and DESIGN-REQ-005

**Checkpoint**: Foundation ready. Story test and implementation work can now begin.

---

## Phase 3: Story - Provider Terminal OAuth Finalization

**Summary**: As an operator completing provider OAuth in the terminal page, I want to finalize the Provider Profile from that same page so that credential enrollment can finish without returning to Settings.

**Independent Test**: Start from a prepared OAuth session that represents a completed provider login, render the provider terminal completion surface and Settings surface against the same session, trigger finalization from the terminal page, and verify state transitions, duplicate-request safety, profile-registration output, query refresh behavior, and safe failure handling without exposing credential material or mutable session identity fields.

**Traceability**: FR-001 through FR-012, SCN-001 through SCN-009, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-008.

**Unit Test Plan**:

- Frontend unit tests validate session projection, action gating, finalize/cancel/reconnect callers, duplicate-click behavior, safe summary rendering, secret-safe display, and Settings refresh notification.
- API unit tests validate state transitions, profile summary response, idempotency, safe invalid-session failures, immutable session-owned identity fields, and secret-safe responses.

**Integration Test Plan**:

- Contract-style frontend/API boundary tests validate terminal and Settings use the same finalization operation.
- Workflow boundary tests preserve `api_finalize_succeeded` compatibility and verify API finalization does not duplicate workflow-side verify/register work.
- Hermetic integration validation runs after the story passes focused unit and workflow checks.

### Unit Tests (write first) ⚠️

> Write these tests FIRST. Run them and confirm they FAIL for the expected reason before implementation.

- [ ] T008 [P] Add failing frontend unit tests for safe OAuth session projection in frontend/src/entrypoints/oauth-terminal.test.tsx covering FR-001, SCN-001, SC-001, DESIGN-REQ-001, DESIGN-REQ-006
- [ ] T009 [P] Add failing frontend unit tests for terminal attach gating by status and terminal refs in frontend/src/entrypoints/oauth-terminal.test.tsx covering FR-002, SCN-002, DESIGN-REQ-001, DESIGN-REQ-008
- [ ] T010 [P] Add failing frontend unit tests for `Finalize Provider Profile` visibility, eligible-state gating, and duplicate-click suppression in frontend/src/entrypoints/oauth-terminal.test.tsx covering FR-003, FR-008, SCN-003, SCN-008, SC-001, SC-003
- [ ] T011 [P] Add failing frontend unit tests proving terminal finalization calls `POST /api/v1/oauth-sessions/{session_id}/finalize` without mutable identity request fields in frontend/src/entrypoints/oauth-terminal.test.tsx covering FR-004, FR-010, SCN-004, DESIGN-REQ-002, DESIGN-REQ-003
- [ ] T012 [P] Add failing frontend unit tests for `verifying`, `registering_profile`, `succeeded`, and `failed` rendering plus safe registered-profile summary in frontend/src/entrypoints/oauth-terminal.test.tsx covering FR-005, FR-006, SCN-005, SCN-006, DESIGN-REQ-004
- [ ] T013 [P] Add failing frontend unit tests for terminal Cancel, Retry, and Reconnect action visibility and callers in frontend/src/entrypoints/oauth-terminal.test.tsx covering FR-011 and terminal recovery edge cases
- [ ] T014 [P] Add failing frontend unit tests that secret-like failure/profile text is not exposed in terminal-visible output in frontend/src/entrypoints/oauth-terminal.test.tsx covering FR-012, SC-006, DESIGN-REQ-005, DESIGN-REQ-006
- [ ] T015 [P] Add failing Settings-side unit tests for terminal-originated success refresh or notification of Provider Profile query data in frontend/src/components/settings/ProviderProfilesManager.test.tsx covering FR-007, SCN-007, SC-005, DESIGN-REQ-007
- [ ] T016 [P] Add failing API unit tests for finalization state sequence `awaiting_user -> verifying -> registering_profile -> succeeded` in tests/unit/api_service/api/routers/test_oauth_sessions.py covering FR-005, SCN-004, SCN-005, SC-002, DESIGN-REQ-002
- [ ] T017 [P] Add failing API unit tests for safe `OAuthSessionResponse` or immediate follow-up projection with `profile_summary` after finalization in tests/unit/api_service/api/routers/test_oauth_sessions.py covering FR-001, FR-006, SCN-006, DESIGN-REQ-004
- [ ] T018 [P] Add failing API unit tests for idempotent duplicate finalize requests in `verifying`, `registering_profile`, and `succeeded` states in tests/unit/api_service/api/routers/test_oauth_sessions.py covering FR-008, SCN-008, SC-003, DESIGN-REQ-003
- [ ] T019 [P] Add failing API unit tests for unauthorized, cancelled, expired, failed, and superseded-session finalization with no Provider Profile mutation in tests/unit/api_service/api/routers/test_oauth_sessions.py covering FR-009, SCN-009, SC-004, DESIGN-REQ-003, DESIGN-REQ-006
- [ ] T020 [P] Add failing API unit tests proving request body or query noise cannot override session-owned `profile_id`, `runtime_id`, `volume_ref`, `volume_mount_path`, provider identity, account label, or slot policy in tests/unit/api_service/api/routers/test_oauth_sessions.py covering FR-010, DESIGN-REQ-003, DESIGN-REQ-004
- [ ] T021 [P] Add failing API unit tests for secret-safe finalize failure and success response fields in tests/unit/api_service/api/routers/test_oauth_sessions.py covering FR-012, SC-006, DESIGN-REQ-005, DESIGN-REQ-006
- [ ] T022 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/oauth-terminal.test.tsx frontend/src/components/settings/ProviderProfilesManager.test.tsx` and confirm T008-T015 fail for missing terminal finalization behavior in frontend/src/entrypoints/oauth-terminal.test.tsx and frontend/src/components/settings/ProviderProfilesManager.test.tsx
- [ ] T023 Run `./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py` and confirm T016-T021 fail for missing API state/idempotency/response behavior in tests/unit/api_service/api/routers/test_oauth_sessions.py

### Integration Tests (write first) ⚠️

- [ ] T024 [P] Add failing workflow boundary test preserving `api_finalize_succeeded` compatibility and no duplicate workflow-side verify/register work in tests/integration/temporal/test_oauth_session.py covering FR-005, SC-002, DESIGN-REQ-002, DESIGN-REQ-005
- [ ] T025 [P] Add failing terminal-to-API contract coverage for the complete terminal finalization flow in frontend/src/entrypoints/oauth-terminal.test.tsx covering SCN-001 through SCN-006 and contracts/oauth-terminal-finalization.md
- [ ] T026 [P] Add failing Settings refresh integration-style coverage for terminal-originated finalization success in frontend/src/components/settings/ProviderProfilesManager.test.tsx covering SCN-007, SC-005, DESIGN-REQ-007
- [ ] T027 Run `./tools/test_unit.sh tests/integration/temporal/test_oauth_session.py` and confirm T024 fails for the intended boundary gap in tests/integration/temporal/test_oauth_session.py

### Red-First Confirmation ⚠️

- [ ] T028 Confirm all new red-first test failures from T022, T023, and T027 are due to missing planned OAuth terminal finalization behavior, not fixture errors, in specs/306-finalize-oauth-terminal/quickstart.md
- [ ] T029 Record the failing test names and expected failure reasons for the implementation handoff in specs/306-finalize-oauth-terminal/tasks.md

### Conditional Fallback Tasks for Implemented-Unverified Rows

- [ ] T030 If T009 exposes a regression, adjust terminal attach readiness logic without broad bridge changes in frontend/src/entrypoints/oauth-terminal.tsx covering FR-002 and DESIGN-REQ-008
- [ ] T031 If T020 exposes mutable identity input, harden finalize request handling in api_service/api/routers/oauth_sessions.py and terminal request construction in frontend/src/entrypoints/oauth-terminal.tsx covering FR-010 and DESIGN-REQ-003
- [ ] T032 If T021 exposes unsafe response data, harden finalization response sanitization in api_service/api/routers/oauth_sessions.py and api_service/api/schemas_oauth_sessions.py covering FR-012 and DESIGN-REQ-005

### Implementation

- [ ] T033 Extend OAuth session response/finalization schemas only as needed for safe terminal projection and profile summary in api_service/api/schemas_oauth_sessions.py covering FR-001, FR-006, FR-012
- [ ] T034 Implement API finalization state sequencing, idempotent duplicate handling, safe invalid-session failures, and session-owned identity enforcement in api_service/api/routers/oauth_sessions.py covering FR-005, FR-008, FR-009, FR-010, SCN-005, SCN-008, SCN-009
- [ ] T035 Preserve API-to-workflow completion/failure signaling compatibility while updating finalization semantics in api_service/services/oauth_session_service.py and api_service/api/routers/oauth_sessions.py covering SC-002 and DESIGN-REQ-002
- [ ] T036 Implement terminal-page session polling/projection state, safe summary rendering, allowed action derivation, and secret-safe display in frontend/src/entrypoints/oauth-terminal.tsx covering FR-001, FR-003, FR-006, FR-011, FR-012
- [ ] T037 Implement terminal-page finalization, cancel, retry, reconnect callers and duplicate-click convergence in frontend/src/entrypoints/oauth-terminal.tsx covering FR-004, FR-008, FR-011, SCN-004, SCN-008
- [ ] T038 Implement terminal-originated Provider Profile refresh notification or equivalent Settings-side invalidation path in frontend/src/entrypoints/oauth-terminal.tsx and frontend/src/components/settings/ProviderProfilesManager.tsx covering FR-007, SCN-007, SC-005
- [ ] T039 Update OAuth terminal styling only as needed for the completion/status/action surface in frontend/src/styles/mission-control.css covering FR-001, FR-003, FR-006, FR-011
- [ ] T040 Update generated OpenAPI types if schema changes require it by running the repo generator and committing frontend/src/generated/openapi.ts covering FR-001, FR-006, FR-012

### Story Validation

- [ ] T041 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/oauth-terminal.test.tsx frontend/src/components/settings/ProviderProfilesManager.test.tsx` and fix failures in frontend/src/entrypoints/oauth-terminal.tsx and frontend/src/components/settings/ProviderProfilesManager.tsx until T008-T015 and T025-T026 pass
- [ ] T042 Run `./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py` and fix failures in api_service/api/routers/oauth_sessions.py and api_service/api/schemas_oauth_sessions.py until T016-T021 pass
- [ ] T043 Run `./tools/test_unit.sh tests/integration/temporal/test_oauth_session.py` and fix workflow boundary regressions in moonmind/workflows/temporal/workflows/oauth_session.py, api_service/services/oauth_session_service.py, or api_service/api/routers/oauth_sessions.py until T024 passes
- [ ] T044 Validate the end-to-end scenario from specs/306-finalize-oauth-terminal/quickstart.md using test fixtures and record any exact blocker in specs/306-finalize-oauth-terminal/tasks.md

**Checkpoint**: The provider terminal finalization story is functionally complete, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T045 [P] Review frontend text, accessibility labels, disabled states, and action ordering for the terminal completion surface in frontend/src/entrypoints/oauth-terminal.tsx and frontend/src/styles/mission-control.css covering FR-001, FR-003, FR-011
- [ ] T046 [P] Review API error messages and logs for secret hygiene and operator-actionable failure summaries in api_service/api/routers/oauth_sessions.py covering FR-009, FR-012, SC-006
- [ ] T047 [P] Confirm docs/ManagedAgents/OAuthTerminal.md remains source requirements only and no migration checklist was added to canonical docs in docs/ManagedAgents/OAuthTerminal.md covering DESIGN-REQ-001 through DESIGN-REQ-008
- [ ] T048 Run the full required unit suite `./tools/test_unit.sh` and fix regressions in frontend/src/entrypoints/oauth-terminal.tsx, frontend/src/components/settings/ProviderProfilesManager.tsx, api_service/api/routers/oauth_sessions.py, api_service/api/schemas_oauth_sessions.py, api_service/services/oauth_session_service.py, and moonmind/workflows/temporal/workflows/oauth_session.py covering SC-001 through SC-006
- [ ] T049 Run hermetic integration suite `./tools/test_integration.sh` when Docker is available and record any environment blocker in specs/306-finalize-oauth-terminal/tasks.md covering integration_ci expectations
- [ ] T050 Run `/speckit.verify` (`/moonspec-verify` managed workflow equivalent) after implementation and tests pass, and preserve verification evidence against the original request and docs/ManagedAgents/OAuthTerminal.md mappings in specs/306-finalize-oauth-terminal/verification.md covering SC-007

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks all story test and implementation work.
- Phase 3 depends on Phase 2.
- Phase 4 depends on story implementation and focused tests passing.

### Within The Story

- T008-T021 unit tests must be written before production implementation.
- T024-T026 integration and contract tests must be written before production implementation.
- T022, T023, T027, T028, and T029 confirm red-first behavior before T033-T040.
- T030-T032 are conditional fallback tasks for implemented-unverified rows and are only executed if verification tests fail.
- T033-T035 complete API/schema/workflow-facing behavior before frontend success rendering depends on final response shape.
- T036-T039 complete terminal and Settings UI behavior after the response contract is clear.
- T041-T044 validate the story independently before polish.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T005, T006, and T007 can run in parallel after T004 if shared frontend test builders are not needed.
- T008-T015 can run in parallel with T016-T021 because frontend and API tests touch different files.
- T024-T026 can run in parallel after unit test outlines are clear.
- T045-T047 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Frontend and API red-first tests can be authored together:
Task: "T008 Add failing frontend unit tests in frontend/src/entrypoints/oauth-terminal.test.tsx"
Task: "T016 Add failing API unit tests in tests/unit/api_service/api/routers/test_oauth_sessions.py"

# Independent polish reviews can run together:
Task: "T045 Review terminal completion UI in frontend/src/entrypoints/oauth-terminal.tsx"
Task: "T046 Review API error messages in api_service/api/routers/oauth_sessions.py"
```

---

## Implementation Strategy

### Requirement Status Handling

- Code-and-test work: all `missing` and `partial` rows from plan.md are covered by red-first test tasks and implementation tasks, including FR-001, FR-003 through FR-009, FR-011, FR-012, SCN-001, SCN-003 through SCN-009, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-007.
- Verification-first with conditional fallback: `implemented_unverified` rows FR-002, FR-010, DESIGN-REQ-005, and DESIGN-REQ-008 are covered by tests first, with T030-T032 as fallback implementation if those tests fail.
- Already verified: SC-007 remains covered by final `/speckit.verify` traceability work in T050.

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 setup.
2. Write unit tests T008-T021.
3. Write integration/contract tests T024-T026.
4. Run red-first confirmations T022, T023, T027, T028, and T029.
5. Execute conditional fallback tasks T030-T032 only if implemented-unverified checks fail.
6. Implement API/schema/workflow behavior T033-T035.
7. Implement terminal and Settings UI behavior T036-T040.
8. Validate focused tests and quickstart path T041-T044.
9. Complete polish and final verification T045-T050.

---

## Coverage Summary

- Functional requirements: FR-001 through FR-012 are covered.
- Acceptance scenarios: SCN-001 through SCN-009 are covered.
- Success criteria: SC-001 through SC-007 are covered.
- Source design mappings: DESIGN-REQ-001 through DESIGN-REQ-008 are covered.
- Original request: preserved in spec.md and verified in T050.
- Unit tests: frontend terminal, Settings refresh, and API finalization tests are required before implementation.
- Integration tests: workflow boundary, contract-style UI/API flow, and hermetic integration suite are included.
