# Task Details Page

## Purpose

The Task Details page is the canonical view for inspecting a single MoonMind task execution. It presents the task identity, current state, original task configuration, execution history, outputs, errors, and all actions that are available for the task in its current state.

The page is declarative and state-driven. Every visible control is derived from the task execution status, the task type, and explicit backend capabilities. A failed task presents the user with the complete set of available recovery actions, which may include **Remediate**, **Edit task**, **Rerun**, and **Resume**.

## Route

The page is available from task lists, task notifications, remediation links, and run history links.

```text
/tasks/:taskExecutionId
```

The page accepts a task execution identifier and loads a single task execution detail record.

## Page-level goals

The page enables a user to:

1. Understand what task ran or is running.
2. See the current task status and relevant timestamps.
3. Inspect the exact steps, choices, instructions, and configuration used for the task.
4. View progress, logs, outputs, artifacts, and failure information.
5. Take the correct next action for the task state.
6. For failed tasks, choose between remediation, editing the failed task for a new run, rerunning the task exactly as originally submitted, or resuming from the last failed step when prior work can be restored.

## Source of truth

The page is rendered from a task execution detail model that includes the task execution, original submission draft, current status, derived capabilities, artifacts, logs, and related runs.

The UI does not infer unavailable actions from status alone. The UI displays actions from explicit capability fields.

Required page data:

```ts
type TaskDetailsPageData = {
  execution: TaskExecutionDetail;
  submissionDraft: TaskSubmissionDraft | null;
  capabilities: TaskExecutionCapabilities;
  progress: TaskProgress | null;
  outputs: TaskOutput[];
  artifacts: TaskArtifact[];
  failure: TaskFailure | null;
  remediation: TaskRemediationSummary | null;
  relatedRuns: RelatedTaskRun[];
  auditEvents: TaskAuditEvent[];
};
```

Required capability fields:

```ts
type TaskExecutionCapabilities = {
  canRemediate: boolean;
  canRerun: boolean;
  canEditForRerun: boolean;
  canResumeFromFailedStep: boolean;
  canUpdateInputs: boolean;
  canCancel: boolean;
  canPause: boolean;
  canResume: boolean;
  canViewLogs: boolean;
  canViewArtifacts: boolean;
  canCopyTaskLink: boolean;
};
```

## Page layout

The page contains the following major regions:

1. Header
2. Primary action bar
3. Status summary
4. Task configuration summary
5. Execution progress
6. Failure and remediation panel, when applicable
7. Outputs and artifacts
8. Logs and events
9. Related runs
10. Metadata and audit details

Desktop layout uses a main content column and an optional right rail. Mobile layout stacks all sections vertically while preserving the header and primary actions near the top of the page.

## Header

The header is always present.

The header contains:

- A breadcrumb or back link to the task list.
- The task name or task title.
- A status badge.
- A short task description or prompt summary, when available.
- The primary action bar.
- A task overflow menu for secondary utility actions.

Example desired layout:

```text
Tasks / Customer Renewal Research

Customer Renewal Research                     [Failed]
Research customer renewal risks and summarize next steps.

[Remediate] [Edit task] [Rerun] [Resume] [More]
```

### Title behavior

The title displays the user-provided task name when present. If no explicit task name exists, the title displays a generated summary of the task objective. If neither is available, the title displays:

```text
Untitled task
```

### Status badge

The status badge is always visible and uses one of the canonical task statuses:

- Pending
- Scheduled
- Running
- Paused
- Completed
- Failed
- Canceled
- Timed out
- Terminated

The status badge is not the only indicator of status. The status summary section provides detailed status context.

## Primary action bar

The primary action bar appears in the header and remains available near the top of the page. On smaller screens, the same actions may collapse into a sticky bottom action bar or an overflow menu, but the actions remain discoverable.

Actions are additive. Showing one action never hides another valid action. In particular, **Remediate** does not replace **Edit task**, **Rerun**, or **Resume** on failed tasks.

### Failed task actions

For a failed MoonMind task, the primary action bar contains all of the following actions when the corresponding capabilities are true:

```text
[Remediate] [Edit task] [Rerun] [Resume]
```

The desired failed-task action state is:

