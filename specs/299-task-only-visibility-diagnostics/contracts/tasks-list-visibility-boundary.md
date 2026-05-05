# Contract: Tasks List Visibility Boundary

## UI Contract

The normal `/tasks/list` page exposes task-list controls only:

- Status filter
- Repository filter
- Live updates toggle
- Page size and pagination controls
- Task table columns: ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, Finished

The normal `/tasks/list` page does not expose:

- Scope control
- Workflow Type control
- Entry control
- Kind column
- Workflow Type column
- Entry column

Legacy workflow-scope URL parameters are handled as follows:

| Parameter | Handling |
| --- | --- |
| `scope=tasks` | Normalize away because task scope is default. |
| `scope=user`, `scope=system`, `scope=all` | Ignore for the normal page, show recoverable notice, emit task-oriented URL state. |
| `workflowType=MoonMind.Run` | Normalize away because task runs are default. |
| `workflowType=MoonMind.ManifestIngest` or system workflow types | Ignore for the normal page, show recoverable notice, emit task-oriented URL state. |
| `entry=run` | Normalize away because run entries are default. |
| `entry=manifest` | Ignore for the normal page, show recoverable notice, emit task-oriented URL state. |
| `state=<value>` | Preserve as a Status filter. |
| `repo=<value>` | Preserve as a Repository filter. |

## API Boundary Contract

For source-temporal list requests used by the normal Tasks List, effective query semantics are task-run bounded:

```text
WorkflowType="MoonMind.Run" AND mm_entry="run" AND ordinary owner scoping
```

Broadening parameters must not widen ordinary list results:

- `scope=all`, `scope=user`, and `scope=system` resolve to task-run query semantics.
- `workflowType` values other than `MoonMind.Run` are ignored for the normal source-temporal list boundary.
- `entry` values other than `run` are ignored for the normal source-temporal list boundary.
- Unknown `scope` values remain validation errors.

## Security Contract

- Ordinary users cannot list system, manifest-ingest, or all workflow rows by editing `/tasks/list` URL parameters.
- Owner scoping remains enforced.
- Rendered labels and filter values are text content, not trusted HTML.
