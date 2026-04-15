# Tasks: Run Authenticated OAuth Terminal Sessions

**Input**: `specs/174-run-authenticated-oauth-terminal-sessions/spec.md`

- [X] T001 Add red-first unit regression coverage for API response terminal refs and MoonMind-owned transport metadata. (FR-001)
- [X] T002 Add red-first activity-boundary unit regression coverage for persisted auth runner metadata and expiry. (FR-002)
- [X] T003 Add red-first WebSocket helper unit regression coverage for owner, active status, TTL, runner container, and provider bootstrap command selection. (FR-003, FR-004, FR-005)
- [X] T004 Add red-first API unit regression coverage for finalize cleanup on success and verification failure paths. (FR-006)
- [X] T004a Add red-first Temporal integration regression coverage for externally observed OAuth terminal failure. (FR-006)
- [X] T005 Extend OAuth session response schema and router mapping with terminal refs and transport metadata. (FR-001)
- [X] T006 Persist runner metadata from `oauth_session.update_terminal_session`. (FR-002)
- [X] T007 Harden terminal WebSocket attach validation and PTY command selection. (FR-003, FR-004, FR-005)
- [X] T008 Stop auth runner containers from API-driven terminal outcomes. (FR-006)
- [X] T009 Run targeted unit and Temporal integration tests and update this task list. (SC-001, SC-002, SC-003)
- [X] T010 Run `/speckit.verify` equivalent and record the verdict. (SC-001, SC-002, SC-003)