```ts
capabilities.canRemediate === true
capabilities.canEditForRerun === true
capabilities.canRerun === true
capabilities.canResumeFromFailedStep === true // only when checkpointed progress is restorable
capabilities.canUpdateInputs === false
```

Failed tasks are terminal executions. The user may inspect, remediate, edit for a new run, rerun, or resume from the last failed step, but the original failed execution is not mutated in place.

### Remediate button

The **Remediate** button is present when:

```ts
capabilities.canRemediate === true
```

The button label is:

```text
Remediate
```

The button opens the remediation experience for the failed task.

Desired destination:

```text
/tasks/:taskExecutionId/remediate
```

The button is usually the primary action for failed tasks when remediation is available.

The button purpose is:

```text
Investigate and resolve the failure.
```

### Edit task button

The **Edit task** button is present when either of the following is true:

```ts
capabilities.canEditForRerun === true
capabilities.canUpdateInputs === true
```

The button label is:

```text
Edit task
```

For failed tasks, **Edit task** opens the create-task page in edit-for-rerun mode. The page loads the original task setup exactly, including all steps, selected choices, instructions, inputs, attachments, tool selections, model settings, scheduling choices, and advanced configuration.

Failed-task edit destination:

```text
/tasks/new?rerunExecutionId=:taskExecutionId&mode=edit
```

Failed-task edit behavior:

- The create-task page title is `Edit task`.
- The page displays a banner explaining that editing creates a new run.
- The original failed execution remains unchanged.
- The user can modify the loaded task configuration.
- Submitting the form creates a new task run using the edited configuration.
- The submit button label is `Run edited task`.

Required banner copy:

```text
You are editing a failed task. Your changes will create a new run. The original failed run will remain unchanged.
```

For non-terminal tasks that support live input updates, **Edit task** opens the create-task page in update-inputs mode.

Running-task edit destination:

```text
/tasks/new?editExecutionId=:taskExecutionId
```

Running-task edit behavior:

- The create-task page title is `Edit task`.
- The original task execution may be updated according to product rules.
- The submit button label is `Save changes`.

### Rerun button

The **Rerun** button is present when:

```ts
capabilities.canRerun === true
```

The button label is:

```text
Rerun
```

For failed tasks, **Rerun** starts a new run using the exact same original task configuration. It does not open an editing form and does not mutate the original failed execution.

Rerun behavior:

- Uses the original submission draft or reconstructed original workflow input.
- Preserves all original steps and choices.
- Preserves original instructions and artifact references when unchanged.
- Creates a new task execution.
- Links the new run back to the original task execution as a rerun.
- Leaves the original failed task unchanged.

Optional confirmation copy:

```text
Rerun task?

This will run the task again with the exact same steps, choices, and settings.

[Cancel] [Rerun task]
```

After a successful rerun request, the user is taken to the new task execution details page or shown a success toast with a link to the new run.

### Resume button

The failed-task **Resume** button is present when:

```ts
capabilities.canResumeFromFailedStep === true
```

The button label is:

```text
Resume
```

The accessible name is:

```text
Resume from failed step
```

The failed-task **Resume** action is separate from the paused-task **Resume** lifecycle action. Failed-task **Resume** retries the last failed step with the completed work before that step restored from durable checkpoints.

Resume behavior:

- Does not open an editing form.
- Uses the original task input snapshot unchanged.
- Pins the source execution by both `workflowId` and `runId`.
- Identifies the last failed step from backend progress data.
- Restores completed prior steps from durable step output refs and workspace, branch, commit, or equivalent checkpoints.
- Creates a linked follow-up execution.
- Displays prior completed steps in the new execution as reused from the original run.
- Starts new execution work at the failed step.
- Leaves the original failed task unchanged.

Optional confirmation copy:

```text
Resume from failed step?

MoonMind will reuse the completed work before the failed step and retry the failed step. The original failed run will remain unchanged.

[Cancel] [Resume]
```

After a successful Resume request, the user is taken to the resumed task execution details page or shown a success toast with a link to the resumed run.

### Cancel button

The **Cancel** button is present when:

```ts
capabilities.canCancel === true
```

The button cancels a pending, scheduled, or running task according to task execution rules.

The original task page remains available after cancellation.

### Pause and lifecycle Resume buttons

