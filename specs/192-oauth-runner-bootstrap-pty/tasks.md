# Tasks: OAuth Runner Bootstrap PTY

**Input**: Design documents from `/specs/192-oauth-runner-bootstrap-pty/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/oauth-runner-bootstrap-pty.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single MM-361 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: The original MM-361 Jira preset brief is preserved in `specs/192-oauth-runner-bootstrap-pty/spec.md`. Tasks cover FR-001 through FR-013, acceptance scenarios 1-5, edge cases, SC-001 through SC-006, and DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-014, and DESIGN-REQ-020.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify` (`/speckit.verify` equivalent)

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active MM-361 artifacts and local test targets before writing failing tests.

- [X] T001 Confirm `.specify/feature.json` points to `specs/192-oauth-runner-bootstrap-pty` and MM-361 traceability is present in `specs/192-oauth-runner-bootstrap-pty/spec.md` (FR-012, SC-006)
- [X] T002 Confirm the planned unit and integration commands in `specs/192-oauth-runner-bootstrap-pty/quickstart.md` match the repo test taxonomy in `AGENTS.md` (SC-001, SC-005)
- [X] T003 [P] Review provider registry bootstrap command fields in `moonmind/workflows/temporal/runtime/providers/registry.py` against `specs/192-oauth-runner-bootstrap-pty/contracts/oauth-runner-bootstrap-pty.md` (FR-003, DESIGN-REQ-011)
- [X] T004 [P] Review current placeholder runner startup in `moonmind/workflows/temporal/runtime/terminal_bridge.py` and record the expected red-first failure target in `specs/192-oauth-runner-bootstrap-pty/quickstart.md` (FR-003, SC-002)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish test fixtures and boundary assumptions required before story implementation starts.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T005 Add or adjust process-spawn monkeypatch fixtures for terminal bridge runner startup in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` (FR-002, FR-003, FR-008, FR-009)
- [X] T006 Add or adjust activity monkeypatch fixtures for provider lookup and terminal bridge startup in `tests/unit/auth/test_oauth_session_activities.py` (FR-001, FR-003, DESIGN-REQ-012)
- [X] T007 [P] Add or adjust provider registry fixture coverage helpers in `tests/unit/auth/test_oauth_provider_registry.py` (FR-003, DESIGN-REQ-011)
- [X] T008 [P] Add or adjust Temporal OAuth session integration activity stubs in `tests/integration/temporal/test_oauth_session.py` to record start and stop runner calls without live provider credentials (FR-006, FR-007, DESIGN-REQ-012)

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - OAuth Runner Bootstrap PTY

**Summary**: As a MoonMind operator, I want Codex OAuth enrollment to launch a short-lived, session-owned auth runner that executes the provider bootstrap command in an interactive terminal so that credential enrollment is first-party and no longer depends on placeholder container behavior.

**Independent Test**: Start a Codex OAuth session using a fake provider bootstrap command, complete or terminate the session through success, failure, expiry, and cancellation paths, and verify volume targeting, command execution ownership, bridge-only access, idempotent cleanup, and redacted failure reporting.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013; acceptance scenarios 1-5; SC-001 through SC-006; DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-014, DESIGN-REQ-020

**Test Plan**:

- Unit: provider registry bootstrap command validation, activity-to-runtime command resolution, terminal bridge command startup, redacted failures, generic exec rejection, and idempotent cleanup.
- Integration: Temporal OAuth session success, failure, cancellation, expiry, API-finalize, and runner stop paths through the existing workflow/activity boundary.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [X] T009 [P] Add failing unit tests proving OAuth provider specs expose non-empty provider bootstrap commands and Codex no longer uses placeholder-only behavior in `tests/unit/auth/test_oauth_provider_registry.py` (FR-003, SC-002, DESIGN-REQ-011)
- [X] T010 [P] Add failing unit tests proving `oauth_session.start_auth_runner` resolves the provider bootstrap command and passes it to terminal bridge startup in `tests/unit/auth/test_oauth_session_activities.py` (FR-001, FR-003, DESIGN-REQ-012)
- [X] T011 [P] Add failing unit tests proving missing or unsupported provider bootstrap command values fail fast with redacted reasons in `tests/unit/auth/test_oauth_session_activities.py` (FR-008, FR-009, FR-013)
- [X] T012 [P] Add failing unit tests proving terminal bridge runner startup uses the selected auth volume mount and provider bootstrap command instead of `sleep` in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` (FR-002, FR-003, SC-001, SC-002)
- [X] T013 [P] Add failing unit tests proving terminal bridge startup redacts Docker, mount, timeout, and command failure details in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` (FR-008, FR-009, FR-013)
- [X] T014 [P] Add failing unit tests proving OAuth terminal bridge rejects generic Docker exec and ordinary task terminal frames while preserving authenticated bridge-only behavior in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` (FR-004, FR-005, FR-010, DESIGN-REQ-014)
- [X] T015 [P] Add failing unit tests proving auth runner cleanup is idempotent for missing, already-stopped, and remove-failed containers in `tests/unit/auth/test_oauth_session_activities.py` (FR-006, FR-007, SC-003)
- [X] T016 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` to confirm T009-T015 fail for the expected MM-361 reasons in `specs/192-oauth-runner-bootstrap-pty/quickstart.md` (SC-001, SC-002, SC-003, SC-004)

### Integration Tests (write first) ⚠️

- [X] T017 [P] Add failing Temporal integration coverage proving OAuth session success starts and stops the session-owned runner through `oauth_session.start_auth_runner` and `oauth_session.stop_auth_runner` in `tests/integration/temporal/test_oauth_session.py` (acceptance scenarios 1, 2, and 4; FR-001, FR-002, FR-003, FR-006)
- [X] T018 [P] Add failing Temporal integration coverage proving cancellation, expiry, failure signal, and API-finalize paths stop the auth runner consistently in `tests/integration/temporal/test_oauth_session.py` (acceptance scenario 4; FR-006, FR-007, SC-003)
- [X] T019 [P] Add failing Temporal integration coverage proving workflow activity payload shape remains compatible, bridge-only terminal metadata is preserved, and provider bootstrap command resolution stays inside the activity/runtime boundary in `tests/integration/temporal/test_oauth_session.py` (acceptance scenario 3; FR-003, FR-004, FR-005, FR-011, DESIGN-REQ-014, DESIGN-REQ-020)
- [X] T020 [P] Add failing Temporal integration coverage proving auth runner launch failure returns an actionable redacted failure reason in `tests/integration/temporal/test_oauth_session.py` (acceptance scenario 5; FR-008, FR-009, FR-013)
- [X] T021 Run `./tools/test_integration.sh` or record the Docker socket blocker in `specs/192-oauth-runner-bootstrap-pty/quickstart.md` to confirm T017-T020 fail for the expected MM-361 reasons before production changes (SC-005, DESIGN-REQ-012)

### Red-First Confirmation ⚠️

- [X] T022 Confirm unit failures from T016 identify placeholder sleep behavior, missing provider command resolution, or missing redaction in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` and `tests/unit/auth/test_oauth_session_activities.py` before editing production code (SC-001, SC-002, SC-004)
- [X] T023 Confirm integration failures from T021 identify missing MM-361 runner lifecycle behavior or document the unavailable Docker blocker in `tests/integration/temporal/test_oauth_session.py` before editing production code (SC-005)

