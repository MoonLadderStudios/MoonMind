# Workflow Details Page

## Purpose

For the Codex harness on Omnigent (canonical identity `agentKind=external`, `agentId=omnigent`, nested harness `codex-native`), the lifecycle stages, safe refs, controls, terminal envelope, and post-cleanup evidence are defined by [`docs/Omnigent/CodexCreateToHostContract.md`](../Omnigent/CodexCreateToHostContract.md) and reconciled end-to-end by [`docs/Omnigent/NormalCodexProductPathReconciliation.md`](../Omnigent/NormalCodexProductPathReconciliation.md).

The Workflow Details page is the canonical view for inspecting a single MoonMind Workflow Execution. It presents the Workflow identity, current state, original Workflow configuration, execution history, outputs, errors, and all actions that are available for the Workflow in its current state.

The page is declarative and state-driven. Every visible control is derived from the Workflow Execution status, the Workflow type, and explicit backend capabilities. A failed Workflow presents the user with the complete set of available recovery actions, which may include **Remediate**, **Edit Workflow**, **Rerun**, and **Retry from failed step**.

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
  canRetryFromStep: boolean;
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

[Remediate] [Edit Workflow] [Rerun] [Retry from failed step] [More]
```

### Title behavior

The title displays the user-provided Workflow name when present. If no explicit Workflow name exists, the title displays a generated summary of the Workflow objective. If neither is available, the title displays:

```text
Untitled workflow
```

### Source context (Repository + Starting Branch)

The shared detail hero renders a compact, read-only source-context strip
(`td-source-context`) directly under the title row, outside tab-specific
content. It shows **Repository** and **Starting Branch** together where
available and stays visible on the default Chat route (`/workflows/{workflowId}`)
as well as Overview, Execution, Evidence, and Debug, on desktop and mobile.
The default tab is unchanged; the strip is not gated by `overviewTabActive`.

Both the shared strip and Overview → Git & Publish read the same normalized
value from the selected execution's recorded `repository` / `startingBranch`
via `resolveWorkflowSourceContext()` in
`frontend/src/entrypoints/workflow-detail.tsx`. The full branch name keeps a
`title` tooltip with sensible wrapping/truncation (`break-all`,
`overflow-wrap: anywhere`).

Branch semantics:

- **Starting Branch** is the recorded source branch only. It is never derived
  from the generated work branch, checkpoint branch, publication destination,
  or PR base. **Target Branch** and **Published/Saved Work Branch** remain
  separate facts.
- A recorded resolved default (for example `develop (default)`) is shown
  verbatim as resolution evidence. An unresolved default is never labeled as
  an observed branch and `main` is never guessed: the backend projection only
  emits the `(default)` suffix from recorded `defaultBranch` evidence.
- The displayed context always belongs to the execution being viewed. The
  detail query key includes the workflow id, so navigation, polling, rerun, or
  resume replaces the record instead of retaining stale branch values.

Unavailable-data behavior (no new persistence):

- Repository-backed execution with a recorded branch: shows the branch in both
  the shared strip and Overview.
- Repository-backed execution with missing branch metadata: shows explicit
  **Not recorded** in both places (Overview previously omitted the row).
- Workflow without a repository source: the shared strip is intentionally
  omitted and Overview shows **Not applicable**.
- Loading: the detail loading placeholder covers the hero; no source-context
  row renders until the execution record resolves.

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

Actions are additive. Showing one action never hides another valid action. In particular, **Remediate** does not replace **Edit Workflow**, **Rerun**, or **Retry from failed step** on failed Workflows.

### Failed Workflow actions

For a failed MoonMind Workflow, the primary action bar contains all of the following actions when the corresponding capabilities are true:

```text
[Remediate] [Edit Workflow] [Rerun] [Retry from failed step]
```

The desired failed-Workflow action state is:

```ts
capabilities.canRemediate === true
capabilities.canEditForRerun === true
capabilities.canRerun === true
capabilities.canRetryFromStep === true // a closed UserWorkflow run that no parent owns
capabilities.canUpdateInputs === false
```

Failed Workflows are terminal executions. The user may inspect, remediate, edit for a new run, rerun, or retry from a step. Retry starts a new run of the same Workflow; the failed run stays in Temporal history and is not mutated.

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

### Retry from failed step

The **Retry from failed step** action and the **Retry** panel are present when:

```ts
capabilities.canRetryFromStep === true
```

Retry behavior:

- Does not open an editing form and keeps the original input, plan, and execution choices.
- Restarts the failed step by default. The panel's **Restart from** selector can choose an earlier step that started; its button then reads **Retry from selected step**.
- Sends `POST /api/executions/{workflowId}/retry-from-step` with the run the page shows as `sourceRunId`.
- Keeps the `workflowId`, so the page follows the Workflow to its new run. Earlier steps keep their results.
- Shows the backend's reason when retry is refused, such as a released issue claim or a history that no longer replays on the installed MoonMind version.

When editing is enabled and rerun is available, the panel also offers **Rerun from the start**. The panel stays hidden for runs that are still active or that completed.

### Cancel button

The **Cancel** button is present when:

```ts
capabilities.canCancel === true
```

It cancels a pending, scheduled, or running Workflow after a one-line confirmation, as do **Reject** and **Force cancel**; reversible **Pause** and **Resume** do not ask.

### Pause and lifecycle Resume buttons

The **Pause** button is present when:

```ts
capabilities.canPause === true
```

The lifecycle **Resume** button is present when:

```ts
capabilities.canResume === true
```

Only one of **Pause** or lifecycle **Resume** is visible at a time. This lifecycle action resumes a paused active Workflow and is unrelated to retrying a failed one.

### More menu

The toolbar **Workflow actions** menu holds the debug-details toggle and the capability-gated Workflow actions, built by the same code as the Workflows list row menu. Safe actions come first and take initial focus; destructive ones follow a divider. **Force cancel** stays under Troubleshooting even on terminal Workflows, whose projected state may be stale, and ineligible actions collapse under **Unavailable (n)** with plain-language reasons.

## Action visibility by status

| Workflow status | Remediate | Edit Workflow | Rerun | Retry from a step | Cancel | Pause | Lifecycle resume |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pending | No | Yes, if inputs can be updated | No | No | Yes | No | No |
| Scheduled | No | Yes, if inputs can be updated | No | No | Yes | No | No |
| Running | No | Yes, if inputs can be updated | No | No | Yes | Yes, if supported | No |
| Paused | No | Yes, if inputs can be updated | No | No | Yes | No | Yes |
| Completed | No | Optional, as edit-for-rerun | Yes | No | No | No | No |
| Failed | Yes | Yes, as edit-for-rerun | Yes | Yes | No | No | No |
| Canceled | No | Optional, as edit-for-rerun | Yes | Yes | No | No | No |
| Timed out | Optional, if failure can be remediated | Yes, as edit-for-rerun | Yes | Yes | No | No | No |
| Terminated | Optional, if failure can be remediated | Yes, as edit-for-rerun | Yes | Yes | No | No | No |

The matrix is descriptive; the capability object returned with the Workflow detail data decides.

## Status summary section

The status summary section is always present.

It contains:

- Current status
- Status reason, when available
- Created time
- Started time, when available
- Completed time, when available
- Duration, ticking from the start time while the Workflow runs
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

For running Workflows, this section updates as new progress is received.

For terminal Workflows, this section displays the final execution path.

After a retry, the earlier steps show their recorded results and the retried step shows as a new attempt.

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
- Retry availability, or the reason retry is unavailable

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
- Retry from the failed step to keep completed prior work.
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

### Authoritative remediation relationships

The Artifacts view renders a distinct **Remediation** region from canonical
remediation links, immutable Workflow inputs, context artifacts, lifecycle
artifacts, approval state, and Checkpoint Branch records. It never parses logs,
chat text, or model prose as authority.

The target-side **Remediation Workflows** list preserves the target's original
outcome separately and shows each remediation Workflow/run link, pinned target
run and selected step/checkpoint, current target state, phase/mode/authority,
profile and policy identity, mutation lock and release, diagnosis, latest action
delivery, approval, explicit repair verification, resolution, prevention,
created branch/run/PR, and cleanup evidence.

The remediation-side **Remediation Target** list additionally shows the complete
authored remediation contract; per-class context availability, freshness,
boundedness, and degradation; live-follow cursor/status; the bounded action
request and result authority chain; cumulative candidate attempt/head state;
Checkpoint Branch source, isolated work branch, publication/promotion/archive;
and the final repair, prevention, cleanup, lease-release, and unresolved-operator
work summary. Durable artifact refs remain available for the full evidence.

Action delivery and repair verification are separate. Branch creation, branch
execution, action delivery, and verification each have their own state. Actions
are offered only when the capability matrix says the execution, approval, and
verification backends are ready; disabled actions show bounded reasons.

Pending approval UI shows action, risk, expected state, policy, expiration,
blast radius, and artifact evidence. Authorized operators can approve or deny
with a rationale, which is sent as the durable decision `comment`. Expired or
stale decisions explain why a new request is required. Operator **Take over and
pause**, **Resume remediation**, and **Cancel remediation** use ordinary durable
Workflow signal/cancel endpoints, and only cancel asks first; takeover never
exposes credentials or raw runtime/session authority. All controls retain labels,
keyboard access, loading/error states, and responsive layout.

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

### Saved Results

The Artifacts tab shows a Saved Results section for the selected run. It reads only the run's artifact listing and never needs the original host, workspace, or source credential. It shows:

- Separate Compute, Save, Publication, and Cleanup outcomes. A failed or canceled run can still show committed saved work, a failed requested publication can still show a successful save, and pending cleanup never hides either.
- Each saved report, non-Git output, and saved-work unit with its completeness, exclusions, and retention. A saved-work unit lists its snapshot, delta, and file-manifest parts with their own downloads.
- Preview, and Download only when the server allows raw access. Expired or restricted outputs say so instead of offering a link.

Actions:

- **Publish saved work** opens a destination form prefilled from the workflow's repository, starting branch, and publish mode. Repository, publish mode, and head branch come first; the base branch and how saved work is applied appear when you ask for them or when the destination needs them. Submitting publishes the saved work without another model run, shows the returned publication workflow and operation, and follows that run until it finishes. The button is available only when the server reports that saved-work publication is allowed. The publication rollout setting is off by default; in that case, the section says why, and Preview, Download, and Continue working still work.
- **Continue working** asks for new instructions and starts a fresh linked Workflow that carries the authorized saved outputs.

If the server rejects an action, its message is shown. If the response is lost, the section says the request may have been accepted, and submitting the same request again reuses the same operation. The full contract is [Artifact Presentation Contract §11.6](../Artifacts/ArtifactPresentationContract.md#116-saved-result-presentation-in-workflow-detail).

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
- Retry from step requested

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
- Remediation run

For a failed Workflow, once the user clicks **Rerun** or submits an edited Workflow from **Edit Workflow**, the new run appears in this section. A retry keeps the same Workflow, so its new run replaces the failed run as the page's current run instead.

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

While loading, the page shows skeletons for the header, action bar, status summary, and main sections, and no action buttons until capabilities load.

### Load failure states

With no Workflow data, the page names the failure from the HTTP status and keeps the error as detail. **Workflow not found** (404/410) and **Access denied** (403) are final and never retried or polled; **Sign-in required** (401) and **Workflow unavailable** (5xx or network, after the shared retries) offer **Retry**.

### Refresh failures

A failed background refresh never replaces loaded data: the Workflow, steps, checkpoint branches, step history, session continuity, and artifacts keep their last data under a "Stale since <time>" notice with the error and **Retry**, and polling recovers by itself after a transient outage such as a managed update.

### Data unavailable state

When the Workflow exists but a section cannot load, the page keeps the summary and shows an inline warning for that section. Without a loadable configuration, **Edit Workflow** and **Rerun** stay hidden unless the backend confirms a valid submission draft or reconstructable input.

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
Retry from failed step
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
Retry from failed step
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

### Retry success notice

The notice carries the backend's message, for example:

```text
Retrying from Run tests; earlier steps are kept.
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

