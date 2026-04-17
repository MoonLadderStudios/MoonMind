# Tasks: Targeted Image Attachment Submission

**Input**: Design documents from `/specs/195-targeted-image-attachment-submission/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: FR-001 through FR-010, SC-001 through SC-005, and DESIGN-REQ-001 through DESIGN-REQ-006/DESIGN-REQ-020 are covered by the unit, router, and contract test tasks below.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py`
- Integration tests: `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the existing task-shaped execution surfaces and feature artifacts are ready.

- [X] T001 Verify feature artifacts and active feature pointer for 195-targeted-image-attachment-submission in `.specify/feature.json` and `specs/195-targeted-image-attachment-submission/spec.md`
- [X] T002 Inspect existing attachment submission behavior in `frontend/src/entrypoints/task-create.tsx`, `moonmind/workflows/tasks/task_contract.py`, and `api_service/api/routers/executions.py`

---

## Phase 2: Foundational

**Purpose**: Establish the validation and API contract boundaries before story implementation.

- [X] T003 [P] Confirm no new storage or migrations are needed for snapshot-preserved attachment refs in `specs/195-targeted-image-attachment-submission/data-model.md` (FR-007, DESIGN-REQ-005)
- [X] T004 [P] Confirm the task-shaped attachment contract in `specs/195-targeted-image-attachment-submission/contracts/task-input-attachments.md` covers objective refs, step refs, and validation failures (FR-001-FR-010, DESIGN-REQ-001-DESIGN-REQ-006, DESIGN-REQ-020)

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Submit Targeted Image Attachments

**Summary**: As a task author, I want the Create page and task-shaped execution submission to bind image attachment refs to either the task objective or a specific step so that MoonMind.Run receives explicit lightweight references with durable target meaning.

**Independent Test**: Submit task-shaped execution payloads that include objective-level and step-level image attachment refs and verify the API accepts, normalizes, forwards, and snapshots them without raw bytes, data URLs, filename-derived targeting, or legacy attachment fields.

**Traceability**: FR-001-FR-010, SC-001-SC-005, DESIGN-REQ-001-DESIGN-REQ-006, DESIGN-REQ-020

**Test Plan**:

- Unit: task contract attachment-ref validation and execution router normalization/failure modes
- Integration: `/api/executions` contract coverage for workflow-start payload and original task input snapshot preservation

### Unit Tests (write first)

- [X] T005 [P] Add failing unit tests for valid objective and step `inputAttachments` refs in `tests/unit/workflows/tasks/test_task_contract.py` (FR-001, FR-002, FR-003, FR-006, DESIGN-REQ-001, DESIGN-REQ-003)
- [X] T006 [P] Add failing unit tests for missing metadata, raw content fields, image data URLs, and filename-collision target handling in `tests/unit/workflows/tasks/test_task_contract.py` (FR-004, FR-005, FR-008, FR-010, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-020)
- [X] T007 [P] Add failing router unit tests proving task-level and step-level `inputAttachments` are forwarded into `MoonMind.Run` initial parameters and invalid refs return 422 in `tests/unit/api/routers/test_executions.py` (FR-001-FR-006, FR-008)
- [X] T008 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py` and confirm T005-T007 fail for the expected missing validation/forwarding behavior

### Integration Tests (write first)

- [X] T009 Add failing contract test proving `/api/executions` persists objective and step attachment refs in the original task input snapshot artifact in `tests/contract/test_temporal_execution_api.py` (FR-007, SC-003, SC-004, DESIGN-REQ-005)
- [X] T010 Run `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py` and confirm T009 fails for the expected missing snapshot or workflow-input behavior

### Implementation

- [X] T011 Implement `TaskInputAttachmentRef` validation and attach it to task and step models in `moonmind/workflows/tasks/task_contract.py` (FR-001-FR-006, FR-008, FR-010, DESIGN-REQ-001-DESIGN-REQ-004, DESIGN-REQ-020)
- [X] T012 Implement execution-router attachment normalization and forwarding for task-level and step-level refs in `api_service/api/routers/executions.py` (FR-001-FR-007, SC-001, SC-003, DESIGN-REQ-001, DESIGN-REQ-005)
- [X] T013 Verify legacy `attachments`, `attachmentIds`, and `attachment_ids` remain non-canonical and unsupported for edit mutation paths in `moonmind/workflows/tasks/task_contract.py` and `api_service/api/routers/executions.py` (FR-009, SC-005, DESIGN-REQ-006)
- [X] T014 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py tests/contract/test_temporal_execution_api.py` and fix failures until targeted unit and contract tests pass

**Checkpoint**: The story is fully functional, covered by unit and contract tests, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Validate the single-story implementation without adding hidden scope.

- [X] T015 Run `./tools/test_unit.sh` for final unit-suite verification
- [X] T016 Review `docs/Tasks/ImageSystem.md` and generated artifacts to confirm source design coverage remains accurate without rewriting canonical docs (DESIGN-REQ-001-DESIGN-REQ-006, DESIGN-REQ-020)
- [X] T017 Run `/moonspec-verify` to validate the final implementation against the original MM-367 feature request

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on story tests and implementation passing

### Within The Story

- T005-T007 must be written before T011-T012.
- T008 must confirm expected red state before production implementation.
- T009 must be written before T012.
- T010 must confirm expected red state before production implementation.
- T011 and T012 are sequential because router normalization depends on the final ref shape.
- T014 must pass before T015 and T017.

### Parallel Opportunities

- T003 and T004 can run in parallel.
- T005, T006, and T007 can be authored in parallel because they touch different test files or independent cases.

---

## Implementation Strategy

1. Complete setup/foundational checks.
2. Add unit and contract tests first.
3. Confirm targeted tests fail for missing validation or forwarding.
4. Implement typed attachment-ref validation and router forwarding.
5. Run targeted tests until they pass.
6. Run full unit verification.
7. Run `/moonspec-verify`.