### Implementation

- [X] T024 Replace placeholder OAuth provider bootstrap command values with validated runtime provider bootstrap commands in `moonmind/workflows/temporal/runtime/providers/registry.py` (FR-003, DESIGN-REQ-011)
- [X] T025 Add provider bootstrap command validation helpers in `moonmind/workflows/temporal/runtime/providers/registry.py` (FR-003, FR-008, FR-009)
- [X] T026 Update `oauth_session.start_auth_runner` to resolve provider bootstrap commands by `runtime_id`, fail fast for missing commands, and pass commands to terminal bridge startup in `moonmind/workflows/temporal/activities/oauth_session_activities.py` (FR-001, FR-003, FR-008, FR-009, DESIGN-REQ-012)
- [X] T027 Update terminal bridge runner startup to accept a provider bootstrap command, mount the selected auth volume, and start the command as the session-owned terminal process instead of placeholder sleep behavior in `moonmind/workflows/temporal/runtime/terminal_bridge.py` (FR-002, FR-003, SC-001, SC-002)
- [X] T028 Add bounded redaction for runner startup, Docker, mount, timeout, and command failures in `moonmind/workflows/temporal/runtime/terminal_bridge.py` (FR-008, FR-009, FR-013)
- [X] T029 Ensure auth runner cleanup remains idempotent and returns stable secret-free outcomes in `api_service/services/oauth_auth_runner.py` and `moonmind/workflows/temporal/activities/oauth_session_activities.py` (FR-006, FR-007, SC-003)
- [X] T030 Confirm OAuth workflow failure and stop paths continue to call existing activities with compatible payloads in `moonmind/workflows/temporal/workflows/oauth_session.py` (FR-006, FR-007, FR-011, DESIGN-REQ-020)
- [X] T031 Confirm generic Docker exec and ordinary task terminal attachment remain rejected or omitted through terminal bridge frame handling in `moonmind/workflows/temporal/runtime/terminal_bridge.py` (FR-004, FR-005, FR-010, DESIGN-REQ-014)
- [X] T032 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` and fix failures in touched production files until the focused unit suite passes (SC-001, SC-002, SC-003, SC-004)
- [X] T033 Run `./tools/test_integration.sh` when Docker is available or record the exact Docker blocker in `specs/192-oauth-runner-bootstrap-pty/quickstart.md` (SC-005)

**Checkpoint**: The MM-361 story is fully functional, covered by unit and integration strategy, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T034 [P] Update `specs/192-oauth-runner-bootstrap-pty/contracts/oauth-runner-bootstrap-pty.md` if implementation evidence changes the runner startup or failure contract without changing story scope (FR-012, SC-006)
- [X] T035 [P] Update `specs/192-oauth-runner-bootstrap-pty/quickstart.md` with final focused unit, full unit, and integration command results or blockers (FR-012, SC-006)
- [X] T036 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification and record the result in `specs/192-oauth-runner-bootstrap-pty/quickstart.md` (SC-006)
- [X] T037 Run `/moonspec-verify` (`/speckit.verify` equivalent) against `specs/192-oauth-runner-bootstrap-pty/spec.md` after implementation and tests pass, preserving MM-361 and the original preset brief in verification output (FR-012, SC-006)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish & Verification (Phase 4)**: Depends on story implementation and focused validation passing.

### Within The Story

- Unit tests T009-T015 must be written before implementation tasks T024-T031.
- Integration tests T017-T020 must be written before implementation tasks T024-T031.
- Red-first confirmation tasks T022-T023 must complete before production code tasks T024-T031.
- Provider registry validation T024-T025 precedes activity resolution T026.
- Activity resolution T026 precedes terminal bridge command startup T027-T028.
- Cleanup and workflow compatibility tasks T029-T030 follow the runner startup contract.
- Focused validation T032-T033 follows all production code tasks.
- Final verification T037 follows full unit verification T036.

### Parallel Opportunities

- T003 and T004 can run in parallel during setup.
- T007 and T008 can run in parallel with T005-T006 because they touch different test files.
- T009, T012, T014, and T017 can be authored in parallel after foundational fixtures are ready because they touch different files.
- T034 and T035 can run in parallel after implementation validation because they update separate MoonSpec artifacts.

---

## Parallel Example: Story Phase

```bash
# Launch independent red-first test authoring together:
Task: "T009 Add failing provider registry bootstrap tests in tests/unit/auth/test_oauth_provider_registry.py"
Task: "T012 Add failing terminal bridge startup command tests in tests/unit/services/temporal/runtime/test_terminal_bridge.py"
Task: "T017 Add failing Temporal success lifecycle coverage in tests/integration/temporal/test_oauth_session.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 setup checks and Phase 2 fixtures.
2. Write unit tests T009-T015 and confirm focused unit failures with T016.
3. Write integration tests T017-T020 and confirm failures or Docker blocker with T021.
4. Complete red-first confirmation T022-T023.
5. Implement provider registry validation, activity command resolution, terminal bridge runner startup, failure redaction, cleanup idempotency, and workflow compatibility through T024-T031.
6. Run focused unit and integration validation with T032-T033.
7. Complete polish, full unit verification, and `/moonspec-verify` through T034-T037.

---

## Notes

- This task list covers exactly one story: MM-361 OAuth Runner Bootstrap PTY.
- Do not add managed Codex task execution changes, Claude/Gemini parity work, generic Docker exec exposure, or ordinary managed task terminal attachment.
- Preserve `MM-361` and the original Jira preset brief in implementation notes, verification output, commit text, and pull request metadata.
- Integration tests may be blocked inside managed-agent containers without Docker socket access; record that blocker explicitly instead of treating integration as passed.
