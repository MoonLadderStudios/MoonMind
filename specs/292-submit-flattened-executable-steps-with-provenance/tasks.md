# Tasks: Submit Flattened Executable Steps with Provenance

**Input**: Design documents from `/work/agent_jobs/mm:d840afab-0992-4107-91d1-bcee9ae1b804/repo/specs/292-submit-flattened-executable-steps-with-provenance/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/flattened-executable-provenance.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around exactly one story: flatten preset-derived executable submissions while preserving provenance for audit and reconstruction.

**Source Traceability**: MM-579 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-010, SCN-001 through SCN-005, SC-001 through SC-006, and DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-015, DESIGN-REQ-016, and DESIGN-REQ-023.

**Requirement Status Summary**: `plan.md` classifies 13 rows as `partial`, 6 as `implemented_unverified`, and 7 as `implemented_verified`. This task list adds code-and-test tasks for partial rows, verification-first tests with conditional fallback implementation for implemented-unverified rows, and final validation for already-verified rows.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- Integration tests: `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on an incomplete task.
- Every task references exact file paths and requirement, scenario, success criterion, or source IDs where applicable.

## Phase 1: Setup

**Purpose**: Confirm the active feature artifacts and existing test harnesses are ready before story work.

- [X] T001 Verify active feature pointer `.specify/feature.json` resolves to `specs/292-submit-flattened-executable-steps-with-provenance` for MM-579 traceability (FR-010, SC-006)
- [X] T002 Confirm required upstream artifacts exist in `specs/292-submit-flattened-executable-steps-with-provenance/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/flattened-executable-provenance.md`, and `quickstart.md` (FR-010, SC-006)
- [X] T003 [P] Confirm backend unit test targets are available in `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/api/routers/test_task_proposals.py`, and `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [X] T004 [P] Confirm Create page integration-boundary test target is available in `frontend/src/entrypoints/task-create.test.tsx`

---

## Phase 2: Foundational

**Purpose**: Establish the exact current contract surfaces before adding MM-579 tests.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T005 Inspect current executable step and provenance validation in `moonmind/workflows/tasks/task_contract.py` against `specs/292-submit-flattened-executable-steps-with-provenance/contracts/flattened-executable-provenance.md` (FR-002, FR-003, DESIGN-REQ-006, DESIGN-REQ-015)
- [X] T006 Inspect current proposal promotion and preview behavior in `moonmind/workflows/task_proposals/service.py` and `api_service/api/routers/task_proposals.py` for stored flat-payload validation and provenance display (FR-004, FR-007, FR-008, DESIGN-REQ-016, DESIGN-REQ-023)
- [X] T007 Inspect current preset preview/apply submission mapping in `frontend/src/entrypoints/task-create.tsx` for generated Tool/Skill flattening, source metadata preservation, and explicit refresh behavior (FR-001, FR-003, FR-006, FR-009, DESIGN-REQ-004)
- [X] T008 Inspect runtime planner behavior in `moonmind/workflows/temporal/worker_runtime.py` to confirm provenance is carried as metadata and not used for live preset lookup (FR-005, SC-003)

**Checkpoint**: Foundation ready - test and implementation work can begin.

---

## Phase 3: Story - Flatten Preset-Derived Executable Submissions

**Summary**: As an operator, I can submit tasks that contain only executable Tool and Skill steps by default, while preset-derived steps retain provenance for audit and reconstruction without runtime lookup.

**Independent Test**: Apply a preset into executable Tool and Skill steps, submit or promote the resulting task, and verify that execution accepts only the flat Tool and Skill steps, provenance remains visible, no live preset lookup is required for correctness, and any catalog refresh requires explicit preview and validation.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-023

**Unit Test Plan**: Cover task contract provenance shape, unresolved Preset rejection, runtime materialization independence, proposal flat-payload validation, proposal provenance preservation, and proposal preview metadata.

**Integration Test Plan**: Cover Create page preset preview/apply/submission, complete submitted provenance, draft-preserving preview failure, and explicit reapply/refresh before replacing reviewed steps.

### Unit Tests (write first)

- [X] T009 [P] Add failing task contract unit tests for canonical `source.presetVersion`, `source.includePath`, and `source.originalStepId` preservation in `tests/unit/workflows/tasks/test_task_contract.py` (FR-003, SCN-002, SC-002, DESIGN-REQ-006, DESIGN-REQ-016)
- [X] T010 [P] Add task contract unit tests proving executable Tool/Skill steps with missing, partial, or stale source provenance still validate without live preset lookup while complete provenance fields are preserved when provided in `tests/unit/workflows/tasks/test_task_contract.py` (FR-003, FR-005, SCN-002, SCN-003, DESIGN-REQ-006, DESIGN-REQ-016)
- [X] T011 [P] Add failing proposal service unit tests requiring preset-derived promotable proposal steps to be explicit flat `type: "tool"` or `type: "skill"` entries in `tests/unit/workflows/task_proposals/test_service.py` (FR-007, FR-008, SCN-004, SC-004, DESIGN-REQ-023)
- [X] T012 [P] Add failing proposal service unit tests preserving `presetVersion`, `includePath`, and `originalStepId` through promotion in `tests/unit/workflows/task_proposals/test_service.py` (FR-003, FR-004, FR-007, DESIGN-REQ-006, DESIGN-REQ-016)
- [X] T013 [P] Add failing proposal API preview tests exposing complete preset provenance metadata in `tests/unit/api/routers/test_task_proposals.py` (FR-004, SCN-002, DESIGN-REQ-016)
- [X] T014 [P] Add runtime planner verification tests that valid Tool/Skill steps still materialize when source provenance is missing, stale, or catalog-unresolvable in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` (FR-005, SCN-003, SC-003, DESIGN-REQ-016)
- [X] T015 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py` and confirm T009-T014 fail only for the expected MM-579 gaps before production changes

### Integration Tests (write first)

- [X] T016 [P] Add failing Create page integration-boundary test that applies a preset and submits only flat Tool/Skill steps with no unresolved Preset placeholder in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, SCN-001, SC-001, DESIGN-REQ-004, DESIGN-REQ-015)
- [X] T017 [P] Add failing Create page integration-boundary test that submitted preset-derived Tool and Skill steps retain `source.kind`, `source.presetId`, `source.presetVersion`, `source.includePath` when provided, and `source.originalStepId` in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-004, SCN-002, SC-002, DESIGN-REQ-006, DESIGN-REQ-016)
- [X] T018 [P] Add failing Create page integration-boundary test that reapply/refresh requires explicit preview and validation before replacing reviewed generated steps in `frontend/src/entrypoints/task-create.test.tsx` (FR-009, SCN-005, SC-005, DESIGN-REQ-023)
- [X] T019 Run `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T016-T018 fail only for the expected MM-579 gaps before production changes

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [X] T020 If T016 exposes flat-submission drift, update applied preset submission serialization in `frontend/src/entrypoints/task-create.tsx` so all generated steps submit as explicit Tool or Skill steps with no Preset placeholder (FR-001, SCN-001, DESIGN-REQ-004)
- [X] T021 If T018 exposes refresh drift, update reapply/refresh handling in `frontend/src/entrypoints/task-create.tsx` so catalog refresh requires explicit preview and validation before replacing reviewed steps (FR-009, SCN-005, DESIGN-REQ-023)
- [X] T022 If T014 exposes runtime provenance coupling, update runtime materialization in `moonmind/workflows/temporal/worker_runtime.py` so execution depends on Tool/Skill payloads and never on live preset catalog lookup (FR-005, SCN-003)

