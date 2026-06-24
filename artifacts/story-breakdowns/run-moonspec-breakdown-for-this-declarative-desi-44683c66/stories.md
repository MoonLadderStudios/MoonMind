# Recurring Schedule Details Page - Story Breakdown

- Source design: `docs/UI/RecurringScheduleDetailsPage.md`
- Source document class: `canonical-declarative`
- Story extraction date: `2026-06-24T18:18:39Z`
- Output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines a Mission Control recurring schedule detail page as a schedule-flavored derivative of the workflow detail page. It establishes stable /schedules/{definitionId} routing, workflow-derived composition, schedule data and runtime-config contracts, edit and action behavior, planned delete semantics, run-history links to spawned workflow executions, explicit empty/error states, authorization rules, and non-goals that prevent duplicating execution-specific surfaces.

## Coverage Points

- `DESIGN-REQ-001` **Recurring schedule rows navigate to details** (requirement, 3. Requirements; 4. Route Model): Each schedule row at /schedules exposes the primary schedule name as a link to /schedules/{definitionId}, and clicking it opens that schedule detail page.
- `DESIGN-REQ-002` **Definition ID is the stable route identity** (state-model, 4. Route Model): definitionId is the stable MoonMind product identity for a recurring schedule and remains stable across edits and spawned workflow executions.
- `DESIGN-REQ-003` **Creation redirects to the new schedule detail page** (requirement, 4. Route Model): New recurring schedule creation sends the user to /schedules/{definitionId}.
- `DESIGN-REQ-004` **Detail page reuses workflow detail composition** (constraint, 1. Purpose; 5. Page Composition): The schedule detail page should be a schedule-flavored derivative of workflow detail, sharing shell, spacing, panels, facts rail, tabs, states, actions, badges, and confirmation patterns where practical.
- `DESIGN-REQ-005` **Schedule-specific content replaces execution concepts** (requirement, 5. Page Composition; 6. Default Detail Layout): Workflow title/state/runtime/timing/actions map to schedule name/state/facts/next-run metadata/actions, while execution-only steps and artifacts tabs are omitted by default.
- `DESIGN-REQ-006` **Default detail layout exposes summary, tabs, and facts rail** (requirement, 6. Default Detail Layout): The page includes breadcrumbs, title, status, action area, summary cards, overview, runs, configuration, optional activity, and a facts rail with schedule and target identifiers.
- `DESIGN-REQ-007` **Detail and run-history data contracts are honored** (integration, 7. Data Contract): The page loads and presents the required schedule fields, target/policy metadata, Temporal identifiers, freshness fields, and run history from existing recurring-workflow endpoints.
- `DESIGN-REQ-008` **Runtime config exposes schedule endpoint templates** (integration, 7. Data Contract): Runtime config sources.schedules provides list/create/detail/update/runNow/runs templates, while delete is added only once the backend DELETE route exists.
- `DESIGN-REQ-009` **Schedule editing is available from detail** (requirement, 3. Requirements; 8. Edit Behavior): Users can edit an existing recurring schedule from /schedules/{definitionId} without returning to workflow creation.
- `DESIGN-REQ-010` **Edit saves changed schedule fields with validation and refetch** (requirement, 8. Edit Behavior): The edit surface validates cron/timezone, PATCHes changed fields when practical, keeps the user on the detail page, refetches detail and runs, and presents errors inline.
- `DESIGN-REQ-011` **Run now, pause, resume, and delete are schedule actions** (requirement, 5. Page Composition; 7. Data Contract; 9. Planned Delete Behavior): Schedule actions include edit, pause/resume, run now, and delete only after the delete backend contract and runtime config template are available.
- `DESIGN-REQ-012` **Delete stops future dispatch without deleting history** (constraint, 3. Requirements; 9. Planned Delete Behavior; 13. Non-Goals): Deleting a schedule removes or disables future dispatch, redirects to /schedules on success, and does not delete already spawned workflow executions or artifacts.
- `DESIGN-REQ-013` **Run history remains separate from workflow execution detail** (state-model, 10. Run History Relationship; 13. Non-Goals): A recurring schedule owns a series of executions; its detail page reviews spawned runs while execution-specific steps, artifacts, logs, proposals, and diagnostics remain on workflow detail pages.
- `DESIGN-REQ-014` **Run rows link to spawned workflow details** (requirement, 4. Route Model; 10. Run History Relationship): Each schedule run row includes workflowId and timing/status data and links to /workflows/{workflowId}?source=temporal.
- `DESIGN-REQ-015` **Workflow details may link back to source schedule** (integration, 10. Run History Relationship): Workflow details may show compact Created by schedule provenance linking back to /schedules/{definitionId} when metadata is available.
- `DESIGN-REQ-016` **Loading, not-found, partial failure, and attention states are explicit** (requirement, 5. Page Composition; 11. Empty, Loading, and Error States): The page reuses detail loading/error conventions, shows not-found with a schedules link, keeps controls available when run history fails, handles reconciliation errors, shows disabled as paused/disabled, and shows dispatch failures as attention states.
- `DESIGN-REQ-017` **Authorization mirrors schedule ownership rules** (security, 12. Authorization Rules): Personal schedules are owner-accessible, global schedules require operator privileges, unauthorized users see normal unauthorized or not-found states, and edit/delete controls reflect edit permission.
- `DESIGN-REQ-018` **Schedule detail must avoid separate product language and duplicated execution surfaces** (non-goal, 1. Purpose; 5. Page Composition; 13. Non-Goals): The page should not create a distinct design system and must not duplicate execution steps, artifacts, logs, proposals, or require a new schedule to change cadence or policy.

