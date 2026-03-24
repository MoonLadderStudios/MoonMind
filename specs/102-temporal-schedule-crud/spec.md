# Feature Specification: Temporal Schedule CRUD in TemporalClientAdapter

**Feature Branch**: `102-temporal-schedule-crud`
**Created**: 2026-03-23
**Status**: Draft
**Input**: User description: "Implement Phase 1 of docs/tmp/TemporalSchedulingPlan.md"

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
|---|---|---|
| DOC-REQ-001 | TemporalScheduling.md §5.1 — Mechanism: Temporal Schedules | System MUST create Temporal Schedule objects via the Temporal Python SDK `client.create_schedule()` |
| DOC-REQ-002 | TemporalScheduling.md §5.4 — Reconciliation Model | System MUST support describing, updating, pausing, unpausing, triggering, and deleting Temporal Schedules |
| DOC-REQ-003 | TemporalScheduling.md §5.5 — Overlap Policy Mapping | System MUST map MoonMind overlap policy modes (`skip`, `allow`, `buffer_one`, `cancel_previous`) to corresponding `ScheduleOverlapPolicy` values |
| DOC-REQ-004 | TemporalScheduling.md §5.6 — Catchup / Backfill Policy | System MUST map MoonMind catchup policy modes (`none`, `last`, `all`) to appropriate `catchup_window` durations |
| DOC-REQ-005 | TemporalScheduling.md §5.8 — Schedule ID Convention | System MUST generate Temporal Schedule IDs following the convention `mm-schedule:{definition_uuid}` |
| DOC-REQ-006 | TemporalSchedulingPlan.md Phase 1 — Jitter mapping | System MUST map MoonMind `jitterSeconds` to `ScheduleSpec.jitter` timedelta |
| DOC-REQ-007 | TemporalSchedulingPlan.md Phase 1 — Adapter error handling | System MUST raise adapter-level exceptions (not raw Temporal SDK errors) from schedule methods |

## User Scenarios & Testing

### User Story 1 — Create a Temporal Schedule from Backend (Priority: P1)

A backend service calls `TemporalClientAdapter.create_schedule()` with a cron expression, timezone, overlap policy, and workflow configuration. The adapter creates a Temporal Schedule object that will automatically start workflows on the specified cadence.

**Why this priority**: This is the foundational capability — without schedule creation, no other schedule operations are possible.

**Independent Test**: Can be tested by calling `create_schedule()` with a mock Temporal client and verifying the correct `Schedule` object is constructed with the right `ScheduleSpec`, `SchedulePolicy`, `ScheduleState`, and `ScheduleActionStartWorkflow`.

**Acceptance Scenarios**:

1. **Given** a valid cron expression, timezone, and definition ID, **When** `create_schedule()` is called, **Then** a Temporal Schedule is created with `id=mm-schedule:{definition_id}`, the cron expression in `ScheduleSpec`, and the specified overlap policy.
2. **Given** an overlap mode of `skip`, **When** `create_schedule()` is called, **Then** `ScheduleOverlapPolicy.SKIP` is set on the schedule policy.
3. **Given** a catchup mode of `all`, **When** `create_schedule()` is called, **Then** `catchup_window` is set to a large duration (365 days).
4. **Given** `jitterSeconds=30`, **When** `create_schedule()` is called, **Then** `ScheduleSpec.jitter` is set to `timedelta(seconds=30)`.
5. **Given** `enabled=false`, **When** `create_schedule()` is called, **Then** `ScheduleState.paused` is set to `True`.

---

### User Story 2 — Manage Schedule Lifecycle (Priority: P1)

A backend service uses the adapter to pause, unpause, trigger, update, and delete existing Temporal Schedules by their definition ID.

**Why this priority**: Schedule lifecycle management is essential for the product to offer enable/disable, manual run, and schedule editing.

**Independent Test**: Each method can be tested with a mock `ScheduleHandle`, verifying the correct SDK calls are made.

**Acceptance Scenarios**:

1. **Given** an existing schedule, **When** `pause_schedule()` is called, **Then** `handle.pause()` is invoked on the Temporal SDK.
2. **Given** a paused schedule, **When** `unpause_schedule()` is called, **Then** `handle.unpause()` is invoked.
3. **Given** an existing schedule, **When** `trigger_schedule()` is called, **Then** `handle.trigger()` is invoked, causing an immediate workflow start.
4. **Given** an existing schedule, **When** `update_schedule()` is called with a new cron expression, **Then** `handle.update()` is invoked with the new `ScheduleSpec`.
5. **Given** an existing schedule, **When** `delete_schedule()` is called, **Then** `handle.delete()` is invoked.
6. **Given** an existing schedule, **When** `describe_schedule()` is called, **Then** the schedule description including `next_action_times` and `recent_actions` is returned.

