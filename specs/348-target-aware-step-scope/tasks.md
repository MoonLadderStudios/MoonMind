# Tasks: Target-Aware Step Execution Scope

**Input**: Design documents from `specs/348-target-aware-step-scope/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/step-context-scope.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write verification tests first, run them before fallback implementation work, and implement only the conditional code changes exposed by failing verification.

**Organization**: Tasks cover exactly one user story: Step-Scoped Execution Context.

**Source Traceability**: MM-649 and the canonical Jira preset brief are preserved in `spec.md`. This task list covers FR-001 through FR-009, acceptance scenarios 1-5, edge cases, SC-001 through SC-005, DESIGN-REQ-001, and DESIGN-REQ-002. Original Jira coverage IDs DESIGN-REQ-021 and DESIGN-REQ-022 are preserved through the DESIGN-REQ-001 and DESIGN-REQ-002 source mappings.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Focused unit iteration: `pytest tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py tests/unit/workflows/temporal/workflows/test_agent_run_prepared_context.py tests/unit/specs/test_mm649_traceability.py -q --tb=short`
- Integration tests: `./tools/test_integration.sh`
- Focused integration iteration: `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -m 'integration_ci' -q --tb=short`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when tasks touch different files and have no dependency on incomplete work.
- Every task includes exact file paths and the relevant requirement, scenario, success criterion, or source-design IDs.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the feature artifact set and existing test surfaces before story work.

- [ ] T001 Confirm active feature artifacts exist at `specs/348-target-aware-step-scope/spec.md`, `specs/348-target-aware-step-scope/plan.md`, `specs/348-target-aware-step-scope/research.md`, `specs/348-target-aware-step-scope/data-model.md`, `specs/348-target-aware-step-scope/contracts/step-context-scope.md`, and `specs/348-target-aware-step-scope/quickstart.md` for FR-009 and SC-005.
- [ ] T002 Review existing prepared-context and workflow boundary tests in `tests/unit/workflows/tasks/test_prepared_context.py`, `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py`, and `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` for FR-001, FR-002, FR-004, FR-005, FR-008, and DESIGN-REQ-001.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish traceability and boundary assumptions before adding story verification tests.

**CRITICAL**: No story implementation or fallback code changes can begin until this phase is complete.

- [ ] T003 Verify `specs/348-target-aware-step-scope/spec.md` contains exactly one `## User Story -` section and no `[NEEDS CLARIFICATION]` markers for the single-story gate.
- [ ] T004 Build a requirement coverage checklist from `specs/348-target-aware-step-scope/plan.md` covering all `implemented_verified`, `implemented_unverified`, and `missing` rows for FR-001 through FR-009, SCN-001 through SCN-005, SC-001 through SC-005, DESIGN-REQ-001, DESIGN-REQ-002, and original Jira coverage IDs DESIGN-REQ-021 through DESIGN-REQ-022.
- [ ] T005 Confirm no new persistent storage or new external provider dependency is needed by reviewing `specs/348-target-aware-step-scope/data-model.md` and `specs/348-target-aware-step-scope/contracts/step-context-scope.md` for Constitution principles II, III, IV, IX, and XIII.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Step-Scoped Execution Context

**Summary**: As an execution-plane engineer, I want each step and delegated AgentRun child to receive only the prepared context relevant to that step so attachments cannot leak across step boundaries.

**Independent Test**: Run or simulate a task with objective context and at least two steps that each have distinct prepared attachments, then verify each step runtime and AgentRun child receives objective context plus only its own step-scoped prepared context, with diagnostics preserving the parent workflow as the target-binding authority.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002.

**Unit Test Plan**:

- Preserve existing verified unit coverage for current-step-only context selection.
- Add verification-first unit coverage for same-workspace exclusion, parent authority metadata, child diagnostic non-redefinition, and MM-649 traceability.

**Integration Test Plan**:

- Preserve existing run-boundary integration coverage for objective plus current-step refs.
- Add verification-first integration coverage for AgentRun child input scoping and same-workspace prepared attachments.

### Unit Tests (write first)

