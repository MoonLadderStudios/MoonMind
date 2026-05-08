# Tasks: Show Attachment and Recovery Diagnostics By Target

**Input**: Design documents from `/work/agent_jobs/mm:1f8186c7-7b34-49ec-bf2f-fee2e3d290af/repo/specs/329-show-attachment-recovery-diagnostics-by-target/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/task-detail-target-diagnostics.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single user story "Target-Aware Task Diagnostics" so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-635 and the original Jira preset brief are preserved in `spec.md`. This task list covers FR-001 through FR-013, acceptance scenarios 1-6, edge cases, SC-001 through SC-006, DESIGN-REQ-023, DESIGN-REQ-024, and `contracts/task-detail-target-diagnostics.md`.

**Requirement Status Summary**:

- Code + tests for partial/missing rows: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-010, FR-011, FR-012, SC-001, SC-002, SC-003, SC-005, DESIGN-REQ-023, DESIGN-REQ-024
- Verification-first with conditional fallback implementation: FR-008, FR-009, FR-013, SC-004, SC-006
- Already verified rows: none

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`; `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`; final `./tools/test_unit.sh`
- Integration tests: `pytest tests/integration/vision/test_context_artifacts.py -m 'integration_ci' -q --tb=short`; `pytest tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py -q --tb=short`; final `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Path Conventions

- Backend: `api_service/`, `moonmind/`, `tests/unit/api/`, `tests/contract/`, `tests/integration/`
- Frontend: `frontend/src/entrypoints/`
- Feature artifacts: `specs/329-show-attachment-recovery-diagnostics-by-target/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active artifacts and test entry points before red-first work begins