---

### User Story 3 — Adapter Error Handling (Priority: P2)

When Temporal SDK calls fail (e.g., Temporal server unavailable, schedule not found), the adapter raises MoonMind-specific exceptions rather than leaking raw SDK errors to callers.

**Why this priority**: Clean error boundaries are required for the reconciliation layer (Phase 2) to handle failures gracefully.

**Independent Test**: Can be tested by configuring the mock Temporal client to raise SDK exceptions and verifying the adapter wraps them in MoonMind-level error types.

**Acceptance Scenarios**:

1. **Given** the Temporal server is unavailable, **When** any schedule method is called, **Then** the adapter raises an adapter-level exception with a descriptive message, not a raw `temporalio` exception.
2. **Given** a schedule that does not exist, **When** `describe_schedule()` is called, **Then** the adapter raises a "schedule not found" exception.

---

### Edge Cases

- What happens when `create_schedule()` is called with a schedule ID that already exists? The adapter should handle the conflict gracefully (raise a descriptive error or return the existing schedule).
- What happens when the cron expression is valid for MoonMind but unsupported by Temporal? The adapter should fail fast with a validation error.
- What happens when `update_schedule()` is called on a deleted schedule? The adapter should raise a "schedule not found" exception.
- How does the adapter handle timezone values that Temporal doesn't recognize? The adapter should validate timezones before passing to Temporal.

## Requirements

### Functional Requirements

- **FR-001**: System MUST expose `create_schedule()` on `TemporalClientAdapter` that creates a Temporal Schedule via the Python SDK. (DOC-REQ-001)
- **FR-002**: System MUST expose `describe_schedule()` that returns schedule state, recent actions, and next run times. (DOC-REQ-002)
- **FR-003**: System MUST expose `update_schedule()` that modifies the spec, policy, or action of an existing schedule. (DOC-REQ-002)
- **FR-004**: System MUST expose `pause_schedule()` and `unpause_schedule()` methods. (DOC-REQ-002)
- **FR-005**: System MUST expose `trigger_schedule()` to fire an immediate workflow start for the schedule. (DOC-REQ-002)
- **FR-006**: System MUST expose `delete_schedule()` to remove a Temporal Schedule. (DOC-REQ-002)
- **FR-007**: System MUST map `overlap.mode` values to `ScheduleOverlapPolicy` enum values: `skip`→`SKIP`, `allow`→`ALLOW_ALL`, `buffer_one`→`BUFFER_ONE`, `cancel_previous`→`CANCEL_OTHER`. (DOC-REQ-003)
- **FR-008**: System MUST map `catchup.mode` values to `catchup_window` durations: `none`→`0`, `last`→`15 minutes`, `all`→`365 days`. (DOC-REQ-004)
- **FR-009**: System MUST generate Schedule IDs using the format `mm-schedule:{definition_uuid}`. (DOC-REQ-005)
- **FR-010**: System MUST generate deterministic workflow IDs for schedule-spawned workflows. (DOC-REQ-005)
- **FR-011**: System MUST map `jitterSeconds` to `ScheduleSpec.jitter` as a `timedelta`. (DOC-REQ-006)
- **FR-012**: System MUST wrap Temporal SDK exceptions in adapter-level exception types. (DOC-REQ-007)

### Key Entities

- **TemporalClientAdapter**: The existing adapter class that wraps Temporal SDK calls. Extended with schedule CRUD methods.
- **Schedule Policy Mapping**: A mapping layer that translates MoonMind policy vocabulary to Temporal `SchedulePolicy` and `ScheduleOverlapPolicy` objects.

## Success Criteria

### Measurable Outcomes

- **SC-001**: All seven schedule lifecycle methods (`create`, `describe`, `update`, `pause`, `unpause`, `trigger`, `delete`) are callable from the adapter and pass unit tests with mocked Temporal client.
- **SC-002**: All four overlap policy values are correctly mapped with dedicated unit tests per mapping.
- **SC-003**: All three catchup policy values are correctly mapped with dedicated unit tests per mapping.
- **SC-004**: Schedule and workflow ID generation follows the documented conventions and is validated by unit tests.
- **SC-005**: No raw Temporal SDK exceptions leak past the adapter boundary — all error paths produce adapter-level exceptions.