### Retry unavailable message

```text
Retry from a step is unavailable: This run belongs to a parent workflow, which owns its retries.
```

## State-specific desired page examples

### Failed Workflow

A failed Workflow page contains:

- Header with Workflow title and `Failed` status badge.
- Primary action bar with **Remediate**, **Edit Workflow**, **Rerun**, and **Retry from failed step** when backend capabilities allow them.
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
  canRetryFromStep: true,
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

### Clicking Retry from failed step

When the user retries a failed Workflow:

1. The backend checks that the run the page shows is still the latest run, that its history replays on the installed MoonMind version, and that any GitHub issue claim it holds is still live.
2. The backend resets the run's Temporal history to the start of the chosen step, keeping the original input unchanged.
3. Earlier steps keep their recorded results; the chosen step and later steps run again.
4. The Workflow keeps its `workflowId` and the page shows the new run; the failed run stays in Temporal history.
5. The user sees the backend's notice, or its reason when the retry is refused.

## Non-goals

The Workflow Details page does not mutate terminal executions in place.

Workflow Detail renders pending and completed remediation approvals from each
link's durable `approvalState`; it never reconstructs decisions from logs. The
projection includes bounded request/action/risk identity, status, actors,
decision and expiration timestamps, rationale, and audit/artifact references,
without credential bodies or raw infrastructure authority. The same projection
appears for the remediation execution and its target.