- [X] T001 Confirm `specs/329-show-attachment-recovery-diagnostics-by-target/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-detail-target-diagnostics.md`, and `quickstart.md` are present and still reference MM-635, FR-001 through FR-013, DESIGN-REQ-023, and DESIGN-REQ-024
- [ ] T002 Confirm backend, frontend, and integration test commands from `specs/329-show-attachment-recovery-diagnostics-by-target/quickstart.md` are runnable in the current environment before editing production files
- [X] T003 [P] Review existing backend projection helpers in `api_service/api/routers/executions.py` for extension points for compact `targetDiagnostics` refs and degraded evidence
- [X] T004 [P] Review existing task detail schemas and render sections in `frontend/src/entrypoints/task-detail.tsx` for the target diagnostics panel insertion point
- [ ] T005 [P] Review existing target-aware attachment and vision evidence in `moonmind/agents/codex_worker/worker.py`, `moonmind/vision/service.py`, and `tests/integration/vision/test_context_artifacts.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the shared schema and fixture shape that unit, integration, backend, and UI work will use

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T006 Define the compact target diagnostics fixture shape in `tests/unit/api/routers/test_executions.py` from `contracts/task-detail-target-diagnostics.md` without editing production schemas yet (FR-001 through FR-012, SC-001 through SC-005)
- [ ] T007 Add reusable backend fixture builders for objective targets, step targets, manifest refs, generated context refs, attachment failure phases, and recovery provenance in `tests/unit/api/routers/test_executions.py` (FR-001 through FR-012, DESIGN-REQ-023, DESIGN-REQ-024)
- [ ] T008 Add reusable frontend fixture payloads for populated targets, empty targets, degraded target diagnostics, Resume provenance, preserved steps, and failed Resume phases in `frontend/src/entrypoints/task-detail.test.tsx` (FR-001 through FR-012, SC-001 through SC-005)
- [X] T009 [P] Add contract fixture examples matching `contracts/task-detail-target-diagnostics.md` in `tests/contract/test_temporal_execution_api.py` or the nearest existing execution API contract test file (FR-001 through FR-012, DESIGN-REQ-023, DESIGN-REQ-024)

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Target-Aware Task Diagnostics

**Summary**: As an operator inspecting task details, I want attachment metadata, generated context references, recovery provenance, and failure phases grouped by target so that I can understand task outcomes without parsing raw workflow history.

**Independent Test**: View task details for attachment-aware tasks, step-aware tasks, resumed executions, and failed Resume attempts; confirm displayed metadata and diagnostics identify the relevant objective or step target and explain outcomes without requiring raw workflow-history inspection.

**Traceability**: FR-001 through FR-013, acceptance scenarios 1-6, edge cases, SC-001 through SC-006, DESIGN-REQ-023, DESIGN-REQ-024, MM-635

**Test Plan**:

- Unit: backend projection/schema validation, bounded phase normalization, degraded evidence handling, frontend rendering, empty/populated targets, current-step context, Resume provenance, preserved prior steps, traceability
- Integration: execution API contract shape, target-aware generated context artifacts, task-detail operator journey, failed-step Resume preservation, final quickstart validation

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T010 [P] Add failing backend unit tests for `targetDiagnostics.targets` grouping objective and step attachment metadata in `tests/unit/api/routers/test_executions.py` (FR-001, FR-002, SC-001, DESIGN-REQ-023)
- [X] T011 [P] Add failing backend unit tests for manifest refs, generated context refs, degraded refs, target failure ownership, and bounded attachment phases in `tests/unit/api/routers/test_executions.py` (FR-003, FR-004, FR-005, FR-006, SC-002, SC-003, DESIGN-REQ-023)
- [X] T012 [P] Add failing backend unit tests for Resume provenance, preserved prior steps, failed Resume phase labels, and raw-history-free structured diagnostics in `tests/unit/api/routers/test_executions.py` (FR-008, FR-009, FR-010, FR-012, SC-004, SC-005, DESIGN-REQ-024)
- [X] T013 [P] Add failing frontend unit tests rendering objective target diagnostics, step target diagnostics, empty target states, refs, failures, and degraded evidence in `frontend/src/entrypoints/task-detail.test.tsx` (FR-001 through FR-007, FR-011, FR-012, SC-001, SC-002, SC-003)
- [X] T014 [P] Add failing frontend unit tests rendering resumed execution source, preserved prior steps, failed Resume phases, and MM-635 traceability-adjacent labels where operator-visible evidence appears in `frontend/src/entrypoints/task-detail.test.tsx` (FR-008, FR-009, FR-010, FR-013, SC-004, SC-005, SC-006)
- [ ] T015 [P] Add failing schema unit tests for target diagnostics validation, bounded phase values, empty target handling, and degraded evidence in `tests/unit/schemas/test_temporal_models.py` (FR-002, FR-005, FR-006, FR-010, DESIGN-REQ-023, DESIGN-REQ-024)

### Integration Tests (write first)

- [X] T016 [P] Add failing execution API contract or route test for the compact `targetDiagnostics` response shape in `tests/contract/test_temporal_execution_api.py` or `tests/unit/api/routers/test_executions.py` (FR-001 through FR-012, DESIGN-REQ-023, DESIGN-REQ-024)
- [ ] T017 [P] Extend target-aware generated context integration coverage in `tests/integration/vision/test_context_artifacts.py` so the generated index provides refs that can be projected by target diagnostics (FR-003, FR-004, SC-003, DESIGN-REQ-023)
- [ ] T018 [P] Extend failed-step Resume integration coverage in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` to preserve source execution and prior-step evidence expected by task detail diagnostics (FR-008, FR-009, SC-004, DESIGN-REQ-024)
- [X] T019 [P] Add task-detail integration-style UI test coverage in `frontend/src/entrypoints/task-detail.test.tsx` for the full operator journey across attachment targets, refs, failure phases, Resume provenance, and raw diagnostics fallback (acceptance scenarios 1-6, FR-001 through FR-012)

### Red-First Confirmation

