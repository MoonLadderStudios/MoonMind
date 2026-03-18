# Requirements Traceability: Workflow Scheduling

**Feature**: 086-workflow-scheduling
**Date**: 2026-03-18

| DOC-REQ | FR | Implementation Surface | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001 | `temporal_models.py` + `agent_queue_models.py`: add `schedule` field | Unit test: `CreateExecutionRequest` accepts schedule field |
| DOC-REQ-002 | FR-004 | `executions.py`: compute `start_delay` from `scheduledFor` | Unit test: deferred create sets `start_delay` on `start_workflow()` |
| DOC-REQ-003 | FR-005, FR-011 | `models.py`: `SCHEDULED` state; `executions.py`: set state on create | Unit test: deferred create sets `mm_state=scheduled` |
| DOC-REQ-004 | FR-019 | `executions.py` + `client.py`: cancel action works on scheduled workflows | Unit test: cancel on scheduled workflow succeeds |
| DOC-REQ-005 | FR-006 | `executions.py`: delegate recurring to `RecurringTasksService` | Unit test: recurring create delegates correctly |
| DOC-REQ-006 | FR-006 | `executions.py`: construct target from request body | Unit test: target payload built from task request |
| DOC-REQ-007 | FR-009 | `executions.py`: response shape for mode=once | Unit test: response has scheduledFor and state=scheduled |
| DOC-REQ-008 | FR-010 | `executions.py`: response shape for mode=recurring | Unit test: response has definitionId and nextRunAt |
| DOC-REQ-009 | FR-012, FR-013 | `dashboard.js`: schedule panel with radio options | Manual verification + browser test |
| DOC-REQ-010 | FR-015 | `dashboard.js`: date/time/timezone picker for deferred | Manual verification + browser test |
| DOC-REQ-011 | FR-016 | `dashboard.js`: cron/name/timezone inputs for recurring | Manual verification + browser test |
| DOC-REQ-012 | FR-014 | `dashboard.js`: dynamic submit button label | Manual verification + browser test |
| DOC-REQ-013 | FR-017 | `dashboard.js`: redirect per mode | Manual verification + browser test |
| DOC-REQ-014 | FR-002 | `client.py`: `start_delay` parameter on `start_workflow()` | Unit test: `start_workflow()` passes `start_delay` to SDK |
| DOC-REQ-015 | FR-003 | `models.py` + Alembic migration: `scheduled_for` column | Unit test: record stores scheduled_for |
| DOC-REQ-016 | FR-020 | `task_dashboard_view_model.py`: `submitScheduleEnabled` flag | Unit test: feature flag present in runtime config |
| DOC-REQ-017 | FR-018 | `dashboard.js`: scheduled banner on detail page | Manual verification + browser test |
| DOC-REQ-018 | FR-007, FR-008 | `executions.py`: validation of scheduledFor and cron | Unit test: past scheduledFor rejected, invalid cron rejected |
