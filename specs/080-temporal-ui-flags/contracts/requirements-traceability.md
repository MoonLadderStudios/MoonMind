# Requirements Traceability

| Source Requirement | Functional Requirement | Planned Implementation Surface | Validation Strategy | Planned Implementation Tasks | Planned Validation Tasks |
|-------------------|------------------------|---------------------------------|---------------------|------------------------------|--------------------------|
| DOC-REQ-001       | FR-001                 | `moonmind/config/settings.py` (`actions_enabled=True`) | E2E and Unit Tests verifying config application. | T002 | T004 |
| DOC-REQ-002       | FR-002                 | `moonmind/config/settings.py` (`submit_enabled=True`) | E2E and Unit Tests verifying config application. | T003 | T004 |
| DOC-REQ-003       | FR-003, FR-004         | `api_service/api/routers/task_dashboard_view_model.py` and `executions.py` | Unit tests to ensure API calls are made and properly restricted based on workflow state. | T007, T008 | T005, T006 |
| DOC-REQ-004       | FR-005, FR-006         | `moonmind/workflows/tasks/routing.py` and submit APIs | Unit tests for direct workflow initiation on submission. | T011, T012 | T009, T010 |
| DOC-REQ-005       | FR-007                 | Testing suites across the codebase. | Verify that automated tests exist and run successfully. | T002, T003, T007, T008, T011, T012 | T004, T005, T006, T009, T010 |