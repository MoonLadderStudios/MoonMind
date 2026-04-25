# Contract: Task Details Actions

The Task Details page consumes `execution.actions` from `/api/executions/{workflowId}`.

Relevant fields:

```ts
type ExecutionActions = {
  canUpdateInputs?: boolean;
  canEditForRerun?: boolean;
  canRerun?: boolean;
  disabledReasons?: Record<string, string>;
};
```

Rendering:

- `canUpdateInputs=true` renders **Edit task** to `/tasks/new?editExecutionId=:id`.
- `canEditForRerun=true` renders **Edit task** to `/tasks/new?rerunExecutionId=:id&mode=edit`.
- `canRerun=true` renders **Rerun** to `/tasks/new?rerunExecutionId=:id`.
