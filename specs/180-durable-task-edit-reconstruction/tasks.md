# Tasks: Durable Task Edit Reconstruction

**Input**: Design documents from `/specs/180-durable-task-edit-reconstruction/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped by phase around the single story "Reopen Any Supported Task Draft" so the work stays focused, traceable, and independently testable.

**Source Traceability**: The original request is preserved in `specs/180-durable-task-edit-reconstruction/spec.md`. Tasks reference FR-001 through FR-016, acceptance scenarios 1-7, edge cases, and SC-001 through SC-006.

**Test Commands**:

- Frontend focused tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Python focused tests: `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py tests/unit/workflows/temporal`
- Full unit verification: `./tools/test_unit.sh`
- Hermetic integration verification: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Establish feature scaffolding and contract anchors without changing runtime behavior.

- [X] T001 Confirm active feature artifacts and traceability references in specs/180-durable-task-edit-reconstruction/spec.md
- [X] T002 Confirm snapshot contract and reconstruction matrix in specs/180-durable-task-edit-reconstruction/contracts/original-task-input-snapshot.md
- [X] T003 [P] Add a dedicated durable task input snapshot reconstruction describe block for upcoming frontend tests in frontend/src/entrypoints/task-create.test.tsx
- [X] T004 [P] Add a dedicated durable task input snapshot API contract test section for upcoming create/detail/update/rerun tests in tests/contract/test_temporal_execution_api.py
- [ ] T005 [P] Create the Temporal boundary test module for compact snapshot refs in tests/unit/workflows/temporal/test_task_input_snapshot_boundary.py

---

## Phase 2: Foundational

**Purpose**: Define shared models, fixtures, and artifact helpers required before story implementation.

**CRITICAL**: No story implementation work begins until this phase is complete.

- [X] T006 Add failing schema/model tests for `TaskInputSnapshotDescriptor` and snapshot ref validation covering FR-005, FR-008, and SC-001 in tests/contract/test_temporal_execution_api.py
- [X] T007 Add failing artifact contract tests for `input.original_snapshot` link type, content type, metadata, retention, and immutability covering FR-001, FR-004, SC-003 in tests/contract/test_temporal_execution_api.py
- [ ] T008 Add failing frontend fixture builders for inline, artifact-backed, skill-only, template-derived, multi-step, attachment-bearing, degraded plan-only, and rerun-of-rerun drafts covering FR-002, FR-006, FR-010, FR-011, FR-012, FR-013 in frontend/src/entrypoints/task-create.test.tsx
- [ ] T009 Add failing Temporal boundary tests proving workflow/update/rerun payloads carry compact snapshot refs only covering FR-004, FR-015, and SC-004 in tests/unit/workflows/temporal/test_task_input_snapshot_boundary.py
- [ ] T010 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py tests/unit/workflows/temporal/test_task_input_snapshot_boundary.py` to confirm T006-T009 fail for missing implementation

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Reopen Any Supported Task Draft

**Summary**: As a MoonMind operator, I need Edit and Rerun to reopen the same create-form draft I originally submitted, even when the task used skills, templates, structured inputs, attachments, or artifact-backed instructions rather than plain inline text.

**Independent Test**: Create one execution for each supported create-form shape, load edit or rerun mode from its execution detail, and verify the reconstructed draft exactly matches the original editable submission fields while schedule-only fields are omitted and generated planner artifacts are never treated as original operator input.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, FR-016; acceptance scenarios 1-7; SC-001, SC-002, SC-003, SC-004, SC-005, SC-006.

### Unit Tests (write first)

