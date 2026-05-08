# Tasks: Prepare Target-Aware Inputs

**Input**: Design documents from `/work/agent_jobs/mm:33deef30-3a30-4229-8cae-32c434b9aad5/repo/specs/325-prepare-target-aware-inputs/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/target-aware-prepared-context.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: `Prepare Step-Scoped Inputs`.

**Source Traceability**: Preserves Jira issue `MM-631`, the original Jira preset brief in `spec.md`, FR-001 through FR-013, SCN-001 through SCN-006, SC-001 through SC-005, edge cases, and DESIGN-REQ-001 through DESIGN-REQ-007.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify` (`/speckit.verify` equivalent)

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when files do not overlap and no dependency is incomplete
- Every task includes an exact file path and traceability IDs where applicable

## Requirement Status Summary

- Already verified rows preserved by regression/final validation: FR-001, FR-002, FR-003, DESIGN-REQ-001
- Verification-only rows with conditional fallback: FR-013, SC-005
- Code-and-test rows: FR-004 through FR-012, SCN-001 through SCN-006, SC-001 through SC-004, DESIGN-REQ-002 through DESIGN-REQ-007
- Missing highest-risk boundary rows: FR-009, SCN-004, SC-003, DESIGN-REQ-005

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm planning artifacts, test paths, and feature scope before writing red tests.

- [ ] T001 Confirm `specs/325-prepare-target-aware-inputs/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/target-aware-prepared-context.md`, and `quickstart.md` exist and preserve `MM-631`. (FR-013, SC-005)
- [ ] T002 Confirm `.specify/feature.json` points to `specs/325-prepare-target-aware-inputs` and that this task list covers exactly one `## User Story -` section in `specs/325-prepare-target-aware-inputs/spec.md`. (FR-013, SC-005)
- [ ] T003 [P] Inspect existing unit fixtures in `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/moonmind/vision/test_service.py`, and `tests/unit/agents/codex_worker/test_worker.py` for reusable target-aware attachment payload builders. (FR-001, FR-002, FR-003, DESIGN-REQ-001)
- [ ] T004 [P] Inspect existing Temporal workflow test fixtures in `tests/unit/workflows/temporal/workflows/conftest.py` and `tests/integration/workflows/temporal/workflows/test_run.py` for child `AgentRun` dispatch interception patterns. (FR-004, FR-009, DESIGN-REQ-005)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the red-test surfaces and shared contract locations before story implementation begins.

**CRITICAL**: No production implementation work can begin until Phase 2 and the red-first test authoring tasks in Phase 3 are complete.

- [ ] T005 Create placeholder test module `tests/unit/workflows/tasks/test_prepared_context.py` for prepared input manifest, step filtering, and failure-model unit coverage. (FR-004, FR-005, FR-006, FR-007, FR-008, FR-010, FR-012, SCN-001, SCN-002, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004)
- [ ] T006 Create placeholder workflow unit test module `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py` for `MoonMind.Run` prepared-context state and child request coverage. (FR-004, FR-005, FR-008, FR-009, FR-010, SCN-003, SCN-004, SC-001, SC-003, DESIGN-REQ-002, DESIGN-REQ-005, DESIGN-REQ-007)
- [ ] T007 Create placeholder integration test module `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` for hermetic workflow boundary scenarios. (SCN-001, SCN-003, SCN-004, SCN-006, SC-001, SC-002, SC-003, SC-004)
- [ ] T008 [P] Create placeholder adapter unit test module `tests/unit/workflows/adapters/test_target_aware_prepared_context.py` for adapter-visible prepared context contract checks. (FR-011, SCN-005, DESIGN-REQ-006)
- [ ] T009 [P] Confirm planned implementation path `moonmind/workflows/tasks/prepared_context.py` is reserved for compact prepared input contracts and pure filtering helpers without adding behavior before red tests. (FR-004, FR-005, FR-007, FR-008, FR-010, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004)

**Checkpoint**: Test and contract locations exist; story test authoring can begin.

---

## Phase 3: Story - Prepare Step-Scoped Inputs

**Summary**: As a runtime step, I want prepared context to include relevant objective inputs and only my own step-scoped inputs so that attachments are materialized, contextualized, and passed across workflow boundaries without leaking unrelated inputs.

