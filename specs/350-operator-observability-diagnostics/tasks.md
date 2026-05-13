# Tasks: Operator Observability for Attachments, Recovery, and Resume Diagnostics

**Input**: Design documents from `/work/agent_jobs/mm:65075e25-154f-4b9d-ada1-8cf187f002c9/repo/specs/350-operator-observability-diagnostics/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/execution-target-diagnostics-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first; confirm red-first failures for partial behavior and record verification-first pass/fail outcomes for implemented_unverified behavior before applying production changes.

**Organization**: One story only: Operator Observability Diagnostics.

**Source Traceability**: MM-651 and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-014, acceptance scenarios 1-7, edge cases, SC-001 through SC-006, DESIGN-REQ-012, DESIGN-REQ-030, and DESIGN-REQ-031.

**Requirement Status Summary**: `plan.md` classifies 12 rows as implemented_verified, 5 rows as implemented_unverified, 6 rows as partial, and 0 rows as missing. This task list preserves already-verified rows in validation, adds verification tests for implemented_unverified rows, and adds conditional fallback implementation tasks for partial rows.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Focused backend tests: `pytest tests/unit/api/routers/test_executions.py -q --tb=short`
- Focused frontend tests: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Focused integration tests: `pytest tests/integration/schemas/test_execution_target_diagnostics_boundary.py -q --tb=short`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete work.
- Every task includes exact file paths and relevant requirement, scenario, success, or source IDs.

## Phase 1: Setup

**Purpose**: Confirm the active feature artifacts, runners, and evidence paths before test authoring.

- [X] T001 Confirm the active feature artifacts and source traceability in specs/350-operator-observability-diagnostics/spec.md, specs/350-operator-observability-diagnostics/plan.md, specs/350-operator-observability-diagnostics/research.md, specs/350-operator-observability-diagnostics/data-model.md, specs/350-operator-observability-diagnostics/contracts/execution-target-diagnostics-contract.md, and specs/350-operator-observability-diagnostics/quickstart.md for MM-651, FR-014, and SC-006
- [X] T002 [P] Confirm backend unit test runner expectations in tools/test_unit.sh and focused backend test target tests/unit/api/routers/test_executions.py for unit strategy coverage
- [X] T003 [P] Confirm frontend unit test runner expectations in package.json and focused frontend test target frontend/src/entrypoints/task-detail.test.tsx for unit strategy coverage
- [X] T004 [P] Confirm hermetic integration runner expectations in tools/test_integration.sh and focused boundary test target tests/integration/schemas/test_execution_target_diagnostics_boundary.py for integration strategy coverage

---

## Phase 2: Foundational

**Purpose**: Validate existing contract locations and fixtures before story tests are added.

**Checkpoint**: No production implementation work begins until Phase 2 is complete.

- [X] T005 Confirm the execution detail projection and target diagnostics helper surfaces in api_service/api/routers/executions.py for FR-001 through FR-013, DESIGN-REQ-012, DESIGN-REQ-030, and DESIGN-REQ-031
- [X] T006 [P] Confirm frontend target diagnostics schema and panel locations in frontend/src/entrypoints/task-detail.tsx for FR-001 through FR-013 and acceptance scenarios 1-7
- [X] T007 [P] Confirm schema boundary fixtures in tests/integration/schemas/test_execution_target_diagnostics_boundary.py and generated model references in frontend/src/generated/openapi.ts for contract coverage

---

## Phase 3: Story - Operator Observability Diagnostics

**Summary**: As an operator inspecting task details, I want attachment metadata, generated context references, recovery provenance, and explicit failure phases grouped by target so that I can diagnose attachment and Resume outcomes without parsing raw workflow history.

**Independent Test**: View task details and diagnostics for attachment-aware tasks, step-aware tasks, resumed executions, attachment failures, and failed Resume attempts, then confirm displayed evidence identifies target ownership, relevant refs, reused prior steps, and failure phase without raw workflow-history inspection.

**Traceability**: FR-001 through FR-014; acceptance scenarios 1-7; SC-001 through SC-006; DESIGN-REQ-012; DESIGN-REQ-030; DESIGN-REQ-031.

**Unit Test Plan**:

- Backend route unit tests cover empty target distinction, generated context refs, compatibility semantic non-drift, and failed Resume phase mapping.
- Frontend unit tests cover target card rendering, empty target copy, generated context evidence, failed Resume phase display, preserved steps, and raw diagnostics access.

**Integration Test Plan**:

- Schema or route-boundary integration tests cover serialized payload shape, alias-shaped inputs, target semantic preservation, preserved-step provenance, and bounded failed Resume phase values.

### Unit Tests (write first)

- [X] T008 [P] Add failing backend unit test for empty objective/step target distinction in tests/unit/api/routers/test_executions.py covering FR-002, SC-001, DESIGN-REQ-030, and edge cases for targets without attachments
- [X] T009 [P] Add verification-first backend unit test for generated context refs in tests/unit/api/routers/test_executions.py covering FR-004, FR-010, SC-003, and DESIGN-REQ-030
- [X] T010 [P] Add verification-first backend unit test for objective-vs-step compatibility non-drift with alias-shaped attachment input in tests/unit/api/routers/test_executions.py covering FR-011, FR-012, DESIGN-REQ-012, and acceptance scenario 7
- [X] T011 [P] Add failing backend unit test for failed Resume phase `failed_step_execution` in tests/unit/api/routers/test_executions.py covering FR-013, SC-005, DESIGN-REQ-031, and acceptance scenario 6
- [X] T012 [P] Add failing frontend unit test for empty target and generated context evidence rendering in frontend/src/entrypoints/task-detail.test.tsx covering FR-002, FR-004, FR-010, SC-003, and acceptance scenarios 2-4
- [X] T013 [P] Add failing frontend unit test for failed Resume phase display and preserved-step provenance in frontend/src/entrypoints/task-detail.test.tsx covering FR-008, FR-009, FR-013, SC-004, SC-005, and acceptance scenarios 5-6

### Integration Tests (write first)

- [X] T014 [P] Add verification-first integration schema-boundary test for alias-shaped objective and step target payloads in tests/integration/schemas/test_execution_target_diagnostics_boundary.py covering FR-011, FR-012, DESIGN-REQ-012, and acceptance scenario 7
- [X] T015 [P] Add failing integration schema-boundary test for `failed_step_execution` recovery phase and preserved-step provenance in tests/integration/schemas/test_execution_target_diagnostics_boundary.py covering FR-008, FR-009, FR-013, SC-004, SC-005, and DESIGN-REQ-031
- [X] T016 [P] Add verification-first integration schema-boundary test for generated context refs and bounded attachment failure phases in tests/integration/schemas/test_execution_target_diagnostics_boundary.py covering FR-003, FR-004, FR-005, FR-006, SC-002, SC-003, and DESIGN-REQ-030

### Red-First Confirmation

- [X] T017 Run `pytest tests/unit/api/routers/test_executions.py -q --tb=short` for tests/unit/api/routers/test_executions.py and confirm T008 and T011 fail for expected partial behavior while T009-T010 either pass as verification or fail before conditional fallback implementation
- [X] T018 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` for frontend/src/entrypoints/task-detail.test.tsx and confirm T012-T013 fail for the expected missing or partial behavior before production changes
- [X] T019 Run `pytest tests/integration/schemas/test_execution_target_diagnostics_boundary.py -q --tb=short` for tests/integration/schemas/test_execution_target_diagnostics_boundary.py and confirm T015 fails for expected partial behavior while T014 and T016 either pass as verification or fail before conditional fallback implementation

