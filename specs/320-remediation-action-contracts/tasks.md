# Tasks: Remediation Action Contracts

**Input**: Design documents from `/work/agent_jobs/mm:f14332d1-2a04-407d-acdd-23b4fa3c3448/repo/specs/320-remediation-action-contracts/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-action-contracts.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: `Typed Remediation Actions`.

**Source Traceability**: Original Jira issue `MM-620` and the preserved Jira preset brief are in `spec.md` `**Input**`. Tasks cover FR-001 through FR-010, SCN-001 through SCN-006, SC-001 through SC-006, and DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py -q`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel only when the task touches different files and has no dependency on incomplete work.
- Every task includes a concrete path and the relevant requirement, scenario, success criterion, or source mapping when applicable.

## Phase 1: Setup

**Purpose**: Confirm the active feature and existing tooling before writing tests.

- [ ] T001 Verify `.specify/feature.json` points to `specs/320-remediation-action-contracts` and that `specs/320-remediation-action-contracts/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/remediation-action-contracts.md` exist for MM-620.
- [ ] T002 Verify unit and integration command availability in `tools/test_unit.sh`, `tools/test_integration.sh`, and `specs/320-remediation-action-contracts/quickstart.md` before adding tests.

---

## Phase 2: Foundational

**Purpose**: Prepare test-only scaffolding that blocks reliable story test authoring.

**Critical**: No production implementation work can begin until foundational tasks and red-first tests are complete.

- [ ] T003 Add or extend reusable unit-test helpers for reading and decoding remediation lifecycle artifacts in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-004, FR-005, SCN-002, and SCN-004.
- [ ] T004 Create the hermetic integration test module scaffold with `integration` and `integration_ci` markers in `tests/integration/temporal/test_remediation_action_contracts.py` for DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.

**Checkpoint**: Test scaffolding is ready; story tests can now be written.

---

## Phase 3: Story - Typed Remediation Actions

**Summary**: As a remediation task, I can list and request only typed, allowlisted administrative actions with durable v1 request and result evidence so that interventions are validated, risk-scored, idempotency-aware, and auditable.

**Independent Test**: Create remediation contexts with different action policies and target states, then verify allowed action listing, request validation, risk outcomes, idempotency behavior, rejection of unsupported raw operations, and durable request/result evidence without unrestricted administrative access.

**Traceability**: FR-001 through FR-010; SCN-001 through SCN-006; SC-001 through SC-006; DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-026.

**Requirement Status Strategy**: FR-001, FR-002, FR-007, FR-009, FR-010, SCN-001, SCN-003, SCN-006, SC-001, SC-004, and SC-006 are already implemented_verified and receive validation only. FR-003, FR-005, FR-006, SCN-002, SCN-004, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-026, and SC-003 need code-and-test work. FR-004, FR-008, SCN-005, SC-002, and SC-005 are implemented_unverified and receive verification tests plus conditional fallback implementation.

**Unit Test Plan**:

- Verify action input validation, idempotency shape handling, and fresh target evidence consumption.
- Verify published v1 request/result artifacts include required fields and redaction.
- Verify action result status enumeration fails closed for unsupported values.
- Verify unsupported raw operation classes are rejected before side effects.
- Preserve existing high-risk, registry metadata, and unsupported action listing coverage.

**Integration Test Plan**:

- Verify the real remediation service/artifact boundary publishes and reads request, result, and verification artifacts for one executable action.
- Verify unsupported raw action attempts do not publish side-effect artifacts.

### Unit Tests (write first)