### Implementation

- [X] T023 Update `TaskStepSource` and related canonical task payload serialization in `moonmind/workflows/tasks/task_contract.py` to preserve canonical `presetVersion` alongside required preset-derived source metadata (FR-003, FR-004, SCN-002, DESIGN-REQ-006, DESIGN-REQ-016)
- [X] T024 Update proposal promotion validation in `moonmind/workflows/task_proposals/service.py` so stored preset-derived proposals must validate as flat executable Tool/Skill payloads and preserve source metadata without live preset re-expansion (FR-007, FR-008, SCN-004, SC-004, DESIGN-REQ-023)
- [X] T025 Update proposal preview serialization in `api_service/api/routers/task_proposals.py` to expose complete preset provenance summary needed for audit/review without making it runtime input (FR-004, DESIGN-REQ-016)
- [X] T026 Update preset expansion-to-step mapping in `frontend/src/entrypoints/task-create.tsx` to retain complete preset-derived source metadata, including `presetVersion`, include path, and original step id when returned by expansion (FR-003, FR-004, SCN-002, DESIGN-REQ-006, DESIGN-REQ-016)
- [X] T027 Keep unresolved Preset, Activity, and shell-shaped executable guardrails intact in `moonmind/workflows/tasks/task_contract.py` while adding MM-579 provenance behavior (FR-002, SC-001, DESIGN-REQ-015)

### Story Validation

