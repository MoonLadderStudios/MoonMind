# Recurring Schedule Details Page - Story Breakdown

- Source design: `docs/UI/RecurringScheduleDetailsPage.md`
- Source document class: `canonical-declarative`
- Story extraction date: `2026-06-24T08:02:19Z`
- Output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines a Mission Control recurring schedule detail page as a schedule-flavored derivative of the normal workflow detail page. It centers the stable schedule definition identity, schedule controls, configuration, and run history while preserving normal workflow detail as the surface for spawned execution evidence. The page uses existing recurring-workflow endpoints and runtime config templates, gates delete until backend support exists, and keeps authorization, error, disabled, and attention states explicit.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **Schedule rows navigate to detail pages** - The /schedules list must link each schedule name/row to /schedules/{definitionId}, and selecting a schedule opens that detail page. Source: 3. Requirements; 4. Route Model.
- `DESIGN-REQ-002` (state-model): **Stable schedule identity drives routing** - definitionId is the stable MoonMind product identity for the schedule and remains stable across edits and spawned executions. Source: 4. Route Model.
- `DESIGN-REQ-003` (requirement): **New schedule creation redirects to detail** - After recurring schedule creation, the UI redirects to the created schedule detail route. Source: 4. Route Model.
- `DESIGN-REQ-004` (constraint): **Workflow detail composition is reused** - The detail page should be a schedule-flavored derivative of workflow detail, reusing shell, spacing, panels, status treatment, loading/error states, actions, and destructive confirmation patterns rather than inventing a separate design system. Source: 5. Page Composition; 13. Non-Goals.
- `DESIGN-REQ-005` (requirement): **Schedule-specific page concepts replace execution concepts** - The page maps workflow title/state/facts/actions to schedule name/state/facts/actions, omits execution-only tabs, and provides overview, runs, configuration, and optional activity areas. Source: 5. Page Composition; 6. Default Detail Layout.
- `DESIGN-REQ-006` (integration): **Detail data and runtime config endpoints are defined** - The page relies on schedule detail, update, run-now, and run-history endpoints and runtime config source templates; delete config appears only when backend delete exists. Source: 7. Data Contract.
- `DESIGN-REQ-007` (requirement): **Schedule editing is available on the detail page** - Users edit schedule properties from /schedules/{definitionId}, using an acceptable inline/drawer/modal pattern, without returning to /workflows/new or creating a replacement schedule. Source: 3. Requirements; 8. Edit Behavior; 13. Non-Goals.
- `DESIGN-REQ-008` (requirement): **Edit saves validate and preserve page context** - The UI validates cron and timezone, PATCHes changed fields, stays on the detail route, refetches data, and shows validation or Temporal reconciliation errors inline without mutating page state on failure. Source: 8. Edit Behavior; 11. Empty, Loading, and Error States.
- `DESIGN-REQ-009` (state-model): **Run history belongs to schedules but links to executions** - A recurring schedule owns a series of workflow executions; the detail page shows schedule-owned run history and links each run to normal workflow detail, where execution-specific artifacts, steps, logs, proposals, and diagnostics remain. Source: 10. Run History Relationship.
- `DESIGN-REQ-010` (integration): **Workflow detail can link back to schedule provenance** - Workflow detail pages may show Created by schedule provenance linking back to /schedules/{definitionId} when metadata is available. Source: 10. Run History Relationship.
- `DESIGN-REQ-011` (requirement): **Manual run, pause, resume, and attention states are surfaced** - The action area and summary/status surfaces expose run now, pause/resume, active/paused/disabled/needs-attention states, and last dispatch failures while preserving authorized controls. Source: 5. Page Composition; 6. Default Detail Layout; 11. Empty, Loading, and Error States.
- `DESIGN-REQ-012` (constraint): **Delete remains gated until backend support exists** - The UI is designed for deletion but must not render the delete action until DELETE and sources.schedules.delete are available. Source: 3. Requirements; 7. Data Contract; 9. Planned Delete Behavior; 11. Empty, Loading, and Error States.
- `DESIGN-REQ-013` (requirement): **Deleting a schedule stops future dispatch only** - When delete is implemented, confirmation calls DELETE, removes or disables future dispatch, redirects to /schedules on success, keeps the user on failure, and does not delete historical workflow executions or artifacts. Source: 9. Planned Delete Behavior; 13. Non-Goals.
- `DESIGN-REQ-014` (requirement): **Empty, loading, partial-error, disabled, and failure states are explicit** - The page distinguishes not found, localized run-history failure, Temporal reconciliation failure, disabled schedules, and dispatch attention while keeping appropriate controls available. Source: 11. Empty, Loading, and Error States.
- `DESIGN-REQ-015` (security): **Authorization follows schedule ownership rules** - Personal schedule permissions follow ownership; global schedule detail requires operator privileges; unauthorized users see normal unauthorized/not-found states; users without edit permission do not see usable edit/delete actions. Source: 12. Authorization Rules.
- `DESIGN-REQ-016` (non-goal): **Execution-specific surfaces are non-goals for schedule detail** - The schedule detail page must not duplicate workflow execution steps, artifacts, logs, proposals, or diagnostics, because those remain on normal workflow detail pages. Source: 5. Page Composition; 10. Run History Relationship; 13. Non-Goals.

