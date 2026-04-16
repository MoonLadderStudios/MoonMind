# Tasks: OAuth Terminal Docker Verification

**Input**: Design documents from `/specs/194-oauth-terminal-docker-verification/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/oauth-terminal-docker-verification.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write or adjust tests first only if a product or harness gap is found; the primary required evidence is Docker-backed integration verification.

**Organization**: Tasks are grouped by phase around the single MM-363 story so verification closure stays focused and traceable.

**Source Traceability**: The original MM-363 Jira preset brief is preserved in `specs/194-oauth-terminal-docker-verification/spec.md`. Tasks cover FR-001 through FR-009, acceptance scenarios 1-7, SC-001 through SC-005, and DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-017, DESIGN-REQ-018, and DESIGN-REQ-020.

**Test Commands**:

- Unit tests: use the focused unit command defined in `specs/194-oauth-terminal-docker-verification/quickstart.md`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify` (`/speckit.verify` equivalent)

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active MM-363 artifacts and the verification environment before changing reports or code.

- [X] T001 Confirm `.specify/feature.json` points to `specs/194-oauth-terminal-docker-verification` and MM-363 traceability is present in `specs/194-oauth-terminal-docker-verification/spec.md` (FR-009, SC-005)
- [X] T002 [P] Confirm no existing feature directory already owns MM-363 using `rg -n "MM-363" specs docs/tmp` (FR-009)
- [X] T003 [P] Inspect `specs/175-launch-codex-auth-materialization/verification.md`, `specs/180-codex-volume-targeting/verification.md`, and `specs/183-oauth-terminal-flow/verification.md` for current Docker-backed evidence gaps (FR-007, SC-004)
- [X] T004 Check Docker availability with `test -S /var/run/docker.sock` and `docker ps`, recording the result in `specs/194-oauth-terminal-docker-verification/quickstart.md` (FR-001, SC-001)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish whether runtime verification can proceed in this environment.

**CRITICAL**: Report closure and code changes are blocked until Docker-backed verification can run or a precise blocker is recorded.

- [X] T005 Confirm `./tools/test_integration.sh` is the required hermetic integration command from repo instructions and `specs/194-oauth-terminal-docker-verification/quickstart.md` (FR-001, SC-001)
- [X] T006 Confirm the active runtime lacks Docker socket access and therefore cannot produce closure evidence in this managed-agent container (FR-001, FR-008, SC-001)

**Checkpoint**: Foundation complete; implementation is blocked in this runtime by missing Docker access.

---

## Phase 3: Story - OAuth Terminal Docker Verification

**Summary**: As a MoonMind maintainer, I want Docker-enabled integration evidence for OAuthTerminal managed-session auth behavior so prior ADDITIONAL_WORK_NEEDED reports can be closed with real runtime proof.

**Independent Test**: Run `./tools/test_integration.sh` in a Docker-enabled environment and confirm managed Codex launch, auth-volume targeting, one-way seeding, and OAuth terminal auth runner/PTY bridge lifecycle behavior.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009; acceptance scenarios 1-7; SC-001 through SC-005; DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-020

**Test Plan**:

- Unit: run focused unit targets only when a non-Docker runtime or harness fix is needed.
- Integration: run `./tools/test_integration.sh`; use focused integration targets only to diagnose failures after Docker is available.

### Unit Tests (write first) ⚠️

> **NOTE: This story is verification-first. Add or adjust unit tests only after Docker-backed integration identifies a product or harness gap that unit coverage can isolate.**

- [ ] T007 [P] Add failing unit coverage for any managed-session launch validation gap identified by Docker-backed verification in `tests/unit/services/temporal/runtime/test_managed_session_controller.py` or `tests/unit/services/temporal/runtime/test_codex_session_runtime.py` (FR-002, FR-003, FR-004, FR-005)
- [ ] T008 [P] Add failing unit coverage for any OAuth terminal runner or PTY bridge gap identified by Docker-backed verification in `tests/unit/auth/test_oauth_session_activities.py` or `tests/unit/services/temporal/runtime/test_terminal_bridge.py` (FR-006, DESIGN-REQ-018)
- [ ] T009 Run the focused unit command from `specs/194-oauth-terminal-docker-verification/quickstart.md` if T007 or T008 changes tests, and confirm failures before production fixes (SC-002, SC-003)

### Integration Tests (write first) ⚠️

