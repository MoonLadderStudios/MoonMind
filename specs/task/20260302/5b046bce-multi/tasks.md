# Tasks: Manifest Queue Alignment and Hardening

**Input**: Design documents from `/specs/028-manifest-queue/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Unit and regression validation is required by this feature and must run via `./tools/test_unit.sh`.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Task can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: Story label for user-story phases only (`[US1]`, `[US2]`, `[US3]`)
- Every task includes an exact file path

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish baseline context and traceability inputs before implementation work.

- [ ] T001 Review runtime baseline paths and behavior in api_service/api/schemas.py, api_service/api/routers/manifests.py, api_service/services/manifests_service.py, and moonmind/workflows/agent_queue/manifest_contract.py
- [ ] T002 [P] Capture baseline manifest-scope validation output using ./tools/test_unit.sh and record notes in specs/028-manifest-queue/checklists/requirements.md
- [ ] T003 [P] Extract DOC-REQ coverage targets from specs/028-manifest-queue/spec.md into specs/028-manifest-queue/contracts/requirements-traceability.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Align shared contracts and coverage artifacts required by all stories.

**CRITICAL**: Complete this phase before starting user-story implementation.

- [ ] T004 Normalize shared request/response contract definitions in specs/028-manifest-queue/data-model.md and specs/028-manifest-queue/contracts/manifests-api.md for ManifestRunRequest and ManifestRunResponse
- [ ] T005 [P] Align runtime-scope and strategy language in specs/028-manifest-queue/plan.md and specs/028-manifest-queue/research.md for production-code-plus-tests delivery
- [ ] T006 [P] Ensure one-row-per-requirement traceability with implementation surfaces in specs/028-manifest-queue/contracts/requirements-traceability.md for DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, and DOC-REQ-005
- [ ] T007 Define feature verification checklist entries in specs/028-manifest-queue/checklists/requirements.md for artifact consistency and runtime validation gating

**Checkpoint**: Shared design and coverage artifacts are synchronized and ready for story execution.

---

## Phase 3: User Story 1 - Fail-Fast Manifest Run Actions (Priority: P1) MVP

**Goal**: Reject unsupported `action` values at request parsing time while preserving supported/default behavior.

**Independent Test**: `ManifestRunRequest` accepts `plan`/`run` (including normalized case/whitespace) and rejects unsupported/non-string/null values before queue submission.

### Tests for User Story 1

- [ ] T008 [P] [US1] Add schema tests for action defaulting, normalization, and rejection in tests/unit/api/test_manifest_run_request_schema.py (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004)
- [ ] T009 [P] [US1] Add router tests ensuring unsupported action requests return HTTP 422 before service submission in tests/unit/api/routers/test_manifests.py (DOC-REQ-001, DOC-REQ-004)

### Implementation for User Story 1

- [ ] T010 [US1] Enforce strict action allowlist (`plan|run`) and normalization in api_service/api/schemas.py (DOC-REQ-001, DOC-REQ-004)
- [ ] T011 [US1] Preserve normalized action pass-through without fallback transforms in api_service/api/routers/manifests.py (DOC-REQ-001)
- [ ] T012 [US1] Validate User Story 1 via ./tools/test_unit.sh focused on manifest request schema and manifests router coverage (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004)

**Checkpoint**: Unsupported actions fail fast and never reach queue submission logic.

---

## Phase 4: User Story 2 - Spec Artifacts Match Runtime Reality (Priority: P1)

**Goal**: Keep `specs/028-manifest-queue` artifacts accurate for current code paths, contracts, and project strategy.

**Independent Test**: Cross-artifact review confirms spec, plan, contracts, and quickstart paths/semantics match current implementation files and behavior.

### Implementation for User Story 2

- [ ] T013 [P] [US2] Update current-state requirements and acceptance criteria in specs/028-manifest-queue/spec.md (DOC-REQ-002)
- [ ] T014 [P] [US2] Align implementation strategy and structure references in specs/028-manifest-queue/plan.md (DOC-REQ-002, DOC-REQ-004)
- [ ] T015 [P] [US2] Align validation semantics and entity definitions in specs/028-manifest-queue/data-model.md (DOC-REQ-002, DOC-REQ-001, DOC-REQ-005)
- [ ] T016 [P] [US2] Align endpoint request/response/error contracts in specs/028-manifest-queue/contracts/manifests-api.md (DOC-REQ-002, DOC-REQ-001, DOC-REQ-005)
- [ ] T017 [US2] Align operator verification flow with required test wrapper in specs/028-manifest-queue/quickstart.md (DOC-REQ-002, DOC-REQ-003)
- [ ] T018 [US2] Update full requirement traceability mappings in specs/028-manifest-queue/contracts/requirements-traceability.md (DOC-REQ-002)

### Validation for User Story 2

- [ ] T019 [US2] Validate artifact file-path and behavior consistency in specs/028-manifest-queue/checklists/requirements.md (DOC-REQ-002)
- [ ] T020 [US2] Validate DOC-REQ task coverage in specs/028-manifest-queue/tasks.md so each DOC-REQ has implementation and validation tasks (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)

**Checkpoint**: Spec artifacts and runtime implementation references are synchronized.

---

## Phase 5: User Story 3 - Regression Coverage for Alignment Rules (Priority: P2)

**Goal**: Protect action-validation hardening and queue metadata compatibility with focused regression tests.

**Independent Test**: `./tools/test_unit.sh` confirms action validation behavior and manifest queue metadata compatibility tests pass.

### Tests for User Story 3

- [ ] T021 [P] [US3] Add manifest queue compatibility regression cases in tests/unit/workflows/agent_queue/test_manifest_contract.py for manifestHash, manifestVersion, requiredCapabilities, and secret-hygiene outputs (DOC-REQ-005)
- [ ] T022 [P] [US3] Add manifests API queue metadata regression cases in tests/unit/api/routers/test_manifests.py for queue.requiredCapabilities and queue.manifestHash responses (DOC-REQ-005)

### Implementation for User Story 3

- [ ] T023 [US3] Preserve queue metadata compatibility across api_service/services/manifests_service.py and moonmind/workflows/agent_queue/manifest_contract.py while adding any required hardening adjustments (DOC-REQ-005)
- [ ] T024 [US3] Execute full regression validation using ./tools/test_unit.sh and record manifest-scope results in specs/028-manifest-queue/checklists/requirements.md (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)

**Checkpoint**: Regression coverage protects runtime hardening and compatibility behavior.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and delivery-readiness checks across all stories.

- [ ] T025 [P] Run runtime tasks scope gate with .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime for specs/028-manifest-queue/tasks.md
- [ ] T026 [P] Perform final cross-artifact sanity pass in specs/028-manifest-queue/spec.md, specs/028-manifest-queue/plan.md, and specs/028-manifest-queue/tasks.md for consistent language and task ordering

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Starts immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2 completion.
- **Phase 4 (US2)**: Depends on Phase 2 completion; can run in parallel with US1 when staffed.
- **Phase 5 (US3)**: Depends on Phase 2 completion and should consume outputs from US1/US2.
- **Phase 6 (Polish)**: Depends on completion of selected user stories.

### User Story Dependencies

- **US1 (P1)**: Can start after foundational phase; no dependency on other stories.
- **US2 (P1)**: Can start after foundational phase; independent but informs final artifact validation.
- **US3 (P2)**: Depends on US1 validation behavior and US2 artifact/contract alignment.

### Within Each User Story

- Tests first (where present), then implementation, then story-level validation command execution.

## Parallel Opportunities

- T002 and T003 can run in parallel in Setup.
- T005 and T006 can run in parallel in Foundational.
- T008 and T009 can run in parallel in US1 tests.
- T013 through T016 can run in parallel in US2 implementation.
- T021 and T022 can run in parallel in US3 tests.
- T025 and T026 can run in parallel in final polish.

## Parallel Example: User Story 1

```bash
Task: "T008 [US1] Add schema tests in tests/unit/api/test_manifest_run_request_schema.py"
Task: "T009 [US1] Add router tests in tests/unit/api/routers/test_manifests.py"
```

## Parallel Example: User Story 2

```bash
Task: "T013 [US2] Update specs/028-manifest-queue/spec.md"
Task: "T014 [US2] Update specs/028-manifest-queue/plan.md"
Task: "T016 [US2] Update specs/028-manifest-queue/contracts/manifests-api.md"
```

## Parallel Example: User Story 3

```bash
Task: "T021 [US3] Add compatibility regression cases in tests/unit/workflows/agent_queue/test_manifest_contract.py"
Task: "T022 [US3] Add metadata regression cases in tests/unit/api/routers/test_manifests.py"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup).
2. Complete Phase 2 (Foundational).
3. Complete Phase 3 (US1).
4. Validate fail-fast behavior through T012.

### Incremental Delivery

1. Deliver US1 hardening (MVP).
2. Deliver US2 artifact alignment and traceability.
3. Deliver US3 regression hardening.
4. Finish cross-cutting polish and scope gates.

### Parallel Team Strategy

1. Team aligns shared foundations (Phases 1-2).
2. One engineer executes US1 runtime hardening while another executes US2 artifact alignment.
3. US3 regression work starts after US1/US2 outputs stabilize.
