# Research

## Decision: Read Execution State Authoritatively from Temporal
**Rationale**: To prevent the local database projection from being out of sync with the true source of truth, API queries to `/tasks/list?source=temporal` and `/tasks/{id}` must read the execution state directly from Temporal.
**Alternatives considered**: Purely relying on local DB sync (rejected due to synchronization delays and the requirement to strictly fetch from Temporal when `source=temporal` is provided).

## Decision: Handling `mm:` Prefixes
**Rationale**: The `mm:` prefix must map consistently to Temporal workflow IDs, likely by stripping it or canonicalizing it before querying Temporal, and restoring it on the return paths to ensure UI parity.
**Alternatives considered**: Storing the `mm:` prefix directly in Temporal Workflow IDs (might conflict with existing workflows).

## Decision: Filtering with Search Attributes
**Rationale**: Temporal allows robust filtering using Custom Search Attributes. We can map API query params like `workflowType`, `entry`, and `state` directly into Temporal List API queries using a SQL-like syntax over Search Attributes.
**Alternatives considered**: Loading all workflows and filtering in Python (rejected due to extreme inefficiency on large datasets).
