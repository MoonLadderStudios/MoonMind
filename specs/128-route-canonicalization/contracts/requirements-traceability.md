# Requirements Traceability: Route Canonicalization

| Spec Requirement | Implementation Location | Validation Strategy |
|---|---|---|
| FR-001: `/tasks/create` → 307 → `/tasks/new` | `task_dashboard.py` `task_create_alias_route` | Unit test: `client.get("/tasks/create", follow_redirects=False)` asserts 307 + location header |
| FR-002: `/tasks/tasks-list` → 307 → `/tasks/list` | `task_dashboard.py` `task_tasks_list_route` | Unit test: `client.get("/tasks/tasks-list", follow_redirects=False)` asserts 307 + location header |
| FR-003: Tasks nav link → `/tasks/list` | `_navigation.html` | Unit test: render nav template and assert href |
| FR-004: Create nav link → `/tasks/new` | `_navigation.html` (no change needed) | Existing test coverage confirms |
| FR-005: Nav active-state matches only canonical | `_navigation.html` | Unit test: render nav on various paths and assert active class only on canonical |
| FR-006: 404 message lists only canonical routes | `task_dashboard.py` `_DASHBOARD_ROUTE_NOT_FOUND_DETAIL` | Unit test: trigger 404 and assert message content |
| FR-007: Canonical routes still render React | `task_dashboard.py` unchanged canonical handlers | Existing test `test_static_sub_routes_render_react_shell` confirms |
| NF-001: 307 status code | Both redirect handlers | Same unit tests as FR-001/FR-002 |
| NF-002: No frontend changes | N/A | Scope verification: no files in `frontend/` modified |
| NF-003: Unit test coverage | `test_task_dashboard.py` | New and updated test cases |
