# Tasks: Claude OAuth Session Backend

**Input**: Design documents from `/specs/241-claude-oauth-session-backend/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-478, FR-001 through FR-009, SC-001 through SC-005, and DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Integration tests: focused route/workflow boundary tests through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh <target>`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm active artifacts and focused test surfaces.

- [X] T001 Create MoonSpec artifacts in `specs/241-claude-oauth-session-backend/` and preserve MM-478 traceability.
- [X] T002 Inspect existing provider registry, OAuth session route, terminal runner, activity, seed profile, and tests for FR-001 through FR-009.

---

## Phase 2: Foundational

**Purpose**: No new infrastructure is required; this story uses existing OAuth Session and provider-profile boundaries.

- [X] T003 Confirm no schema migration or new persistent table is needed for MM-478 in `api_service/db/models.py` and existing OAuth session/profile models.

**Checkpoint**: Existing OAuth session/profile foundation is sufficient.

---

## Phase 3: Story - Claude OAuth Session Backend

**Summary**: As an operator, when I choose Connect with Claude OAuth, MoonMind creates a Claude-specific OAuth session that starts a short-lived auth runner with the correct mounted Claude home and bootstrap command.

**Independent Test**: Simulate or create a `claude_anthropic` OAuth session and verify provider defaults, persisted session transport, auth-runner Docker arguments, and seeded profile shape.

**Traceability**: FR-001 through FR-009, SC-001 through SC-005, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018.

**Test Plan**:

- Unit: provider registry defaults, activity bootstrap command resolution, terminal runner Docker argument/environment construction, startup seed profile shape.
- Integration: route-level OAuth session creation boundary with DB persistence and mocked workflow start.

### Unit Tests (write first)

- [X] T004 [P] Add failing exact Claude provider defaults test in `tests/unit/auth/test_oauth_provider_registry.py` for FR-002, SC-002, DESIGN-REQ-010.
- [X] T005 [P] Add failing Claude auth-runner activity bootstrap test in `tests/unit/auth/test_oauth_session_activities.py` for FR-003, DESIGN-REQ-006.
- [X] T006 [P] Add failing Claude terminal runner env/mount test in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` for FR-004, FR-005, SC-003, DESIGN-REQ-011, DESIGN-REQ-012.
- [X] T007 [P] Add failing `claude_anthropic` seed profile shape test in `tests/unit/api_service/test_provider_profile_auto_seed.py` for FR-006, FR-007, SC-004, DESIGN-REQ-003, DESIGN-REQ-017, DESIGN-REQ-018.
- [X] T008 Run focused unit tests from T004 through T007 and confirm they fail for the expected missing Claude OAuth behavior.

### Integration Tests (write first)

- [X] T009 [P] Add failing route-level OAuth session creation test in `tests/unit/api_service/api/routers/test_oauth_sessions.py` proving `claude_anthropic` stores `moonmind_pty_ws` and Claude defaults for FR-001, SC-001.
- [X] T010 Run focused OAuth session route test and confirm it fails for the expected missing Claude session transport default.

### Implementation

- [X] T011 Update `moonmind/workflows/temporal/runtime/providers/registry.py` so `claude_code` uses `moonmind_pty_ws` and `["claude", "login"]` for FR-002, FR-003, DESIGN-REQ-010.
- [X] T012 Update `moonmind/workflows/temporal/runtime/terminal_bridge.py` to emit runtime-specific OAuth runner environment variables and empty Claude API-key variables for FR-004, FR-005, FR-009, DESIGN-REQ-011, DESIGN-REQ-012.
- [X] T013 Update `api_service/main.py` seeded `claude_anthropic` profile to include `CLAUDE_API_KEY` in `clear_env_keys` for FR-006, FR-007, DESIGN-REQ-003, DESIGN-REQ-018.
- [X] T014 Run focused tests for provider registry, OAuth activities, terminal bridge, OAuth sessions route, and provider profile auto-seed; fix failures.

**Checkpoint**: Claude OAuth backend behavior is implemented and covered by focused tests.

---

## Phase 4: Polish & Verification

- [X] T015 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification.
- [X] T016 Run `/speckit.verify` equivalent and record results in `specs/241-claude-oauth-session-backend/verification.md`.
- [X] T017 Review diff for MM-478 traceability, secret hygiene, and absence of unrelated changes.

---

## Dependencies & Execution Order

- T001-T003 before story implementation.
- T004-T010 must be written and observed failing before T011-T013.
- T011-T013 can be implemented after red-first confirmation.
- T014 before T015-T017.

## Parallel Opportunities

- T004, T005, T006, T007, and T009 can be authored in parallel because they touch different test files.
- T011 and T013 are independent; T012 is independent of both after tests are written.

## Implementation Strategy

Use TDD with focused backend boundaries first. Keep the implementation narrow: registry data, runner argument construction, and startup seed profile metadata. Do not add new OAuth endpoints, tables, workflow types, or frontend scope.
