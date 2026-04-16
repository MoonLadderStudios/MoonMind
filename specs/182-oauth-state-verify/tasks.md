# Tasks: OAuth Session State and Verification Boundaries

**Input**: `specs/182-oauth-state-verify/spec.md`, `specs/182-oauth-state-verify/plan.md`, `specs/182-oauth-state-verify/research.md`, `specs/182-oauth-state-verify/data-model.md`, `specs/182-oauth-state-verify/contracts/oauth-state-verify.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py`
- Integration command: `./tools/test_integration.sh`; required coverage target: `tests/integration/temporal/test_oauth_session.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Story: `STORY-005` from MM-359 Jira preset brief
- Coverage: FR-001 through FR-006; DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020
- Original preset brief: `MM-359: OAuth Session State and Verification Boundaries`

## Phase 1: Setup

- [X] T001 Confirm active story artifacts and MM-359 traceability in specs/182-oauth-state-verify/spec.md, specs/182-oauth-state-verify/plan.md, specs/182-oauth-state-verify/research.md, specs/182-oauth-state-verify/data-model.md, and specs/182-oauth-state-verify/contracts/oauth-state-verify.md (STORY-005)
- [X] T002 Confirm focused unit and integration commands from specs/182-oauth-state-verify/quickstart.md are runnable or have exact environment blockers recorded (STORY-005)

## Phase 2: Foundational

- [X] T003 Inspect existing production touchpoints named in specs/182-oauth-state-verify/plan.md and map current behavior before writing tests (FR-001 through FR-006; DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020)
- [X] T004 Prepare or update shared test fixtures needed by this story in tests/unit/ and tests/integration/ without implementing production behavior (STORY-005)

## Phase 3: OAuth Session State and Verification Boundaries

Story summary: Use transport-neutral OAuth statuses and secret-free verification results at profile and launch boundaries.

Independent test: Exercise OAuth success, cancel, expire, disabled-bridge, and verification failure paths with mocked volume verification.

Traceability IDs: FR-001 through FR-006; DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020

Unit test plan: write red-first unit tests for validation, serialization, redaction, and boundary payload behavior before production code.

Integration test plan: write red-first hermetic integration tests for the real API/workflow/runtime/container/UI boundary before production code when Docker/browser services are available.

- [X] T005 [P] Add failing unit test for session_transport none and provider registry defaults for FR-001 FR-002 DESIGN-REQ-015 in tests/unit/auth/test_oauth_provider_registry.py
- [X] T006 [P] Add failing unit test for secret-free volume verification status/failure metadata for FR-003 FR-005 DESIGN-REQ-010 DESIGN-REQ-018 in tests/unit/auth/test_volume_verifiers.py
- [X] T007 [P] Add failing unit test for profile registration blocking on verification failure and launch materialization checks for FR-003 FR-004 DESIGN-REQ-016 in tests/unit/auth/test_oauth_session_activities.py
- [X] T008 [P] Add failing integration test for Temporal OAuth workflow status transitions and secret-free verification outputs for SC-001 through SC-005 DESIGN-REQ-015 DESIGN-REQ-018 in tests/integration/temporal/test_oauth_session.py
- [X] T009 Run red-first focused unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py` and record expected failures in specs/182-oauth-state-verify/tasks.md (STORY-005)
- [X] T010 Run red-first integration tests with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`; otherwise record `/var/run/docker.sock` blocker in specs/182-oauth-state-verify/tasks.md (STORY-005)
- [X] T011 Implement transport-neutral provider session transport defaults for FR-001 FR-002 in moonmind/workflows/temporal/runtime/providers/registry.py
- [X] T012 Implement compact secret-free verification results for FR-003 FR-005 in moonmind/workflows/temporal/runtime/providers/volume_verifiers.py
- [X] T013 Implement verification-gated profile registration and materialization checks for FR-003 FR-004 in moonmind/workflows/temporal/activities/oauth_session_activities.py
- [X] T014 Implement status transitions and disabled-bridge behavior for FR-001 FR-002 in moonmind/workflows/temporal/workflows/oauth_session.py
- [X] T015 Run focused unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py` and update task evidence in specs/182-oauth-state-verify/tasks.md
- [X] T016 Run integration verification with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`; update task evidence in specs/182-oauth-state-verify/tasks.md
- [X] T017 Validate the single-story acceptance scenarios and MM-359 traceability against specs/182-oauth-state-verify/spec.md and specs/182-oauth-state-verify/contracts/oauth-state-verify.md

## Final Phase: Polish And Verification

- [X] T018 Refactor only story-local code and tests after green validation in files named by specs/182-oauth-state-verify/plan.md
- [X] T019 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-005
- [X] T020 Run quickstart validation from specs/182-oauth-state-verify/quickstart.md and record any Docker integration blocker
- [X] T021 Run final `/moonspec-verify` equivalent for specs/182-oauth-state-verify/spec.md and write verification result in specs/182-oauth-state-verify/verification.md

## Evidence

- Red-first focused unit run: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py` failed as expected before production edits with 5 failures covering missing `session_transport` default exposure, non-compact verification output, and missing registration block on failed verification metadata.
- Focused unit run after implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py` passed: 34 Python tests and 225 frontend tests.
- Related API/adapter unit run: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workflows/adapters/test_managed_agent_adapter.py` passed: 54 Python tests and 225 frontend tests.
- Local Temporal OAuth integration: `pytest tests/integration/temporal/test_oauth_session.py -q --tb=short` passed: 6 tests.
- Compose-backed integration runner: `./tools/test_integration.sh` blocked because `/var/run/docker.sock` is unavailable in this managed container.
- Full unit run: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed: 3440 Python tests, 16 subtests, and 225 frontend tests.
- Final MoonSpec verification: `specs/182-oauth-state-verify/verification.md` verdict `FULLY_IMPLEMENTED`.

## Dependencies And Order

- Complete Phase 1 and Phase 2 before writing red-first story tests.
- Write and confirm unit and integration tests fail before implementation tasks.
- Complete implementation tasks before green validation and final verification.
- Keep this task list scoped to exactly one story; do not implement other OAuthTerminal generated specs here.

## Parallel Examples

- Unit test tasks marked `[P]` may run in parallel when they touch different files.
- Integration test tasks marked `[P]` may run in parallel with unit test authoring, but red-first confirmation must wait for both groups.

## Implementation Strategy

- Follow TDD: red unit tests, red integration tests, production implementation, focused green tests, full unit suite, final `/moonspec-verify`.
- Preserve `MM-359` and the preset brief in all verification evidence.
- Treat missing Docker socket as a blocker to record, not a passing integration result.
