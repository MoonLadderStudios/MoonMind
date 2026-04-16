# Tasks: Simplify Orchestrate Summary

**Input**: `specs/193-simplify-orchestrate-summary/spec.md`  
**Plan**: `specs/193-simplify-orchestrate-summary/plan.md`  
**Contracts**: `specs/193-simplify-orchestrate-summary/contracts/preset-summary-ownership.md`  
**Unit command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py`  
**Integration command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py` for seed expansion contract coverage; run `./tools/test_integration.sh` only if workflow runtime code changes.

## Traceability Inventory

- FR-001, FR-005, SC-003: Covered by existing workflow finalization contract review and task T012.
- FR-002, SC-001, Scenario 1: Covered by Jira preset expansion tests T003-T004 and YAML implementation T006.
- FR-003, SC-002, Scenario 2: Covered by MoonSpec preset expansion tests T003-T004 and YAML implementation T007.
- FR-004, Scenario 3: Covered by handoff preservation tests T003-T004 and implementation guardrails T006-T007.
- FR-006, SC-004, Scenario 5: Covered by docs/contract update T008 and validation T012.
- FR-007, SC-005: Covered by spec artifact preservation and verification tasks T011-T013.
- Edge cases: Covered by red-first checks T005, implementation tasks T006-T008, and validation T012.

## Phase 1: Setup

- [X] T001 Confirm active feature directory and inspect seed template/test surfaces in `.specify/feature.json`, `api_service/data/task_step_templates/jira-orchestrate.yaml`, `api_service/data/task_step_templates/moonspec-orchestrate.yaml`, and `tests/unit/api/test_task_step_templates_service.py`

## Phase 2: Foundational

- [X] T002 Confirm workflow finalization already writes the canonical finish summary in `moonmind/workflows/temporal/workflows/run.py` and document that no workflow code change is needed in `specs/193-simplify-orchestrate-summary/research.md`

## Phase 3: Story - Workflow-Owned Finish Summary

**Story Summary**: Remove generic final narrative report steps from Jira and MoonSpec orchestration presets while preserving structured handoff data and workflow-owned finish summaries.

**Independent Test**: Expand the seeded Jira and MoonSpec orchestration presets and verify no generic final report steps remain, required structured handoff references remain, and workflow finalization continues to own the finish summary contract.

### Tests First

- [X] T003 Add failing unit assertions for `jira-orchestrate` report-step removal and PR handoff preservation in `tests/unit/api/test_task_step_templates_service.py` for FR-002, FR-004, SC-001, Scenario 1, and Scenario 3
- [X] T004 Add failing unit assertions for `moonspec-orchestrate` report-step removal and verification-stage preservation in `tests/unit/api/test_task_step_templates_service.py` for FR-003, FR-004, SC-002, and Scenario 2
- [X] T005 Run red-first focused unit command `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py` and confirm the new assertions fail before YAML changes

### Implementation

- [X] T006 Remove the generic final Jira orchestration report step from `api_service/data/task_step_templates/jira-orchestrate.yaml` while preserving PR creation, `artifacts/jira-orchestrate-pr.json`, and Jira Code Review transition instructions for FR-002 and FR-004
- [X] T007 Remove the generic final MoonSpec orchestration report / publish narration step from `api_service/data/task_step_templates/moonspec-orchestrate.yaml` while preserving verification as the final operational stage for FR-003 and FR-004
- [X] T008 Clarify canonical finish summary ownership versus optional preset structured outputs in `docs/Tasks/TaskFinishSummarySystem.md` for FR-001, FR-005, FR-006, SC-003, and SC-004
- [X] T009 Update expected seeded step counts, skill sequences, and expanded step indexes in `tests/unit/api/test_task_step_templates_service.py` after report-step removal for FR-002 and FR-003

### Story Validation

- [X] T010 Run focused unit command `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py` and confirm it passes
- [X] T011 Confirm MM-366 remains preserved in `specs/193-simplify-orchestrate-summary/spec.md`, `specs/193-simplify-orchestrate-summary/tasks.md`, and verification evidence for FR-007 and SC-005

## Final Phase: Polish And Verification

- [X] T012 Run or explicitly justify integration coverage for terminal success, failure, cancellation, and no-change summary contract evidence using `moonmind/workflows/temporal/workflows/run.py`, `docs/Tasks/TaskFinishSummarySystem.md`, and targeted tests for FR-001, FR-005, and SC-003
- [X] T013 Run final unit verification `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for the completed story
- [X] T014 Run `/speckit.verify` equivalent with `moonspec-verify` and record the verdict in `specs/193-simplify-orchestrate-summary/verification.md`

## Dependencies And Order

1. T001 and T002 establish the current runtime and test surfaces.
2. T003 and T004 write failing tests before implementation.
3. T005 confirms red-first behavior.
4. T006 through T009 implement the YAML, docs, and test expectation changes.
5. T010 through T014 validate and verify completion.

## Parallel Opportunities

- T003 and T004 both touch the same test file and should not run in parallel.
- T006 and T007 touch different YAML files and can be done together after red-first confirmation.
- T008 touches documentation and can proceed in parallel with T006/T007 after test failures are confirmed.

## Implementation Strategy

Keep the change scoped to report-only preset steps. Do not remove operational gates, PR creation, Jira Code Review transition, MoonSpec verification, or structured handoff artifacts. Treat `reports/run_summary.json` from workflow finalization as the canonical summary owner and add test assertions at the seed catalog boundary where preset behavior is loaded and expanded.
