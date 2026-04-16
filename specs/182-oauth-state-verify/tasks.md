# Tasks: OAuth Session State and Verification Boundaries

**Input**: `specs/182-oauth-state-verify/spec.md`, `specs/182-oauth-state-verify/plan.md`, `specs/182-oauth-state-verify/research.md`, `specs/182-oauth-state-verify/data-model.md`, `specs/182-oauth-state-verify/contracts/oauth-state-verify.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py`
- Integration command: `./tools/test_integration.sh # include tests/integration/temporal/test_oauth_session.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Story: `STORY-005` from MM-318 breakdown
- Coverage: FR-001 through FR-006; DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020
- Original preset brief: `MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md`

## Phase 1: Setup

- [ ] T001 Confirm active story artifacts and MM-318 traceability in specs/182-oauth-state-verify/spec.md, specs/182-oauth-state-verify/plan.md, specs/182-oauth-state-verify/research.md, specs/182-oauth-state-verify/data-model.md, and specs/182-oauth-state-verify/contracts/oauth-state-verify.md (STORY-005)
- [ ] T002 Confirm focused unit and integration commands from specs/182-oauth-state-verify/quickstart.md are runnable or have exact environment blockers recorded (STORY-005)

## Phase 2: Foundational

- [ ] T003 Inspect existing production touchpoints named in specs/182-oauth-state-verify/plan.md and map current behavior before writing tests (FR-001 through FR-006; DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020)
- [ ] T004 Prepare or update shared test fixtures needed by this story in tests/unit/ and tests/integration/ without implementing production behavior (STORY-005)

## Phase 3: OAuth Session State and Verification Boundaries

Story summary: Use transport-neutral OAuth statuses and secret-free verification results at profile and launch boundaries.

Independent test: Exercise OAuth success, cancel, expire, disabled-bridge, and verification failure paths with mocked volume verification.

Traceability IDs: FR-001 through FR-006; DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020

Unit test plan: write red-first unit tests for validation, serialization, redaction, and boundary payload behavior before production code.

Integration test plan: write red-first hermetic integration tests for the real API/workflow/runtime/container/UI boundary before production code when Docker/browser services are available.

- [ ] T005 [P] Add failing unit test for session_transport none and provider registry defaults for FR-001 FR-002 DESIGN-REQ-015 in tests/unit/auth/test_oauth_provider_registry.py
- [ ] T006 [P] Add failing unit test for secret-free volume verification status/failure metadata for FR-003 FR-005 DESIGN-REQ-010 DESIGN-REQ-018 in tests/unit/auth/test_volume_verifiers.py
- [ ] T007 [P] Add failing unit test for profile registration blocking on verification failure and launch materialization checks for FR-003 FR-004 DESIGN-REQ-016 in tests/unit/auth/test_oauth_session_activities.py
- [ ] T008 [P] Add failing integration test for Temporal OAuth workflow status transitions and secret-free verification outputs for SC-001 through SC-005 DESIGN-REQ-015 DESIGN-REQ-018 in tests/integration/temporal/test_oauth_session.py
- [ ] T009 Run red-first focused unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py` and record expected failures in specs/182-oauth-state-verify/tasks.md (STORY-005)
- [ ] T010 Run red-first integration tests with `./tools/test_integration.sh # include tests/integration/temporal/test_oauth_session.py` when Docker is available, or record `/var/run/docker.sock` blocker in specs/182-oauth-state-verify/tasks.md (STORY-005)
- [ ] T011 Implement transport-neutral provider session transport defaults for FR-001 FR-002 in moonmind/workflows/temporal/runtime/providers/registry.py
- [ ] T012 Implement compact secret-free verification results for FR-003 FR-005 in moonmind/workflows/temporal/runtime/providers/volume_verifiers.py
- [ ] T013 Implement verification-gated profile registration and materialization checks for FR-003 FR-004 in moonmind/workflows/temporal/activities/oauth_session_activities.py
- [ ] T014 Implement status transitions and disabled-bridge behavior for FR-001 FR-002 in moonmind/workflows/temporal/workflows/oauth_session.py
- [ ] T015 Run focused unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py` and update task evidence in specs/182-oauth-state-verify/tasks.md
- [ ] T016 Run integration verification with `./tools/test_integration.sh # include tests/integration/temporal/test_oauth_session.py` when Docker is available and update task evidence in specs/182-oauth-state-verify/tasks.md
- [ ] T017 Validate the single-story acceptance scenarios and MM-318 traceability against specs/182-oauth-state-verify/spec.md and specs/182-oauth-state-verify/contracts/oauth-state-verify.md

## Final Phase: Polish And Verification

- [ ] T018 Refactor only story-local code and tests after green validation in files named by specs/182-oauth-state-verify/plan.md
- [ ] T019 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-005
- [ ] T020 Run quickstart validation from specs/182-oauth-state-verify/quickstart.md and record any Docker integration blocker
- [ ] T021 Run final `/moonspec-verify` equivalent for specs/182-oauth-state-verify/spec.md and write verification result in specs/182-oauth-state-verify/verification.md

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