- [ ] T006 [P] Add verification-first unit test for same-workspace prepared attachments excluding sibling-step refs in `tests/unit/workflows/tasks/test_prepared_context.py` covering FR-003, SCN-002, SC-003, and DESIGN-REQ-001.
- [ ] T007 [P] Add verification-first unit test for parent-owned prepared context metadata on AgentRun child requests in `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py` covering FR-006, SCN-003, SC-002, and DESIGN-REQ-002.
- [ ] T008 [P] Add verification-first unit test proving child workflow result metadata or diagnostics do not redefine parent target binding in `tests/unit/workflows/temporal/workflows/test_agent_run_prepared_context.py` covering FR-007, SCN-004, SC-004, and DESIGN-REQ-002.
- [ ] T009 [P] Add MM-649 traceability unit test in `tests/unit/specs/test_mm649_traceability.py` covering FR-009 and SC-005 across `specs/348-target-aware-step-scope/spec.md`, `plan.md`, and `tasks.md`.
- [ ] T010 Run `pytest tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py tests/unit/workflows/temporal/workflows/test_agent_run_prepared_context.py tests/unit/specs/test_mm649_traceability.py -q --tb=short` and record which verification tests pass, fail, or require fallback implementation for FR-003, FR-006, FR-007, FR-009, SC-003, SC-004, SC-005, and DESIGN-REQ-002.

### Integration Tests (write first)

- [ ] T011 [P] Add verification-first integration test for AgentRun child input scoping in `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` covering FR-005, FR-006, SC-002, SCN-003, and DESIGN-REQ-002.
- [ ] T012 [P] Add verification-first integration test for same-workspace prepared attachment materialization excluding sibling-step refs in `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` covering FR-003, SCN-002, SC-003, and DESIGN-REQ-001.
- [ ] T013 Run `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -m 'integration_ci' -q --tb=short` and record which verification tests pass, fail, or require fallback implementation for FR-003, FR-005, FR-006, SC-002, SC-003, and DESIGN-REQ-002.

### Red-First Confirmation

- [ ] T014 Confirm T006-T013 were executed before production code changes; for `implemented_unverified` rows, mark passing verification tasks as no-code-needed and failing verification tasks as requiring the conditional fallback implementation in `specs/348-target-aware-step-scope/tasks.md`.
- [ ] T015 Confirm existing verified behavior remains covered by current tests before fallback changes by reviewing T010 and T013 output for FR-001, FR-002, FR-004, FR-005, FR-008, SCN-001, SCN-003, SC-001, and DESIGN-REQ-001.

### Conditional Fallback Implementation

- [ ] T016 If T006 or T012 fails, update prepared-context selection or metadata handling in `moonmind/workflows/tasks/prepared_context.py` so same-workspace manifests still expose only objective plus current-step refs for FR-003, SCN-002, SC-003, and DESIGN-REQ-001.
- [ ] T017 If T007 or T011 fails, update parent workflow request assembly in `moonmind/workflows/temporal/workflows/run.py` so `parameters.metadata.moonmind.preparedContext` remains parent-owned and scoped to the represented step for FR-006, SC-002, and DESIGN-REQ-002.
- [ ] T018 If T008 fails, update child workflow result or diagnostic metadata handling in `moonmind/workflows/temporal/workflows/agent_run.py` so child diagnostics preserve parent target binding without redefining or broadening scope for FR-007, SCN-004, SC-004, and DESIGN-REQ-002.
- [ ] T019 If T016-T018 change workflow boundary payloads, update or add compatibility-sensitive regression coverage in `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py`, `tests/unit/workflows/temporal/workflows/test_agent_run_prepared_context.py`, or `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` for Constitution principle IX and DESIGN-REQ-002.

### Story Validation

- [ ] T020 Run focused unit command `pytest tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py tests/unit/workflows/temporal/workflows/test_agent_run_prepared_context.py tests/unit/specs/test_mm649_traceability.py -q --tb=short` after any fallback implementation and confirm FR-001 through FR-009 coverage.
- [ ] T021 Run focused integration command `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -m 'integration_ci' -q --tb=short` after any fallback implementation and confirm SCN-001 through SCN-005 and SC-001 through SC-004 coverage.
- [ ] T022 Validate the story against `specs/348-target-aware-step-scope/quickstart.md` and `specs/348-target-aware-step-scope/contracts/step-context-scope.md`, confirming objective refs, current-step refs, AgentRun child inputs, diagnostics, and sibling-step exclusions for FR-001 through FR-008 and DESIGN-REQ-001 through DESIGN-REQ-002.

