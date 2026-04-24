# Tasks: Expose Image Diagnostics and Failure Evidence

**Input**: Design documents from `/specs/203-expose-image-diagnostics/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: DESIGN-REQ-019 maps to FR-001 through FR-012, SC-001 through SC-008, and acceptance scenarios 1 through 5.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py tests/unit/moonmind/vision/test_service.py tests/unit/api/routers/test_temporal_artifacts.py tests/unit/workflows/tasks/test_task_contract.py`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/vision/test_context_artifacts.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing worker, vision, and test structure for the story.

- [X] T001 Confirm existing prepare materialization and task context structure in `moonmind/agents/codex_worker/worker.py` for FR-004, FR-006, FR-008
- [X] T002 Confirm existing vision context artifact generation in `moonmind/vision/service.py` for FR-005, FR-007
- [X] T003 Create MoonSpec feature artifacts in `specs/203-expose-image-diagnostics/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish diagnostic event shape and helper boundaries before story implementation.

- [X] T004 Add failing unit test for prepare download started/completed diagnostics with objective and step target metadata in `tests/unit/agents/codex_worker/test_attachment_materialization.py` covering FR-004, SC-003, DESIGN-REQ-019
- [X] T005 Add failing unit test for prepare download failed diagnostics including step target and sanitized error detail in `tests/unit/agents/codex_worker/test_attachment_materialization.py` covering FR-004, FR-009, SC-003, SC-006
- [X] T006 Add failing unit test for prepared task diagnostics exposing attachment manifest path and target-aware attachment metadata in `tests/unit/agents/codex_worker/test_attachment_materialization.py` covering FR-006, FR-008, SC-005
- [X] T007 Run `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py` to confirm T004-T006 fail for the expected reason

**Checkpoint**: Foundation tests demonstrate the missing diagnostics before implementation begins.

---

## Phase 3: Story - Image Input Diagnostics

**Summary**: As an operator debugging image-input failures, I need target-aware events, manifest/context path discovery, and step-specific failure evidence without scraping raw workflow history heuristics.

**Independent Test**: Run image input upload, validation, prepare-download, and context-generation flows for objective-scoped and step-scoped attachments, force representative failures, and verify emitted diagnostics identify the lifecycle event, attachment target, evidence paths, and affected step target without requiring raw workflow history inspection.

**Traceability**: FR-001 through FR-012, SC-001 through SC-008, DESIGN-REQ-019, MM-375

**Test Plan**:

- Unit: prepare download started/completed/failed diagnostics, task context evidence paths, target-aware metadata, context generation status diagnostics, disabled/failure cases.
- Integration: existing filesystem artifact coverage validates manifest/context path existence; final focused tests validate the story behavior at worker/service boundaries.

### Unit Tests (write first) ⚠️

- [X] T008 [P] Add failing unit test for image context generation started/completed diagnostics with objective and step context paths in `tests/unit/moonmind/vision/test_service.py` covering FR-005, FR-007, SC-004
- [X] T009 [P] Add failing unit test for disabled image context generation diagnostic status and traceability in `tests/unit/moonmind/vision/test_service.py` covering FR-005, FR-007, SC-004
- [X] T010 [P] Add failing unit test proving diagnostics do not infer target bindings from filenames or attachment order in `tests/unit/moonmind/vision/test_service.py` covering FR-010, SC-007
- [X] T011 Run `./tools/test_unit.sh tests/unit/moonmind/vision/test_service.py` to confirm T008-T010 fail for the expected reason

### Integration Tests (write first) ⚠️

- [X] T012 [P] Confirm or extend filesystem integration coverage for context index paths and statuses in `tests/integration/vision/test_context_artifacts.py` covering acceptance scenarios 4 and 5, FR-007, FR-010
- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/vision/test_context_artifacts.py -q` to confirm integration evidence for context paths

### Implementation

- [X] T014 Add compact image diagnostic event construction helpers to `moonmind/agents/codex_worker/worker.py` for FR-004, FR-006, FR-008, FR-009, FR-010
- [X] T015 Emit prepare download started/completed/failed diagnostics from `_materialize_input_attachments` in `moonmind/agents/codex_worker/worker.py` for FR-004, SC-003, SC-006
- [X] T016 Add prepared task diagnostics summary with manifest path, attachment count, attachment target metadata, and image diagnostic events in `moonmind/agents/codex_worker/worker.py` for FR-006, FR-008, SC-005
- [X] T017 Add image context diagnostic event data to `moonmind/vision/service.py` for started/completed/failed/disabled statuses, target identity, source refs, and context paths covering FR-005, FR-007, SC-004
- [X] T018 Keep vision diagnostics on the existing `VisionContextArtifactBundle` return contract without adding unnecessary exported types in `moonmind/vision/service.py` for FR-005, FR-007
- [X] T019 Run focused unit commands, fix failures, and complete story validation for FR-004 through FR-010 in `specs/203-expose-image-diagnostics/quickstart.md`

**Checkpoint**: Prepare-download, image-context, upload, and validation diagnostics are covered by focused tests and implementation evidence.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen completed story without changing core scope.

- [X] T020 Run traceability check `rg -n "MM-375|DESIGN-REQ-019" specs/203-expose-image-diagnostics`
- [X] T021 Run final unit validation `./tools/test_unit.sh`
- [X] T022 Add failing unit tests for attachment upload started/completed diagnostics with target metadata in `tests/unit/api/routers/test_temporal_artifacts.py` covering FR-001, FR-002, SC-001, DESIGN-REQ-019
- [X] T023 Add failing unit tests for attachment validation failed diagnostics with target metadata and failure detail in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-003, SC-002, DESIGN-REQ-019
- [X] T024 Run `./tools/test_unit.sh tests/unit/api/routers/test_temporal_artifacts.py tests/unit/workflows/tasks/test_task_contract.py` to confirm T022-T023 fail for the expected reason
- [X] T025 Implement attachment upload started/completed diagnostic publication in `api_service/api/routers/temporal_artifacts.py` or the existing artifact upload boundary covering FR-001, FR-002, SC-001
- [X] T026 Implement attachment validation failed diagnostic evidence at the task attachment contract boundary in `moonmind/workflows/tasks/task_contract.py` covering FR-003, SC-002
- [X] T027 Run focused upload diagnostic unit tests and update quickstart evidence in `specs/203-expose-image-diagnostics/quickstart.md`
- [X] T028 Run final unit validation `./tools/test_unit.sh`
- [X] T029 Run `/moonspec-verify` read-only verification against MM-375 source requirements and implemented evidence

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion
- **Story (Phase 3)**: Depends on Foundational tests
- **Polish (Phase 4)**: Depends on focused tests passing

### Within The Story

- Unit tests are written and confirmed failing before production code.
- Integration evidence is confirmed before implementation completion.
- Worker diagnostic helpers precede prepare-stage event emission and task context summary.
- Vision diagnostic events precede exports.
- Quickstart and verification follow passing focused tests, including the upload and validation diagnostic tasks added during alignment.

### Parallel Opportunities

- T008, T009, and T010 can be authored in parallel within one unit test file if edits are coordinated.
- T012 can be validated independently from worker prepare test edits.

---

## Implementation Strategy

1. Confirm existing worker and vision boundaries.
2. Write failing unit tests for prepare diagnostics and vision diagnostics.
3. Confirm red-first failures.
4. Implement compact diagnostic events at worker and vision service boundaries.
5. Run focused tests until passing.
6. Run traceability and final unit verification.
7. Complete the upload and validation diagnostic gap tasks identified by alignment.
8. Run `/moonspec-verify` coverage check against MM-375 and DESIGN-REQ-019.
