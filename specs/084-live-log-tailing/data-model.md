# Data Model: Live Log Tailing

**Branch**: `084-live-log-tailing` | **Date**: 2026-03-17

## Existing Entities (No Changes)

### `task_run_live_sessions`

This table already contains all fields needed for live log tailing:

| Column | Type | Role in Log Tailing |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `workflow_id` / `task_run_id` | String | Links to owning execution |
| `status` | Enum | Drives panel state rendering (DISABLED, STARTING, READY, ENDED, ERROR, REVOKED) |
| `web_ro` | Text (nullable) | URL embedded in the iframe for the Live Output panel |
| `created_at` | Timestamp | Session creation time |
| `ready_at` | Timestamp | When RO endpoint became available |
| `ended_at` | Timestamp | When session was torn down |
| `error_message` | Text (nullable) | Displayed in error state |

### State Transitions Relevant to UI

```
DISABLED → (no panel available)
STARTING → (loading indicator)
READY    → (iframe with web_ro URL)
ENDED    → ("Session ended")
ERROR    → ("Live output is not available")
REVOKED  → ("Session ended")
```

## New Entities

None. No new tables, columns, or data structures are introduced.