- [ ] T011 Add failing frontend test for simple inline and artifact-backed task snapshot reconstruction covering scenarios 1-2 and SC-002 in frontend/src/entrypoints/task-create.test.tsx
- [X] T012 Add failing frontend test for skill-only task reconstruction with no instructions covering scenario 3, FR-010, and SC-006 in frontend/src/entrypoints/task-create.test.tsx
- [ ] T013 Add failing frontend test for template-derived task reconstruction including template inputs and customized steps covering scenario 4 and FR-011 in frontend/src/entrypoints/task-create.test.tsx
- [ ] T014 Add failing frontend test for multi-step attachments and per-step overrides covering scenario 5 and FR-012 in frontend/src/entrypoints/task-create.test.tsx
- [ ] T015 Add failing frontend test for degraded plan-artifact fallback copy and blocked submit covering scenario 6, FR-007, FR-009, FR-014, and SC-005 in frontend/src/entrypoints/task-create.test.tsx
- [ ] T016 Add failing frontend test for rerun-of-rerun using the latest submitted snapshot and lineage covering scenario 7 and FR-013 in frontend/src/entrypoints/task-create.test.tsx
- [ ] T017 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` to confirm T011-T016 fail for the expected missing snapshot-first implementation

### API, Artifact, And Workflow Boundary Tests (write first)

- [ ] T018 Add failing API create test proving every supported `MoonMind.Run` submission writes and links an `input.original_snapshot` artifact before returning execution detail covering FR-001, FR-002, FR-003, SC-001, and SC-003 in tests/contract/test_temporal_execution_api.py
- [ ] T019 Add failing API execution-detail test for compact `taskInputSnapshot` descriptor and disabled reasons covering FR-005, FR-008, and SC-001 in tests/contract/test_temporal_execution_api.py
- [ ] T020 Add failing API/update test proving `UpdateInputs` creates a replacement snapshot and preserves historical artifacts covering FR-004, FR-013, and SC-003 in tests/contract/test_temporal_execution_api.py
- [ ] T021 Add failing API/rerun test proving `RequestRerun` creates a new snapshot and lineage for reruns of reruns covering scenario 7, FR-013, and SC-003 in tests/contract/test_temporal_execution_api.py
- [ ] T022 Add failing workflow boundary test proving compact snapshot refs cross `MoonMind.Run` create/update/rerun boundaries and large draft content does not enter workflow history covering FR-004, FR-015, and SC-004 in tests/unit/workflows/temporal/test_task_input_snapshot_boundary.py
- [ ] T023 Run `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py tests/unit/workflows/temporal/test_task_input_snapshot_boundary.py` to confirm T018-T022 fail for the expected missing implementation

### Implementation

- [X] T024 Define `TaskInputSnapshotDescriptor` response model and snapshot metadata constants covering FR-005 and FR-008 in moonmind/schemas/temporal_models.py
- [X] T025 Implement original snapshot artifact creation and execution linking for task-shaped create requests covering FR-001, FR-002, FR-003, FR-004, and SC-003 in api_service/api/routers/executions.py
- [X] T026 Persist compact snapshot refs on execution records or memo/search-safe metadata and include them in execution serialization covering FR-005 and SC-001 in api_service/api/routers/executions.py
- [X] T027 Implement reconstruction capability gating and disabled reasons for missing/unreadable snapshots covering FR-008, FR-014, and SC-005 in api_service/api/routers/executions.py
- [X] T028 Implement replacement snapshot creation/linking for `UpdateInputs` and `RequestRerun` covering FR-013, FR-015, and scenarios 1-7 in api_service/api/routers/executions.py
- [ ] T029 Update Temporal execution service/update paths to carry compact snapshot refs without large draft content covering FR-004, FR-015, and SC-004 in moonmind/workflows/temporal/service.py
- [ ] T030 Update `MoonMind.Run` workflow input/update handling only as needed for compact snapshot refs and boundary compatibility covering FR-015 in moonmind/workflows/temporal/workflows/run.py
- [ ] T031 Extend frontend reconstruction types to include snapshot descriptors, snapshot payloads, source classification, structured skill inputs, steps, attachments, templates, dependencies, story-output, and proposal policy covering FR-002 and FR-006 in frontend/src/lib/temporalTaskEditing.ts
- [X] T032 Implement snapshot-first draft reconstruction and remove the instruction-required assumption for skill-only tasks covering FR-006, FR-010, and SC-006 in frontend/src/lib/temporalTaskEditing.ts
- [ ] T033 Implement task-create loading behavior for authoritative snapshots, degraded read-only fallback, disabled reasons, and blocked derived-output submission covering FR-005, FR-007, FR-009, FR-014, and SC-005 in frontend/src/entrypoints/task-create.tsx
- [ ] T034 Implement edit/rerun submit behavior that creates replacement snapshots and preserves lineage before update submission covering FR-013 and scenario 7 in frontend/src/entrypoints/task-create.tsx
- [ ] T035 Run focused frontend, API, and workflow tests from T017 and T023 and fix failures in frontend/src/lib/temporalTaskEditing.ts, frontend/src/entrypoints/task-create.tsx, api_service/api/routers/executions.py, moonmind/workflows/temporal/service.py, and moonmind/workflows/temporal/workflows/run.py

**Checkpoint**: The story is fully functional, covered by frontend, API, artifact, and Temporal boundary tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T036 [P] Update docs/Tasks/TaskEditingSystem.md with desired-state snapshot reconstruction semantics after runtime behavior exists
- [ ] T037 [P] Update docs/Temporal/ArtifactPresentationContract.md link-type table or presentation guidance for `input.original_snapshot` after runtime behavior exists
- [ ] T038 [P] Add or refine secret-safety assertions for snapshot metadata and disabled reasons in tests/contract/test_temporal_execution_api.py
- [ ] T039 Run quickstart validation commands from specs/180-durable-task-edit-reconstruction/quickstart.md
- [X] T040 Run full unit verification with `./tools/test_unit.sh`
- [X] T041 Run hermetic integration verification with `./tools/test_integration.sh` when Docker Compose is available, or document the exact environment blocker in specs/180-durable-task-edit-reconstruction/verification.md
- [ ] T042 Run `/moonspec-verify` for specs/180-durable-task-edit-reconstruction after implementation and tests pass

---

## Dependencies & Execution Order

- Setup T001-T005 can start immediately.
- Foundational tests T006-T010 block production implementation.
- Frontend unit tests T011-T016 and API/workflow tests T018-T022 must be written and confirmed failing before implementation.
- Schema/API implementation T024-T028 precedes frontend submit behavior T033-T034.
- Temporal service/workflow boundary work T029-T030 precedes boundary test green runs.
- Frontend reconstruction T031-T032 precedes task-create loading/submission behavior T033-T034.
- Polish T036-T042 starts only after focused tests pass.

## Parallel Opportunities

- T003-T005 can run in parallel.
- T011-T016 should be authored serially because they touch the same frontend test file.
- T018-T022 can run in parallel across API and Temporal test files.
- T036-T038 can run in parallel after implementation is green because they touch docs and focused security tests.

## Implementation Strategy

1. Add red tests for the reported no-instructions skill-only failure and each supported task shape.
2. Add API/artifact/Temporal red tests proving snapshots are written, linked, exposed, and passed by ref.
3. Implement compact schema and API descriptor surfaces.
4. Implement snapshot creation/linking on create, update, and rerun.
5. Update frontend reconstruction to prefer snapshots and classify derived fallback evidence.
6. Validate focused tests, then full unit and integration suites.
7. Update canonical docs only after runtime behavior exists.

## Coverage Summary

- FR-001 through FR-004: covered by T007, T018, T022, T025, T029, T030.
- FR-005 through FR-009: covered by T006, T015, T019, T024, T026, T027, T031, T033.
- FR-010 through FR-012: covered by T012-T014, T031, T032.
- FR-013: covered by T016, T020, T021, T028, T034.
- FR-014: covered by T015, T019, T027, T033.
- FR-015: covered by T009, T022, T029, T030.
- FR-016: covered by T011-T023, T035, T039-T042.
- Acceptance scenarios 1-7: covered by T011-T016 and T018-T022.
- SC-001 through SC-006: covered by T018-T023, T035, and final verification tasks.