## Ordered Story Candidates

### STORY-001: Navigate to recurring schedule details

- Short name: `schedule-detail-route`
- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 1. Purpose, 3. Requirements, 4. Route Model, 11. Empty, Loading, and Error States
- Why: The detail page is only useful if list and creation flows land on the stable schedule identity rather than an execution identity.
- Independent test: Create or mock a schedule row and creation response, then verify navigation lands on /schedules/{definitionId}, the detail loader uses definitionId, and not-found/unauthorized responses render the standard states without exposing schedule controls.
- Dependencies: None
- Needs clarification: None
- Scope:
  - Make schedule names or rows in /schedules navigate to /schedules/{definitionId}.
  - Route newly created recurring schedules to /schedules/{definitionId}.
  - Load schedule detail by definitionId and preserve that product identity across edits and spawned runs.
  - Show normal not-found or unauthorized state when detail access is missing.
- Out of scope:
  - Editing schedule fields beyond route-level loading and access behavior.
  - Run-history rendering beyond establishing the destination route.
- Acceptance criteria:
  - Each visible schedule row in /schedules exposes the primary schedule name as a link to /schedules/{definitionId}.
  - Clicking a schedule opens the detail page for that schedule definition.
  - Successful new recurring schedule creation redirects to /schedules/{definitionId}.
  - The route key is definitionId, not a workflow execution ID or Temporal run ID.
  - Unauthorized or missing schedules show the normal unauthorized or not-found state with a link back to /schedules where appropriate.
- Requirements:
  - The route model includes /schedules and /schedules/{definitionId}.
  - definitionId remains the stable product identity across schedule edits and spawned executions.
  - Detail access failures do not degrade into a read-only global schedule page for unauthorized users.
- Owned coverage:
  - `DESIGN-REQ-001`: Owns list-to-detail navigation.
  - `DESIGN-REQ-002`: Owns stable route identity and loader key behavior.
  - `DESIGN-REQ-003`: Owns post-create redirect to the detail route.
  - `DESIGN-REQ-014`: Owns not-found state for missing schedule detail.
  - `DESIGN-REQ-015`: Owns unauthorized detail access state for the route.

### STORY-002: Render schedule-flavored detail overview

- Short name: `schedule-detail-overview`
- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 1. Purpose, 5. Page Composition, 6. Default Detail Layout, 7. Data Contract, 11. Empty, Loading, and Error States, 13. Non-Goals
- Why: Operators should recognize recurring schedules as control-plane objects that create normal workflow executions, without learning a new visual language.
- Independent test: Load a mocked schedule detail with active, disabled, and failed-dispatch variants and verify the page uses workflow-detail conventions, renders schedule-specific fields, omits execution-only tabs, and localizes partial run-history errors without disabling the rest of the controls.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Reuse workflow detail shell, header spacing, panel rhythm, facts rail, tabs, loading states, error states, status badge style, and action placement where practical.
  - Show schedule name, description or target summary, state, summary cards, overview, configuration, optional activity, and facts rail using schedule data.
  - Expose schedule states active, paused, disabled, and needs attention without treating disabled as failed.
  - Omit execution-only steps and artifacts tabs by default.
- Out of scope:
  - Full edit implementation, run-history table behavior, or delete execution.
  - Duplicating spawned workflow logs, artifacts, proposals, diagnostics, or execution steps on the schedule page.
