# Tasks: TmateSessionManager

**Feature**: `104-tmate-session-manager`
**Branch**: `104-tmate-session-manager`
**Spec**: [spec.md](spec.md)
**Plan**: [plan.md](plan.md)

## Phase 1: Setup

- [ ] T001 Create `moonmind/workflows/temporal/runtime/tmate_session.py` with module docstring and imports (DOC-REQ-001)

## Phase 2: Foundational — Core Dataclasses and Config

- [ ] T002 [P] Implement `TmateServerConfig` dataclass with `host`, `port`, `rsa_fingerprint`, `ed25519_fingerprint` fields and `from_env()` factory method in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-005, DOC-REQ-013)
- [ ] T003 [P] Implement `TmateEndpoints` dataclass with `session_name`, `socket_path`, `attach_ro`, `attach_rw`, `web_ro`, `web_rw` fields in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-002)

## Phase 3: User Story 1 — TmateSessionManager Core (P1)

**Story Goal**: Developers can create, start, and tear down tmate sessions via TmateSessionManager.
**Independent Test**: Instantiate manager, verify start/teardown behavior, validate config file generation.

- [ ] T004 [US1] Implement `TmateSessionManager.__init__()` with `session_name`, `socket_dir`, `server_config` parameters and internal path computation in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-001)
- [ ] T005 [US1] Implement `TmateSessionManager.is_available()` static method using `shutil.which("tmate")` in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-003)
- [ ] T006 [US1] Implement config file generation method that writes `TMATE_FOREGROUND_RESTART_OFF` and optional `TmateServerConfig` set-option directives in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-006)
- [ ] T007 [US1] Implement `TmateSessionManager.start()` method: create socket dir, write config, launch tmate subprocess via `asyncio.create_subprocess_exec`, wait for readiness via `tmate wait tmate-ready`, and extract all 4 endpoint types in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-004, DOC-REQ-012)
- [ ] T008 [US1] Implement `endpoints` and `exit_code_path` properties in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-003)
- [ ] T009 [US1] Implement `TmateSessionManager.teardown()` method: kill tmate process, remove socket/config/exit-code files in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-010)
- [ ] T010 [US1] Create `tests/unit/workflows/temporal/runtime/test_tmate_session.py` with tests for: `is_available()`, `TmateEndpoints` construction, `TmateServerConfig.from_env()`, config file generation with and without server config, `start()` error handling, `teardown()` cleanup (DOC-REQ-001 through DOC-REQ-006, DOC-REQ-010, DOC-REQ-012, DOC-REQ-013)

## Phase 4: User Story 2 — Launcher Refactor (P1)

**Story Goal**: `ManagedRuntimeLauncher.launch()` delegates tmate lifecycle to `TmateSessionManager` — no inline tmate logic remains.
**Independent Test**: Existing launcher tests pass; launcher produces identical endpoint structure.

- [ ] T011 [US2] Refactor `ManagedRuntimeLauncher.launch()` in `moonmind/workflows/temporal/runtime/launcher.py` to replace inline tmate logic (lines 426-527) with `TmateSessionManager` delegation: `is_available()` check, `start()` call, endpoint dict construction from `TmateEndpoints` (DOC-REQ-007, DOC-REQ-011)
- [ ] T012 [US2] Remove `TMATE_SOCKET_DIR` and `TMATE_FOREGROUND_RESTART_OFF` constants from `moonmind/workflows/temporal/runtime/launcher.py` and `_build_tmate_wrapper_script` static method (DOC-REQ-007)
- [ ] T013 [US2] Update existing launcher tests in `tests/unit/workflows/temporal/runtime/test_launcher.py` to verify TmateSessionManager delegation with preserved endpoint dict structure (DOC-REQ-007)

## Phase 5: User Story 3 — OAuth Activities Refactor (P2)

**Story Goal**: `oauth_session_activities.start_auth_runner()` uses shared TmateSessionManager endpoint patterns instead of hardcoded Docker-exec polling.
**Independent Test**: OAuth activity tests pass; shared endpoint key constants used.

- [ ] T014 [US3] Refactor `oauth_session_start_auth_runner()` in `moonmind/workflows/temporal/activities/oauth_session_activities.py` to import and use `TmateEndpoints` field names and endpoint extraction command constants from `tmate_session.py` instead of hardcoded Docker-exec polling patterns (DOC-REQ-008, DOC-REQ-011)
- [ ] T015 [US3] Update existing OAuth activity tests to verify shared patterns are used (DOC-REQ-008)

## Phase 6: User Story 4 — Self-Hosted Server Config (P2)

**Story Goal**: Setting MOONMIND_TMATE_SERVER_* environment variables correctly configures TmateSessionManager to use a private relay.
**Independent Test**: Config file contains expected set-option directives when env vars set.

- [ ] T016 [US4] Wire `TmateServerConfig.from_env()` into `TmateSessionManager.start()` default server_config resolution in `moonmind/workflows/temporal/runtime/tmate_session.py` (DOC-REQ-005, DOC-REQ-013)
- [ ] T017 [US4] Add integration-style unit test in `tests/unit/workflows/temporal/runtime/test_tmate_session.py` verifying config file has server `set-option` directives when MOONMIND_TMATE_SERVER_HOST is set (DOC-REQ-006, DOC-REQ-013)

## Phase 7: Polish & Validation

- [ ] T018 Run full test suite via `./tools/test_unit.sh` and verify all tests pass
- [ ] T019 Verify no remaining inline tmate lifecycle logic in `launcher.py` or `oauth_session_activities.py` (visual code review / grep verification) (DOC-REQ-011)

## Dependencies

```
T001 → T002, T003 (setup before dataclasses)
T002, T003 → T004-T009 (dataclasses before manager)
T004-T009 → T010 (manager before tests)
T010 → T011-T013 (core tested before launcher refactor)
T010 → T014-T015 (core tested before OAuth refactor)
T010 → T016-T017 (core tested before server config wiring)
T011-T017 → T018-T019 (all implementation before final validation)
```

## Parallel Execution Opportunities

- T002 and T003 are parallelizable (separate dataclasses)
- T011-T013 (launcher) and T014-T015 (OAuth) can run in parallel after T010 completes
- T016-T017 (server config) can run in parallel with T011-T015

## Implementation Strategy

**MVP**: Complete Phase 1-3 (setup + dataclasses + TmateSessionManager core + tests). This delivers the shared abstraction without touching existing consumers.

**Incremental**: Then Phase 4 (launcher refactor) delivers the highest-impact consumer migration, followed by Phase 5 (OAuth) and Phase 6 (server config).

## DOC-REQ Coverage Summary

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
|---|---|---|
| DOC-REQ-001 | T001, T004 | T010 |
| DOC-REQ-002 | T003 | T010 |
| DOC-REQ-003 | T005, T008 | T010 |
| DOC-REQ-004 | T007 | T010 |
| DOC-REQ-005 | T002, T016 | T010, T017 |
| DOC-REQ-006 | T006 | T010, T017 |
| DOC-REQ-007 | T011, T012 | T013, T019 |
| DOC-REQ-008 | T014 | T015, T019 |
| DOC-REQ-009 | T004, T007, T009 | T010 |
| DOC-REQ-010 | T009 | T010 |
| DOC-REQ-011 | T011, T014 | T019 |
| DOC-REQ-012 | T007 | T010 |
| DOC-REQ-013 | T002, T016 | T010, T017 |
