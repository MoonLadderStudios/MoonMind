# Tasks: Run Authenticated OAuth Terminal Sessions

**Input**: `specs/174-run-authenticated-oauth-terminal-sessions/spec.md`

- [X] T001 Add API response regression coverage for terminal refs and MoonMind-owned transport metadata. (FR-001)
- [X] T002 Add activity-boundary regression coverage for persisted auth runner metadata and expiry. (FR-002)
- [X] T003 Add WebSocket helper regression coverage for owner, active status, TTL, runner container, and provider bootstrap command selection. (FR-003, FR-004, FR-005)
- [X] T004 Add finalize cleanup regression coverage for success and verification failure paths. (FR-006)
- [X] T005 Extend OAuth session response schema and router mapping with terminal refs and transport metadata. (FR-001)
- [X] T006 Persist runner metadata from `oauth_session.update_terminal_session`. (FR-002)
- [X] T007 Harden terminal WebSocket attach validation and PTY command selection. (FR-003, FR-004, FR-005)
- [X] T008 Stop auth runner containers from API-driven terminal outcomes. (FR-006)
- [X] T009 Run targeted unit tests and update this task list. (SC-001, SC-002, SC-003)
- [X] T010 Run `/speckit.verify` equivalent and record the verdict. (SC-001, SC-002, SC-003)
