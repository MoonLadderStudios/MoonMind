# Requirements Traceability

| Source Requirement | Functional Requirement | Implementation Surface | Validation Strategy |
| :--- | :--- | :--- | :--- |
| **DOC-REQ-001**: All execution operations use Temporal calls. | **FR-001**, **FR-003** | `ExecutionService` methods (`create_execution`, `cancel_execution`, etc.) | Unit tests mocking `TemporalClientAdapter` to ensure it is called instead of just writing to local DB. |
| **DOC-REQ-002**: Local DB only reflects Temporal, not source-of-truth. Listing shows only actual Temporal workflows. | **FR-002** | `ExecutionService.list_executions` and `describe_execution` | Integration tests verifying that list/detail operations return data sourced from Temporal history and state (with DB caching verified). |
| **DOC-REQ-003**: Signals/updates errors come from workflow validation, not stale DB checks. | **FR-004** | `ExecutionService.signal_execution` and action routers | Test that invalid states trigger exceptions from Temporal workflow handle rather than DB checks before dispatch. |
