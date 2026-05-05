# Data Model: Tasks List Canonical Route and Shell

This story introduces no new persistent entities, database tables, migrations, or durable workflow payloads.

## Existing Runtime Objects

### Dashboard Boot Payload

- **Purpose**: Carries the Mission Control page key, API base, initial data, dashboard configuration, and layout metadata from the server-rendered shell to the React entrypoint.
- **Relevant fields**:
  - `page`: must be `tasks-list` for `/tasks/list`.
  - `apiBase`: base path for MoonMind API calls.
  - `initialData.dashboardConfig`: server-generated dashboard runtime configuration.
  - `initialData.layout.dataWidePanel`: wide data-panel layout flag for table-heavy pages.

### Tasks List Shell State

- **Purpose**: Frontend state for live updates, polling copy, disabled notice, page size, pagination cursor stack, filters, and sorted rows.
- **Persistence**: Browser runtime state and URL state only; no new backend storage.
- **Validation rules**:
  - Data requests use the boot payload API base.
  - Disabled list configuration keeps the shell recoverable.
  - Pagination state remains bounded by page size and cursor tokens returned by MoonMind APIs.

## State Transitions

- Request `/tasks` -> HTTP redirect to `/tasks/list`.
- Request `/tasks/tasks-list` -> HTTP redirect to `/tasks/list`.
- Request `/tasks/list` -> authenticated FastAPI route renders the Mission Control React shell.
- React shell enabled -> fetches execution list through MoonMind API route.
- React shell disabled -> renders disabled notice and avoids execution-list fetches.