**Independent Test**: Submit or construct a task with objective-scoped attachments and multiple step-scoped attachments, then verify preparation produces a canonical manifest and each step, including a delegated child step, receives only the prepared context targeted to it.

**Traceability**: FR-001 through FR-013; SCN-001 through SCN-006; SC-001 through SC-005; DESIGN-REQ-001 through DESIGN-REQ-007; MM-631.

**Unit Test Plan**:

- Validate prepared manifest/result contracts, objective vs step target derivation, step filtering, large-content guardrails, and failure payloads.
- Preserve existing verified behavior for text/binary separation and derived image context as secondary data.
- Verify adapter-visible context cannot broaden target binding.

**Integration Test Plan**:

- Verify `MoonMind.Run` prepares objective and step attachments before affected step dispatch.
- Verify child `MoonMind.AgentRun` requests receive only objective plus represented-step context.
- Verify missing or unauthorized preparation fails before affected step execution.

### Unit Tests (write first)

- [ ] T010 [P] Add failing unit tests for `PreparedInputManifest`, `PreparedInputEntry`, and `StepPreparedContext` validation in `tests/unit/workflows/tasks/test_prepared_context.py`. (FR-004, FR-005, FR-007, FR-010, SCN-001, SCN-002, DESIGN-REQ-002, DESIGN-REQ-003)
- [ ] T011 [P] Add failing unit tests proving objective context plus only current-step entries are selected in `tests/unit/workflows/tasks/test_prepared_context.py`. (FR-008, SCN-003, SC-001, DESIGN-REQ-004)
- [ ] T012 [P] Add failing unit tests proving unrelated step refs, embedded binary, data URLs, and generated markdown are rejected or omitted in `tests/unit/workflows/tasks/test_prepared_context.py`. (FR-001, FR-002, FR-003, FR-008, FR-010, DESIGN-REQ-001, DESIGN-REQ-004)
- [ ] T013 [P] Add failing unit tests for bounded prepare failure payloads in `tests/unit/workflows/tasks/test_prepared_context.py`. (FR-006, FR-012, SCN-006, SC-004, DESIGN-REQ-003)
- [ ] T014 [P] Add failing adapter contract tests proving prepared context refs are consumed without inventing target rules in `tests/unit/workflows/adapters/test_target_aware_prepared_context.py`. (FR-011, SCN-005, DESIGN-REQ-006)
- [ ] T015 [P] Add regression unit tests proving existing `TaskInputAttachmentRef` and `VisionService.render_target_contexts` behavior remains compatible in `tests/unit/workflows/tasks/test_task_contract.py` and `tests/unit/moonmind/vision/test_service.py`. (FR-001, FR-002, FR-003, DESIGN-REQ-001)

### Integration Tests (write first)

- [ ] T016 [P] Add failing workflow unit test proving `MoonMind.Run` records a prepared manifest ref before the first affected step dispatch in `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py`. (FR-004, FR-005, FR-007, FR-010, SCN-001, SC-002, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-007)
- [ ] T017 [P] Add failing workflow unit test proving step dispatch receives objective plus current-step prepared context and omits later-step context in `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py`. (FR-008, SCN-003, SC-001, DESIGN-REQ-004)
- [ ] T018 [P] Add failing workflow unit test proving delegated child `AgentRun` request `inputRefs` or metadata contains only represented-step prepared context in `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py`. (FR-009, SCN-004, SC-003, DESIGN-REQ-005, DESIGN-REQ-007)
- [ ] T019 [P] Add failing workflow unit test proving prepare failure prevents affected step dispatch and records bounded diagnostics in `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py`. (FR-006, FR-012, SCN-006, SC-004, DESIGN-REQ-003)
- [ ] T020 [P] Add failing hermetic integration scenario with one objective attachment and two step attachments in `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py`. (SCN-001, SCN-003, SCN-004, SC-001, SC-002, SC-003)

### Red-First Confirmation

- [ ] T021 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/adapters/test_target_aware_prepared_context.py` and confirm T010-T014 fail for missing prepared-context contracts/helpers. (FR-004 through FR-012, SCN-001 through SCN-006)
- [ ] T022 Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py` and confirm T016-T019 fail for missing `MoonMind.Run` prepared-context wiring. (FR-004, FR-005, FR-008, FR-009, FR-010, FR-012, DESIGN-REQ-002, DESIGN-REQ-005, DESIGN-REQ-007)
- [ ] T023 Run `./tools/test_integration.sh` or a focused equivalent for `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` and confirm T020 fails for missing workflow boundary behavior. (SC-001, SC-002, SC-003, SC-004)