## Ordered Story Candidates

### STORY-001: Route recurring schedules to a stable detail page

- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 3. Requirements, 4. Route Model
- Short name: `schedule-detail-route`
- Description: As an operator, I can open a recurring schedule from the schedule list or creation flow and land on a stable detail URL keyed by definitionId so I can manage that schedule directly.
- Independent test: Create or fixture a recurring schedule, verify its list row links to /schedules/{definitionId}, verify the route opens that schedule, and verify creation success redirects to the same route key.
- Dependencies: None
- Needs clarification: None
- Acceptance criteria:
  - Given a schedule appears in /schedules, its primary name is a navigable link to /schedules/{definitionId}.
  - When the link is opened, the recurring schedule detail page loads data for that exact definitionId.
  - When a new recurring schedule is created successfully, the UI redirects to /schedules/{definitionId}.
  - The detail route continues to use the same definitionId after schedule edits and across spawned runs.
- Owned coverage:
  - `DESIGN-REQ-001`: Owns list-row navigation to the detail route.
  - `DESIGN-REQ-002`: Owns definitionId route identity and stability expectations.
  - `DESIGN-REQ-003`: Owns create-success redirect behavior.
- Out of scope:
  - Implementing edit, run history, or destructive actions on the detail page.
  - Changing the stable identity model away from definitionId.

### STORY-002: Render a workflow-derived recurring schedule detail shell

- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 1. Purpose, 5. Page Composition, 6. Default Detail Layout, 13. Non-Goals
- Short name: `schedule-detail-shell`
- Description: As an operator, I see a recurring schedule detail page that feels like workflow detail while clearly representing a schedule definition instead of a single execution.
- Independent test: Render a schedule detail fixture and assert the header, status, summary cards, facts rail, Overview/Runs/Configuration tabs, and lack of default Steps/Artifacts tabs match the desired workflow-derived composition.
- Dependencies: STORY-001
- Needs clarification: None
- Acceptance criteria:
  - The detail page uses workflow detail page shell, spacing, panel rhythm, status badge style, loading/error posture, and action placement where applicable.
  - The page labels schedule-specific data as schedule name, schedule state, schedule definition ID, Temporal Schedule ID, target facts, next run, last run, dispatch result, and updated time.
  - Overview, Runs, and Configuration are available as the default detail areas, and Activity appears only when supported by available data.
  - Steps and Artifacts tabs are not rendered by default for schedules.
  - No unique schedule-product visual language is introduced for the page.
- Owned coverage:
  - `DESIGN-REQ-004`: Owns reuse of workflow detail composition and page conventions.
  - `DESIGN-REQ-005`: Owns schedule-specific replacements for workflow execution concepts.
  - `DESIGN-REQ-006`: Owns default layout, tabs, summary cards, and facts rail.
  - `DESIGN-REQ-018`: Owns non-goals around visual language and duplicated execution surfaces.
- Out of scope:
  - Creating a separate schedule-only design system.
  - Duplicating execution logs, artifacts, proposals, diagnostics, or step details on the schedule page.

### STORY-003: Load schedule detail and run-history contracts

- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 7. Data Contract, 11. Empty, Loading, and Error States
- Short name: `schedule-data-contract`
- Description: As an operator, I see current schedule configuration, timing, target, policy, attention, and run-history data loaded from the recurring workflow API and runtime config templates.
- Independent test: Mock runtime config and API responses for detail, runs, not-found, runs failure, disabled schedule, and dispatch error cases, then verify rendered data and state behavior.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None
- Acceptance criteria:
  - The page loads detail data from GET /api/recurring-workflows/{definitionId}.
  - The page loads run history from GET /api/recurring-workflows/{definitionId}/runs using the configured runs template.
  - All documented schedule fields are either displayed in the main content, summary cards, facts rail, or editable configuration view, or intentionally omitted only when absent from the response.
  - Runtime config exposes list/create/detail/update/runNow/runs templates under sources.schedules.
  - sources.schedules.delete is not exposed until DELETE /api/recurring-workflows/{definitionId} exists.
  - A missing schedule shows a not-found state with a link back to /schedules.
  - A run-history failure leaves schedule controls available and shows an error only in the runs panel.
  - A disabled schedule is presented as paused/disabled, and dispatch failures are presented as attention states while still allowing authorized actions.
- Owned coverage:
  - `DESIGN-REQ-007`: Owns schedule detail field and endpoint consumption.
  - `DESIGN-REQ-008`: Owns runtime config endpoint templates and delete-template gating.
  - `DESIGN-REQ-016`: Owns loading, not-found, partial failure, disabled, reconciliation, and attention states.
- Out of scope:
  - Adding sources.schedules.delete before the backend DELETE contract exists.
  - Inventing new backend persistence fields beyond the documented page data contract.

### STORY-004: Edit recurring schedule configuration from detail

- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 8. Edit Behavior, 13. Non-Goals
- Short name: `schedule-editing`
- Description: As an authorized operator, I can edit an existing recurring schedule from its detail page, validate schedule fields, save changes, and remain on the detail page with refreshed data.
- Independent test: Render an authorized schedule detail, enter edit mode, change editable fields, verify client validation, verify PATCH payload and refetch behavior, and verify validation or reconciliation failures remain inline on the detail page.
- Dependencies: STORY-003
- Needs clarification: None
- Assumptions: Target workflow parameter editing is limited to fields the backend update contract accepts.
- Acceptance criteria:
  - An authorized user can open Edit schedule from the detail page.
  - The edit surface supports name, description, enabled/paused state, cron, timezone, overlap policy, catchup policy, jitter seconds, and backend-supported target workflow parameters.
  - Invalid cron or timezone values produce immediate client-side feedback before save.
  - Saving calls PATCH /api/recurring-workflows/{definitionId}, using only changed fields when practical.
  - After successful save, the user remains on /schedules/{definitionId} and detail plus run-history data are refetched.
  - Validation and Temporal reconciliation errors are shown inline and leave page state unchanged.
- Owned coverage:
  - `DESIGN-REQ-009`: Owns availability of schedule editing on the detail page.
  - `DESIGN-REQ-010`: Owns validation, PATCH, refetch, stay-on-page, and inline error behavior.
  - `DESIGN-REQ-018`: Owns the non-goal that cadence or policy changes should not require creating a new schedule.
- Out of scope:
  - Requiring users to create a new schedule to change cadence or policy.
  - Redirecting users to /workflows/new for recurring schedule edits.
  - Implementing target field editing when unsupported by the backend contract.

### STORY-005: Control schedule actions and planned deletion

- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 5. Page Composition, 7. Data Contract, 9. Planned Delete Behavior
- Short name: `schedule-actions-delete`
- Description: As an authorized operator, I can use schedule-specific actions safely, including run now and pause/resume now, and delete only when the backend delete contract is available.
- Independent test: Mock action permissions and runtime config with and without delete support, verify action visibility, run-now call behavior, delete confirmation copy, DELETE call, success redirect, and failure handling.
- Dependencies: STORY-003, STORY-004
- Needs clarification: None
- Assumptions: Pause/resume uses the existing update or action contract available in the recurring workflow API surface.
- Acceptance criteria:
  - Schedule actions include edit, run now, pause/resume, and delete only when backend delete support is available.
  - Run now calls POST /api/recurring-workflows/{definitionId}/run through the configured runNow template.
  - Delete is hidden when sources.schedules.delete is absent or backend delete support is not available.
  - Delete confirmation names the schedule and states that future recurring runs stop while prior workflow executions and artifacts remain.
  - Successful delete removes or disables future dispatch through the backend contract, redirects to /schedules, and shows a success toast or banner.
  - Failed delete keeps the user on the detail page and shows the error.
- Owned coverage:
  - `DESIGN-REQ-011`: Owns run now, pause/resume, and delete action availability and delete gating.
  - `DESIGN-REQ-012`: Owns future-dispatch stop semantics, redirect, confirmation, and history preservation.
- Out of scope:
  - Rendering delete before the backend route and runtime config template are available.
  - Deleting historical workflow executions or their artifacts.

