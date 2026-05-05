# Data Model: Desktop Columns and Compound Headers

No persistent data model changes.

## UI State

- `sortField`: one visible table field, default `scheduledFor`.
- `sortDir`: `asc` or `desc`, default `desc`.
- `temporalState`: canonical status filter value.
- `repository`: repository text filter value.
- `targetRuntime`: runtime identifier filter value such as `codex_cli`.
- `openFilter`: the currently open column filter popover, or `null`.
- `listCursor` and `cursorStack`: existing pagination state reset by filter changes.

## API Query Additions

- `targetRuntime`: optional query parameter for `GET /api/executions?source=temporal&scope=tasks`.
- Temporal visibility mapping: `targetRuntime` filters `mm_target_runtime`.

## State Transitions

- Sorting a column changes `sortField` and `sortDir` only.
- Opening a column filter changes `openFilter` only.
- Applying status, repository, or runtime filters resets pagination to the first page.
- Clearing filters clears all three filter values and resets pagination to the first page.
