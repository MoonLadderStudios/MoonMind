# Tasks: Dependencies and Execution Options

**Input**: Design documents from `specs/198-dependencies-execution-options/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style Create page tests are REQUIRED. Write tests first, confirm they fail for the intended reason when implementation is absent, then implement production code until they pass.

**Source Traceability**: MM-379, the Original Jira Preset Brief preserved in `spec.md`, FR-001 through FR-010, acceptance scenarios 1-6, SC-001 through SC-007, DESIGN-REQ-004, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm MM-379 orchestration input exists in `spec.md` (Input) and the original Jira preset brief is preserved in `specs/198-dependencies-execution-options/spec.md` (FR-010, SC-007)
- [X] T002 Create Moon Spec artifact directory `specs/198-dependencies-execution-options/` with spec, plan, research, data model, contract, quickstart, and tasks

## Phase 2: Foundational

- [X] T003 Identify Create page dependency and execution option surfaces in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx` (DESIGN-REQ-004, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015)

## Phase 3: Story - Dependencies and Execution Options

**Summary**: As a task author, I can select dependencies and configure execution options without Jira or image flows weakening validation.

**Independent Test**: Submit Create page drafts across dependency, runtime, publish, resolver, Jira import, and image upload flows and inspect validation messages plus create payloads.

**Traceability**: Original Jira Preset Brief, FR-001 through FR-010; scenarios 1-6; SC-001 through SC-007; DESIGN-REQ-004, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015.

### Unit Tests

- [X] T004 Add or confirm dependency picker tests for duplicate rejection and the 10-item cap in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-002, SC-002, DESIGN-REQ-013)
- [X] T005 Add test for dependency fetch failure preserving manual draft submission in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, SC-001, DESIGN-REQ-013)
- [X] T006 Add or confirm runtime/provider profile option tests for runtime-specific server-provided options in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-005, SC-003, DESIGN-REQ-014)
- [X] T007 Add or confirm merge automation availability and copy tests in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, FR-007, FR-009, SC-004, SC-005, DESIGN-REQ-015)

### Integration Tests

- [X] T008 Add or confirm Create page request-shape test for selected dependencies submitted through `payload.task.dependsOn` in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-002, DESIGN-REQ-013)
- [X] T009 Add or confirm Create page request-shape tests for merge automation payload preservation and omission in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, FR-007, SC-004, SC-005)
- [X] T010 Add tests proving Jira import and image upload do not bypass repository validation, publish validation, runtime gating, dependency limits, or resolver-style restrictions in `frontend/src/entrypoints/task-create.test.tsx` (FR-008, SC-006, DESIGN-REQ-015)

### Red-First Confirmation

- [X] T011 Run focused UI tests and record that newly added T005/T010 coverage passes against the existing runtime behavior, so no production patch is required

### Implementation

- [X] T012 Verify dependency fetch failure behavior in `frontend/src/entrypoints/task-create.tsx`; no production patch required after T005 passed (FR-003)
- [X] T013 Verify dependency limit, duplicate rejection, and dependency payload behavior in `frontend/src/entrypoints/task-create.tsx`; no production patch required after T004/T008 passed (FR-001, FR-002)
- [X] T014 Verify runtime/provider profile default behavior in `frontend/src/entrypoints/task-create.tsx`; no production patch required after T006 passed (FR-004, FR-005)
- [X] T015 Verify merge automation visibility, stale omission, payload shape, and user-facing copy in `frontend/src/entrypoints/task-create.tsx`; no production patch required after T007/T009 passed (FR-006, FR-007, FR-009)
- [X] T016 Verify Jira/image validation isolation in `frontend/src/entrypoints/task-create.tsx`; no production patch required after T010 passed (FR-008)

### Story Validation

- [X] T017 Run focused UI validation `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`
- [X] T018 Run final repository unit validation `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --no-xdist`

## Phase 4: Polish

- [X] T019 Run final `/moonspec-verify` work and record verification in `specs/198-dependencies-execution-options/verification.md`

## Dependencies & Execution Order

- Setup and foundational review precede story implementation.
- T004-T010 must exist before T012-T016 are considered complete.
- T011 records whether newly added coverage exposed a production gap; this run found the existing runtime behavior already satisfied the new coverage.
- T017 and T018 validate implementation before T019 verification.

## Parallel Examples

- T005 and T010 can be drafted together only if they touch independent test cases in `frontend/src/entrypoints/task-create.test.tsx`; final execution must still run the focused suite.
- T012-T016 should be sequenced after focused test failures identify the exact production gaps.

## Notes

- This task list covers one story only.
- MM-379 and the original Jira preset brief are preserved as the canonical Jira source.
