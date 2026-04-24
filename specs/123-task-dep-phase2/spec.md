# Feature Specification: Task Dependencies Phase 2 - MoonMind.Run Dependency Gate

**Feature Branch**: `123-task-dep-phase2`  
**Created**: 2026-04-01  
**Status**: Draft  
**Input**: User description: "Implement Phase 2 of docs/Tasks/TaskDependencies.md using test-driven development"

## Source Document Requirements

Extracted from `docs/Tasks/TaskDependencies.md` Phase 2 and `docs/Tasks/TaskDependencies.md` section 5.

| Requirement ID | Source Citation | Requirement Summary |
|----------------|----------------|---------------------|
| DOC-REQ-001 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 1 | `MoonMindRunWorkflow.run()` MUST parse `initialParameters.task.dependsOn` after `_initialize_from_payload()` and before entering `planning`. |
| DOC-REQ-002 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 2 | When dependencies are present, the workflow MUST set `mm_state` to `waiting_on_dependencies` and expose dependency IDs in memo/search metadata. |
| DOC-REQ-003 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 3 and `docs/Tasks/TaskDependencies.md` §5.2 | The workflow MUST wait on each prerequisite using Temporal external workflow handles and block until all prerequisites complete successfully. |
| DOC-REQ-004 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 4 | Dependency waiting MUST be wrapped in a Temporal `CancellationScope` so cancellation interrupts the wait cleanly. |
| DOC-REQ-005 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 5 | The dependency gate MUST be guarded by `workflow.patched("dependency-gate-v1")` for replay safety. |
| DOC-REQ-006 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 6 and `docs/Tasks/TaskDependencies.md` §5.1 | If `dependsOn` is absent or empty, the workflow MUST proceed directly from `initializing` to `planning`. |
| DOC-REQ-007 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 7 and `docs/Tasks/TaskDependencies.md` §5.4 | If a prerequisite fails, is canceled, or is terminated, the dependent run MUST fail with a dependency-specific message. |
| DOC-REQ-008 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 8 and `docs/Tasks/TaskDependencies.md` §5.3 | Canceling the dependent run during dependency wait MUST cancel only the dependent run, not prerequisite runs. |
| DOC-REQ-009 | `docs/Tasks/TaskDependencies.md` Phase 2 bullet 9 | After dependency wait resolves, the workflow MUST re-check `self._paused` before entering planning, consistent with the existing pause gate. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Dependent run waits before planning (Priority: P1)

An operator submits a `MoonMind.Run` execution with `initialParameters.task.dependsOn`, and the workflow blocks in `waiting_on_dependencies` until every prerequisite completes successfully before entering planning.

**Why this priority**: This is the core runtime behavior for the feature. Without it, Phase 1 validation has no effect at execution time.

**Independent Test**: Start the workflow with dependencies in a workflow test environment, assert it enters `waiting_on_dependencies`, resolves prerequisite handles, and only then runs the planning stage.

**Acceptance Scenarios**:

1. **Given** a run with a non-empty `initialParameters.task.dependsOn`, **When** the workflow starts, **Then** it transitions from `initializing` to `waiting_on_dependencies` before `planning`.
2. **Given** all prerequisite workflows complete successfully, **When** the dependency gate resolves, **Then** the run continues into planning and execution without failing.
3. **Given** `initialParameters.task.dependsOn` is absent or empty, **When** the workflow starts, **Then** it skips the dependency gate and proceeds directly to planning.

---

### User Story 2 - Dependency failures fail the dependent run clearly (Priority: P1)

An operator can see a dependent run fail with a dependency-specific reason if any prerequisite fails, is canceled, is terminated, or cannot be awaited successfully at runtime.

**Why this priority**: Failure propagation is a correctness and operability requirement. Silent hangs or generic failures would make dependencies unsafe to use.

**Independent Test**: Simulate failed or canceled prerequisite handles and assert the dependent workflow finalizes as failed with a dependency-specific error message naming the dependency when possible.

**Acceptance Scenarios**:

1. **Given** a prerequisite workflow fails, **When** the dependency gate awaits it, **Then** the dependent run finalizes as failed with a dependency-specific error.
2. **Given** a prerequisite workflow is canceled or terminated, **When** the dependency gate awaits it, **Then** the dependent run finalizes as failed with a dependency-specific error.
3. **Given** dependency waiting fails after create-time validation because the target cannot be resolved at runtime, **When** the workflow handles the error, **Then** it fails instead of waiting indefinitely.

---

