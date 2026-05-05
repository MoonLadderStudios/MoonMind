# Data Model: Canonical Task Run List Route

## Task Run List View

Represents the ordinary Mission Control `/tasks/list` surface.

Fields:
- `route`: canonical route, always `/tasks/list`.
- `pageKey`: rendered page identity, expected `tasks-list`.
- `layout`: wide data-panel list layout.
- `queryIntent`: ordinary task-run list.
- `allowedRows`: user-visible task-run executions.

Validation rules:
- The ordinary view must not represent itself as a generic workflow browser.
- The ordinary view must not expose broad workflow columns or controls.
- Browser data loading must use MoonMind-owned surfaces.

## Compatibility URL

Represents a legacy or manually edited URL opened on `/tasks/list`.

Fields:
- `scope`: optional legacy scope value.
- `workflowType`: optional workflow type value.
- `entry`: optional entry value.
- `state`: optional status value.
- `repo`: optional repository value.
- `limit`: optional page size.

Validation rules:
- Task-safe values preserve ordinary task-list meaning.
- Broad values such as `scope=system`, `scope=all`, system workflow types, and `entry=manifest` must not widen ordinary list visibility.
- Manifest-oriented values route to the manifest surface or produce a recoverable explanation.
- Broad workflow values route authorized administrators to diagnostics when supported, or produce a recoverable explanation.
- Filter changes reset cursor state.

## Diagnostics Surface

Represents the separate administrator workflow browsing surface.

Fields:
- `access`: permission-gated.
- `purpose`: platform debugging and broad workflow visibility.
- `visibleWorkflowMetadata`: workflow type, entry, owner, namespace, run ID, raw status, and system filters when authorized.

Validation rules:
- Ordinary operators cannot reach diagnostics behavior through `/tasks/list` query edits.
- Diagnostics access must be explicit and separate from the normal task table.

## Visible Task Run

Represents one row authorized for the ordinary task list.

Fields:
- `taskId`
- `title`
- `status`
- `runtime`
- `skill`
- `repository`
- `scheduledFor`
- `createdAt`
- `closedAt`

Validation rules:
- Rows must be ordinary task-run executions.
- System workflow rows and manifest-ingest rows are excluded from the ordinary table.
- Labels render as text and must not include trusted HTML.

## State Transitions

Compatibility URL handling:

```text
legacy URL opened
  -> classify params as task-safe, manifest-oriented, or broad-workflow
  -> task-safe params load ordinary task-run list
  -> manifest-oriented params route/message outside ordinary task table
  -> broad-workflow params route authorized admin to diagnostics or show recoverable message
  -> ordinary task-run visibility remains bounded throughout
```
