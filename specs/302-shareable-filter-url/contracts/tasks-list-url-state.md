# Contract: Tasks List URL State

## Legacy Inputs

- `state=<value>` loads as a Status include filter.
- `repo=<value>` loads as a Repository exact include filter.
- `scope=tasks`, `workflowType=MoonMind.Run`, and `entry=run` are task-safe and do not create visible filters.
- `scope=system`, `scope=all`, system workflow types, and `entry=manifest` do not widen the normal Tasks List page.

## Canonical List Filters

These params support comma-encoded values and repeated parameters:

- `stateIn`, `stateNotIn`
- `repoIn`, `repoNotIn`
- `targetRuntimeIn`, `targetRuntimeNotIn`
- `targetSkillIn`, `targetSkillNotIn`

For example, these are equivalent:

```text
targetRuntimeIn=codex_cli,claude_code
targetRuntimeIn=codex_cli&targetRuntimeIn=claude_code
```

## Validation Errors

The page and API reject non-empty include and exclude values for the same field:

```text
stateIn=completed&stateNotIn=canceled
targetRuntimeIn=codex_cli&targetRuntimeNotIn=jules
```

The API returns `422` with `code: invalid_execution_query`.

## Normalization

- Empty values are omitted from normalized URL/API state.
- Duplicate values are de-duplicated in first-seen order.
- Filter and page-size changes clear `nextPageToken`.