### User Story 3 - Dependency waiting remains replay-safe and cancel-safe (Priority: P2)

An operator can cancel the dependent run while it is waiting on prerequisites, and in-flight or replayed workflows remain safe because the dependency gate is patch-guarded and isolated inside a cancellation scope.

**Why this priority**: Temporal workflow changes must preserve replay safety and cancel behavior for in-flight executions.

**Independent Test**: Verify patched and unpatched paths behave correctly in workflow-boundary tests, and that canceling the dependent run interrupts the wait without signaling or canceling prerequisite workflows.

**Acceptance Scenarios**:

1. **Given** a run waiting on dependencies, **When** the dependent workflow is canceled, **Then** the dependency wait is interrupted and only the dependent run transitions to canceled.
2. **Given** an in-flight workflow history without the patch marker, **When** the workflow replays, **Then** it follows the legacy no-gate path and remains replay-safe.
3. **Given** a dependency wait resolves while the workflow is paused, **When** the gate exits, **Then** the workflow honors the existing pause gate before entering planning.

---

### Edge Cases

- What happens when `dependsOn` is present but normalizes to an empty list? The workflow treats it as absent and skips the dependency gate.
- What happens when all prerequisites already completed successfully before the dependent workflow starts? The external workflow `result()` calls resolve immediately and the workflow moves straight to the pause/planning gate.
- What happens when memo or search-attribute upserts for dependency metadata fail in tests because attributes are not registered? The workflow logs the warning and continues, consistent with existing visibility update behavior.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `MoonMindRunWorkflow.run()` MUST read `initialParameters.task.dependsOn` immediately after payload initialization and before entering planning. (DOC-REQ-001)
- **FR-002**: When the normalized dependency list is non-empty, the workflow MUST transition to `waiting_on_dependencies` before awaiting prerequisites. (DOC-REQ-002)
- **FR-003**: When dependencies are present, the workflow MUST expose dependency identifiers through compact workflow metadata used by memo and visibility surfaces. (DOC-REQ-002)
- **FR-004**: The workflow MUST await every dependency through Temporal external workflow handles and continue only after all dependencies complete successfully. (DOC-REQ-003)
- **FR-005**: Dependency waiting MUST execute inside a Temporal `CancellationScope` so canceling the dependent workflow interrupts the wait without mutating prerequisite workflows. (DOC-REQ-004, DOC-REQ-008)
- **FR-006**: The dependency-gate behavior MUST be guarded by `workflow.patched("dependency-gate-v1")`, while the unpatched path preserves pre-Phase-2 behavior for replay compatibility. (DOC-REQ-005)
- **FR-007**: When `dependsOn` is absent or empty, the workflow MUST skip the dependency gate and proceed directly from `initializing` to `planning`. (DOC-REQ-006)
- **FR-008**: If any prerequisite fails, is canceled, is terminated, or cannot be awaited successfully at runtime, the dependent workflow MUST fail with a dependency-specific error message. (DOC-REQ-007)
- **FR-009**: After dependency waiting resolves successfully, the workflow MUST honor the existing pause gate before entering planning. (DOC-REQ-009)
- **FR-010**: Workflow-boundary tests MUST cover the patched dependency gate path, the legacy unpatched compatibility path, and at least one degraded dependency outcome. (DOC-REQ-005, DOC-REQ-007)
- **FR-011**: Existing run workflow tests unrelated to dependency gating MUST continue to pass without behavioral regressions.

### Key Entities

- **Dependency Gate**: The pre-planning workflow stage that inspects `initialParameters.task.dependsOn`, exposes dependency metadata, and waits on prerequisite workflow completion.
- **Prerequisite Execution Handle**: A Temporal external workflow handle returned by `workflow.get_external_workflow_handle(dep_id)` and awaited through `result()`.
- **Dependency Failure Outcome**: The dependency-specific workflow error surfaced when a prerequisite does not complete successfully.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A dependency-aware `MoonMind.Run` enters `waiting_on_dependencies` before planning and only reaches planning after all prerequisite handles succeed.
- **SC-002**: A run with no dependencies follows the existing `initializing -> planning` path with no dependency-wait side effects.
- **SC-003**: A failed or canceled prerequisite causes the dependent workflow to fail with a dependency-specific message instead of hanging or returning a generic error.
- **SC-004**: Canceling a dependent workflow while it is waiting interrupts the wait and does not cancel prerequisite workflows.
- **SC-005**: Workflow-boundary tests cover patched, unpatched, and degraded dependency outcomes and pass via `./tools/test_unit.sh`.
