# Tasks: Exact Full Rerun Workflow

**Input**: Design documents from `/work/agent_jobs/mm:5918c7c6-d387-42cb-b505-bee2d80f5a2e/repo/specs/344-exact-full-rerun-workflow/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/exact-full-rerun-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: exact full rerun from a failed execution using the original task input snapshot unchanged.

**Source Traceability**: Preserves Jira issue `MM-645` and the original Jira preset brief from `spec.md`. Requirement status from `plan.md`: 10 partial, 5 missing, 8 implemented_unverified, 1 implemented_verified across FR/SCN/SC/DESIGN rows.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Focused frontend tests: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- Focused Python unit tests: `pytest tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py -q`
- Focused hermetic integration tests: `pytest tests/integration/temporal -m 'integration_ci' -q --tb=short`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, success criterion, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the existing test surfaces and feature artifacts are ready for MM-645 work.

- [X] T001 Verify `specs/344-exact-full-rerun-workflow/spec.md`, `specs/344-exact-full-rerun-workflow/plan.md`, `specs/344-exact-full-rerun-workflow/research.md`, `specs/344-exact-full-rerun-workflow/data-model.md`, `specs/344-exact-full-rerun-workflow/contracts/exact-full-rerun-contract.md`, and `specs/344-exact-full-rerun-workflow/quickstart.md` exist and preserve `MM-645` traceability for FR-010 and SC-005
- [X] T002 Confirm frontend unit test tooling is available for `frontend/src/entrypoints/task-detail.test.tsx` and `frontend/src/entrypoints/task-create.test.tsx` using `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- [X] T003 Confirm Python unit test tooling is available for `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/api/routers/test_executions.py` using `pytest tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py -q`
- [X] T004 Confirm hermetic integration test tooling is available for `tests/integration/temporal` using `pytest tests/integration/temporal -m 'integration_ci' -q --tb=short`

---

## Phase 2: Foundational

**Purpose**: Establish blocking fixtures and shared contract expectations before story test work begins.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T005 [P] Identify and document the existing exact-rerun fixture shapes in `frontend/src/entrypoints/task-detail.test.tsx` and `frontend/src/entrypoints/task-create.test.tsx` for FR-001, FR-006, SCN-001, and the Mission Control contract
- [X] T006 [P] Identify and document existing source execution fixture builders in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-003, FR-004, FR-005, FR-007, DESIGN-REQ-001, and DESIGN-REQ-002
- [X] T007 [P] Identify and document existing execution router fixture builders in `tests/unit/api/routers/test_executions.py` for FR-002, FR-008, FR-009, SCN-002, and DESIGN-REQ-003
- [X] T008 [P] Identify an existing hermetic integration test location or create a placeholder test module path under `tests/integration/temporal/test_exact_full_rerun_workflow.py` for SCN-001 through SCN-005
- [X] T009 Confirm no new persistent database table or migration is needed by reviewing `specs/344-exact-full-rerun-workflow/data-model.md` and existing execution record fields in `api_service/db/models.py` for the storage constraint

**Checkpoint**: Foundation ready; story test and implementation work can now begin.

---

## Phase 3: Story - Exact Failed Task Rerun

**Summary**: As a Mission Control user, I want Rerun on a failed execution to start the whole task again from the original task input so that I can repeat the exact same work without editing the task or carrying over partial progress.

**Independent Test**: Start from a failed execution with a known original task input snapshot, choose Rerun, and confirm the new execution uses the unchanged snapshot, records exact-rerun provenance, starts from the beginning, and imports zero completed progress from the source execution.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004

**Unit Test Plan**:

- Frontend: direct Rerun action from task detail does not navigate to `/tasks/new`, editable retry remains separate, unavailable reason remains visible.
- Service: exact no-mutation rerun derives `exact_full_rerun` provenance, pins source workflow/run IDs, strips resume/progress fields, and rejects missing source identity.
- Router: exact rerun snapshot lineage and missing snapshot/source identity behavior remain explicit.

**Integration Test Plan**:

- Mission Control/API path creates a new exact rerun execution without authoring.
- Created execution reuses original snapshot unchanged, records provenance, starts from the beginning, and imports no completed progress or resume checkpoint state.

