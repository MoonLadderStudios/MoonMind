# Contract: Create Page Shell

## Server Route Contract

`GET /tasks/new`

Expected behavior:
- Requires the same dashboard authentication as other Mission Control task pages.
- Returns the Mission Control React shell.
- Boot payload `page` is `task-create`.
- Boot payload `initialData.dashboardConfig` is built for current path `/tasks/new`.

`GET /tasks/create`

Expected behavior:
- Returns a redirect to `/tasks/new`.
- Does not render a separate Create page shell.

## Browser Shell Contract

The Create page form exposes canonical section metadata using `data-canonical-create-section`.

Create mode section order:
1. Header
2. Steps
3. Task Presets
4. Dependencies
5. Execution context
6. Execution controls
7. Schedule
8. Submit

Edit and rerun modes:
- Use the same task composition page entrypoint.
- May omit creation-only schedule controls.
- Must preserve Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, and Submit ordering.

## REST Boundary Contract

Browser actions use MoonMind REST endpoints from the boot payload or defaults:
- Task creation: configured temporal create endpoint, default `/api/executions`.
- Task update/rerun: configured temporal update endpoint.
- Artifact operations: configured temporal artifact endpoints.
- Jira browsing: configured `/api/jira/...` endpoints when Jira integration is enabled.
- Provider profile selection: configured `/api/v1/provider-profiles`.
- Task presets: configured `/api/task-step-templates...` endpoints.

The browser must not call Jira, object storage, or model provider URLs directly as part of this page shell story.

## Optional Integration Contract

When optional integrations are unavailable:
- Missing Jira configuration hides Jira browsing without disabling manual instructions.
- Missing or disabled attachment policy hides image upload without disabling manual instructions.
- Missing task preset catalog hides or disables preset controls without disabling manual steps and task submission.
