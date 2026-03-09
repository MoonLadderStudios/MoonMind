# Tasks: Wire Temporal Artifacts

**Input**: Design documents from `/specs/001-wire-temporal-artifacts/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Create project structure for tests in `tests/unit/workflows/temporal/test_run_artifacts.py`
- [x] T002 Create project structure for tests in `tests/unit/workflows/temporal/test_manifest_ingest_artifacts.py`
- [x] T003 Create workflow file structure in `moonmind/workflows/temporal/workflows/manifest_ingest.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

- [x] T004 Review and update `moonmind/workflows/temporal/artifacts.py` to ensure `ArtifactService` can support new link types and sizes (Implements DOC-REQ-003)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Replace Large Payloads with References in MoonMind.Run (Priority: P1) 🎯 MVP

**Goal**: MoonMind.Run workflow stores large outputs in the artifact store and keeps only a reference in history.

**Independent Test**: Trigger a MoonMind.Run workflow and inspect its Temporal history to ensure no large strings/blobs are returned by activities.

### Tests for User Story 1

- [x] T005 [P] [US1] Create validation tests for MoonMind.Run artifacts in `tests/unit/workflows/temporal/test_run_artifacts.py` (Validates DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)

### Implementation for User Story 1

- [x] T006 [US1] Update `plan.generate` and workflow initialization activities in `moonmind/workflows/temporal/activity_runtime.py` to use `ArtifactService` and return `plan_ref` and `input_ref` (Implements DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-005)
- [x] T007 [US1] Update `sandbox.run_command` and finalization activities in `moonmind/workflows/temporal/activity_runtime.py` to return `diagnostics_ref`, `logs_ref`, and `run_index_ref` (Implements DOC-REQ-001, DOC-REQ-003, DOC-REQ-005)
- [x] T008 [US1] Update `moonmind/workflows/temporal/workflows/run.py` to expect references from activities instead of raw data (Implements DOC-REQ-001, DOC-REQ-004, DOC-REQ-005)

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Replace Large Payloads with References in ManifestIngest (Priority: P1)

**Goal**: ManifestIngest workflow uses artifact references for all large outputs.

**Independent Test**: Run a ManifestIngest workflow and verify that the Temporal execution history contains only reference pointers.

### Tests for User Story 2

- [x] T009 [P] [US2] Create validation tests for ManifestIngest artifacts in `tests/unit/workflows/temporal/test_manifest_ingest_artifacts.py` (Validates DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)

### Implementation for User Story 2

- [x] T010 [US2] Create `MoonMind.ManifestIngest` workflow in `moonmind/workflows/temporal/workflows/manifest_ingest.py` (Implements DOC-REQ-001, DOC-REQ-004, DOC-REQ-005)
- [x] T011 [US2] Implement `manifest.process` activity in `moonmind/workflows/temporal/manifest_ingest.py` to store data and return `summary_ref` and `nodes_ref` (Implements DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - Validate Production Code and Tests (Priority: P1)

**Goal**: Ensure changes are backed by production runtime code and validation tests.

**Independent Test**: Ensure CI passes and tests explicitly verify that large payloads are not leaking into Temporal histories.

### Tests for User Story 3

- [x] T012 [P] [US3] Execute `pytest tests/unit/workflows/temporal/test_run_artifacts.py` to validate US1 (Validates DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)
- [x] T013 [P] [US3] Execute `pytest tests/unit/workflows/temporal/test_manifest_ingest_artifacts.py` to validate US2 (Validates DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)

### Implementation for User Story 3

- [x] T014 [US3] Ensure CI is triggered and successfully passes on branch `001-wire-temporal-artifacts` (Implements DOC-REQ-005)

**Checkpoint**: All user stories should now be independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T015 Run formatting and linting on `moonmind/workflows/temporal/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2)
- **User Story 2 (P1)**: Can start after Foundational (Phase 2)
- **User Story 3 (P1)**: Validates US1 and US2, should be run after they are completed

---

## Implementation Strategy

### Incremental Delivery

1. Complete Setup + Foundational
2. Add User Story 1 → Test independently
3. Add User Story 2 → Test independently
4. Add User Story 3 → Verify all
5. Polish and final checks