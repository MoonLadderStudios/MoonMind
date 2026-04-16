# Tasks: Codex Managed Session Volume Targeting

**Input**: `specs/180-codex-volume-targeting/spec.md`, `specs/180-codex-volume-targeting/plan.md`, `specs/180-codex-volume-targeting/research.md`, `specs/180-codex-volume-targeting/data-model.md`, `specs/180-codex-volume-targeting/contracts/codex-volume-targeting.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py`
- Integration command: `./tools/test_integration.sh`; required coverage target: `tests/integration/services/temporal/test_codex_session_task_creation.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Story: `STORY-002` from MM-356 Jira preset brief
- Coverage: FR-001 through FR-005; DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-020
- Original preset brief: `MM-356: Codex Managed Session Volume Targeting`

## Phase 1: Setup

- [X] T001 Confirm active story artifacts and MM-356 traceability in specs/180-codex-volume-targeting/spec.md, specs/180-codex-volume-targeting/plan.md, specs/180-codex-volume-targeting/research.md, specs/180-codex-volume-targeting/data-model.md, and specs/180-codex-volume-targeting/contracts/codex-volume-targeting.md (STORY-002)
- [X] T002 Confirm focused unit and integration commands from specs/180-codex-volume-targeting/quickstart.md are runnable or have exact environment blockers recorded (STORY-002)

## Phase 2: Foundational

- [X] T003 Inspect existing production touchpoints named in specs/180-codex-volume-targeting/plan.md and map current behavior before writing tests (FR-001 through FR-005; DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-020)
- [X] T004 Prepare or update shared test fixtures needed by this story in tests/unit/ and tests/integration/ without implementing production behavior (STORY-002)

## Phase 3: Codex Managed Session Volume Targeting

Story summary: Launch managed Codex sessions with workspace volume always mounted and auth volume only at an explicit separate target.

Independent test: Launch managed Codex sessions with and without MANAGED_AUTH_VOLUME_PATH and inspect Docker command plus validation failures.

Traceability IDs: FR-001 through FR-005; DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-020

Unit test plan: write red-first unit tests for validation, serialization, redaction, and boundary payload behavior before production code.

Integration test plan: write red-first hermetic integration tests for the real API/workflow/runtime/container/UI boundary before production code when Docker/browser services are available.

- [X] T005 [P] Add failing unit test for launch request path and auth target validation for FR-001 FR-003 DESIGN-REQ-006 in tests/unit/schemas/test_managed_session_models.py
- [X] T006 [P] Add failing unit test for profile-derived auth target and workspace Codex home payload for FR-001 FR-002 DESIGN-REQ-005 in tests/unit/workflows/adapters/test_codex_session_adapter.py
- [X] T007 [P] Add failing unit test for Docker mount command and reserved environment behavior for FR-002 FR-003 FR-004 SC-005 DESIGN-REQ-017 in tests/unit/services/temporal/runtime/test_managed_session_controller.py
- [X] T008 [P] Add failing integration test for compose-backed managed session launch boundary for SC-001 through SC-004 DESIGN-REQ-004 DESIGN-REQ-017 in tests/integration/services/temporal/test_codex_session_task_creation.py
- [X] T009 Run red-first focused unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py` and record expected failures in specs/180-codex-volume-targeting/tasks.md (STORY-002)
- [X] T010 Run red-first integration tests with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/services/temporal/test_codex_session_task_creation.py`; otherwise record `/var/run/docker.sock` blocker in specs/180-codex-volume-targeting/tasks.md (STORY-002)
- [X] T011 Implement launch request validation for FR-001 FR-003 in moonmind/schemas/managed_session_models.py
- [X] T012 Implement OAuth-backed profile launch payload shaping for FR-001 FR-002 in moonmind/workflows/adapters/codex_session_adapter.py
- [X] T013 Implement workspace/auth volume mounting and reserved environment propagation for FR-002 FR-003 FR-004 in moonmind/workflows/temporal/runtime/managed_session_controller.py
- [X] T014 Run focused unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py` and update task evidence in specs/180-codex-volume-targeting/tasks.md
- [X] T015 Run integration verification with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/services/temporal/test_codex_session_task_creation.py`; update task evidence in specs/180-codex-volume-targeting/tasks.md
- [X] T016 Validate the single-story acceptance scenarios and MM-356 traceability against specs/180-codex-volume-targeting/spec.md and specs/180-codex-volume-targeting/contracts/codex-volume-targeting.md

## Final Phase: Polish And Verification

- [X] T017 Refactor only story-local code and tests after green validation in files named by specs/180-codex-volume-targeting/plan.md
- [X] T018 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-002
- [X] T019 Run quickstart validation from specs/180-codex-volume-targeting/quickstart.md and record any Docker integration blocker
- [X] T020 Run final `/moonspec-verify` equivalent for specs/180-codex-volume-targeting/spec.md and write verification result in specs/180-codex-volume-targeting/verification.md

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
- Preserve `MM-356` and the preset brief in all verification evidence.
- Treat missing Docker socket as a blocker to record, not a passing integration result.

## Execution Evidence

- Alignment: updated spec, plan, research, data model, contract, quickstart, checklist, and tasks to make `MM-356: Codex Managed Session Volume Targeting` the canonical preserved Jira preset brief.
- Production: `moonmind/schemas/managed_session_models.py` now validates launch request paths as absolute POSIX paths and rejects `MANAGED_AUTH_VOLUME_PATH` equal to `codexHomePath`; existing adapter/controller boundaries provide profile-derived auth target shaping, workspace volume mounting, explicit auth volume mounting, and reserved `MOONMIND_SESSION_*` environment propagation.
- Unit tests: added launch request schema coverage in `tests/unit/schemas/test_managed_session_models.py`; existing adapter and controller tests cover profile-derived auth target, explicit auth mount target, absence of auth mount without an explicit target, and reserved environment rejection.
- Integration tests: added `tests/integration/services/temporal/test_codex_session_task_creation.py::test_codex_session_launch_command_uses_workspace_and_explicit_auth_target` to verify the launch-command boundary with a fake runner.
- Red-first note: new schema tests were authored for behavior absent from the schema boundary before `moonmind/schemas/managed_session_models.py` was updated; adapter and controller behavior already existed and was preserved.
- Focused unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py` passed with 115 Python tests and 224 frontend tests.
- Integration boundary verification: `python -m pytest tests/integration/services/temporal/test_codex_session_task_creation.py::test_codex_session_launch_command_uses_workspace_and_explicit_auth_target -q --tb=short` passed.
- Compose integration verification: `./tools/test_integration.sh` did not run because `/var/run/docker.sock` is unavailable in this managed-agent container.
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3419 Python tests, 16 subtests, and 224 frontend tests.