### Unit Tests (write first)

- [X] T010 [P] Add failing frontend unit test proving the Rerun action submits directly from `frontend/src/entrypoints/task-detail.test.tsx` without linking to `/tasks/new` for FR-001, FR-006, SCN-001, SC-001, and the exact-rerun contract
- [X] T011 [P] Add failing frontend unit test preserving Edit task as the editable retry path while Rerun remains exact in `frontend/src/entrypoints/task-detail.test.tsx` for FR-006 and the contract non-goal
- [X] T012 [P] Add failing frontend unit test removing or guarding exact Rerun authoring-form dependency in `frontend/src/entrypoints/task-create.test.tsx` for FR-001, FR-006, and SCN-001
- [X] T013 [P] Add failing service unit test in `tests/unit/workflows/temporal/test_temporal_service.py` proving exact no-mutation `RequestRerun` creates `task.recovery.kind = exact_full_rerun` with source workflow/run IDs for FR-003, FR-004, SCN-003, SC-003, and DESIGN-REQ-001
- [X] T014 [P] Add failing service unit test in `tests/unit/workflows/temporal/test_temporal_service.py` proving exact full rerun strips `resume`, `resumeSource`, `resumeCheckpointRef`, `preservedSteps`, and `completedSteps` for FR-005, FR-007, SCN-004, SCN-005, SC-004, and DESIGN-REQ-002
- [X] T015 [P] Add failing service unit test in `tests/unit/workflows/temporal/test_temporal_service.py` proving exact rerun rejects or blocks missing source run identity rather than creating unpinned provenance for FR-004 and DESIGN-REQ-001
- [X] T016 [P] Add failing router unit test in `tests/unit/api/routers/test_executions.py` proving exact rerun persists/returns source snapshot lineage without mutable task patch fields for FR-002, FR-008, SCN-002, SC-002, and DESIGN-REQ-003
- [X] T017 [P] Add failing router unit test in `tests/unit/api/routers/test_executions.py` proving missing original task input snapshot keeps exact Rerun unavailable with `original_task_input_snapshot_missing` for FR-009
- [ ] T018 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx` and confirm T010-T012 fail for the expected MM-645 reasons before implementation
- [ ] T019 Run `pytest tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py -q` and confirm T013-T017 fail for the expected MM-645 reasons before implementation

### Integration Tests (write first)

- [X] T020 [P] Add failing hermetic integration test in `tests/integration/temporal/test_exact_full_rerun_workflow.py` proving a failed execution direct Rerun creates a new execution without task authoring for FR-001, FR-006, SCN-001, and SC-001
- [X] T021 [P] Add failing hermetic integration test in `tests/integration/temporal/test_exact_full_rerun_workflow.py` proving exact rerun reuses the original task input snapshot unchanged for FR-002, FR-008, SCN-002, SC-002, DESIGN-REQ-003, and DESIGN-REQ-004
- [X] T022 [P] Add failing hermetic integration test in `tests/integration/temporal/test_exact_full_rerun_workflow.py` proving created exact rerun execution contains `exact_full_rerun` provenance with source workflow/run IDs for FR-003, FR-004, SCN-003, SC-003, and DESIGN-REQ-001
- [X] T023 [P] Add failing hermetic integration test in `tests/integration/temporal/test_exact_full_rerun_workflow.py` proving exact rerun starts from the beginning and excludes preserved progress/checkpoint state for FR-005, FR-007, SCN-004, SCN-005, SC-004, and DESIGN-REQ-002
- [ ] T024 Run `pytest tests/integration/temporal -m 'integration_ci' -q --tb=short` and confirm T020-T023 fail for the expected MM-645 reasons before implementation

### Conditional Verification For Implemented-Unverified Rows

- [X] T025 Run the focused tests from T013-T014 and T020-T023 after writing them to determine whether FR-005, FR-007, SCN-004, SCN-005, SC-004, DESIGN-REQ-002, and existing full-rerun sanitization already pass or require fallback implementation in `moonmind/workflows/temporal/service.py`
- [X] T026 If T025 passes for from-beginning/no-progress behavior, record the existing evidence in `specs/344-exact-full-rerun-workflow/tasks.md` task notes or implementation handoff without changing production code for FR-005, FR-007, SCN-004, SCN-005, SC-004, and DESIGN-REQ-002

### Implementation

- [X] T027 Implement server-side exact rerun provenance derivation in `moonmind/workflows/temporal/service.py` for no-mutation `RequestRerun`, covering FR-003, FR-004, SCN-003, SC-003, and DESIGN-REQ-001
- [X] T028 Implement or adjust exact full rerun parameter/snapshot reuse behavior in `moonmind/workflows/temporal/service.py` so the original task input snapshot is reused unchanged and no resume/progress fields carry over, covering FR-002, FR-005, FR-007, FR-008, SCN-002, SCN-004, SCN-005, SC-002, SC-004, DESIGN-REQ-002, DESIGN-REQ-003, and DESIGN-REQ-004
- [X] T029 Update exact rerun update-route handling in `api_service/api/routers/executions.py` to persist/return correct source snapshot lineage, reject unsafe missing source identity, and preserve `original_task_input_snapshot_missing` behavior for FR-002, FR-004, FR-008, and FR-009
- [X] T030 Update Mission Control task detail Rerun behavior in `frontend/src/entrypoints/task-detail.tsx` to submit direct exact rerun without navigating to task authoring, covering FR-001, FR-006, SCN-001, SC-001, and the contract
- [X] T031 Update shared task editing helpers in `frontend/src/lib/temporalTaskEditing.ts` only if needed to separate direct exact Rerun from edit-for-rerun navigation, covering FR-001 and FR-006
- [X] T032 Remove or guard obsolete exact Rerun authoring-form assumptions in `frontend/src/entrypoints/task-create.tsx` while preserving edit-for-rerun behavior, covering FR-006 and the contract non-goal
- [X] T033 Apply fallback implementation in `moonmind/workflows/temporal/service.py` only if T025 shows existing sanitization does not fully prevent completed progress, preserved step output, or checkpoint import for FR-005, FR-007, SCN-004, SCN-005, SC-004, and DESIGN-REQ-002
- [X] T034 Regenerate or adjust generated API/type fixtures only if the implementation changes public response shape in `frontend/src/generated/openapi.ts`, covering contract consistency for FR-001 through FR-009

### Story Validation

- [X] T035 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx` and fix failures in `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/entrypoints/task-create.tsx`, and `frontend/src/lib/temporalTaskEditing.ts`
- [X] T036 Run `pytest tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py -q` and fix failures in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py`
- [X] T037 Run `pytest tests/integration/temporal -m 'integration_ci' -q --tb=short` and fix failures in the exact rerun workflow path
- [X] T038 Validate the independent story end to end against `specs/344-exact-full-rerun-workflow/quickstart.md`, confirming no authoring form, unchanged snapshot reuse, `exact_full_rerun` provenance, full from-beginning execution, and no progress import for FR-001 through FR-009 and SCN-001 through SCN-005

**Checkpoint**: The exact failed task Rerun story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding scope.

- [X] T039 [P] Review `specs/344-exact-full-rerun-workflow/spec.md`, `specs/344-exact-full-rerun-workflow/plan.md`, `specs/344-exact-full-rerun-workflow/research.md`, `specs/344-exact-full-rerun-workflow/data-model.md`, `specs/344-exact-full-rerun-workflow/contracts/exact-full-rerun-contract.md`, `specs/344-exact-full-rerun-workflow/quickstart.md`, and `specs/344-exact-full-rerun-workflow/tasks.md` to ensure `MM-645` and the original Jira preset brief remain traceable for FR-010 and SC-005
- [X] T040 [P] Add any missing edge-case coverage discovered during implementation to `tests/unit/workflows/temporal/test_temporal_service.py` or `tests/unit/api/routers/test_executions.py` for missing source identity, missing snapshot, and progress carryover guardrails
- [X] T041 Run `./tools/test_unit.sh` for final unit verification across Python and frontend suites
- [ ] T042 Run `./tools/test_integration.sh` for final hermetic integration_ci verification
- [ ] T043 Run `/moonspec-verify` for `specs/344-exact-full-rerun-workflow/spec.md` after implementation and tests pass, comparing final evidence against the preserved MM-645 Jira preset brief

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish And Verification (Phase 4)**: Depends on story implementation and focused tests passing.

### Within The Story

- Unit tests T010-T017 must be written before implementation tasks T027-T034.
- Integration tests T020-T023 must be written before implementation tasks T027-T034.
- Red-first confirmation T018, T019, and T024 must complete before production code tasks.
- Conditional verification T025 decides whether fallback implementation T033 is needed for implemented-unverified cleanup behavior.
- Service implementation T027-T028 should precede API route wiring T029.
- API route wiring T029 should precede frontend direct-action implementation T030-T032 when frontend depends on response semantics.
- Story validation T035-T038 follows implementation tasks.
- Final verification T043 follows final unit and integration runs T041-T042.

### Parallel Opportunities

- T005, T006, T007, and T008 can run in parallel because they inspect different fixture/test surfaces.
- T010, T013, T016, and T017 can be authored in parallel because they touch different test files.
- T020-T023 can be authored together in one integration module, but coordinate edits because they share `tests/integration/temporal/test_exact_full_rerun_workflow.py`.
- T027 and T030 can begin in parallel only after red-first confirmation if API contract assumptions are stable; T029 should reconcile backend response behavior before final frontend validation.
- T039 and T040 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Launch independent red-first unit test authoring:
Task: "T010 Add direct Rerun frontend unit test in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T013 Add exact provenance service unit test in tests/unit/workflows/temporal/test_temporal_service.py"
Task: "T016 Add source snapshot router unit test in tests/unit/api/routers/test_executions.py"

# Launch independent implementation after red-first confirmation:
Task: "T027 Implement exact rerun provenance in moonmind/workflows/temporal/service.py"
Task: "T030 Implement direct Rerun action in frontend/src/entrypoints/task-detail.tsx"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 setup/foundation checks.
2. Write frontend, service, router, and integration tests first.
3. Run T018, T019, and T024 to confirm the tests fail for the intended MM-645 gaps.
4. Implement server-side exact rerun provenance and unchanged snapshot/no-progress semantics.
5. Wire API route behavior and then frontend direct Rerun behavior.
6. Re-run focused frontend, unit, and integration tests.
7. Run final `./tools/test_unit.sh` and `./tools/test_integration.sh`.
8. Run `/moonspec-verify` against the preserved MM-645 Jira preset brief.

### Requirement Status Handling

- Code-and-test work: FR-001, FR-002, FR-003, FR-004, FR-006, FR-008, SCN-001, SCN-002, SCN-003, SC-001, SC-002, SC-003, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-004.
- Verification-first with conditional fallback: FR-005, FR-007, FR-010, SCN-004, SCN-005, SC-004, SC-005, DESIGN-REQ-002.
- Already verified, preserve with regression/final validation: FR-009.

---

## Notes

- This task list covers one story only: MM-645 exact full rerun workflow.
- Unit and integration tests are required before implementation.
- Exact Rerun must remain distinct from Edit task/edit-for-rerun and failed-step Resume.
- Do not introduce new persistent storage.
- Preserve `MM-645` in downstream implementation notes, verification output, commit text, and pull request metadata.
- T005-T007 fixture notes: frontend coverage uses `task-detail.test.tsx` task action payloads and `task-create.test.tsx` edit-for-rerun coverage; service coverage uses `temporal_db` source execution fixtures in `test_temporal_service.py`; router coverage uses `_build_execution_record()` and task input snapshot action serialization fixtures in `test_executions.py`.
- T026/T038 evidence: focused service and integration assertions prove `_full_rerun_parameters()` strips task run IDs, resume source/checkpoint, preserved steps, completed steps, and task resume data while adding exact provenance from the source workflow/run.
- TDD red evidence: service exact-rerun unit expectations failed before production changes because `task.recovery` was missing; the new MM-645 integration test failed against an unmodified temporary worktree because `exact_full_rerun` provenance was absent.
- T042 blocked in this managed container: `./tools/test_integration.sh` reached Docker Compose image build and failed with Docker daemon `403 Forbidden` administrative rules. Direct hermetic pytest verification passed with `pytest tests/integration/temporal -m 'integration_ci' -q --tb=short`.
