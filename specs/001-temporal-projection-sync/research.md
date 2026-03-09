# Research: Temporal Projection Sync

## 1. Mapping Temporal State to Local DB
- **Decision**: Use `client.get_workflow_handle()` to fetch execution status and search attributes, then map to `TemporalExecutionRecord`.
- **Rationale**: Temporal visibility provides search attributes (like `mm_state`, `mm_entry`) which directly correspond to local DB fields. `describe_workflow` provides execution status.
- **Alternatives considered**: Relying purely on list visibility queries (rejected because it may lack detail or be eventually consistent) versus direct workflow description.

## 2. Preventing Duplicate DB Rows
- **Decision**: Use SQLAlchemy `upsert` (e.g. `INSERT ... ON CONFLICT DO UPDATE`) based on the unique `workflow_id` constraint.
- **Rationale**: Ensures concurrent API reads do not result in `IntegrityError` or duplicated records.
- **Alternatives considered**: Explicit locking or read-then-write (rejected due to race condition risks in a concurrent API).