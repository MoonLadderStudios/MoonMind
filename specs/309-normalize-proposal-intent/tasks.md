# Tasks: Normalize Proposal Intent in Temporal Submissions

**Input**: Design documents from `specs/309-normalize-proposal-intent/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/proposal-intent-normalization.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around the single MM-595 story: canonical proposal intent in task submissions.

**Source Traceability**: MM-595, FR-001 through FR-008, acceptance scenarios 1-7, SC-001 through SC-006, DESIGN-REQ-003 through DESIGN-REQ-006.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/workflows/test_run_proposals.py tests/unit/agents/codex_worker/test_worker.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/workflows/task_proposals/test_service.py`
- Integration tests: `./tools/test_integration.sh`
- Targeted integration iteration: `pytest tests/integration/workflows/temporal/workflows/test_run.py -k proposals -q --tb=short`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete work
- Every task includes exact file paths and the requirement, scenario, success criterion, or source mapping it validates or implements

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active feature context and required artifacts before writing tests.

- [X] T001 Confirm `specs/309-normalize-proposal-intent/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/proposal-intent-normalization.md`, and `quickstart.md` exist and preserve MM-595 plus DESIGN-REQ-003 through DESIGN-REQ-006
- [X] T002 Review the requirement status table in `specs/309-normalize-proposal-intent/plan.md` and build the implementation checklist for partial, implemented_unverified, and implemented_verified rows
- [X] T003 [P] Inspect existing task-submission and proposal tests in `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/temporal/workflows/test_run_proposals.py`, `tests/unit/agents/codex_worker/test_worker.py`, and `tests/unit/workflows/task_proposals/test_service.py` for reusable fixtures

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Identify the exact submission surfaces and compatibility boundaries before story test work begins.

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [X] T004 Map new task creation surfaces that can carry proposal intent in `api_service/api/routers/executions.py`, `moonmind/workflows/task_proposals/service.py`, and `moonmind/agents/codex_worker/worker.py` for FR-003 and DESIGN-REQ-004
- [X] T005 Map workflow compatibility-read behavior in `moonmind/workflows/temporal/workflows/run.py` for FR-005 and DESIGN-REQ-005, including root-only older payloads and nested-precedence cases
- [X] T006 [P] Map proposal state vocabulary surfaces in `moonmind/workflows/temporal/workflows/run.py`, `api_service/db/models.py`, `api_service/api/routers/executions.py`, `api_service/api/routers/task_dashboard_view_model.py`, and `frontend/src/utils/executionStatusPillClasses.ts` for FR-006 and DESIGN-REQ-006

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Canonical Proposal Intent

**Summary**: As a MoonMind operator, I need every task creation surface to persist proposal intent in the canonical nested task payload so proposal behavior is deterministic across API submissions, schedules, promotions, and Codex managed sessions.

**Independent Test**: Submit representative task creation requests through each supported creation surface and verify the stored run input, proposal-stage gate decision, compatibility handling, and reported lifecycle state without relying on root-level flags, runtime-local metadata, or environment-derived proposal intent.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006

**Unit Test Plan**:

- API task-shaped submission tests prove nested proposal intent is written and root proposal intent is absent.
- Workflow proposal-stage tests prove canonical nested gating, global gate behavior, and compatibility-only root reads.
- Codex worker tests prove managed-session proposal checks read canonical nested task intent.
- Proposal promotion/service tests prove promoted task payloads preserve canonical nested proposal intent.
- Dashboard/status tests prove `proposals` vocabulary remains consistent.

**Integration Test Plan**:

- Temporal workflow proposal tests prove canonical nested opt-in invokes proposal activities and absent opt-in skips them.
- Integration evidence confirms finish summary proposal metadata and state vocabulary remain durable and safe.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T007 [P] Add failing API unit test proving new task-shaped submissions persist `initial_parameters["task"]["proposeTasks"]` and do not persist root `initial_parameters["proposeTasks"]` for FR-001, FR-004, SC-001, SC-004, DESIGN-REQ-003, and DESIGN-REQ-005 in `tests/unit/api/routers/test_executions.py`
- [X] T008 [P] Add failing API unit test proving `task.proposalPolicy` remains nested and no flattened root proposal policy is written for FR-002 and DESIGN-REQ-003 in `tests/unit/api/routers/test_executions.py`
- [X] T009 [P] Add failing workflow unit test proving nested `parameters.task.proposeTasks` takes precedence over conflicting root `parameters.proposeTasks` for FR-004, FR-005, SC-003, and DESIGN-REQ-005 in `tests/unit/workflows/temporal/workflows/test_run_proposals.py`
- [X] T010 [P] Add workflow compatibility unit test proving root-only older payloads are read only by the compatibility gate for FR-005 and SC-003 in `tests/unit/workflows/temporal/workflows/test_run_proposals.py`
- [X] T011 [P] Add or update Codex worker unit test proving managed-session proposal behavior ignores runtime-local or environment proposal hints when canonical `task.proposeTasks` is absent for FR-003, FR-004, and DESIGN-REQ-004 in `tests/unit/agents/codex_worker/test_worker.py`
- [X] T012 [P] Add proposal promotion/service unit test proving promoted task creation preserves canonical nested proposal intent and does not synthesize root proposal intent for FR-003 and DESIGN-REQ-004 in `tests/unit/workflows/task_proposals/test_service.py`
- [X] T013 [P] Add status vocabulary unit test proving `proposals` is consistently mapped for workflow/API/dashboard surfaces for FR-006, SC-005, and DESIGN-REQ-006 in `tests/unit/api/routers/test_task_dashboard_view_model.py`

