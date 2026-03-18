# Data Model: Workflow Scheduling

**Feature**: 086-workflow-scheduling
**Date**: 2026-03-18

## Modified Entities

### TemporalExecutionRecord

| Field | Type | Change | Description |
| --- | --- | --- | --- |
| `scheduled_for` | `DateTime(timezone=True), nullable=True` | **ADD** | UTC timestamp of deferred start time. Null for immediate executions. |

**State transitions** (new):
- `scheduled` → `initializing` (when `start_delay` elapses and workflow begins)
- `scheduled` → `canceled` (when user cancels before start)

### MoonMindWorkflowState (enum)

| Value | Change | Dashboard Status |
| --- | --- | --- |
| `SCHEDULED = "scheduled"` | **ADD** | `queued` |

## New Models (Pydantic)

### ScheduleParameters

```python
class ScheduleParameters(BaseModel):
    mode: Literal["once", "recurring"]

    # mode=once fields
    scheduled_for: Optional[datetime] = Field(None, alias="scheduledFor")

    # mode=recurring fields
    name: Optional[str] = None
    description: Optional[str] = None
    cron: Optional[str] = None
    timezone: Optional[str] = None
    enabled: bool = True
    scope_type: Optional[str] = Field(None, alias="scopeType")
    policy: Optional[dict[str, Any]] = None
```

### CreateExecutionRequest (modified)

```python
schedule: Optional[ScheduleParameters] = Field(None, alias="schedule")
```

### CreateJobRequest (modified)

```python
schedule: Optional[ScheduleParameters] = Field(None, alias="schedule")
```

### ScheduleCreatedResponse (new)

```python
class ScheduleCreatedResponse(BaseModel):
    scheduled: Literal[True] = True
    definition_id: str = Field(..., alias="definitionId")
    name: str
    cron: str
    timezone: str
    next_run_at: datetime = Field(..., alias="nextRunAt")
    redirect_path: str = Field(..., alias="redirectPath")
```

## Unchanged Entities

- `RecurringTaskDefinition` — reused as-is for `mode=recurring`
- `RecurringTaskRun` — reused as-is
- `RecurringTasksService` — no modifications
