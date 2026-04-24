# Task Editing System

**Status:** Active
**Owners:** MoonMind Engineering
**Last Updated:** 2026-04-13

## 1. Purpose

This document defines the canonical task editing design for the Temporal era of MoonMind.

The legacy application allowed operators to open a task from the task details page, edit its configuration, and resubmit the task. That behavior was lost during the migration away from queue-job-centric task execution. The Temporal-native design restores that capability without reintroducing queue-era assumptions.

The core idea is simple:

- `/tasks/new` remains the single task submission surface.
- Existing Temporal executions become the editable object.
- The task details page regains an **Edit** action for executions that support in-place input updates.
- Terminal executions, including failed and canceled executions, gain a **Rerun** action that reuses the same prefilled submit experience and allows the operator to edit the draft before submitting it again.

This keeps create, edit, and rerun on one form while making Temporal the source of truth.

## 2. Scope

This design applies to:

- the shared task submit page at `/tasks/new`
- Temporal-backed task detail surfaces in Mission Control
- Temporal-backed list or card surfaces that may expose edit or rerun entry points
- input reconstruction from Temporal execution details and referenced artifacts
- submission through Temporal execution updates

This design does **not** apply to:

- legacy queue jobs
- proposal review flows
- recurring schedule editing
- inline edit modals on the detail page
- direct mutation of previously stored artifacts
- non-Temporal task execution systems

## 3. Design goals

### 3.1 Temporal is the source of truth

Task editing must operate on Temporal execution state, capabilities, and inputs. No queue-job payload should be required to support edit or rerun.

### 3.2 One submit experience

Create, edit, and rerun should all reuse `/tasks/new` so operators do not learn separate flows.

### 3.3 Preserve create-form ergonomics

Editing should feel like reopening a familiar task form with the same fields, validations, templates, and artifact behavior.

### 3.4 Respect lifecycle correctness

Active executions can be edited only when the backend exposes that capability. Terminal executions cannot be “edited in place”; they can only be rerun.

Failed and canceled terminal executions are first-class rerun candidates. When the backend exposes rerun capability for one of these executions, the operator must be able to reopen its task inputs as an editable draft, change the relevant fields, and submit the updated draft as a new rerun request.

### 3.5 Preserve auditability

The system must preserve operator-visible lineage between the original execution, any new artifacts created during editing, and any rerun request.

### 3.6 Avoid in-place artifact mutation

Editing a task must never rewrite historical artifacts. New artifacts are created for new input content.

### 3.7 Remove legacy coupling

The canonical model must not rely on `editJobId`, queue resubmit semantics, or queue-first route structures.

## 4. Canonical model

### 4.1 Editable object

The editable object is a Temporal execution identified by `workflowId`.

### 4.2 Supported workflow type

The initial supported workflow type is:

- `MoonMind.Run`

Any other workflow type is out of scope until explicitly added.

### 4.3 Modes

`/tasks/new` supports three modes:

| Mode | When used | Behavior |
|---|---|---|
| Create | No edit/rerun query parameter present | Starts a brand-new execution |
| Edit | `editExecutionId` points to an active execution that supports updates | Sends `UpdateInputs` to the existing workflow |
| Rerun | `rerunExecutionId` points to a terminal execution that supports rerun | Reconstructs an editable draft and sends `RequestRerun` |

### 4.4 Lifecycle model

- **Edit** is for non-terminal executions only.
- **Rerun** is for terminal executions only.
- Failed and canceled executions are terminal, but they must be eligible for rerun when `actions.canRerun` is `true`.
- Rerun mode must allow the operator to edit reconstructed task inputs before submitting the rerun request.
- UI availability is determined by backend capability flags, not frontend guesses.

## 5. Route model

### 5.1 Canonical routes

Create mode:

```text
/tasks/new
```

Edit mode:

```text
/tasks/new?editExecutionId=<workflowId>
```

Rerun mode:

```text
/tasks/new?rerunExecutionId=<workflowId>
```

### 5.2 Deprecated routes and params

The following are deprecated and must not be used by new UI or documentation:

- `/tasks/queue/new`
- `editJobId`
- queue-job update flows
- queue resubmit terminology when referring to Temporal reruns

### 5.3 Mode resolution order

When `/tasks/new` loads, mode is resolved in this order:

1. `rerunExecutionId`
2. `editExecutionId`
3. create mode

This ensures rerun remains explicit and cannot be accidentally overridden by edit handling.

## 6. Entry points

