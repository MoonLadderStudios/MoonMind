# Contract: Tasks List Column Filters

## UI Contract

The Tasks List desktop table renders one compound header per visible column.

Each header exposes:

- a sort button whose accessible name includes the column label and current sort state;
- a filter button whose accessible name includes the column label and active/inactive filter state;
- a popover when the filter button or active filter chip is activated.

Status, Repository, and Runtime popovers expose editable controls in this story. Other visible columns may expose a disabled placeholder until their advanced filters are implemented, but they must still keep sort and filter targets separate.

## API Contract

`GET /api/executions` accepts:

```text
source=temporal
scope=tasks
state=<canonical-state>
repo=<repository>
targetRuntime=<runtime-id>
```

When `source=temporal`, `targetRuntime` maps to the Temporal visibility search attribute `mm_target_runtime` and remains combined with the task-scope query. Unsupported workflow scope, workflow type, and entry parameters remain ignored or normalized by the normal Tasks List path.
