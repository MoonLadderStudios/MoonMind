# Tasks: Codex Auth Volume Profile Contract

**Input**: `specs/189-codex-auth-profile/spec.md`, `specs/189-codex-auth-profile/plan.md`, `specs/189-codex-auth-profile/research.md`, `specs/189-codex-auth-profile/data-model.md`, `specs/189-codex-auth-profile/contracts/codex-auth-profile.md`, `specs/189-codex-auth-profile/quickstart.md`

## Prerequisites

- Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py`
- Integration command: `./tools/test_integration.sh`; required coverage target: `tests/integration/temporal/test_oauth_session.py`
- Full unit command before verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

- Jira issue: `MM-355`
- Story: `STORY-001` Codex Auth Volume Profile Contract
- Coverage: FR-001 through FR-010; SC-001 through SC-005; acceptance scenarios 1-5; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020
- Original preset brief: preserved in `specs/189-codex-auth-profile/spec.md`
- Contract: `specs/189-codex-auth-profile/contracts/codex-auth-profile.md`

## Phase 1: Setup

- [ ] T001 Confirm active story artifacts and `MM-355` traceability in `specs/189-codex-auth-profile/spec.md`, `specs/189-codex-auth-profile/plan.md`, `specs/189-codex-auth-profile/research.md`, `specs/189-codex-auth-profile/data-model.md`, `specs/189-codex-auth-profile/contracts/codex-auth-profile.md`, and `specs/189-codex-auth-profile/quickstart.md` (FR-010, SC-005)
- [ ] T002 Confirm focused unit and integration commands from `specs/189-codex-auth-profile/quickstart.md` are runnable or have exact environment blockers recorded in `specs/189-codex-auth-profile/tasks.md` (SC-001 through SC-005)

## Phase 2: Foundational

- [ ] T003 Inspect existing profile, OAuth, schema, and workflow touchpoints named in `specs/189-codex-auth-profile/plan.md` and map current behavior before writing tests in `specs/189-codex-auth-profile/tasks.md` (FR-001 through FR-009, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020)
- [ ] T004 Prepare or update shared test fixtures for Codex OAuth Provider Profiles, OAuth verification evidence, slot policy metadata, and secret-like nested values in `tests/unit/schemas/test_agent_runtime_models.py`, `tests/unit/auth/test_oauth_session_activities.py`, `tests/unit/api/routers/test_oauth_sessions.py`, `tests/unit/api_service/api/routers/test_provider_profiles.py`, and `tests/integration/temporal/test_oauth_session.py` without implementing production behavior (FR-001 through FR-009)

## Phase 3: Codex Auth Volume Profile Contract

Story summary: Register or update Codex OAuth Provider Profiles using durable auth-volume refs and slot policy metadata without leaking credential contents or implying non-Codex managed-session parity.

Independent test: Create or update a Codex OAuth profile from verified OAuth session data, then confirm stored profile data, operator-visible profile data, and workflow-visible profile snapshots include only runtime identity, provider identity, auth-volume refs, materialization mode, mount path, and slot policy metadata while excluding raw credential contents, token values, auth file payloads, raw auth-volume listings, environment dumps, and unrelated runtime-home state.

Traceability IDs: FR-001 through FR-010; SC-001 through SC-005; acceptance scenarios 1-5; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020.

Unit test plan: write red-first unit tests for Codex OAuth profile shape validation, blank/unsafe ref rejection, slot policy preservation, OAuth finalization profile registration/update, provider profile response redaction, workflow/profile snapshot redaction, non-Codex scope guardrails, and `MM-355` traceability.

Integration test plan: write red-first hermetic integration tests for the real OAuth verification to Provider Profile registration boundary and the worker-bound workflow/activity payload shape when Docker is available.

- [ ] T005 [P] Add failing unit tests for Codex OAuth Provider Profile shape validation, including `runtime_id`, `provider_id`, `credential_source`, `runtime_materialization_mode`, `volume_ref`, and `volume_mount_path`, in `tests/unit/schemas/test_agent_runtime_models.py` (FR-001, FR-002, FR-004, SC-001, SC-003, DESIGN-REQ-003, DESIGN-REQ-016)
- [ ] T006 [P] Add failing unit tests for missing, blank, whitespace-only, and unsafe auth-volume refs and mount paths in `tests/unit/schemas/test_agent_runtime_models.py` (FR-004, SC-003, acceptance scenario 3, DESIGN-REQ-003)
- [ ] T007 [P] Add failing unit tests for secret-free Provider Profile registration/update from verified OAuth evidence, including `volume_ref`, `volume_mount_path`, provider identity, materialization mode, and slot policy preservation, in `tests/unit/auth/test_oauth_session_activities.py` (FR-002, FR-003, FR-007, SC-001, acceptance scenarios 1 and 4, DESIGN-REQ-001, DESIGN-REQ-016)
- [ ] T008 [P] Add failing unit tests for OAuth finalization profile registration/update response redaction and sanitized validation failures in `tests/unit/api/routers/test_oauth_sessions.py` (FR-005, FR-007, SC-002, acceptance scenarios 1, 2, and 4, DESIGN-REQ-010, DESIGN-REQ-016)
- [ ] T009 [P] Add failing unit tests for Provider Profile response redaction, nested provider metadata redaction, slot policy exposure, and non-Codex scope guardrails in `tests/unit/api_service/api/routers/test_provider_profiles.py` (FR-003, FR-005, FR-008, SC-002, SC-004, acceptance scenarios 2 and 5, DESIGN-REQ-002, DESIGN-REQ-010)
- [ ] T010 [P] Add failing unit tests for workflow-facing profile snapshot redaction and compact ref preservation in `tests/unit/auth/test_oauth_session_activities.py` (FR-002, FR-006, FR-009, SC-002, DESIGN-REQ-010, DESIGN-REQ-020)
- [ ] T011 [P] Add failing integration test for OAuth verification to Provider Profile registration through the real boundary in `tests/integration/temporal/test_oauth_session.py` (FR-001, FR-002, FR-003, FR-007, SC-001, SC-002, acceptance scenarios 1 and 4, DESIGN-REQ-001, DESIGN-REQ-016)
- [ ] T012 [P] Add failing integration test for secret-free workflow/activity payload shape at the OAuth/profile boundary in `tests/integration/temporal/test_oauth_session.py` (FR-005, FR-006, FR-009, SC-002, acceptance scenario 2, DESIGN-REQ-010, DESIGN-REQ-020)
- [ ] T013 [P] Add failing integration test for non-Codex profile independence and no Claude/Gemini task-scoped parity requirement in `tests/integration/temporal/test_oauth_session.py` (FR-008, SC-004, acceptance scenario 5, DESIGN-REQ-002)
- [ ] T014 Run red-first focused unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py` and record expected failures in `specs/189-codex-auth-profile/tasks.md` (T005 through T010)
- [ ] T015 Run red-first integration tests with `./tools/test_integration.sh` when Docker is available; required coverage target is `tests/integration/temporal/test_oauth_session.py`; otherwise record the exact `/var/run/docker.sock` blocker in `specs/189-codex-auth-profile/tasks.md` (T011 through T013)
- [ ] T016 Implement Codex OAuth Provider Profile validation models and helpers in `moonmind/schemas/agent_runtime_models.py` for required shape, blank/unsafe ref rejection, and compact secret-free profile snapshots (FR-001, FR-002, FR-004, FR-006, SC-001, SC-003, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016)
- [ ] T017 Implement Provider Profile registration/update metadata handling in `moonmind/workflows/temporal/activities/oauth_session_activities.py` and related OAuth workflow code in `moonmind/workflows/temporal/workflows/oauth_session.py` (FR-002, FR-003, FR-006, FR-007, FR-009, SC-001, SC-002, acceptance scenarios 1 and 4, DESIGN-REQ-016, DESIGN-REQ-020)
- [ ] T018 Implement OAuth finalization create/update behavior and sanitized failure handling in `api_service/api/routers/oauth_sessions.py` and `api_service/api/schemas_oauth_sessions.py` (FR-002, FR-005, FR-007, SC-001, SC-002, acceptance scenarios 1, 2, 3, and 4, DESIGN-REQ-010, DESIGN-REQ-016)
- [ ] T019 Implement secret-free Provider Profile serialization, nested metadata redaction, slot policy exposure, and non-Codex guardrails in `api_service/api/routers/provider_profiles.py` and `api_service/services/provider_profile_service.py` (FR-003, FR-005, FR-008, FR-009, SC-002, SC-004, acceptance scenarios 2 and 5, DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-020)
- [ ] T020 Implement traceability preservation for `MM-355` in generated story evidence or verification metadata in `specs/189-codex-auth-profile/spec.md`, `specs/189-codex-auth-profile/contracts/codex-auth-profile.md`, and `specs/189-codex-auth-profile/tasks.md` without adding runtime scope (FR-010, SC-005)
- [ ] T021 Run focused unit tests until green with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py` and update evidence in `specs/189-codex-auth-profile/tasks.md` (T005 through T010, T016 through T019)
- [ ] T022 Run integration verification with `./tools/test_integration.sh` when Docker is available; required coverage target is `tests/integration/temporal/test_oauth_session.py`; update evidence or exact Docker blocker in `specs/189-codex-auth-profile/tasks.md` (T011 through T013, T017 through T019)
- [ ] T023 Validate the single-story acceptance scenarios, success criteria, source design mappings, and `MM-355` traceability against `specs/189-codex-auth-profile/spec.md` and `specs/189-codex-auth-profile/contracts/codex-auth-profile.md` (FR-001 through FR-010, SC-001 through SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020)

## Final Phase: Polish And Verification

- [ ] T024 Refactor only story-local code and tests after green validation in `api_service/api/routers/provider_profiles.py`, `api_service/api/routers/oauth_sessions.py`, `api_service/api/schemas_oauth_sessions.py`, `api_service/services/provider_profile_service.py`, `moonmind/schemas/agent_runtime_models.py`, `moonmind/workflows/temporal/activities/oauth_session_activities.py`, and `moonmind/workflows/temporal/workflows/oauth_session.py` (FR-001 through FR-009)
- [ ] T025 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification and record the result in `specs/189-codex-auth-profile/tasks.md` (SC-001 through SC-005)
- [ ] T026 Run quickstart validation from `specs/189-codex-auth-profile/quickstart.md` and record any exact Docker integration blocker in `specs/189-codex-auth-profile/tasks.md` (SC-001 through SC-005)
- [ ] T027 Run final `/moonspec-verify` equivalent for `specs/189-codex-auth-profile/spec.md` and write the verification result to `specs/189-codex-auth-profile/verification.md` (FR-010, SC-005)

## Dependencies And Order

- Complete Phase 1 and Phase 2 before writing red-first story tests.
- Write unit tests T005 through T010 and integration tests T011 through T013 before implementation tasks T016 through T020.
- Run red-first confirmation tasks T014 and T015 before implementation tasks T016 through T020.
- Complete implementation tasks T016 through T020 before green validation tasks T021 through T023.
- Complete focused validation before final full unit, quickstart, and `/moonspec-verify` tasks.
- Keep this task list scoped to exactly one story; do not implement interactive OAuth terminal UI, managed-session launch, Codex App Server startup, per-run home seeding, or Docker workload credential inheritance.

## Parallel Examples

- T005 and T007 can run in parallel because they touch different test files.
- T008 and T009 can run in parallel because one targets OAuth session routing and the other targets Provider Profile routing.
- T011, T012, and T013 can be drafted in parallel with unit test authoring, but red-first confirmation must wait for all relevant tests.
- T016, T018, and T019 should not run until red-first tests are confirmed; after that, they can proceed in parallel only if file ownership remains separate and shared schema changes from T016 are coordinated.

## Implementation Strategy

- Follow TDD: red unit tests, red integration tests, production implementation, focused green tests, integration verification, full unit suite, quickstart validation, final `/moonspec-verify`.
- Preserve `MM-355` and the original Jira preset brief in all MoonSpec artifacts and verification evidence.
- Treat missing Docker socket as a blocker to record, not a passing integration result.
- Keep credential values out of tests, logs, artifacts, and task evidence; use fake secret-like sentinel values only to prove redaction.
