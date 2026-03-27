# Task Editing System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-27

## 1. Purpose

Define how users edit task inputs before or during execution, and how they rerun
terminal executions with modified inputs.

## 2. UX Design

### 2.1 Entry points

Task detail may expose:

- **Edit** for proposals that have not yet been promoted
- **Edit** for active or deferred Temporal executions
- **Rerun** for terminal Temporal executions

### 2.2 Flow

1. User clicks **Edit** or **Rerun**.
2. Browser navigates to `/tasks/queue/new?editJobId=<id>` as a compatibility
   alias for the standard create page.
3. The page fetches current proposal or execution data and pre-fills the form.
4. On submit:
   - proposals update in place through the proposal APIs
   - active/deferred executions use `POST /api/executions/{workflowId}/update`
   - terminal executions create a fresh run through `POST /api/executions`

## 3. Edit Mode

Editing a running or scheduled `MoonMind.Run` execution is supported via
Temporal Workflow Updates.

Behavior:

- submit to `POST /api/executions/{workflowId}/update`
- use `updateName="UpdateInputs"` for input changes
- use `updateName="SetTitle"` for title-only changes
- keep the same workflow execution identity while applying the update

The UI should surface accepted/applied/message semantics honestly rather than
pretending every edit was applied instantly.

## 4. Rerun Mode

When editing a terminal workflow:

- the UI submits a new create request to `POST /api/executions`
- the original execution remains immutable history
- the new execution gets a new `workflowId`
- lineage should remain visible in summaries or audit artifacts when needed
