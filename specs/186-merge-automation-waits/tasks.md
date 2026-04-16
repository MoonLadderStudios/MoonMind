# Tasks: Merge Automation Waits

**Input**: Design documents from `specs/186-merge-automation-waits/`  
**Prerequisites**: spec.md, plan.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Unit tests and workflow-boundary tests are required. Write or update tests first, confirm focused failures when feasible, then implement production changes until they pass.

**Source Traceability**: The original MM-351 Jira preset brief is preserved in `specs/186-merge-automation-waits/spec.md`. Tasks cover FR-001 through FR-013, acceptance scenarios 1-8, SC-001 through SC-006, and in-scope DESIGN-REQ-004, DESIGN-REQ-010 through DESIGN-REQ-017, DESIGN-REQ-025, and DESIGN-REQ-029.

**Test Commands**:

- Focused unit/workflow tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/test_run_merge_gate_start.py tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py tests/unit/workflows/temporal/test_temporal_workers.py`
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Hermetic integration: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm MM-351 active feature artifacts and traceability in specs/186-merge-automation-waits/spec.md
- [X] T002 Confirm source design requirements from docs/Tasks/PrMergeAutomation.md are mapped in specs/186-merge-automation-waits/spec.md

## Phase 2: Foundational

- [X] T003 Confirm implementation touchpoints for workflow type, model, activity, worker, parent start, and docs in specs/186-merge-automation-waits/plan.md
- [X] T004 Confirm contract payload and activity names in specs/186-merge-automation-waits/contracts/merge-automation-contract.md

## Phase 3: Story - Wait For Current PR Readiness

**Summary**: `MoonMind.MergeAutomation` waits on state-based, head-SHA-sensitive external readiness and uses signal-first, bounded-polling waits before launching a resolver child run.

**Independent Test**: Run focused Temporal workflow tests with fake readiness and resolver activities for blockers, signals, fallback polling, Continue-As-New preservation, expiration, and gate-open output.

**Traceability**: FR-001 through FR-013; scenarios 1-8; SC-001 through SC-006; DESIGN-REQ-004, DESIGN-REQ-010 through DESIGN-REQ-017, DESIGN-REQ-025, DESIGN-REQ-029.

### Unit And Workflow Tests

- [X] T005 Update model tests for `MoonMind.MergeAutomation`, compact start payload, `publishContextRef`, fallback poll normalization, and missing head SHA in tests/unit/workflows/temporal/test_merge_gate_models.py
- [X] T006 Update helper tests for blocker classification, stale head SHA, sanitized blockers, resolver request publish mode `none`, and Continue-As-New payload preservation in tests/unit/workflows/temporal/test_merge_gate_workflow.py
- [X] T007 Update parent-start tests for canonical `MoonMind.MergeAutomation`, compact `publishContextRef`, merge config, resolver template, and no `MoonMind.MergeGate` alias in tests/unit/workflows/temporal/test_run_merge_gate_start.py
- [X] T008 Update workflow-boundary tests for signal-first wake, configured fallback polling, expired terminal output, deterministic gate-open output, and duplicate resolver prevention in tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py
- [X] T009 Update worker registration tests for `MoonMind.MergeAutomation` and removal of `MoonMind.MergeGate` in tests/unit/workflows/temporal/test_temporal_workers.py
- [X] T010 Run focused tests and confirm failures identify missing MM-351 implementation behavior

### Implementation

- [X] T011 Implement canonical merge automation models and workflow type registration in moonmind/schemas/temporal_models.py, moonmind/workflows/temporal/workers.py, and moonmind/workflows/temporal/worker_entrypoint.py
- [X] T012 Rename activity contracts to `merge_automation.evaluate_readiness` and `merge_automation.create_resolver_run` in moonmind/workflows/temporal/activity_catalog.py and moonmind/workflows/temporal/activity_runtime.py
- [X] T013 Implement compact parent start payload with `publishContextRef`, `mergeAutomationConfig`, `resolverTemplate`, and canonical child workflow type in moonmind/workflows/temporal/workflows/run.py
- [X] T014 Implement lifecycle vocabulary, signal-first waits, bounded fallback polling, expiration, deterministic output, and Continue-As-New payload preservation in moonmind/workflows/temporal/workflows/merge_gate.py
- [X] T015 Update operator-facing workflow and publishing docs from `MoonMind.MergeGate` to `MoonMind.MergeAutomation` in docs/Temporal/WorkflowTypeCatalogAndLifecycle.md and docs/Tasks/TaskPublishing.md
- [X] T016 Run focused tests and fix failures

## Phase 4: Verification

- [X] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/test_run_merge_gate_start.py tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py tests/unit/workflows/temporal/test_temporal_workers.py`
- [X] T018 Run full unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or record exact blocker
- [X] T019 Run hermetic integration verification with `./tools/test_integration.sh` when Docker is available, or record exact Docker blocker in specs/186-merge-automation-waits/verification.md
- [X] T020 Run `/moonspec-verify` for specs/186-merge-automation-waits and record verdict in specs/186-merge-automation-waits/verification.md

## Dependencies & Execution Order

- T005-T009 must be completed before production implementation tasks T011-T015.
- T011 and T012 unblock T014.
- T013 depends on T011.
- T016 runs after T011-T015.
- T017-T020 are final verification tasks.

## Parallel Example

```text
Task: "Update model tests in tests/unit/workflows/temporal/test_merge_gate_models.py"
Task: "Update worker registration tests in tests/unit/workflows/temporal/test_temporal_workers.py"
Task: "Update docs in docs/Temporal/WorkflowTypeCatalogAndLifecycle.md and docs/Tasks/TaskPublishing.md"
```
