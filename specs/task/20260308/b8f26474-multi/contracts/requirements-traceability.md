# Requirements Traceability

| DOC-REQ ID | Implementation Surface | Validation Strategy |
| ----------- | ---------------------- | ------------------- |
| **DOC-REQ-001** | `api_service/api/routers/executions.py` (`list_executions`, `describe_execution`), `TemporalExecutionService` adapter | Write automated tests querying `/tasks/list?source=temporal` and verifying payload matches mock Temporal SDK history/visibility responses. |
| **DOC-REQ-002** | ID canonicalization logic in `api_service/api/routers/executions.py` and `TemporalExecutionService` | Write tests asserting that IDs with `mm:` prefixes are successfully routed to their actual Temporal Workflow IDs. |
| **DOC-REQ-003** | Query builder in `TemporalExecutionService.list_executions` | Write tests verifying that query parameters accurately translate into Temporal's Search Attribute list filters. |
| **FR-001**      | `api_service/api/routers/executions.py` | Unit tests for API routing with `source=temporal`. |
| **FR-002**      | `TemporalExecutionService` | Assert `rawState`, `closeStatus`, and `waitingReason` are populated from Temporal. |
| **FR-003**      | `api_service/api/routers/executions.py` | Covered by DOC-REQ-002 validations. |
| **FR-004**      | `TemporalExecutionService` | Covered by DOC-REQ-003 validations. |
| **FR-005**      | Source codebase + test files | Confirmed via coverage tools in CI. |
