# Task Dependencies Implementation Plan

## Phase 1: Canonical Payload & Database Updates
*   **Task 1.1:** Update the canonical payload in `moonmind.workflows.tasks.task_contract` to add the `dependsOn` field to the `task` block.
*   **Task 1.2:** Confirm `WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"` is present in `MoonMindWorkflowState` in `api_service/db/models.py` (already implemented; update only if further changes are needed).
*   **Task 1.3:** Confirm there is an Alembic migration adding the `waiting_on_dependencies` enum value to the PostgreSQL `moonmindworkflowstate` type (already implemented; extend only if additional DB/projection work is required).
*   **Task 1.4:** Confirm `api_service/core/sync.py` recognizes and correctly handles the `WAITING_ON_DEPENDENCIES` state during projection sync (already implemented; adjust only if behavior needs refinement).

## Phase 2: API & Validation
*   **Task 2.1:** Validate creation requests on the API: enforce a limit of 10 entries and ensure no self-dependency.
*   **Task 2.2:** Verify existence: validate that each ID in `dependsOn` resolves to an existing `MoonMind.Run` workflow by querying the database projection.
*   **Task 2.3:** Implement cycle detection traversing the transitive dependency graph (limit 20 hops) returning a 409 Conflict if found.

## Phase 3: Temporal Workflow Execution (`MoonMind.Run`)
*   **Task 3.1:** Verify that `STATE_WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"` is defined in `run.py` alongside the existing state constants and reference it where appropriate in the workflow logic.
*   **Task 3.2:** Implement `asyncio.gather` on Temporal external workflow handles to wait for dependencies in the workflow before planning.
*   **Task 3.3:** Integrate `workflow.wait_condition` or shielded check evaluating `self._cancel_requested` and checking `self._paused`.
*   **Task 3.4:** Handle failures by catching `WorkflowFailureError` and failing with a `DependencyFailedError`.
*   **Task 3.5:** Update state tracking: set `mm_state` search attribute to `waiting_on_dependencies` and update the workflow memo with `dependsOn` IDs.
*   **Task 3.6:** Update the finish summary to record whether a wait occurred, the duration of the wait, and the resolution.

## Phase 4: Frontend Component (Mission Control UI)
*   **Task 4.1:** Update the Task Creation Form to add a "Dependencies" section with a multi-select combobox or typeahead search querying the executions list endpoint (e.g. `/api/executions`).
*   **Task 4.2:** Introduce a new visual state/badge for `WAITING_ON_DEPENDENCIES` and quick-view showing titles of blocking tasks in the Task List.
*   **Task 4.3:** Add a "Dependencies" panel in the Task Detail Page to show real-time status and downstream dependent tasks via reverse lookup.