- Acceptance criteria:
  - The detail page presents breadcrumb, title, state badge, target summary, action area, summary cards, main tabs, and facts rail using the existing workflow-detail visual rhythm.
  - Overview shows schedule summary, next run, cadence, timezone, target, policy, and latest dispatch status when present.
  - Configuration shows schedule fields in read-only form until edit is chosen.
  - Activity is optional and can show audit events, reconciliation warnings, or Temporal describe metadata when available.
  - Steps and artifacts tabs are omitted by default because schedules are not workflow executions.
  - Detail-request loading and error states match the workflow detail posture; run-history failure is localized to the runs panel when schedule detail succeeds.
- Requirements:
  - Schedule detail consumes id/definitionId, name, description, enabled, cron, timezone, nextRunAt, lastScheduledFor, lastDispatchStatus, lastDispatchError, scope, target, policy, temporalScheduleId, and updatedAt where available.
  - Runtime config exposes detail, update, runNow, and runs endpoint templates under sources.schedules.
  - The page remains schedule-focused and does not become a separate product surface with a unique design system.
- Owned coverage:
  - `DESIGN-REQ-004`: Owns reuse of workflow detail composition and visual conventions.
  - `DESIGN-REQ-005`: Owns schedule-specific concept mapping, layout, tabs, facts, and summary cards.
  - `DESIGN-REQ-006`: Owns detail data consumption and runtime config endpoint availability except delete.
  - `DESIGN-REQ-011`: Owns schedule state and attention presentation in the overview shell.
  - `DESIGN-REQ-014`: Owns loading, partial error, disabled, and attention state presentation.
  - `DESIGN-REQ-016`: Owns omission of execution-specific schedule-page tabs and content.

### STORY-003: Edit recurring schedule configuration

- Short name: `schedule-editing`
- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 3. Requirements, 7. Data Contract, 8. Edit Behavior, 11. Empty, Loading, and Error States, 12. Authorization Rules, 13. Non-Goals
- Why: Recurring schedules should be maintainable as durable definitions; users should not create replacement schedules just to change cadence or policy.
- Independent test: With mocked detail and update endpoints, verify an authorized user can enter edit mode, invalid cron/timezone values produce immediate inline feedback, a valid PATCH stays on /schedules/{definitionId}, refetches data, and API validation or Temporal reconciliation failures leave the prior page state unchanged.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None
- Scope:
  - Expose an Edit schedule action on /schedules/{definitionId} for users with edit permission.
  - Support an inline configuration edit mode, side drawer, or small modal for editable schedule fields.
  - Validate cron and timezone client-side before save.
  - Submit PATCH /api/recurring-workflows/{definitionId} with changed fields when practical, keep the user on the detail route, refetch detail and run-history data, and show validation or reconciliation errors inline.
- Out of scope:
  - Backend support for fields not already supported by the update contract.
  - Using /workflows/new as an edit surface.
- Acceptance criteria:
  - Authorized users can choose Edit schedule from the schedule detail page.
  - Editable fields include schedule name, description, enabled or paused state, cron, timezone, overlap policy, catchup policy, jitter seconds, and supported target workflow parameters.
  - Invalid cron or timezone values are reported before submission.
  - Successful save PATCHes /api/recurring-workflows/{definitionId}, remains on /schedules/{definitionId}, refetches schedule detail, and refetches run history.
  - Validation and Temporal reconciliation errors render inline without navigating away or applying misleading local state.
  - Users without edit permission cannot perform edits; edit controls are hidden or disabled with a clear explanation.
- Requirements:
  - The detail page is the primary edit path for existing recurring schedules.
  - Save payloads contain only changed fields when practical.
  - The edit surface must not require returning to /workflows/new or creating a new schedule to change cadence or policy.
- Owned coverage:
  - `DESIGN-REQ-006`: Owns use of update endpoint and relevant runtime config template.
  - `DESIGN-REQ-007`: Owns detail-page editing behavior and editable field set.
  - `DESIGN-REQ-008`: Owns validation, PATCH save, refetch, and inline error behavior.
  - `DESIGN-REQ-014`: Owns update failure and Temporal reconciliation error state.
  - `DESIGN-REQ-015`: Owns edit-permission behavior.

### STORY-004: Review schedule run history