### Conditional Fallback Implementation

- [X] T020 If T008 fails, update target construction in api_service/api/routers/executions.py so objective and step targets without attachments are distinguishable when required for FR-002, SC-001, and DESIGN-REQ-030
- [X] T021 If T009 or T016 fails, update ref normalization or projection in api_service/api/routers/executions.py so generated context refs are preserved and surfaced for FR-004, FR-010, SC-003, and DESIGN-REQ-030
- [X] T022 If T010 or T014 fails, update target overlay merge and alias handling in api_service/api/routers/executions.py so compatibility-shaped input cannot retarget, merge, or relabel objective and step attachments for FR-011, FR-012, and DESIGN-REQ-012
- [X] T023 If T011 or T015 fails, update failed Resume phase derivation in api_service/api/routers/executions.py so `failed_step_execution` is surfaced without mislabeling other phases for FR-013, SC-005, and DESIGN-REQ-031
- [X] T024 If Resume source/provenance inputs are insufficient for T023, update Resume provenance emission in moonmind/workflows/temporal/service.py and validation handling in moonmind/workflows/temporal/workflows/run.py for FR-008, FR-009, FR-013, SC-004, SC-005, and DESIGN-REQ-031
- [X] T025 If T012 or T013 fails, update TargetDiagnosticsPanel rendering in frontend/src/entrypoints/task-detail.tsx for empty targets, generated context refs, failed Resume phase, source run, checkpoint, preserved steps, and raw diagnostics independence covering FR-002, FR-004, FR-008, FR-009, FR-010, FR-013, SC-003, SC-004, and SC-005
- [X] T026 If schema models change during T020-T025, update Pydantic/OpenAPI-facing target diagnostics models in moonmind/schemas/temporal_models.py and regenerate or refresh frontend/src/generated/openapi.ts for contract coverage in specs/350-operator-observability-diagnostics/contracts/execution-target-diagnostics-contract.md

### Story Validation

