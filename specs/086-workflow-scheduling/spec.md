# Feature Specification: Workflow Scheduling

**Feature Branch**: `086-workflow-scheduling`
**Created**: 2026-03-18
**Status**: Draft
**Input**: User description: "Fully implement docs/Temporal/WorkflowSchedulingGuide.md"

## Source Document Requirements

Requirements extracted from `docs/Temporal/WorkflowSchedulingGuide.md` (Sections 4.4, 4.5, and related API/UI contracts).

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | §4.4 – `schedule` Object Schema | The create execution endpoint MUST accept an optional `schedule` object with `mode` field (`once` or `recurring`) alongside the existing payload |
| DOC-REQ-002 | §4.4 – mode=once | When `schedule.mode=once`, the API MUST defer workflow execution to the specified `scheduledFor` timestamp using Temporal `start_delay` |
| DOC-REQ-003 | §4.4 – mode=once Visibility | A deferred workflow MUST be immediately visible in Mission Control with a `scheduled` state before its start time |
| DOC-REQ-004 | §4.4 – mode=once Cancellation | A deferred workflow MUST be cancellable before its start time via the existing cancel action |
| DOC-REQ-005 | §4.4 – mode=recurring | When `schedule.mode=recurring`, the API MUST delegate to `RecurringTasksService.create_definition()` using the provided cron, timezone, and policy fields |
| DOC-REQ-006 | §4.4 – mode=recurring Target | The API MUST construct a recurring task target payload from the task-shaped or execution-shaped create request body |
| DOC-REQ-007 | §4.4 – Response: once | For `schedule.mode=once`, the response MUST include `workflowId`, `state=scheduled`, `scheduledFor`, and a `redirectPath` |
| DOC-REQ-008 | §4.4 – Response: recurring | For `schedule.mode=recurring`, the response MUST include `definitionId`, `name`, `cron`, `nextRunAt`, and a `redirectPath` to the schedule detail page |
| DOC-REQ-009 | §4.5 – Schedule Panel | The Mission Control submit form at `/tasks/new` MUST include a schedule panel with radio options: "Run immediately", "Schedule for later", "Set up recurring schedule" |
| DOC-REQ-010 | §4.5 – Deferred One-Time Fields | When "Schedule for later" is selected, the UI MUST show date picker, time picker, and timezone selector |
| DOC-REQ-011 | §4.5 – Recurring Fields | When "Set up recurring schedule" is selected, the UI MUST show schedule name, cron expression input, cron preview, and timezone picker |
| DOC-REQ-012 | §4.5 – Submit Button Label | The submit button label MUST change dynamically: "Submit" for immediate, "Schedule" for deferred, "Create Schedule" for recurring |
| DOC-REQ-013 | §4.5 – Redirect per Mode | After submission, the dashboard MUST redirect to the task detail page for immediate/deferred, or schedule detail page for recurring |
| DOC-REQ-014 | §4.4 – start_delay Backend | `TemporalClientAdapter.start_workflow()` MUST accept a `start_delay` parameter and pass it to the Temporal SDK |
| DOC-REQ-015 | §4.4 – TemporalExecutionRecord | `TemporalExecutionRecord` MUST gain a `scheduled_for` field to persist the deferred start time |
| DOC-REQ-016 | §4.5 – Feature Flag | The schedule panel MUST be gated behind a `submitScheduleEnabled` feature flag; when disabled the panel is hidden |
| DOC-REQ-017 | §4.5 – Scheduled Banner | The task detail page MUST show a "Scheduled to run at {time}" banner for deferred executions that have not yet started |
| DOC-REQ-018 | §4.4 – Validation | The API MUST validate that `scheduledFor` is a future UTC timestamp for `mode=once` and that the cron expression is valid for `mode=recurring` |

## User Scenarios & Testing

### User Story 1 — Schedule a One-Time Deferred Task (Priority: P1)

An operator wants to schedule a code deployment task to run at 2 AM when traffic is low. They fill in the task instructions, select "Schedule for later", pick the target date/time, and submit. The task appears in their task list immediately with a "Scheduled" badge. At 2 AM, the task starts automatically.

**Why this priority**: One-time deferred execution is the most requested scheduling feature and requires the deepest backend change (Temporal `start_delay` integration).

**Independent Test**: Can be tested by creating a task with `schedule.mode=once` via API and verifying (a) the workflow is created with `start_delay`, (b) it appears in the list with `scheduled` state, (c) it can be cancelled before start, (d) it transitions to `initializing` after the delay.

