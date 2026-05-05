# Data Model: Task-only Visibility and Diagnostics Boundary

## Existing Concepts

### Tasks List Query State

- `state`: optional canonical task lifecycle filter preserved from old URLs.
- `repo`: optional repository filter preserved from old URLs.
- `limit`: page size.
- `nextPageToken`: pagination cursor.
- `sort` / `sortDir`: client-visible sort state.

Validation rules:
- Workflow-kind state such as `scope`, `workflowType`, and `entry` is not part of the normal Tasks List query state.
- Old `scope`, `workflowType`, and `entry` parameters are read only to detect unsupported legacy state, then omitted from emitted URL state.
- URL state must not include secrets.

### Execution List Boundary

- Effective normal list scope: task runs only.
- Task-compatible legacy values: `workflowType=MoonMind.Run` and `entry=run`, which normalize to the same effective task-run boundary.
- Unsupported broad values: `scope=user`, `scope=system`, `scope=all`, non-run `workflowType`, and non-run `entry` values.

Validation rules:
- Unsupported broad values fail safe to task-run query semantics for source-temporal list requests.
- Owner scoping for ordinary users remains enforced.
- Invalid unknown scope values still produce validation errors instead of silent broadening.

## State Transitions

- Initial page load reads URL state.
- Task-compatible filters initialize Status and Repository controls.
- Unsupported workflow-scope state sets a recoverable notice and is dropped from synchronized URL state.
- User filter changes reset pagination and keep URL state task-oriented.
