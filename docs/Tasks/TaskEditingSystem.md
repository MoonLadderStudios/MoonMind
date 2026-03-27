# Task Editing System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-13  

## 1. Purpose

Add the ability to **edit a Task Payload** before the execution begins, or to edit it when cloning/resubmitting a run.

This generally applies to modifying unstarted `Task Proposals` waiting for human review, modifying queued/running Temporal Workflows, or resubmitting failed/cancelled Temporal Workflows.

* It reuses the existing **Create/Submit** UI (`/tasks/new`), switching the primary CTA to **Update** or **Resubmit**.
* It utilizes the API routes to update an existing proposal or clone a terminal workflow.

## 2. UX Design

### 2.1 Entry points

**Task detail**

* If the UI is viewing a `Task Proposal` that has not yet been promoted to a Temporal Workflow, show an **Edit** button.
* If the UI is viewing a queued or running `MoonMind.Run` Temporal Workflow, show an **Edit** button.
* If viewing a terminal `MoonMind.Run` Temporal Workflow (Failed or Cancelled), show a **Resubmit** button.

### 2.2 Edit/Resubmit Flow

1. User clicks **Edit** or **Resubmit**.
2. Browser navigates to `/tasks/queue/new?editJobId=<uuid>` (reusing the standard Create page alias).
3. The Create page detects `editJobId` and fetches the job state/parameters from the API.
4. The form is pre-filled with the original values (`priority`, `affinityKey`, `payload.instructions`, `payload.skill`).
5. On submit:
   * For an unstarted **Proposal** or queue job, the UI updates the proposal in place via `PUT /api/queue/jobs/{id}`.
   * For a queued or running **Workflow Execution**, the UI submits to `POST /api/executions/{workflowId}/update` with `updateName="UpdateInputs"`. The backend implements this using Temporal Workflow Updates (`UpdateInputs`) to modify inputs in place without creating a new execution.
   * For a terminal **Workflow Execution**, the UI submits to `POST /api/queue/jobs/{jobId}/resubmit`, which creates a fresh Temporal Workflow Execution while leaving the original terminal history untouched.

## 3. Edit Mode (Queued/Running Workflows)

Editing inputs for a running `MoonMind.Run` is supported natively via Temporal Workflow Updates (`UpdateInputs`). This approach modifies inputs in place, avoiding the creation of a new execution and preventing races where an old execution might continue running after a cancel request.

When a user edits a queued or active workflow:

* Submitting triggers `POST /api/executions/{workflowId}/update` with `updateName="UpdateInputs"`.
* The Temporal Server applies the update synchronously, modifying the internal workflow state.
* The API returns a successful response indicating the workflow inputs have been updated.
* The UI updates the current run detail page.
* The workflow continues executing seamlessly with the updated parameters.

## 4. Resubmit Mode (Terminal Workflows)

When editing a terminal workflow:

* Submitting triggers `POST /api/queue/jobs/{jobId}/resubmit`.
* The API returns `201 Created` with a new execution reference.
* The UI redirects to the new run detail page.
* The backend appends a history/audit event: `Job resubmitted from sourceJobId` so humans can track the lineage.
