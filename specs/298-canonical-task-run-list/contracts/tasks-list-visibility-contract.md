# Tasks List Visibility Contract

## Route Contract

| Request | Expected Result |
| --- | --- |
| `GET /tasks/list` | Render Mission Control page key `tasks-list` with wide list layout and runtime dashboard config. |
| `GET /tasks` | Redirect to `/tasks/list`. |
| `GET /tasks/tasks-list` | Redirect to `/tasks/list`. |

## Ordinary List Contract

The ordinary `/tasks/list` page is task-run focused.

Required behavior:
- Loads list data through MoonMind-owned API paths.
- Requests user-visible task-run executions by default.
- Does not expose system workflow rows.
- Does not expose manifest-ingest rows in the ordinary table.
- Does not expose `Kind`, `Workflow Type`, or `Entry` columns or ordinary broad-workflow controls.

## Compatibility URL Contract

| Input URL Pattern | Required Behavior |
| --- | --- |
| `/tasks/list?scope=tasks` | Load the ordinary task-run view. |
| `/tasks/list?workflowType=MoonMind.Run` | Load the ordinary task-run view when paired with no entry or `entry=run`. |
| `/tasks/list?entry=run` | Load the ordinary task-run view. |
| `/tasks/list?scope=system` | Do not show system rows in the ordinary table; route authorized admins to diagnostics or show a recoverable message. |
| `/tasks/list?scope=all` | Do not show all workflow rows in the ordinary table; route authorized admins to diagnostics or show a recoverable message. |
| `/tasks/list?workflowType=<system value>` | Do not show system rows in the ordinary table; use diagnostics or a recoverable message. |
| `/tasks/list?workflowType=MoonMind.ManifestIngest` | Route to Manifests or show a recoverable message; do not add broad workflow columns to the task table. |
| `/tasks/list?entry=manifest` | Route to Manifests or show a recoverable message. |

## Security Contract

- URL params must not bypass authorization.
- Hidden or unauthorized workflow values must not appear as ordinary table rows, filter values, counts, or labels.
- URL state must not contain secrets.
- Labels render as text, not trusted HTML.

## Test Contract

Required coverage:
- Router unit tests for canonical and alias routes.
- Boot payload unit tests for page key, layout, and dashboard config.
- Tasks List UI tests for safe URL normalization, removed broad controls, and MoonMind-owned fetch paths.
- Execution list boundary tests proving ordinary task scope excludes system and manifest workflows.
- At least four broad compatibility URL cases.