### Conditional Fallback for Verification-Only Rows

- [ ] T024 If T001-T002 or final verification show `MM-631` traceability is missing, update `specs/325-prepare-target-aware-inputs/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/target-aware-prepared-context.md`, and `tasks.md` to restore the issue key and original preset brief references. (FR-013, SC-005)

### Implementation

- [ ] T025 Implement compact prepared input models, validation, target derivation, step filtering, and failure payload helpers in `moonmind/workflows/tasks/prepared_context.py`. (FR-004, FR-005, FR-007, FR-008, FR-010, FR-012, SCN-001, SCN-002, SCN-003, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004)
- [ ] T026 Export prepared context helpers from `moonmind/workflows/tasks/__init__.py` for workflow and adapter use. (FR-004, FR-005, FR-008, FR-010)
- [ ] T027 Implement or extend runtime prepare activity/service behavior in `moonmind/workflows/temporal/activity_runtime.py` to produce compact manifest/context refs and explicit prepare failures. (FR-004, FR-006, FR-007, FR-012, SCN-001, SCN-002, SCN-006, SC-002, SC-004, DESIGN-REQ-003)
- [ ] T028 Wire `MoonMind.Run` prepare orchestration and bounded prepared-context state in `moonmind/workflows/temporal/workflows/run.py`. (FR-004, FR-005, FR-010, SCN-001, SCN-002, DESIGN-REQ-002, DESIGN-REQ-007)
- [ ] T029 Wire per-step prepared context filtering in `moonmind/workflows/temporal/workflows/run.py` before planner, step runtime, or child dispatch. (FR-008, SCN-003, SC-001, DESIGN-REQ-004)
- [ ] T030 Wire child `AgentRun` request `inputRefs` or bounded metadata in `moonmind/workflows/temporal/workflows/run.py` so represented child steps receive only relevant objective and current-step context. (FR-009, SCN-004, SC-003, DESIGN-REQ-005, DESIGN-REQ-007)
- [ ] T031 Update `moonmind/workflows/temporal/workflows/agent_run.py` only if child request validation or metadata propagation requires accepting the prepared-context contract. (FR-009, FR-011, DESIGN-REQ-005, DESIGN-REQ-006)
- [ ] T032 Update runtime adapter realization in `moonmind/workflows/adapters/codex_session_adapter.py` and `moonmind/workflows/adapters/managed_agent_adapter.py` only as needed to consume prepared context refs without broadening target rules. (FR-011, SCN-005, DESIGN-REQ-006)
- [ ] T033 Preserve existing Codex worker materialization behavior in `moonmind/agents/codex_worker/worker.py`, changing it only if the shared prepared-context helpers replace duplicate target filtering safely. (FR-001, FR-002, FR-003, DESIGN-REQ-001, DESIGN-REQ-003)

### Story Validation

