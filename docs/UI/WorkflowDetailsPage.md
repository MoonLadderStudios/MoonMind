# Workflow Details Page

## Purpose

For **Codex via Omnigent**, the runtime label, lifecycle stages, safe refs, controls, terminal envelope, and post-cleanup evidence are defined by [`docs/Omnigent/CodexCreateToHostContract.md`](../Omnigent/CodexCreateToHostContract.md).

The Workflow Details page is the canonical view for inspecting a single MoonMind Workflow Execution. It presents the Workflow identity, current state, original Workflow configuration, execution history, outputs, errors, and all actions that are available for the Workflow in its current state.

The page is declarative and state-driven. Every visible control is derived from the Workflow Execution status, the Workflow type, and explicit backend capabilities. A failed Workflow presents the user with the complete set of available recovery actions, which may include **Remediate**, **Edit Workflow**, **Rerun**, and **Resume**.

## Route

The page is available from Workflows lists, Workflow notifications, remediation links, and run history links.

```text
/workflows/{workflowId}
```

The page accepts a Workflow Execution identifier and loads a single Workflow Execution detail record.

Addendum: `docs/UI/CollectionWorkspaceLayout.md` defines the shared entity-detail frame and far-left collection geometry. `docs/UI/WorkflowWorkspaceSidebar.md` defines the desktop workspace presentation that can host this same detail page next to the workflow sidebar. This document remains canonical for detail content, subroutes, primary actions, dialogs, logs, artifacts, and recovery behavior.

## Page-level goals

The page enables a user to:

1. Understand what Workflow ran or is running.
2. See the current Workflow status and relevant timestamps.
3. Inspect the exact steps, choices, instructions, and configuration used for the Workflow.
4. View progress, logs, outputs, artifacts, and failure information.
5. Take the correct next action for the Workflow state.
6. For failed Workflows, choose between remediation, editing the failed Workflow for a new run, rerunning the Workflow exactly as originally submitted, or resuming from the last failed step when prior work can be restored.

## Source of truth

The page is rendered from a Workflow Execution detail model that includes the Workflow Execution, original submission draft, current status, derived capabilities, artifacts, logs, and related runs.

The UI does not infer unavailable actions from status alone. The UI displays actions from explicit capability fields.

Required page data:

```ts
type WorkflowDetailsPageData = {
  execution: WorkflowExecutionDetail;
  submissionDraft: WorkflowSubmissionDraft | null;
  capabilities: WorkflowExecutionCapabilities;
  progress: WorkflowProgress | null;
  outputs: WorkflowOutput[];
  artifacts: WorkflowArtifact[];
  failure: WorkflowFailure | null;
  remediation: WorkflowRemediationSummary | null;
  relatedRuns: RelatedWorkflowRun[];
  auditEvents: WorkflowAuditEvent[];
};
```

Required capability fields:

```ts
type WorkflowExecutionCapabilities = {
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
  canCopyWorkflowLink: boolean;
};
```

## Page layout

The page contains the following major regions:

1. Header
2. Primary action bar
3. Status summary
4. Workflow configuration summary
5. Execution progress
6. Failure and remediation panel, when applicable
7. Outputs and artifacts
8. Logs and events
9. Related runs
10. Metadata and audit details

Desktop Workflow detail renders inside the shared `EntityDetailFrame`: breadcrumb; title/subtitle/status; primary and overflow actions; summary/facts strip; tabs/sections; main evidence slab; and optional right facts rail. The frame is the primary pane sibling of the far-left Workflow sidebar; it never owns that sidebar and the pair is never centered inside a max-width wrapper. Recurring schedule detail uses the same structural, spacing, status, action, tab, facts-rail, loading, error, and responsive primitives with a schedule adapter. Mobile stacks these regions and removes the desktop collection sidebar.

## Header

The header is always present.

The header contains:

- A breadcrumb or back link to the Workflows list.
- The Workflow name or Workflow title.
- A status badge.
- A short Workflow description or prompt summary, when available.
- The primary action bar.
- A Workflow overflow menu for secondary utility actions.

Example desired layout:

