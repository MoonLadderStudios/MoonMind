# Requirements Traceability

| DOC-REQ ID | FR ID | Planned Implementation Surface | Validation Strategy |
|------------|-------|--------------------------------|---------------------|
| DOC-REQ-001 | FR-001 | `moonmind/workflows/temporal/client.py` (`start_workflow`) and `api_service/api/routers/executions.py` | Unit test for `start_workflow`; E2E test starting via API and verifying on Temporal. |
| DOC-REQ-002 | FR-002 | `moonmind/workflows/temporal/workflows/run.py` (implementing `MoonMindRunWorkflow` with state transitions) | Unit test the workflow to ensure it transitions through initializing, planning, executing. |
| DOC-REQ-003 | FR-003 | `moonmind/workflows/temporal/workflows/run.py` (return correct status on completion or raise Exception on failure) | Unit test workflow termination states (success and failure). |
| DOC-REQ-004 | FR-004 | `moonmind/workflows/temporal/workflows/run.py` (using `workflow.upsert_search_attributes`) | Unit test verifying `upsert_search_attributes` is called with expected keys. |
| DOC-REQ-005 | FR-005 | Test files in `tests/` | E2E and Unit tests as part of CI validation. |

*All DOC-REQ mapped and validated.*
