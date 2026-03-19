# Tasks: Manifest Phase 0 Temporal Alignment

**Input**: Design artifacts in `specs/086-manifest-phase0/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Runtime validation is required (`DOC-REQ-012`); run `./tools/test_unit.sh` for final verification.

**Organization**: Tasks are grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable within phase (different files, no blocking dependency)
- **[Story]**: Labels `[US1]`, `[US2]`, `[US3]`, `[US4]` map to spec user stories
- Every task includes concrete file paths

---

## Phase 1: Setup (Shared Context)

**Purpose**: Ensure spec artifacts and traceability are in place before runtime work.

- [x] T001 Verify `specs/086-manifest-phase0/contracts/requirements-traceability.md` has entries for DOC-REQ-001 through DOC-REQ-012 with mapped FRs and validation strategies.
- [x] T002 [P] Verify all existing manifest test fixtures in `tests/fixtures/manifests/` are compatible with current v0 schema and update if needed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Confirm core modules compile and imports resolve before augmenting tests.

**⚠️ CRITICAL**: Complete this phase before user-story work.

- [x] T003 Run `./tools/test_unit.sh` to confirm all existing manifest test suites pass without changes, establishing the green baseline (DOC-REQ-012).

**Checkpoint**: Green baseline established; all existing tests pass.

---

## Phase 3: User Story 1 - Manifest Compile and Plan Validation (Priority: P1) 🎯 MVP

**Goal**: Verify manifest compilation produces correct `CompiledManifestPlanModel` with stable node IDs, manifest hash, and required capabilities.

**Independent Test**: Run compile-focused tests and verify stable node IDs and plan structure.

### Tests for User Story 1

- [x] T004 [P] [US1] Add test for deterministic node ID stability in `tests/unit/workflows/temporal/test_manifest_ingest.py` — same manifest content must produce identical node IDs across multiple invocations (DOC-REQ-002).
- [x] T005 [P] [US1] Add test for manifest hash computation and version tracking in `tests/unit/workflows/agent_queue/test_manifest_contract.py` — verify `manifestHash` format is `sha256:{hex}` and `manifestVersion` is correctly set (DOC-REQ-003).
- [x] T006 [P] [US1] Add test for execution policy boundary enforcement in `tests/unit/workflows/temporal/test_manifest_ingest.py` — execution policy must be limited to `failurePolicy` and `maxConcurrency`; structural override attempts must be ignored (DOC-REQ-008).

### Implementation for User Story 1

- [x] T007 [US1] If any tests from T004–T006 fail, fix the implementation in `moonmind/workflows/temporal/manifest_ingest.py` and/or `moonmind/workflows/agent_queue/manifest_contract.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-008).

**Checkpoint**: US1 delivers verified compile and plan validation.

---

## Phase 4: User Story 2 - Manifest Workflow Updates and Interactive Control (Priority: P2)

**Goal**: Verify all 6 Temporal Updates produce correct state transitions in the `MoonMind.ManifestIngest` workflow.

**Independent Test**: Exercise each Update and verify state changes.

### Tests for User Story 2

- [x] T008 [P] [US2] Add test for `UpdateManifest` with `APPEND` mode in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify new nodes added without modifying existing nodes, and duplicate node ID collision is rejected (DOC-REQ-005).
- [x] T009 [P] [US2] Add test for `UpdateManifest` with `REPLACE_FUTURE` mode in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify pending/ready nodes replaced while running/succeeded/failed nodes preserved (DOC-REQ-005).
- [x] T010 [P] [US2] Add tests for `CancelNodes` with running tasks in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify running child workflow tasks receive cancellation (DOC-REQ-005, DOC-REQ-007).

### Implementation for User Story 2

- [x] T011 [US2] If any tests from T008–T010 fail, fix the Update handler implementations in `moonmind/workflows/temporal/manifest_ingest.py` (DOC-REQ-005, DOC-REQ-007).

**Checkpoint**: US2 delivers verified interactive control via all 6 Updates.

---

## Phase 5: User Story 3 - Fan-Out Execution with Failure Policy (Priority: P3)

**Goal**: Verify fan-out execution respects concurrency limits, dependency ordering, and failure policies.

**Independent Test**: Run manifest with multiple nodes and verify concurrency and failure behavior.

### Tests for User Story 3

- [x] T012 [P] [US3] Add test for concurrency limit enforcement in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify at most `maxConcurrency` child workflows run concurrently (DOC-REQ-009).
- [x] T013 [P] [US3] Add test for `fail_fast` failure policy in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify remaining pending/ready nodes are canceled when one node fails (DOC-REQ-009).
- [x] T014 [P] [US3] Add test for `continue` failure policy in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify independent nodes continue when one node fails (DOC-REQ-009).
- [x] T015 [P] [US3] Add test for dependency-ordered scheduling in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify nodes with unmet dependencies do not start (DOC-REQ-009).

