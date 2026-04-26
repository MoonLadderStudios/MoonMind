# Tasks: Explicit Failure and Rollback Controls

**Input**: Design documents from `specs/265-explicit-failure-and-rollback-controls/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/deployment-failure-rollback-controls.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: Explicit Failure and Rollback Controls for MM-523.

**Source Traceability**: The original MM-523 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-011, acceptance scenarios 1 through 7, edge cases, SC-001 through SC-008, and DESIGN-REQ-001 through DESIGN-REQ-006.

**Requirement Status Summary**:

- Code and tests required: FR-001, FR-004, FR-005, FR-006, FR-007, FR-009, FR-011; DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-006; SC-001, SC-004, SC-005, SC-007, SC-008.
- Verification tests plus conditional fallback: FR-003, FR-008; SC-003, SC-006.
- Already verified, preserve through final validation: FR-002, FR-010; SC-002; DESIGN-REQ-002, DESIGN-REQ-005.

**Test Commands**:

- Unit tests: `pytest tests/unit/workflows/skills/test_deployment_update_execution.py tests/unit/api/routers/test_deployment_operations.py -q`
- UI unit tests: `./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx`
- Integration tests: `pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q`
- Final unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final integration suite: `./tools/test_integration.sh` when Docker is available
- Final verification: `/speckit.verify`

## Phase 1: Setup

**Purpose**: Confirm the active feature artifacts and test surfaces are ready.

- [ ] T001 Verify `.specify/feature.json` points to `specs/265-explicit-failure-and-rollback-controls` and record the branch-name prerequisite-script blocker in `specs/265-explicit-failure-and-rollback-controls/tasks.md`. (FR-011, SC-008)
- [ ] T002 Inspect current deployment implementation and test entry points in `moonmind/workflows/skills/deployment_execution.py`, `api_service/services/deployment_operations.py`, `api_service/api/routers/deployment_operations.py`, `frontend/src/components/settings/OperationsSettingsSection.tsx`, `tests/unit/workflows/skills/test_deployment_update_execution.py`, `tests/unit/api/routers/test_deployment_operations.py`, and `frontend/src/components/settings/OperationsSettingsSection.test.tsx`. (FR-001-FR-010)

---

## Phase 2: Foundational

**Purpose**: Establish shared test fixtures and contract expectations before story implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T003 [P] Add shared failing-test fixtures for deployment failure classes, rollback-safe before-state evidence, unsafe before-state evidence, and rollback metadata in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-001, FR-006, FR-007, DESIGN-REQ-001, DESIGN-REQ-004)
- [ ] T004 [P] Add shared fake recent-action/projection fixtures for deployment stack state and rollback submission tests in `tests/unit/api/routers/test_deployment_operations.py`. (FR-003, FR-004, FR-005, FR-009, SC-003, SC-007)
- [ ] T005 [P] Add mock deployment stack state entries for eligible and ineligible rollback actions in `frontend/src/components/settings/OperationsSettingsSection.test.tsx`. (FR-006, FR-007, SC-005)

**Checkpoint**: Foundation ready for red-first story tests.

---

## Phase 3: Story - Explicit Failure and Rollback Controls

**Summary**: As an operations administrator, I need failed updates and rollbacks to remain explicit audited actions, so partial deployment changes do not trigger hidden retries or silent rollbacks.

**Independent Test**: Exercise deployment update failure and rollback decision flows with controlled update outcomes and artifact evidence, then verify final statuses, retry behavior, rollback eligibility, required authorization inputs, audit records, and absence of silent rollback.

**Traceability**: FR-001 through FR-011; acceptance scenarios 1-7; SC-001 through SC-008; DESIGN-REQ-001 through DESIGN-REQ-006.

### Unit Tests (write first)

- [ ] T006 [P] Add failing unit test matrix for normalized failure classes and actionable reasons in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-001, SC-001, DESIGN-REQ-001)
- [ ] T007 [P] Add failing unit tests for rollback eligibility from safe, missing, ambiguous, malformed, and non-allowlisted before-state evidence in `tests/unit/api/routers/test_deployment_operations.py`. (FR-006, FR-007, SC-005, DESIGN-REQ-004)
- [ ] T008 [P] Add failing unit tests proving rollback submission uses the normal typed update path with `operationKind=rollback`, `rollbackSourceActionId`, admin authorization, explicit confirmation, reason, and policy-valid target in `tests/unit/api/routers/test_deployment_operations.py`. (FR-004, FR-005, SC-004, DESIGN-REQ-003)
- [ ] T009 [P] Add verification-first unit test proving two explicit deployment update submissions are distinct audited operator actions, not automatic retry continuation, in `tests/unit/api/routers/test_deployment_operations.py`. (FR-003, SC-003, DESIGN-REQ-002)
- [ ] T010 [P] Add verification-first unit test proving failed deployment execution does not enqueue or perform rollback without explicit rollback metadata in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-008, SC-006, DESIGN-REQ-004)
- [ ] T011 [P] Add failing API unit tests for recent failure and rollback action fields in deployment stack state in `tests/unit/api/routers/test_deployment_operations.py`. (FR-009, SC-007, DESIGN-REQ-006)
- [ ] T012 [P] Add failing UI unit tests for rendering rollback controls only on eligible recent actions and withholding them for unsafe actions in `frontend/src/components/settings/OperationsSettingsSection.test.tsx`. (FR-006, FR-007, acceptance scenarios 4-5)
- [ ] T013 [P] Add failing UI unit test for rollback confirmation text and submitted typed deployment payload including explicit confirmation metadata in `frontend/src/components/settings/OperationsSettingsSection.test.tsx`. (FR-004, FR-005, acceptance scenario 3)
- [ ] T014 Run `pytest tests/unit/workflows/skills/test_deployment_update_execution.py tests/unit/api/routers/test_deployment_operations.py -q` and `./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx` to confirm T006-T013 fail for the expected missing behavior. (SC-001, SC-003, SC-004, SC-005, SC-006, SC-007)

### Integration Tests (write first)

- [ ] T015 [P] Add failing hermetic integration test for failed `deployment.update_compose_stack` dispatch exposing failure metadata without automatic rollback in `tests/integration/temporal/test_deployment_update_execution_contract.py`. (FR-001, FR-008, SC-001, SC-006, DESIGN-REQ-001, DESIGN-REQ-004)
- [ ] T016 [P] Add failing hermetic integration test proving rollback dispatch still invokes `deployment.update_compose_stack` through the existing typed tool contract in `tests/integration/temporal/test_deployment_update_execution_contract.py`. (FR-004, FR-005, DESIGN-REQ-003, DESIGN-REQ-005)
- [ ] T017 Run `pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q` to confirm T015-T016 fail for the expected missing behavior. (SC-004, SC-006)

### Red-First Confirmation

- [ ] T018 Confirm and record in `specs/265-explicit-failure-and-rollback-controls/tasks.md` that T006-T017 were run before production implementation and failed for missing MM-523 behavior rather than fixture or syntax errors. (FR-001-FR-009, SC-001-SC-007)

### Conditional Fallback For Implemented-Unverified Rows

- [ ] T019 If T009 fails, update explicit retry/audit request handling in `api_service/services/deployment_operations.py` so repeated operator submissions remain separate audited update requests. (FR-003, SC-003)
- [ ] T020 If T010 or T015 shows an automatic rollback path, remove that path in `moonmind/workflows/skills/deployment_execution.py` or `api_service/services/deployment_operations.py` and require explicit rollback metadata instead. (FR-008, SC-006, DESIGN-REQ-004)

### Implementation

- [ ] T021 Add deployment failure-class normalization and failure metadata output in `moonmind/workflows/skills/deployment_execution.py`. (FR-001, SC-001, DESIGN-REQ-001)
- [ ] T022 Update deployment tool output schema for compact failure metadata in `moonmind/workflows/skills/deployment_tools.py`. (FR-001, DESIGN-REQ-001)
- [ ] T023 Add rollback eligibility and recent deployment action models to `api_service/api/routers/deployment_operations.py`. (FR-006, FR-007, FR-009, DESIGN-REQ-004, DESIGN-REQ-006)
- [ ] T024 Implement rollback eligibility derivation and fail-closed unsafe evidence handling in `api_service/services/deployment_operations.py`. (FR-006, FR-007, SC-005, DESIGN-REQ-004)
- [ ] T025 Extend rollback submission metadata, explicit confirmation validation, and normal typed update queue construction in `api_service/services/deployment_operations.py`. (FR-004, FR-005, DESIGN-REQ-003)
- [ ] T026 Wire rollback request fields, including `confirmation`, through `DeploymentUpdateRequest` and queue response handling in `api_service/api/routers/deployment_operations.py`. (FR-004, FR-005)
- [ ] T027 Surface bounded failure and rollback recent actions in deployment stack state from existing execution/artifact evidence in `api_service/api/routers/deployment_operations.py` and `api_service/services/deployment_operations.py`. (FR-009, SC-007, DESIGN-REQ-006)
- [ ] T028 Add rollback eligibility schema, rollback action rendering, unsafe-action withholding, and rollback confirmation flow in `frontend/src/components/settings/OperationsSettingsSection.tsx`. (FR-004, FR-005, FR-006, FR-007, acceptance scenarios 3-5)
- [ ] T029 Preserve allowlisted boundaries for rollback targets and raw command-log hiding in `api_service/services/deployment_operations.py`, `api_service/api/routers/deployment_operations.py`, and `frontend/src/components/settings/OperationsSettingsSection.tsx`. (FR-010, DESIGN-REQ-005)

### Story Validation

- [ ] T030 Run `pytest tests/unit/workflows/skills/test_deployment_update_execution.py tests/unit/api/routers/test_deployment_operations.py -q` and fix only MM-523-related failures. (FR-001-FR-010)
- [ ] T031 Run `./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx` and fix only MM-523-related failures. (FR-004-FR-009)
- [ ] T032 Run `pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q` and fix only MM-523-related failures. (FR-001, FR-004, FR-005, FR-008)
- [ ] T033 Run traceability grep `rg -n "MM-523|DESIGN-REQ-001|rollbackEligibility|operationKind|failureClass" specs/265-explicit-failure-and-rollback-controls moonmind api_service frontend/src tests` and update missing traceability in feature artifacts or tests. (FR-011, SC-008)

**Checkpoint**: MM-523 story is functionally complete, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [ ] T034 [P] Review `moonmind/workflows/skills/deployment_execution.py` and `api_service/services/deployment_operations.py` for secret hygiene and remove any unredacted failure or rollback evidence from outputs. (FR-001, FR-009, security guardrail)
- [ ] T035 [P] Review `frontend/src/components/settings/OperationsSettingsSection.tsx` for accessible rollback button labels, confirmation text, and no overlapping or misleading UI states. (FR-004, FR-006, FR-007)
- [ ] T036 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full required unit verification, including existing retry-policy and allowlist boundary coverage in `tests/unit/workflows/skills/test_deployment_tool_contracts.py`, `tests/unit/workflows/skills/test_deployment_update_execution.py`, and `tests/unit/api/routers/test_deployment_operations.py`. (FR-002, FR-010, SC-001-SC-008, DESIGN-REQ-002, DESIGN-REQ-005)
- [ ] T037 Run `./tools/test_integration.sh` when Docker is available; if unavailable, record the Docker availability blocker and the focused hermetic integration evidence from T032 in `specs/265-explicit-failure-and-rollback-controls/verification.md`. (SC-004, SC-006)
- [ ] T038 Run `/speckit.verify` after implementation and tests pass, preserving MM-523, final verdict, tests run, and remaining risks in `specs/265-explicit-failure-and-rollback-controls/verification.md`. (FR-011, SC-008)

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks story test authoring.
- Phase 3 depends on Phase 2. Unit and integration tests must be written and confirmed failing before implementation tasks T021-T029.
- Phase 4 depends on the story being implemented and focused tests passing.

### Within The Story

- T006-T013 may be authored in parallel after T003-T005 because they touch separate test files.
- T014 must run after T006-T013 and before implementation.
- T015-T016 may be authored in parallel because they add separate integration scenarios in the same file but do not depend on production changes; coordinate edits if one agent owns the file.
- T017 must run after T015-T016 and before implementation.
- T018 must complete before T019-T029.
- T019-T020 are conditional fallback tasks only if verification-first tests fail.
- T021-T022 precede integration validation for tool output.
- T023-T027 precede UI implementation T028 because the UI consumes API shape.
- T030-T033 validate the complete story before polish.

## Parallel Opportunities

- T003, T004, and T005 can run in parallel.
- T006, T007, T008, T009, T010, T011, T012, and T013 can run in parallel by file ownership groups.
- T015 and T016 can run in parallel if the integration test file is coordinated.
- T034 and T035 can run in parallel after story validation.

## Parallel Example

```bash
# Backend executor tests
Task: "T006 Add failing unit test matrix in tests/unit/workflows/skills/test_deployment_update_execution.py"
Task: "T010 Add no-silent-rollback verification test in tests/unit/workflows/skills/test_deployment_update_execution.py"

# API and UI tests
Task: "T007/T008/T011 Add API tests in tests/unit/api/routers/test_deployment_operations.py"
Task: "T012/T013 Add UI tests in frontend/src/components/settings/OperationsSettingsSection.test.tsx"
```

## Implementation Strategy

1. Preserve the existing typed deployment update tool and API route names.
2. Write red-first unit, UI, and integration tests for missing and partial MM-523 behavior.
3. Run focused tests and confirm failures before production changes.
4. Implement failure metadata first, then backend rollback eligibility/submission/recent action projection, then UI rollback controls.
5. Preserve already-verified no-default-retry and allowlist boundaries through final validation rather than rewriting them.
6. Run focused tests, full unit verification, available integration verification, and final `/speckit.verify`.

## Notes

- This task list covers one story only.
- Do not add a separate rollback executor or new persistent deployment action table unless implementation proves existing execution/artifact evidence cannot satisfy the contract; if that happens, update `plan.md` and `data-model.md` before changing scope.
- Rollback must remain an explicit operator action through the normal typed deployment update path.
- No production implementation task may start until red-first confirmation T018 is complete.
