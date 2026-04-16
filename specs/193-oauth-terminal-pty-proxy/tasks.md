# Tasks: OAuth Terminal PTY Proxy

**Input**: `specs/193-oauth-terminal-pty-proxy/spec.md`, `specs/193-oauth-terminal-pty-proxy/plan.md`, `specs/193-oauth-terminal-pty-proxy/research.md`, `specs/193-oauth-terminal-pty-proxy/data-model.md`, `specs/193-oauth-terminal-pty-proxy/contracts/oauth-terminal-pty-proxy.md`

**Unit command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_terminal_bridge.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/api/test_oauth_terminal_websocket.py`

**Integration command**: `./tools/test_integration.sh`

**Traceability**: FR-001 through FR-007; acceptance scenarios 1-6; SC-001 through SC-006; DESIGN-REQ-011, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-020.

## Phase 1: Setup

- [X] T001 Confirm feature artifacts and active feature pointer for MM-362 in `.specify/feature.json` and `specs/193-oauth-terminal-pty-proxy/spec.md`.
- [X] T002 Confirm focused test targets exist for OAuth router and bridge coverage in `tests/unit/services/temporal/runtime/test_terminal_bridge.py`, `tests/unit/api_service/api/routers/test_oauth_sessions.py`, and `tests/unit/api_service/api/test_oauth_terminal_websocket.py`.

## Phase 2: Foundational

- [X] T003 Review current OAuth attach and generic terminal boundaries in `api_service/api/routers/oauth_sessions.py`, `api_service/api/websockets.py`, and `moonmind/workflows/temporal/runtime/terminal_bridge.py` for DESIGN-REQ-014 and DESIGN-REQ-020.
- [X] T004 Define deterministic fake PTY adapter test seams in `moonmind/workflows/temporal/runtime/terminal_bridge.py` before production forwarding changes for FR-002, FR-003, and DESIGN-REQ-013.

## Phase 3: Story - OAuth Terminal PTY Proxy

**Story summary**: Mission Control OAuth terminal attaches to the real auth-runner PTY through an owner-scoped one-time WebSocket and keeps ordinary task terminal behavior separate.

**Independent test**: Start or simulate a bridge-ready OAuth session, attach with a valid one-time token, exchange terminal input/output, resize, heartbeat, and close frames, and verify safe connection metadata and rejection of unsupported frames.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007; SC-001, SC-002, SC-003, SC-004, SC-005, SC-006; DESIGN-REQ-011, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-020.

- [X] T005 [P] Add failing unit tests for PTY input forwarding, output streaming, resize propagation, heartbeat acknowledgement, and safe counters in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` for FR-002, FR-003, FR-004, SC-002, SC-003, SC-004, DESIGN-REQ-013.
- [X] T006 [P] Add failing unit tests for OAuth attach token use, WebSocket metadata persistence, unsupported frame rejection, and no raw scrollback persistence in `tests/unit/api_service/api/routers/test_oauth_sessions.py` for FR-001, FR-003, FR-004, FR-005, SC-001, SC-003, SC-005, DESIGN-REQ-014.
- [X] T007 [P] Add failing unit tests that keep generic terminal helper behavior separate from OAuth terminal semantics in `tests/unit/api_service/api/test_oauth_terminal_websocket.py` for FR-005, FR-006, SC-006, DESIGN-REQ-014, DESIGN-REQ-020.
- [ ] T008 [P] Add or update integration test coverage for the OAuth session terminal boundary in `tests/integration/temporal/test_oauth_session.py` for acceptance scenarios 1-6 and DESIGN-REQ-011, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015. Blocked locally by missing Docker socket for meaningful auth-runner PTY boundary execution.
- [X] T009 Run the focused unit test command and confirm the new tests fail before implementation in `specs/193-oauth-terminal-pty-proxy/tasks.md`.
- [X] T010 Run or attempt the integration command and record failing or environment-blocked status before implementation in `specs/193-oauth-terminal-pty-proxy/tasks.md`.
- [X] T011 Implement the PTY adapter and frame forwarding helpers in `moonmind/workflows/temporal/runtime/terminal_bridge.py` for FR-002, FR-003, FR-004, DESIGN-REQ-013.
- [X] T012 Wire the OAuth terminal WebSocket to the PTY adapter, token guard, resize/heartbeat handling, output streaming, close metadata, and unsupported-frame rejection in `api_service/api/routers/oauth_sessions.py` for FR-001, FR-002, FR-003, FR-004, FR-005, DESIGN-REQ-011, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015.
- [X] T013 Preserve generic task terminal separation and existing helper expectations in `api_service/api/websockets.py` for FR-006 and DESIGN-REQ-020.
- [X] T014 Update safe metadata persistence so only counters, dimensions, timestamps, and close reasons are stored in `api_service/api/routers/oauth_sessions.py` for FR-003, FR-004, SC-003, SC-004.
- [X] T015 Run focused unit tests and mark story tasks complete only after passing evidence in `specs/193-oauth-terminal-pty-proxy/tasks.md`.
- [X] T016 Run full unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and record pass/fail evidence in `specs/193-oauth-terminal-pty-proxy/tasks.md`.
- [X] T017 Run or attempt `./tools/test_integration.sh` and record pass/fail or Docker blocker evidence in `specs/193-oauth-terminal-pty-proxy/tasks.md`.

## Phase 4: Polish And Verification

- [X] T018 [P] Review `specs/193-oauth-terminal-pty-proxy/contracts/oauth-terminal-pty-proxy.md` against implemented WebSocket behavior and update only if implementation differs for FR-001 through FR-006.
- [X] T019 [P] Review `specs/193-oauth-terminal-pty-proxy/quickstart.md` against executed commands and update validation notes.
- [X] T020 Run `/speckit.verify` equivalent and write `specs/193-oauth-terminal-pty-proxy/verification.md` with final verdict and DESIGN-REQ coverage.

## Execution Evidence

- Red-first focused unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_terminal_bridge.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/api/test_oauth_terminal_websocket.py` failed before implementation with `ImportError: cannot import name 'InMemoryPtyAdapter'`.
- Focused unit after implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_terminal_bridge.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/api/test_oauth_terminal_websocket.py` passed with 32 Python tests and 231 dashboard tests.
- Full unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3444 Python tests, 16 subtests, and 231 dashboard tests.
- Integration: `./tools/test_integration.sh` was attempted and blocked because `/var/run/docker.sock` is unavailable in this managed container.

## Dependencies And Execution Order

1. Setup tasks T001-T002 complete before foundational review.
2. Foundational tasks T003-T004 complete before story tests.
3. Unit and integration test tasks T005-T008 precede red-first confirmation T009-T010.
4. Implementation tasks T011-T014 start only after red-first confirmation.
5. Validation tasks T015-T017 complete before polish and final verification.
6. Final verification T020 is last.

## Parallel Opportunities

- T005, T006, T007, and T008 can be drafted in parallel because they touch different test files.
- T018 and T019 can run in parallel after implementation validation.

## Implementation Strategy

Deliver the smallest runtime change that turns the OAuth attach WebSocket into a real auth-runner PTY proxy while preserving token ownership, TTL, safe metadata, and generic terminal separation. Keep Docker/socket interactions isolated behind bridge helpers so unit tests can use fake adapters and integration tests can cover the real container path when Docker is available.