- [ ] T034 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py tests/unit/workflows/adapters/test_target_aware_prepared_context.py` and fix failures until unit coverage passes. (FR-001 through FR-012, DESIGN-REQ-001 through DESIGN-REQ-007)
- [ ] T035 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/moonmind/vision/test_service.py tests/unit/agents/codex_worker/test_attachment_materialization.py tests/unit/agents/codex_worker/test_worker.py` and fix regressions in existing verified attachment behavior. (FR-001, FR-002, FR-003, DESIGN-REQ-001)
- [ ] T036 Run `./tools/test_integration.sh` or the focused workflow integration target for `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` and fix failures until the independent story passes. (SCN-001 through SCN-006, SC-001 through SC-004)
- [ ] T037 Update `specs/325-prepare-target-aware-inputs/quickstart.md` only if validated commands or workflow-boundary constraints changed during implementation. (FR-013, SC-005)

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently testable against the MM-631 brief.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T038 [P] Refactor duplicated target filtering or manifest mapping introduced during implementation in `moonmind/workflows/tasks/prepared_context.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/workflows/temporal/workflows/run.py`. (FR-004 through FR-012)
- [ ] T039 [P] Review bounded diagnostics and secret hygiene in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/workflows/run.py`. (FR-010, FR-012, SC-004)
- [ ] T040 [P] Review `specs/325-prepare-target-aware-inputs/contracts/target-aware-prepared-context.md` and `data-model.md` against implemented payload shapes, updating artifacts if behavior changed. (FR-013, SC-005)
- [ ] T041 Run full unit verification with `./tools/test_unit.sh`. (FR-001 through FR-013, DESIGN-REQ-001 through DESIGN-REQ-007)
- [ ] T042 Run hermetic integration verification with `./tools/test_integration.sh`, or document why any required Temporal workflow boundary test remains local-only. (SCN-001 through SCN-006, SC-001 through SC-004)
- [ ] T043 Run `/moonspec-verify` (`/speckit.verify` equivalent) to validate the final implementation against `MM-631`, the original Jira preset brief, `spec.md`, `plan.md`, `tasks.md`, source design mappings, and test evidence. (FR-013, SC-005)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Phase 1 completion and blocks story work.
- **Story (Phase 3)**: Depends on Phase 2 completion.
- **Polish And Verification (Phase 4)**: Depends on story implementation and validation tasks passing.

### Within The Story

- Unit tests T010-T015 must be written before implementation T025-T033.
- Integration tests T016-T020 must be written before implementation T025-T033.
- Red-first confirmations T021-T023 must complete before implementation T025-T033.
- Contract/model helpers T025-T026 should land before activity and workflow wiring T027-T030.
- Child request and adapter work T030-T032 depends on the step prepared context shape from T025.
- Story validation T034-T037 runs after implementation tasks.
- Final `/moonspec-verify` (`/speckit.verify` equivalent) T043 runs only after unit and integration verification.

### Parallel Opportunities

- T003 and T004 can run in parallel.
- T007 and T008 can run in parallel with T005-T006 once paths are agreed.
- T010-T015 can be authored in parallel because they cover separate unit-test concerns.
- T016-T020 can be authored in parallel because they cover separate workflow scenarios.
- T038-T040 can run in parallel after story validation.

## Parallel Example: Story Test Authoring

```bash
# Unit tests in separate files or non-overlapping sections:
Task: "T010 Add prepared manifest validation tests in tests/unit/workflows/tasks/test_prepared_context.py"
Task: "T014 Add adapter contract tests in tests/unit/workflows/adapters/test_target_aware_prepared_context.py"

# Workflow boundary tests by scenario:
Task: "T017 Add step dispatch filtering workflow unit test in tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py"
Task: "T020 Add hermetic integration scenario in tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py"
```

## Implementation Strategy

1. Preserve verified behavior first: keep task input refs, text/binary separation, and vision target context tests green.
2. Add red unit tests for compact prepared context models and pure filtering helpers.
3. Add red workflow and integration tests for `MoonMind.Run` prepare, child `AgentRun` dispatch, and prepare failures.
4. Implement the shared prepared context contract with compact refs and bounded diagnostics.
5. Wire runtime prepare into `MoonMind.Run` before affected step dispatch.
6. Filter prepared context per step before planner, runtime, and child dispatch.
7. Update adapters only where needed to consume the prepared context contract without changing target rules.
8. Run focused unit and integration validation, then full unit and integration suites.
9. Run `/moonspec-verify` (`/speckit.verify` equivalent) and preserve `MM-631` traceability in final evidence.

## Coverage Inventory

- FR-001, FR-002, FR-003, DESIGN-REQ-001: preserved by existing evidence plus regression tests T015, T033, T035, T041.
- FR-004, FR-005, FR-006, FR-007, FR-008, FR-010, FR-012, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007: covered by T010-T013, T016-T019, T025-T029, T034, T036.
- FR-009, SCN-004, SC-003, DESIGN-REQ-005: covered by T018, T020, T030, T031, T036.
- FR-011, SCN-005, DESIGN-REQ-006: covered by T014, T032, T034.
- SCN-001, SCN-002, SCN-003, SCN-006, SC-001, SC-002, SC-004: covered by T016-T020, T027-T030, T036, T042.
- FR-013, SC-005, MM-631 traceability: covered by T001, T002, T024, T037, T040, T043.

## Notes

- This task list intentionally does not create future stories or broad refactors.
- `MoonMind.Run` remains the parent source of truth for target binding; adapters consume prepared refs but must not invent target rules.
- Large or binary content must remain behind artifact or workspace refs and out of workflow history.