- [ ] T005 Add failing unit tests for action-specific input metadata validation, idempotency shape reuse, and fresh target evidence gating in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-003, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.
- [ ] T006 Add failing unit tests that read the published `remediation.action_request` artifact and assert the full v1 request evidence contract plus redaction in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-004, SCN-002, SC-002, and DESIGN-REQ-016.
- [ ] T007 Add failing unit tests that read the published `remediation.action_result` artifact and assert `message`, `appliedAt`, `verificationRequired`, `verificationHint`, status, before/after refs, and redacted side effects in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-005, SCN-004, SC-003, and DESIGN-REQ-016.
- [ ] T008 Add failing unit tests for allowed status validation and unsupported status rejection in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-006 and SC-003.
- [ ] T009 Add explicit unit tests for unsupported raw host, database, Docker, volume, network, secret-reading, and redaction-bypass action classes in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-008, SCN-005, SC-005, and DESIGN-REQ-015.
- [ ] T010 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py -q` and confirm T005 through T009 fail for the expected red-first reasons before production implementation.

### Integration Tests (write first)

- [ ] T011 Add a failing hermetic integration test for one executable remediation action that verifies list, authority, guard, execute, request artifact, result artifact, verification artifact, and link update behavior in `tests/integration/temporal/test_remediation_action_contracts.py` for SCN-002, SCN-004, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.
- [ ] T012 Add a failing hermetic integration test proving unsupported raw actions are rejected and do not publish side-effect request/result artifacts in `tests/integration/temporal/test_remediation_action_contracts.py` for SCN-005, FR-008, SC-005, and DESIGN-REQ-015.
- [ ] T013 Run `./tools/test_integration.sh` and confirm T011 through T012 fail for the expected red-first reasons before production implementation.
- [ ] T014 Record the red-first unit and integration failure evidence in `specs/320-remediation-action-contracts/implementation-notes.md` for FR-003, FR-004, FR-005, FR-006, FR-008, SCN-002, SCN-004, SCN-005, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.

### Implementation

- [ ] T015 Implement action parameter/input metadata validation and idempotency shape safeguards in `moonmind/workflows/temporal/remediation_actions.py` for FR-003, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.
- [ ] T016 Conditional fallback if T006 or T011 exposes a request artifact gap: update action request artifact publication in `moonmind/workflows/temporal/remediation_tools.py` for FR-004, SCN-002, SC-002, and DESIGN-REQ-016.
- [ ] T017 Implement allowed result status validation and fail-fast unsupported status handling in `moonmind/workflows/temporal/remediation_tools.py` for FR-006 and SC-003.
- [ ] T018 Complete the v1 action result artifact payload in `moonmind/workflows/temporal/remediation_tools.py` with `message`, `appliedAt`, `verificationRequired`, `verificationHint`, before/after refs, and redacted side effects for FR-005, SCN-004, SC-003, and DESIGN-REQ-016.
- [ ] T019 Conditional fallback if T009 or T012 exposes a raw-operation proof gap: expand raw operation deny-listing and explicit denial reasons in `moonmind/workflows/temporal/remediation_actions.py` for FR-008, SCN-005, SC-005, and DESIGN-REQ-015.
- [ ] T020 Ensure request/result/verification artifact payloads remain bounded and secret-safe in `moonmind/workflows/temporal/remediation_tools.py` for FR-004, FR-005, DESIGN-REQ-015, DESIGN-REQ-016, and SC-002.
- [ ] T021 Update integration fixture wiring only as needed in `tests/integration/temporal/test_remediation_action_contracts.py` so the hermetic tests exercise the real artifact service boundary for DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.

### Story Validation

- [ ] T022 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py -q` and verify all unit coverage for FR-001 through FR-010, SCN-001 through SCN-006, and SC-001 through SC-006 passes.
- [ ] T023 Run `./tools/test_integration.sh` and verify hermetic integration coverage for DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-026, SCN-002, SCN-004, and SCN-005 passes.
- [ ] T024 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and verify the full required unit suite passes before final verification.
- [ ] T025 Validate the independent story flow in `specs/320-remediation-action-contracts/quickstart.md` and record the result in `specs/320-remediation-action-contracts/implementation-notes.md` for MM-620 traceability.

**Checkpoint**: The single story is fully functional, covered by unit and integration tests, and independently validated.

---

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed story without adding scope.

- [ ] T026 [P] Update `specs/320-remediation-action-contracts/contracts/remediation-action-contracts.md` if implementation changes the final v1 request/result contract wording for FR-004, FR-005, and FR-006.
- [ ] T027 [P] Update `specs/320-remediation-action-contracts/data-model.md` if implementation changes action request/result fields or state transitions for FR-004, FR-005, and FR-006.
- [ ] T028 [P] Update `specs/320-remediation-action-contracts/research.md` if implementation evidence changes any requirement status classification for FR-003, FR-004, FR-005, FR-006, or FR-008.
- [ ] T029 Re-run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py -q` after polish changes to guard against drift in `tests/unit/workflows/temporal/test_remediation_context.py`.
- [ ] T030 Re-run `./tools/test_integration.sh` after polish changes when `tests/integration/temporal/test_remediation_action_contracts.py` exists or integration wiring changed.
- [ ] T031 Run `/moonspec-verify` against `specs/320-remediation-action-contracts/spec.md`, `plan.md`, `tasks.md`, source design mappings, preserved MM-620 Jira preset brief, and test evidence after implementation and tests pass.

---

## Dependencies and Execution Order

### Phase Dependencies

- Setup phase has no dependencies.
- Foundational phase depends on Setup and blocks story test authoring.
- Story phase depends on Foundational completion.
- Polish and Verification depend on story validation passing.

### Within The Story

- T005 through T009 must be written before T010.
- T011 through T012 must be written before T013.
- T010 and T013 red-first confirmations must complete before T015 through T021 production implementation.
- T016 is conditional fallback work for implemented_unverified FR-004 and is skipped only if T006 and T011 prove existing request evidence already satisfies the contract.
- T019 is conditional fallback work for implemented_unverified FR-008 and is skipped only if T009 and T012 prove existing raw-operation denial already satisfies the contract.
- T022 through T025 validate the completed story and must pass before Phase 4.
- T031 is last and runs only after implementation, focused tests, integration tests, quickstart validation, and full unit tests pass.

### Parallel Opportunities

- T001 and T002 can run in parallel.
- T003 and T004 can run in parallel because they prepare different test scopes.
- T026 through T028 can run in parallel if implementation changes require artifact updates.
- Test authoring in T005 through T009 touches the same unit file and should be coordinated rather than parallelized.
- T011 and T012 touch the same integration file and should be coordinated rather than parallelized.

---

## Implementation Strategy

1. Confirm the active feature and tooling in Phase 1.
2. Prepare test scaffolding in Phase 2.
3. Write all unit and integration tests first.
4. Run focused unit and integration commands to capture red-first failures.
5. Implement only the gaps identified by the failing tests.
6. Skip conditional fallback implementation tasks only when verification tests pass without code changes.
7. Re-run focused unit tests, hermetic integration tests, and the full unit suite.
8. Validate the quickstart story flow.
9. Run final `/moonspec-verify` with MM-620 and the preserved preset brief as source evidence.

## Notes

- This task list covers one story only: `Typed Remediation Actions`.
- Existing implemented_verified rows remain traceable but do not receive unnecessary implementation tasks.
- Partial rows receive red-first tests and implementation tasks.
- Implemented_unverified rows receive verification tests and conditional fallback implementation tasks.
- No production implementation should start before T010 and T013 confirm expected red-first failures.