```text
Workflows / Customer Renewal Research

Customer Renewal Research                     [Failed]
Research customer renewal risks and summarize next steps.

[Remediate] [Edit Workflow] [Rerun] [Resume] [More]
```

### Title behavior

The title displays the user-provided Workflow name when present. If no explicit Workflow name exists, the title displays a generated summary of the Workflow objective. If neither is available, the title displays:

```text
Untitled workflow
```

### Status badge

The status badge is always visible and uses one of the canonical Workflow statuses:

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

Actions are additive. Showing one action never hides another valid action. In particular, **Remediate** does not replace **Edit Workflow**, **Rerun**, or **Resume** on failed Workflows.

### Failed Workflow actions

For a failed MoonMind Workflow, the primary action bar contains all of the following actions when the corresponding capabilities are true:

```text
[Remediate] [Edit Workflow] [Rerun] [Resume]
```

The desired failed-Workflow action state is:

```ts
capabilities.canRemediate === true
capabilities.canEditForRerun === true
capabilities.canRerun === true
capabilities.canResumeFromFailedStep === true // only when checkpointed progress is restorable
capabilities.canUpdateInputs === false
```

Failed Workflows are terminal executions. The user may inspect, remediate, edit for a new run, rerun, or resume from the last failed step, but the original failed execution is not mutated in place.

### Remediate button

The **Remediate** button is present when:

```ts
capabilities.canRemediate === true
```

The button label is:

```text
Remediate
```

The button opens the remediation experience for the failed Workflow.

Desired destination:

```text
/workflows/{workflowId}/remediate
```

The button is usually the primary action for failed Workflows when remediation is available.

The button purpose is:

```text
Investigate and resolve the failure.
```

### Edit Workflow button

The **Edit Workflow** button is present when either of the following is true:

```ts
capabilities.canEditForRerun === true
capabilities.canUpdateInputs === true
```

The button label is:

```text
Edit Workflow
```

For failed Workflows, **Edit Workflow** opens the Create page in edit-for-rerun mode. The page loads the original Workflow setup exactly, including all steps, selected choices, instructions, inputs, attachments, tool selections, model settings, scheduling choices, and advanced configuration.

Failed-Workflow edit destination:

```text
/workflows/new?rerunExecutionId={workflowId}&mode=edit
```

Failed-Workflow edit behavior:

- The Create page title is `Edit Workflow`.
- The page displays a banner explaining that editing creates a new run.
- The original failed execution remains unchanged.
- The user can modify the loaded Workflow configuration.
- Submitting the form creates a new Workflow run using the edited configuration.
- The submit button label is `Run edited workflow`.

Required banner copy:

```text
You are editing a failed workflow. Your changes will create a new run. The original failed run will remain unchanged.
```

For non-terminal Workflows that support live input updates, **Edit Workflow** opens the Create page in update-inputs mode.

Running-Workflow edit destination:

```text
/workflows/new?editExecutionId={workflowId}
```

Running-Workflow edit behavior:

- The Create page title is `Edit Workflow`.
- The original Workflow Execution may be updated according to product rules.
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

For failed Workflows, **Rerun** starts a new run using the exact same original Workflow configuration. It does not open an editing form and does not mutate the original failed execution.

Rerun behavior:

- Uses the original submission draft or reconstructed original workflow input.
- Preserves all original steps and choices.
- Preserves original instructions and artifact references when unchanged.
- Creates a new Workflow Execution.
- Links the new run back to the original Workflow Execution as a rerun.
- Leaves the original failed Workflow unchanged.

Rerun executes directly from the capability-gated action control without collecting extra operator input.

After a successful rerun request, the user is taken to the new Workflow Execution details page or shown a success toast with a link to the new run.

### Resume button

The failed-Workflow **Resume** button is present when:

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

The failed-Workflow **Resume** action is separate from the paused-Workflow **Resume** lifecycle action. Failed-Workflow **Resume** retries the last failed step with the completed work before that step restored from durable checkpoints.

Resume behavior:

