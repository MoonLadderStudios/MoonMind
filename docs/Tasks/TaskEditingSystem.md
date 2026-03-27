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
   * For an unstarted **Proposal**, the UI updates the proposal in place via a `PUT`.
   * For a queued or running **Workflow Execution**, the UI submits to `POST /api/queue/jobs/{jobId}/edit`. The backend implements this using idiomatic Temporal patterns: it **cancels** the existing Temporal Workflow execution and **starts** a new execution with the updated payload. Temporal workflow start inputs are immutable, so in-place mutation of a running workflow's parameters is not supported.
   * For a terminal **Workflow Execution**, the UI submits to `POST /api/queue/jobs/{jobId}/resubmit`, which creates a fresh Temporal Workflow Execution while leaving the original terminal history untouched.

## 3. Edit Mode (Queued/Running Workflows)

Because Temporal workflow start arguments are **immutable**, a workflow cannot have its initial parameters updated in place once the run has been registered with the Temporal Server.

When a user edits a queued or active workflow:

* Submitting triggers `POST /api/queue/jobs/{jobId}/edit`.
* The API acts as a coordinator for the Temporal primitives:
  1. It issues a **Cancel** request to the existing Temporal Workflow Execution.
  2. It immediately issues a **Start** request for a new `MoonMind.Run` execution with the updated parameters.
* The API returns `201 Created` with the new execution reference.
* The UI redirects to the new run detail page.
* The backend appends a history/audit event to the new run: `Job edited and replaced sourceJobId: <old-uuid>` so humans can track the lineage.

## 4. Resubmit Mode (Terminal Workflows)

When editing a terminal workflow:

* Submitting triggers `POST /api/queue/jobs/{jobId}/resubmit`.
* The API returns `201 Created` with a new execution reference.
* The UI redirects to the new run detail page.
* The backend appends a history/audit event: `Job resubmitted from sourceJobId` so humans can track the lineage.
