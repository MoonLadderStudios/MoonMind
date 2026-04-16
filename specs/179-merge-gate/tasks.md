# Tasks: Merge Gate

**Input**: Design documents from `/specs/179-merge-automation/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped by phase around the single story "Gate Pull Requests Before Automated Resolution" so the work stays focused, traceable, and independently testable.

**Source Traceability**: The original MM-341 Jira preset brief is preserved in `specs/179-merge-automation/spec.md`. Tasks reference FR-001 through FR-012, acceptance scenarios 1-7, edge cases, and SC-001 through SC-005.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/test_run_merge_gate_start.py`
- Workflow-boundary tests: `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Establish feature scaffolding and shared test locations without changing runtime behavior.

- [X] T001 Confirm MM-341 active feature artifacts and traceability references in specs/179-merge-automation/spec.md
- [X] T002 Create merge-automation workflow module placeholder in moonmind/workflows/temporal/workflows/merge_gate.py
- [X] T003 [P] Create unit test module placeholder for models in tests/unit/workflows/temporal/test_merge_gate_models.py
- [X] T004 [P] Create unit test module placeholder for merge-automation workflow helpers in tests/unit/workflows/temporal/test_merge_gate_workflow.py
- [X] T005 [P] Create unit test module placeholder for parent run gate startup in tests/unit/workflows/temporal/test_run_merge_gate_start.py
- [X] T006 [P] Create Temporal boundary test module placeholder in tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py

---

## Phase 2: Foundational

**Purpose**: Define shared contract inventory and test fixtures that block story implementation without adding production behavior before red-first tests.

**CRITICAL**: No story implementation work begins until this phase is complete.

- [X] T007 Confirm merge-automation contract inventory and payload names from specs/179-merge-automation/contracts/merge-automation-contract.md
- [X] T008 Confirm merge-automation entity and state-transition inventory from specs/179-merge-automation/data-model.md
- [X] T009 Confirm workflow registration and activity routing anchors from specs/179-merge-automation/plan.md
- [X] T010 Add reusable fake readiness and resolver-launch fixtures for SC-001 through SC-005 in tests/unit/workflows/temporal/test_merge_gate_workflow.py
- [X] T011 Add parent-run publish outcome fixture for acceptance scenarios 1 and 2 in tests/unit/workflows/temporal/test_run_merge_gate_start.py

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Gate Pull Requests Before Automated Resolution

**Summary**: As a MoonMind operator using automated pull request publishing, I want a visible merge gate to wait for external readiness signals before launching pr-resolver so implementation runs can finish promptly while merge automation proceeds independently.

**Independent Test**: Run a task configured to publish a pull request with merge automation enabled, simulate blocked and ready external states, and verify that the implementation run completes after publishing, the merge gate records waiting blockers, and exactly one resolver follow-up starts only when the gate opens.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012; acceptance scenarios 1-7; SC-001, SC-002, SC-003, SC-004, SC-005; MM-341 original brief.

**Test Plan**:

- Unit: model validation, readiness classification, idempotency keys, parent-run gate-start decision, resolver payload construction, stale/closed/policy-denied blockers.
- Workflow-boundary: parent-to-gate workflow startup, parent completion after gate startup, waiting blockers, gate-open resolver launch, duplicate-event prevention, resolver readiness reuse after remediation commits.
- Hermetic integration: workflow registration, activity routing, and projection behavior through `./tools/test_integration.sh` when Docker Compose is available.

### Unit Tests (write first)

- [X] T012 [P] Add failing unit tests for merge-automation model validation covering FR-003, FR-005, FR-006, FR-012, stale revision, missing PR identity, and unsupported policy values in tests/unit/workflows/temporal/test_merge_gate_models.py
- [X] T013 [P] Add failing unit tests for readiness classification blockers covering acceptance scenarios 3 and 6, edge cases, SC-002, and SC-004 in tests/unit/workflows/temporal/test_merge_gate_workflow.py
- [X] T014 [P] Add failing unit tests for resolver launch idempotency and publish mode `none` payloads covering FR-007, FR-008, FR-009, scenario 4, scenario 5, and SC-003 in tests/unit/workflows/temporal/test_merge_gate_workflow.py
- [X] T015 [P] Add failing unit tests for parent `MoonMind.Run` gate-start decisions covering FR-001, FR-002, FR-004, scenario 1, scenario 2, and disabled merge automation in tests/unit/workflows/temporal/test_run_merge_gate_start.py
- [X] T016 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/test_run_merge_gate_start.py` to confirm T012-T015 fail for the expected missing implementation