- Does not open an editing form.
- Uses the original Workflow input snapshot unchanged.
- Pins the source execution by both `workflowId` and `runId`.
- Identifies the last failed step from backend progress data.
- Restores completed prior steps from durable step output refs and workspace, branch, commit, or equivalent checkpoints.
- Creates a linked follow-up execution.
- Displays prior completed steps in the new execution as reused from the original run.
- Starts new execution work at the failed step.
- Leaves the original failed Workflow unchanged.

Resume executes directly from the capability-gated action control without collecting extra operator input.

After a successful Resume request, the user is taken to the resumed Workflow Execution details page or shown a success toast with a link to the resumed run.

### Cancel button

The **Cancel** button is present when:

```ts
capabilities.canCancel === true
```

The button cancels a pending, scheduled, or running Workflow according to Workflow Execution rules.

The original Workflow page remains available after cancellation.

### Pause and lifecycle Resume buttons

The **Pause** button is present when:

```ts
capabilities.canPause === true
```

The lifecycle **Resume** button is present when:

```ts
capabilities.canResume === true
```

Only one of **Pause** or lifecycle **Resume** is visible at a time. This lifecycle action resumes a paused active Workflow and is not the failed-Workflow **Resume** action described above.

### More menu

The overflow menu contains secondary utility actions that are valid for the Workflow.

Possible menu items:

- Copy workflow link
- Copy workflow ID
- Copy run ID
- View raw input
- View raw output
- Download logs
- Download artifacts
- Open related run
- Archive workflow, if supported
- Delete workflow, if supported

Destructive Workflow lifecycle actions are visually separated, clearly labeled, and capability-gated by the backend.

## Action visibility by status

The page follows this desired action matrix.

| Workflow status | Remediate | Edit Workflow | Rerun | Failed step recovery | Cancel | Pause | Lifecycle resume |
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

The matrix is descriptive. The final source of truth is the capability object returned with the Workflow detail data.

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

When the Workflow is failed, the failure reason is visible without requiring the user to open logs.

## Workflow configuration summary

The Workflow configuration summary is always present when the original Workflow configuration is available.

It displays the exact user-visible configuration used to create the Workflow.

Required content:

- Workflow objective
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

The page displays every step included in the Workflow, in the original order.

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

### Exactness requirement for failed-Workflow edit

The configuration shown on the Workflow Details page and the configuration loaded by **Edit Workflow** for a failed Workflow come from the same source of truth.

When a user selects **Edit Workflow** on a failed Workflow, the edit form contains exactly the same values shown in the Workflow configuration summary unless the backend reports that an option is no longer available.

If an option is no longer available, the edit form displays the prior value, marks it as unavailable, and prompts the user to choose a replacement before submitting.

## Execution progress section

The execution progress section is present for Workflows with progress data.

It contains:

- Overall progress state
- Current step
- Completed steps
- Failed step, when applicable
- Pending steps
- Step-level timestamps
- Step-level outputs or summaries, when available
- Preserved steps reused from a source run, when viewing a resumed execution

For running Workflows, this section updates as new progress is received.

For terminal Workflows, this section displays the final execution path.

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

The failure section must not obscure the primary action bar. Users can always see or quickly access **Remediate**, **Edit Workflow**, and **Rerun** on failed Workflows.

Example:

```text
Failure
The workflow failed while generating the final customer summary.

Failed step
Draft customer-facing summary

Error
The selected source artifact was unavailable.

Suggested actions
- Remediate the failed workflow to diagnose and resolve the issue.
- Edit the workflow to change inputs or choices and run it again.
- Rerun the workflow exactly as originally configured.
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

For failed Workflows with remediation available, the panel includes a **Remediate** button in addition to the header action.

Panel button label:

```text
Remediate
```

Panel button destination:

```text
/workflows/{workflowId}/remediate
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

For failed Workflows, partial outputs remain visible if they were produced before failure.

If no outputs exist, the section displays an empty state:

