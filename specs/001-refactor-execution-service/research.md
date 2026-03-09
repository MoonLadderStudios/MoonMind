# Research: Refactor Execution Service to Temporal Authority

## Temporal Client Integration
- **Decision**: Use `TemporalClientAdapter` and `temporalio` SDK for all workflow operations (start, cancel, signal, update, list, detail) within the `ExecutionService`.
- **Rationale**: `TemporalClientAdapter` already exists and is configured with the correct namespace and connection parameters. Using it ensures consistency and avoids duplicating client logic.
- **Alternatives considered**: Directly instantiating `temporalio.client.Client` in `ExecutionService`. Rejected because it bypasses existing topology and connection caching.

## State Synchronization Strategy
- **Decision**: Treat local DB (`TemporalExecutionRecord`) strictly as a projection/cache. On list/detail requests, optionally trigger an upsert using Temporal's visibility or describe APIs if the record is stale or missing.
- **Rationale**: Meets the requirement that Temporal is the authoritative source of truth (DOC-REQ-002) while maintaining high read performance for the UI.
- **Alternatives considered**: Removing the local DB completely. Rejected because the migration plan specifies maintaining the DB as a projection cache for performance and fallback purposes.

## Action Routing
- **Decision**: Route UI actions (pause, resume, cancel) directly to `workflow_handle.signal()` or `workflow_handle.cancel()`.
- **Rationale**: Matches DOC-REQ-001 and DOC-REQ-003. Workflow validation will handle errors, preventing stale local state from blocking actions.
- **Alternatives considered**: Validate state in the DB before signaling. Rejected because it violates DOC-REQ-003.