### Integration Tests (write first)

- [X] T014 [P] Add failing Temporal workflow integration test using canonical `task.proposeTasks` to invoke proposal activities and report proposal counts for FR-007, SC-002, and DESIGN-REQ-005 in `tests/integration/workflows/temporal/workflows/test_run.py`
- [X] T015 [P] Add failing Temporal workflow integration test proving absent canonical nested proposal opt-in skips proposal activities even if non-canonical metadata is present for FR-004, FR-005, SC-004, and DESIGN-REQ-005 in `tests/integration/workflows/temporal/workflows/test_run.py`
- [X] T016 [P] Add service-boundary test for promoted task creation payload normalization for FR-003, SC-001, and DESIGN-REQ-004 in `tests/unit/workflows/task_proposals/test_service.py`; if T004 finds a separate scheduler file with proposal-intent writes, add a new explicit follow-up task naming that file before implementation

### Red-First Confirmation

- [X] T017 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/workflows/test_run_proposals.py tests/unit/agents/codex_worker/test_worker.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/workflows/task_proposals/test_service.py` and confirm T007-T013 fail for expected missing behavior before production edits
- [ ] T018 Run `pytest tests/integration/workflows/temporal/workflows/test_run.py -k proposals -q --tb=short` or the narrowed integration command selected in T016 and confirm T014-T016 fail for expected missing behavior before production edits

### Implementation

- [X] T019 Remove root-level proposal intent writes for new task-shaped execution submissions while preserving nested `task.proposeTasks` and `task.proposalPolicy` for FR-001, FR-002, FR-004, SC-001, SC-004, DESIGN-REQ-003, and DESIGN-REQ-005 in `api_service/api/routers/executions.py`
- [X] T020 Update API tests and any local assertions that currently expect root `initial_parameters["proposeTasks"]` for FR-001 and FR-004 in `tests/unit/api/routers/test_executions.py`
- [X] T021 Implement missing canonical proposal intent propagation for proposal promotion in `moonmind/workflows/task_proposals/service.py` and API submission in `api_service/api/routers/executions.py` for FR-003, SC-001, and DESIGN-REQ-004; if T004 finds a separate scheduler proposal-intent writer, add a new explicit task naming that file before editing it
- [X] T022 Tighten or document workflow compatibility helper behavior so root proposal intent is only a previous-payload read path and nested values win on conflict for FR-005, SC-003, and DESIGN-REQ-005 in `moonmind/workflows/temporal/workflows/run.py`
- [X] T023 Implement any Codex managed-session canonical payload corrections exposed by T011 for FR-003, FR-004, and DESIGN-REQ-004 in `moonmind/agents/codex_worker/worker.py`
- [X] T024 Implement status vocabulary consistency fixes exposed by T013-T015 for FR-006, SC-005, and DESIGN-REQ-006 in `moonmind/workflows/temporal/workflows/run.py`, `api_service/api/routers/executions.py`, `api_service/api/routers/task_dashboard_view_model.py`, or `frontend/src/utils/executionStatusPillClasses.ts`
- [X] T025 Update `docs/Tasks/TaskProposalSystem.md` only if implementation changes desired-state proposal vocabulary or submission contract references for FR-006, FR-008, SC-006, and DESIGN-REQ-006

### Story Validation

- [X] T026 Run the targeted unit command from T017 and confirm all unit tests pass for FR-001 through FR-007 and DESIGN-REQ-003 through DESIGN-REQ-006
- [ ] T027 Run the targeted integration command from T018 and confirm proposal workflow behavior passes for acceptance scenarios 4-6 and SC-002 through SC-004
- [X] T028 Run `rg -n "MM-595|DESIGN-REQ-003|DESIGN-REQ-004|DESIGN-REQ-005|DESIGN-REQ-006" specs/309-normalize-proposal-intent docs/Tasks/TaskProposalSystem.md` and confirm traceability for FR-008 and SC-006

**Checkpoint**: The MM-595 story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Final Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T029 [P] Refactor duplicated proposal-intent test fixtures in `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/temporal/workflows/test_run_proposals.py`, and `tests/unit/agents/codex_worker/test_worker.py` if the implementation created avoidable duplication
- [X] T030 [P] Review changed files for secret-like strings and ensure no raw credentials, tokens, or environment dumps were added in code, tests, docs, or artifacts
- [ ] T031 Run `./tools/test_unit.sh` for the full unit suite and fix failures related to MM-595
- [ ] T032 Run `./tools/test_integration.sh` for hermetic integration coverage and document any environment blocker exactly
- [ ] T033 Run quickstart validation from `specs/309-normalize-proposal-intent/quickstart.md` and record commands/results for final verification
- [ ] T034 Run `/speckit.verify` against `specs/309-normalize-proposal-intent/spec.md` after implementation and tests pass, and preserve the verdict in `specs/309-normalize-proposal-intent/verification.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks production implementation.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on story implementation and targeted tests passing.