**Acceptance Scenarios**:

1. **Given** the user is on `/tasks/new` and `submitScheduleEnabled` is `true`, **When** the user selects "Schedule for later" and picks a future date/time, **Then** a date/time/timezone picker is shown and the submit button shows "Schedule".
2. **Given** the user submits with `schedule.mode=once` and a valid future `scheduledFor`, **When** the backend processes the request, **Then** a Temporal workflow is started with `start_delay` and state `scheduled`, and the response includes `scheduledFor`.
3. **Given** a deferred task exists that has not yet started, **When** the user views its detail page, **Then** a banner shows "Scheduled to run at {time}" with a Cancel button.
4. **Given** a deferred task exists, **When** the user cancels it, **Then** the Temporal workflow is cancelled and the task transitions to `cancelled` state.

---

### User Story 2 — Create a Recurring Schedule from Submit Form (Priority: P2)

A developer wants to run a daily code review at 9 AM on weekdays. On the `/tasks/new` form, they type their instructions, select "Set up recurring schedule", enter `0 9 * * 1-5`, and submit. A schedule definition is created and they are redirected to its detail page showing the next run time.

**Why this priority**: Unified scheduling from the submit form is a major UX improvement over navigating separately to a dedicated schedule creation page, but can leverage existing `RecurringTasksService` with lighter backend changes.

**Independent Test**: Can be tested by submitting a task with `schedule.mode=recurring` via API and verifying (a) a `RecurringTaskDefinition` is created, (b) the cron and timezone are correct, (c) the response includes `definitionId` and `nextRunAt`.

**Acceptance Scenarios**:

1. **Given** the user is on `/tasks/new` with `submitScheduleEnabled` enabled, **When** they select "Set up recurring schedule", **Then** the UI shows schedule name (auto-filled from title), cron input, cron preview, and timezone picker.
2. **Given** the user submits with a valid cron expression, **When** the backend processes the request, **Then** a `RecurringTaskDefinition` is created via `RecurringTasksService` and the response includes `definitionId`, `nextRunAt`, and `redirectPath`.
3. **Given** an invalid cron expression is submitted, **When** the backend validates, **Then** a 422 error is returned with a descriptive message.

---

### User Story 3 — Backend: Schedule Object on Create Endpoint (Priority: P1)

The existing `POST /api/executions` and `POST /api/queue/jobs` endpoints accept an optional `schedule` object that routes to either deferred or recurring creation.

**Why this priority**: This is the backend foundation that both UI stories depend on.

**Independent Test**: Can be tested with curl/httpie directly against the API without any UI changes.

**Acceptance Scenarios**:

1. **Given** a `POST /api/executions` request without a `schedule` field, **When** processed, **Then** the workflow starts immediately (existing behavior, no regression).
2. **Given** a `POST /api/executions` request with `schedule.mode=once` and a valid future `scheduledFor`, **When** processed, **Then** a Temporal workflow is started with `start_delay` computed as `scheduledFor - now`.
3. **Given** a `POST /api/executions` request with `schedule.mode=once` and a past `scheduledFor`, **When** processed, **Then** a 422 error is returned.
4. **Given** a `POST /api/executions` request with `schedule.mode=recurring` and a valid `cron`, **When** processed, **Then** a `RecurringTaskDefinition` is created and the response shape matches the recurring variant.
5. **Given** a `POST /api/queue/jobs` request with `schedule.mode=once`, **When** processed, **Then** the same deferred behavior applies (the unified create path handles both request shapes).

---

### Edge Cases

- What happens when the `scheduledFor` time is less than 1 second in the future? The API should accept it but Temporal may start the workflow nearly immediately.
- What happens when the `scheduledFor` time is very far in the future (e.g., 1 year)? The API should accept it — Temporal supports long `start_delay` values.
- What happens when the recurring schedule `cron` would never fire (e.g., February 30)? The cron parser should reject it with a validation error.
- What happens when `submitScheduleEnabled` is `false` and a `schedule` field is sent via API? The backend should still accept it — feature flags only gate the UI, not the API.
- What happens when the user changes the schedule mode radio after partially filling fields? The UI should clear mode-specific fields and show the new mode's fields.

## Requirements

### Functional Requirements

