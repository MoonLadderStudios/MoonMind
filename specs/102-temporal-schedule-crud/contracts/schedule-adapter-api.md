# Schedule Adapter API Contract

## Module: `moonmind.workflows.temporal.client.TemporalClientAdapter`

All methods accept `definition_id: UUID` and derive the Temporal Schedule ID via `make_schedule_id(definition_id)`.

### `create_schedule`

```python
async def create_schedule(
    self,
    *,
    definition_id: UUID,
    cron_expression: str,
    timezone: str,
    overlap_mode: str = "skip",
    catchup_mode: str = "last",
    jitter_seconds: int = 0,
    enabled: bool = True,
    note: str = "",
    workflow_type: str,
    workflow_input: Mapping[str, Any] | None = None,
    memo: Mapping[str, Any] | None = None,
    search_attributes: Mapping[str, Any] | None = None,
) -> str:
    """Create a Temporal Schedule. Returns the schedule ID."""
```

**Raises**: `ScheduleAlreadyExistsError` if schedule ID exists, `ScheduleOperationError` on SDK failure.

### `describe_schedule`

```python
async def describe_schedule(self, *, definition_id: UUID) -> ScheduleDescription:
    """Describe a Temporal Schedule (next runs, recent actions, state)."""
```

**Raises**: `ScheduleNotFoundError`, `ScheduleOperationError`.

### `update_schedule`

```python
async def update_schedule(
    self,
    *,
    definition_id: UUID,
    cron_expression: str | None = None,
    timezone: str | None = None,
    overlap_mode: str | None = None,
    catchup_mode: str | None = None,
    jitter_seconds: int | None = None,
    enabled: bool | None = None,
    note: str | None = None,
) -> None:
    """Update the spec, policy, or state of an existing schedule."""
```

**Raises**: `ScheduleNotFoundError`, `ScheduleOperationError`.

### `pause_schedule`

```python
async def pause_schedule(self, *, definition_id: UUID) -> None:
    """Pause a Temporal Schedule (no new runs until unpaused)."""
```

### `unpause_schedule`

```python
async def unpause_schedule(self, *, definition_id: UUID) -> None:
    """Unpause a Temporal Schedule."""
```

### `trigger_schedule`

```python
async def trigger_schedule(self, *, definition_id: UUID) -> None:
    """Trigger an immediate run of the schedule."""
```

### `delete_schedule`

```python
async def delete_schedule(self, *, definition_id: UUID) -> None:
    """Delete a Temporal Schedule."""
```

**Raises**: `ScheduleNotFoundError`, `ScheduleOperationError`.

---

## Module: `moonmind.workflows.temporal.schedule_mapping`

### Policy Mapping Functions

```python
def map_overlap_policy(mode: str) -> ScheduleOverlapPolicy
def map_catchup_window(mode: str) -> timedelta
def build_schedule_spec(cron: str, timezone: str, jitter_seconds: int) -> ScheduleSpec
def build_schedule_policy(overlap_mode: str, catchup_mode: str) -> SchedulePolicy
def build_schedule_state(enabled: bool, note: str) -> ScheduleState
def make_schedule_id(definition_id: UUID) -> str
def make_workflow_id_template(definition_id: UUID) -> str
```

---

## Module: `moonmind.workflows.temporal.schedule_errors`

```python
class ScheduleAdapterError(Exception): ...
class ScheduleNotFoundError(ScheduleAdapterError): ...
class ScheduleAlreadyExistsError(ScheduleAdapterError): ...
class ScheduleOperationError(ScheduleAdapterError): ...
```