### Implementation for User Story 3

- [x] T016 [US3] If any tests from T012–T015 fail, fix the fan-out execution logic in `moonmind/workflows/temporal/manifest_ingest.py` (DOC-REQ-009).

**Checkpoint**: US3 delivers verified fan-out with concurrency and failure policy enforcement.

---

## Phase 6: User Story 4 - Summary and Run-Index Artifact Generation (Priority: P4)

**Goal**: Verify finalization produces correct summary and run-index artifacts.

**Independent Test**: Complete a manifest ingest and verify artifact contents.

### Tests for User Story 4

- [x] T017 [P] [US4] Add test for summary artifact structure in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify `ManifestIngestSummaryModel` contains correct state, counts, and failed node IDs (DOC-REQ-006).
- [x] T018 [P] [US4] Add test for run-index artifact structure in `tests/unit/workflows/temporal/test_manifest_ingest.py` — verify per-node state, child workflow IDs, and result artifact refs (DOC-REQ-006).

### Implementation for User Story 4

- [x] T019 [US4] If any tests from T017–T018 fail, fix the artifact generation logic in `moonmind/workflows/temporal/manifest_ingest.py` (DOC-REQ-006).

**Checkpoint**: US4 delivers verified artifact generation.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T020 [P] Verify secret leak detection rejects all known raw token patterns and accepts safe references in `tests/unit/workflows/agent_queue/test_manifest_contract.py` (DOC-REQ-004, DOC-REQ-011).
- [x] T021 [P] Verify API response sanitization hides raw manifest YAML in `tests/unit/api/routers/test_manifests.py` (DOC-REQ-010, DOC-REQ-011).
- [x] T022 Run `./tools/test_unit.sh` and record full pass evidence in `specs/086-manifest-phase0/quickstart.md` (DOC-REQ-012).
- [x] T023 [P] Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and record result (DOC-REQ-012).
- [x] T024 [P] Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` and record result (DOC-REQ-012).


---

## Dependencies & Execution Order

### Phase Dependencies

1. Phase 1 (Setup) starts immediately.
2. Phase 2 (Foundational) depends on Phase 1; blocks all user stories.
3. Phase 3 (US1) depends on Phase 2 and is the MVP increment.
4. Phase 4 (US2) depends on Phase 2; can proceed after US1 or in parallel once baseline is stable.
5. Phase 5 (US3) depends on Phase 2; can proceed after US1.
6. Phase 6 (US4) depends on Phase 2; can proceed after US1.
7. Phase 7 (Polish) runs after all user stories are complete.

### Parallel Opportunities

- Phase 1: T001 and T002 can run in parallel.
- US1: T004, T005, T006 can all run in parallel.
- US2: T008, T009, T010 can all run in parallel.
- US3: T012, T013, T014, T015 can all run in parallel.
- US4: T017 and T018 can run in parallel.
- Polish: T020, T021 can run in parallel; T023, T024 can run in parallel after T022.

---

## Implementation Strategy

### MVP First (US1)

1. Complete Phase 1 and Phase 2.
2. Deliver US1 (Phase 3) and validate compile/plan independently.
3. Treat this checkpoint as the minimum shippable increment.

### Incremental Delivery

1. Ship US1 compile/plan validation.
2. Ship US2 interactive control verification.
3. Ship US3 fan-out execution verification.
4. Ship US4 artifact generation verification.
5. Finish with Phase 7 validation and traceability refresh.

### Quality Gates

1. Unit suite gate: `./tools/test_unit.sh`
2. Runtime scope gate (tasks): `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. Runtime scope gate (diff): `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

## Task Summary

- Total tasks: **24**
- User story tasks: **US1 = 4**, **US2 = 4**, **US3 = 5**, **US4 = 3**
- Parallelizable tasks (`[P]`): **16**
- Suggested MVP scope: **through Phase 3 (US1)**

## DOC-REQ Coverage Matrix

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
|---------|------------------------|---------------------|
| DOC-REQ-001 | T007 | T004, T022 |
| DOC-REQ-002 | T007 | T004, T022 |
| DOC-REQ-003 | T007 | T005, T022 |
| DOC-REQ-004 | T007 | T020, T022 |
| DOC-REQ-005 | T011 | T008, T009, T010, T022 |
| DOC-REQ-006 | T019 | T017, T018, T022 |
| DOC-REQ-007 | T011 | T010, T022 |
| DOC-REQ-008 | T007 | T006, T022 |
| DOC-REQ-009 | T016 | T012, T013, T014, T015, T022 |
| DOC-REQ-010 | T007 | T021, T022 |
| DOC-REQ-011 | T007 | T020, T021, T022 |
| DOC-REQ-012 | T003, T007, T011, T016, T019 | T022, T023, T024 |