**Checkpoint**: The single story is verified, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T023 [P] Update implementation notes or verification notes in `specs/348-target-aware-step-scope/research.md` only if fallback implementation changes alter the plan assumptions for FR-003, FR-006, FR-007, or DESIGN-REQ-002.
- [ ] T024 [P] Confirm `MM-649`, the canonical Jira preset brief, FR-001 through FR-009, SC-001 through SC-005, DESIGN-REQ-001, DESIGN-REQ-002, and original Jira coverage IDs DESIGN-REQ-021 through DESIGN-REQ-022 remain present in `specs/348-target-aware-step-scope/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/step-context-scope.md`, `quickstart.md`, and `tasks.md`.
- [ ] T025 Run `./tools/test_unit.sh` for final unit verification covering FR-001 through FR-009 and DESIGN-REQ-001 through DESIGN-REQ-002.
- [ ] T026 Run `./tools/test_integration.sh` for final hermetic integration verification covering SCN-001 through SCN-005 and SC-001 through SC-004.
- [ ] T027 Run `/moonspec-verify` for `specs/348-target-aware-step-scope/spec.md` after implementation and tests pass, preserving MM-649, the canonical Jira preset brief, FR-001 through FR-009, SC-001 through SC-005, DESIGN-REQ-001, DESIGN-REQ-002, and original Jira coverage IDs DESIGN-REQ-021 through DESIGN-REQ-022 in final verification evidence.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish & Verification (Phase 4)**: Depends on story validation and passing focused tests.

### Within The Story

- Unit verification tasks T006-T010 must run before fallback implementation tasks T016-T019.
- Integration verification tasks T011-T013 must run before fallback implementation tasks T016-T019.
- Red-first confirmation tasks T014-T015 must complete before production code fallback changes.
- Conditional fallback implementation tasks T016-T019 are skipped when verification tests pass.
- Story validation tasks T020-T022 run after tests and any fallback implementation.
- Final verification tasks T025-T027 run after focused story validation passes.

### Parallel Opportunities

- T006, T007, T008, and T009 can run in parallel because they target distinct unit test files.
- T011 and T012 can be authored together only if coordinated carefully in the same integration test file.
- T023 and T024 can run in parallel after story validation because they touch documentation/traceability surfaces.

---

## Parallel Example: Story Phase

```bash
# Launch independent unit test authoring together:
Task: "Add same-workspace prepared-context unit coverage in tests/unit/workflows/tasks/test_prepared_context.py"
Task: "Add parent authority request metadata unit coverage in tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py"
Task: "Add child diagnostic non-redefinition unit coverage in tests/unit/workflows/temporal/workflows/test_agent_run_prepared_context.py"
Task: "Add MM-649 traceability unit coverage in tests/unit/specs/test_mm649_traceability.py"
```

---

## Implementation Strategy

### Verification-First Story Delivery

1. Complete Phase 1 and Phase 2 artifact/traceability checks.
2. Add unit verification tests for all `implemented_unverified` and `missing` rows.
3. Add integration verification tests for workflow and AgentRun child boundary scenarios.
4. Run focused tests before production code changes.
5. If verification passes, skip conditional fallback implementation tasks and preserve evidence.
6. If verification fails, complete only the matching fallback implementation tasks.
7. Re-run focused unit and integration commands.
8. Run full `./tools/test_unit.sh` and `./tools/test_integration.sh`.
9. Run final `/moonspec-verify`.

### Requirement Status Handling

- Already verified: FR-001, FR-002, FR-004, FR-005, FR-008, SCN-001, SCN-003, SC-001, DESIGN-REQ-001; preserve with final validation only.
- Verification-only first: FR-003, FR-006, FR-007, SCN-002, SCN-004, SCN-005, SC-002, SC-003, SC-004, DESIGN-REQ-002.
- Missing traceability/final evidence: FR-009, SC-005, and original Jira coverage IDs DESIGN-REQ-021 through DESIGN-REQ-022; cover through T009, T024, and T027.
- Conditional fallback code changes: T016-T019 only run if verification tests fail.

---

## Notes

- This task list covers exactly one story.
- Do not add new persistent storage, provider credentials, or broad runtime refactors.
- Preserve workflow payload compatibility for parent-child AgentRun boundaries.
- Do not introduce compatibility aliases or fallback transforms that silently change target semantics.
- Keep large or binary attachment content out of workflow-visible payloads.