The Workflow Details page does not hide valid failed-Workflow actions merely because remediation is available.

The Workflow Details page does not reconstruct edit-form state from display-only labels when a canonical submission draft is available.

The Workflow Details page does not collect extra operator input before invoking Workflow lifecycle actions.

The Workflow Details page does not allow Workflow input editing as part of **Retry from failed step**. Editing instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies belongs to **Edit Workflow**.

## Acceptance criteria

The page is considered correct when all of the following are true:

1. A failed Workflow displays **Remediate**, **Edit Workflow**, **Rerun**, and **Retry from failed step** when the backend capabilities allow them.
2. **Remediate** opens the remediation flow.
3. **Edit Workflow** on a failed Workflow opens `/workflows/new?rerunExecutionId={workflowId}&mode=edit`.
4. The edit-for-rerun Create page loads every original step and selected choice exactly.
5. Submitting an edited failed Workflow creates a new run and does not mutate the failed execution.
6. **Rerun** creates a new run using the exact original Workflow configuration.
7. **Retry from failed step** starts a new run of the same Workflow that keeps the earlier steps' results and runs the failed step again.
8. The original failed run remains visible and unchanged after remediation, edit-for-rerun, rerun, or retry.
9. Remediation availability does not suppress edit, rerun, or retry availability.
10. All actions are rendered from explicit capability fields.
11. Users can inspect failure details, original configuration, outputs, artifacts, logs, related runs, and metadata from the page.
