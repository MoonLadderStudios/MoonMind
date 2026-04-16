# Tasks: OAuth Terminal Enrollment Flow

**Input**: `specs/183-oauth-terminal-flow/spec.md`, `specs/183-oauth-terminal-flow/plan.md`, `specs/183-oauth-terminal-flow/research.md`, `specs/183-oauth-terminal-flow/data-model.md`, `specs/183-oauth-terminal-flow/contracts/oauth-terminal-flow.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py`
- UI command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx`
- Integration command: `./tools/test_integration.sh`; required coverage target: `tests/integration/temporal/test_oauth_session.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Story: `STORY-004` from MM-358 Jira preset brief
- Coverage: FR-001 through FR-005; DESIGN-REQ-001, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020
- Original preset brief: `MM-358: OAuth Terminal Enrollment Flow`

## Phase 1: Setup

- [X] T001 Confirm active story artifacts and MM-358 traceability in specs/183-oauth-terminal-flow/spec.md, specs/183-oauth-terminal-flow/plan.md, specs/183-oauth-terminal-flow/research.md, specs/183-oauth-terminal-flow/data-model.md, and specs/183-oauth-terminal-flow/contracts/oauth-terminal-flow.md (STORY-004)
- [X] T002 Confirm focused unit and integration commands from specs/183-oauth-terminal-flow/quickstart.md are runnable or have exact environment blockers recorded (STORY-004)

## Phase 2: Foundational

- [X] T003 Inspect existing production touchpoints named in specs/183-oauth-terminal-flow/plan.md and map current behavior before writing tests (FR-001 through FR-005; DESIGN-REQ-001, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020)
- [X] T004 Prepare or update shared test fixtures needed by this story in tests/unit/ and tests/integration/ without implementing production behavior (STORY-004)

## Phase 3: OAuth Terminal Enrollment Flow

Story summary: Provide first-party OAuth enrollment through a short-lived auth runner and authenticated PTY/WebSocket bridge.

Independent test: Start OAuth session with fake bootstrap command, attach over WebSocket, finalize, and assert terminal metadata, cleanup, and status transitions.

Traceability IDs: FR-001 through FR-005; DESIGN-REQ-001, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020

Unit test plan: write red-first unit tests for validation, serialization, redaction, terminal bridge frame handling, Mission Control attach behavior, and boundary payload behavior before production code.

Integration test plan: write red-first hermetic integration tests for the real API/workflow/runtime/container/UI boundary before production code when Docker/browser services are available.

- [X] T005 [P] Add failing unit test for OAuth session authorization, creation, attach, cancel, finalize behavior for FR-001 FR-004 DESIGN-REQ-011 in tests/unit/api_service/api/routers/test_oauth_sessions.py
- [X] T006 [P] Add failing unit test for auth runner lifecycle and cleanup behavior for FR-002 FR-004 DESIGN-REQ-012 in tests/unit/auth/test_oauth_session_activities.py
- [X] T007 [P] Add failing unit test for PTY/WebSocket resize/input/output/heartbeat ownership behavior for FR-003 FR-004 DESIGN-REQ-013 DESIGN-REQ-014 in tests/unit/services/temporal/runtime/test_terminal_bridge.py
- [X] T008 [P] Add failing unit test for Mission Control terminal rendering and no task-terminal exposure for FR-003 FR-004 DESIGN-REQ-008 in frontend/src/entrypoints/mission-control.test.tsx
- [X] T009 [P] Add failing integration test for OAuth workflow plus terminal bridge lifecycle for SC-001 through SC-005 DESIGN-REQ-011 DESIGN-REQ-014 in tests/integration/temporal/test_oauth_session.py
- [X] T010 Run red-first focused Python unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` and UI tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx` and record expected failures in specs/183-oauth-terminal-flow/tasks.md (STORY-004)
- [X] T011 Run red-first integration tests with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`; otherwise record `/var/run/docker.sock` blocker in specs/183-oauth-terminal-flow/tasks.md (STORY-004)
- [X] T012 Implement OAuth session create/attach/cancel/finalize API behavior for FR-001 FR-004 in api_service/api/routers/oauth_sessions.py and api_service/api/schemas_oauth_sessions.py
- [X] T013 Implement authenticated PTY/WebSocket bridge behavior for FR-003 FR-004 in moonmind/workflows/temporal/runtime/terminal_bridge.py
- [X] T014 Implement auth runner startup and cleanup for FR-002 FR-004 in moonmind/workflows/temporal/activities/oauth_session_activities.py
- [X] T015 Implement session lifecycle transitions and cleanup coordination for FR-001 FR-004 in moonmind/workflows/temporal/workflows/oauth_session.py
- [X] T016 Implement Mission Control terminal surface for FR-003 in frontend/src/entrypoints/oauth-terminal.tsx and frontend/src/entrypoints/mission-control-app.tsx
- [X] T017 Run focused Python unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` and UI tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx` and update task evidence in specs/183-oauth-terminal-flow/tasks.md
- [X] T018 Run integration verification with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`; update task evidence in specs/183-oauth-terminal-flow/tasks.md
- [X] T019 Validate the single-story acceptance scenarios and MM-358 traceability against specs/183-oauth-terminal-flow/spec.md and specs/183-oauth-terminal-flow/contracts/oauth-terminal-flow.md

## Final Phase: Polish And Verification

- [X] T020 Refactor only story-local code and tests after green validation in files named by specs/183-oauth-terminal-flow/plan.md
- [X] T021 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-004
- [X] T022 Run quickstart validation from specs/183-oauth-terminal-flow/quickstart.md and record any Docker integration blocker
- [X] T023 Run final `/moonspec-verify` equivalent for specs/183-oauth-terminal-flow/spec.md and write verification result in specs/183-oauth-terminal-flow/verification.md

## Dependencies And Order

- Complete Phase 1 and Phase 2 before writing red-first story tests.
- Write and confirm unit and integration tests fail before implementation tasks.
- Complete implementation tasks before green validation and final verification.
- Keep this task list scoped to exactly one story; do not implement neighboring OAuth Terminal generated specs here.

## Parallel Examples

- Unit test tasks marked `[P]` may run in parallel when they touch different files.
- Integration test tasks marked `[P]` may run in parallel with unit test authoring, but red-first confirmation must wait for both groups.

## Implementation Strategy

- Follow TDD: red unit tests, red integration tests, production implementation, focused green tests, full unit suite, final `/moonspec-verify`.
- Preserve `MM-358` and the preset brief in all verification evidence.
- Treat missing Docker socket as a blocker to record, not a passing integration result.

## Execution Evidence

- Focused backend and bridge unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` passed with 28 Python tests and dashboard tests green.
- Focused Mission Control UI test: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx` passed with 4 tests.
- Full unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3435 Python tests, 16 subtests, and 225 dashboard tests.
- Integration verification: `./tools/test_integration.sh` was not run because `/var/run/docker.sock` is absent in this managed container and `docker ps` cannot connect to the Docker API.
- Moon Spec prerequisite script: `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` is blocked by branch naming (`mm-358-148db66e` does not match the expected numeric feature-branch pattern); artifacts were inspected directly under `specs/183-oauth-terminal-flow`.
