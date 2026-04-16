# Tasks: Codex Auth Volume Profile Contract

**Input**: `specs/179-codex-auth-profile/spec.md`, `specs/179-codex-auth-profile/plan.md`, `specs/179-codex-auth-profile/research.md`, `specs/179-codex-auth-profile/data-model.md`, `specs/179-codex-auth-profile/contracts/codex-auth-profile.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py`
- Integration command: `./tools/test_integration.sh`; required coverage target: `tests/integration/temporal/test_oauth_session.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Story: `STORY-001` from MM-318 breakdown
- Coverage: FR-001 through FR-006; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020
- Original preset brief: `MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md`

## Phase 1: Setup

- [ ] T001 Confirm active story artifacts and MM-318 traceability in specs/179-codex-auth-profile/spec.md, specs/179-codex-auth-profile/plan.md, specs/179-codex-auth-profile/research.md, specs/179-codex-auth-profile/data-model.md, and specs/179-codex-auth-profile/contracts/codex-auth-profile.md (STORY-001)
- [ ] T002 Confirm focused unit and integration commands from specs/179-codex-auth-profile/quickstart.md are runnable or have exact environment blockers recorded (STORY-001)

## Phase 2: Foundational

- [ ] T003 Inspect existing production touchpoints named in specs/179-codex-auth-profile/plan.md and map current behavior before writing tests (FR-001 through FR-006; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020)
- [ ] T004 Prepare or update shared test fixtures needed by this story in tests/unit/ and tests/integration/ without implementing production behavior (STORY-001)

## Phase 3: Codex Auth Volume Profile Contract

Story summary: Register or update Codex OAuth Provider Profiles using durable auth-volume refs without leaking credential contents.

Independent test: Create or update a Codex OAuth profile from verified OAuth session data, then assert stored/API/workflow profile views contain refs and policy metadata only.

Traceability IDs: FR-001 through FR-006; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020

Unit test plan: write red-first unit tests for validation, serialization, redaction, and boundary payload behavior before production code.

Integration test plan: write red-first hermetic integration tests for the real API/workflow/runtime/container/UI boundary before production code when Docker/browser services are available.

- [ ] T005 [P] Add failing unit test for Codex OAuth profile shape validation for FR-001 FR-002 FR-004 SC-003 DESIGN-REQ-003 in tests/unit/schemas/test_agent_runtime_models.py
- [ ] T006 [P] Add failing unit test for secret-free Provider Profile registration/update for FR-002 FR-003 DESIGN-REQ-010 DESIGN-REQ-016 in tests/unit/auth/test_oauth_session_activities.py
- [ ] T007 [P] Add failing unit test for serialized profile response redaction and non-Codex scope guardrail for FR-003 FR-005 SC-004 DESIGN-REQ-002 in tests/unit/api_service/api/routers/test_provider_profiles.py
- [ ] T008 [P] Add failing integration test for OAuth verification to Provider Profile registration boundary for SC-001 SC-002 DESIGN-REQ-001 DESIGN-REQ-016 in tests/integration/temporal/test_oauth_session.py
- [ ] T009 Run red-first focused unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py` and record expected failures in specs/179-codex-auth-profile/tasks.md (STORY-001)
- [ ] T010 Run red-first integration tests with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`; otherwise record `/var/run/docker.sock` blocker in specs/179-codex-auth-profile/tasks.md (STORY-001)
- [ ] T011 Implement Codex OAuth profile validation fields for FR-001 FR-004 in moonmind/schemas/agent_runtime_models.py
- [ ] T012 Implement Provider Profile registration/update metadata handling for FR-002 FR-003 in moonmind/workflows/temporal/activities/oauth_session_activities.py
- [ ] T013 Implement secret-free profile serialization and non-Codex scope behavior for FR-003 FR-005 in api_service/api/routers/provider_profiles.py
- [ ] T014 Run focused unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py` and update task evidence in specs/179-codex-auth-profile/tasks.md
- [ ] T015 Run integration verification with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`; update task evidence in specs/179-codex-auth-profile/tasks.md
- [ ] T016 Validate the single-story acceptance scenarios and MM-318 traceability against specs/179-codex-auth-profile/spec.md and specs/179-codex-auth-profile/contracts/codex-auth-profile.md

## Final Phase: Polish And Verification

- [ ] T017 Refactor only story-local code and tests after green validation in files named by specs/179-codex-auth-profile/plan.md
- [ ] T018 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-001
- [ ] T019 Run quickstart validation from specs/179-codex-auth-profile/quickstart.md and record any Docker integration blocker
- [ ] T020 Run final `/moonspec-verify` equivalent for specs/179-codex-auth-profile/spec.md and write verification result in specs/179-codex-auth-profile/verification.md

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