### Workflow-Boundary Tests (write first)

- [X] T017 Add failing Temporal workflow-boundary test for parent run publishing a PR, starting one merge gate, and completing independently covering FR-001, FR-002, FR-004, scenario 1, scenario 2, and SC-001 in tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py
- [X] T018 Add failing Temporal workflow-boundary test for waiting blockers and sanitized projection state covering FR-005, FR-006, FR-011, scenario 3, SC-002, and contract query shape in tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py
- [X] T019 Add failing Temporal workflow-boundary test for gate-open resolver creation and duplicate event/replay prevention covering FR-007, FR-008, FR-009, scenario 4, scenario 5, and SC-003 in tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py
- [X] T020 Add failing Temporal workflow-boundary test for closed PR, stale revision, policy denial, and unavailable external state covering FR-012, scenario 6, edge cases, and SC-004 in tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py
- [X] T021 Add failing workflow or resolver-boundary test for post-remediation readiness reuse without a second top-level merge gate covering FR-010, scenario 7, and SC-005 in tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py
- [X] T022 Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py` to confirm T017-T021 fail for the expected missing implementation

### Implementation

- [X] T023 Implement merge-automation model contracts and validation for FR-003, FR-005, FR-006, FR-008, FR-012 in moonmind/schemas/temporal_models.py
- [X] T024 Implement merge-automation activity names and routing metadata for FR-005 and FR-007 in moonmind/workflows/temporal/activity_catalog.py
- [X] T025 Implement readiness classification helper and sanitized blocker normalization for FR-005, FR-006, FR-012, SC-002, and SC-004 in moonmind/workflows/temporal/workflows/merge_gate.py
- [X] T026 Implement `MoonMind.MergeAutomation` workflow wait/evaluate/open/block lifecycle for FR-005, FR-006, FR-007, FR-008, scenario 3, scenario 4, scenario 5, and scenario 6 in moonmind/workflows/temporal/workflows/merge_gate.py
- [X] T027 Implement external event signal handling for duplicate and out-of-order readiness events covering FR-008 and edge cases in moonmind/workflows/temporal/workflows/merge_gate.py
- [X] T028 Implement resolver follow-up creation payload builder using pr-resolver and publish mode `none` for FR-007, FR-009, scenario 4, and contract resolver request in moonmind/workflows/temporal/workflows/merge_gate.py
- [X] T029 Register merge-automation workflow type for FR-002 and operator visibility in moonmind/workflows/temporal/workers.py
- [X] T030 Register merge-automation workflow implementation for FR-002 in moonmind/workflows/temporal/worker_entrypoint.py
- [X] T031 Implement merge-automation activity runtime bindings for readiness evaluation and resolver creation covering FR-005, FR-007, FR-009, and contract activity names in moonmind/workflows/temporal/activity_runtime.py
- [X] T032 Implement parent `MoonMind.Run` merge-automation request detection and post-PR gate startup for FR-001, FR-002, FR-004, scenario 1, scenario 2, and SC-001 in moonmind/workflows/temporal/workflows/run.py
- [X] T033 Implement resolver-side readiness reuse hook or helper call after remediation commits for FR-010, scenario 7, and SC-005 in moonmind/workflows/temporal/workflows/run.py
- [X] T034 Expose compact gate status, blockers, and resolver refs through existing workflow memo/query/projection path for FR-011 and contract projection shape in moonmind/workflows/temporal/workflows/merge_gate.py
- [X] T035 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/test_run_merge_gate_start.py tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py` and fix failures in moonmind/workflows/temporal/workflows/merge_gate.py