### Within The Story

- T007-T016 must be authored before production tasks T019-T025.
- T017-T018 must confirm red-first failures before T019-T025 begin.
- T019 and T020 are the first implementation pair because they remove the known root write contract.
- T021, T023, and T024 can proceed after their corresponding failing tests identify gaps.
- T026-T028 validate the story before any polish task.
- T034 is last and only runs after implementation and tests pass.

### Parallel Opportunities

- T003 and T006 can run in parallel after T001.
- T007-T013 can run in parallel because they touch distinct test files or independent sections.
- T014-T016 can run in parallel after T004-T006.
- T021, T023, and T024 can run in parallel if their tests expose changes in disjoint files.
- T029 and T030 can run in parallel after T026-T028 pass.

## Parallel Example

```bash
# After foundational mapping is complete, author independent failing tests in parallel:
Task: "T007 API no-root proposal intent test in tests/unit/api/routers/test_executions.py"
Task: "T009 workflow nested-precedence test in tests/unit/workflows/temporal/workflows/test_run_proposals.py"
Task: "T011 Codex canonical task flag test in tests/unit/agents/codex_worker/test_worker.py"
Task: "T013 status vocabulary test in tests/unit/api/routers/test_task_dashboard_view_model.py"
```

## Implementation Strategy

1. Confirm active artifacts and source traceability.
2. Map exact new-write surfaces and compatibility-read boundaries.
3. Write unit and integration tests first for partial and implemented_unverified rows.
4. Confirm red-first failures for the missing or partial behavior.
5. Remove root-level proposal intent from new writes while preserving nested policy and in-flight compatibility reads.
6. Fill any propagation or status-vocabulary gaps exposed by tests.
7. Run targeted unit, targeted integration, traceability, full unit, full integration, quickstart, and `/speckit.verify`.

## Notes

- This task list covers exactly one story: MM-595 canonical proposal intent.
- FR-007 is currently implemented_verified, but it remains covered by regression and final verification tasks.
- Compatibility reads are allowed only for previous payload shapes; new submissions must not write root proposal intent.
- Commit text and PR metadata must include MM-595 after implementation is complete.

## Implementation Verification Notes

- Red-first unit evidence: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/workflows/test_run_proposals.py tests/unit/agents/codex_worker/test_worker.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/workflows/task_proposals/test_service.py` failed before production edits for the expected missing API root-write removal, workflow nested-precedence, Codex explicit opt-in, and proposal-promotion default behavior.
- Targeted unit evidence after implementation: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/workflows/test_run_proposals.py tests/unit/workflows/temporal/test_run_artifacts.py::test_run_proposals_stage_uses_task_proposal_policy tests/unit/agents/codex_worker/test_worker.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/workflows/task_proposals/test_service.py` passed with 384 Python tests plus frontend unit coverage.
- Targeted Temporal integration blocker: `pytest tests/integration/workflows/temporal/workflows/test_run.py -k proposals -q --tb=short` timed out at 5 minutes twice inside the Temporal test-server workflow run before producing assertions.
- Hermetic integration blocker: `./tools/test_integration.sh` cannot connect to Docker because `/var/run/docker.sock` is absent in this managed container.
- Full unit blocker: `./tools/test_unit.sh` still fails on unrelated pre-existing/projection issues involving missing active `.agents/skills` PR-resolver/fix-comments files and missing `docs/tmp/remaining-work` tracker files; the MM-595 proposal-related unit failure found by the full run was fixed and its targeted regression now passes.
