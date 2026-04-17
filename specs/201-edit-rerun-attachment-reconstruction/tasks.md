# Tasks: Edit and Rerun Attachment Reconstruction

**Input**: Design documents from `/specs/201-edit-rerun-attachment-reconstruction/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration/contract tests are REQUIRED. Existing tests from the attachment-binding implementation are acceptable evidence when they map directly to MM-382 requirements.

**Organization**: Tasks are grouped by phase around the single MM-382 user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: FR-001 through FR-012; acceptance scenarios 1-5; SC-001 through SC-005; DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-023, DESIGN-REQ-025.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx && pytest tests/unit/api/routers/test_executions.py -q`
- Integration/contract tests: `pytest tests/contract/test_temporal_execution_api.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, success, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm MM-382 input, existing related artifacts, and runtime implementation surfaces.

- [X] T001 Classify `docs/tmp/jira-orchestration-inputs/MM-382-moonspec-orchestration-input.md` as a single-story runtime feature request covering MM-382
- [X] T002 Inspect existing Moon Spec artifacts and identify `specs/196-preserve-attachment-bindings/` as related but keyed to MM-369, requiring a new MM-382 canonical spec directory
- [X] T003 Inspect `docs/UI/CreatePage.md` sections 13 and 14 for DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-005, and DESIGN-REQ-006
- [X] T004 Inspect current implementation and tests in `frontend/src/lib/temporalTaskEditing.ts`, `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create.test.tsx`, `tests/unit/api/routers/test_executions.py`, and `tests/contract/test_temporal_execution_api.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish source authority and verification mapping before implementation validation.

**CRITICAL**: No story validation can be claimed until this phase is complete.

- [X] T005 Create MM-382 spec artifacts under `specs/201-edit-rerun-attachment-reconstruction/` preserving the Jira issue key in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/edit-rerun-attachment-reconstruction.md`, `quickstart.md`, and `tasks.md`
- [X] T006 Map each in-scope MM-382 source requirement to functional requirements and test evidence in `specs/201-edit-rerun-attachment-reconstruction/spec.md`
- [X] T007 Confirm no unresolved story-critical clarification remains in `specs/201-edit-rerun-attachment-reconstruction/spec.md`

**Checkpoint**: Foundation ready - story validation and implementation gap checks can now begin.

---

## Phase 3: Story - Edit and Rerun Attachment Reconstruction

**Summary**: As a task author, I can edit or rerun an existing MoonMind.Run and get a reconstructed draft that preserves objective text, attachments, templates, dependencies, runtime options, and untouched attachment refs unless I change them.

**Independent Test**: Create an execution snapshot with objective attachments, step attachments, runtime settings, publish settings, template state, and dependencies; reconstruct it for edit and rerun; verify unchanged attachment refs and editable fields survive, explicit add/remove changes stay target-scoped, and incomplete binding data fails explicitly.

**Traceability**: FR-001 through FR-012; acceptance scenarios 1-5; SC-001-SC-005; DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-023, DESIGN-REQ-025.

**Test Plan**:

- Unit: frontend draft transformation and Create-page state/submission behavior; backend action availability for missing original snapshots.
- Integration/contract: task-shaped execution contract coverage for persisted snapshot body containing objective and step `inputAttachments`.

### Unit Tests (write first or verify existing red-first evidence)

- [X] T008 [P] Verify existing frontend unit test for `buildTemporalSubmissionDraftFromExecution` preserving objective and step `inputAttachments` from the authoritative snapshot in `frontend/src/entrypoints/task-create.test.tsx` covers FR-001, FR-002, FR-005, SC-001, and DESIGN-REQ-019
- [X] T009 [P] Verify existing frontend unit test for explicit reconstruction failure when snapshot attachment refs cannot be bound from `draft.task` in `frontend/src/entrypoints/task-create.test.tsx` covers FR-003, FR-010, SC-005, and DESIGN-REQ-023
- [X] T010 [P] Verify existing frontend unit test for Create-page edit/rerun submission retaining unchanged persisted attachment refs in `frontend/src/entrypoints/task-create.test.tsx` covers FR-006, FR-008, FR-009, SC-002, and DESIGN-REQ-021
- [X] T011 [P] Verify existing backend unit coverage for original snapshot requirements in `tests/unit/api/routers/test_executions.py` covers FR-003 and DESIGN-REQ-023

### Integration/Contract Tests (write first or verify existing red-first evidence)

- [X] T012 Verify existing contract coverage in `tests/contract/test_temporal_execution_api.py` asserts snapshot task body preserves objective and step `inputAttachments` covering FR-008, FR-009, DESIGN-REQ-005, and DESIGN-REQ-006

### Implementation

- [X] T013 Verify `frontend/src/lib/temporalTaskEditing.ts` reconstructs attachment refs from authoritative task snapshots and fails explicitly when compact refs cannot be bound, covering FR-001, FR-002, FR-003, FR-010
- [X] T014 Verify `frontend/src/entrypoints/task-create.tsx` hydrates persisted objective and step attachments distinctly from local files, covering FR-005, FR-006, DESIGN-REQ-020, DESIGN-REQ-021
- [X] T015 Verify `frontend/src/entrypoints/task-create.tsx` preserves unchanged refs and supports explicit add/remove/replace behavior in submitted payloads, covering FR-007, FR-008, FR-009, FR-011
- [X] T016 Verify `tests/unit/api/routers/test_executions.py` and `tests/contract/test_temporal_execution_api.py` provide backend snapshot and API contract evidence for FR-003, FR-008, FR-009
- [X] T017 Run focused unit commands `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx && pytest tests/unit/api/routers/test_executions.py -q`, then fix failures
- [X] T018 Run focused contract command `pytest tests/contract/test_temporal_execution_api.py -q`, then fix failures
- [X] T019 Validate the single MM-382 story against acceptance scenarios 1-5 using `frontend/src/entrypoints/task-create.test.tsx` and `tests/contract/test_temporal_execution_api.py`

**Checkpoint**: The story is fully functional, covered by unit and contract tests, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Validate the completed story without adding hidden scope.

- [X] T020 [P] Update `specs/201-edit-rerun-attachment-reconstruction/quickstart.md` if final commands or blockers differ from planned validation
- [X] T021 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification
- [X] T022 Run `./tools/test_integration.sh` when Docker is available, or record the exact Docker socket blocker
- [X] T023 Run `/moonspec-verify` equivalent read-only verification and record evidence in `specs/201-edit-rerun-attachment-reconstruction/verification.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story validation
- **Story (Phase 3)**: Depends on Foundational completion
- **Polish (Phase 4)**: Depends on focused unit and contract evidence

### Within The Story

- Test evidence tasks T008-T012 must be mapped before implementation completion tasks T013-T016 are claimed.
- Focused validation T017 and T018 must pass or record exact blockers before T019.
- Final validation T021 and feasible integration validation T022 must run or record exact blockers before T023.

### Parallel Opportunities

- T003 and T004 can run in parallel.
- T008, T009, T010, T011, and T012 can be verified in parallel because they inspect distinct assertions.
- T020 can run after focused validation while final verification commands are queued.

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Preserve MM-382 as the canonical source in spec artifacts.
2. Reuse existing attachment-binding implementation only where tests and code map directly to MM-382 requirements.
3. Run focused frontend, backend unit, and contract tests.
4. Make bounded code/test fixes only if focused verification reveals an MM-382 gap.
5. Run final unit and feasible integration verification.
6. Run `/moonspec-verify` equivalent read-only verification and record the verdict.