**Checkpoint**: The story is fully functional, covered by unit and Temporal boundary tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T036 [P] Update operator-facing Temporal workflow catalog references for `MoonMind.MergeAutomation` in docs/Temporal/WorkflowTypeCatalogAndLifecycle.md
- [X] T037 [P] Update publish/merge automation quick reference in docs/Tasks/TaskPublishing.md
- [X] T038 [P] Add or refine edge-case unit coverage for sanitized blockers and secret-like provider details in tests/unit/workflows/temporal/test_merge_gate_workflow.py
- [X] T039 Run quickstart validation commands from specs/179-merge-automation/quickstart.md
- [X] T040 Run full unit verification with `./tools/test_unit.sh`
- [X] T041 Run hermetic integration verification with `./tools/test_integration.sh` when Docker Compose is available, or document the exact environment blocker in specs/179-merge-automation/verification.md
- [X] T042 Run `/speckit.verify` for specs/179-merge-automation after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on the story being functional and tests passing.

### Within The Story

- Unit tests T012-T015 must be written before implementation and confirmed failing by T016.
- Workflow-boundary tests T017-T021 must be written before implementation and confirmed failing by T022.
- Model contract implementation T023 precedes workflow lifecycle work T025.
- Activity catalog/runtime work T024 and T031 precedes full workflow-boundary green tests.
- Parent run startup work T032 depends on model contracts T023 and gate workflow startup shape T025.
- Resolver readiness reuse T033 depends on readiness helper work T024.
- Story validation T035 runs after all implementation tasks T023-T034.

### Parallel Opportunities

- T003-T006 can run in parallel after T001-T002 because they touch different test files.
- T012-T015 can run in parallel after foundational fixtures exist because they touch different test modules.
- T017-T021 should be authored in one file and are not marked parallel to avoid merge conflicts.
- T028 and T029 can be implemented near each other but are not marked parallel because workflow registration should be reviewed as one boundary.
- T036-T038 can run in parallel after story validation because they touch docs and focused test hardening separately.

---

## Parallel Example

```bash
Task: "Add failing unit tests for merge-automation model validation in tests/unit/workflows/temporal/test_merge_gate_models.py"
Task: "Add failing unit tests for parent MoonMind.Run gate-start decisions in tests/unit/workflows/temporal/test_run_merge_gate_start.py"
Task: "Update operator-facing Temporal workflow catalog references in docs/Temporal/WorkflowTypeCatalogAndLifecycle.md"
```

---

## Implementation Strategy

1. Complete setup and foundational schema/activity scaffolding.
2. Write all unit tests and confirm they fail for missing merge-automation models, workflow helpers, and parent startup logic.
3. Write all Temporal workflow-boundary tests and confirm they fail for missing workflow registration/lifecycle behavior.
4. Implement compact models, activity names, readiness helpers, workflow lifecycle, activity bindings, and parent-run startup.
5. Re-run focused unit and workflow-boundary tests until green.
6. Complete documentation polish and quickstart validation.
7. Run full unit verification and hermetic integration verification when available.
8. Run `/speckit.verify` against the original MM-341 request.

---

## Coverage Summary

- FR-001 through FR-004: covered by T015, T017, T032, T035.
- FR-005 through FR-008: covered by T012-T014, T018-T020, T023-T027, T031, T035.
- FR-009: covered by T014, T019, T028, T031, T035.
- FR-010: covered by T021, T033, T035.
- FR-011: covered by T018, T034, T036, T039.
- FR-012: covered by T012, T013, T020, T023, T024, T035.
- Acceptance scenarios 1-7: covered by T017-T021 and implementation tasks T026-T033.
- SC-001 through SC-005: covered by T017-T021, T035, T039-T042.
- Original MM-341 brief preservation: covered by T001 and final `/speckit.verify` task T042.