- Short name: `schedule-run-history`
- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 4. Route Model, 6. Default Detail Layout, 7. Data Contract, 10. Run History Relationship, 11. Empty, Loading, and Error States, 13. Non-Goals
- Why: The schedule page controls cadence and summarizes spawned runs while preserving workflow detail as the source for execution-specific evidence.
- Independent test: Mock a schedule with multiple spawned runs and verify the Runs tab loads the runs endpoint, renders required timing/status/link fields, navigates to normal workflow detail on click, shows localized errors for run-history failure, and does not render execution-only artifacts or steps on the schedule page.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None
- Scope:
  - Load schedule-owned runs from /api/recurring-workflows/{definitionId}/runs.
  - Render the Runs tab with workflowId, scheduled time, actual start time when available, status, and link to /workflows/{workflowId}?source=temporal.
  - Keep run-history failures localized so schedule controls remain available when detail loading succeeded.
  - Add or preserve compact workflow-detail provenance back to /schedules/{definitionId} when schedule metadata is available.
- Out of scope:
  - Rendering workflow execution steps, logs, artifacts, proposals, or diagnostics on the schedule page.
  - Deleting or mutating spawned workflow executions.
- Acceptance criteria:
  - The Runs tab lists recent and historical runs owned by the schedule.
  - Each run row includes spawned workflowId, scheduled time, actual start time when available, status, and a link to /workflows/{workflowId}?source=temporal.
  - Clicking a run opens normal workflow detail.
  - Workflow detail may show a compact Created by schedule link back to /schedules/{definitionId} when metadata is present.
  - If run-history loading fails after detail loading succeeds, controls and overview remain available while the Runs panel shows a localized error.
  - The schedule page does not duplicate execution-specific steps, artifacts, logs, proposals, or diagnostics.
- Requirements:
  - A recurring schedule is represented as a control-plane object that owns a series of workflow executions.
  - Workflow execution detail remains the place for execution-specific evidence.
- Owned coverage:
  - `DESIGN-REQ-006`: Owns use of the runs endpoint and runtime config template.
  - `DESIGN-REQ-009`: Owns schedule-run relationship and run row link behavior.
  - `DESIGN-REQ-010`: Owns optional workflow-detail provenance link behavior.
  - `DESIGN-REQ-014`: Owns localized run-history error state.
  - `DESIGN-REQ-016`: Owns non-duplication of execution evidence on schedule detail.

### STORY-005: Control schedule dispatch from detail

- Short name: `schedule-dispatch-controls`
- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 5. Page Composition, 6. Default Detail Layout, 7. Data Contract, 11. Empty, Loading, and Error States, 12. Authorization Rules
- Why: Schedule detail should be the control surface for operational schedule actions that affect future and immediate dispatch.
- Independent test: Mock authorized and unauthorized schedule details, then verify Run now calls the run endpoint, pause/resume state changes follow available update behavior, disabled schedules appear paused/disabled instead of failed, dispatch failures show attention without blocking authorized actions, and unauthorized controls are hidden or disabled.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None
- Scope:
  - Expose Run now and Pause/Resume schedule actions when authorized and backed by available contracts.
  - Call POST /api/recurring-workflows/{definitionId}/run for immediate manual runs.
  - Reflect enabled, paused, disabled, last dispatch status, and last dispatch error in status badges, summary cards, or attention states.
  - Allow edit, run now, and available destructive actions when authorized even if the last dispatch indicates failure.
- Out of scope:
  - Delete behavior, which is separately gated by backend availability.
  - Inventing new backend contracts for pause/resume if the current update contract owns enabled-state changes.
- Acceptance criteria:
  - Run now is available from the detail page for authorized users and calls POST /api/recurring-workflows/{definitionId}/run.
  - Pause/Resume controls reflect the current enabled or paused state and use the supported schedule update path.
  - Disabled schedules are presented as paused or disabled rather than failed.
  - lastDispatchStatus and lastDispatchError produce an attention state while leaving authorized edit, run now, and available destructive actions accessible.
  - Users without action permission cannot trigger run now or pause/resume; controls are hidden or disabled with a clear explanation.
- Requirements:
  - The page maps workflow actions to schedule actions: edit, pause/resume, run now, and delete only when supported.
  - Operational schedule controls use runtime config endpoints under sources.schedules.
