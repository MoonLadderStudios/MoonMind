# Scheduler Migration Plan

This document outlines the steps to drop all remaining references to the old scheduler in favor of the new Temporal scheduling system.

## 1. Remove `moonmind-scheduler` Entrypoint
- Remove `moonmind-scheduler` script entry from `pyproject.toml`.
- Remove `scheduler` service from `docker-compose.yaml`.

## 2. Remove Old Scheduler Code
- Delete `moonmind/workflows/recurring_tasks/scheduler.py` (which contains `RecurringTaskScheduler` and the `main()` function for the `moonmind-scheduler` CLI).
- Remove scheduler-specific runtime configuration from `moonmind/config/settings.py` (e.g., `scheduler_poll_interval_ms`, `scheduler_batch_size`, `scheduler_max_backfill`, `scheduler_lock_timeout_seconds`).

## 3. Deprecate `RecurringTasksService` Polling Logic
- In `api_service/services/recurring_tasks_service.py`, remove the methods explicitly used by the polling loop:
  - `run_scheduler_tick`
  - `schedule_due_definitions`
  - `dispatch_pending_runs`
  - Any associated locking logic and helper data models like `RecurringDispatchResult`.

## 4. Transition `RecurringTaskDefinition` API Endpoints
- The core API (`api_service/api/routers/recurring_tasks.py`) handles CRUD operations for `RecurringTaskDefinition`.
- The `RecurringTaskDefinition` model must be migrated to be backed directly by Temporal Schedules, or its underlying engine logic must delegate strictly to `TemporalExecutionService`/Temporal Schedules instead of the local DB.
- Any manual run trigger (`/api/recurring-tasks/{definitionId}/run`) should be translated to a Temporal workflow execution start.

## 5. Documentation Updates
- Update `docs/MoonMindArchitecture.md` to remove references to `scheduler` and `moonmind-scheduler`.
- Retire or archive `docs/TaskRecurringSchedulesSystem.md` if it exclusively describes the old system, or replace it with a reference to the Temporal implementation.
