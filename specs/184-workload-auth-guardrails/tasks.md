# Tasks: Workload Auth-Volume Guardrails

**Input**: `specs/184-workload-auth-guardrails/spec.md`, `specs/184-workload-auth-guardrails/plan.md`, `specs/184-workload-auth-guardrails/research.md`, `specs/184-workload-auth-guardrails/data-model.md`, `specs/184-workload-auth-guardrails/contracts/workload-auth-guardrails.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py`
- Integration command: `./tools/test_integration.sh`; required coverage target: `tests/integration/services/temporal/workflows/test_agent_run.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Story: `STORY-006` from MM-318 breakdown
- Coverage: FR-001 through FR-005; DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-020
- Original preset brief: `MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md`

## Phase 1: Setup

- [X] T001 Confirm active story artifacts and MM-318 traceability in specs/184-workload-auth-guardrails/spec.md, specs/184-workload-auth-guardrails/plan.md, specs/184-workload-auth-guardrails/research.md, specs/184-workload-auth-guardrails/data-model.md, and specs/184-workload-auth-guardrails/contracts/workload-auth-guardrails.md (STORY-006)
- [X] T002 Confirm focused unit and integration commands from specs/184-workload-auth-guardrails/quickstart.md are runnable or have exact environment blockers recorded (STORY-006)

## Phase 2: Foundational

- [X] T003 Inspect existing production touchpoints named in specs/184-workload-auth-guardrails/plan.md and map current behavior before writing tests (FR-001 through FR-005; DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-020)
- [X] T004 Prepare or update shared test fixtures needed by this story in tests/unit/ and tests/integration/ without implementing production behavior (STORY-006)

## Phase 3: Workload Auth-Volume Guardrails

Story summary: Prevent Docker-backed workloads from implicitly inheriting managed-runtime auth volumes and managed-session identity.

Independent test: Launch workload profiles from a simulated managed-session-assisted step and assert auth-volume mounts are rejected unless explicitly declared by policy.

Traceability IDs: FR-001 through FR-005; DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-020

Unit test plan: write red-first unit tests for validation, serialization, redaction, and boundary payload behavior before production code.

Integration test plan: write red-first hermetic integration tests for the real API/workflow/runtime/container/UI boundary before production code when Docker/browser services are available.

- [X] T005 [P] Add failing unit test for mount allowlist and credential mount declaration validation for FR-001 FR-002 FR-003 SC-005 DESIGN-REQ-009 in tests/unit/workloads/test_workload_contract.py
- [X] T006 [P] Add failing unit test for secret redaction and workload identity separation for FR-004 FR-005 DESIGN-REQ-010 DESIGN-REQ-020 in tests/unit/workloads/test_docker_workload_launcher.py
- [X] T007 [P] Add failing integration test for workflow-to-workload launch boundary without implicit auth inheritance for SC-001 through SC-004 DESIGN-REQ-009 DESIGN-REQ-020 in tests/integration/services/temporal/workflows/test_agent_run.py
- [X] T008 Run red-first focused unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py` and record expected failures in specs/184-workload-auth-guardrails/tasks.md (STORY-006)
- [X] T009 Run red-first integration tests with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/services/temporal/workflows/test_agent_run.py`; otherwise record `/var/run/docker.sock` blocker in specs/184-workload-auth-guardrails/tasks.md (STORY-006)
- [X] T010 Implement workload profile mount allowlist and credential mount declaration validation for FR-001 FR-002 FR-003 in moonmind/schemas/workload_models.py
- [X] T011 Implement runtime launch rejection/redaction behavior for FR-002 FR-003 in moonmind/workloads/docker_launcher.py
- [X] T012 Implement workload identity separation and secret-free result metadata for FR-004 FR-005 in moonmind/workloads/docker_launcher.py
- [X] T013 Run focused unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py` and update task evidence in specs/184-workload-auth-guardrails/tasks.md
- [X] T014 Run integration verification with `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/services/temporal/workflows/test_agent_run.py`; update task evidence in specs/184-workload-auth-guardrails/tasks.md
- [X] T015 Validate the single-story acceptance scenarios and MM-318 traceability against specs/184-workload-auth-guardrails/spec.md and specs/184-workload-auth-guardrails/contracts/workload-auth-guardrails.md

## Final Phase: Polish And Verification

- [X] T016 Refactor only story-local code and tests after green validation in files named by specs/184-workload-auth-guardrails/plan.md
- [X] T017 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-006
- [X] T018 Run quickstart validation from specs/184-workload-auth-guardrails/quickstart.md and record any Docker integration blocker
- [X] T019 Run final `/moonspec-verify` equivalent for specs/184-workload-auth-guardrails/spec.md and write verification result in specs/184-workload-auth-guardrails/verification.md

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

## Implementation Evidence

- Red unit evidence: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py` failed before production changes on missing `credentialMounts` support and missing workload `identityKind` metadata.
- Red integration evidence: `pytest tests/integration/services/temporal/workflows/test_agent_run.py -k workload_auth_volume_guardrails -q --tb=short` failed before production changes on missing `credentialMounts` support.
- Focused unit green: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py` passed with 68 tests.
- Focused integration green: `pytest tests/integration/services/temporal/workflows/test_agent_run.py -m integration_ci -k workload_auth_volume_guardrails -q --tb=short` passed with 1 test.
- Full unit green: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3202 Python tests, 16 subtests, and 221 frontend tests.
- Required integration runner blocker: `./tools/test_integration.sh` could not connect to `/var/run/docker.sock`; Docker-backed compose verification was not available in this managed-agent environment.
