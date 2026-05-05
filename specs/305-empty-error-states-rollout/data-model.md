# Data Model: Empty/Error States and Regression Coverage for Final Rollout

This story does not add persistent entities, database tables, or stored workflow payloads.

## Runtime State Used by the UI

### ColumnFilters

Existing client-side query state representing active task-list filters.

Fields used by this story:
- `status`, `repository`, `targetRuntime`, `targetSkill`: value filters with include/exclude modes.
- `taskId`, `title`: text contains filters.
- `scheduledFor`, `createdAt`, `closedAt`: date filters with optional blank handling where supported.

Validation rules:
- Contradictory include and exclude filters for the same field are invalid.
- Active filters must remain editable or clearable when validation fails.
- Clearing filters resets pagination to the first page.

### ListErrorMessage

Derived display state for failed list requests.

Fields:
- `message`: sanitized operator-visible error message.

Validation rules:
- Prefer structured API `detail.message` when available.
- Accept simple string `detail` responses.
- Fall back to HTTP status text when structured detail is unavailable.
- Do not render raw secrets, headers, or stack traces.

### PaginationState

Existing client-side pagination state.

Fields:
- `nextPageToken`: opaque cursor for the current page request.
- `cursorStack`: previous-page cursor stack.
- `pageSize`: selected page size.

Validation rules:
- Empty later pages retain previous-page recovery when `cursorStack` or `nextPageToken` indicates pagination context.
- Filter changes clear stale cursor state.
