# Contract: Tasks List Canonical Route and Shell

## Server Routes

### `GET /tasks/list`

- Requires the existing dashboard authentication dependency.
- Returns the shared Mission Control HTML shell.
- Boot payload requirements:
  - `page` is `tasks-list`.
  - `initialData.dashboardConfig` is generated for `/tasks/list`.
  - `initialData.layout.dataWidePanel` is `true`.
- Must not expose raw provider, Temporal, Jira, GitHub, object-storage, or credential details to the browser.

### `GET /tasks`

- Requires the existing dashboard authentication dependency.
- Returns an HTTP redirect to `/tasks/list`.

### `GET /tasks/tasks-list`

- Requires the existing dashboard authentication dependency.
- Returns an HTTP redirect to `/tasks/list`.

## Browser Data Loading

- The Tasks List React entrypoint uses the boot payload `apiBase`.
- Execution-list data loads through MoonMind API routes, currently `/api/executions`.
- Browser code must not call Temporal, GitHub, Jira, object storage, or runtime providers directly.

## Shell Composition

- The rendered page has exactly one Tasks List control deck.
- The rendered page has exactly one Tasks List data slab using the wide data-panel layout.
- Live updates controls, polling copy, disabled notice, page-size controls, and pagination controls remain available according to runtime configuration and result state.
