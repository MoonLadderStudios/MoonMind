# Tasks: Claude Browser Terminal Sign-In Ceremony

**Input**: Design documents from `/specs/242-claude-browser-terminal-signin/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style UI tests are REQUIRED. Write verification tests first, confirm they pass for already-implemented behavior or expose a focused gap, then implement production code only if the tests fail for a real missing behavior. Red-first confirmation applies to any implementation contingency: if a verification test exposes a real missing behavior, keep or adjust that failing test first, confirm the expected failure, and only then change production code.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-479, FR-001 through FR-009, SC-001 through SC-005, and DESIGN-REQ-006 through DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-017.

**Test Commands**:

- Focused tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/services/temporal/runtime/test_terminal_bridge.py --ui-args frontend/src/entrypoints/mission-control.test.tsx`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm active artifacts and focused test surfaces.

- [X] T001 Create MoonSpec artifacts in `specs/242-claude-browser-terminal-signin/` and preserve MM-479 traceability.
- [X] T002 Inspect existing OAuth terminal page, OAuth session route, terminal bridge, and tests for FR-001 through FR-009.

---

## Phase 2: Foundational

**Purpose**: No new persistence or infrastructure is required; this story uses existing OAuth Session and PTY/WebSocket boundaries.

- [X] T003 Confirm no schema migration, new endpoint, or new terminal surface is needed in `api_service/db/models.py`, `api_service/api/routers/oauth_sessions.py`, and `frontend/src/entrypoints/oauth-terminal.tsx`.

**Checkpoint**: Existing OAuth terminal foundation is sufficient.

---

## Phase 3: Story - Claude Browser Terminal Sign-In Ceremony

**Summary**: As an operator, I can complete Claude OAuth in a MoonMind browser terminal by opening Claude's authentication URL externally and pasting the returned token or code back into the terminal while the session waits for me.

**Independent Test**: Simulate a Claude OAuth session reaching `awaiting_user`, attach the browser terminal with a one-time token, forward a returned code to the PTY, and verify only secret-free metadata persists.

**Traceability**: FR-001 through FR-009, SC-001 through SC-005, DESIGN-REQ-006 through DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-017.

**Test Plan**:

- Unit: terminal bridge forwards Claude auth-code-like input to PTY and safe metadata excludes raw input; generic terminal frames remain rejected.
- Route: Claude awaiting-user attach returns a one-time token and persists only a token hash.
- Integration-style UI: OAuth terminal page waits through non-ready states and attaches once a Claude session is `awaiting_user`.

### Verification Tests (write first)

- [X] T004 [P] Add Claude awaiting-user attach-token route test in `tests/unit/api_service/api/routers/test_oauth_sessions.py` for FR-003, FR-007, SC-002, DESIGN-REQ-009, DESIGN-REQ-016, DESIGN-REQ-017.
- [X] T005 [P] Add Claude authorization-code PTY forwarding and safe metadata test in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` for FR-004, FR-005, FR-006, SC-003, DESIGN-REQ-008, DESIGN-REQ-010.
- [X] T006 [P] Add Claude OAuth terminal UI attach test in `frontend/src/entrypoints/mission-control.test.tsx` for FR-001, FR-002, FR-003, SC-001, DESIGN-REQ-006, DESIGN-REQ-009.
- [X] T007 Run focused tests from T004 through T006 and classify failures as implementation gaps or verification-only evidence.

### Implementation Contingency

- [X] T008 Confirm no production change is needed in `api_service/api/routers/oauth_sessions.py` because T004 passed for FR-003 and FR-007.
- [X] T009 Confirm no production change is needed in `moonmind/workflows/temporal/runtime/terminal_bridge.py` because T005 passed for FR-004 through FR-006.
- [X] T010 Confirm no production change is needed in `frontend/src/entrypoints/oauth-terminal.tsx` because T006 passed for FR-001 through FR-003.
- [X] T011 Run focused tests again and confirm MM-479 behavior passes.

**Checkpoint**: Claude browser terminal ceremony is implemented or verified with focused evidence.

---

## Phase 4: Story Validation

- [X] T012 Validate the single MM-479 story against `specs/242-claude-browser-terminal-signin/spec.md`, `specs/242-claude-browser-terminal-signin/plan.md`, focused route/unit/UI tests, and `specs/242-claude-browser-terminal-signin/verification.md`.

---

## Phase 5: Polish & Verification

- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification.
- [X] T014 Run `/moonspec-verify` equivalent and record results in `specs/242-claude-browser-terminal-signin/verification.md`.
- [X] T015 Review diff for MM-479 traceability, secret hygiene, and absence of unrelated changes.

---

## Dependencies & Execution Order

- T001-T003 before story verification.
- T004-T006 can be authored in parallel.
- T007 before any implementation contingency tasks.
- T008-T010 only if focused verification exposes real implementation gaps.
- T011 before T012-T015.

## Parallel Opportunities

- T004, T005, and T006 touch different test files and can be authored in parallel.
- T008, T009, and T010 touch separate production files, but should only run for observed gaps.

## Implementation Strategy

Use test-first verification. The expected path is adding focused evidence around existing OAuth terminal behavior. Keep any production changes narrow and limited to the shared OAuth session route, terminal bridge, or OAuth terminal page; do not add a Claude-only terminal stack, new storage, or new credential persistence path.