- [ ] T010 Run `./tools/test_integration.sh` in a Docker-enabled environment and capture a secret-free summary in `specs/194-oauth-terminal-docker-verification/quickstart.md` (FR-001, SC-001)
- [ ] T011 If the full integration suite fails, run focused integration targets in `tests/integration/services/temporal/test_codex_session_task_creation.py`, `tests/integration/services/temporal/test_codex_session_runtime.py`, and `tests/integration/temporal/test_oauth_session.py` to isolate OAuthTerminal-relevant failures (FR-002 through FR-006)
- [X] T012 If Docker is unavailable, record the exact blocker in `specs/194-oauth-terminal-docker-verification/quickstart.md` and do not close prior reports (FR-008, SC-001)

### Red-First Confirmation ⚠️

- [ ] T013 Confirm any new unit or integration test failure identifies the intended MM-363 behavior gap before editing production code (SC-002, SC-003)
- [X] T014 Confirm missing Docker socket blocks red-first integration execution in this managed-agent runtime (FR-001, FR-008, SC-001)

### Implementation

- [ ] T015 Apply the smallest runtime or test harness fix needed for a Docker-backed managed Codex launch evidence gap in `moonmind/workflows/temporal/runtime/managed_session_controller.py`, `moonmind/workflows/temporal/runtime/codex_session_runtime.py`, or integration tests (FR-002 through FR-005)
- [ ] T016 Apply the smallest runtime or test harness fix needed for a Docker-backed OAuth terminal runner/PTY bridge evidence gap in `moonmind/workflows/temporal/activities/oauth_session_activities.py`, `moonmind/workflows/temporal/runtime/terminal_bridge.py`, or integration tests (FR-006)
- [ ] T017 Rerun focused unit and integration checks after any fix until the story evidence passes or a precise blocker remains (SC-001 through SC-003)
- [X] T018 Update `specs/175-launch-codex-auth-materialization/verification.md`, `specs/180-codex-volume-targeting/verification.md`, and `specs/183-oauth-terminal-flow/verification.md` only with passing Docker-backed evidence or the exact remaining blocker, preserving MM-363 traceability (FR-007, FR-008, FR-009, SC-004, SC-005)

**Checkpoint**: The MM-363 story is complete only when Docker-backed evidence passes and affected verification reports are updated accordingly. In this managed-agent runtime, implementation is blocked by missing Docker socket access.

---

## Phase 4: Polish & Verification

**Purpose**: Finalize evidence without adding hidden scope.

- [ ] T019 Run the relevant unit command from `specs/194-oauth-terminal-docker-verification/quickstart.md` if any code or unit-test files changed (SC-005)
- [X] T020 Run `/moonspec-verify` (`/speckit.verify` equivalent) against `specs/194-oauth-terminal-docker-verification/spec.md`, preserving MM-363 in verification output (FR-009, SC-005)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on setup and blocks report closure.
- **Story (Phase 3)**: Depends on Docker-backed integration availability or exact blocker recording.
- **Polish & Verification (Phase 4)**: Depends on implementation evidence or blocker classification.

### Within The Story

- T010 must run before report closure tasks T018 when Docker is available; T012 is the prerequisite for blocker-only report updates when Docker is unavailable.
- T007-T009 are needed only when Docker-backed evidence identifies a unit-testable gap.
- T015-T016 are needed only when integration evidence identifies product or harness gaps.
- T018 must not change prior report verdicts without passing Docker-backed evidence.

### Parallel Opportunities

- T002 and T003 can run in parallel during setup.
- T007 and T008 can be authored in parallel if Docker-backed verification identifies independent managed-session and OAuth-terminal gaps.
- T015 and T016 can proceed independently only if failures are isolated to disjoint files.

## Implementation Strategy

1. Confirm active spec artifacts and Docker availability.
2. Run `./tools/test_integration.sh` in a Docker-enabled environment.
3. If Docker is unavailable, record the exact blocker and stop without claiming closure.
4. If Docker is available and integration fails, isolate failures with focused integration targets.
5. Add failing unit or integration tests for any concrete product/harness gaps.
6. Apply the smallest runtime or harness fix.
7. Rerun focused and full integration verification.
8. Update prior verification reports only with passing evidence or exact blockers.
9. Run final MoonSpec verification.

## Notes

- This task list covers exactly one story: MM-363 OAuth Terminal Docker Verification.
- Existing unit and fake-runner evidence cannot substitute for Docker-backed closure evidence.
- Preserve `MM-363` and the original Jira preset brief in implementation notes, verification output, commit text, and pull request metadata.
- Do not paste full Docker Compose output, credential listings, tokens, or environment dumps into reports.