- [X] T027 Run `pytest tests/unit/api/routers/test_executions.py -q --tb=short` for tests/unit/api/routers/test_executions.py and fix failures until backend target diagnostics behavior passes FR-001 through FR-014 and DESIGN-REQ-012/DESIGN-REQ-030/DESIGN-REQ-031
- [X] T028 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` for frontend/src/entrypoints/task-detail.test.tsx and fix failures until task detail rendering passes acceptance scenarios 1-7 and SC-001 through SC-005
- [X] T029 Run `pytest tests/integration/schemas/test_execution_target_diagnostics_boundary.py -q --tb=short` for tests/integration/schemas/test_execution_target_diagnostics_boundary.py and fix failures until contract and integration boundary coverage passes DESIGN-REQ-012, DESIGN-REQ-030, and DESIGN-REQ-031
- [X] T030 Confirm already-verified behavior remains covered in tests/unit/api/routers/test_executions.py and frontend/src/entrypoints/task-detail.test.tsx for FR-001, FR-003, FR-005, FR-006, FR-007, FR-008, FR-009, FR-014, SC-001, SC-002, SC-004, and SC-006

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently testable from task detail.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding scope.

- [X] T031 [P] Review api_service/api/routers/executions.py, moonmind/workflows/temporal/service.py, moonmind/workflows/temporal/workflows/run.py, and frontend/src/entrypoints/task-detail.tsx for secret-safe diagnostics, compact refs, and no binary payload leakage covering constitution security guardrails and DESIGN-REQ-030
- [X] T032 [P] Update specs/350-operator-observability-diagnostics/quickstart.md if validation commands or focused test paths changed during implementation for FR-014 and SC-006
- [X] T033 Run `./tools/test_unit.sh` for full unit verification required by AGENTS.md and fix failures related to MM-651
- [ ] T034 Run `./tools/test_integration.sh` for required hermetic integration verification and fix failures related to MM-651
- [X] T035 Run `rg -n "MM-651|DESIGN-REQ-012|DESIGN-REQ-030|DESIGN-REQ-031" specs/350-operator-observability-diagnostics` for specs/350-operator-observability-diagnostics and confirm traceability coverage for FR-014 and SC-006
- [ ] T036 Run `/moonspec-verify` for specs/350-operator-observability-diagnostics after implementation and tests pass, validating MM-651, the original Jira preset brief, spec.md, plan.md, tasks.md, required tests, DESIGN-REQ-012, DESIGN-REQ-030, and DESIGN-REQ-031

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Phase 2; tests must be written and confirmed before conditional implementation.
- **Polish And Verification (Phase 4)**: Depends on story tests and implementation passing.

### Within The Story

- T008-T016 must be written before T017-T019.
- T017-T019 red-first and verification-first confirmation must complete before T020-T026 production changes.
- T020-T026 are conditional fallback tasks; skip any task whose verification tests already pass without production changes.
- T027-T030 validate the story after conditional implementation.
- T033-T036 run only after focused story validation passes.

### Parallel Opportunities

- T002-T004 can run in parallel.
- T006-T007 can run in parallel after T005.
- T008-T016 can be authored in parallel because they touch different backend, frontend, and integration test files or independent test cases.
- T031-T032 can run in parallel after story validation.

## Parallel Example: Story Phase

```bash
# Parallel test authoring after Phase 2:
Task: "T008 Add backend empty target test in tests/unit/api/routers/test_executions.py"
Task: "T012 Add frontend target diagnostics rendering test in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T014 Add integration alias contract test in tests/integration/schemas/test_execution_target_diagnostics_boundary.py"
```

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational inspection tasks.
2. Add backend, frontend, and integration tests for partial and implemented_unverified rows.
3. Run focused tests and confirm red-first failures for partial behavior while recording verification-first pass/fail outcomes.
4. Apply conditional fallback implementation only where verification fails.
5. Re-run focused backend, frontend, and integration tests until the one story passes.
6. Run full required unit and hermetic integration suites.
7. Run `/moonspec-verify`.

### Status Handling

- **Code-and-test work**: FR-002, FR-010, FR-013, SC-005, DESIGN-REQ-030, DESIGN-REQ-031.
- **Verification-first with conditional fallback**: FR-004, FR-011, FR-012, SC-003, DESIGN-REQ-012.
- **Already verified and preserved by validation**: FR-001, FR-003, FR-005, FR-006, FR-007, FR-008, FR-009, FR-014, SC-001, SC-002, SC-004, SC-006.

## Notes

- This task list covers exactly one story.
- Unit and integration tests are mandatory and appear before production implementation tasks.
- Red-first and verification-first confirmation tasks T017-T019 appear before conditional production changes.
- Final `/moonspec-verify` work is T036.
- Do not create new persistent storage for this story.
- Preserve `MM-651` and the original Jira preset brief in downstream verification evidence, commit text, and pull request metadata.
