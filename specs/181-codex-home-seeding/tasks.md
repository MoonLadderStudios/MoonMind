# Tasks: Per-Run Codex Home Seeding

**Input**: `specs/181-codex-home-seeding/spec.md`, `specs/181-codex-home-seeding/plan.md`, `specs/181-codex-home-seeding/research.md`, `specs/181-codex-home-seeding/data-model.md`, `specs/181-codex-home-seeding/contracts/codex-home-seeding.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py`
- Integration command: `./tools/test_integration.sh # include tests/integration/services/temporal/test_codex_session_runtime.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Story: `STORY-003` from MM-318 breakdown
- Coverage: FR-001 through FR-005; DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-019, DESIGN-REQ-020
- Original preset brief: `MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md`

## Phase 1: Setup

- [ ] T001 Confirm active story artifacts and MM-318 traceability in specs/181-codex-home-seeding/spec.md, specs/181-codex-home-seeding/plan.md, specs/181-codex-home-seeding/research.md, specs/181-codex-home-seeding/data-model.md, and specs/181-codex-home-seeding/contracts/codex-home-seeding.md (STORY-003)
- [ ] T002 Confirm focused unit and integration commands from specs/181-codex-home-seeding/quickstart.md are runnable or have exact environment blockers recorded (STORY-003)

## Phase 2: Foundational

- [ ] T003 Inspect existing production touchpoints named in specs/181-codex-home-seeding/plan.md and map current behavior before writing tests (FR-001 through FR-005; DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-019, DESIGN-REQ-020)
- [ ] T004 Prepare or update shared test fixtures needed by this story in tests/unit/ and tests/integration/ without implementing production behavior (STORY-003)

## Phase 3: Per-Run Codex Home Seeding

Story summary: Seed eligible durable auth entries into a per-run Codex home and start Codex App Server from that home.

Independent test: Run the session runtime with fake auth-volume contents and assert eligible files copy, excluded files do not, and CODEX_HOME is per-run.

Traceability IDs: FR-001 through FR-005; DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-019, DESIGN-REQ-020

Unit test plan: write red-first unit tests for validation, serialization, redaction, and boundary payload behavior before production code.

Integration test plan: write red-first hermetic integration tests for the real API/workflow/runtime/container/UI boundary before production code when Docker/browser services are available.

- [ ] T005 [P] Add failing unit test for per-run Codex home creation and missing auth source failures for FR-001 FR-002 DESIGN-REQ-005 DESIGN-REQ-007 in tests/unit/services/temporal/runtime/test_codex_session_runtime.py
- [ ] T006 [P] Add failing unit test for auth seeding eligibility/exclusions and config preservation for FR-002 FR-004 DESIGN-REQ-010 DESIGN-REQ-019 in tests/unit/services/temporal/runtime/test_codex_session_runtime.py
- [ ] T007 [P] Add failing unit test for runtime evidence surface guardrails for FR-004 DESIGN-REQ-019 in tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py
- [ ] T008 [P] Add failing integration test for in-container runtime launch and CODEX_HOME behavior for SC-001 through SC-004 DESIGN-REQ-008 in tests/integration/services/temporal/test_codex_session_runtime.py
- [ ] T009 Run red-first focused unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` and record expected failures in specs/181-codex-home-seeding/tasks.md (STORY-003)
- [ ] T010 Run red-first integration tests with `./tools/test_integration.sh # include tests/integration/services/temporal/test_codex_session_runtime.py` when Docker is available, or record `/var/run/docker.sock` blocker in specs/181-codex-home-seeding/tasks.md (STORY-003)
- [ ] T011 Implement per-run home creation, auth seeding, error handling, and CODEX_HOME startup for FR-001 FR-002 FR-003 in moonmind/workflows/temporal/runtime/codex_session_runtime.py
- [ ] T012 Implement operator evidence handling that avoids runtime homes/auth volumes for FR-004 in moonmind/workflows/temporal/runtime/strategies/codex_cli.py
- [ ] T013 Run focused unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` and update task evidence in specs/181-codex-home-seeding/tasks.md
- [ ] T014 Run integration verification with `./tools/test_integration.sh # include tests/integration/services/temporal/test_codex_session_runtime.py` when Docker is available and update task evidence in specs/181-codex-home-seeding/tasks.md
- [ ] T015 Validate the single-story acceptance scenarios and MM-318 traceability against specs/181-codex-home-seeding/spec.md and specs/181-codex-home-seeding/contracts/codex-home-seeding.md

## Final Phase: Polish And Verification

- [ ] T016 Refactor only story-local code and tests after green validation in files named by specs/181-codex-home-seeding/plan.md
- [ ] T017 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-003
- [ ] T018 Run quickstart validation from specs/181-codex-home-seeding/quickstart.md and record any Docker integration blocker
- [ ] T019 Run final `/moonspec-verify` equivalent for specs/181-codex-home-seeding/spec.md and write verification result in specs/181-codex-home-seeding/verification.md

## Dependencies And Order

- Complete Phase 1 and Phase 2 before writing red-first story tests.
- Write and confirm unit and integration tests fail before implementation tasks.
- Complete implementation tasks before green validation and final verification.
- Keep this task list scoped to exactly one story; do not implement other MM-318 generated specs here.

## Parallel Examples

- Unit test tasks marked `[P]` may run in parallel when they touch different files.
- Integration test tasks marked `[P]` may run in parallel with unit test authoring, but red-first confirmation must wait for both groups.

## Implementation Strategy

- Follow TDD: red unit tests, red integration tests, production implementation, focused green tests, full unit suite, final `/moonspec-verify`.
- Preserve `MM-318` and the preset brief in all verification evidence.
- Treat missing Docker socket as a blocker to record, not a passing integration result.