The **Pause** button is present when:

```ts
capabilities.canPause === true
```

The lifecycle **Resume** button is present when:

```ts
capabilities.canResume === true
```

Only one of **Pause** or lifecycle **Resume** is visible at a time. This lifecycle action resumes a paused active task and is not the failed-task **Resume** action described above.

### More menu

The overflow menu contains secondary utility actions that are valid for the task.

Possible menu items:

- Copy task link
- Copy task ID
- Copy workflow ID
- View raw input
- View raw output
- Download logs
- Download artifacts
- Open related run
- Archive task, if supported
- Delete task, if supported

Destructive actions are visually separated and require confirmation.

## Action visibility by status

The page follows this desired action matrix.

| Task status | Remediate | Edit task | Rerun | Resume failed step | Cancel | Pause | Lifecycle resume |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pending | No | Yes, if inputs can be updated | No | No | Yes | No | No |
| Scheduled | No | Yes, if inputs can be updated | No | No | Yes | No | No |
| Running | No | Yes, if inputs can be updated | No | No | Yes | Yes, if supported | No |
| Paused | No | Yes, if inputs can be updated | No | No | Yes | No | Yes |
| Completed | No | Optional, as edit-for-rerun | Yes | No | No | No | No |
| Failed | Yes | Yes, as edit-for-rerun | Yes | Yes, if checkpointed progress is restorable | No | No | No |
| Canceled | No | Optional, as edit-for-rerun | Yes | No | No | No | No |
| Timed out | Optional, if failure can be remediated | Yes, as edit-for-rerun | Yes | Optional, if a failed step and checkpoint are available | No | No | No |
| Terminated | Optional, if failure can be remediated | Yes, as edit-for-rerun | Yes | Optional, if a failed step and checkpoint are available | No | No | No |

The matrix is descriptive. The final source of truth is the capability object returned with the task detail data.

## Status summary section

The status summary section is always present.

It contains:

- Current status
- Status reason, when available
- Created time
- Started time, when available
- Completed time, when available
- Duration
- Last updated time
- Owner or creator
- Trigger type
- Retry count
- Current attempt number
- Environment or workspace, when applicable

Example:

```text
Status
Failed

Reason
Step 4 failed while generating final summary.

Created
Apr 24, 2026, 9:12 AM

Started
Apr 24, 2026, 9:13 AM

Failed
Apr 24, 2026, 9:19 AM

Duration
6m 12s

Attempts
1 of 3
```

When the task is failed, the failure reason is visible without requiring the user to open logs.

## Task configuration summary

The task configuration summary is always present when the original task configuration is available.

It displays the exact user-visible configuration used to create the task.

Required content:

- Task objective
- Instructions
- Steps
- Selected choices
- Inputs
- Attached files or artifacts
- Tools or integrations selected
- Model or agent settings, when user-visible
- Schedule or trigger configuration, when applicable
- Output format or destination
- Advanced options, when applicable

This section supports two levels of detail:

1. A readable summary for scanning.
2. An expanded exact configuration view for verification.

### Steps and choices

The page displays every step included in the task, in the original order.

For each step, the page displays:

- Step name
- Step description
- Selected option or choice
- Required inputs
- Optional inputs that were provided
- Step status, when execution data is available
- Step output, when available

Example:

```text
Step 1: Gather account context
Choice: Use CRM and recent emails
Status: Completed

Step 2: Analyze renewal risk
Choice: Conservative risk scoring
Status: Completed

Step 3: Draft customer-facing summary
Choice: Concise executive summary
Status: Failed
```

### Exactness requirement for failed-task edit

The configuration shown on the Task Details page and the configuration loaded by **Edit task** for a failed task come from the same source of truth.

When a user selects **Edit task** on a failed task, the edit form contains exactly the same values shown in the task configuration summary unless the backend reports that an option is no longer available.

If an option is no longer available, the edit form displays the prior value, marks it as unavailable, and prompts the user to choose a replacement before submitting.

## Execution progress section

The execution progress section is present for tasks with progress data.

It contains:

- Overall progress state
- Current step
- Completed steps
- Failed step, when applicable
- Pending steps
- Step-level timestamps
- Step-level outputs or summaries, when available
- Preserved steps reused from a source run, when viewing a resumed execution

