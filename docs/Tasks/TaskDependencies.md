# Task Dependencies System

Status: Proposed
Owners: MoonMind Engineering
Last Updated: 2026-03-16

## 1. Purpose

This document outlines the design for a **Task Dependencies** system within MoonMind. This system allows a Task to specify prerequisite Tasks that must complete successfully before the dependent Task begins execution.

This enables complex, multi-stage workflows across disparate agents or execution environments by explicitly modeling temporal dependencies between separate `MoonMind.Run` executions.

## 2. Requirements

- A Task can define a list of one or more prerequisite Task IDs.
- The dependent Task will remain in a "Waiting for Dependencies" state until all prerequisites have reached a successful terminal state.
- If any prerequisite Task fails, is cancelled, or terminates unsuccessfully, the dependent Task should fail or be cancelled automatically.
- The Mission Control UI must visually indicate when a Task is blocked by dependencies, and allow users to configure these dependencies during task creation/editing.

## 3. Backend Component

The backend implementation involves updating the canonical payload, modifying the API to accept dependencies, and updating the Temporal Workflow logic to wait for preconditions.

### 3.1 Canonical Payload Updates

The canonical task payload defined in `moonmind.workflows.agent_queue.task_contract` and exposed via `/api/queue/jobs` will be extended with an optional `dependsOn` field under the `task` block:

```json
{
  "task": {
    "instructions": "Run integration tests",
    "dependsOn": [
      "task-run-id-1",
      "task-run-id-2"
    ],
    "proposeTasks": true,
    "steps": []
  }
}
```

### 3.2 Temporal Workflow Execution (`MoonMind.Run`)

The `MoonMind.Run` Temporal Workflow will enforce these dependencies before beginning actual work (i.e., before scheduling any Activities).

**Waiting Mechanism:**
The workflow will use Temporal's `workflow.wait_condition` to block execution until the dependency condition is met.

1. **Initialization:** Upon starting, the workflow checks if `task.dependsOn` is populated.
2. **Condition Evaluation:**
   - The workflow executes a local or standard Activity to query the status of the prerequisite `workflow_ids` via the Temporal client.
   - Using `workflow.wait_condition` combined with `asyncio.sleep` (to yield execution) or driven by Temporal Signals. Since `wait_condition` requires `asyncio.TimeoutError` handling, it will safely block execution until a specified timeout or condition is met.
   - If all tasks in `dependsOn` have a status of `Completed` (Success), the workflow proceeds.
   - If any task in `dependsOn` has a status of `Failed`, `Cancelled`, or `Terminated`, the workflow immediately fails with a `DependencyFailedError`.
3. **State Tracking:**
   While waiting, the workflow updates its internal state (Search Attributes / Memo) to indicate it is `WAITING_ON_DEPENDENCIES` to inform the API and UI.

## 4. Frontend Component

The Mission Control UI must be updated to support viewing and configuring task dependencies.

### 4.1 Task Creation & Editing

- **Task Proposal / Creation Form:** Add a "Dependencies" section.
- **UI Element:** A multi-select combobox or typeahead search field that queries the `/api/queue/jobs` endpoint to find existing Task Runs.
- Users can select existing Tasks to block the new Task.
- For Presets/Templates, allow defining dependencies on other templates, which will instantiate together as a linked graph of executions.

### 4.2 Task List & Detail Views

- **Task List (Table):**
  - Introduce a new visual state/badge for `WAITING_ON_DEPENDENCIES`.
  - Provide a tooltip or quick-view showing the IDs/Titles of the blocking tasks.
- **Task Detail Page:**
  - Add a "Dependencies" panel showing the list of prerequisite tasks.
  - Show the real-time status of each prerequisite (e.g., Running, Completed, Failed).
  - Include clickable links to navigate directly to the parent task's detail page.
  - Similarly, show a "Dependent Tasks" (downstream) list if a task blocks others.

## 5. Failure Modes & Edge Cases

- **Circular Dependencies:** The API `/api/queue/jobs` must validate upon creation that adding a dependency does not create a cycle (e.g., Task A depends on Task B, which depends on Task A).
- **Deleted/Purged Tasks:** If a dependency refers to a Task ID that cannot be found, the dependent task should fail immediately to avoid waiting forever.
- **Timeout:** The standard workflow timeout still applies. If dependencies take longer than the workflow's configured timeout, the dependent workflow will eventually time out.