- Owned coverage:
  - `DESIGN-REQ-006`: Owns use of runNow endpoint and update path for enabled-state changes.
  - `DESIGN-REQ-011`: Owns run-now, pause/resume, disabled, and dispatch-attention behavior.
  - `DESIGN-REQ-014`: Owns disabled and last-dispatch failure presentation.
  - `DESIGN-REQ-015`: Owns authorization behavior for dispatch controls.
- Assumptions:
  - Pause/resume can be represented through the existing update contract unless the backend exposes a more specific action contract before implementation.

### STORY-006: Gate and execute schedule deletion

- Short name: `schedule-delete`
- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 3. Requirements, 4. Route Model, 7. Data Contract, 9. Planned Delete Behavior, 11. Empty, Loading, and Error States, 12. Authorization Rules, 13. Non-Goals
- Why: Deletion is destructive and must be contract-gated so the UI does not promise behavior the backend cannot safely perform.
- Independent test: Run UI tests with runtime config both lacking and including sources.schedules.delete: verify delete is absent when unsupported, present only for authorized users when supported, confirmation text names the schedule and preserves historical runs, successful DELETE redirects to /schedules, and failed DELETE leaves the user on detail with an error.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None
- Scope:
  - Render Delete schedule only when DELETE /api/recurring-workflows/{definitionId} is implemented and sources.schedules.delete is available.
  - Place the destructive action in the detail page action area, visually separated from routine actions.
  - Require confirmation that names the schedule and explains that future recurring runs stop but prior workflow executions and artifacts remain.
  - On confirmation, call DELETE /api/recurring-workflows/{definitionId}; on success redirect to /schedules with confirmation; on failure stay on the detail page and show the error.
- Out of scope:
  - Implementing the backend delete route itself if it is not already available.
  - Deleting historical workflow executions or their artifacts.
- Acceptance criteria:
  - Delete schedule is not rendered when the backend delete route or sources.schedules.delete runtime config template is unavailable.
  - When available, Delete schedule appears as a visually separated destructive action for authorized users only.
  - Confirmation names the schedule and states that deleting stops future recurring runs but does not delete prior workflow executions or artifacts.
  - Confirmed deletion calls DELETE /api/recurring-workflows/{definitionId}.
  - Successful deletion redirects to /schedules and shows a success toast or banner.
  - Failed deletion keeps the user on /schedules/{definitionId} and shows the error.
- Requirements:
  - Backend deletion must delete or soft-delete the MoonMind definition and delete or pause the corresponding Temporal Schedule so no future runs are dispatched.
  - Historical workflow executions spawned by the schedule remain available under /workflows/{workflowId}.
- Owned coverage:
  - `DESIGN-REQ-012`: Owns backend/runtime-config gating for delete visibility.
  - `DESIGN-REQ-013`: Owns destructive confirmation, DELETE behavior, redirect/failure behavior, stopped future dispatch, and preservation of historical executions.
  - `DESIGN-REQ-015`: Owns delete authorization behavior.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-002
- `DESIGN-REQ-005` -> STORY-002
- `DESIGN-REQ-006` -> STORY-002, STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-007` -> STORY-003
- `DESIGN-REQ-008` -> STORY-003
- `DESIGN-REQ-009` -> STORY-004
- `DESIGN-REQ-010` -> STORY-004
- `DESIGN-REQ-011` -> STORY-002, STORY-005
- `DESIGN-REQ-012` -> STORY-006
- `DESIGN-REQ-013` -> STORY-006
- `DESIGN-REQ-014` -> STORY-001, STORY-002, STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-015` -> STORY-001, STORY-003, STORY-005, STORY-006
- `DESIGN-REQ-016` -> STORY-002, STORY-004

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: STORY-001, STORY-002
- `STORY-005` depends on: STORY-001, STORY-002
- `STORY-006` depends on: STORY-001, STORY-002

## Out Of Scope

- Creating or modifying spec.md files or specs/ directories during breakdown.
- Implementing backend delete support when the delete route is unavailable.
- Duplicating execution-specific steps, artifacts, logs, proposals, or diagnostics on the schedule detail page.
- Deleting historical workflow executions or their artifacts when deleting a schedule.
- Replacing the workflow-detail visual language with a separate schedule design system.

## Coverage Gate

PASS - every major design point is owned by at least one story.
