# Tasks: Route Canonicalization

## T001 — Write TDD tests for redirect behavior [P]
- [X] T001a [P] Add test: `GET /tasks/create` returns 307 redirect to `/tasks/new`
- [X] T001b [P] Add test: `GET /tasks/tasks-list` returns 307 redirect to `/tasks/list`
- [X] T001c [P] Update existing `test_static_sub_routes_render_react_shell`: remove `/tasks/create` and `/tasks/tasks-list` from the 200-OK path list
- [X] T001d [P] Update existing `test_data_wide_panel_on_selected_react_routes`: remove `/tasks/tasks-list` from the `dataWidePanel:true` path list (it will be a redirect now)
- [X] T001e [P] Update existing `test_react_tasks_list_and_detail_boot_include_dashboard_config`: remove `/tasks/tasks-list` from the `dashboardConfig` assertion list
- [X] T001f [P] Update existing `test_invalid_dashboard_route_returns_404`: assert 404 message no longer mentions `/tasks/create`

**Independent Test**: Run `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard.py` — new tests fail (red), existing tests still pass.

## T002 — Convert alias routes to pure redirects [P]
- [X] T002a [P] Change `task_tasks_list_route` to return `RedirectResponse(url="/tasks/list", status_code=307)`
- [X] T002b [P] Change `task_create_alias_route` to return `RedirectResponse(url="/tasks/new", status_code=307)`
- [X] T002c [P] Remove `"create"` and `"tasks-list"` from `_STATIC_PATHS`
- [X] T002d [P] Update `_DASHBOARD_ROUTE_NOT_FOUND_DETAIL["message"]` to remove `/tasks/create` reference

**Independent Test**: Re-run unit tests — all pass including new redirect tests.

## T003 — Update navigation template [P]
- [X] T003a [P] Change Tasks nav link href from `/tasks` to `/tasks/list`
- [X] T003b [P] Simplify Tasks link active-state: remove `/tasks` and `/tasks/tasks-list` checks, keep `/tasks/list` + catch-all for detail pages
- [X] T003c [P] Simplify Create link active-state: remove `/tasks/create` check, keep only `/tasks/new`

**Independent Test**: Render nav template on `/tasks/list` and `/tasks/new` and verify correct active states and hrefs.

## T004 — Verify and run full test suite [P]
- [X] T004a [P] Run `./tools/test_unit.sh` — all tests pass
- [X] T004b [P] Run scope validation: confirm no frontend files were modified

**Independent Test**: `./tools/test_unit.sh` passes with zero failures.
