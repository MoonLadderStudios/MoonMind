# Research: Workflow Scheduling

**Feature**: 086-workflow-scheduling
**Date**: 2026-03-18

## Temporal `start_delay` Support

**Decision**: Use Temporal SDK `start_delay` parameter for deferred one-time execution.

**Rationale**: The `temporalio` Python SDK supports `start_delay: timedelta` on `Client.start_workflow()`. When set, Temporal creates the workflow execution immediately (visible in Visibility) but delays dispatch to the task queue until the delay elapses. This gives us:
- Immediate visibility in Mission Control
- Cancellation before start
- No intermediate scheduling infrastructure

**Alternatives considered**:
- **Temporal Schedules**: More complex, designed for recurring—overkill for one-time deferred.
- **RecurringTaskDefinition with single-fire cron**: Works but adds unnecessary overhead and a definition record that must be cleaned up.
- **In-workflow `workflow.sleep()`**: Starts the workflow immediately, wasting a worker slot during the wait.

**Code reference**: `temporalio.client.Client.start_workflow()` accepts `start_delay: Optional[timedelta] = None`.

## MoonMindWorkflowState Enum Extension

**Decision**: Add `SCHEDULED = "scheduled"` to `MoonMindWorkflowState`.

**Rationale**: The workflow is created but not yet dispatched. The `scheduled` state is distinct from `initializing` (which means the workflow is already running its first activity). Dashboard status maps `scheduled → queued` (consistent with the existing "waiting to run" semantics).

**Alternatives considered**:
- Reusing `initializing` would be confusing because the workflow hasn't actually started.
- Adding to the Temporal search attribute `mm_state` ensures Visibility queries can filter scheduled workflows.

## Database Schema: `scheduled_for` Column

**Decision**: Add `scheduled_for: DateTime | None` to `TemporalExecutionRecord`.

**Rationale**: The column stores the user-provided target execution time. It is nullable (null for immediate executions). Used by:
- API response serialization (`scheduledFor` field)
- Dashboard detail page banner ("Scheduled to run at {time}")
- Future: list filtering by scheduled time

**Migration**: Standard Alembic `alter_table add_column` with `nullable=True`, no data migration needed.

## Recurring Schedule Target Construction

**Decision**: Reuse existing `RecurringTasksService.create_definition()` for `schedule.mode=recurring`.

**Rationale**: The recurring tasks service already handles cron validation, timezone normalization, policy application, and run scheduling. The inline schedule flow just needs to construct the `target` payload from the create request body before delegating. No changes to `RecurringTasksService` are needed.

**Target mapping**:
- `CreateJobRequest` (task-shaped) → `target.kind=queue_task` with `target.job.type=task` and `target.job.payload` from request
- `CreateExecutionRequest` (Temporal-shaped) → `target.kind=queue_task` with payload reconstructed from `initialParameters`

## Feature Flag Pattern

**Decision**: Add `submitScheduleEnabled: bool` to `featureFlags.temporalDashboard` in `build_runtime_config()`.

**Rationale**: Follows the existing pattern used by `list_enabled`, `detail_enabled`, `actions_enabled`, and `submit_enabled`. The dashboard reads this flag and conditionally renders the schedule panel.

**Code reference**: `api_service/api/routers/task_dashboard_view_model.py` → `build_runtime_config()`.