```text
No outputs were produced for this workflow.
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

For failed Workflows, the logs section highlights log lines related to the failure.

Default view shows user-relevant logs first. Raw technical logs are available through an expandable advanced view.

## Events and audit section

The events and audit section contains a chronological timeline of meaningful Workflow events.

Events may include:

- Workflow created
- Workflow scheduled
- Workflow started
- Step started
- Step completed
- Step failed
- Workflow paused
- Workflow resumed
- Workflow canceled
- Workflow failed
- Workflow completed
- Remediation started
- Remediation completed
- Workflow rerun requested
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
- Reruns created from the current Workflow
- Edited reruns created from the current Workflow
- Resumed runs created from the current Workflow
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
- Recovered from failed step
- Remediation run

For a failed Workflow, once the user clicks **Rerun**, clicks **Resume**, or submits an edited Workflow from **Edit Workflow**, the new run appears in this section.

## Metadata section

The metadata section is present for users with permission to view technical details.

It contains:

- Workflow Execution ID
- Workflow ID
- Run ID
- Workflow type
- Queue name, when applicable
- Attempt number
- Parent Workflow, when applicable
- Source Workflow, when this is a rerun
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

When the Workflow Execution does not exist or the user does not have access, the page displays:

```text
Workflow not found

This workflow may have been deleted, moved, or you may not have permission to view it.
```

Primary action:

```text
Back to workflows
```

### Data unavailable state

When the Workflow exists but some details cannot be loaded, the page displays the available Workflow summary and an inline warning for the missing section.

Example:

```text
Workflow configuration unavailable

The original workflow configuration could not be loaded. You can still view status and logs.
```

If the Workflow configuration is unavailable, the **Edit Workflow** and **Rerun** actions are hidden unless the backend confirms that a valid submission draft or reconstructable workflow input is available.

## Accessibility requirements

The page is fully keyboard accessible.

Required accessibility behavior:

- All action buttons are reachable by keyboard.
- All buttons have clear accessible names.
- Status is exposed to assistive technology.
- Failure messages are announced when they load.
- Toasts and action result messages use accessible live regions.
- Text-entry dialogs, where an action inherently requires text input, trap focus while open.
- Focus returns to the triggering action after a text-entry dialog is closed.
- Copy actions provide accessible success feedback.

Button accessible names:

```text
Remediate workflow
Edit Workflow
Rerun workflow
Cancel workflow
Pause workflow
Resume workflow
Resume from failed step
Copy workflow link
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
- The failed-Workflow actions remain available and are not hidden behind remediation alone.

## Copy requirements

### Failed Workflow header actions

```text
Remediate
Edit Workflow
Rerun
Resume
```

### Failed Workflow edit banner

```text
You are editing a failed workflow. Your changes will create a new run. The original failed run will remain unchanged.
```

### Rerun success toast

```text
Workflow rerun started.
```

Toast action:

```text
View new run
```

### Resume success toast

```text
Workflow resumed from failed step.
```

Toast action:

```text
View resumed run
```

### Edited rerun success toast

```text
Edited workflow run started.
```

Toast action:

```text
View new run
```

### Remediation unavailable message

```text
Remediation is not available for this workflow.
```

### Edit unavailable message

```text
This workflow cannot be edited because its original configuration is unavailable.
```

### Rerun unavailable message

```text
This workflow cannot be rerun because its original configuration is unavailable.
```

### Resume unavailable message

```text
This workflow cannot be resumed because the completed work before the failed step is not recoverable.
```

## State-specific desired page examples

### Failed Workflow

A failed Workflow page contains:

- Header with Workflow title and `Failed` status badge.
- Primary action bar with **Remediate**, **Edit Workflow**, **Rerun**, and **Resume** when backend capabilities allow them.
- Status summary with failure timestamp and duration.
- Failure section with failed step, error summary, and technical details.
- Workflow configuration summary with original steps and choices.
- Execution progress showing completed steps and the failed step.
- Partial outputs, if any.
- Artifacts, if any.
- Logs with failure-relevant entries highlighted.
- Related runs, if reruns or remediation runs exist.
- Metadata and audit events.