- [X] T028 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py` and fix failures until backend MM-579 tests pass (FR-002, FR-003, FR-004, FR-005, FR-007, FR-008, SC-002, SC-003, SC-004)
- [X] T029 Run `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx` and fix failures until Create page MM-579 tests pass (FR-001, FR-003, FR-004, FR-006, FR-009, SC-001, SC-002, SC-005)
- [X] T030 Run `rg -n "MM-579|presetVersion|DESIGN-REQ-004|DESIGN-REQ-006|DESIGN-REQ-015|DESIGN-REQ-016|DESIGN-REQ-023" specs/292-submit-flattened-executable-steps-with-provenance` to confirm traceability remains present (FR-010, SC-006)

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T031 [P] Review `specs/292-submit-flattened-executable-steps-with-provenance/contracts/flattened-executable-provenance.md` against implemented behavior and update only if implementation reveals a contract clarification need (FR-010, DESIGN-REQ-023)
- [X] T032 [P] Review `specs/292-submit-flattened-executable-steps-with-provenance/data-model.md` against final provenance field names and state transitions, especially `presetVersion` (FR-003, FR-010, DESIGN-REQ-006)
- [X] T033 [P] Review `specs/292-submit-flattened-executable-steps-with-provenance/quickstart.md` so commands and expected coverage match final test evidence (FR-010, SC-006)
- [X] T034 Run full unit verification with `./tools/test_unit.sh` and record any unrelated blocker with focused test evidence if the full suite cannot complete
- [X] T035 Run `/moonspec-verify` after implementation and tests pass, validating against `specs/292-submit-flattened-executable-steps-with-provenance/spec.md`, `plan.md`, `tasks.md`, the preserved MM-579 Jira preset brief, and focused test evidence

---

## Dependencies And Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish And Verification (Phase 4)**: Depends on story tests and implementation passing.

### Within The Story

- Unit tests T009-T014 must be written before implementation tasks T023-T027.
- Integration tests T016-T018 must be written before implementation tasks T020-T027.
- Red-first confirmation tasks T015 and T019 must complete before production changes.
- Conditional fallback tasks T020-T022 run only if verification tests expose drift in implemented-unverified rows.
- Core data/validation changes T023-T027 precede story validation T028-T030.
- Final verification T035 runs only after focused and full verification tasks have completed or blockers are documented.

### Parallel Opportunities

- T003 and T004 can run in parallel.
- T009, T011, T013, T014, T016, T017, and T018 can be authored in parallel because they touch different focused test concerns, though same-file test edits should be coordinated.
- T023, T024, T025, and T026 can be implemented in parallel after red-first confirmation because they touch distinct production files.
- T031, T032, and T033 can run in parallel after story validation.

---

## Parallel Example

```bash
# Backend test authoring can be split by boundary:
Task: "Add failing task contract provenance tests in tests/unit/workflows/tasks/test_task_contract.py"
Task: "Add failing proposal promotion tests in tests/unit/workflows/task_proposals/test_service.py"
Task: "Add failing proposal preview tests in tests/unit/api/routers/test_task_proposals.py"

# Production implementation can be split after red-first confirmation:
Task: "Update task provenance model in moonmind/workflows/tasks/task_contract.py"
Task: "Update proposal promotion validation in moonmind/workflows/task_proposals/service.py"
Task: "Update Create page source metadata mapping in frontend/src/entrypoints/task-create.tsx"
```

---

## Implementation Strategy

1. Confirm all MM-579 artifacts and test targets exist.
2. Inspect current task contract, proposal, runtime, and Create page boundaries.
3. Add failing unit tests for canonical provenance, flat proposal payloads, preview metadata, and runtime provenance independence.
4. Add failing Create page integration-boundary tests for flat submission, complete provenance, and explicit refresh.
5. Confirm red-first failures with the focused unit and frontend commands.
6. Implement only the failing gaps, preserving already-verified unresolved Preset rejection and runtime independence behavior.
7. Rerun focused backend and frontend tests until green.
8. Run full unit verification or record exact unrelated blockers.
9. Run `/moonspec-verify` against MM-579 and the preserved Jira preset brief.

---

## Notes

- This task list covers exactly one story and does not create broad Step Type refactors beyond MM-579.
- Implemented-verified rows are preserved through final validation rather than unnecessary implementation tasks.
- Implemented-unverified rows include conditional fallback implementation tasks that are skipped when verification tests pass.
- The final implementation must preserve `MM-579` in artifacts, commit text, PR metadata, and verification output.