### 6.1 Task detail page

The task details page is the primary place where editing becomes visible again.

The detail page should:

- show **Edit** when `actions.canUpdateInputs` is `true`
- show **Rerun** when `actions.canRerun` is `true`
- navigate to `/tasks/new` using the correct query parameter
- avoid rendering actions that are not supported by the current execution

Canonical navigation targets:

```text
Edit   -> /tasks/new?editExecutionId=<workflowId>
Rerun  -> /tasks/new?rerunExecutionId=<workflowId>
```

### 6.2 Task list or card surfaces

List and card surfaces may optionally expose the same actions, but the detail page is the canonical entry point that restores the lost edit behavior.

## 7. Submit-page behavior

### 7.1 Shared page, mode-specific behavior

`/tasks/new` remains a shared page, but mode changes:

- the title
- the primary CTA label
- the data source for initial field values
- the submit handler
- some control visibility

### 7.2 Mode-specific UI expectations

| Mode | Page title | Primary CTA |
|---|---|---|
| Create | New Task | Create Task |
| Edit | Edit Task | Save Changes |
| Rerun | Rerun Task | Rerun Task |

### 7.3 Hidden or constrained controls

In edit and rerun modes:

- recurring schedule controls are hidden
- queue-specific controls are absent
- controls unsupported by the current workflow type are disabled or omitted

### 7.4 Loading behavior

For edit and rerun:

1. Parse the execution id from the route.
2. Load Temporal execution detail.
3. Validate workflow type and capabilities.
4. Rebuild a standard submission draft from the execution and any referenced artifacts.
5. Render the shared form with those prefilled values.

## 8. Prefill model

### 8.1 Draft reconstruction helper

The canonical reconstruction entry point is:

```ts
buildTemporalSubmissionDraftFromExecution(execution)
```

This helper converts a Temporal execution detail payload into the same draft shape used by normal task creation.

### 8.2 Draft data sources

Draft reconstruction may read from:

- execution detail fields
- execution input parameters
- referenced input artifacts
- template metadata that was applied during original submission
- provider/runtime configuration stored on the execution

### 8.3 Fields that should prefill

The reconstructed draft should prefill, where available:

- runtime
- provider profile
- model
- effort
- repository
- starting branch
- target branch
- publish mode
- task instructions
- primary skill
- explicit steps or workflow options
- applied template state
- other standard create-form inputs already supported by `/tasks/new`

### 8.4 Instructions and artifacts

Instructions may be stored inline or in artifacts.

The submit page must reconstruct the operator-visible instruction text regardless of storage strategy. Operators should not have to reason about whether the prior task used inline text or artifact-backed content.

### 8.5 Fallback behavior

If an execution cannot fully reconstruct a draft:

- the page must show a clear error state
- partial, misleading prefills must be avoided
- the operator must not be allowed to submit unsupported or malformed updates

## 9. Submit semantics

### 9.1 Create mode

Create mode continues to use the normal task creation path and is unchanged by this design.

### 9.2 Edit mode

Edit mode updates an existing Temporal execution in place.

Flow:

1. Validate the shared form.
2. Externalize edited instructions or large inputs when required.
3. Build a Temporal update payload.
4. Submit the update to the existing workflow.

Canonical request:

```http
POST /api/executions/{workflowId}/update
```

Canonical update name:

```json
{ "updateName": "UpdateInputs" }
```

The payload should include the newly prepared input state, which may contain:

- a new `inputArtifactRef` when the edited inputs are artifact-backed
- a `parametersPatch` for structured values
- any other update fields required by the backend contract

A helper such as `buildTemporalArtifactEditUpdatePayload(...)` may be used to assemble this request.

### 9.3 Rerun mode

Rerun mode uses the same prefilled form but requests a rerun instead of mutating the original terminal execution.

For failed or canceled executions, rerun mode is the supported way to edit the prior task and submit it again. The original execution remains immutable and terminal; the shared form reconstructs the previous input state, allows the operator to change instructions, runtime options, repository or branch fields, skills, templates, and other supported inputs, and then sends those edited values through `RequestRerun`.

Canonical request:

```http
POST /api/executions/{workflowId}/update
```

Canonical update name:

```json
{ "updateName": "RequestRerun" }
```

The rerun request uses the edited or confirmed form values and follows the same artifact rules as edit mode.

### 9.4 No queue fallback

There is no queue-era fallback path. If Temporal editing or rerun is unsupported, the UI must fail explicitly rather than silently routing through legacy queue logic.