- [ ] T020 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and confirm T010-T012 fail for missing target diagnostics behavior before backend implementation (FR-001 through FR-012)
- [ ] T021 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm T013-T014 and T019 fail for missing task-detail target diagnostics UI before frontend implementation (FR-001 through FR-012)
- [ ] T022 Run the focused schema unit test command for `tests/unit/schemas/test_temporal_models.py` through `./tools/test_unit.sh tests/unit/schemas/test_temporal_models.py` and confirm T015 fails before schema implementation (FR-002, FR-005, FR-006, FR-010)
- [ ] T023 Run focused integration commands for `tests/integration/vision/test_context_artifacts.py` and `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, documenting any environment blocker, and confirm T017-T018 fail or identify verification-only pass evidence before production changes (FR-003, FR-004, FR-008, FR-009)

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [X] T024 If FR-008 or SC-004 verification fails, update Resume source projection in `api_service/api/routers/executions.py` and related frontend rendering in `frontend/src/entrypoints/task-detail.tsx` to expose source workflow/run provenance in target diagnostics (FR-008, SC-004, DESIGN-REQ-024)
- [X] T025 If FR-009 verification fails, update step ledger projection or task-detail rendering in `api_service/api/routers/executions.py` and `frontend/src/entrypoints/task-detail.tsx` so preserved prior steps remain visible with source workflow ID, run ID, logical step ID, and attempt provenance (FR-009, SC-004, DESIGN-REQ-024)
- [ ] T026 If FR-013 or SC-006 traceability verification fails, update `specs/329-show-attachment-recovery-diagnostics-by-target/tasks.md`, implementation notes, and later verification output to preserve MM-635 and the original Jira preset brief (FR-013, SC-006)

### Implementation

- [X] T027 Implement target diagnostics Pydantic models and serialization aliases in `moonmind/schemas/temporal_models.py` (FR-001 through FR-012, SC-001 through SC-005)
- [X] T028 Implement bounded attachment and Resume phase normalization helpers in `api_service/api/routers/executions.py` (FR-005, FR-006, FR-010, SC-002, SC-005, DESIGN-REQ-023, DESIGN-REQ-024)
- [ ] T029 Implement target diagnostics extraction from task input snapshot, execution parameters, memo/search attributes, artifact refs, step ledger rows, and Resume summary in `api_service/api/routers/executions.py` (FR-001 through FR-010, FR-012, DESIGN-REQ-023, DESIGN-REQ-024)
- [X] T030 Wire `targetDiagnostics` into execution detail responses without embedding large artifact bodies in `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` (FR-003, FR-004, FR-011, FR-012)
- [X] T031 Update task detail Zod schemas for target diagnostics in `frontend/src/entrypoints/task-detail.tsx` (FR-001 through FR-012)
- [X] T032 Implement target diagnostics UI sections for objective targets, step targets, empty targets, attachment metadata, refs, failures, and degraded evidence in `frontend/src/entrypoints/task-detail.tsx` (FR-001 through FR-007, FR-011, FR-012, SC-001 through SC-003)
- [X] T033 Implement Recovery Provenance UI for resumed execution source, preserved prior steps, checkpoint refs, and failed Resume phases in `frontend/src/entrypoints/task-detail.tsx` (FR-008, FR-009, FR-010, SC-004, SC-005, DESIGN-REQ-024)
- [X] T034 Preserve raw diagnostics panels while adding structured diagnostics summaries in `frontend/src/entrypoints/task-detail.tsx` (FR-012, acceptance scenarios 2-3, acceptance scenario 6)
- [X] T035 Keep generated context and manifest evidence as refs and preserve artifact authorization/redaction behavior in `api_service/api/routers/executions.py` and `frontend/src/entrypoints/task-detail.tsx` (FR-003, FR-004, FR-011, DESIGN-REQ-023)
- [ ] T036 Update or add integration fixture plumbing for target-aware generated context refs and Resume preserved-step evidence in `tests/integration/vision/test_context_artifacts.py` and `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` only as needed to support T017-T018 (FR-003, FR-004, FR-008, FR-009)

### Story Validation

- [X] T037 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and verify backend target diagnostics unit coverage passes (FR-001 through FR-012, DESIGN-REQ-023, DESIGN-REQ-024)
- [ ] T038 Run `./tools/test_unit.sh tests/unit/schemas/test_temporal_models.py` and verify schema validation coverage passes (FR-002, FR-005, FR-006, FR-010)
- [X] T039 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and verify task-detail target diagnostics UI coverage passes (acceptance scenarios 1-6, FR-001 through FR-012)
- [ ] T040 Run focused integration coverage from `quickstart.md` for vision target context artifacts and failed-step Resume preservation, documenting Docker or Temporal test-server blockers if present (SC-003, SC-004, DESIGN-REQ-023, DESIGN-REQ-024)
- [ ] T041 Validate the story end-to-end against `specs/329-show-attachment-recovery-diagnostics-by-target/quickstart.md`, confirming raw diagnostics remain available but are not required for target ownership, refs, recovery provenance, or failure phase inspection (FR-001 through FR-012, SC-001 through SC-005)

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding hidden scope

- [ ] T042 [P] Review `specs/329-show-attachment-recovery-diagnostics-by-target/plan.md`, `research.md`, `data-model.md`, `contracts/task-detail-target-diagnostics.md`, and `quickstart.md` for drift after implementation and update only if behavior changed (FR-013, SC-006)
- [X] T043 [P] Review task-detail copy and accessibility semantics in `frontend/src/entrypoints/task-detail.tsx` so target groups, failure phases, refs, and empty/degraded states are scannable and do not require raw history parsing (FR-002, FR-006, FR-012)
- [X] T044 [P] Review backend projection output in `api_service/api/routers/executions.py` for bounded metadata, no secret leakage, no large artifact bodies, and no hidden fallback semantics (FR-003, FR-004, FR-011, DESIGN-REQ-023, DESIGN-REQ-024)
- [ ] T045 Run final unit verification with `./tools/test_unit.sh`, documenting any blocker with exact failing command and reason (FR-001 through FR-013)
- [ ] T046 Run final hermetic integration verification with `./tools/test_integration.sh`, or document Docker/socket/environment blocker and focused integration evidence already collected (SC-001 through SC-006)
- [ ] T047 Run `/moonspec-verify` to validate the final implementation against MM-635, the original Jira preset brief, `spec.md`, `plan.md`, `tasks.md`, required tests, DESIGN-REQ-023, and DESIGN-REQ-024 (FR-013, SC-006)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on story implementation and story validation.

### Within The Story

- Unit tests T010-T015 must be written before implementation.
- Integration tests T016-T019 must be written before implementation.
- Red-first confirmation T020-T023 must complete before production implementation tasks T027-T036.
- Conditional fallback tasks T024-T026 apply only if verification-first tests fail.
- Backend schema/projection tasks T027-T030 should precede frontend schema/render tasks T031-T035.
- Integration fixture task T036 follows the backend/UI contract shape.
- Story validation T037-T041 follows implementation.

### Parallel Opportunities

- T003, T004, and T005 can run in parallel during setup.
- T009 can run in parallel with T006-T008 after the contract is understood.
- T010-T015 can be authored in parallel because they touch separate test scopes or independent sections.
- T016-T019 can be authored in parallel because they cover different integration/contract surfaces.
- T042-T044 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Launch red-first test authoring together:
Task: "T010 add backend projection unit tests in tests/unit/api/routers/test_executions.py"
Task: "T013 add frontend rendering unit tests in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T017 extend vision integration coverage in tests/integration/vision/test_context_artifacts.py"

# Launch polish reviews together:
Task: "T042 review MoonSpec artifacts for drift"
Task: "T043 review task-detail accessibility and copy"
Task: "T044 review backend projection bounded metadata"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational schema/fixture tasks.
2. Write backend, frontend, schema, contract, and integration tests first.
3. Run red-first commands and confirm failures for missing or partial requirements.
4. Run verification-first tests for implemented-unverified Resume and traceability rows; skip conditional fallback implementation only when those tests pass.
5. Implement backend target diagnostics models, projection, phase normalization, and compact refs.
6. Implement task-detail schemas and UI sections.
7. Validate the story with focused unit and integration commands.
8. Complete polish, full unit verification, hermetic integration verification or documented blocker, and final `/moonspec-verify`.

---

## Notes

- This task list covers one story only: Target-Aware Task Diagnostics.
- Every missing or partial requirement has red-first unit and/or integration test coverage plus implementation tasks.
- Every implemented-unverified row has verification-first tasks and conditional fallback implementation tasks.
- No rows are treated as already verified.
- Preserve MM-635 and the original Jira preset brief in implementation notes, verification output, commit text, and pull request metadata.
