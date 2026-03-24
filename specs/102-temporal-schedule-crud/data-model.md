# Data Model: Temporal Schedule CRUD

This feature does not introduce new database models. It operates at the Temporal adapter layer.

## Key Entities

### ScheduleOverlapPolicy (Temporal SDK enum, mapped from MoonMind vocabulary)

| MoonMind `overlap.mode` | Temporal `ScheduleOverlapPolicy` |
|---|---|
| `skip` | `SKIP` |
| `allow` | `ALLOW_ALL` |
| `buffer_one` | `BUFFER_ONE` |
| `cancel_previous` | `CANCEL_OTHER` |

### Catchup Window (derived from MoonMind vocabulary)

| MoonMind `catchup.mode` | `catchup_window` |
|---|---|
| `none` | `timedelta(0)` |
| `last` | `timedelta(minutes=15)` |
| `all` | `timedelta(days=365)` |

### Schedule Exception Types

| Exception | When Raised |
|---|---|
| `ScheduleAdapterError` | Base class for all schedule adapter errors |
| `ScheduleNotFoundError` | Schedule ID does not exist in Temporal |
| `ScheduleAlreadyExistsError` | Schedule ID already exists |
| `ScheduleOperationError` | Temporal SDK call failed (wraps underlying error) |

### ID Conventions

| Entity | Format | Example |
|---|---|---|
| Schedule ID | `mm-schedule:{definition_uuid}` | `mm-schedule:a1b2c3d4-...` |
| Spawned Workflow ID | `mm:{definition_uuid}:{epoch}` | `mm:a1b2c3d4-...:1711234567` |
