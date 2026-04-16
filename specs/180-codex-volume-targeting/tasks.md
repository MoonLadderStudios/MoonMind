# Tasks: Codex Managed Session Volume Targeting

**Input**: `specs/180-codex-volume-targeting/spec.md`, `specs/180-codex-volume-targeting/plan.md`, `specs/180-codex-volume-targeting/research.md`, `specs/180-codex-volume-targeting/data-model.md`, `specs/180-codex-volume-targeting/contracts/codex-volume-targeting.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py`
- Integration command: `./tools/test_integration.sh # include tests/integration/services/temporal/test_codex_session_task_creation.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Story: `STORY-002` from MM-318 breakdown
- Coverage: FR-001 through FR-005; DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-020
- Original preset brief: `MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md`

## Phase 1: Setup

- [ ] T001 Confirm active story artifacts and MM-318 traceability in specs/180-codex-volume-targeting/spec.md, specs/180-codex-volume-targeting/plan.md, specs/180-codex-volume-targeting/research.md, specs/180-codex-volume-targeting/data-model.md, and specs/180-codex-volume-targeting/contracts/codex-volume-targeting.md (STORY-002)
- [ ] T002 Confirm focused unit and integration commands from specs/180-codex-volume-targeting/quickstart.md are runnable or have exact environment blockers recorded (STORY-002)

## Phase 2: Foundational

- [ ] T003 Inspect existing production touchpoints named in specs/180-codex-volume-targeting/plan.md and map current behavior before writing tests (FR-001 through FR-005; DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-020)
- [ ] T004 Prepare or update shared test fixtures needed by this story in tests/unit/ and tests/integration/ without implementing production behavior (STORY-002)

## Phase 3: Codex Managed Session Volume Targeting

Story summary: Launch managed Codex sessions with workspace volume always mounted and auth volume only at an explicit separate target.

Independent test: Launch managed Codex sessions with and without MANAGED_AUTH_VOLUME_PATH and inspect Docker command plus validation failures.

Traceability IDs: FR-001 through FR-005; DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-020

Unit test plan: write red-first unit tests for validation, serialization, redaction, and boundary payload behavior before production code.

Integration test plan: write red-first hermetic integration tests for the real API/workflow/runtime/container/UI boundary before production code when Docker/browser services are available.

- [ ] T005 [P] Add failing unit test for launch request path and auth target validation for FR-001 FR-003 DESIGN-REQ-006 in tests/unit/schemas/test_managed_session_models.py
- [ ] T006 [P] Add failing unit test for profile-derived auth target and workspace Codex home payload for FR-001 FR-002 DESIGN-REQ-005 in tests/unit/workflows/adapters/test_codex_session_adapter.py
- [ ] T007 [P] Add failing unit test for Docker mount command and reserved environment behavior for FR-002 FR-003 FR-004 DESIGN-REQ-017 in tests/unit/services/temporal/runtime/test_managed_session_controller.py
- [ ] T008 [P] Add failing integration test for compose-backed managed session launch boundary for SC-001 through SC-004 DESIGN-REQ-004 DESIGN-REQ-017 in tests/integration/services/temporal/test_codex_session_task_creation.py
- [ ] T009 Run red-first focused unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py` and record expected failures in specs/180-codex-volume-targeting/tasks.md (STORY-002)
- [ ] T010 Run red-first integration tests with `./tools/test_integration.sh # include tests/integration/services/temporal/test_codex_session_task_creation.py` when Docker is available, or record `/var/run/docker.sock` blocker in specs/180-codex-volume-targeting/tasks.md (STORY-002)
- [ ] T011 Implement launch request validation for FR-001 FR-003 in moonmind/schemas/managed_session_models.py
- [ ] T012 Implement OAuth-backed profile launch payload shaping for FR-001 FR-002 in moonmind/workflows/adapters/codex_session_adapter.py
- [ ] T013 Implement workspace/auth volume mounting and reserved environment propagation for FR-002 FR-003 FR-004 in moonmind/workflows/temporal/runtime/managed_session_controller.py
- [ ] T014 Run focused unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py` and update task evidence in specs/180-codex-volume-targeting/tasks.md
- [ ] T015 Run integration verification with `./tools/test_integration.sh # include tests/integration/services/temporal/test_codex_session_task_creation.py` when Docker is available and update task evidence in specs/180-codex-volume-targeting/tasks.md
- [ ] T016 Validate the single-story acceptance scenarios and MM-318 traceability against specs/180-codex-volume-targeting/spec.md and specs/180-codex-volume-targeting/contracts/codex-volume-targeting.md

## Final Phase: Polish And Verification

- [ ] T017 Refactor only story-local code and tests after green validation in files named by specs/180-codex-volume-targeting/plan.md
- [ ] T018 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-002
- [ ] T019 Run quickstart validation from specs/180-codex-volume-targeting/quickstart.md and record any Docker integration blocker
- [ ] T020 Run final `/moonspec-verify` equivalent for specs/180-codex-volume-targeting/spec.md and write verification result in specs/180-codex-volume-targeting/verification.md

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