## 10. Artifact rules

### 10.1 No historical mutation

Previously stored input artifacts are immutable for the purpose of task editing. An edit must create new artifact references instead of mutating old ones.

### 10.2 Preserve lineage

The system should preserve enough metadata to understand:

- which execution was edited
- which new artifacts were created for the edit
- whether the action was an in-place input update or a rerun request

### 10.3 Externalization policy

Large instruction bodies or other large inputs should continue to use the platform’s normal artifact externalization rules.

## 11. API contract

### 11.1 Read contract requirements

The Temporal execution read path must expose enough information to rebuild the submit-page draft and render correct actions.

At minimum, the detail payload should make available:

- `workflowId`
- workflow type
- current run identity as needed by the detail page
- current input parameters
- referenced input artifacts
- capability flags such as `actions.canUpdateInputs` and `actions.canRerun`
- any template, runtime, model, repository, and publish information required to reconstruct the form

For failed and canceled `MoonMind.Run` executions, the read contract must expose enough input state for the submit page to build an editable rerun draft whenever `actions.canRerun` is `true`.

### 11.2 Update contract requirements

The update endpoint must support:

- `updateName = "UpdateInputs"`
- `updateName = "RequestRerun"`

The contract should allow the frontend to send:

- structured parameter patches
- new artifact references for edited input content
- enough context for the backend to validate the request against workflow state

### 11.3 Response handling

The frontend must handle accepted update responses and surface meaningful state to the operator.

The backend may indicate outcomes such as:

- applied immediately
- scheduled for the next safe point
- handled through continue-as-new

Those states must be reflected in success messaging and redirect behavior.

## 12. Redirect and refresh behavior

After a successful edit or rerun request:

- redirect back to the Temporal detail route for that workflow
- refresh detail data so the operator sees the latest backend state
- for rerun, land on the latest-run view when the detail experience supports it

The operator should always return to the execution context they came from rather than being dropped into a generic queue or list page.

## 13. Validation and guardrails

The submit page must block submission when any of the following is true:

- the workflow type is unsupported
- the requested mode is unsupported for the current execution
- required fields cannot be reconstructed or are missing
- required artifacts cannot be created
- backend capability flags do not allow the requested action
- the backend rejects the update because the workflow state changed

Errors should be explicit and operator-readable. The frontend should not pretend a task is editable if Temporal says it is not.

## 14. Observability and audit

The system must preserve a clear audit trail across:

- the original execution
- the operator action taken from the detail page
- any newly created artifacts
- any backend update result, including deferred application semantics
- any rerun instance created from a terminal execution

Operator-visible detail views should make it possible to understand whether a change was applied immediately, scheduled for a safe point, or resulted in a rerun path.

## 15. Non-goals

This design intentionally does not include:

- a separate edit-only form distinct from `/tasks/new`
- queue-first task editing UX
- proposal editing
- manifest-run editing
- recurring schedule editing
- inline quick-edit controls on the detail page
- direct mutation of historical artifacts

## 16. Deprecated legacy references

The following concepts should be removed from canonical docs and primary UI flows when describing Temporal task editing:

- `editJobId`
- `/tasks/queue/new`
- queue-job update behavior
- queue resubmit language when the action is actually a Temporal rerun
- proposal-centric wording for standard task editing

## 17. Completion criteria

This design is considered implemented when all of the following are true:

1. The task detail page once again exposes an **Edit** action for supported Temporal executions.
2. `/tasks/new?editExecutionId=<workflowId>` opens with a prefilled draft reconstructed from Temporal execution data and artifacts.
3. `/tasks/new?rerunExecutionId=<workflowId>` opens the same shared form in rerun mode.
4. Edit submissions call `UpdateInputs`.
5. Rerun submissions call `RequestRerun`.
6. Failed and canceled executions with `actions.canRerun = true` open an editable prefilled rerun draft and can be submitted again with changed inputs.
7. Edited input content creates new artifact refs rather than mutating old artifacts.
8. Operators return to the Temporal detail experience after success.
9. Canonical documentation and UI no longer rely on queue-era route params or terminology.
10. Regression tests cover the detail-page entry point, prefill reconstruction, submit behavior, failed/canceled rerun editing, and redirect flow.

## 18. Implementation tracking

Implementation sequencing and migration details should be tracked in:

```text
docs/Tasks/TaskEditingSystem.md
```

That plan should remain an execution checklist. This document is the canonical target-state design.
