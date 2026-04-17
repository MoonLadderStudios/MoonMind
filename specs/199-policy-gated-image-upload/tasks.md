# Tasks: Policy-Gated Image Upload and Submit

**Input**: Design documents from `specs/199-policy-gated-image-upload/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style Create page tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Source Traceability**: MM-380, FR-001 through FR-015, acceptance scenarios 1-7, SC-001 through SC-006, DESIGN-REQ-016, DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-006.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final unit verification: `./tools/test_unit.sh`
- Final MoonSpec verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm MM-380 orchestration input exists in `docs/tmp/jira-orchestration-inputs/MM-380-moonspec-orchestration-input.md` and is preserved in `specs/199-policy-gated-image-upload/spec.md` (FR-015)
- [X] T002 Confirm active feature artifacts exist in `specs/199-policy-gated-image-upload/`: spec, plan, research, data model, contract, quickstart, checklist, and tasks

## Phase 2: Foundational

- [X] T003 Identify current Create page attachment policy, validation, upload, submit, and failure-message surfaces in `frontend/src/entrypoints/task-create.tsx` and existing coverage in `frontend/src/entrypoints/task-create.test.tsx`

## Phase 3: Story - Policy-Gated Image Upload and Submit

**Summary**: As a task author, I can add permitted image inputs, see validation and upload failures at the correct target, and submit only after local images become artifact-backed structured attachment refs.

**Independent Test**: Load the Create page with disabled and image-only attachment policies, exercise objective and step image selection, validation, upload, failure, retry/remove, and submit flows, and inspect the execution payload.

**Traceability**: FR-001 through FR-015; scenarios 1-7; SC-001 through SC-006; DESIGN-REQ-016, DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-006.

### Unit Tests

- [X] T004 Add failing test for disabled attachment policy hiding objective and step attachment entry points while preserving text-only manual authoring in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-002, FR-014, SC-001, scenario 1, DESIGN-REQ-016, DESIGN-REQ-006)
- [X] T005 Add failing test for image-only policy using image-specific labels for objective and step attachment controls in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-014, SC-002, scenario 2, DESIGN-REQ-016)
- [X] T006 Add failing test for count, per-file size, total size, and content type validation before upload in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-006, FR-014, SC-003, scenario 3, DESIGN-REQ-016)
- [X] T007 Add failing test for target-scoped upload failure with retry/remove affordances and unrelated draft preservation in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, FR-007, FR-008, FR-009, FR-014, SC-004, scenario 4, DESIGN-REQ-023)
- [X] T008 Add failing test for preview failure preserving attachment metadata and remove actions without corrupting the draft in `frontend/src/entrypoints/task-create.test.tsx` (FR-007, FR-008, FR-014, SC-004, scenario 5, DESIGN-REQ-016, DESIGN-REQ-023)

### Integration Tests

- [X] T009 Add failing integration-style Create page submit test proving local objective and step images upload before create payload submission and produce `task.inputAttachments` and `task.steps[n].inputAttachments` refs in `frontend/src/entrypoints/task-create.test.tsx` (FR-010, FR-011, FR-012, FR-014, SC-005, scenario 6, DESIGN-REQ-021, DESIGN-REQ-025)
- [X] T010 Add failing integration-style submit-blocking tests for invalid, failed, incomplete, and uploading attachments in create/edit/rerun flows in `frontend/src/entrypoints/task-create.test.tsx` (FR-005, FR-006, FR-013, FR-014, SC-006, scenario 7, DESIGN-REQ-021, DESIGN-REQ-023)
- [X] T011 Confirm MM-380 traceability appears in feature artifacts and verification inputs in `specs/199-policy-gated-image-upload/spec.md`, `specs/199-policy-gated-image-upload/tasks.md`, and `docs/tmp/jira-orchestration-inputs/MM-380-moonspec-orchestration-input.md` (FR-015)

### Red-First Confirmation

- [X] T012 Run focused UI tests with `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and confirm T004-T010 fail before implementation

### Implementation

- [X] T013 Update attachment policy label derivation and rendered objective/step copy in `frontend/src/entrypoints/task-create.tsx` so image-only policy uses image-specific labels and disabled policy hides all entry points (FR-001, FR-002, FR-003)
- [X] T014 Update attachment validation and submit gating in `frontend/src/entrypoints/task-create.tsx` to validate count, per-file size, total size, content type, invalid state, failed state, incomplete state, and uploading state before create/edit/rerun submit (FR-004, FR-005, FR-006, FR-013)
- [X] T015 Update target-scoped attachment state and failure rendering in `frontend/src/entrypoints/task-create.tsx` so upload and preview failures remain visible at the affected objective or step target with remove/retry actions (FR-006, FR-007, FR-008, FR-009)
- [X] T016 Update upload-before-submit handling in `frontend/src/entrypoints/task-create.tsx` so local objective and step images are uploaded before create/edit/rerun payloads are sent and refs remain in canonical target fields only (FR-010, FR-011, FR-012)

### Story Validation

- [X] T017 Run focused UI validation `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- [X] T018 Run final repository unit validation `./tools/test_unit.sh`

## Phase 4: Polish And Verification

- [X] T019 Run `/moonspec-verify` equivalent and record verification in `specs/199-policy-gated-image-upload/verification.md`

## Dependencies & Execution Order

- T001-T003 establish inputs and implementation surfaces.
- T004-T011 must be written before implementation tasks.
- T012 must confirm the focused tests fail for the intended reasons before T013-T016 are considered complete.
- T013-T016 implement the story.
- T017 and T018 validate implementation before T019 verification.

## Parallel Example

```text
T004, T005, and T011 can be drafted in parallel because they cover different test assertions and artifact checks.
T006, T007, T008, T009, and T010 should be sequenced carefully because they share `frontend/src/entrypoints/task-create.test.tsx`.
```

## Notes

- This task list covers one story only.
- MM-380 is preserved as the canonical Jira source key.
- No backend schema or persistent storage changes are planned unless frontend tests expose an existing contract gap.