Desired failed-Workflow action model:

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
  canCopyWorkflowLink: true
}
```

### Running Workflow

A running Workflow page contains:

- Header with Workflow title and `Running` status badge.
- Primary actions for any valid running-state actions, such as **Edit Workflow**, **Pause**, or **Cancel**.
- Live execution progress.
- Current step.
- Partial outputs, if available.
- Logs and events.

Running Workflows do not show **Remediate** or **Rerun**.

### Completed Workflow

A completed Workflow page contains:

- Header with Workflow title and `Completed` status badge.
- **Rerun** action when supported.
- Optional **Edit Workflow** action when edit-for-rerun is supported.
- Final outputs.
- Artifacts.
- Execution timeline.
- Related runs.

### Canceled, timed out, or terminated Workflow

A canceled, timed out, or terminated Workflow page contains:

- Header with the corresponding terminal status badge.
- **Rerun** action when supported.
- **Edit Workflow** action when edit-for-rerun is supported.
- Remediation action when the backend explicitly marks remediation as available.
- Status reason.
- Timeline and logs.

## Interaction contracts

### Clicking Remediate

When the user clicks **Remediate**:

1. The user is routed to the remediation experience for the current Workflow.
2. The remediation experience receives the current Workflow Execution ID.
3. The original Workflow details page remains reachable by back navigation.

### Clicking Edit Workflow on a failed Workflow

When the user clicks **Edit Workflow** on a failed Workflow:

1. The user is routed to the Create page in edit-for-rerun mode.
2. The Create page loads the source Workflow Execution's submission draft.
3. All original Workflow values are prefilled exactly.
4. The page displays the failed-Workflow edit banner.
5. The original failed execution is not modified.
6. Submitting the edited form starts a new run.
7. The new run is linked to the original execution as an edited rerun.

### Clicking Rerun on a failed Workflow

When the user clicks **Rerun** on a failed Workflow:

1. The system creates a new run using the original submission draft.
2. The new run preserves the original steps, choices, instructions, inputs, and advanced settings.
3. The original failed execution is not modified.
4. The user receives a success toast or is routed to the new run.
5. The new run is linked to the original execution as a rerun.

### Clicking Resume on a failed Workflow

When the user clicks **Resume** on a failed Workflow:

1. The backend creates a linked follow-up execution using the original Workflow input snapshot unchanged.
2. The backend pins the source by `workflowId` and `runId`.
3. The backend restores completed prior work from durable step output refs and workspace, branch, commit, or equivalent checkpoints.
4. The new execution marks prior completed steps as preserved from the source run.
5. The new execution starts newly executed work at the last failed step.
6. The original failed execution is not modified.
7. The user receives a success toast or is routed to the resumed run.
8. The new run is linked to the original execution as `Recovered from failed step`.

## Non-goals

The Workflow Details page does not mutate terminal executions in place.

The Workflow Details page does not hide valid failed-Workflow actions merely because remediation is available.

The Workflow Details page does not reconstruct edit-form state from display-only labels when a canonical submission draft is available.

The Workflow Details page does not collect extra operator input before invoking Workflow lifecycle actions.

The Workflow Details page does not allow Workflow input editing as part of failed-step **Resume**. Editing instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies belongs to **Edit Workflow**.

## Acceptance criteria

The page is considered correct when all of the following are true:

1. A failed Workflow displays **Remediate**, **Edit Workflow**, **Rerun**, and **Resume** when the backend capabilities allow them.
2. **Remediate** opens the remediation flow.
3. **Edit Workflow** on a failed Workflow opens `/workflows/new?rerunExecutionId={workflowId}&mode=edit`.
4. The edit-for-rerun Create page loads every original step and selected choice exactly.
5. Submitting an edited failed Workflow creates a new run and does not mutate the failed execution.
6. **Rerun** creates a new run using the exact original Workflow configuration.
7. **Resume** creates a linked follow-up execution that reuses completed prior work and starts at the last failed step.
8. The original failed Workflow remains visible and unchanged after remediation, edit-for-rerun, rerun, or Resume.
9. Remediation availability does not suppress edit, rerun, or Resume availability.
10. All actions are rendered from explicit capability fields.
11. Users can inspect failure details, original configuration, outputs, artifacts, logs, related runs, and metadata from the page.
