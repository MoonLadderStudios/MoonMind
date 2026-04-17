# Tasks: Step-First Draft and Attachment Targets

**Input**: Design documents from `specs/196-step-first-draft-attachment-targets/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style Create page tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Source Traceability**: MM-377, FR-001 through FR-009, acceptance scenarios 1-6, SC-001 through SC-005, DESIGN-REQ-005 through DESIGN-REQ-009, DESIGN-REQ-024, DESIGN-REQ-025.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm MM-377 orchestration input exists in `docs/tmp/jira-orchestration-inputs/MM-377-moonspec-orchestration-input.md` and is preserved in `specs/196-step-first-draft-attachment-targets/spec.md` (FR-009)
- [X] T002 Create Moon Spec artifact directory `specs/196-step-first-draft-attachment-targets/` with spec, plan, research, data model, contract, quickstart, and tasks

## Phase 2: Foundational

- [X] T003 Identify Create page runtime surfaces in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`

## Phase 3: Story - Step-First Draft and Attachment Targets

**Summary**: As a task author, I can keep objective and step image inputs attached to explicit targets through normal Create page editing.

**Independent Test**: Submit a draft with objective and reordered step images and inspect the execution create payload.

**Traceability**: FR-001 through FR-009; scenarios 1-6; SC-001 through SC-005; DESIGN-REQ-005 through DESIGN-REQ-009, DESIGN-REQ-024, DESIGN-REQ-025.

### Unit Tests

- [X] T004 Add failing test for step attachment refs staying structured and out of instruction text in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-005, SC-002)
- [X] T005 Add failing test for objective-scoped attachment submission through `task.inputAttachments` in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-005, SC-001)
- [X] T006 Add failing test for step reorder preserving attachment ownership in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, SC-003)

### Integration Tests

- [X] T007 Add failing integration-style Create page payload test for objective attachment upload, artifact metadata, and `/api/executions` task payload in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-005, SC-001)
- [X] T008 Add failing integration-style Create page payload test for step reorder preserving owning `task.steps[n].inputAttachments` in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, DESIGN-REQ-008, SC-003)

### Red-First Confirmation

- [X] T009 Run focused UI tests and confirm T004-T008 fail before implementation

### Implementation

- [X] T010 Add objective attachment draft state and target-aware selected-file helpers in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-004, FR-007)
- [X] T011 Submit objective and step attachments through structured target refs only in `frontend/src/entrypoints/task-create.tsx` (FR-003, FR-004, FR-005)
- [X] T012 Render objective-scoped attachment controls and selected attachment removal actions in `frontend/src/entrypoints/task-create.tsx` (FR-004, FR-008)
- [X] T013 Preserve step attachment ownership across add/remove/reorder operations in `frontend/src/entrypoints/task-create.tsx` (FR-006)
- [X] T014 Route Jira image imports to the selected objective or step target in `frontend/src/entrypoints/task-create.tsx` (FR-004, FR-006)

### Story Validation

- [X] T015 Run focused UI validation `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- [X] T016 Run final repository unit validation `./tools/test_unit.sh`

## Phase 4: Polish

- [X] T017 Run `/moonspec-verify` equivalent and record verification in `specs/196-step-first-draft-attachment-targets/verification.md`

## Dependencies & Execution Order

- Setup and foundational review precede story implementation.
- T004-T008 must fail before T010-T014 are considered complete.
- T015 and T016 validate implementation before T017 verification.

## Notes

- This task list covers one story only.
- MM-377 is preserved as the canonical Jira source key.