For running tasks, this section updates as new progress is received.

For terminal tasks, this section displays the final execution path.

For resumed executions, prior completed steps restored from the source run are displayed as preserved, for example:

```text
Step 1: Gather account context
Status: Completed - reused from original run

Step 2: Analyze renewal risk
Status: Completed - reused from original run

Step 3: Draft customer-facing summary
Status: Running - resumed here
```

## Failure section

The failure section is present when:

```ts
execution.status === "FAILED" || failure !== null
```

It contains:

- Failure summary
- Failed step, when known
- Error message
- User-readable explanation
- Technical details, expandable
- Relevant logs or trace snippets
- Suggested next actions
- Remediation status, when available
- Resume availability or the disabled reason, when a failed step exists but checkpointed progress cannot be restored

The failure section must not obscure the primary action bar. Users can always see or quickly access **Remediate**, **Edit task**, and **Rerun** on failed tasks.

Example:

```text
Failure
The task failed while generating the final customer summary.

Failed step
Draft customer-facing summary

Error
The selected source artifact was unavailable.

Suggested actions
- Remediate the failed task to diagnose and resolve the issue.
- Edit the task to change inputs or choices and run it again.
- Rerun the task exactly as originally configured.
- Resume from the failed step to reuse completed prior work, if available.
```

## Remediation panel

The remediation panel is present when remediation data exists or remediation is available.

It contains:

- Remediation availability
- Current remediation status
- Last remediation attempt, when any
- Remediation recommendation summary
- Link or button to open remediation

For failed tasks with remediation available, the panel includes a **Remediate** button in addition to the header action.

Panel button label:

```text
Remediate
```

Panel button destination:

```text
/tasks/:taskExecutionId/remediate
```

The remediation panel never replaces the header action buttons.

## Outputs section

The outputs section is present when outputs exist.

It contains:

- Final output, when available
- Partial output, when available
- Step outputs
- Generated summaries
- Destination delivery status, when applicable
- Links to output artifacts

For failed tasks, partial outputs remain visible if they were produced before failure.

If no outputs exist, the section displays an empty state:

```text
No outputs were produced for this task.
```

## Artifacts section

The artifacts section is present when artifacts exist or when the user has permission to view artifacts.

It contains:

- Uploaded input files
- Generated output files
- Intermediate artifacts, when user-visible
- Instruction artifacts
- Log bundles, when available
- Artifact name
- Artifact type
- Created time
- Size, when available
- Download or open action

Artifact actions:

- Open
- Download
- Copy artifact ID, when applicable

For edit-for-rerun flows, unchanged artifact-backed fields reuse their original artifact references. Edited artifact-backed fields create new artifacts with lineage back to the original artifact.

## Logs section

The logs section is present when:

```ts
capabilities.canViewLogs === true
```

It contains:

- Searchable log stream or log table
- Timestamp
- Severity
- Step or component
- Message
- Expandable details
- Copy log line action
- Download logs action, when supported

For failed tasks, the logs section highlights log lines related to the failure.

Default view shows user-relevant logs first. Raw technical logs are available through an expandable advanced view.

## Events and audit section

The events and audit section contains a chronological timeline of meaningful task events.

Events may include:

- Task created
- Task scheduled
- Task started
- Step started
- Step completed
- Step failed
- Task paused
- Task resumed
- Task canceled
- Task failed
- Task completed
- Remediation started
- Remediation completed
- Task rerun requested
- Edited rerun created
- Failed-step resume requested
- Resumed run created

Each event includes:

- Timestamp
- Actor or system source
- Event name
- Event details

## Related runs section

The related runs section is present when related runs exist.

It contains:

- Original run, when viewing a rerun
- Reruns created from the current task
- Edited reruns created from the current task
- Resumed runs created from the current task
- Remediation-generated runs, when applicable

Each related run displays:

- Run title
- Relationship type
- Status
- Created time
- Duration
- Link to run details

Relationship labels:

- Original run
- Rerun
- Edited rerun
- Resumed from failed step
- Remediation run

For a failed task, once the user clicks **Rerun**, clicks **Resume**, or submits an edited task from **Edit task**, the new run appears in this section.

## Metadata section

The metadata section is present for users with permission to view technical details.

