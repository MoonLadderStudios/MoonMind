# Tasks: Visible Step Attachments

**Input**: Design documents from `specs/207-visible-step-attachments/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style Create page tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Source Traceability**: MM-410; FR-001 through FR-012; acceptance scenarios 1-11; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-009.

**Requirement Status Summary**: FR-004 and FR-012 are missing; FR-002 and FR-003 are partial; FR-001 is implemented_verified; FR-005 through FR-011 are implemented_unverified and require verification coverage with fallback implementation if tests expose gaps.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm MM-410 orchestration input exists in `spec.md` (Input) and is preserved in `specs/207-visible-step-attachments/spec.md` (MM-410, FR-012)
- [X] T002 Confirm Moon Spec artifacts exist in `specs/207-visible-step-attachments/` for spec, plan, research, data model, contract, quickstart, checklist, and tasks (FR-012)

## Phase 2: Foundational

- [X] T003 Inspect current Create page attachment helpers and tests in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx` before editing (FR-002, FR-004, DESIGN-REQ-002)

## Phase 3: Story - Visible Step Attachments

**Summary**: As a task author, I can use a compact per-step + button to append policy-permitted attachments to the exact step they inform.

**Independent Test**: Enable attachment policy, add files through the step + control, verify append/dedupe and target ownership, then submit and inspect artifact upload ordering and structured step refs.

**Traceability**: FR-001 through FR-012; acceptance scenarios 1-11; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-009.

### Unit Tests

- [X] T004 Add failing test for image-only policy rendering an accessible `Add images to Step 1` + control and no generic visible file input in `frontend/src/entrypoints/task-create.test.tsx` (FR-002, FR-003, SC-002, DESIGN-REQ-002, DESIGN-REQ-008)
- [X] T005 Add failing test for mixed-content policy rendering generic `Add attachments to Step 1` copy in `frontend/src/entrypoints/task-create.test.tsx` (FR-002, DESIGN-REQ-005)
- [X] T006 Add failing test for repeated step + selections appending files instead of replacing prior selections in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, SC-003)
- [X] T007 Add failing test for exact duplicate local file identity dedupe on the same step in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, SC-003)
- [X] T008 Add verification test for policy-disabled hiding and text-only usability in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, SC-001)
- [X] T009 Add verification test for preview failure and remove action through the new + control path in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, FR-007, FR-008, SC-005)

### Integration Tests

- [X] T010 Add failing integration-style test for same-filename files on different steps preserving target ownership through submission in `frontend/src/entrypoints/task-create.test.tsx` (FR-005, FR-010, SC-004, SC-006)
- [X] T011 Add verification integration-style test for step reorder preserving files added through the + control in `frontend/src/entrypoints/task-create.test.tsx` (FR-005, DESIGN-REQ-001)
- [X] T012 Add verification integration-style test for upload-before-submit and owning `task.steps[n].inputAttachments` payload refs through the + control in `frontend/src/entrypoints/task-create.test.tsx` (FR-009, FR-010, DESIGN-REQ-006)
- [X] T013 Add verification test for persisted and new edit/rerun attachments coexisting with the new control path in `frontend/src/entrypoints/task-create.test.tsx` (FR-011, SC-007)

### Red-First Confirmation

- [X] T014 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and confirm T004-T007 and T010 fail for the expected missing + affordance and append/dedupe behavior before implementation

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [X] T015 If T008-T013 expose regressions, repair existing target ownership, validation, preview, upload, payload, or edit/rerun behavior in `frontend/src/entrypoints/task-create.tsx` without changing artifact or execution API contracts (FR-005 through FR-011)

### Implementation

- [X] T016 Add append/dedupe helper for step attachment file selections in `frontend/src/entrypoints/task-create.tsx` (FR-004)
- [X] T017 Replace the visible generic step file input with a compact per-step + button backed by a hidden file input in `frontend/src/entrypoints/task-create.tsx` (FR-002, FR-003)
- [X] T018 Add or update Create-page styles for stable compact step attachment + control in `frontend/src/styles/mission-control.css` (FR-002, FR-006)
- [X] T019 Preserve target-specific validation, preview, retry, remove, and persisted attachment rendering with the new control path in `frontend/src/entrypoints/task-create.tsx` (FR-005 through FR-011)
- [X] T020 Preserve MM-410 traceability in `specs/207-visible-step-attachments/tasks.md` as tasks are completed (FR-012)

### Story Validation

- [X] T021 Run focused UI validation `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- [X] T022 Run final repository unit validation `./tools/test_unit.sh`

## Phase 4: Polish And Verification

- [X] T023 Review `specs/207-visible-step-attachments/quickstart.md` against implemented behavior and update only if commands or expectations changed
- [X] T024 Run `/moonspec-verify` equivalent and record verification in `specs/207-visible-step-attachments/verification.md`

## Dependencies & Execution Order

- T001-T003 must complete before story work.
- T004-T013 are test-first tasks and must precede T016-T019.
- T014 confirms red-first behavior before implementation.
- T015 is conditional and only runs if verification tests expose existing behavior gaps.
- T016-T019 implement the missing behavior.
- T021 and T022 validate implementation before T024 verification.

## Parallel Opportunities

- T004 and T005 can be drafted together only if edits are coordinated in the same test file.
- T006 and T007 are closely related append/dedupe tests and should be kept adjacent.
- T018 can proceed after T017 defines the class names and does not depend on T016.

## Notes

- This task list covers one story only.
- MM-410 is preserved as the canonical Jira source key.
- No backend, artifact API, execution API, worker, or storage changes are planned.
