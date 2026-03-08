# Requirements Traceability

| Source Requirement | Functional Requirement | Planned Implementation Surface | Validation Strategy |
|-------------------|------------------------|---------------------------------|---------------------|
| DOC-REQ-001       | FR-001                 | `moonmind/config/settings.py` (`actions_enabled=True`) | E2E and Unit Tests verifying config application. |
| DOC-REQ-002       | FR-002                 | `moonmind/config/settings.py` (`submit_enabled=True`) | E2E and Unit Tests verifying config application. |
| DOC-REQ-003       | FR-003, FR-004         | `api_service/api/routers/task_dashboard_view_model.py` and `executions.py` | Unit tests to ensure API calls are made and properly restricted based on workflow state. |
| DOC-REQ-004       | FR-005, FR-006         | `moonmind/workflows/tasks/routing.py` and submit APIs | Unit tests for direct workflow initiation on submission. |
| DOC-REQ-005       | FR-007                 | Testing suites across the codebase. | Verify that automated tests exist and run successfully. |
