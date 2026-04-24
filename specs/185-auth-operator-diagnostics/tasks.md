# Tasks: Auth Operator Diagnostics

**Input**: `specs/185-auth-operator-diagnostics/spec.md`, `specs/185-auth-operator-diagnostics/plan.md`, `specs/185-auth-operator-diagnostics/research.md`, `specs/185-auth-operator-diagnostics/data-model.md`, `specs/185-auth-operator-diagnostics/contracts/auth-operator-diagnostics.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- Integration command: `./tools/test_integration.sh` when Docker is available
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Jira issue: `MM-336`
- Story: `STORY-005` from MM-318 breakdown
- Coverage: FR-001 through FR-007; SC-001 through SC-005; DESIGN-REQ-004, DESIGN-REQ-016, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022
- Original preset brief: `spec.md` (Input)

## Phase 1: Setup

- [X] T001 Confirm active story artifacts and MM-336 traceability in specs/185-auth-operator-diagnostics/spec.md, specs/185-auth-operator-diagnostics/plan.md, specs/185-auth-operator-diagnostics/research.md, specs/185-auth-operator-diagnostics/data-model.md, and specs/185-auth-operator-diagnostics/contracts/auth-operator-diagnostics.md (STORY-005)
- [X] T002 Confirm focused unit and integration commands from specs/185-auth-operator-diagnostics/quickstart.md are runnable or have exact environment blockers recorded (STORY-005)

## Phase 2: Foundational

- [X] T003 Inspect existing OAuth session, provider profile, managed session activity, and controller projections before writing tests in api_service/api/routers/oauth_sessions.py, api_service/api/schemas_oauth_sessions.py, moonmind/workflows/temporal/activity_runtime.py, and moonmind/workflows/temporal/runtime/managed_session_controller.py (FR-001 through FR-007)
- [X] T004 Prepare shared test fixtures for sanitized provider profile summaries and auth diagnostics in tests/unit/api_service/api/routers/test_oauth_sessions.py and tests/unit/workflows/temporal/test_agent_runtime_activities.py (SC-001 through SC-003)

## Phase 3: View Safe Auth Diagnostics

Story summary: Project OAuth enrollment, Provider Profile, managed Codex auth materialization, and ordinary task execution diagnostics through safe operator-visible metadata.

Independent test: Simulate successful and failed OAuth enrollment plus successful and failed managed Codex session launch; assert safe status, profile summary, readiness, validation failure, diagnostics refs, and artifact/log pointers are exposed while raw credentials, auth-volume listings, runtime-home contents, and terminal scrollback are omitted.

Traceability IDs: FR-001 through FR-007; SC-001 through SC-005; DESIGN-REQ-004, DESIGN-REQ-016, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022

Unit test plan: write red-first tests for OAuth profile summary projection and managed session auth diagnostics metadata before production code.

Integration test plan: use the real activity/controller boundary with mocked Docker/controller dependencies for deterministic hermetic coverage; run Docker-backed integration runner if available.

- [X] T005 [P] Add failing OAuth session response test for sanitized profile summary and redacted failure fields covering FR-001 FR-002 SC-001 DESIGN-REQ-004 in tests/unit/api_service/api/routers/test_oauth_sessions.py
- [X] T006 [P] Add failing managed session launch activity test for authDiagnostics success metadata covering FR-003 FR-004 SC-002 DESIGN-REQ-016 DESIGN-REQ-021 in tests/unit/workflows/temporal/test_agent_runtime_activities.py
- [X] T007 [P] Add failing managed session launch activity test for sanitized validation failure diagnostics covering FR-005 SC-003 DESIGN-REQ-016 DESIGN-REQ-021 in tests/unit/workflows/temporal/test_agent_runtime_activities.py
- [X] T008 [P] Add managed session controller test asserting durable record/artifact refs remain the execution evidence and auth/runtime homes are not published as artifacts covering FR-006 FR-007 SC-004 DESIGN-REQ-020 DESIGN-REQ-022 in tests/unit/services/temporal/runtime/test_managed_session_controller.py
- [X] T009 Run red-first focused tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py` and record expected failures in specs/185-auth-operator-diagnostics/tasks.md (STORY-005)
- [X] T010 Implement ProviderProfileSummary schema and OAuth session profile summary projection for FR-001 FR-002 in api_service/api/schemas_oauth_sessions.py and api_service/api/routers/oauth_sessions.py
- [X] T011 Implement authDiagnostics metadata construction for managed Codex launch success for FR-003 FR-004 in moonmind/workflows/temporal/activity_runtime.py
- [X] T012 Implement sanitized auth diagnostics failure classification for managed Codex launch failures for FR-005 in moonmind/workflows/temporal/activity_runtime.py
- [X] T013 Confirm managed-session records/artifact publication continue to expose only logs, summaries, diagnostics, reset/control-boundary refs, and never auth homes as artifacts for FR-006 FR-007 in moonmind/workflows/temporal/runtime/managed_session_controller.py
- [X] T014 Run focused tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py` and update task evidence in specs/185-auth-operator-diagnostics/tasks.md
- [X] T015 Run integration verification with `./tools/test_integration.sh` when Docker is available; otherwise record `/var/run/docker.sock` blocker in specs/185-auth-operator-diagnostics/tasks.md
- [X] T016 Validate MM-336 traceability and source design coverage against specs/185-auth-operator-diagnostics/spec.md and specs/185-auth-operator-diagnostics/contracts/auth-operator-diagnostics.md

## Final Phase: Polish And Verification

- [X] T017 Refactor only story-local code and tests after green validation in files named by specs/185-auth-operator-diagnostics/plan.md
- [X] T018 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification for STORY-005
- [X] T019 Run quickstart validation from specs/185-auth-operator-diagnostics/quickstart.md and record any Docker integration blocker
- [X] T020 Run final `/moonspec-verify` equivalent for specs/185-auth-operator-diagnostics/spec.md and write verification result in specs/185-auth-operator-diagnostics/verification.md

## Dependencies And Order

- Complete Phase 1 and Phase 2 before red-first story tests.
- Write and confirm unit/boundary tests fail before implementation tasks.
- Complete implementation tasks before green validation and final verification.
- Keep this task list scoped to MM-336 STORY-005.

## Parallel Examples

- T005, T006, T007, and T008 may be authored in parallel because they touch different test concerns.
- T010 can proceed after T005 red confirmation; T011 and T012 can proceed after T006 and T007 red confirmation.

## Implementation Strategy

- Follow TDD: red unit/boundary tests, production implementation, focused green tests, full unit suite, final `/moonspec-verify`.
- Preserve `MM-336` and the preset brief in all verification evidence.
- Treat missing Docker socket as a blocker to record, not a passing integration result.

## Implementation Evidence

- Red unit evidence: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py` failed before production changes on missing `profile_summary`, `volumeRef`/`volumeMountPath` profile fields, and missing managed-session `authDiagnostics`.
- Focused unit green: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py` passed with 123 tests.
- Full unit green: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3247 Python tests, 16 subtests, and 222 frontend tests.
- Required integration runner blocker: `/var/run/docker.sock` is not present in this managed-agent environment, so `./tools/test_integration.sh` cannot run here.