- **FR-001**: The `CreateExecutionRequest` and `CreateJobRequest` Pydantic models MUST accept an optional `schedule` field (DOC-REQ-001)
- **FR-002**: `TemporalClientAdapter.start_workflow()` MUST accept an optional `start_delay: timedelta` parameter and pass it through to the Temporal SDK (DOC-REQ-014)
- **FR-003**: `TemporalExecutionRecord` MUST have a `scheduled_for: datetime | None` column to persist the deferred start time (DOC-REQ-015)
- **FR-004**: When `schedule.mode=once`, the API MUST compute `start_delay = scheduledFor - now` and pass it to `start_workflow()` (DOC-REQ-002)
- **FR-005**: When `schedule.mode=once`, the execution record MUST be created with `mm_state=scheduled` and the `scheduled_for` value persisted (DOC-REQ-003)
- **FR-006**: When `schedule.mode=recurring`, the API MUST construct a target payload from the create request and delegate to `RecurringTasksService.create_definition()` (DOC-REQ-005, DOC-REQ-006)
- **FR-007**: The API MUST validate `scheduledFor` as a valid future UTC timestamp when `mode=once` (DOC-REQ-018)
- **FR-008**: The API MUST validate the cron expression when `mode=recurring` using the existing `parse_cron_expression()` (DOC-REQ-018)
- **FR-009**: The response for `mode=once` MUST include `state=scheduled`, `scheduledFor`, and `redirectPath` fields (DOC-REQ-007)
- **FR-010**: The response for `mode=recurring` MUST be a distinct shape with `definitionId`, `name`, `cron`, `nextRunAt`, and `redirectPath` (DOC-REQ-008)
- **FR-011**: `MoonMindWorkflowState` MUST include a `SCHEDULED` state with dashboard status mapping (DOC-REQ-003)
- **FR-012**: The dashboard submit form MUST include a schedule panel gated by `submitScheduleEnabled` feature flag (DOC-REQ-009, DOC-REQ-016)
- **FR-013**: The schedule panel MUST provide "Run immediately", "Schedule for later", and "Set up recurring schedule" radio options (DOC-REQ-009)
- **FR-014**: The schedule panel MUST dynamically change the submit button label per mode (DOC-REQ-012)
- **FR-015**: The "Schedule for later" mode MUST show date picker, time picker, and timezone selector (DOC-REQ-010)
- **FR-016**: The "Set up recurring schedule" mode MUST show schedule name, cron input with live preview, and timezone picker (DOC-REQ-011)
- **FR-017**: After submission the dashboard MUST redirect appropriately per mode (DOC-REQ-013)
- **FR-018**: The task detail page MUST show a "Scheduled" status badge and countdown banner for deferred executions not yet started (DOC-REQ-017)
- **FR-019**: Deferred executions MUST be cancellable before their start time (DOC-REQ-004)
- **FR-020**: `build_runtime_config()` MUST include `submitScheduleEnabled` in the `featureFlags.temporalDashboard` block (DOC-REQ-016)

### Key Entities

- **ScheduleParameters**: The `schedule` object on the create request — mode, scheduledFor (once), cron/timezone/name/policy (recurring)
- **TemporalExecutionRecord**: Extended with `scheduled_for` field for deferred execution tracking
- **RecurringTaskDefinition**: Existing entity reused for `mode=recurring` — no changes needed
- **MoonMindWorkflowState**: Enum extended with `SCHEDULED` value

## Success Criteria

### Measurable Outcomes

- **SC-001**: Users can schedule a one-time deferred task from the submit form and it executes within 5 seconds of the specified time
- **SC-002**: Users can create a recurring schedule from the submit form in under 30 seconds
- **SC-003**: All existing submit flows continue to work identically when the `schedule` field is absent (zero regression)
- **SC-004**: Deferred tasks appear in the task list immediately with "Scheduled" status and are cancellable
- **SC-005**: The schedule panel is fully hidden when `submitScheduleEnabled` is `false`
- **SC-006**: 100% of DOC-REQ-* requirements have passing unit or integration tests

## Assumptions

- The Temporal Python SDK version in use supports the `start_delay` parameter on `start_workflow()` (available since `temporalio >= 1.5.0`).
- The existing `RecurringTasksService` infrastructure is stable and can handle additional load from inline schedule creation.
- No database migration conflicts will arise from adding the `scheduled_for` column to `TemporalExecutionRecord`.
- The `submitScheduleEnabled` feature flag follows the existing pattern in `build_runtime_config()`.