It contains:

- Task execution ID
- Workflow ID
- Run ID
- Workflow type
- Queue name, when applicable
- Attempt number
- Parent task, when applicable
- Source task, when this is a rerun
- Created by
- Workspace
- Environment
- Version
- Feature flags that affected execution, when user-visible

Each identifier supports copy-to-clipboard.

## Empty, loading, and error states

### Loading state

While loading, the page displays a skeleton for:

- Header
- Action bar
- Status summary
- Main content sections

The page does not display incomplete action buttons before capabilities are loaded.

### Not found state

When the task execution does not exist or the user does not have access, the page displays:

```text
Task not found

This task may have been deleted, moved, or you may not have permission to view it.
```

Primary action:

```text
Back to tasks
```

### Data unavailable state

When the task exists but some details cannot be loaded, the page displays the available task summary and an inline warning for the missing section.

Example:

```text
Task configuration unavailable

The original task configuration could not be loaded. You can still view status and logs.
```

If the task configuration is unavailable, the **Edit task** and **Rerun** actions are hidden unless the backend confirms that a valid submission draft or reconstructable workflow input is available.

## Accessibility requirements

The page is fully keyboard accessible.

Required accessibility behavior:

- All action buttons are reachable by keyboard.
- All buttons have clear accessible names.
- Status is exposed to assistive technology.
- Failure messages are announced when they load.
- Toasts and confirmation dialogs use accessible live regions.
- Confirmation dialogs trap focus while open.
- Focus returns to the triggering action after a dialog is closed.
- Copy actions provide accessible success feedback.

Button accessible names:

```text
Remediate task
Edit task
Rerun task
Cancel task
Pause task
Resume task
Resume from failed step
Copy task link
```

## Responsive behavior

On desktop:

- Header and primary actions appear at the top.
- Main sections appear in the primary column.
- Metadata, related runs, or compact status details may appear in a right rail.

On tablet:

- Header remains at the top.
- Primary actions may wrap to a second row.
- Right-rail content stacks below the main content.

On mobile:

- Header stacks vertically.
- Primary actions remain visible near the top or in a sticky bottom action bar.
- Secondary actions move into the overflow menu.
- The failed-task actions remain available and are not hidden behind remediation alone.

## Copy requirements

### Failed task header actions

```text
Remediate
Edit task
Rerun
Resume
```

### Failed task edit banner

```text
You are editing a failed task. Your changes will create a new run. The original failed run will remain unchanged.
```

### Rerun confirmation

```text
Rerun task?

This will run the task again with the exact same steps, choices, and settings.
```

Confirmation actions:

```text
Cancel
Rerun task
```

### Rerun success toast

```text
Task rerun started.
```

Toast action:

```text
View new run
```

### Resume confirmation

```text
Resume from failed step?

MoonMind will reuse the completed work before the failed step and retry the failed step. The original failed run will remain unchanged.
```

Confirmation actions:

```text
Cancel
Resume
```

### Resume success toast

```text
Task resumed from failed step.
```

Toast action:

```text
View resumed run
```

### Edited rerun success toast

```text
Edited task run started.
```

Toast action:

```text
View new run
```

### Remediation unavailable message

```text
Remediation is not available for this task.
```

### Edit unavailable message

```text
This task cannot be edited because its original configuration is unavailable.
```

### Rerun unavailable message

```text
This task cannot be rerun because its original configuration is unavailable.
```

### Resume unavailable message

```text
This task cannot be resumed because the completed work before the failed step is not recoverable.
```

## State-specific desired page examples

### Failed task

A failed task page contains:

- Header with task title and `Failed` status badge.
- Primary action bar with **Remediate**, **Edit task**, **Rerun**, and **Resume** when backend capabilities allow them.
- Status summary with failure timestamp and duration.
- Failure section with failed step, error summary, and technical details.
- Task configuration summary with original steps and choices.
- Execution progress showing completed steps and the failed step.
- Partial outputs, if any.
- Artifacts, if any.
- Logs with failure-relevant entries highlighted.
- Related runs, if reruns or remediation runs exist.
- Metadata and audit events.

Desired failed-task action model:

