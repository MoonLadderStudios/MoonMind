# Research: Tasks List Canonical Route and Shell

## Input Classification

Decision: Single-story runtime feature request.
Evidence: The MM-585 Jira preset brief selects one Tasks List shell story from `docs/UI/TasksListPage.md` sections 1, 2, 3, and 5.1.
Rationale: The request has one independently testable outcome: `/tasks/list` is canonical, legacy route requests redirect, and the current shell surfaces remain available.
Alternatives considered: Broad design classification was rejected because the brief narrows source coverage to route and page-shell sections rather than the full Tasks List column-filter redesign.
Test implications: Focused backend route tests and frontend render tests are sufficient; no breakdown stage is needed.

## FR-001 and DESIGN-REQ-001

Decision: Implemented and verified.
Evidence: `api_service/api/routers/task_dashboard.py` defines the `/tasks/list` route; `tests/unit/api/routers/test_task_dashboard.py` requests `/tasks/list` in route-shell tests.
Rationale: The route is already registered and returns the shared shell for authenticated users.
Alternatives considered: Adding a new route module would duplicate existing dashboard routing.
Test implications: Focused backend route tests should be rerun.

## FR-002 and DESIGN-REQ-002

Decision: Implemented and verified.
Evidence: `task_dashboard_root` redirects `/tasks` to `/tasks/list`; `task_tasks_list_route` redirects `/tasks/tasks-list` to `/tasks/list`; tests assert redirect status and location.
Rationale: The legacy route behavior is already explicit and covered.
Alternatives considered: Rendering legacy aliases directly was rejected by the source design and prior canonicalization work.
Test implications: Focused backend route tests should be rerun.

## FR-003, FR-004, FR-005, DESIGN-REQ-003, and DESIGN-REQ-004

Decision: Implemented and verified.
Evidence: `/tasks/list` calls `_render_react_page(request, "tasks-list", list_path, initial_data={"dashboardConfig": build_runtime_config(list_path)}, data_wide_panel=True)`; route tests assert React shell assets, dashboard config, and `dataWidePanel:true`.
Rationale: Server-hosting, page key, runtime config, and wide layout are all present in the route handler and covered by tests.
Alternatives considered: Moving runtime config to the browser would violate the source design.
Test implications: Focused backend route tests should be rerun.

## FR-006 through FR-009 and DESIGN-REQ-006

Decision: Implemented and verified.
Evidence: `frontend/src/entrypoints/tasks-list.test.tsx` asserts one control deck, one data slab, Live updates, polling copy, disabled list behavior, page-size selector, and pagination.
Rationale: The current page shell preserves all MM-585 shell surfaces and has direct UI render evidence.
Alternatives considered: Adding new shell components would add scope and risk without changing required behavior.
Test implications: Focused Vitest UI tests should be rerun.

## FR-010

Decision: Implemented and verified.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` fetches `${payload.apiBase}/executions?...`; UI tests assert `/api/executions?...` URLs from the boot payload API base.
Rationale: Browser data loading remains behind MoonMind API routes and does not call external providers directly.
Alternatives considered: Direct Temporal or provider calls were rejected by the source design.
Test implications: Focused Vitest UI tests should be rerun.

## FR-011 and SC-006

Decision: Implemented but pending final verification.
Evidence: `spec.md`, `plan.md`, and `tasks.md` preserve the MM-585 Jira preset brief and source requirement IDs.
Rationale: Traceability must be confirmed in the final verification report after validation commands run.
Alternatives considered: Omitting traceability would violate the Jira orchestration input.
Test implications: Final MoonSpec verification must preserve MM-585 and DESIGN-REQ evidence.
