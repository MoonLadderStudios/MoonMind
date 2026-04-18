# Tasks: Create Task Publish Controls

**Input**: Design documents from `specs/208-create-task-publish-controls/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style Create page tests are REQUIRED. Write tests first, confirm they fail for the intended reason when implementation is absent, then implement production code until they pass.

**Source Traceability**: MM-412, original Jira preset brief preserved in `spec.md`, FR-001 through FR-013, acceptance scenarios 1-10, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-010.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm the canonical MM-412 Jira preset brief is preserved in `docs/tmp/jira-orchestration-inputs/MM-412-moonspec-orchestration-input.md` and `specs/208-create-task-publish-controls/spec.md` (MM-412, SC-007)
- [X] T002 Confirm Moon Spec artifact set exists in `specs/208-create-task-publish-controls/` with spec, plan, research, data model, UI contract, quickstart, and tasks

## Phase 2: Foundational

- [X] T003 Inspect current publish controls in `frontend/src/entrypoints/task-create.tsx` and existing tests in `frontend/src/entrypoints/task-create.test.tsx` before story work (FR-001 through FR-013, DESIGN-REQ-001 through DESIGN-REQ-010)

## Phase 3: Story - Consolidate Publish Controls

**Summary**: As a task author, I want repository, branch, publish mode, and merge automation intent authored together in the Steps card so publishing choices are grouped with the execution plan they affect.

**Independent Test**: Open the Create page for create, edit, and rerun flows; select each publish option from the Steps card control group; verify there is no separate merge automation checkbox, resolver-style restrictions hold, legacy PR-with-merge drafts hydrate to the combined option, and submitted payloads preserve existing publish and merge automation semantics.

**Traceability**: MM-412; FR-001 through FR-013; acceptance scenarios 1-10; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-010.

### Unit Tests

- [X] T004 Add failing UI test in `frontend/src/entrypoints/task-create.test.tsx` proving Execution context has no standalone `Enable merge automation` checkbox and Publish Mode remains in the Steps card (FR-001, FR-002, FR-003, SC-001, SC-002, DESIGN-REQ-001, DESIGN-REQ-002)
- [X] T005 Add failing UI test in `frontend/src/entrypoints/task-create.test.tsx` proving ordinary PR-publishing tasks expose a visible PR-with-merge Publish Mode option with accessible copy (FR-004, FR-009, FR-010, SC-006, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-009)
- [X] T006 Add failing UI tests in `frontend/src/entrypoints/task-create.test.tsx` proving resolver-style skill and preset constraints hide or clear the PR-with-merge Publish Mode option (FR-007, FR-008, SC-005, DESIGN-REQ-004, DESIGN-REQ-008)
- [X] T007 Add failing hydration tests in `frontend/src/entrypoints/task-create.test.tsx` proving edit/rerun stored None, Branch, PR, and PR-with-merge states map to visible Publish Mode selections (FR-012, SC-004, DESIGN-REQ-006)

### Integration Tests

- [X] T008 Add failing request-shape tests in `frontend/src/entrypoints/task-create.test.tsx` proving None, Branch, PR, and PR with Merge Automation submit the exact existing publish and merge automation payload semantics (FR-005, FR-006, SC-003, DESIGN-REQ-003, DESIGN-REQ-007)
- [X] T009 Add failing request-shape tests in `frontend/src/entrypoints/task-create.test.tsx` proving invalid or constrained combined selections omit merge automation and do not change Jira Orchestrate behavior (FR-007, FR-008, FR-011, SC-005, DESIGN-REQ-004, DESIGN-REQ-005)

### Red-First Confirmation

- [X] T010 Run focused tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and record that new MM-412 tests fail for the expected missing combined-option behavior before production edits

### Implementation

- [X] T011 Implement a UI-layer Publish Mode selection mapping in `frontend/src/entrypoints/task-create.tsx` so PR with Merge Automation is visible but submits existing PR publish plus merge automation semantics (FR-004, FR-005, FR-006, DESIGN-REQ-003, DESIGN-REQ-007)
- [X] T012 Remove the standalone Execution context merge automation checkbox from `frontend/src/entrypoints/task-create.tsx` and keep explanatory merge automation copy attached to the combined Publish Mode behavior (FR-002, FR-010, DESIGN-REQ-005)
- [X] T013 Apply resolver-style and preset publish constraints to the combined Publish Mode selection in `frontend/src/entrypoints/task-create.tsx`, including clearing invalid stale PR-with-merge selections visibly (FR-007, FR-008, DESIGN-REQ-004, DESIGN-REQ-008)
- [X] T014 Update edit/rerun hydration in `frontend/src/entrypoints/task-create.tsx` so stored PR publishing plus merge automation reconstructs to PR with Merge Automation when allowed (FR-012, DESIGN-REQ-006)
- [X] T015 Preserve compact Steps-card Branch and Publish Mode accessibility and responsive grouping in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-003, FR-009, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-009)

### Story Validation

- [X] T016 Run focused validation `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and fix failures until MM-412 story coverage passes

## Phase 4: Polish And Verification

- [X] T017 Update `docs/UI/CreatePage.md` so merge automation is described as a PR-specific Publish Mode choice rather than a standalone Execution context checkbox (FR-013, SC-007, DESIGN-REQ-010)
- [X] T018 Run final unit validation `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [X] T019 Run final `/moonspec-verify` work and record verification in `specs/208-create-task-publish-controls/verification.md`

## Dependencies & Execution Order

- T001-T003 must complete before story tests.
- T004-T009 must be written before T011-T015 production implementation.
- T010 records red-first evidence before production edits.
- T011-T015 implement the UI behavior.
- T016 validates the story before documentation polish and final verification.
- T017 updates canonical desired-state docs after behavior is implemented.
- T018 and T019 are final verification gates.

## Parallel Examples

- T004 and T005 can be drafted together only if edits remain in separate test blocks and are reconciled before T010.
- T017 can be prepared after implementation behavior is clear, but it should not replace the required production tests.

## Notes

- This task list covers one story only.
- MM-412 and the original Jira preset brief are preserved as canonical source input.
- The implementation must not introduce a new backend publish-mode enum or worker contract.