```ts
{
  canRemediate: true,
  canRerun: true,
  canEditForRerun: true,
  canResumeFromFailedStep: true,
  canUpdateInputs: false,
  canCancel: false,
  canPause: false,
  canResume: false,
  canViewLogs: true,
  canViewArtifacts: true,
  canCopyTaskLink: true
}
```

### Running task

A running task page contains:

- Header with task title and `Running` status badge.
- Primary actions for any valid running-state actions, such as **Edit task**, **Pause**, or **Cancel**.
- Live execution progress.
- Current step.
- Partial outputs, if available.
- Logs and events.

Running tasks do not show **Remediate** or **Rerun**.

### Completed task

A completed task page contains:

- Header with task title and `Completed` status badge.
- **Rerun** action when supported.
- Optional **Edit task** action when edit-for-rerun is supported.
- Final outputs.
- Artifacts.
- Execution timeline.
- Related runs.

### Canceled, timed out, or terminated task

A canceled, timed out, or terminated task page contains:

- Header with the corresponding terminal status badge.
- **Rerun** action when supported.
- **Edit task** action when edit-for-rerun is supported.
- Remediation action when the backend explicitly marks remediation as available.
- Status reason.
- Timeline and logs.

## Interaction contracts

### Clicking Remediate

When the user clicks **Remediate**:

1. The user is routed to the remediation experience for the current task.
2. The remediation experience receives the current task execution ID.
3. The original task details page remains reachable by back navigation.

### Clicking Edit task on a failed task

When the user clicks **Edit task** on a failed task:

1. The user is routed to the create-task page in edit-for-rerun mode.
2. The create-task page loads the source task execution's submission draft.
3. All original task values are prefilled exactly.
4. The page displays the failed-task edit banner.
5. The original failed execution is not modified.
6. Submitting the edited form starts a new run.
7. The new run is linked to the original execution as an edited rerun.

### Clicking Rerun on a failed task

When the user clicks **Rerun** on a failed task:

1. The user confirms the rerun, if confirmation is enabled.
2. The system creates a new run using the original submission draft.
3. The new run preserves the original steps, choices, instructions, inputs, and advanced settings.
4. The original failed execution is not modified.
5. The user receives a success toast or is routed to the new run.
6. The new run is linked to the original execution as a rerun.

### Clicking Resume on a failed task

When the user clicks **Resume** on a failed task:

1. The user confirms Resume, if confirmation is enabled.
2. The backend creates a linked follow-up execution using the original task input snapshot unchanged.
3. The backend pins the source by `workflowId` and `runId`.
4. The backend restores completed prior work from durable step output refs and workspace, branch, commit, or equivalent checkpoints.
5. The new execution marks prior completed steps as preserved from the source run.
6. The new execution starts newly executed work at the last failed step.
7. The original failed execution is not modified.
8. The user receives a success toast or is routed to the resumed run.
9. The new run is linked to the original execution as `Resumed from failed step`.

## Non-goals

The Task Details page does not mutate terminal executions in place.

The Task Details page does not hide valid failed-task actions merely because remediation is available.

The Task Details page does not reconstruct edit-form state from display-only labels when a canonical submission draft is available.

The Task Details page does not allow destructive actions without confirmation.

The Task Details page does not allow task input editing as part of failed-step **Resume**. Editing instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies belongs to **Edit task**.

## Acceptance criteria

The page is considered correct when all of the following are true:

1. A failed task displays **Remediate**, **Edit task**, **Rerun**, and **Resume** when the backend capabilities allow them.
2. **Remediate** opens the remediation flow.
3. **Edit task** on a failed task opens `/tasks/new?rerunExecutionId=:taskExecutionId&mode=edit`.
4. The edit-for-rerun create-task page loads every original step and selected choice exactly.
5. Submitting an edited failed task creates a new run and does not mutate the failed execution.
6. **Rerun** creates a new run using the exact original task configuration.
7. **Resume** creates a linked follow-up execution that reuses completed prior work and starts at the last failed step.
8. The original failed task remains visible and unchanged after remediation, edit-for-rerun, rerun, or Resume.
9. Remediation availability does not suppress edit, rerun, or Resume availability.
10. All actions are rendered from explicit capability fields.
11. Users can inspect failure details, original configuration, outputs, artifacts, logs, related runs, and metadata from the page.