### STORY-006: Link schedule run history to workflow executions

- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 4. Route Model, 10. Run History Relationship, 13. Non-Goals
- Short name: `schedule-run-history`
- Description: As an operator, I can review runs spawned by a recurring schedule and open the normal workflow detail page for execution-specific evidence.
- Independent test: Fixture a schedule with spawned run history, verify run rows include workflow identifiers, timing and status, verify links navigate to workflow detail, and verify available provenance metadata creates a backlink from workflow detail to the schedule.
- Dependencies: STORY-003
- Needs clarification: None
- Acceptance criteria:
  - The Runs tab shows recent and historical runs owned by the schedule.
  - Each run row includes workflowId, scheduled time, actual start time when available, status, and a link to /workflows/{workflowId}?source=temporal.
  - Clicking a run opens the normal workflow detail page for that workflow execution.
  - Workflow detail can show a compact Created by schedule link back to /schedules/{definitionId} when metadata is available.
  - Schedule detail does not duplicate execution-specific steps, artifacts, logs, proposals, or diagnostics.
- Owned coverage:
  - `DESIGN-REQ-013`: Owns the model that schedules own runs but are not executions.
  - `DESIGN-REQ-014`: Owns run-row fields and links to workflow detail.
  - `DESIGN-REQ-015`: Owns optional workflow-detail provenance backlink.
  - `DESIGN-REQ-018`: Owns non-duplication of execution-specific workflow surfaces.
- Out of scope:
  - Displaying workflow execution steps, artifacts, logs, proposals, or diagnostics in the schedule detail page.
  - Treating the schedule definition itself as a workflow execution.

### STORY-007: Enforce schedule detail authorization affordances

- Source reference: `docs/UI/RecurringScheduleDetailsPage.md`; sections: 12. Authorization Rules, 11. Empty, Loading, and Error States
- Short name: `schedule-authorization`
- Description: As MoonMind, I enforce recurring schedule ownership and operator access rules on the detail page and only expose edit or delete controls when the user is allowed to use them.
- Independent test: Exercise personal owner, global operator, unauthorized, read-only, and no-edit-permission fixtures, then assert page visibility and action affordances match documented ownership rules.
- Dependencies: STORY-003
- Needs clarification: None
- Acceptance criteria:
  - Personal schedules can be viewed, edited, and deleted by their owner, subject to delete contract availability.
  - Global schedules require operator privileges for detail access, including read-only GET requests.
  - Users without detail access see the normal unauthorized or not-found state, not a read-only global schedule detail page.
  - Users with detail access but without edit permission can view the page but edit/delete actions are hidden or disabled with a clear explanation.
  - Attention, disabled, and partial-error states do not override authorization checks for action visibility.
- Owned coverage:
  - `DESIGN-REQ-016`: Owns interaction between error/attention states and available controls.
  - `DESIGN-REQ-017`: Owns schedule detail authorization and permission-gated actions.
- Out of scope:
  - Changing backend authorization policy for recurring schedules outside the detail-page contracts.
  - Showing read-only global schedules to users who lack detail access.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-002
- `DESIGN-REQ-005` -> STORY-002
- `DESIGN-REQ-006` -> STORY-002
- `DESIGN-REQ-007` -> STORY-003
- `DESIGN-REQ-008` -> STORY-003
- `DESIGN-REQ-009` -> STORY-004
- `DESIGN-REQ-010` -> STORY-004
- `DESIGN-REQ-011` -> STORY-005
- `DESIGN-REQ-012` -> STORY-005
- `DESIGN-REQ-013` -> STORY-006
- `DESIGN-REQ-014` -> STORY-006
- `DESIGN-REQ-015` -> STORY-006
- `DESIGN-REQ-016` -> STORY-003, STORY-007
- `DESIGN-REQ-017` -> STORY-007
- `DESIGN-REQ-018` -> STORY-002, STORY-004, STORY-006

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-003, STORY-004
- `STORY-006` depends on: STORY-003
- `STORY-007` depends on: STORY-003

## Out Of Scope Items And Rationale

- Creating a separate schedule-only design language is out of scope because the canonical design requires workflow-detail reuse.
- Duplicating workflow execution steps, artifacts, logs, proposals, or diagnostics on the schedule page is out of scope because those belong to spawned workflow detail pages.
- Rendering delete before the backend DELETE route and runtime config template exist is out of scope because the design gates the destructive action on contract availability.
- Deleting historical workflow executions or artifacts is out of scope because deleting a schedule only stops future recurring dispatch.
- Creating a new schedule to change cadence or policy is out of scope because the detail page must support editing an existing schedule.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
